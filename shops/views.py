from itertools import groupby
import re
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from employees.access import (
    active_employee_required,
    get_profile_for_request,
    role_home_url_name,
)
from employees.countries import COUNTRY_DIAL_CODES
from employees.throttle import rate_limit
from employees.workspace import sidebar_for_my_shop
from employees.services import verify_active_employee_code
from items.models import (
    Item,
    ShopItemPrice,
    ShopStock,
    StockMovement,
    StockMovementType,
    StockRequestStatus,
)
from items.services import (
    apply_stock_movement,
    build_stock_catalog_page,
    last_buying_prices_for_items,
    respond_to_stock_request,
    search_suppliers,
)

from .models import ExpenseCategory, ExpensePaymentStatus, Shop
from .daraja_stk import (
    get_stk_payment,
    handle_stk_callback,
    initiate_stk_push,
    stk_payment_payload,
    stk_ready,
    sync_callback_base_from_request,
)
from .services import (
    authenticate_shop_login,
    build_expense_supplier_receipt,
    build_stock_in_supplier_receipt,
    build_stock_request_delivery_note,
    build_shop_day_prompt,
    close_shop_day,
    list_shop_day_prompts,
    shop_working_hours_status_map,
    complete_shop_checkout,
    create_shop,
    day_session_balance_summary,
    delete_shop,
    find_client_by_phone,
    format_kenya_phone,
    get_company_pos_settings,
    get_company_stock_settings,
    get_company_working_hours_settings,
    get_last_closed_shop_day,
    get_open_shop_day,
    get_shop_receipt_detail,
    list_shop_day_sessions,
    list_shop_receipts,
    open_shop_day,
    pos_settings_as_dict,
    register_shop_expense,
    return_shop_receipt_items,
    search_clients_by_name,
    search_expense_suppliers,
    toggle_shop_hidden,
    toggle_shop_suspended,
    update_shop,
    verify_shop_password,
)
from .session import (
    clear_active_shop,
    clear_shop_portal_session,
    get_shop_for_profile,
    resolve_active_shop,
    resolve_portal_shop,
    set_active_shop,
    shop_floor_required,
    shops_for_profile,
)

EMPTY_FORM = {
    "name": "",
    "location": "",
    "email": "",
    "phone_number": "",
    "login_code": "",
}


def _form_data_from_post(post) -> dict:
    return {
        "name": post.get("name", "").strip().upper(),
        "location": post.get("location", "").strip().upper(),
        "email": post.get("email", "").strip().lower(),
        "phone_number": post.get("phone_number", "").strip().upper(),
        "login_code": post.get("login_code", "").strip(),
    }


def _validation_errors(exc: ValidationError) -> list:
    return exc.messages if hasattr(exc, "messages") else [str(exc)]


def _wants_json_response(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    requested_with = (request.headers.get("X-Requested-With") or "").lower()
    return (
        "application/json" in accept
        or requested_with == "xmlhttprequest"
        or (request.POST.get("ajax") or "") == "1"
    )


@require_http_methods(["GET", "POST"])
def shop_management(request, profile, meta, module, page_sidebar):
    from employees.module_permissions import (
        module_capabilities,
        require_module_permission,
    )

    form_data = dict(EMPTY_FORM)
    form_errors = []
    open_register_modal = False
    open_edit_modal = False
    edit_shop = None
    caps = module_capabilities(profile, "shop-management")

    if request.method == "POST":
        action = (request.POST.get("action") or "register").strip()
        shop_id = (request.POST.get("shop_id") or "").strip()
        denied = require_module_permission(
            request, profile, "shop-management", action
        )
        if denied is not None:
            return denied

        if action == "register":
            form_data = _form_data_from_post(request.POST)
            try:
                create_shop(profile, request.POST, request.FILES)
            except ValidationError as exc:
                form_errors = _validation_errors(exc)
                open_register_modal = True
            else:
                messages.success(request, f"Shop “{form_data['name']}” registered successfully.")
                return redirect(request.path)

        elif action == "edit":
            edit_shop = get_object_or_404(Shop, pk=shop_id)
            form_data = _form_data_from_post(request.POST)
            try:
                update_shop(edit_shop, request.POST, request.FILES)
            except ValidationError as exc:
                form_errors = _validation_errors(exc)
                open_edit_modal = True
            else:
                messages.success(request, f"Shop “{form_data['name']}” updated successfully.")
                return redirect(request.path)

        elif action == "toggle_suspend":
            shop = get_object_or_404(Shop, pk=shop_id)
            toggle_shop_suspended(shop)
            state = "suspended" if shop.is_suspended else "unsuspended"
            messages.success(request, f"Shop “{shop.name}” {state}.")
            return redirect(request.path)

        elif action == "toggle_hide":
            shop = get_object_or_404(Shop, pk=shop_id)
            toggle_shop_hidden(shop)
            state = "hidden" if shop.is_hidden else "visible"
            messages.success(request, f"Shop “{shop.name}” is now {state}.")
            return redirect(request.path)

        elif action == "delete":
            shop = get_object_or_404(Shop, pk=shop_id)
            name = shop.name
            delete_shop(shop)
            messages.success(request, f"Shop “{name}” deleted.")
            return redirect(request.path)

        else:
            messages.error(request, "Unknown action.")
            return redirect(request.path)
    else:
        denied = require_module_permission(request, profile, "shop-management", "view")
        if denied is not None:
            return denied

    shops = list(Shop.objects.select_related("created_by__user").order_by("name"))

    return render(
        request,
        "shops/shop_management.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "shops": shops,
            "shop_count": len(shops),
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "open_edit_modal": open_edit_modal,
            "edit_shop": edit_shop,
            "module_permissions": caps,
        },
    )


def _shop_workspace_url(shop):
    return reverse("employees:my_shop_workspace", kwargs={"shop_id": shop.pk})


def _shop_select_url(*, shop_id=""):
    url = reverse("employees:my_shop_select")
    if shop_id:
        from urllib.parse import urlencode

        return f"{url}?{urlencode({'shop_id': shop_id})}"
    return url


def _enter_shop(request, shop):
    set_active_shop(request, shop)
    return redirect(_shop_workspace_url(shop))


def _shops_for_floor(profile, shop):
    if profile is None:
        return [shop]
    return shops_for_profile(profile)


def _shop_day_prompt_context(shop, profile):
    if shop is None:
        return {}
    from employees.module_permissions import employee_may

    if profile is not None and not employee_may(profile, "my-shop", "workspace"):
        return {}

    prompt = build_shop_day_prompt(shop=shop)
    if not prompt.get("show"):
        return {}

    return {
        "shop_day_modal": True,
        "shop_day_prompt": prompt,
        "shop_day_toggle_url": reverse(
            "employees:my_shop_day_toggle", kwargs={"shop_id": shop.pk}
        ),
        "shop_day_verify_url": reverse(
            "employees:my_shop_verify_login_code", kwargs={"shop_id": shop.pk}
        ),
    }


def _shop_day_all_shops_context(shops, profile):
    from employees.module_permissions import employee_may

    settings_row = get_company_working_hours_settings()
    if not settings_row.enabled:
        return {"working_hours_enabled": False}

    can_open_close = profile is None or employee_may(profile, "my-shop", "open_close")
    prompts = list_shop_day_prompts(shops=shops) if can_open_close else []
    status_map = shop_working_hours_status_map(shops=shops) if can_open_close else {}

    return {
        "working_hours_enabled": True,
        "shop_day_prompts": prompts,
        "shop_day_status_map": status_map,
        "shop_day_needs_open": sum(
            1 for row in prompts if row.get("mode") == "open"
        ),
        "shop_day_needs_close": sum(
            1 for row in prompts if row.get("mode") == "close"
        ),
    }


def _shop_floor_chrome(shop, profile, shops, *, active, print_channels=None):
    portal = profile is None
    pending_request_count = 0
    stock_request_status_url = ""
    if shop is not None:
        pending_request_count = StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            requested_from_shop=shop,
            request_status=StockRequestStatus.PENDING,
        ).count()
        stock_request_status_url = reverse(
            "employees:my_shop_stock_request_status", kwargs={"shop_id": shop.pk}
        )
    return {
        "profile": profile,
        "role_label": "Shop portal" if portal else profile.get_role_display(),
        "status_label": "Signed in" if portal else profile.get_status_display(),
        "page_sidebar": sidebar_for_my_shop(
            None if portal else profile.role,
            shop=shop,
            shops=shops,
            active=active,
            shop_open=get_open_shop_day(shop) is not None,
            print_channels=print_channels,
            portal=portal,
            profile=profile,
            pending_request_count=pending_request_count,
        ),
        "shop": shop,
        "shops": shops,
        "shop_portal": portal,
        "stock_request_status_url": stock_request_status_url,
        "sidebar_pending_request_count": pending_request_count,
        **_shop_day_prompt_context(shop, profile),
    }


def _require_my_shop_permission(
    request,
    profile,
    submodule,
    *,
    as_json=False,
    login_code=None,
    actor=None,
    portal_ok=False,
):
    """
    Gate MY-SHOP actions against the acting employee.

    Shop portal sessions have no employee profile. Browsing is allowed with the
    shop password (`portal_ok=True`). Mutations must pass the staff 6-digit ID
    (`login_code` / `actor`) so that employee's matrix permissions apply.
    """
    from employees.module_permissions import require_module_permission
    from employees.services import verify_active_employee_code

    acting = actor
    if acting is None and login_code is not None:
        acting = verify_active_employee_code(login_code)
    if acting is None:
        acting = profile
    if acting is None:
        if portal_ok:
            return None
        error = "Enter a valid active staff 6-digit ID."
        if as_json:
            from django.http import JsonResponse

            return JsonResponse({"ok": False, "error": error}, status=403)
        from django.contrib import messages
        from django.shortcuts import redirect

        messages.error(request, error)
        return redirect(request.path)

    return require_module_permission(
        request, acting, "my-shop", submodule, as_json=as_json
    )


@rate_limit("login", methods=("POST",))
@require_http_methods(["GET", "POST"])
def shop_portal_login(request):
    """Public shop portal: sign in with 6-digit shop code + password."""
    from employees.portal_auth import begin_shop_portal_session, render_portal_login

    portal_shop = resolve_portal_shop(request)
    if portal_shop is not None and request.method == "GET":
        return redirect(_shop_workspace_url(portal_shop))

    error = None
    login_code = ""
    if request.method == "POST":
        login_code = re.sub(r"\D+", "", request.POST.get("login_code") or "")[:6]
        password = request.POST.get("password") or ""
        if len(login_code) != 6:
            error = "Enter the 6-digit shop code."
        elif len(password) < 1:
            error = "Enter the shop password."
        else:
            shop = authenticate_shop_login(login_code, password)
            if shop is None:
                error = "Invalid shop code or password. Please try again."
            else:
                begin_shop_portal_session(request, shop)
                return redirect(_shop_workspace_url(shop))

    return render_portal_login(
        request,
        login_mode="shop",
        shop_error=error,
        shop_login_code=login_code,
    )


@require_http_methods(["GET", "POST"])
def shop_portal_logout(request):
    clear_shop_portal_session(request)
    return redirect("employees:shop_login")


def _render_shop_login(
    request,
    *,
    profile,
    shops,
    selected_shop=None,
    form_error="",
):
    meta = {
        "title": "Shop login",
        "headline": "Shop login",
        "summary": "Enter the shop password to open the floor workspace.",
        "icon": "store",
    }
    shop_day_ctx = _shop_day_all_shops_context(shops, profile)
    status_map = shop_day_ctx.get("shop_day_status_map") or {}
    return render(
        request,
        "shops/my_shop_select.html",
        {
            "profile": profile,
            "meta": meta,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_my_shop(profile.role, shop=None, shops=shops),
            "shops": shops,
            "shop_login_rows": [
                {
                    "shop": shop,
                    "hours_status": status_map.get(str(shop.pk), ""),
                }
                for shop in shops
            ],
            "shop_count": len(shops),
            "multi_shop": len(shops) > 1,
            "selected_shop": selected_shop,
            "selected_shop_id": str(selected_shop.pk) if selected_shop else "",
            "selected_shop_hours_status": (
                status_map.get(str(selected_shop.pk), "") if selected_shop else ""
            ),
            "form_error": form_error,
            "dashboard_url": reverse(role_home_url_name(profile.role)),
            **shop_day_ctx,
        },
    )


@require_http_methods(["GET", "POST"])
def my_shop_entry(request):
    """MY-SHOP sidebar: end employee session and open shop portal login."""
    from django.contrib.auth import logout

    from employees.access import clear_profile_session

    clear_profile_session(request)
    logout(request)
    return redirect("employees:shop_login")


@active_employee_required
@require_http_methods(["GET", "POST"])
def my_shop_select(request):
    """Select an authorised shop and authenticate with the shop password."""
    from employees.module_permissions import employee_may_any, permission_denied_response

    profile = get_profile_for_request(request)
    if not employee_may_any(profile, "my-shop"):
        return permission_denied_response(
            request,
            profile,
            message="You do not have permission to access MY-SHOP.",
        )
    shops = shops_for_profile(profile)

    if not shops:
        messages.info(
            request,
            "No active shops are assigned to your account yet. Contact HR for shop access.",
        )
        return redirect(role_home_url_name(profile.role))

    if request.method == "POST":
        shop = get_shop_for_profile(profile, request.POST.get("shop_id"))
        password = request.POST.get("password") or ""
        if shop is None:
            return _render_shop_login(
                request,
                profile=profile,
                shops=shops,
                form_error="Select a shop you are authorised to access.",
            )
        if not verify_shop_password(shop, password):
            return _render_shop_login(
                request,
                profile=profile,
                shops=shops,
                selected_shop=shop,
                form_error="Incorrect shop password. Try again.",
            )
        return _enter_shop(request, shop)

    preferred = get_shop_for_profile(profile, request.GET.get("shop_id"))
    active = resolve_active_shop(request, profile)
    # Resume unlocked shop unless the user is switching to a different shop.
    if active is not None and (preferred is None or str(preferred.pk) == str(active.pk)):
        return redirect(_shop_workspace_url(active))

    if preferred is None and len(shops) == 1:
        preferred = shops[0]

    return _render_shop_login(
        request,
        profile=profile,
        shops=shops,
        selected_shop=preferred,
    )


@active_employee_required
@require_POST
def my_shop_leave(request):
    clear_active_shop(request)
    return redirect(role_home_url_name(get_profile_for_request(request).role))


def _catalog_base_qs():
    return (
        Item.objects.filter(is_suspended=False)
        .only(
            "id",
            "category",
            "name",
            "description",
            "shop_price",
            "minimum_selling_price",
            "maximum_selling_price",
            "use_individual_shop_prices",
            "track_serial_number",
            "image",
            "is_suspended",
        )
        .order_by("category", "name")
    )


def _catalog_filter_qs(qs, *, q="", category=""):
    from items.services import item_text_search_q

    category = (category or "").strip()
    if category:
        qs = qs.filter(category=category)

    query = (q or "").strip()
    if query:
        qs = qs.filter(item_text_search_q(query))
    return qs


def _catalog_rows_for_items(shop, items):
    if not items:
        return []
    item_ids = [item.pk for item in items]
    stock_by_item = {
        item_id: qty
        for item_id, qty in ShopStock.objects.filter(
            shop=shop, item_id__in=item_ids
        ).values_list("item_id", "quantity")
    }
    price_by_item = {
        item_id: price
        for item_id, price in ShopItemPrice.objects.filter(
            shop=shop, item_id__in=item_ids
        ).values_list("item_id", "price")
    }
    rows = []
    for item in items:
        override = price_by_item.get(item.pk) if item.use_individual_shop_prices else None
        price = item.resolve_list_price(override)
        description = (item.description or "").strip()
        if len(description) > 160:
            description = description[:157].rstrip() + "..."
        image_url = ""
        if item.image:
            try:
                image_url = item.image.url
            except ValueError:
                image_url = ""
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "description": description,
                "price": str(price),
                "min_price": str(item.minimum_selling_price),
                "max_price": str(item.maximum_selling_price),
                "stock": int(stock_by_item.get(item.pk, 0)),
                "track_serial": bool(item.track_serial_number),
                "image_url": image_url,
            }
        )
    return rows


def _catalog_items_by_category(shop):
    """Full catalog grouped by category (buy-stock / legacy callers)."""
    items = list(_catalog_base_qs())
    if not items:
        return [], 0
    rows = _catalog_rows_for_items(shop, items)
    by_id = {row["id"]: row for row in rows}
    # Rebuild template-friendly structure with model instances for older pages.
    items_by_category = []
    for category, group in groupby(items, key=lambda item: item.category):
        group_rows = []
        for item in group:
            payload = by_id[item.pk]
            group_rows.append(
                {
                    "item": item,
                    "price": payload["price"],
                    "quantity": payload["stock"],
                }
            )
        items_by_category.append({"category": category, "items": group_rows})
    return items_by_category, len(items)


def build_shop_catalog_page(
    shop,
    *,
    q="",
    category="",
    page=1,
    page_size=120,
):
    """Paginated catalog payload for the MY-SHOP floor API."""
    from items.services import _paginate_queryset

    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size or 120)
    except (TypeError, ValueError):
        page_size = 120
    page_size = min(max(page_size, 24), 240)

    qs = _catalog_filter_qs(_catalog_base_qs(), q=q, category=category)
    page_data = _paginate_queryset(qs, page=page, page_size=page_size)
    rows = _catalog_rows_for_items(shop, page_data["items"])

    categories = list(
        Item.objects.filter(is_suspended=False)
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )

    return {
        "ok": True,
        "total": page_data["total"],
        "page": page_data["page"],
        "page_size": page_size,
        "has_more": page_data["has_more"],
        "next_page": page_data["next_page"],
        "categories": categories,
        "items": rows,
        "q": (q or "").strip(),
        "category": (category or "").strip(),
    }


def _pending_stock_requests_for_shop(shop):
    requests = list(
        StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            requested_from_shop=shop,
            request_status=StockRequestStatus.PENDING,
        )
        .select_related("shop", "created_by", "created_by__user")
        .prefetch_related("lines__item")
        .order_by("-created_at")
    )
    item_ids = {
        line.item_id
        for movement in requests
        for line in movement.lines.all()
    }
    stock_by_item = {
        item_id: qty
        for item_id, qty in ShopStock.objects.filter(
            shop=shop, item_id__in=item_ids
        ).values_list("item_id", "quantity")
    }
    for movement in requests:
        for line in movement.lines.all():
            available = stock_by_item.get(line.item_id, 0)
            line.available_qty = available
            line.transfer_max = min(line.quantity, available)
    return requests


def _outgoing_pending_stock_requests_for_shop(shop):
    """Pending requests this shop sent to other shops (awaiting their response)."""
    rows = list(
        StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            shop=shop,
            request_status=StockRequestStatus.PENDING,
        )
        .select_related(
            "requested_from_shop",
            "created_by",
            "created_by__user",
        )
        .prefetch_related("lines__item")
        .order_by("-created_at")
    )
    for movement in rows:
        movement.units_total = sum(
            int(line.quantity or 0) for line in movement.lines.all()
        )
    return rows


def _stock_request_status_payload(shop):
    """Lean JSON for floor polling: incoming alerts + decision updates."""
    pending = list(
        StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            requested_from_shop=shop,
            request_status=StockRequestStatus.PENDING,
        )
        .select_related("shop")
        .prefetch_related("lines")
        .order_by("-created_at")
    )
    alerts = []
    for movement in pending:
        units = sum(int(line.quantity or 0) for line in movement.lines.all())
        entry = {
            "id": movement.pk,
            "from_shop": movement.shop.name if movement.shop_id else "Another shop",
            "units": units,
            "created_at": movement.created_at.isoformat() if movement.created_at else "",
            "unseen": not movement.supplier_notified,
        }
        alerts.append(entry)
    decisions = list(
        StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            shop=shop,
            request_status__in=(
                StockRequestStatus.FULFILLED,
                StockRequestStatus.DECLINED,
            ),
            requester_notified=False,
        )
        .select_related("requested_from_shop")
        .order_by("-responded_at", "-created_at")
    )
    decision_alerts = []
    for movement in decisions:
        supplier = movement.requested_from_shop
        decision_alerts.append(
            {
                "id": movement.pk,
                "status": movement.request_status,
                "from_shop": supplier.name if supplier else "another shop",
            }
        )
    unseen_count = sum(1 for row in alerts if row["unseen"])
    return {
        "ok": True,
        "pending_count": len(alerts),
        "unseen_count": unseen_count,
        "pending": alerts,
        "decision_count": len(decision_alerts),
        "decisions": decision_alerts,
    }


def _stock_request_decisions_for_shop(shop):
    """Accepted/declined requests waiting for the requesting shop to acknowledge."""
    return list(
        StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            shop=shop,
            request_status__in=(
                StockRequestStatus.FULFILLED,
                StockRequestStatus.DECLINED,
            ),
            requester_notified=False,
        )
        .select_related(
            "requested_from_shop",
            "responded_by",
            "responded_by__user",
            "created_by",
            "created_by__user",
        )
        .prefetch_related("lines__item")
        .order_by("-responded_at", "-created_at")
    )


def _previous_stock_requests_for_shop(shop, *, limit=20):
    """Fulfilled/declined requests this shop supplied or requested."""
    rows = list(
        StockMovement.objects.filter(
            movement_type=StockMovementType.REQUEST,
            request_status__in=(
                StockRequestStatus.FULFILLED,
                StockRequestStatus.DECLINED,
            ),
        )
        .filter(Q(requested_from_shop=shop) | Q(shop=shop))
        .select_related(
            "shop",
            "requested_from_shop",
            "responded_by",
            "responded_by__user",
            "created_by",
            "created_by__user",
        )
        .prefetch_related("lines__item")
        .order_by("-responded_at", "-created_at")[:limit]
    )
    for movement in rows:
        movement.is_incoming = (
            movement.requested_from_shop_id is not None
            and movement.requested_from_shop_id == shop.pk
        )
        movement.counterparty = (
            movement.shop if movement.is_incoming else movement.requested_from_shop
        )
        movement.units_total = sum(int(line.quantity or 0) for line in movement.lines.all())
    return rows


def _require_active_shop_session(request, shop_id):
    portal_shop = resolve_portal_shop(request)
    if portal_shop is not None:
        if str(portal_shop.pk) != str(shop_id):
            messages.error(request, "You are signed into a different shop.")
            return None, None, redirect(_shop_workspace_url(portal_shop))
        return None, portal_shop, None

    profile = get_profile_for_request(request)
    if profile is None:
        messages.info(request, "Sign in with the shop code to open this workspace.")
        return None, None, redirect("employees:shop_login")

    shop = get_shop_for_profile(profile, shop_id)
    if shop is None:
        messages.error(request, "You are not authorised to open that shop.")
        return None, None, redirect("employees:my_shop")

    active = resolve_active_shop(request, profile)
    if active is None or str(active.pk) != str(shop.pk):
        messages.info(request, "Enter the shop password to open this workspace.")
        return None, None, redirect(_shop_select_url(shop_id=shop.pk))
    return profile, shop, None


def _require_shop_read_access(request, shop_id):
    """
    Read-only shop access for catalog APIs.

    Allows portal session, unlocked shop session, or an employee who is already
    assigned to the shop (so catalogs can be cached before the password unlock).
    Mutations still use _require_active_shop_session.
    """
    portal_shop = resolve_portal_shop(request)
    if portal_shop is not None:
        if str(portal_shop.pk) != str(shop_id):
            return None, None, JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
        return None, portal_shop, None

    profile = get_profile_for_request(request)
    if profile is None or not profile.is_active_employee:
        return None, None, JsonResponse(
            {"ok": False, "error": "Shop session required."}, status=403
        )

    shop = get_shop_for_profile(profile, shop_id)
    if shop is None:
        from employees.models import EmployeeRole
        from shops.models import Shop

        if profile.role in (
            EmployeeRole.SUPER_ADMIN,
            EmployeeRole.COMPANY_MANAGER,
            EmployeeRole.IT_SUPPORT,
        ):
            shop = Shop.objects.filter(
                pk=shop_id, is_hidden=False, is_suspended=False
            ).first()
        if shop is None:
            return None, None, JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
    return profile, shop, None


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_workspace(request, shop_id):
    """Shop floor workspace: requires an authenticated shop session."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "workspace", portal_ok=True)
    if denied:
        return denied

    shops = _shops_for_floor(profile, shop)
    item_count = Item.objects.filter(is_suspended=False).count()
    category_count = (
        Item.objects.filter(is_suspended=False)
        .order_by("category")
        .values("category")
        .distinct()
        .count()
    )
    pending_requests = _pending_stock_requests_for_shop(shop)
    request_decisions = _stock_request_decisions_for_shop(shop)
    pos_settings = get_company_pos_settings()
    pos_flags = pos_settings_as_dict(pos_settings)
    enabled_kinds = pos_flags["kinds"]
    enabled_payments = pos_flags["payment_methods"]
    enabled_print_channels = pos_flags["print_channels"]
    # Sale needs at least one payment method; otherwise hide it from the cart.
    from employees.module_permissions import employee_may

    cart_kinds = [
        kind
        for kind in enabled_kinds
        if (kind != "sale" or pos_flags["cash_sale_checkout"])
        and (
            profile is None
            or employee_may(profile, "my-shop", kind)
        )
    ]
    default_kind = cart_kinds[0] if cart_kinds else ""
    default_payment = enabled_payments[0] if enabled_payments else ""
    default_print_via = enabled_print_channels[0] if enabled_print_channels else ""
    checkout_enabled = bool(cart_kinds)
    show_document_picker = len(cart_kinds) > 1
    show_payment_picker = bool(
        default_kind == "sale" and pos_flags["cash_sale_checkout"] and enabled_payments
    )

    buy_stock_ctx = _buy_stock_items_context(shop)
    buy_stock_item_count = buy_stock_ctx["item_count"]

    meta = {
        "title": shop.name,
        "headline": shop.name,
        "summary": shop.location,
        "icon": "store",
    }
    return render(
        request,
        "shops/my_shop_workspace.html",
        {
            **_shop_floor_chrome(
                shop,
                profile,
                shops,
                active="workspace",
                print_channels=enabled_print_channels,
            ),
            "meta": meta,
            "item_count": item_count,
            "category_count": category_count,
            "pending_stock_requests": pending_requests,
            "pending_request_count": len(pending_requests),
            "stock_request_decisions": request_decisions,
            "stock_request_decision_count": len(request_decisions),
            "serial_search_url": reverse(
                "employees:my_shop_serial_search", kwargs={"shop_id": shop.pk}
            ),
            "catalog_url": reverse(
                "employees:my_shop_catalog", kwargs={"shop_id": shop.pk}
            ),
            "verify_login_code_url": reverse(
                "employees:my_shop_verify_login_code", kwargs={"shop_id": shop.pk}
            ),
            "stock_request_supplier_ack_url": reverse(
                "employees:my_shop_stock_request_supplier_ack",
                kwargs={"shop_id": shop.pk},
            ),
            "checkout_url": reverse(
                "employees:my_shop_checkout", kwargs={"shop_id": shop.pk}
            ),
            "stk_initiate_url": reverse(
                "employees:my_shop_stk_initiate", kwargs={"shop_id": shop.pk}
            ),
            "stk_status_url_template": reverse(
                "employees:my_shop_stk_status",
                kwargs={"shop_id": shop.pk, "payment_id": "00000000-0000-0000-0000-000000000000"},
            ).replace("00000000-0000-0000-0000-000000000000", "__ID__"),
            "print_relay_url": reverse(
                "employees:my_shop_print_relay", kwargs={"shop_id": shop.pk}
            ),
            "print_scan_url": reverse(
                "employees:my_shop_wifi_printer_scan", kwargs={"shop_id": shop.pk}
            ),
            "client_lookup_url": reverse(
                "employees:my_shop_client_lookup", kwargs={"shop_id": shop.pk}
            ),
            "pos_settings": pos_settings,
            "pos_flags": pos_flags,
            "stk_ready": stk_ready(),
            "cart_kinds": cart_kinds,
            "checkout_enabled": checkout_enabled,
            "show_document_picker": show_document_picker,
            "show_payment_picker": show_payment_picker,
            "default_kind": default_kind,
            "default_payment": default_payment,
            "default_print_via": default_print_via,
            "buy_stock_modal": True,
            "buy_stock_next": reverse(
                "employees:my_shop_workspace", kwargs={"shop_id": shop.pk}
            ),
            "buy_stock_item_count": buy_stock_item_count,
            "total_units": buy_stock_ctx["total_units"],
            "items_by_category": buy_stock_ctx["items_by_category"],
            "countries": buy_stock_ctx["countries"],
            "supplier_search_url": buy_stock_ctx["supplier_search_url"],
            "stock_requirements_json": buy_stock_ctx["stock_requirements_json"],
            "stock_catalog_url": buy_stock_ctx["stock_catalog_url"],
            "use_stock_catalog_api": buy_stock_ctx["use_stock_catalog_api"],
            "register_expense_modal": True,
            "expense_next": reverse(
                "employees:my_shop_workspace", kwargs={"shop_id": shop.pk}
            ),
            "form_data": {
                "category": "",
                "name": "",
                "amount": "",
                "payment_status": "",
                "supplier_id": "",
                "supplier_name": "",
                "supplier_phone_country_code": "+254",
                "supplier_phone_country_iso": "KE",
                "supplier_phone_number": "",
                "login_code": "",
            },
            "form_errors": [],
            "expense_categories": ExpenseCategory.choices,
            "payment_statuses": ExpensePaymentStatus.choices,
            "expense_supplier_search_url": reverse(
                "employees:expense_supplier_search"
            ),
            "recent_expenses": [],
        },
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_catalog(request, shop_id):
    """Paginated JSON catalog for the MY-SHOP floor (keeps first HTML light)."""
    profile, shop, denied = _require_shop_read_access(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "workspace", as_json=True, portal_ok=True)
    if denied:
        return denied

    payload = build_shop_catalog_page(
        shop,
        q=request.GET.get("q") or "",
        category=request.GET.get("category") or "",
        page=request.GET.get("page") or 1,
        page_size=request.GET.get("page_size") or 120,
    )
    return JsonResponse(payload)


def _render_my_shop_tool_page(
    request,
    *,
    shop,
    profile,
    shops,
    active,
    title,
    headline,
    summary,
    icon,
    template_name,
    extra_context=None,
):
    meta = {
        "title": title,
        "headline": headline,
        "summary": summary,
        "icon": icon,
    }
    context = {
        **_shop_floor_chrome(shop, profile, shops, active=active),
        "meta": meta,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, template_name, context)


def _buy_stock_items_context(shop):
    """Lean shell context for buy-stock — catalog loads via JSON API."""
    import json as _json

    from django.db.models import Sum

    from shops.services import get_company_stock_settings

    item_count = Item.objects.count()
    total_units = (
        ShopStock.objects.filter(shop=shop).aggregate(total=Sum("quantity"))["total"]
        or 0
    )
    return {
        "items_by_category": [],
        "item_count": item_count,
        "category_count": 0,
        "total_units": int(total_units),
        "selected_shop": shop,
        "stock_mode": "in",
        "countries": COUNTRY_DIAL_CODES,
        "supplier_search_url": reverse(
            "employees:my_shop_supplier_search", kwargs={"shop_id": shop.pk}
        ),
        "serial_check_url": reverse("employees:serial_in_stock_check"),
        "verify_login_code_url": reverse(
            "employees:my_shop_verify_login_code", kwargs={"shop_id": shop.pk}
        ),
        "stock_catalog_url": reverse(
            "employees:my_shop_buy_stock_catalog", kwargs={"shop_id": shop.pk}
        ),
        "use_stock_catalog_api": True,
        "catalog_shops_json": _json.dumps(
            [{"id": shop.pk, "name": shop.name}]
        ),
        "stock_requirements_json": _json.dumps(
            get_company_stock_settings().as_requirements_dict()
        ),
    }


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_buy_stock_catalog(request, shop_id):
    """Paginated buy-stock catalog JSON."""
    profile, shop, denied = _require_shop_read_access(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "buy_stock", as_json=True, portal_ok=True)
    if denied:
        return denied

    payload = build_stock_catalog_page(
        shop_id=shop.pk,
        mode="in",
        q=request.GET.get("q") or "",
        page=request.GET.get("page") or 1,
        page_size=request.GET.get("page_size") or 48,
        include_suspended=True,
        include_totals=False,
    )
    return JsonResponse(payload)


@shop_floor_required
@require_http_methods(["GET", "POST"])
def my_shop_buy_stock(request, shop_id):
    """Buy stock from an outside supplier and stock it into the active shop."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        if _wants_json_response(request):
            return JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
        return denied
    denied = _require_my_shop_permission(
        request,
        profile,
        "buy_stock",
        as_json=_wants_json_response(request),
        portal_ok=True,
    )
    if denied:
        return denied

    shops = _shops_for_floor(profile, shop)

    if request.method == "POST":
        wants_json = _wants_json_response(request)
        login_code = (request.POST.get("login_code") or "").strip()
        authorising = verify_active_employee_code(login_code)
        if authorising is None:
            if wants_json:
                return JsonResponse(
                    {"ok": False, "error": "Enter a valid active staff 6-digit ID."},
                    status=400,
                )
            messages.error(request, "Enter a valid active staff 6-digit ID.")
        else:
            denied = _require_my_shop_permission(
                request,
                profile,
                "buy_stock",
                as_json=wants_json,
                actor=authorising,
            )
            if denied:
                return denied
            post = request.POST.copy()
            post["shop_id"] = str(shop.pk)
            post["mode"] = "in"
            try:
                movement = apply_stock_movement(authorising, "in", post)
            except ValidationError as exc:
                errors = _validation_errors(exc)
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": errors[0] if errors else "Could not stock in.",
                            "errors": errors,
                        },
                        status=400,
                    )
                for message in errors:
                    messages.error(request, message)
            else:
                # Credit the authorising staff member (same pattern as day open/close).
                if movement.created_by_id != authorising.pk:
                    movement.created_by = authorising
                    movement.save(update_fields=["created_by"])
                line_count = movement.lines.count()
                success_message = (
                    f"Bought and stocked in {line_count} item"
                    f"{'' if line_count == 1 else 's'} for {shop.name}."
                )
                messages.success(request, success_message)
                next_url = (request.POST.get("next") or "").strip()
                if not (next_url.startswith("/") and not next_url.startswith("//")):
                    next_url = reverse(
                        "employees:my_shop_buy_stock", kwargs={"shop_id": shop.pk}
                    )
                if wants_json:
                    print_payload = build_stock_in_supplier_receipt(
                        movement, shop=shop, authorised_by=authorising
                    )
                    return JsonResponse(
                        {
                            "ok": True,
                            "message": success_message,
                            "next": next_url,
                            **print_payload,
                        }
                    )
                return redirect(next_url)

    return _render_my_shop_tool_page(
        request,
        shop=shop,
        profile=profile,
        shops=shops,
        active="buy_stock",
        title=f"Buy stock — {shop.name}",
        headline="Buy stock items",
        summary=(
            f"Select items bought outside, enter quantities, supplier details, "
            f"payment status, and an active staff ID to stock into {shop.name}."
        ),
        icon="package-plus",
        template_name="shops/my_shop_buy_stock.html",
        extra_context=_buy_stock_items_context(shop),
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_stock_requests(request, shop_id):
    """Review incoming stock requests and request decision updates."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "stock_requests", portal_ok=True)
    if denied:
        return denied

    shops = _shops_for_floor(profile, shop)
    pending_requests = _pending_stock_requests_for_shop(shop)
    outgoing_pending = _outgoing_pending_stock_requests_for_shop(shop)
    request_decisions = _stock_request_decisions_for_shop(shop)
    previous_requests = _previous_stock_requests_for_shop(shop)
    return _render_my_shop_tool_page(
        request,
        shop=shop,
        profile=profile,
        shops=shops,
        active="stock_requests",
        title=f"Stock requests — {shop.name}",
        headline="Stock requests",
        summary=(
            f"Request stock from another shop, review incoming transfers, "
            f"and see previous requests for {shop.name}."
        ),
        icon="clipboard-list",
        template_name="shops/my_shop_stock_requests.html",
        extra_context={
            "pending_stock_requests": pending_requests,
            "pending_request_count": len(pending_requests),
            "outgoing_pending_requests": outgoing_pending,
            "outgoing_pending_count": len(outgoing_pending),
            "stock_request_decisions": request_decisions,
            "stock_request_decision_count": len(request_decisions),
            "previous_stock_requests": previous_requests,
            "previous_request_count": len(previous_requests),
            "serial_search_url": reverse(
                "employees:my_shop_serial_search", kwargs={"shop_id": shop.pk}
            ),
            "verify_login_code_url": reverse(
                "employees:my_shop_verify_login_code", kwargs={"shop_id": shop.pk}
            ),
            "create_stock_request_url": reverse(
                "employees:my_shop_stock_request_create", kwargs={"shop_id": shop.pk}
            ),
            "stock_request_from_stock_url": reverse(
                "employees:my_shop_stock_request_from_stock",
                kwargs={"shop_id": shop.pk},
            ),
            "stock_request_supplier_ack_url": reverse(
                "employees:my_shop_stock_request_supplier_ack",
                kwargs={"shop_id": shop.pk},
            ),
            "request_from_shops": list(
                Shop.objects.filter(is_hidden=False, is_suspended=False)
                .exclude(pk=shop.pk)
                .order_by("name")
                .only("id", "name", "location")
            ),
            "request_items": list(
                Item.objects.filter(is_suspended=False)
                .order_by("name")
                .values("id", "name")[:800]
            ),
        },
    )


@shop_floor_required
@require_http_methods(["POST"])
def my_shop_stock_request_create(request, shop_id):
    """Create an outgoing stock request from this shop to another shop."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        if _wants_json_response(request):
            return JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
        return denied

    wants_json = _wants_json_response(request)
    login_code = (request.POST.get("login_code") or "").strip()
    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        error = "Enter a valid active staff 6-digit ID."
        if wants_json:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("employees:my_shop_stock_requests", shop_id=shop.pk)

    denied = _require_my_shop_permission(
        request,
        profile,
        "stock_requests",
        as_json=wants_json,
        actor=authorising,
    )
    if denied:
        return denied

    from_shop_id = (request.POST.get("requested_from_shop_id") or "").strip()
    notes = (request.POST.get("notes") or "").strip()
    post = request.POST.copy()
    post["shop_id"] = str(shop.pk)
    post["requested_from_shop_id"] = from_shop_id
    post["mode"] = "request"

    try:
        movement = apply_stock_movement(authorising, StockMovementType.REQUEST, post)
    except ValidationError as exc:
        errors = _validation_errors(exc)
        if wants_json:
            return JsonResponse(
                {
                    "ok": False,
                    "error": errors[0] if errors else "Could not create stock request.",
                },
                status=400,
            )
        for message in errors:
            messages.error(request, message)
        return redirect("employees:my_shop_stock_requests", shop_id=shop.pk)

    if notes and not (movement.notes or "").strip():
        movement.notes = notes[:2000]
        movement.save(update_fields=["notes"])

    line_count = movement.lines.count()
    from_name = (
        movement.requested_from_shop.name
        if movement.requested_from_shop_id
        else "another shop"
    )
    success = (
        f"Requested {line_count} item{'' if line_count == 1 else 's'} "
        f"from {from_name}. They will be notified."
    )
    messages.success(request, success)
    if wants_json:
        return JsonResponse({"ok": True, "message": success, "request_id": movement.pk})
    return redirect("employees:my_shop_stock_requests", shop_id=shop.pk)


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_stock_request_from_stock(request, shop_id):
    """Return on-hand quantities at the shop being requested from."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(
        request, profile, "stock_requests", as_json=True, portal_ok=True
    )
    if denied:
        return denied

    from_shop_id = (request.GET.get("from_shop_id") or "").strip()
    if not from_shop_id:
        return JsonResponse(
            {"ok": False, "error": "Select a shop to request from."}, status=400
        )
    if str(from_shop_id) == str(shop.pk):
        return JsonResponse(
            {"ok": False, "error": "Choose a different shop to request from."},
            status=400,
        )

    from_shop = (
        Shop.objects.filter(
            pk=from_shop_id, is_hidden=False, is_suspended=False
        )
        .only("id", "name")
        .first()
    )
    if from_shop is None:
        return JsonResponse(
            {"ok": False, "error": "That shop is not available."}, status=404
        )

    stocks = {
        str(item_id): int(qty or 0)
        for item_id, qty in ShopStock.objects.filter(shop=from_shop).values_list(
            "item_id", "quantity"
        )
    }
    return JsonResponse(
        {
            "ok": True,
            "from_shop_id": from_shop.pk,
            "from_shop_name": from_shop.name,
            "stocks": stocks,
        }
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_stock_request_status(request, shop_id):
    """Poll endpoint: incoming stock-request alerts for the active shop."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(
        request, profile, "workspace", as_json=True, portal_ok=True
    )
    if denied:
        return denied
    return JsonResponse(_stock_request_status_payload(shop))


@shop_floor_required
@require_POST
def my_shop_stock_request_supplier_ack(request, shop_id):
    """Mark unseen incoming requests as seen by the supplying shop."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        if _wants_json_response(request):
            return JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
        return denied
    denied = _require_my_shop_permission(
        request,
        profile,
        "stock_requests",
        as_json=_wants_json_response(request),
        portal_ok=True,
    )
    if denied:
        return denied

    updated = StockMovement.objects.filter(
        movement_type=StockMovementType.REQUEST,
        requested_from_shop=shop,
        request_status=StockRequestStatus.PENDING,
        supplier_notified=False,
    ).update(supplier_notified=True)

    if _wants_json_response(request):
        return JsonResponse({"ok": True, "acked": updated})
    return redirect("employees:my_shop_workspace", shop_id=shop.pk)


@shop_floor_required
@require_http_methods(["GET", "POST"])
def my_shop_register_expense(request, shop_id):
    """Register an outside expense against the active shop (modal on workspace)."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        if _wants_json_response(request):
            return JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
        return denied
    denied = _require_my_shop_permission(
        request,
        profile,
        "register_expense",
        as_json=_wants_json_response(request),
        portal_ok=True,
    )
    if denied:
        return denied

    workspace_url = reverse(
        "employees:my_shop_workspace", kwargs={"shop_id": shop.pk}
    )
    expense_modal_url = f"{workspace_url}?modal=register-expense"

    if request.method == "GET":
        return redirect(expense_modal_url)

    form_data = {
        "category": (request.POST.get("category") or "").strip().lower(),
        "name": (request.POST.get("name") or "").strip().upper(),
        "amount": (request.POST.get("amount") or "").strip(),
        "payment_status": (request.POST.get("payment_status") or "").strip().lower(),
        "supplier_id": (request.POST.get("supplier_id") or "").strip(),
        "supplier_name": (request.POST.get("supplier_name") or "").strip().upper(),
        "supplier_phone_country_code": (
            request.POST.get("supplier_phone_country_code") or "+254"
        ).strip(),
        "supplier_phone_country_iso": (
            request.POST.get("supplier_phone_country_iso") or "KE"
        )
        .strip()
        .upper(),
        "supplier_phone_number": (
            request.POST.get("supplier_phone_number") or ""
        ).strip(),
        "login_code": (request.POST.get("login_code") or "").strip(),
    }
    wants_json = _wants_json_response(request)
    try:
        result = register_shop_expense(shop=shop, profile=profile, payload=form_data)
    except ValidationError as exc:
        form_errors = _validation_errors(exc)
        if wants_json:
            return JsonResponse(
                {
                    "ok": False,
                    "error": form_errors[0]
                    if form_errors
                    else "Could not record expense.",
                },
                status=400,
            )
        for message in form_errors:
            messages.error(request, message)
        return redirect(expense_modal_url)

    messages.success(request, result["message"])
    next_url = (request.POST.get("next") or "").strip()
    if not (next_url.startswith("/") and not next_url.startswith("//")):
        next_url = workspace_url
    if wants_json:
        print_payload = build_expense_supplier_receipt(
            result["expense"],
            shop=shop,
            authorised_by=result.get("authorised_by") or "",
        )
        return JsonResponse(
            {
                "ok": True,
                "message": result["message"],
                "next": next_url,
                **print_payload,
            }
        )
    return redirect(next_url)


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_supplier_search(request, shop_id):
    """Live stock-supplier suggestions for Buy stock on the shop floor."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(
        request, profile, "buy_stock", as_json=True, portal_ok=True
    )
    if denied:
        return denied

    query = (request.GET.get("q") or "").strip()
    by = (request.GET.get("by") or "name").strip().lower()
    dial = (request.GET.get("dial") or "").strip()
    match = (request.GET.get("match") or "contains").strip().lower()
    results = search_suppliers(query=query, by=by, dial=dial, limit=8, match=match)
    return JsonResponse(
        {
            "ok": True,
            "match": match,
            "results": [
                {
                    "id": supplier.pk,
                    "name": supplier.name,
                    "dial": supplier.phone_country_code,
                    "iso": supplier.phone_country_iso or "KE",
                    "phone": supplier.phone_number,
                }
                for supplier in results
            ],
        }
    )


@shop_floor_required
@require_http_methods(["GET"])
def expense_supplier_search_api(request):
    """Search expense suppliers by name or phone."""
    query = (request.GET.get("q") or "").strip()
    by = (request.GET.get("by") or "name").strip().lower()
    dial = (request.GET.get("dial") or "").strip()
    match = (request.GET.get("match") or "contains").strip().lower()
    results = search_expense_suppliers(
        query=query, by=by, dial=dial, limit=8, match=match
    )
    return JsonResponse(
        {
            "ok": True,
            "match": match,
            "results": [
                {
                    "id": supplier.pk,
                    "name": supplier.name,
                    "dial": supplier.phone_country_code,
                    "iso": supplier.phone_country_iso or "KE",
                    "phone": supplier.phone_number,
                }
                for supplier in results
            ],
        }
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_receipts(request, shop_id):
    """Browse, reprint, and return receipts for the active shop."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "receipts", portal_ok=True)
    if denied:
        return denied

    shops = _shops_for_floor(profile, shop)
    pos_settings = get_company_pos_settings()
    pos_flags = pos_settings_as_dict(pos_settings)
    return _render_my_shop_tool_page(
        request,
        shop=shop,
        profile=profile,
        shops=shops,
        active="receipts",
        title=f"Receipts — {shop.name}",
        headline="Receipts",
        summary=f"Search, reprint, or return receipts for {shop.name}.",
        icon="receipt",
        template_name="shops/my_shop_receipts.html",
        extra_context={
            "verify_login_code_url": reverse(
                "employees:my_shop_verify_login_code", kwargs={"shop_id": shop.pk}
            ),
            "receipts_list_url": reverse(
                "employees:my_shop_receipts_list", kwargs={"shop_id": shop.pk}
            ),
            "receipt_detail_url_template": reverse(
                "employees:my_shop_receipt_detail",
                kwargs={"shop_id": shop.pk, "receipt_id": 0},
            ),
            "receipt_return_url_template": reverse(
                "employees:my_shop_receipt_return",
                kwargs={"shop_id": shop.pk, "receipt_id": 0},
            ),
            "print_relay_url": reverse(
                "employees:my_shop_print_relay", kwargs={"shop_id": shop.pk}
            ),
            "pos_flags": pos_flags,
        },
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_reprint(request, shop_id):
    """Legacy reprint URL — redirect to receipts."""
    return redirect("employees:my_shop_receipts", shop_id=shop_id)


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_receipts_list(request, shop_id):
    """JSON list of shop receipts with live search and date filters."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(request, profile, "receipts", as_json=True, portal_ok=True)
    if denied:
        return denied

    try:
        payload = list_shop_receipts(
            shop=shop,
            query=request.GET.get("q") or "",
            filter_mode=request.GET.get("filter") or "day",
            day=request.GET.get("day") or "",
            date_from=request.GET.get("from") or "",
            date_to=request.GET.get("to") or "",
            month=request.GET.get("month") or "",
            year=request.GET.get("year") or "",
            limit=request.GET.get("limit") or 200,
        )
    except ValidationError as exc:
        errors = _validation_errors(exc)
        return JsonResponse(
            {
                "ok": False,
                "error": errors[0] if errors else "Invalid filter.",
                "errors": errors,
            },
            status=400,
        )
    return JsonResponse(payload)


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_receipt_detail(request, shop_id, receipt_id):
    """JSON detail for one shop receipt (modal + reprint payload)."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(request, profile, "receipts", as_json=True, portal_ok=True)
    if denied:
        return denied

    try:
        payload = get_shop_receipt_detail(shop=shop, receipt_id=receipt_id)
    except ValidationError as exc:
        errors = _validation_errors(exc)
        return JsonResponse(
            {
                "ok": False,
                "error": errors[0] if errors else "Receipt not found.",
                "errors": errors,
            },
            status=404,
        )
    return JsonResponse(payload)


@shop_floor_required
@require_POST
def my_shop_receipt_return(request, shop_id, receipt_id):
    """Return one or more items from a sale/credit receipt (staff ID required)."""
    import json

    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(request, profile, "return_receipt", as_json=True, portal_ok=True)
    if denied:
        return denied

    try:
        if request.content_type and "application/json" in request.content_type:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = {
                "login_code": request.POST.get("login_code"),
                "lines": json.loads(request.POST.get("lines") or "[]"),
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid return payload."}, status=400)

    try:
        result = return_shop_receipt_items(
            shop=shop, receipt_id=receipt_id, payload=payload
        )
    except ValidationError as exc:
        errors = _validation_errors(exc)
        return JsonResponse(
            {
                "ok": False,
                "error": errors[0] if errors else "Return failed.",
                "errors": errors,
            },
            status=400,
        )

    return JsonResponse(result)


@shop_floor_required
@require_http_methods(["GET", "POST"])
def my_shop_day_toggle(request, shop_id):
    """Open or close the shop day with balances, stock confirm, and staff ID."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "open_close", portal_ok=True)
    if denied:
        return denied

    shops = _shops_for_floor(profile, shop)
    open_session = get_open_shop_day(shop)
    is_open = open_session is not None
    mode = "close" if is_open else "open"
    form_errors = []
    form_data = {
        "cash_amount": "",
        "mpesa_amount": "",
        "credit_amount": "",
        "stock_confirmed": False,
        "login_code": "",
    }

    if request.method == "POST":
        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        form_data = {
            "cash_amount": (request.POST.get("cash_amount") or "").strip(),
            "mpesa_amount": (request.POST.get("mpesa_amount") or "").strip(),
            "credit_amount": (request.POST.get("credit_amount") or "").strip(),
            "stock_confirmed": request.POST.get("stock_confirmed")
            in ("1", "true", "on", "yes"),
            "login_code": (request.POST.get("login_code") or "").strip(),
        }
        payload = {
            "cash_amount": form_data["cash_amount"] or "0",
            "mpesa_amount": form_data["mpesa_amount"] or "0",
            "credit_amount": form_data["credit_amount"] or "0",
            "stock_confirmed": form_data["stock_confirmed"],
            "login_code": form_data["login_code"],
        }
        try:
            result = (
                close_shop_day(shop=shop, payload=payload)
                if is_open
                else open_shop_day(shop=shop, payload=payload)
            )
        except ValidationError as exc:
            form_errors = _validation_errors(exc)
            if wants_json:
                return JsonResponse(
                    {"ok": False, "errors": form_errors},
                    status=400,
                )
        else:
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": result["message"],
                        "action": result["action"],
                    }
                )
            messages.success(request, result["message"])
            return redirect("employees:my_shop_day_toggle", shop_id=shop.pk)

    # Refresh open state after failed post / for GET
    open_session = get_open_shop_day(shop)
    is_open = open_session is not None
    mode = "close" if is_open else "open"
    day_sessions = list_shop_day_sessions(shop, limit=40)
    open_summary = (
        day_session_balance_summary(open_session) if open_session else {}
    )
    last_closed = None if is_open else get_last_closed_shop_day(shop)

    # Suggest expected closing balances when closing (leave blank if user typed).
    if is_open and open_summary and not form_errors:
        if not form_data["cash_amount"]:
            form_data["cash_amount"] = str(
                int(open_summary["expected_cash"].quantize(Decimal("1")))
            )
        if not form_data["mpesa_amount"]:
            form_data["mpesa_amount"] = str(
                int(open_summary["expected_mpesa"].quantize(Decimal("1")))
            )
        if not form_data["credit_amount"]:
            form_data["credit_amount"] = str(
                int(open_summary["expected_credit"].quantize(Decimal("1")))
            )
    # Suggest last closing as opening balances when starting a new day.
    elif not is_open and last_closed and not form_errors:
        if not form_data["cash_amount"] and last_closed.closing_cash is not None:
            form_data["cash_amount"] = str(
                int(Decimal(last_closed.closing_cash).quantize(Decimal("1")))
            )
        if not form_data["mpesa_amount"] and last_closed.closing_mpesa is not None:
            form_data["mpesa_amount"] = str(
                int(Decimal(last_closed.closing_mpesa).quantize(Decimal("1")))
            )
        if not form_data["credit_amount"] and last_closed.closing_credit is not None:
            form_data["credit_amount"] = str(
                int(Decimal(last_closed.closing_credit).quantize(Decimal("1")))
            )

    return _render_my_shop_tool_page(
        request,
        shop=shop,
        profile=profile,
        shops=shops,
        active="day_toggle",
        title=("Close shop" if is_open else "Open shop") + f" — {shop.name}",
        headline="Close shop" if is_open else "Open shop",
        summary=(
            f"Record closing cash, M-Pesa, and credit balances for {shop.name}, "
            f"then review opening/closing history below."
            if is_open
            else f"Record opening cash, M-Pesa, and credit balances for {shop.name}. "
            f"Each open/close is saved in the list below."
        ),
        icon="door-closed" if is_open else "door-open",
        template_name="shops/my_shop_day_toggle.html",
        extra_context={
            "mode": mode,
            "is_open": is_open,
            "open_session": open_session,
            "open_summary": open_summary,
            "last_closed": last_closed,
            "day_sessions": day_sessions,
            "day_session_count": len(day_sessions),
            "form_data": form_data,
            "form_errors": form_errors,
            "verify_login_code_url": reverse(
                "employees:my_shop_verify_login_code", kwargs={"shop_id": shop.pk}
            ),
        },
    )


@shop_floor_required
@require_POST
def my_shop_verify_login_code(request, shop_id):
    """Validate an active employee 6-digit ID before accept/decline is unlocked."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)

    code = (request.POST.get("login_code") or request.POST.get("employee_code") or "").strip()
    authorising = verify_active_employee_code(code)
    if authorising is None:
        return JsonResponse(
            {"ok": False, "error": "Not a valid active staff ID."},
            status=400,
        )
    from employees.module_permissions import my_shop_capabilities

    name = authorising.user.get_full_name() or authorising.user.username
    return JsonResponse(
        {
            "ok": True,
            "employee_id": authorising.employee_id,
            "name": name,
            "capabilities": my_shop_capabilities(authorising),
        }
    )


@shop_floor_required
@require_POST
def my_shop_checkout(request, shop_id):
    """Complete cart checkout as sale, credit, or quotation."""
    import json

    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)

    try:
        if request.content_type and "application/json" in request.content_type:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = {
                "kind": request.POST.get("kind"),
                "payment_method": request.POST.get("payment_method"),
                "cash_amount": request.POST.get("cash_amount"),
                "mpesa_amount": request.POST.get("mpesa_amount"),
                "client_name": request.POST.get("client_name"),
                "client_phone": request.POST.get("client_phone"),
                "login_code": request.POST.get("login_code"),
                "share_whatsapp": request.POST.get("share_whatsapp")
                in ("1", "true", "on", "yes"),
                "lines": json.loads(request.POST.get("lines") or "[]"),
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid checkout payload."}, status=400)

    kind = str(payload.get("kind") or "").strip().lower()
    perm_key = kind if kind in {"sale", "credit", "quotation"} else "workspace"
    denied = _require_my_shop_permission(
        request,
        profile,
        perm_key,
        as_json=True,
        login_code=payload.get("login_code"),
    )
    if denied:
        return denied

    try:
        result = complete_shop_checkout(shop=shop, profile=profile, payload=payload)
    except ValidationError as exc:
        errors = _validation_errors(exc)
        return JsonResponse(
            {"ok": False, "error": errors[0] if errors else "Checkout failed.", "errors": errors},
            status=400,
        )

    return JsonResponse(
        {
            "ok": True,
            "receipt_number": result["receipt_number"],
            "kind": result["kind"],
            "kind_label": result["kind_label"],
            "total": result["total"],
            "whatsapp_url": result["whatsapp_url"],
            "authorised_by": result["authorised_by"],
            "print_via": result.get("print_via") or "",
            "print_required": bool(result.get("print_required")),
            "receipt_text": result.get("message") or "",
            "receipt_ticket": result.get("receipt_ticket") or {},
            "receipt_qr": result.get("receipt_qr") or {},
            "receipt_font": result.get("receipt_font") or {},
            "receipt_paper_width": result.get("receipt_paper_width") or "80",
            "stock_updates": result.get("stock_updates") or [],
            "message": (
                f"{result['kind_label']} {result['receipt_number']} completed "
                f"(KSh {result['total']})."
            ),
            "mpesa_receipt_number": result.get("mpesa_receipt_number") or "",
        }
    )


@shop_floor_required
@require_POST
def my_shop_stk_initiate(request, shop_id):
    """Start an STK Push for the M-Pesa portion of a sale checkout."""
    import json

    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(request, profile, "sale", as_json=True, portal_ok=True)
    if denied:
        return denied
    sync_callback_base_from_request(request, persist=True)
    if not stk_ready():
        return JsonResponse(
            {"ok": False, "error": "STK Push is not enabled in Daraja settings."},
            status=400,
        )

    try:
        if request.content_type and "application/json" in request.content_type:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = request.POST
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid STK payload."}, status=400)

    try:
        payment = initiate_stk_push(
            purpose="sale",
            amount=payload.get("amount"),
            phone=payload.get("phone") or "",
            account_reference=f"SHOP{shop.pk}",
            description=(payload.get("description") or f"{shop.name} sale")[:40],
            shop=shop,
            profile=profile,
            request=request,
        )
    except ValidationError as exc:
        errors = _validation_errors(exc)
        return JsonResponse(
            {"ok": False, "error": errors[0] if errors else "STK Push failed."},
            status=400,
        )

    return JsonResponse(
        {
            "ok": True,
            "message": "STK Push sent. Ask the customer to enter their M-Pesa PIN.",
            **stk_payment_payload(payment),
        }
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_stk_status(request, shop_id, payment_id):
    """Poll STK Push status for a shop sale."""
    _profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)

    payment = get_stk_payment(payment_id)
    if payment is None or (payment.shop_id and payment.shop_id != shop.pk):
        return JsonResponse({"ok": False, "error": "STK payment not found."}, status=404)

    return JsonResponse({"ok": True, **stk_payment_payload(payment)})


from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
@require_POST
def daraja_stk_callback(request):
    """Public Safaricom STK callback."""
    import json

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    handle_stk_callback(payload)
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


def _is_lan_printer_host(host: str) -> bool:
    """Allow only loopback / private LAN hosts for raw printer relay (SSRF guard)."""
    import ipaddress
    import socket

    host = (host or "").strip().lower()
    if not host or host in {"localhost"}:
        return host == "localhost"
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
        ):
            return True
    return False


@shop_floor_required
@require_POST
def my_shop_print_relay(request, shop_id):
    """Relay ESC/POS bytes to a LAN Wi‑Fi/Ethernet thermal printer (port 9100)."""
    import base64
    import json
    import socket

    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(request, profile, "print", as_json=True, portal_ok=True)
    if denied:
        return denied

    pos = get_company_pos_settings()
    if not pos.print_channel_enabled("wifi"):
        return JsonResponse(
            {"ok": False, "error": "Wi‑Fi printing is disabled in POS settings."},
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid print payload."}, status=400)

    host = str(payload.get("host") or "").strip()
    try:
        port = int(payload.get("port") or 9100)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid printer port."}, status=400)
    if port < 1 or port > 65535:
        return JsonResponse({"ok": False, "error": "Invalid printer port."}, status=400)
    if not _is_lan_printer_host(host):
        return JsonResponse(
            {
                "ok": False,
                "error": "Printer host must be a local/LAN address (e.g. 192.168.x.x).",
            },
            status=400,
        )

    raw_b64 = str(payload.get("data") or "").strip()
    if not raw_b64:
        return JsonResponse({"ok": False, "error": "Missing print data."}, status=400)
    try:
        data = base64.b64decode(raw_b64, validate=True)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid print data encoding."}, status=400)
    if not data or len(data) > 256_000:
        return JsonResponse({"ok": False, "error": "Print data is empty or too large."}, status=400)

    # Explain subnet mismatch before attempting a long connect timeout.
    try:
        import ipaddress

        from .printer_discovery import _local_ipv4_addresses, _networks_from_local_ips

        local_ips = _local_ipv4_addresses()
        networks = _networks_from_local_ips(local_ips)
        printer_ip = ipaddress.ip_address(host)
        if networks and isinstance(printer_ip, ipaddress.IPv4Address):
            if not any(printer_ip in net for net in networks):
                local_list = ", ".join(local_ips) or "unknown"
                nets = ", ".join(str(net) for net in networks)
                return JsonResponse(
                    {
                        "ok": False,
                        "error": (
                            f"Printer {host} is on a different network than this PC "
                            f"({local_list} / {nets}). "
                            "Put the printer on the same Wi‑Fi as this computer, "
                            "then use the printer’s IP from that network (or tap Scan)."
                        ),
                        "host": host,
                        "port": port,
                        "local_ips": local_ips,
                        "networks": [str(net) for net in networks],
                    },
                    status=502,
                )
    except ValueError:
        pass

    try:
        with socket.create_connection((host, port), timeout=2.5) as sock:
            sock.settimeout(4)
            sock.sendall(data)
    except OSError as exc:
        detail = str(exc).lower()
        if "timed out" in detail or "10060" in detail:
            msg = (
                f"Could not reach printer at {host}:{port} (timed out). "
                "Confirm the printer is powered on, on the same Wi‑Fi as this PC, "
                "and that raw printing (port 9100) is enabled."
            )
        else:
            msg = f"Could not reach printer at {host}:{port} ({exc})."
        return JsonResponse({"ok": False, "error": msg}, status=502)

    return JsonResponse({"ok": True, "host": host, "port": port})


@shop_floor_required
@require_http_methods(["GET", "POST"])
def my_shop_wifi_printer_scan(request, shop_id):
    """Scan the server LAN for Wi‑Fi/Ethernet printers (printer ports only)."""
    from .printer_discovery import discover_lan_printers

    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)
    denied = _require_my_shop_permission(request, profile, "print", as_json=True, portal_ok=True)
    if denied:
        return denied

    pos = get_company_pos_settings()
    if not pos.print_channel_enabled("wifi"):
        return JsonResponse(
            {"ok": False, "error": "Wi‑Fi printing is disabled in POS settings."},
            status=400,
        )

    thorough = False
    if request.method == "GET":
        thorough = request.GET.get("thorough", "0") in ("1", "true", "yes")
    else:
        import json

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        thorough = str(payload.get("thorough", "0")).lower() in (
            "1",
            "true",
            "yes",
        )

    result = discover_lan_printers(thorough=thorough)
    if not result.get("ok"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_serial_search(request, shop_id):
    """Live search available serials for sell / stock-request on the shop floor.

    Uses shop session (portal or unlocked employee shop), not employee portal
    login — ``employees:serial_search`` is unavailable under shop portal auth.
    """
    from items.services import search_available_serials

    profile, shop, denied = _require_shop_read_access(request, shop_id)
    if denied:
        return denied

    item_id = (request.GET.get("item_id") or "").strip()
    query = (request.GET.get("q") or "").strip()
    match = (request.GET.get("match") or "contains").strip().lower()
    exclude = request.GET.getlist("exclude") or []
    results = search_available_serials(
        item_id=item_id,
        shop_id=shop.pk,
        query=query,
        exclude=exclude,
        limit=12,
        match=match,
    )
    return JsonResponse(
        {
            "ok": True,
            "match": match,
            "shop_id": shop.pk,
            "results": results,
        }
    )


@shop_floor_required
@require_http_methods(["GET"])
def my_shop_client_lookup(request, shop_id):
    """Look up registered clients by phone or live-search by name."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return JsonResponse({"ok": False, "error": "Shop session required."}, status=403)

    phone = (request.GET.get("phone") or "").strip()
    name_query = (request.GET.get("name") or request.GET.get("q") or "").strip()

    if phone:
        client = find_client_by_phone(phone)
        if client is None:
            return JsonResponse({"ok": True, "found": False, "results": []})
        payload = {
            "id": client.pk,
            "full_name": (client.full_name or "").upper(),
            "phone_number": format_kenya_phone(
                client.phone_number or client.phone_normalized
            ),
        }
        return JsonResponse({"ok": True, "found": True, "results": [payload], **payload})

    if name_query:
        clients = search_clients_by_name(name_query)
        results = [
            {
                "id": client.pk,
                "full_name": (client.full_name or "").upper(),
                "phone_number": format_kenya_phone(
                    client.phone_number or client.phone_normalized
                ),
            }
            for client in clients
        ]
        return JsonResponse(
            {
                "ok": True,
                "found": bool(results),
                "results": results,
            }
        )

    return JsonResponse({"ok": True, "found": False, "results": []})


@shop_floor_required
@require_POST
def my_shop_stock_request_respond(request, shop_id, request_id):
    """Accept or decline a pending stock request (requires shop login code)."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        if _wants_json_response(request):
            return JsonResponse(
                {"ok": False, "error": "Shop session required."}, status=403
            )
        return denied

    movement = get_object_or_404(
        StockMovement.objects.select_related("shop", "requested_from_shop"),
        pk=request_id,
        movement_type=StockMovementType.REQUEST,
        requested_from_shop=shop,
        request_status=StockRequestStatus.PENDING,
    )

    decision = (request.POST.get("decision") or "").strip().lower()
    login_code = (request.POST.get("login_code") or "").strip()
    wants_json = _wants_json_response(request)
    serials_by_line = {}
    quantities_by_line = {}
    for key, values in request.POST.lists():
        if key.startswith("serials_"):
            line_id = key.replace("serials_", "", 1).strip()
            if line_id:
                serials_by_line[line_id] = values
        elif key.startswith("qty_"):
            line_id = key.replace("qty_", "", 1).strip()
            if line_id:
                quantities_by_line[line_id] = (values[0] if values else "").strip()

    try:
        movement = respond_to_stock_request(
            movement=movement,
            profile=profile,
            decision=decision,
            login_code=login_code,
            serials_by_line=serials_by_line,
            quantities_by_line=quantities_by_line,
        )
    except ValidationError as exc:
        errors = _validation_errors(exc)
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": errors[0] if errors else "Could not respond."},
                status=400,
            )
        for message in errors:
            messages.error(request, message)
        return redirect("employees:my_shop_workspace", shop_id=shop.pk)

    requester_name = movement.shop.name if movement.shop else "the requesting shop"
    if decision == "accept":
        success_message = (
            f"Stock request from {requester_name} accepted. Stock transferred."
        )
    else:
        success_message = (
            f"Stock request from {requester_name} declined. They will be notified."
        )
    messages.success(request, success_message)

    next_url = reverse("employees:my_shop_workspace", kwargs={"shop_id": shop.pk})
    if wants_json:
        payload = {
            "ok": True,
            "message": success_message,
            "next": next_url,
            "decision": decision,
        }
        if decision == "accept":
            authorising = getattr(movement, "responded_by", None)
            print_payload = build_stock_request_delivery_note(
                movement, shop=shop, authorised_by=authorising
            )
            payload.update(print_payload)
        return JsonResponse(payload)
    return redirect(next_url)


@shop_floor_required
@require_POST
def my_shop_stock_request_result_ack(request, shop_id, request_id):
    """Requesting shop acknowledges an accept/decline notification."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied
    denied = _require_my_shop_permission(request, profile, "respond_stock_request", portal_ok=True)
    if denied:
        return denied

    movement = get_object_or_404(
        StockMovement,
        pk=request_id,
        movement_type=StockMovementType.REQUEST,
        shop=shop,
        request_status__in=(
            StockRequestStatus.FULFILLED,
            StockRequestStatus.DECLINED,
        ),
        requester_notified=False,
    )
    movement.requester_notified = True
    movement.save(update_fields=["requester_notified"])
    return redirect("employees:my_shop_workspace", shop_id=shop.pk)


@shop_floor_required
@require_POST
def my_shop_stock_request_results_ack_all(request, shop_id):
    """Acknowledge all pending accept/decline notifications for this shop."""
    profile, shop, denied = _require_active_shop_session(request, shop_id)
    if denied:
        return denied

    StockMovement.objects.filter(
        movement_type=StockMovementType.REQUEST,
        shop=shop,
        request_status__in=(
            StockRequestStatus.FULFILLED,
            StockRequestStatus.DECLINED,
        ),
        requester_notified=False,
    ).update(requester_notified=True)
    return redirect("employees:my_shop_workspace", shop_id=shop.pk)
