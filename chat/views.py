"""Pages + JSON polling API for MY-CHATBOT."""

import hashlib
import json
import logging
import mimetypes
import os
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import bot as bot_engine
from . import icons as icon_ops
from . import rider_deliveries as rider_ops
from . import services
from . import stock as stock_ops
from . import store_stock as store_stock_ops
from .auth import (
    api_rider_required,
    api_shop_required,
    api_company_store_manager_required,
    authenticate_employee,
    authenticate_shop,
    can_view_all_shops,
    current_employee,
    current_shop,
    employee_allocated_to_shop,
    employee_assigned_shop_ids,
    login_employee,
    login_shop,
    logout_shop,
    role_home_url,
    role_icon,
    role_label,
    role_slug,
    shop_required,
)
from .models import Conversation, ConversationKind, Message, Participant
from .myshop_models import (
    Shop,
    ShopReceipt,
    ShopReceiptLine,
)

logger = logging.getLogger(__name__)

HISTORY_PAGE_SIZE = 40
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_body(request) -> dict:
    if request.content_type and "json" in request.content_type:
        try:
            return json.loads(request.body or b"{}")
        except (ValueError, TypeError):
            return {}
    return request.POST.dict()


def _err(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def _perm_denied(employee_id, *keys):
    """JSON 403 when the HR permission matrix blocks every key."""
    try:
        stock_ops.assert_employee_may(employee_id, *keys)
    except stock_ops.StockError as exc:
        return _err(str(exc), 403)
    return None


def _needs_member_confirmation(request, shop) -> bool:
    """Staff acting on a shop they aren't allocated to need a member's code.

    Employees signed in through the staff portal (admin / manager / IT
    support) can operate any shop via the picker. When the acting employee
    isn't allocated to the shop, stock-in and transfer only proceed after a
    member of that shop confirms by entering their own staff code. Shop
    portal sessions act as the shop itself, so they skip this check.
    """
    emp = current_employee(request)
    if emp is None:
        return False
    return not employee_allocated_to_shop(emp["id"], shop.pk)


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _company_name() -> str:
    return services.company_name()


def _participants(convo: Conversation, me: Shop):
    me_part = peer = None
    for p in convo.participants.all():
        if p.shop_id == me.pk:
            me_part = p
        else:
            peer = p
    return me_part, peer


def _serialize_active(
    convo: Conversation,
    me: Shop,
    after_id: int = 0,
    changed_since=None,
) -> dict:
    me_part, peer = _participants(convo, me)
    group = convo.kind == ConversationKind.GROUP
    peers = (
        [p for p in convo.participants.all() if p.shop_id != me.pk]
        if group
        else None
    )
    qs = convo.messages.select_related().order_by("id")
    if after_id:
        qs = qs.filter(id__gt=after_id)
    msgs = list(qs[:400])
    shops_map = services.shops_map_for([convo]) if group else {}
    sender_names = (
        {sid: s.name for sid, s in shops_map.items()} if group else None
    )
    peer_shop = services.shop_by_id(peer.shop_id) if peer else None
    bot_thread = services.is_bot_thread(convo)
    data = {
        "id": convo.pk,
        "kind": convo.kind,
        "messages": [
            services.serialize_message(
                m, me, peer, bot_thread,
                peers=peers, sender_names=sender_names,
            )
            for m in msgs
        ],
        "peer": (
            services.serialize_group_peer(convo, me, shops_map)
            if group
            else services.serialize_peer(peer, peer_shop)
        ),
        "suggestions": bot_engine.BOT_SUGGESTIONS
        if convo.kind == ConversationKind.BOT
        else bot_engine.STOCK_SUGGESTIONS
        if services.is_stock_bot(convo)
        else bot_engine.EXPENSE_SUGGESTIONS
        if services.is_expense_bot(convo)
        else [],
    }
    if convo.kind == ConversationKind.BOT:
        data["peer"] = {
            "online": True,
            "typing": False,
            "last_seen": "",
            "name": services.bot_name(me.name),
            "location": services.BOT_SUBTITLE,
            "read_up_to": 0,
        }
    elif services.is_stock_bot(convo):
        data["peer"] = {
            "online": True,
            "typing": False,
            "last_seen": "",
            "name": services.stock_bot_name(me.name),
            "location": services.STOCK_BOT_SUBTITLE,
            "read_up_to": 0,
        }
    elif services.is_expense_bot(convo):
        data["peer"] = {
            "online": True,
            "typing": False,
            "last_seen": "",
            "name": services.expense_bot_name(me.name),
            "location": services.EXPENSE_BOT_SUBTITLE,
            "read_up_to": 0,
        }
    if after_id and changed_since is not None:
        # Change feed: messages already held by the client whose stored copy
        # was rewritten since its last heartbeat (transfer outcome, pending
        # confirmation). The small overlap absorbs commits that landed while
        # the previous poll was still in flight.
        horizon = changed_since - timedelta(seconds=3)
        changed_qs = convo.messages.filter(
            id__lte=after_id, updated_at__gt=horizon
        ).order_by("id")
        data["changed"] = [
            services.serialize_message(
                m, me, peer, bot_thread,
                peers=peers, sender_names=sender_names,
            )
            for m in changed_qs[:200]
        ]
    return data


def _conversations_payload(me: Shop) -> list:
    services.get_or_create_bot(me)  # assistant thread always exists
    modes = bot_engine.shop_stock_modes(me)
    if modes["items"] or modes["shelf"]:
        services.get_or_create_stock_bot(me)  # stock assistant thread
    services.get_or_create_expense_bot(me)  # expenses assistant thread
    convos = list(services.my_conversations(me))
    shops_map = services.shops_map_for(convos)
    return [services.serialize_conversation(c, me, shops_map) for c in convos]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def home(request):
    if current_shop(request) is not None:
        return redirect("chat:app")
    return redirect("chat:login")


@require_GET
def service_worker(request):
    """Chatbot-scoped SW with no-cache so every deploy is detected quickly."""
    path = os.path.join(settings.BASE_DIR, "static", "sw-chatbot.js")
    if not os.path.isfile(path):
        raise Http404()
    resp = FileResponse(open(path, "rb"), content_type="application/javascript")
    resp["Cache-Control"] = "no-cache, no-store, must-revalidate"
    # Scope limited to /chatbot/ so this PWA does not fight MY-SHOP's root SW.
    resp["Service-Worker-Allowed"] = reverse("chat:home")
    return resp


@require_GET
def app_icon(request, size: int):
    """PNG app icon branded with the company name (fallback: MY SHOP).

    Unauthenticated on purpose — favicon / PWA / notification fetches often
    carry no session. `rounded=1` bakes a soft squircle for favicons.
    """
    if size not in (192, 512):
        raise Http404()
    rounded = request.GET.get("rounded") in {"1", "true", "any"}
    label = icon_ops.icon_label()
    # Memcached-safe key (no spaces / punctuation from company names).
    digest = hashlib.sha1(
        f"{icon_ops.ICON_ASSET_VERSION}:{size}:{int(rounded)}:{label}".encode()
    ).hexdigest()[:16]
    cache_key = f"chat:app-icon:{digest}"
    png = cache.get(cache_key)
    if not png:
        png = icon_ops.render_app_icon(size, rounded=rounded)
        cache.set(cache_key, png, 3600)
    resp = HttpResponse(png, content_type="image/png")
    # Versioned URLs (`?v=`) bust clients; keep a short cache for repeats.
    resp["Cache-Control"] = "public, max-age=300"
    return resp


@require_GET
def manifest(request):
    """PWA manifest — rendered dynamically so the install name follows the
    company name set in MY-SHOP settings. Unauthenticated on purpose: manifest
    fetches carry no credentials.

    Stable `id` + changing icon URLs let Chrome auto-update installed apps
    (including the home-screen icon on Android) when the PWA is opened.
    """
    name = services.app_name()
    bust = icon_ops.icon_cache_bust()
    icon_192 = reverse("chat:app_icon", kwargs={"size": 192})
    icon_512 = reverse("chat:app_icon", kwargs={"size": 512})
    icons = [
        {
            "src": f"{icon_192}?rounded=1&v={bust}",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": f"{icon_192}?v={bust}",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "maskable",
        },
        {
            "src": f"{icon_512}?rounded=1&v={bust}",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": f"{icon_512}?v={bust}",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]
    home = reverse("chat:home")
    start = reverse("chat:app")
    resp = JsonResponse(
        {
            # Stable identity under /chatbot/ — change only if you intend a new install.
            "id": home,
            "name": name,
            "short_name": name,
            "description": "Shop-to-shop chat and business data assistant",
            "start_url": start,
            "scope": home,
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0b141a",
            "theme_color": "#00a884",
            "icons": icons,
        }
    )
    resp["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def login_view(request):
    if request.method == "GET":
        if current_shop(request) is not None:
            return redirect("chat:app")
        employee = current_employee(request)
        if employee is not None:
            return redirect(role_home_url(employee["role"]))

    error = ""
    code = ""
    mode = "shop"
    if request.method == "POST":
        mode = "employee" if request.POST.get("mode") == "employee" else "shop"
        code = re.sub(r"\D+", "", request.POST.get("login_code") or "")[:6]
        password = request.POST.get("password") or ""
        key = f"chatbot:login:{_client_ip(request)}"
        attempts = cache.get(key, 0)
        if attempts >= LOGIN_MAX_ATTEMPTS:
            error = "Too many attempts. Wait a minute and try again."
        elif len(code) != 6 or not password:
            error = "Enter the 6-digit code and password."
        elif mode == "employee":
            employee = authenticate_employee(code, password)
            if employee is None:
                cache.set(key, attempts + 1, LOGIN_WINDOW_SECONDS)
                error = "Invalid staff ID or password."
            else:
                cache.delete(key)
                request.session.cycle_key()
                login_employee(request, employee)
                return redirect(role_home_url(employee["role"]))
        else:
            shop = authenticate_shop(code, password)
            if shop is None:
                cache.set(key, attempts + 1, LOGIN_WINDOW_SECONDS)
                error = "Invalid shop code or password. Please try again."
            else:
                cache.delete(key)
                request.session.cycle_key()
                login_shop(request, shop)
                next_url = request.GET.get("next") or request.POST.get("next") or ""
                if next_url.startswith("/"):
                    return redirect(next_url)
                return redirect("chat:app")

    return render(
        request,
        "chat/login.html",
        {
            "error": error,
            "login_code": code,
            "mode": mode,
            "company_name": _company_name(),
        },
    )


def _shop_overview():
    """Per-shop today aggregates for the employee all-shops dashboard."""
    from django.db.models import Count, F, Sum
    from decimal import Decimal
    from .myshop_models import Expense, ShopReceipt

    today = timezone.localdate()
    start_dt, end_dt = bot_engine._window(today, today)

    sales = {
        r["shop_id"]: r
        for r in ShopReceipt.objects.filter(
            kind=bot_engine.SALE_KIND,
            status__in=bot_engine._analytics_statuses(
                bot_engine.analytics_receipt_default()
            ),
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )
        .values("shop_id")
        .annotate(t=Sum("total"), c=Count("id"))
    }
    expenses = {
        r["shop_id"]: r["t"]
        for r in Expense.objects.filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        )
        .values("shop_id")
        .annotate(t=Sum("amount"))
    }
    credits = {
        r["shop_id"]: r["t"]
        for r in (
            ShopReceipt.objects.filter(
                kind=bot_engine.CREDIT_KIND,
                status__in=bot_engine.COUNTED_STATUSES,
            )
            .annotate(balance=F("total") - F("amount_paid"))
            .filter(balance__gt=0)
            .values("shop_id")
            .annotate(t=Sum("balance"))
        )
    }

    rows = []
    totals = {"sales": Decimal("0"), "expenses": Decimal("0"),
              "credit": Decimal("0"), "receipts": 0}
    for shop in services.active_shops():
        s = sales.get(shop.pk, {})
        row = {
            "shop": shop,
            "sales": s.get("t") or 0,
            "receipts": s.get("c") or 0,
            "expenses": expenses.get(shop.pk) or 0,
            "credit": credits.get(shop.pk) or 0,
        }
        totals["sales"] += row["sales"]
        totals["receipts"] += row["receipts"]
        totals["expenses"] += row["expenses"]
        totals["credit"] += row["credit"]
        rows.append(row)
    return rows, totals


def _stock_overview():
    """Today's stock-movement totals per shop for the stock-mode dashboard."""
    from decimal import Decimal

    from . import all_stock

    today = timezone.localdate()
    rows = []
    totals = {"in": Decimal("0"), "out": Decimal("0"),
              "traded": Decimal("0"), "transfers": 0}
    for shop in services.active_shops():
        shop_modes = bot_engine.shop_stock_modes(shop)
        shop_kind = "items" if shop_modes["items"] else "shelf"
        data = all_stock.build_stock_day(
            today, shop_id=shop.pk, kind=shop_kind
        )
        by_key = {s["key"]: s for s in data["sections"]}
        traded = (
            by_key["trade_in"]["value"] + by_key["trade_out"]["value"]
        )
        row = {
            "shop": shop,
            "in_value": by_key["in"]["value"],
            "out_value": by_key["out"]["value"],
            "traded": traded,
            "transfers": by_key["transfers"]["moves"],
            "moves": sum(s["moves"] for s in data["sections"]),
            "in_fmt": bot_engine.money(by_key["in"]["value"]),
            "out_fmt": bot_engine.money(by_key["out"]["value"]),
            "traded_fmt": bot_engine.money(traded),
        }
        totals["in"] += row["in_value"]
        totals["out"] += row["out_value"]
        totals["traded"] += row["traded"]
        totals["transfers"] += row["transfers"]
        rows.append(row)
    totals_fmt = {
        "in": bot_engine.money(totals["in"]),
        "out": bot_engine.money(totals["out"]),
        "traded": bot_engine.money(totals["traded"]),
        "transfers": totals["transfers"],
    }
    return rows, totals_fmt


def _expenses_overview():
    """Today's expense totals per shop for the expenses-mode dashboard."""
    from datetime import datetime, time, timedelta
    from decimal import Decimal

    from django.db.models import Count, Sum

    from .myshop_models import Expense

    today = timezone.localdate()
    start_dt = timezone.make_aware(datetime.combine(today, time.min))
    end_dt = start_dt + timedelta(days=1)
    shops = list(services.active_shops())
    rows = []
    totals = {"total": Decimal("0"), "unpaid": Decimal("0"), "count": 0}
    for shop in shops:
        agg = Expense.objects.filter(
            shop_id=shop.pk,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        ).aggregate(
            t=Sum("amount"), u=Sum("amount_paid"), c=Count("id")
        )
        total = agg["t"] or Decimal("0")
        unpaid = total - (agg["u"] or Decimal("0"))
        rows.append(
            {
                "shop": shop,
                "count": agg["c"] or 0,
                "total_fmt": bot_engine.money(total),
                "unpaid_fmt": bot_engine.money(unpaid),
            }
        )
        totals["total"] += total
        totals["unpaid"] += unpaid
        totals["count"] += agg["c"] or 0
    totals_fmt = {
        "total": bot_engine.money(totals["total"]),
        "unpaid": bot_engine.money(totals["unpaid"]),
        "count": totals["count"],
    }
    return rows, totals_fmt


@require_POST
def pick_shop(request):
    """Employee-only shop switcher — the /all/ sidebar posts here."""
    employee = current_employee(request)
    if employee is None:
        return redirect("chat:login")

    if str(request.POST.get("shop_id")) == "all":
        if not can_view_all_shops(employee["role"]):
            return redirect(role_home_url(employee["role"]))
        request.session["chatbot_all_shops"] = True
        request.session.pop("chatbot_shop_id", None)
        return redirect("chat:all_shops")
    try:
        shop = services.shop_by_id(int(request.POST.get("shop_id") or 0))
    except (TypeError, ValueError):
        shop = None
    if shop is None:
        if can_view_all_shops(employee["role"]):
            return redirect("chat:all_shops")
        return redirect(role_home_url(employee["role"]))
    request.session.pop("chatbot_all_shops", None)
    login_shop(request, shop)
    fab = (request.POST.get("fab") or "").strip()
    if fab == "receipts":
        return redirect("chat:receipts")
    if fab in {
        "sale", "stockin", "stockout", "transfer", "trade", "trade_in",
        "trade_out", "expense"
    }:
        return redirect(f"{reverse('chat:app')}?fab={fab}")
    return redirect("chat:app")


def role_home_view(request, slug):
    """Role workspace for staff who do not get the all-shops view."""
    employee = current_employee(request)
    if employee is None:
        return redirect("chat:login")
    if can_view_all_shops(employee["role"]):
        return redirect("chat:all_shops")
    expected = role_slug(employee["role"])
    if slug != expected:
        return redirect(role_home_url(employee["role"]))
    role = employee["role"]
    context = {
        "employee": employee,
        "company_name": _company_name(),
        "role": role,
        "role_label": role_label(role),
        "role_icon": role_icon(role),
        "role_slug": expected,
    }
    if role == "company_store_manager":
        context.update(
            {
                "items_url": reverse("chat:api_store_items"),
                "employees_url": reverse("chat:api_store_employees"),
                "stock_in_url": reverse("chat:api_store_stock_in"),
                "stock_out_url": reverse("chat:api_store_stock_out"),
                "trade_url": reverse("chat:api_store_trade"),
                "report_url": reverse("chat:api_store_report"),
                "feed_url": reverse("chat:api_store_feed"),
                "stock_req_json": json.dumps(
                    store_stock_ops.stock_req_dict()
                ),
                "stock_out_reasons": store_stock_ops.STOCK_OUT_REASONS,
                "payment_statuses": tuple(
                    (value, store_stock_ops.PAYMENT_STATUS_LABELS[value])
                    for value in store_stock_ops.STOCK_PAYMENT_STATUSES
                ),
            }
        )
        return render(request, "chat/store_manager.html", context)
    if role == "rider":
        context.update(
            {
                "board_url": reverse("chat:api_rider_board"),
                "status_url": reverse("chat:api_rider_status"),
                "accept_url_template": reverse(
                    "chat:api_rider_accept", kwargs={"delivery_id": 0}
                ).replace("/0/", "/__ID__/"),
                "complete_url_template": reverse(
                    "chat:api_rider_complete", kwargs={"delivery_id": 0}
                ).replace("/0/", "/__ID__/"),
            }
        )
        return render(request, "chat/rider.html", context)

    # Shop-floor roles get a WhatsApp-style list of allocated shops.
    shop_ids = employee_assigned_shop_ids(employee["id"])
    shops = []
    for shop in services.active_shops():
        if shop.pk not in shop_ids:
            continue
        shops.append(
            {
                "id": shop.pk,
                "name": shop.name,
                "location": shop.location,
                "initials": services.initials(shop.name),
                "tone": services.avatar_tone(shop.pk),
            }
        )
    context["shops"] = shops
    return render(request, "chat/role.html", context)


@api_rider_required
@require_GET
def api_rider_board(request):
    """Open / active / completed delivery boards for the signed-in rider."""
    board = rider_ops.build_rider_board(request.chat_employee["id"])
    return JsonResponse({"ok": True, **board})


@api_rider_required
@require_GET
def api_rider_status(request):
    """Pollable open-delivery snapshot for new-job alerts."""
    return JsonResponse(rider_ops.rider_alert_payload(request.chat_employee["id"]))


@api_rider_required
@require_POST
def api_rider_accept(request, delivery_id: int):
    """Claim an open delivery — first rider wins."""
    try:
        card = rider_ops.accept_delivery(
            delivery_id=delivery_id,
            rider_id=request.chat_employee["id"],
        )
    except rider_ops.RiderError as exc:
        return _err(str(exc))
    return JsonResponse(
        {
            "ok": True,
            "delivery": card,
            "message": "Delivery accepted. Client details are now on your active board.",
        }
    )


@api_rider_required
@require_POST
def api_rider_complete(request, delivery_id: int):
    """Mark an accepted delivery as delivered (sale/credit rules)."""
    data = _json_body(request)
    try:
        card = rider_ops.complete_delivery(
            delivery_id=delivery_id,
            rider_id=request.chat_employee["id"],
            receiver_name=data.get("receiver_name") or "",
            payment_reference=data.get("payment_reference") or "",
            payment_at=data.get("payment_at") or "",
            payment_amount=data.get("payment_amount") or "",
        )
    except rider_ops.RiderError as exc:
        return _err(str(exc))
    paid = card.get("status") == "delivered_paid"
    return JsonResponse(
        {
            "ok": True,
            "delivery": card,
            "message": (
                "Delivery marked as delivered (paid). Sale/credit status unchanged."
                if paid
                else "Delivery marked as delivered. Sale/credit status unchanged."
            ),
        }
    )


@api_company_store_manager_required
@require_GET
def api_store_items(request):
    """Item typeahead + central-store qty for the Company Store Manager cart."""
    denied = _perm_denied(
        request.chat_employee["id"],
        ("stock-management", "view"),
        ("stock-management", "in"),
        ("stock-management", "out"),
    )
    if denied is not None:
        return denied
    q = request.GET.get("q") or ""
    return JsonResponse(
        {
            "ok": True,
            "items": store_stock_ops.search_store_items(query=q, limit=12),
        }
    )


@api_company_store_manager_required
@require_GET
def api_store_employees(request):
    """Active employee typeahead for delivered-by / received-by."""
    denied = _perm_denied(
        request.chat_employee["id"],
        ("stock-management", "view"),
        ("stock-management", "in"),
        ("stock-management", "out"),
    )
    if denied is not None:
        return denied
    return JsonResponse(
        {
            "ok": True,
            "employees": store_stock_ops.search_active_employees(
                query=request.GET.get("q") or "",
                limit=request.GET.get("limit") or 12,
            ),
        }
    )


@api_company_store_manager_required
@require_POST
def api_store_stock_in(request):
    """Record qty into the central store (does not change shop floor stock)."""
    denied = _perm_denied(
        request.chat_employee["id"], ("stock-management", "in")
    )
    if denied is not None:
        return denied
    data = _json_body(request)
    try:
        stock_req = store_stock_ops.get_stock_requirements()
        details = store_stock_ops.parse_supplier_payment_details(
            data, stock_req=stock_req
        )
        movements = store_stock_ops.record_store_stock_lines(
            mode="in",
            actor_id=request.chat_employee["id"],
            lines=store_stock_ops._parse_lines(
                data.get("lines") or [],
                require_price=bool(
                    getattr(stock_req, "require_buying_price_on_in", False)
                ),
            ),
            received_by_id=data.get("received_by_id"),
            note=data.get("note") or "",
            **details,
        )
    except store_stock_ops.StoreError as exc:
        return _err(str(exc))
    body = store_stock_ops.store_stock_body(movements, mode="in")
    card = store_stock_ops.store_stock_card(movements, mode="in")
    return JsonResponse(
        {
            "ok": True,
            "movements": store_stock_ops.serialize_movements(movements),
            "count": len(movements),
            "body": body,
            "card": card,
        }
    )


@api_company_store_manager_required
@require_POST
def api_store_stock_out(request):
    """Record qty leaving the central store (must not exceed on-hand)."""
    denied = _perm_denied(
        request.chat_employee["id"], ("stock-management", "out")
    )
    if denied is not None:
        return denied
    data = _json_body(request)
    try:
        stock_req = store_stock_ops.get_stock_requirements()
        details = store_stock_ops.parse_stock_out_details(
            data, stock_req=stock_req
        )
        note = details.pop("note", "")
        movements = store_stock_ops.record_store_stock_lines(
            mode="out",
            actor_id=request.chat_employee["id"],
            lines=data.get("lines") or [],
            received_by_id=data.get("received_by_id"),
            note=note,
            supplier_name=data.get("supplier_name") or "",
            supplier_phone=data.get("supplier_phone") or "",
            **details,
        )
    except store_stock_ops.StoreError as exc:
        return _err(str(exc))
    body = store_stock_ops.store_stock_body(movements, mode="out")
    card = store_stock_ops.store_stock_card(movements, mode="out")
    return JsonResponse(
        {
            "ok": True,
            "movements": store_stock_ops.serialize_movements(movements),
            "count": len(movements),
            "body": body,
            "card": card,
        }
    )


@api_company_store_manager_required
@require_POST
def api_store_trade(request):
    """One-way central-store trade with a person (trade in / trade out)."""
    data = _json_body(request)
    denied = _perm_denied(
        request.chat_employee["id"],
        (
            "stock-management",
            "in"
            if str(data.get("trade_kind") or "").strip().lower()
            == store_stock_ops.REASON_TRADE_IN
            else "out",
        ),
    )
    if denied is not None:
        return denied
    try:
        stock_req = store_stock_ops.get_stock_requirements()
        kind, lines = store_stock_ops.parse_store_trade_lines(
            data,
            require_price=bool(
                getattr(stock_req, "require_buying_price_on_in", False)
            ),
        )
        movements = store_stock_ops.record_store_trade(
            actor_id=request.chat_employee["id"],
            kind=kind,
            person_name=data.get("person_name") or "",
            person_phone=data.get("person_phone") or "",
            lines=lines,
            note=(data.get("note") or "").strip(),
            require_person=bool(
                getattr(stock_req, "require_supplier_on_in", False)
            ),
        )
    except store_stock_ops.StoreError as exc:
        return _err(str(exc))
    mode = "in" if kind == store_stock_ops.REASON_TRADE_IN else "out"
    body = store_stock_ops.store_stock_body(movements, mode=mode)
    card = store_stock_ops.store_stock_card(movements, mode=mode)
    return JsonResponse(
        {
            "ok": True,
            "movements": store_stock_ops.serialize_movements(movements),
            "count": len(movements),
            "body": body,
            "card": card,
        }
    )


@api_company_store_manager_required
@require_GET
def api_store_feed(request):
    """WhatsApp-style reference feed of recent store stock in/out cards."""
    denied = _perm_denied(
        request.chat_employee["id"],
        ("stock-management", "movements"),
        ("stock-management", "view"),
    )
    if denied is not None:
        return denied
    return JsonResponse(
        {
            "ok": True,
            "messages": store_stock_ops.build_store_feed(
                limit=request.GET.get("limit") or 40
            ),
        }
    )


@api_company_store_manager_required
@require_GET
def api_store_report(request):
    """Filtered central-store movement ledger."""
    denied = _perm_denied(
        request.chat_employee["id"], ("stock-management", "report")
    )
    if denied is not None:
        return denied
    report = store_stock_ops.build_store_stock_report(request.GET)
    return JsonResponse({"ok": True, **report})


def all_shops_view(request):
    """Employee-only combined assistant view across every shop."""
    employee = current_employee(request)
    if employee is None:
        return redirect("chat:login")
    if not can_view_all_shops(employee["role"]):
        return redirect(role_home_url(employee["role"]))
    # Landing here means combined view — drop any single-shop session.
    request.session["chatbot_all_shops"] = True
    request.session.pop("chatbot_shop_id", None)
    open_mode = (request.GET.get("open") or "")
    stock_mode = open_mode == "stock"
    expenses_mode = open_mode == "expenses"
    stock_rows = stock_totals = exp_rows = exp_totals = None
    if stock_mode:
        stock_rows, stock_totals = _stock_overview()
    if expenses_mode:
        exp_rows, exp_totals = _expenses_overview()
    rows, totals = _shop_overview()
    for row in rows:
        row["sales_fmt"] = bot_engine.money(row["sales"])
        row["expenses_fmt"] = bot_engine.money(row["expenses"])
        row["credit_fmt"] = bot_engine.money(row["credit"])
    shops = [
        {
            "id": s.pk,
            "name": s.name,
            "location": s.location,
            "initials": services.initials(s.name),
            "tone": services.avatar_tone(s.pk),
        }
        for s in services.active_shops()
    ]
    return render(
        request,
        "chat/all.html",
        {
            "employee": employee,
            "company_name": _company_name(),
            "shops": shops,
            "shop_rows": rows,
            "totals": {
                "sales": bot_engine.money(totals["sales"]),
                "receipts": totals["receipts"],
                "expenses": bot_engine.money(totals["expenses"]),
                "credit": bot_engine.money(totals["credit"]),
            },
            "receipt_status_default_json": json.dumps(
                bot_engine.analytics_receipt_default()
            ),
            "stock_modes": bot_engine.company_stock_modes(),
            "stock_mode": stock_mode,
            "expenses_mode": expenses_mode,
            "stock_rows": stock_rows,
            "stock_totals": stock_totals,
            "exp_rows": exp_rows,
            "exp_totals": exp_totals,
        },
    )


def _stock_page_day(request):
    """Parse ``?date=YYYY-MM-DD`` (default today)."""
    from datetime import datetime

    raw = (request.GET.get("date") or "").strip()
    try:
        return (
            datetime.strptime(raw, "%Y-%m-%d").date()
            if raw
            else timezone.localdate()
        )
    except ValueError:
        return timezone.localdate()


def _stock_page_kind(request, modes, scope):
    """Effective stock-type filter for the day page.

    ``kind=items|shelf`` picks only while that mode is enabled; the central
    store scope is item-ledger only. Bare/invalid picks fall back to the
    enabled ledger (items first).
    """
    from . import all_stock

    if scope == "store":
        return "items"
    asked = (request.GET.get("kind") or "").strip().lower()
    if asked in all_stock.KIND_KEYS and modes.get(asked):
        return asked
    if modes.get("items"):
        return "items"
    if modes.get("shelf"):
        return "shelf"
    return asked if asked in all_stock.KIND_KEYS else "items"


def _stock_page_context(request, *, day, view, scope, kind, modes, data):
    """Shared context: money/qty formatting + summary/detail context."""
    from . import all_stock

    sections = data["sections"]
    for section in sections:
        section["units_fmt"] = bot_engine.qty_fmt(section["units"])
        section["value_fmt"] = bot_engine.money(section["value"])
        for row in section["rows"]:
            row["qty_fmt"] = bot_engine.qty_fmt(row["qty"])
            row["value_fmt"] = bot_engine.money(row["value"])
            for line in row["lines"]:
                line["qty_fmt"] = bot_engine.qty_fmt(line["qty"])
                line["unit_fmt"] = bot_engine.money(line["unit"])
                line["value_fmt"] = bot_engine.money(line["value"])
    suppliers = data["suppliers"]
    for sup in suppliers:
        sup["units_fmt"] = bot_engine.qty_fmt(sup["units"])
        sup["value_fmt"] = bot_engine.money(sup["value"])
        for line in sup["lines"]:
            line["qty_fmt"] = bot_engine.qty_fmt(line["qty"])
            line["unit_fmt"] = bot_engine.money(line["unit"])
            line["value_fmt"] = bot_engine.money(line["value"])

    view_section = next(
        (s for s in sections if s["key"] == view), None
    )
    if view == "report":
        cards = sections
    elif view_section is not None:
        cards = [view_section]
    else:
        cards = []

    day_iso = day.isoformat()

    def href(*, scope_v=None, kind_v=None, view_v=None):
        s = scope if scope_v is None else scope_v
        k = kind if kind_v is None else kind_v
        url = f"?date={day_iso}&scope={s}&kind={k}"
        if view_v is not None:
            url += f"&view={view_v}"
        return url

    total_value = sum(s["value"] for s in sections)
    return {
        "day_iso": day_iso,
        "day_label": day.strftime("%d %b %Y"),
        "is_today": day == timezone.localdate(),
        "view": view,
        "view_section": view_section,
        "cards": cards,
        "sections": sections,
        "suppliers": suppliers,
        "suppliers_units_fmt": bot_engine.qty_fmt(
            sum(s["units"] for s in suppliers)
        ),
        "suppliers_value_fmt": bot_engine.money(
            sum(s["value"] for s in suppliers)
        ),
        "scope": scope,
        "kind": kind,
        "kind_options": [
            {
                "key": k,
                "label": all_stock.KIND_LABELS[k],
                "active": k == kind,
                "href": href(kind_v=k, view_v=""),
            }
            for k in all_stock.KIND_KEYS
        ]
        if scope != "store" and modes.get("items") and modes.get("shelf")
        else [],
        "items_mode_on": bool(modes.get("items")),
        "show_mode_alert": kind == "items" and not modes.get("items"),
        "total_moves": sum(s["moves"] for s in sections),
        "total_value_fmt": bot_engine.money(total_value),
        "base_qs": f"date={day_iso}&scope={scope}&kind={kind}",
    }


@require_GET
def all_stock_view(request):
    """All-shops stock movement page — day sections with drill-down.

    Sections: stock in, stock out, traded in, traded out, transfers,
    suppliers and the combined day report (``?view=report``).
    ``?scope=shops|store`` picks the shops ledger vs the central store
    ledger; ``?kind=items|shelf`` filters the stock type when both modes
    are on.
    """
    employee = current_employee(request)
    if employee is None:
        return redirect("chat:login")
    if not can_view_all_shops(employee["role"]):
        return redirect(role_home_url(employee["role"]))

    from . import all_stock

    day = _stock_page_day(request)
    modes = bot_engine.company_stock_modes()
    scope = (request.GET.get("scope") or "").strip().lower()
    if scope not in all_stock.SCOPE_KEYS:
        scope = "shops"
    kind = _stock_page_kind(request, modes, scope)
    view = (request.GET.get("view") or "").strip().lower()
    if view not in all_stock.VIEW_KEYS:
        view = ""

    data = all_stock.build_stock_day(day, kind=kind, scope=scope)
    context = _stock_page_context(
        request,
        day=day,
        view=view,
        scope=scope,
        kind=kind,
        modes=modes,
        data=data,
    )
    context.update(
        {
            "employee": employee,
            "company_name": _company_name(),
            "page_label": "All shops",
            "back_href": reverse("chat:all_shops"),
            "back_label": "All shops",
            "scope_options": [
                {
                    "key": s,
                    "label": all_stock.SCOPE_LABELS[s],
                    "active": s == scope,
                    "href": f"?date={context['day_iso']}&scope={s}",
                }
                for s in all_stock.SCOPE_KEYS
            ],
        }
    )
    return render(request, "chat/all_stock.html", context)


@shop_required
@require_GET
def shop_stock_view(request):
    """This shop's stock movement page — same sections, shop-scoped.

    ``?kind=items|shelf`` filters the stock type when the shop runs both
    modes; there is no scope picker — a shop only sees its own ledger
    (transfers where it sends or receives stock included).
    """
    me = request.chat_shop
    from . import all_stock

    day = _stock_page_day(request)
    modes = stock_ops.shop_stock_modes(me)
    kind = _stock_page_kind(request, modes, "shops")
    view = (request.GET.get("view") or "").strip().lower()
    if view not in all_stock.VIEW_KEYS:
        view = ""

    data = all_stock.build_stock_day(day, shop_id=me.pk, kind=kind)
    context = _stock_page_context(
        request,
        day=day,
        view=view,
        scope="shops",
        kind=kind,
        modes=modes,
        data=data,
    )
    context.update(
        {
            "employee": current_employee(request),
            "company_name": _company_name(),
            "page_label": me.name,
            "back_href": reverse("chat:app"),
            "back_label": "Chats",
            "scope_options": [],
        }
    )
    return render(request, "chat/all_stock.html", context)


@require_POST
def api_all_chat(request):
    """POST {text} → {reply} — the all-shops assistant."""
    employee = current_employee(request)
    if employee is None:
        return JsonResponse(
            {"ok": False, "error": "authentication required"}, status=401
        )
    data = _json_body(request)
    text = (data.get("text") or "").strip()
    if not text:
        return JsonResponse({"ok": False, "error": "Empty message."})

    from employees.models import EmployeeProfile
    from employees.workspace import day_report_section_enabled

    profile = EmployeeProfile.objects.filter(pk=employee["id"]).first()
    section_visible = lambda key: day_report_section_enabled(profile, key)
    mode = (data.get("mode") or "")
    if mode == "stock":
        scope_shop = services.shop_by_id(data.get("shop"))
        reply = bot_engine.reply_for_all_stock(
            text,
            section_visible=section_visible,
            fallback_topic=(data.get("topic") or ""),
            shop=scope_shop,
        )
        card = bot_engine.card_for_all_stock(
            text, fallback_topic=(data.get("topic") or ""),
            shop=scope_shop,
        )
        if card is None and scope_shop is None:
            card = bot_engine.card_for_all(
                text, section_visible=section_visible
            )
    elif mode == "expenses":
        scope_shop = services.shop_by_id(data.get("shop"))
        reply = bot_engine.reply_for_all_expenses(
            text,
            section_visible=section_visible,
            fallback_topic=(data.get("topic") or ""),
            shop=scope_shop,
        )
        card = bot_engine.card_for_all_expenses(
            text, fallback_topic=(data.get("topic") or ""),
            shop=scope_shop,
        )
    else:
        reply = bot_engine.reply_for_all(
            text, section_visible=section_visible
        )
        card = bot_engine.card_for_all(
            text, section_visible=section_visible
        )
    return JsonResponse({"ok": True, "reply": reply, "card": card})


@require_GET
def all_session_report(request, session_id):
    """Full cashier report for one shop day session.

    Opened from the report card's session rows — renders the same
    day-accounting sheet the analytics Cashiers report prints (every
    section: money flow, items sold, sellers, expenses, stock in/out).
    """
    employee = current_employee(request)
    if employee is None:
        return redirect("chat:login")
    if not can_view_all_shops(employee["role"]):
        return redirect(role_home_url(employee["role"]))

    from django.core.exceptions import PermissionDenied
    from django.utils.text import slugify

    from employees.models import EmployeeProfile
    from employees.module_permissions import employee_may
    from employees.workspace import (
        day_report_counted_statuses,
        day_report_section_enabled,
        day_report_status_mode,
    )
    from shops.models import ShopDaySession
    from shops.services import (
        build_shop_day_report_pdf,
        get_company_display_name,
    )

    profile = (
        EmployeeProfile.objects.select_related("user")
        .filter(pk=employee["id"])
        .first()
    )
    # Same gate as the portal's analytics_day_session view.
    if profile is None or not employee_may(profile, "analytics", "balances"):
        raise PermissionDenied()

    session = (
        ShopDaySession.objects.filter(pk=session_id)
        .select_related("shop", "opened_by__user", "closed_by__user")
        .first()
    )
    if session is None:
        raise Http404("Day session not found.")

    if (request.GET.get("download") or "").strip().lower() in (
        "1",
        "pdf",
        "download",
        "print",
    ):
        report_mode = day_report_status_mode(profile)
        printed_by = profile.user.get_full_name() or profile.user.username
        pdf_bytes = build_shop_day_report_pdf(
            shop=session.shop,
            session=session,
            printed_by=printed_by,
            company_name=get_company_display_name(),
            counted_statuses=day_report_counted_statuses(profile),
            section_visible=lambda key: day_report_section_enabled(
                profile, key
            ),
            sales_scope_label=(
                "Pending sales only"
                if report_mode == "pending"
                else "Confirmed sales only" if report_mode == "confirmed" else ""
            ),
        )
        stamp = timezone.localtime(session.opened_at).strftime("%Y%m%d")
        filename = (
            f"day-report-{slugify(session.shop.name) or 'shop'}-{stamp}.pdf"
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

    from employees.analytics_services import build_day_session_report_page

    context = build_day_session_report_page(
        profile=profile, request=request, session=session
    )
    page = context.get("page") or {}

    if (request.GET.get("estimate") or "").strip() == "1":
        tables = page.get("tables") or []
        row_total = sum(len(t.get("rows") or []) for t in tables)
        # Rough A4 estimate: header/KPIs ≈ a third page, each section
        # title + footnote ≈ 3 rows, ~34 body rows per page.
        a4_page_estimate = max(1, (row_total + len(tables) * 3 + 40) // 34)
        return JsonResponse(
            {
                "ok": True,
                "paper": "a4",
                "receipt_count": page.get("sales_count") or 0,
                "a4_page_estimate": a4_page_estimate,
                "kind_label": "Day report",
                "period_label": page.get("period_label") or "",
            }
        )

    paper = (request.GET.get("paper") or "").strip().lower()
    if paper == "50":
        paper = "58"
    if paper in ("a4", "80", "58"):
        return render(
            request,
            "employees/analytics_day_session_print.html",
            {
                "page": page,
                "paper": paper,
                "session": session,
                "printed_at": timezone.localtime(),
                "company_name": get_company_display_name(),
                "auto_print": (request.GET.get("auto") or "").strip() == "1",
                "ksh_prefix": "" if paper == "a4" else "KSh ",
            },
        )

    from employees.analytics_services import hydrate_table_zrows

    hydrate_table_zrows(page)
    if "back" not in request.GET:
        page["back_href"] = reverse("chat:all_shops")
        page["back_label"] = "All shops"
    return render(
        request,
        "chat/day_session_report.html",
        {
            "page": page,
            "session": session,
            "employee": employee,
            "printed_at": timezone.localtime(),
            "company_name": get_company_display_name(),
        },
    )


@require_POST
def logout_view(request):
    logout_shop(request)
    return redirect("chat:login")


@shop_required
def app_view(request):
    me = request.chat_shop
    trade = stock_ops.trade_flags(me)
    contacts = [
        {
            "id": s.pk,
            "name": s.name,
            "location": s.location,
            "phone": s.phone_number,
            "initials": services.initials(s.name),
            "tone": services.avatar_tone(s.pk),
        }
        for s in services.active_shops().exclude(pk=me.pk)
    ]
    context = {
        "company_name": _company_name(),
        "me": {
            "id": me.pk,
            "name": me.name,
            "location": me.location,
            "code": me.login_code,
            "initials": services.initials(me.name),
            "tone": services.avatar_tone(me.pk),
            "staff_view": current_employee(request) is not None,
            "needs_member_confirm": _needs_member_confirmation(request, me),
            "trade_out": trade["trade_out"],
            "trade_in": trade["trade_in"],
            "trade_enabled": trade["trade_out"] or trade["trade_in"],
            "stock_modes": stock_ops.shop_stock_modes(me),
        },
        "stock_req_json": json.dumps(store_stock_ops.stock_req_dict()),
        "shelf_req_json": json.dumps(
            store_stock_ops.get_stock_requirements()
            .as_shelf_requirements_dict()
        ),
        "conversations_json": json.dumps(_conversations_payload(me)),
        "contacts_json": json.dumps(contacts),
        "suggestions_json": json.dumps(bot_engine.BOT_SUGGESTIONS),
        "receipt_status_default_json": json.dumps(
            bot_engine.analytics_receipt_default()
        ),
        "expense_categories": stock_ops.EXPENSE_CATEGORIES,
    }
    return render(request, "chat/app.html", context)


@shop_required
def receipts_view(request):
    """Shop receipts browser — confirm, cancel, and return."""
    me = request.chat_shop
    pos = stock_ops.effective_pos_settings(me)
    confirm_methods = [
        {"value": m, "label": l}
        for m, l in (
            ("cash", "Cash"),
            ("mpesa", "M-Pesa"),
            ("card", "Card"),
            ("both", "Cash + M-Pesa"),
            ("cash_card", "Cash + Card"),
            ("mpesa_card", "M-Pesa + Card"),
            ("cash_mpesa_card", "Cash + M-Pesa + Card"),
        )
        if stock_ops.payment_method_enabled(pos, m)
    ]
    return render(
        request,
        "chat/receipts.html",
        {
            "company_name": _company_name(),
            "me": {
                "id": me.pk,
                "name": me.name,
                "location": me.location,
                "code": me.login_code,
                "initials": services.initials(me.name),
                "tone": services.avatar_tone(me.pk),
                "staff_view": current_employee(request) is not None,
            },
            "list_url": reverse("chat:api_receipts_list"),
            "detail_base": reverse("chat:api_receipt_detail", args=[0]),
            "confirm_url": reverse("chat:api_receipts_confirm"),
            "cancel_url": reverse("chat:api_receipts_cancel"),
            "return_url": reverse("chat:api_receipts_return"),
            "chat_url": reverse("chat:app"),
            "confirm_requires_details": bool(
                pos.get("require_mpesa_reference_on_confirm")
            ),
            "confirm_methods_json": json.dumps(confirm_methods),
        },
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@api_shop_required
@require_GET
def api_poll(request):
    """Combined heartbeat: presence touch + conversation list + active thread."""
    me = request.chat_shop
    # Stamp `now` before any reads so the client's next `changed_since`
    # watermark can never skip an update committed mid-request.
    now = timezone.now()
    convo_id = request.GET.get("conversation_id") or ""
    after_id = request.GET.get("after_id") or "0"
    try:
        after_id = int(after_id)
    except (TypeError, ValueError):
        after_id = 0
    changed_since = None
    raw_since = (request.GET.get("changed_since") or "").strip()
    if raw_since:
        try:
            changed_since = datetime.fromisoformat(raw_since)
            if changed_since.tzinfo is None:
                changed_since = timezone.make_aware(changed_since)
        except ValueError:
            changed_since = None

    active = None
    if convo_id:
        convo = services.get_member_conversation(convo_id, me)
        if convo is None:
            return _err("Conversation not found.", 404)
        active = _serialize_active(
            convo, me, after_id=after_id, changed_since=changed_since
        )

    # One small UPDATE marks this device online in every thread we share.
    Participant.objects.filter(shop_id=me.pk).update(
        last_seen_at=timezone.now()
    )

    return JsonResponse(
        {
            "ok": True,
            "now": now.isoformat(),
            "conversations": _conversations_payload(me),
            "active": active,
        }
    )


@api_shop_required
@require_GET
def api_history(request, conversation_id):
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None:
        return _err("Conversation not found.", 404)
    try:
        before_id = int(request.GET.get("before_id") or 0)
    except (TypeError, ValueError):
        before_id = 0
    me_part, peer = _participants(convo, me)
    group = convo.kind == ConversationKind.GROUP
    peers = (
        [p for p in convo.participants.all() if p.shop_id != me.pk]
        if group
        else None
    )
    sender_names = (
        {sid: s.name for sid, s in services.shops_map_for([convo]).items()}
        if group
        else None
    )
    qs = convo.messages.order_by("-id")
    if before_id:
        qs = qs.filter(id__lt=before_id)
    msgs = list(qs[: HISTORY_PAGE_SIZE + 1])
    has_more = len(msgs) > HISTORY_PAGE_SIZE
    msgs = msgs[:HISTORY_PAGE_SIZE]
    msgs.reverse()
    return JsonResponse(
        {
            "ok": True,
            "has_more": has_more,
            "messages": [
                services.serialize_message(
                    m, me, peer, services.is_bot_thread(convo),
                    peers=peers, sender_names=sender_names,
                )
                for m in msgs
            ],
        }
    )


@api_shop_required
@require_POST
def api_open(request):
    me = request.chat_shop
    data = _json_body(request)
    target = (data.get("target") or "").strip()

    if target == "assistant":
        convo = services.get_or_create_bot(me)
    else:
        try:
            other_id = int(target)
        except (TypeError, ValueError):
            return _err("Pick a shop to chat with.")
        if other_id == me.pk:
            return _err("You cannot chat with your own shop.")
        other = services.shop_by_id(other_id)
        if other is None:
            return _err("That shop is not available.")
        convo = services.get_or_create_direct(me, other)

    me_part, _peer = _participants(convo, me)
    if me_part:
        services.touch_presence(me_part)
    shops_map = services.shops_map_for([convo])
    return JsonResponse(
        {
            "ok": True,
            "conversation": services.serialize_conversation(convo, me, shops_map),
            "active": _serialize_active(convo, me),
        }
    )


@api_shop_required
@require_POST
def api_group_create(request):
    """POST {name, members:[shop_id…]} → create + open a group thread."""
    me = request.chat_shop
    data = _json_body(request)
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return _err("Give the group a name.")
    raw = data.get("members") or []
    member_ids = []
    for value in raw if isinstance(raw, list) else []:
        try:
            member_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    # Dedupe and never add the creator twice.
    member_ids = [i for i in dict.fromkeys(member_ids) if i != me.pk][:50]
    if not member_ids:
        return _err("Pick at least one shop to add.")
    members = [
        s for s in (services.shop_by_id(i) for i in member_ids) if s
    ]
    if not members:
        return _err("None of those shops are available.")

    convo = services.create_group(me, name, members)
    me_part, _peer = _participants(convo, me)
    if me_part:
        services.touch_presence(me_part)
        latest = (
            convo.messages.order_by("-id")
            .values_list("id", flat=True)
            .first()
            or 0
        )
        services.mark_read(me_part, latest)
    shops_map = services.shops_map_for([convo])
    return JsonResponse(
        {
            "ok": True,
            "conversation": services.serialize_conversation(convo, me, shops_map),
            "active": _serialize_active(convo, me),
        }
    )


@api_shop_required
@require_POST
def api_group_update(request, conversation_id):
    """POST {name} → rename a group the shop belongs to."""
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None or convo.kind != ConversationKind.GROUP:
        return _err("Group not found.", 404)
    data = _json_body(request)
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return _err("Give the group a name.")
    services.rename_group(convo, me, name)
    shops_map = services.shops_map_for([convo])
    return JsonResponse(
        {
            "ok": True,
            "conversation": services.serialize_conversation(convo, me, shops_map),
            "active": _serialize_active(convo, me),
        }
    )


@api_shop_required
@require_POST
def api_group_delete(request, conversation_id):
    """POST → delete a group for every participant."""
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None or convo.kind != ConversationKind.GROUP:
        return _err("Group not found.", 404)
    services.delete_group(convo)
    return JsonResponse({"ok": True, "deleted_id": conversation_id})


@api_shop_required
@require_POST
def api_send(request, conversation_id):
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None:
        return _err("Conversation not found.", 404)
    me_part, peer = _participants(convo, me)
    if me_part:
        services.touch_presence(me_part)

    upload = request.FILES.get("image")
    if upload is not None:
        msg, error = services.post_image(convo, me, upload)
    else:
        data = _json_body(request)
        msg, error = services.post_text(
            convo, me, data.get("body") or "", data.get("client_tag") or ""
        )
    if error:
        return _err(error)

    # Mark my own messages read on my side.
    if me_part:
        services.mark_read(me_part, msg.pk)

    reply_msg = None
    if convo.kind == ConversationKind.BOT and upload is None:
        reply_msg = services.post_bot_reply(
            convo,
            bot_engine.reply_for(me, msg.body),
            bot_engine.card_meta(me, msg.body),
        )
    elif convo.kind == ConversationKind.BOT and upload is not None:
        reply_msg = services.post_bot_reply(
            convo,
            "I can only read text for now — ask me things like *sales today* "
            "or *low stock*.",
        )
    elif services.is_stock_bot(convo) and upload is None:
        reply_msg = services.post_bot_reply(
            convo,
            bot_engine.reply_for_stock(me, msg.body),
            bot_engine.card_meta_stock(me, msg.body),
        )
    elif services.is_stock_bot(convo):
        reply_msg = services.post_bot_reply(
            convo,
            "I can only read text for now — ask me things like *stock in* "
            "or *stock report*.",
        )
    elif services.is_expense_bot(convo) and upload is None:
        reply_msg = services.post_bot_reply(
            convo,
            bot_engine.reply_for_expenses(me, msg.body),
            bot_engine.card_meta_expenses(me, msg.body),
        )
    elif services.is_expense_bot(convo):
        reply_msg = services.post_bot_reply(
            convo,
            "I can only read text for now — ask me things like "
            "*expenses* or *expense report*.",
        )

    bot_thread = services.is_bot_thread(convo)
    group = convo.kind == ConversationKind.GROUP
    peers = (
        [p for p in convo.participants.all() if p.shop_id != me.pk]
        if group
        else None
    )
    sender_names = (
        {sid: s.name for sid, s in services.shops_map_for([convo]).items()}
        if group
        else None
    )
    payload = {
        "ok": True,
        "message": services.serialize_message(
            msg, me, peer, bot_thread,
            peers=peers, sender_names=sender_names,
        ),
    }
    if reply_msg is not None:
        payload["reply"] = services.serialize_message(
            reply_msg, me, peer, bot_thread,
            peers=peers, sender_names=sender_names,
        )
    return JsonResponse(payload)


@api_shop_required
@require_POST
def api_read(request, conversation_id):
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None:
        return _err("Conversation not found.", 404)
    me_part, _peer = _participants(convo, me)
    if me_part is None:
        return _err("Not a participant.", 403)
    data = _json_body(request)
    try:
        up_to_id = int(data.get("up_to_id") or 0)
    except (TypeError, ValueError):
        up_to_id = 0
    latest = convo.messages.order_by("-id").values_list("id", flat=True).first() or 0
    services.mark_read(me_part, min(up_to_id or latest, latest))
    return JsonResponse({"ok": True})


@api_shop_required
@require_POST
def api_typing(request, conversation_id):
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None:
        return _err("Conversation not found.", 404)
    if services.is_bot_thread(convo):
        return JsonResponse({"ok": True})
    me_part, _peer = _participants(convo, me)
    if me_part is None:
        return _err("Not a participant.", 403)
    services.set_typing(me_part)
    return JsonResponse({"ok": True})


@api_shop_required
@require_GET
def api_item_search(request):
    """Live item suggestions for transfer / stock-in pickers."""
    me = request.chat_shop
    return JsonResponse(
        {"ok": True, "items": stock_ops.item_search(me, request.GET.get("q") or "")}
    )


@api_shop_required
@require_GET
def api_shelf_item_search(request):
    """Live shelf-item suggestions for the shelf stock pickers."""
    me = request.chat_shop
    return JsonResponse(
        {
            "ok": True,
            "items": stock_ops.shelf_item_search(
                me, request.GET.get("q") or ""
            ),
        }
    )


@api_shop_required
@require_GET
def api_serial_search(request):
    """Live serial-number suggestions (in-stock at the signed-in shop)."""
    me = request.chat_shop
    return JsonResponse(
        {
            "ok": True,
            "serials": stock_ops.serial_search(
                me,
                request.GET.get("item_id"),
                request.GET.get("q") or "",
            ),
        }
    )


@api_shop_required
@require_GET
def api_supplier_search(request):
    """Live supplier suggestions (name or phone) for stock-in / expense."""
    kind = (request.GET.get("kind") or "stock").strip().lower()
    if kind not in ("stock", "expense"):
        kind = "stock"
    return JsonResponse(
        {
            "ok": True,
            "suppliers": stock_ops.supplier_search(
                request.GET.get("q") or "", kind=kind
            ),
        }
    )


@require_GET
def api_receipt_items(request, receipt_id):
    """Lines on one receipt — the credits card's eye-icon preview.

    Shop sessions may only open their own receipts; signed-in staff
    (the all-shops assistant) can open any of them.
    """
    me = current_shop(request)
    emp = current_employee(request)
    if me is None and emp is None:
        return JsonResponse(
            {"ok": False, "error": "authentication required"}, status=401
        )
    receipt = ShopReceipt.objects.filter(pk=receipt_id).first()
    if receipt is None or (
        emp is None and (me is None or receipt.shop_id != me.pk)
    ):
        return _err("Receipt not found.", 404)
    items = [
        {
            "name": l.item_name,
            "qty": bot_engine.qty_fmt(
                (l.quantity or 0) - (l.returned_quantity or 0)
            ),
            "price": bot_engine.money(l.unit_price),
            "total": bot_engine.money(l.line_total),
            "serials": l.serial_numbers or [],
        }
        for l in ShopReceiptLine.objects.filter(
            receipt_id=receipt.pk
        ).order_by("pk")
    ]
    return JsonResponse(
        {
            "ok": True,
            "receipt": {
                "number": stock_ops._receipt_tally_code(receipt),
                "customer": receipt.client_name or "Walk-in customer",
                "total": bot_engine.money(receipt.total),
                "paid": bot_engine.money(receipt.amount_paid),
            },
            "items": items,
        }
    )


@api_shop_required
@require_GET
def api_receipts_list(request):
    """Filtered receipt list for the Receipts page."""
    me = request.chat_shop
    try:
        rows = stock_ops.list_receipts(
            me,
            mode=request.GET.get("mode") or "day",
            day=request.GET.get("day") or "",
            date_from=request.GET.get("from") or "",
            date_to=request.GET.get("to") or "",
            month=request.GET.get("month") or "",
            year=request.GET.get("year") or "",
            status=request.GET.get("status") or "",
            kind=request.GET.get("kind") or "",
            query=request.GET.get("q") or "",
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))
    return JsonResponse({"ok": True, "receipts": rows})


@api_shop_required
@require_GET
def api_receipt_detail(request, receipt_id):
    """Full receipt detail for the Receipts page panel."""
    me = request.chat_shop
    try:
        detail = stock_ops.receipt_detail(me, receipt_id)
    except stock_ops.StockError as exc:
        return _err(str(exc), 404)
    return JsonResponse({"ok": True, "receipt": detail})


@api_shop_required
@require_POST
def api_receipts_confirm(request):
    me = request.chat_shop
    data = _json_body(request)
    try:
        result = stock_ops.confirm_receipts_bulk(
            me,
            data.get("ids") or [],
            staff_code=data.get("staff_code") or "",
            mpesa_receipt_number=data.get("mpesa_receipt_number") or "",
            payment_at=data.get("payment_at") or "",
            amount=data.get("amount"),
            payment_method=data.get("payment_method") or "",
            cash_amount=data.get("cash_amount"),
            mpesa_amount=data.get("mpesa_amount"),
            card_amount=data.get("card_amount"),
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))
    return JsonResponse(result)


@api_shop_required
@require_POST
def api_receipts_cancel(request):
    me = request.chat_shop
    data = _json_body(request)
    try:
        result = stock_ops.cancel_receipts_bulk(
            me,
            data.get("ids") or [],
            staff_code=data.get("staff_code") or "",
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))
    return JsonResponse(result)


@api_shop_required
@require_POST
def api_receipts_return(request):
    me = request.chat_shop
    data = _json_body(request)
    try:
        result = stock_ops.return_receipts_bulk(
            me,
            data.get("receipts") or [],
            reason=data.get("reason") or "",
            staff_code=data.get("staff_code") or "",
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))
    return JsonResponse(result)


@api_shop_required
@require_POST
def api_transfer(request, conversation_id):
    """Create a pending stock transfer to the chat peer (or a chosen shop).

    Mirrors MY-SHOP's transfer request: stock moves only when the receiving
    shop confirms it in MY-SHOP. The card message is the instant notice;
    `supplier_notified=False` flags it in their pending stock requests.
    """
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None:
        return _err("Conversation not found.", 404)
    if services.is_bot_thread(convo):
        return _err("Transfers can only be sent in a shop chat.")
    me_part, peer = _participants(convo, me)

    data = _json_body(request)
    dest_id = data.get("destination_shop_id")
    if dest_id:
        destination = services.shop_by_id(dest_id)
    else:
        destination = services.shop_by_id(peer.shop_id) if peer else None

    stock_kind = (data.get("stock_kind") or "items").strip().lower()
    if _needs_member_confirmation(request, me) and not (
        data.get("staff_code") or ""
    ).strip():
        if destination is not None:
            data = dict(data)
            data["destination_shop_id"] = destination.pk
        return _post_pending(
            request, me,
            "shelf_transfer" if stock_kind == "shelf" else "transfer",
            data,
        )

    try:
        if stock_kind == "shelf":
            dest_convo, msg = _create_shelf_transfer(
                me, destination, data,
                require_member=_needs_member_confirmation(request, me),
            )
        else:
            dest_convo, msg = _create_transfer(
                me, destination, data,
                require_member=_needs_member_confirmation(request, me),
            )
    except stock_ops.StockError as exc:
        return _err(str(exc))

    payload = {"ok": True, "conversation_id": dest_convo.pk}
    if dest_convo.pk == convo.pk:
        payload["message"] = services.serialize_message(msg, me, peer)
    return JsonResponse(payload)


def _create_transfer(me, destination, data, require_member=False):
    """Shared transfer creation — returns (dest_convo, card_message)."""
    movement, lines = stock_ops.create_transfer(
        me, destination, data.get("lines") or [],
        staff_code=data.get("staff_code") or "",
        note=data.get("note") or "",
        require_member=require_member,
    )
    movement_lines = list(
        stock_ops.StockMovementLine.objects.filter(
            movement_id=movement.pk
        ).order_by("pk")
    )
    summary = ", ".join(
        f"{line['quantity']}× {line['item'].name}" for line in lines
    )
    body = f"Stock transfer → {destination.name}: {summary}"
    card = stock_ops.transfer_card(movement, me, destination, movement_lines)
    # Post the notification into the conversation with the actual receiving
    # shop — creating it if needed — so they are always notified, even when
    # the dropdown destination differs from the current chat peer.
    dest_convo = services.get_or_create_direct(me, destination)
    msg = services.post_card(dest_convo, me, body, card)
    # Tag lets us find this card again when the transfer is responded to.
    msg.client_tag = f"transfer:{movement.pk}"
    msg.save(update_fields=["client_tag"])
    dest_me_part, _ = _participants(dest_convo, me)
    if dest_me_part:
        services.mark_read(dest_me_part, msg.pk)
        services.touch_presence(dest_me_part)
    # A read-only copy lands in the shared Transfers feed — responses are
    # handled from the direct thread, the feed just tracks the activity.
    _activity_post(
        "transfer", me, body,
        stock_ops.transfer_card(
            movement, me, destination, movement_lines, readonly=True
        ),
        by=stock_ops.employee_label(movement.created_by_id),
        client_tag=f"transfer-feed:{movement.pk}",
    )
    return dest_convo, msg


def _create_shelf_transfer(me, destination, data, require_member=False):
    """Immediate shelf transfer — returns (dest_convo, card_message).

    Shelf transfers move stock in one shot (OUT at source + IN at the
    destination, auto-registering the item there); the card is a
    read-only notice, not an accept/decline request.
    """
    destination, legs = stock_ops.create_shelf_transfer(
        me,
        destination,
        data.get("lines") or [],
        staff_code=data.get("staff_code") or "",
        note=data.get("note") or "",
        require_member=require_member,
    )
    lines = [leg["line"] for leg in legs]
    summary = ", ".join(
        f"{line['quantity']}× {line['item'].name}" for line in lines
    )
    body = f"Shelf transfer → {destination.name}: {summary}"
    card = stock_ops.shelf_movements_card(
        "Shelf transfer",
        me,
        lines,
        direction="out",
        detail=f"{me.name} → {destination.name}",
    )
    dest_convo = services.get_or_create_direct(me, destination)
    msg = services.post_card(dest_convo, me, body, card)
    dest_me_part, _ = _participants(dest_convo, me)
    if dest_me_part:
        services.mark_read(dest_me_part, msg.pk)
        services.touch_presence(dest_me_part)
    _activity_post(
        "transfer", me, body, card,
        by=stock_ops.employee_label(
            legs[0]["out"].created_by_id if legs else None
        ),
    )
    return dest_convo, msg


@api_shop_required
@require_POST
def api_transfers(request):
    """Conversation-free transfer — used from the chat list page FAB."""
    me = request.chat_shop
    data = _json_body(request)
    stock_kind = (data.get("stock_kind") or "items").strip().lower()
    destination = services.shop_by_id(data.get("destination_shop_id"))
    if destination is None:
        return _err("Select a shop to transfer to.")
    if _needs_member_confirmation(request, me) and not (
        data.get("staff_code") or ""
    ).strip():
        return _post_pending(
            request, me,
            "shelf_transfer" if stock_kind == "shelf" else "transfer",
            data,
        )
    try:
        if stock_kind == "shelf":
            dest_convo, msg = _create_shelf_transfer(
                me, destination, data,
                require_member=_needs_member_confirmation(request, me),
            )
        else:
            dest_convo, msg = _create_transfer(
                me, destination, data,
                require_member=_needs_member_confirmation(request, me),
            )
    except stock_ops.StockError as exc:
        return _err(str(exc))
    return JsonResponse(
        {
            "ok": True,
            "conversation_id": dest_convo.pk,
            "message": services.serialize_message(
                msg, me, _participants(dest_convo, me)[1]
            ),
        }
    )


@api_shop_required
@require_POST
def api_transfer_respond(request, movement_id):
    """Accept (with received quantities) or decline a pending transfer.

    Mirrors MY-SHOP's respond_to_stock_request — stock only moves on accept
    when every received quantity matches what was sent. A member of the
    receiving shop enters their 6-digit staff code so the movement records
    which employee accepted or declined (`responded_by_id`).
    """
    me = request.chat_shop
    data = _json_body(request)
    try:
        movement = stock_ops.respond_to_transfer(
            me,
            movement_id,
            data.get("decision") or "",
            data.get("received") or {},
            data.get("reason") or "",
            data.get("serials") or {},
            staff_code=data.get("staff_code") or "",
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))

    responder = stock_ops.employee_label(movement.responded_by_id)

    # Update the original card message so the status persists on reload.
    card_msg = Message.objects.filter(
        client_tag=f"transfer:{movement.pk}"
    ).order_by("id").first()
    convo = card_msg.conversation if card_msg else None
    if card_msg is not None:
        meta = dict(card_msg.meta or {})
        transfer = dict(meta.get("transfer") or {})
        transfer["status"] = movement.request_status
        if movement.response_note:
            transfer["response_note"] = movement.response_note
        if responder:
            transfer["responded_by"] = responder
        meta["transfer"] = transfer
        card_msg.meta = meta
        card_msg.save(update_fields=["meta", "updated_at"])

    # The read-only copy in the shared Transfers feed tracks the outcome
    # too — update its status so the feed stays accurate on reload.
    feed_msg = Message.objects.filter(
        client_tag=f"transfer-feed:{movement.pk}"
    ).order_by("id").first()
    if feed_msg is not None:
        meta = dict(feed_msg.meta or {})
        transfer = dict(meta.get("transfer") or {})
        transfer["status"] = movement.request_status
        if movement.response_note:
            transfer["response_note"] = movement.response_note
        if responder:
            transfer["responded_by"] = responder
        meta["transfer"] = transfer
        feed_msg.meta = meta
        feed_msg.save(update_fields=["meta", "updated_at"])

    # Post a follow-up note both sides can see.
    note = None
    if convo is not None:
        by = f" by {responder}" if responder else ""
        if movement.request_status == "fulfilled":
            text = (
                f"Transfer confirmed{by} — stock moved to {me.name}."
            )
        else:
            text = (
                f"Transfer declined{by}: " + (movement.response_note or "")
            )
        note = services.post_card(convo, me, text, {})
        _activity_post("transfer", me, text)

    me_part, peer = _participants(convo, me) if convo else (None, None)
    if me_part:
        services.mark_read(me_part, note.pk if note else movement.pk)
    payload = {
        "ok": True,
        "status": movement.request_status,
        "card_message_id": card_msg.pk if card_msg else None,
        # Serialized per-viewer so a declined card keeps its quantities
        # hidden from the receiving shop.
        "card_meta": (
            services.serialize_message(card_msg, me, peer)["meta"]
            if card_msg
            else None
        ),
    }
    if note is not None:
        payload["message"] = services.serialize_message(note, me, peer)
    return JsonResponse(payload)


def _activity_post(activity, shop, body, meta=None, *, by="",
                   client_tag=""):
    """Mirror a completed action into its shared activity-feed group.

    The same card lands in the all-shops feed so everyone can track the
    day's activity; `by` names the staff member who ran it (rendered as a
    "by …" byline under the card).
    """
    convo = services.activity_conversation(activity)
    if convo is None:
        return None
    data = dict(meta or {})
    if by:
        data["by"] = by
    msg = services.post_card(convo, shop, body, data)
    if client_tag:
        msg.client_tag = client_tag
        msg.save(update_fields=["client_tag"])
    for p in convo.participants.all():
        if p.shop_id == shop.pk:
            services.mark_read(p, msg.pk)
            break
    return msg


def _post_pending(request, me, action: str, data: dict) -> JsonResponse:
    """Dry-run the action and post a pending-confirmation card.

    Non-member staff can't complete stock actions directly — the validated
    payload is stored on a card in the shop's Assistant thread until a
    member of that shop confirms with their staff code.
    """
    emp = current_employee(request)
    payload = {
        k: v
        for k, v in data.items()
        if k not in ("staff_code", "csrfmiddlewaretoken")
    }
    try:
        body, meta = stock_ops.build_pending(
            action,
            me,
            payload,
            requested_by=stock_ops.employee_label(emp["id"]) if emp else "",
            requested_by_id=emp["id"] if emp else None,
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))
    convo = services.get_or_create_bot(me)
    msg = services.post_bot_reply(convo, body, meta)
    me_part, _ = _participants(convo, me)
    if me_part:
        services.mark_read(me_part, msg.pk)
    return JsonResponse(
        {
            "ok": True,
            "pending": True,
            "message": services.serialize_message(msg, me, None, True),
            "conversation_id": convo.pk,
        }
    )


@api_shop_required
@require_POST
def api_quick_stockin(request, conversation_id):
    """The `#` quick stock-in — posts into the *current* conversation.

    With a staff code the stock-in executes immediately and a "Stock
    received" card lands in the chat. Without one it posts a pending-
    confirmation card; a member of the posting shop confirms it with their
    code and the card becomes the received card in place.
    """
    me = request.chat_shop
    convo = services.get_member_conversation(conversation_id, me)
    if convo is None:
        return _err("Conversation not found.", 404)
    me_part, peer = _participants(convo, me)
    if me_part:
        services.touch_presence(me_part)

    data = _json_body(request)
    supplier_name = (data.get("supplier_name") or "").strip() or "QUICK STOCK-IN"
    supplier_phone = (
        (data.get("supplier_phone") or "").strip()
        or (me.phone_number or "").strip()
    )
    if not supplier_phone:
        return _err(
            "Your shop has no phone number on file — "
            "use the Stock in panel instead."
        )
    payload = {
        "lines": data.get("lines") or [],
        "supplier_name": supplier_name,
        "supplier_phone": supplier_phone,
    }
    code = (data.get("staff_code") or "").strip()
    group = convo.kind == ConversationKind.GROUP
    peers = (
        [p for p in convo.participants.all() if p.shop_id != me.pk]
        if group
        else None
    )
    sender_names = (
        {sid: s.name for sid, s in services.shops_map_for([convo]).items()}
        if group
        else None
    )

    if code:
        # Member/owner path: a typed code executes immediately.
        try:
            movement, lines = stock_ops.create_stock_in(
                me,
                payload["lines"],
                supplier_name=supplier_name,
                supplier_phone=supplier_phone,
                staff_code=code,
                require_member=_needs_member_confirmation(request, me),
            )
        except stock_ops.StockError as exc:
            return _err(str(exc))
        summary = ", ".join(
            f"{line['quantity']}× {line['item'].name}" for line in lines
        )
        body = f"Stock received: {summary} — {supplier_name.upper()}"
        card = stock_ops.stock_in_card(
            movement, me, lines, supplier_name.upper()
        )
        msg = services.post_card(convo, me, body, card)
        _activity_post(
            "stockin", me, body, card,
            by=stock_ops.employee_label(movement.created_by_id),
        )
    else:
        emp = current_employee(request)
        try:
            body, meta = stock_ops.build_pending(
                "stockin",
                me,
                payload,
                requested_by=(
                    stock_ops.employee_label(emp["id"]) if emp else me.name
                ),
                requested_by_id=emp["id"] if emp else None,
            )
        except stock_ops.StockError as exc:
            return _err(str(exc))
        # Only members of the shop receiving the stock may confirm it.
        meta["pending_action"]["shop_id"] = me.pk
        meta["pending_action"]["shop_name"] = me.name
        msg = services.post_card(convo, me, body, meta)

    if me_part:
        services.mark_read(me_part, msg.pk)
    return JsonResponse(
        {
            "ok": True,
            "pending": not code,
            "message": services.serialize_message(
                msg, me, peer, services.is_bot_thread(convo),
                peers=peers, sender_names=sender_names,
            ),
            "conversation_id": convo.pk,
        }
    )


@api_shop_required
@require_POST
def api_pending_confirm(request, message_id):
    """A shop member's staff code executes a stored pending request.

    The pending card becomes the real receipt/movement card in place; for
    transfers the destination card is also posted into the direct
    conversation so the receiving shop is notified.
    """
    me = request.chat_shop
    msg = (
        Message.objects.filter(
            pk=message_id,
            conversation__participants__shop_id=me.pk,
        )
        .select_related("conversation")
        .first()
    )
    pa = (msg.meta or {}).get("pending_action") if msg else None
    if msg is None or not isinstance(pa, dict):
        return _err("Request not found.", 404)
    if pa.get("status") != "pending":
        return _err("This request was already handled.")

    data = _json_body(request)
    # Group/thread cards name the shop that owns the action — only its
    # members' codes verify. Cards without shop_id act on the viewer's shop.
    action_shop = services.shop_by_id(pa.get("shop_id")) or me
    emp_id = stock_ops.member_employee_id(
        data.get("staff_code") or "", action_shop
    )
    if emp_id is None:
        return _err(
            f"Only a member of {action_shop.name} can confirm — "
            "ask a shop staff member to enter their code."
        )
    try:
        result = stock_ops.execute_pending(action_shop, pa, emp_id)
    except stock_ops.StockError as exc:
        return _err(str(exc))

    member_label = stock_ops.employee_label(emp_id)
    meta = dict(msg.meta or {})
    meta.pop("pending_action", None)
    meta.update(result["meta"])
    meta["confirmed"] = {"by": member_label}
    msg.meta = meta
    msg.body = result["body"]
    msg.save(update_fields=["meta", "body", "updated_at"])

    # Mirror the completed action into its shared activity-feed group so
    # every shop can track the day's stock-in/transfers/trades/expenses.
    activity = {
        "stockin": "stockin",
        "stockout": "stockout",
        "shelf_in": "stockin",
        "shelf_out": "stockout",
        "shelf_transfer": "transfer",
        "transfer": "transfer",
        "expense": "expense",
        "trade": "trade",
    }.get(result["action"])
    if activity is not None:
        _activity_post(
            activity,
            action_shop,
            result["body"],
            result["meta"],
            by=member_label,
            client_tag=(
                f"transfer-feed:{result['movement'].pk}"
                if result["action"] == "transfer"
                else ""
            ),
        )

    convo = msg.conversation
    me_part, peer = _participants(convo, me)
    group = convo.kind == ConversationKind.GROUP
    peers = (
        [p for p in convo.participants.all() if p.shop_id != me.pk]
        if group
        else None
    )
    sender_names = (
        {sid: s.name for sid, s in services.shops_map_for([convo]).items()}
        if group
        else None
    )
    payload = {
        "ok": True,
        "message": services.serialize_message(
            msg, me, peer, services.is_bot_thread(convo),
            peers=peers, sender_names=sender_names,
        ),
        "whatsapp_url": result.get("whatsapp_url") or "",
    }
    if result["action"] == "stockin":
        # The quick `#` flow: the card becomes the received card and a note
        # lands in the chat so every member sees it went through.
        note = services.post_card(
            convo,
            action_shop,
            result["body"]
            + (f" — confirmed by {member_label}" if member_label else ""),
            {},
        )
        if me_part:
            services.mark_read(me_part, note.pk)
        payload["note"] = services.serialize_message(
            note, me, peer, services.is_bot_thread(convo),
            peers=peers, sender_names=sender_names,
        )
    if result["action"] == "transfer":
        destination = result["destination"]
        dest_convo = services.get_or_create_direct(me, destination)
        # Rebuild the card non-readonly — result["meta"] is the readonly
        # summary for the sender's thread; the destination must be able to
        # accept/decline from their copy.
        dest_meta = stock_ops.transfer_card(
            result["movement"], me, destination, result["movement_lines"]
        )
        card_msg = services.post_card(
            dest_convo, me, result["body"], dest_meta
        )
        card_msg.client_tag = f"transfer:{result['movement'].pk}"
        card_msg.save(update_fields=["client_tag"])
        dest_me_part, dest_peer = _participants(dest_convo, me)
        if dest_me_part:
            services.mark_read(dest_me_part, card_msg.pk)
            services.touch_presence(dest_me_part)
        payload["card"] = services.serialize_message(
            card_msg, me, dest_peer
        )
        payload["card_conversation_id"] = dest_convo.pk
    if result["action"] == "shelf_transfer":
        # The shelf legs already moved — the destination card is a
        # read-only notice, not an accept/decline request.
        destination = result["destination"]
        dest_convo = services.get_or_create_direct(action_shop, destination)
        dest_meta = stock_ops.shelf_movements_card(
            "Shelf transfer received",
            destination,
            result["lines"],
            direction="in",
            detail=f"From {action_shop.name}",
        )
        card_msg = services.post_card(
            dest_convo, action_shop, result["body"], dest_meta
        )
        dest_me_part, dest_peer = _participants(dest_convo, action_shop)
        if dest_me_part:
            services.mark_read(dest_me_part, card_msg.pk)
            services.touch_presence(dest_me_part)
        payload["card"] = services.serialize_message(
            card_msg, me, dest_peer
        )
        payload["card_conversation_id"] = dest_convo.pk
    return JsonResponse(payload)


@api_shop_required
@require_POST
def api_stock_in(request):
    """Record stock bought into the signed-in shop (immediate movement).

    Supplier name + phone are required — the row is upserted into
    items_supplier so it auto-suggests next time. The confirmation card is
    posted privately to the shop's Assistant thread.
    """
    me = request.chat_shop
    data = _json_body(request)
    supplier_name = (data.get("supplier_name") or "").strip()
    supplier_phone = (data.get("supplier_phone") or "").strip()
    stock_kind = (data.get("stock_kind") or "items").strip().lower()
    if _needs_member_confirmation(request, me) and not (
        data.get("staff_code") or ""
    ).strip():
        return _post_pending(
            request, me, "shelf_in" if stock_kind == "shelf" else "stockin",
            data,
        )
    if stock_kind == "shelf":
        try:
            movements, lines = stock_ops.create_shelf_stock_in(
                me,
                data.get("lines") or [],
                supplier_name=supplier_name,
                supplier_phone=supplier_phone,
                payment_status=data.get("payment_status") or "",
                note=data.get("note") or "",
                staff_code=data.get("staff_code") or "",
                require_member=_needs_member_confirmation(request, me),
            )
        except stock_ops.StockError as exc:
            return _err(str(exc))
        convo = services.get_or_create_bot(me)
        summary = ", ".join(
            f"{line['quantity']}× {line['item'].name}" for line in lines
        )
        body = f"Shelf stock received: {summary}"
        if supplier_name:
            body += f" — {supplier_name.upper()}"
        card = stock_ops.shelf_movements_card(
            "Shelf stock received",
            me,
            lines,
            direction="in",
            supplier_name=supplier_name.upper(),
        )
        msg = services.post_bot_reply(convo, body, card)
        me_part, _peer = _participants(convo, me)
        if me_part:
            services.mark_read(me_part, msg.pk)
        _activity_post(
            "stockin", me, body, card,
            by=stock_ops.employee_label(
                movements[0].created_by_id if movements else None
            ),
        )
        return JsonResponse(
            {
                "ok": True,
                "message": services.serialize_message(msg, me, None, True),
                "conversation_id": convo.pk,
            }
        )
    try:
        movement, lines = stock_ops.create_stock_in(
            me,
            data.get("lines") or [],
            supplier_name=supplier_name,
            supplier_phone=supplier_phone,
            payment_status=data.get("payment_status") or "",
            staff_code=data.get("staff_code") or "",
            require_member=_needs_member_confirmation(request, me),
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))

    convo = services.get_or_create_bot(me)
    summary = ", ".join(
        f"{line['quantity']}× {line['item'].name}" for line in lines
    )
    body = f"Stock received: {summary} — {supplier_name.upper()}"
    card = stock_ops.stock_in_card(movement, me, lines, supplier_name.upper())
    msg = services.post_bot_reply(convo, body, card)
    me_part, _peer = _participants(convo, me)
    if me_part:
        services.mark_read(me_part, msg.pk)
    _activity_post(
        "stockin", me, body, card,
        by=stock_ops.employee_label(movement.created_by_id),
    )
    return JsonResponse(
        {
            "ok": True,
            "message": services.serialize_message(msg, me, None, True),
            "conversation_id": convo.pk,
        }
    )


@api_shop_required
@require_POST
def api_stock_out(request):
    """Record stock leaving the shop — item stock or shelf stock.

    Item stock-out mirrors MY-SHOP's stock-management OUT movement
    (serials are consumed, weighted-avg cost stamped on each line).
    Shelf stock-out delegates to record_shelf_movement(mode="out").
    """
    me = request.chat_shop
    data = _json_body(request)
    stock_kind = (data.get("stock_kind") or "items").strip().lower()
    if _needs_member_confirmation(request, me) and not (
        data.get("staff_code") or ""
    ).strip():
        return _post_pending(
            request, me,
            "shelf_out" if stock_kind == "shelf" else "stockout",
            data,
        )
    try:
        if stock_kind == "shelf":
            movements, lines = stock_ops.create_shelf_stock_out(
                me,
                data.get("lines") or [],
                reason=data.get("reason") or "",
                refund=data.get("refund") or "",
                refund_amount=data.get("refund_amount"),
                note=data.get("note") or "",
                staff_code=data.get("staff_code") or "",
                require_member=_needs_member_confirmation(request, me),
            )
            card = stock_ops.shelf_movements_card(
                "Shelf stock out",
                me,
                lines,
                direction="out",
                detail=stock_ops.REASON_LABELS.get(
                    (data.get("reason") or "").strip().lower(), ""
                ),
            )
            actor_id = (
                movements[0].created_by_id if movements else None
            )
        else:
            movement, lines = stock_ops.create_stock_out(
                me,
                data.get("lines") or [],
                reason=data.get("reason") or "",
                refund=data.get("refund") or "",
                refund_amount=data.get("refund_amount"),
                note=data.get("note") or "",
                staff_code=data.get("staff_code") or "",
                require_member=_needs_member_confirmation(request, me),
            )
            card = stock_ops.stock_out_card(movement, me, lines)
            actor_id = movement.created_by_id
    except stock_ops.StockError as exc:
        return _err(str(exc))

    convo = services.get_or_create_bot(me)
    summary = ", ".join(
        f"{line['quantity']}× {line['item'].name}" for line in lines
    )
    body = (
        ("Shelf stock removed: " if stock_kind == "shelf" else "Stock removed: ")
        + summary
    )
    msg = services.post_bot_reply(convo, body, card)
    me_part, _peer = _participants(convo, me)
    if me_part:
        services.mark_read(me_part, msg.pk)
    _activity_post(
        "stockout", me, body, card,
        by=stock_ops.employee_label(actor_id),
    )
    return JsonResponse(
        {
            "ok": True,
            "message": services.serialize_message(msg, me, None, True),
            "conversation_id": convo.pk,
        }
    )


@api_shop_required
@require_POST
def api_sale(request):
    """Record a sale — mirrors MY-SHOP's checkout (receipt + stock + serials).

    Sales, credit sales and quotations are exempt from member confirmation
    (only stock-in and transfers need it): a signed-in staff member
    completes them under their own employee profile when no code is typed;
    a typed code only needs to be an active employee, not a shop member.
    The receipt card is posted privately to the shop's Assistant thread.
    """
    me = request.chat_shop
    data = _json_body(request)
    emp = current_employee(request)
    code = (data.get("staff_code") or "").strip()
    kind = (data.get("kind") or "sale").strip().lower()
    if kind in stock_ops.TRADE_KINDS:
        return _api_trade(request, me, data, kind)
    employee_id = emp["id"] if emp is not None and not code else None
    due = None
    if data.get("credit_due_date"):
        try:
            from datetime import date

            due = date.fromisoformat(str(data["credit_due_date"])[:10])
        except (TypeError, ValueError):
            return _err("Enter a valid payment due date.")
    try:
        receipt, lines = stock_ops.create_sale(
            me,
            data.get("lines") or [],
            kind=kind,
            payment_method=data.get("payment_method") or "",
            cash_amount=data.get("cash_amount"),
            mpesa_amount=data.get("mpesa_amount"),
            card_amount=data.get("card_amount"),
            client_name=data.get("client_name") or "",
            client_phone=data.get("client_phone") or "",
            credit_due_date=due,
            staff_code=data.get("staff_code") or "",
            share_whatsapp=bool(data.get("share_whatsapp")),
            employee_id=employee_id,
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))

    convo = services.get_or_create_bot(me)
    total = sum((l["line_total"] for l in lines))
    body = (
        f"{stock_ops.receipt_label(receipt.kind)} {receipt.receipt_number} — "
        f"{len(lines)} item"
        f"{'s' if len(lines) != 1 else ''}, KSh {total:,.2f}"
    )
    card = stock_ops.sale_card(receipt, lines, total)
    msg = services.post_bot_reply(convo, body, card)
    me_part, _ = _participants(convo, me)
    if me_part:
        services.mark_read(me_part, msg.pk)
    return JsonResponse(
        {
            "ok": True,
            "receipt": receipt.receipt_number,
            "message": services.serialize_message(msg, me, None, True),
            "conversation_id": convo.pk,
            "whatsapp_url": stock_ops.quotation_whatsapp_url(
                receipt,
                lines,
                share_whatsapp=bool(data.get("share_whatsapp")),
            ),
        }
    )


@api_shop_required
@require_POST
def api_expense(request):
    """Register a shop expense — mirrors MY-SHOP's register_shop_expense.

    Supplier name + phone are required (upserted into shops_expensesupplier).
    Gated like stock-in and transfers: staff acting on a shop they aren't
    allocated to send a pending-confirmation card. The receipt card posts
    privately to the shop's Assistant thread.
    """
    me = request.chat_shop
    data = _json_body(request)
    if _needs_member_confirmation(request, me) and not (
        data.get("staff_code") or ""
    ).strip():
        return _post_pending(request, me, "expense", data)
    try:
        expenses = stock_ops.create_expenses(
            me,
            payload=data,
            payment_status=data.get("payment_status") or "",
            supplier_name=data.get("supplier_name") or "",
            supplier_phone=data.get("supplier_phone") or "",
            staff_code=data.get("staff_code") or "",
            require_member=_needs_member_confirmation(request, me),
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))

    from decimal import Decimal

    total = sum((e.amount for e in expenses), Decimal("0.00"))
    first = expenses[0]
    convo = services.get_or_create_bot(me)
    if len(expenses) == 1:
        body = (
            f"Expense {first.name} — KSh {first.amount:,.2f}"
            f" · {first.payment_status} — {first.supplier_name}"
        )
    else:
        body = (
            f"{len(expenses)} expenses — KSh {total:,.2f}"
            f" · {first.payment_status} — {first.supplier_name}"
        )
    card = stock_ops.expense_card(expenses, me)
    msg = services.post_bot_reply(convo, body, card)
    me_part, _ = _participants(convo, me)
    if me_part:
        services.mark_read(me_part, msg.pk)
    _activity_post(
        "expense", me, body, card,
        by=stock_ops.employee_label(first.created_by_id),
    )
    return JsonResponse(
        {
            "ok": True,
            "message": services.serialize_message(msg, me, None, True),
            "conversation_id": convo.pk,
        }
    )


def _api_trade(request, me, data, kind):
    """Trade out / trade in — gated like stock-in and transfers.

    Trades move stock without payment, so staff acting on a shop they aren't
    allocated to need a member's confirmation (pending card or member code).
    The receipt card is posted privately to the shop's Assistant thread.
    """
    if _needs_member_confirmation(request, me) and not (
        data.get("staff_code") or ""
    ).strip():
        return _post_pending(request, me, "trade", data)
    try:
        receipt, lines = stock_ops.create_trade(
            me,
            data.get("lines") or [],
            kind=kind,
            client_name=data.get("client_name") or "",
            client_phone=data.get("client_phone") or "",
            staff_code=data.get("staff_code") or "",
            require_member=_needs_member_confirmation(request, me),
        )
    except stock_ops.StockError as exc:
        return _err(str(exc))

    convo = services.get_or_create_bot(me)
    total = sum((l["line_total"] for l in lines))
    body = (
        f"{stock_ops.receipt_label(receipt.kind)} {receipt.receipt_number} — "
        f"{len(lines)} item"
        f"{'s' if len(lines) != 1 else ''}, KSh {total:,.2f}"
    )
    card = stock_ops.sale_card(receipt, lines, total)
    msg = services.post_bot_reply(convo, body, card)
    me_part, _ = _participants(convo, me)
    if me_part:
        services.mark_read(me_part, msg.pk)
    _activity_post(
        "trade", me, body, card,
        by=stock_ops.employee_label(receipt.created_by_id),
    )
    return JsonResponse(
        {
            "ok": True,
            "receipt": receipt.receipt_number,
            "message": services.serialize_message(msg, me, None, True),
            "conversation_id": convo.pk,
        }
    )


@api_shop_required
@require_GET
def api_client_search(request):
    """Live client suggestions (name or phone) for checkout."""
    return JsonResponse(
        {
            "ok": True,
            "clients": stock_ops.client_search(request.GET.get("q") or ""),
        }
    )


@api_shop_required
@require_GET
def api_attachment(request, message_id):
    """Stream a message image — only to participants of that conversation."""
    me = request.chat_shop
    try:
        msg = Message.objects.select_related("conversation").get(pk=message_id)
    except Message.DoesNotExist:
        raise Http404
    if not msg.conversation.participants.filter(shop_id=me.pk).exists():
        raise Http404
    if not msg.attachment:
        raise Http404
    try:
        path = msg.attachment.path
    except (ValueError, NotImplementedError):
        raise Http404
    if not os.path.exists(path):
        raise Http404
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    response = FileResponse(open(path, "rb"), content_type=content_type)
    response["Cache-Control"] = "private, max-age=86400"
    return response
