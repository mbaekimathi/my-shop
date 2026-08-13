from itertools import groupby
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import F, Q
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from employees.access import active_employee_required
from employees.countries import COUNTRY_DIAL_CODES
from employees.workspace import sidebar_for_stock_management

from .models import Item, ShopItemPrice, ShopStock
from .services import (
    actionable_shops_for_profile,
    apply_serial_status,
    apply_stock_movement,
    build_stock_catalog_page,
    build_stock_print_document,
    build_stock_print_pdf,
    check_serials_already_in_stock,
    create_item,
    delete_item,
    estimate_stock_print_a4_pages,
    last_buying_prices_for_items,
    search_available_serials,
    search_suppliers,
    toggle_item_suspended,
    update_item,
)

EMPTY_FORM = {
    "category": "",
    "name": "",
    "description": "",
    "minimum_selling_price": "",
    "maximum_selling_price": "",
    "shop_price": "",
    "pricing_mode": "single",
    "shop_prices": {},
    "track_serial_number": False,
}


@active_employee_required
@require_GET
def supplier_search_api(request):
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


@active_employee_required
@require_GET
def serial_search_api(request):
    item_id = (request.GET.get("item_id") or "").strip()
    shop_id = (request.GET.get("shop_id") or "").strip()
    query = (request.GET.get("q") or "").strip()
    match = (request.GET.get("match") or "contains").strip().lower()
    exclude = request.GET.getlist("exclude") or []
    results = search_available_serials(
        item_id=item_id,
        shop_id=shop_id,
        query=query,
        exclude=exclude,
        limit=12,
        match=match,
    )
    return JsonResponse({"ok": True, "results": results, "match": match})


@active_employee_required
@require_GET
def serial_in_stock_check_api(request):
    """Live check: is this serial already available for stock-in?"""
    item_id = (request.GET.get("item_id") or "").strip()
    serials = request.GET.getlist("serial") or []
    if not serials:
        raw = (request.GET.get("q") or request.GET.get("serial") or "").strip()
        if raw:
            serials = [raw]
    found = check_serials_already_in_stock(item_id=item_id, serials=serials)
    ordered = []
    seen = set()
    for raw in serials:
        serial = str(raw or "").strip().upper()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        hit = found.get(serial)
        ordered.append(
            {
                "serial": serial,
                "in_stock": bool(hit),
                "shop_name": (hit or {}).get("shop_name") or "",
            }
        )
        if len(ordered) >= 12:
            break
    return JsonResponse({"ok": True, "results": ordered})


def _active_pricing_shops():
    from shops.models import Shop

    return list(Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name"))


def _pricing_shops_for_profile(profile):
    """Active shops allocated to the signed-in employee."""
    return list(
        profile.assigned_shops.filter(is_hidden=False, is_suspended=False).order_by("name")
    )


def _serial_shops_for_profile(profile):
    """Shops the signed-in employee may see on serials pages (allocated shops only)."""
    from employees.models import SHOP_ASSIGNABLE_ROLES

    allocated = _pricing_shops_for_profile(profile)
    if allocated or profile.role in SHOP_ASSIGNABLE_ROLES:
        return allocated
    return actionable_shops_for_profile(profile)


def _serial_shops_label(shops):
    if not shops:
        return "No allocated shops"
    if len(shops) == 1:
        return shops[0].name
    return f"{len(shops)} allocated shops"


def _shop_prices_from_post(post, shops) -> dict:
    prices = {}
    for shop in shops:
        raw = (post.get(f"shop_price_{shop.pk}") or "").strip()
        if raw:
            prices[str(shop.pk)] = raw
    return prices


def _form_data_from_post(post) -> dict:
    shops = _active_pricing_shops()
    pricing_mode = (post.get("pricing_mode") or "single").strip().lower()
    if pricing_mode not in ("single", "individual"):
        pricing_mode = "single"
    return {
        "category": post.get("category", "").strip().upper(),
        "name": post.get("name", "").strip().upper(),
        "description": post.get("description", "").strip(),
        "minimum_selling_price": post.get("minimum_selling_price", "").strip(),
        "maximum_selling_price": post.get("maximum_selling_price", "").strip(),
        "shop_price": post.get("shop_price", "").strip(),
        "pricing_mode": pricing_mode,
        "shop_prices": _shop_prices_from_post(post, shops),
        "track_serial_number": (post.get("track_serial_number") or "").strip().lower()
        in ("1", "true", "on", "yes"),
    }


def _form_data_from_item(item: Item) -> dict:
    shop_prices = {
        str(shop_id): str(price)
        for shop_id, price in ShopItemPrice.objects.filter(item=item).values_list(
            "shop_id", "price"
        )
    }
    return {
        "category": item.category,
        "name": item.name,
        "description": item.description,
        "minimum_selling_price": str(item.minimum_selling_price),
        "maximum_selling_price": str(item.maximum_selling_price),
        "shop_price": str(item.shop_price),
        "pricing_mode": "individual" if item.use_individual_shop_prices else "single",
        "shop_prices": shop_prices,
        "track_serial_number": item.track_serial_number,
    }


def _shop_price_display(item: Item, prices_by_item: dict) -> str:
    if not item.use_individual_shop_prices:
        return f"KSh {item.shop_price:.2f}"
    prices = prices_by_item.get(item.pk) or []
    if not prices:
        return f"KSh {item.shop_price:.2f}"
    low = min(prices)
    high = max(prices)
    if low == high:
        return f"KSh {low:.2f}"
    return f"KSh {low:.2f} – {high:.2f}"


def _shop_prices_json(item: Item, prices_map: dict) -> str:
    payload = {
        str(shop_id): f"{price:.2f}"
        for shop_id, price in (prices_map.get(item.pk) or {}).items()
    }
    if not payload and not item.use_individual_shop_prices:
        # Prefill all shops from global price when opening edit in individual mode later.
        return "{}"
    return json.dumps(payload)


def _validation_errors(exc: ValidationError) -> list:
    return exc.messages if hasattr(exc, "messages") else [str(exc)]


@require_http_methods(["GET", "POST"])
def item_management(request, profile, meta, module, page_sidebar):
    from employees.module_permissions import (
        module_capabilities,
        require_module_permission,
    )

    form_data = dict(EMPTY_FORM)
    form_errors = []
    open_register_modal = False
    open_edit_modal = False
    edit_item = None
    caps = module_capabilities(profile, "item-management")

    if request.method == "POST":
        action = (request.POST.get("action") or "register").strip()
        item_id = (request.POST.get("item_id") or "").strip()
        denied = require_module_permission(
            request, profile, "item-management", action
        )
        if denied is not None:
            return denied

        if action == "register":
            form_data = _form_data_from_post(request.POST)
            try:
                create_item(profile, request.POST, request.FILES)
            except ValidationError as exc:
                form_errors = _validation_errors(exc)
                open_register_modal = True
            else:
                messages.success(request, f"Item “{form_data['name']}” registered successfully.")
                return redirect(request.path)

        elif action == "edit":
            edit_item = get_object_or_404(Item, pk=item_id)
            form_data = _form_data_from_post(request.POST)
            editable_shop_ids = {
                shop.pk for shop in _pricing_shops_for_profile(profile)
            }
            try:
                update_item(
                    edit_item,
                    request.POST,
                    request.FILES,
                    editable_shop_ids=editable_shop_ids,
                )
            except ValidationError as exc:
                form_errors = _validation_errors(exc)
                open_edit_modal = True
            else:
                messages.success(request, f"Item “{form_data['name']}” updated successfully.")
                return redirect(request.path)

        elif action == "toggle_suspend":
            item = get_object_or_404(Item, pk=item_id)
            toggle_item_suspended(item)
            state = "suspended" if item.is_suspended else "unsuspended"
            messages.success(request, f"Item “{item.name}” {state}.")
            return redirect(request.path)

        elif action == "delete":
            item = get_object_or_404(Item, pk=item_id)
            name = item.name
            delete_item(item)
            messages.success(request, f"Item “{name}” deleted.")
            return redirect(request.path)

        else:
            messages.error(request, "Unknown action.")
            return redirect(request.path)
    else:
        denied = require_module_permission(request, profile, "item-management", "view")
        if denied is not None:
            return denied

    pricing_shops = _active_pricing_shops()
    edit_pricing_shops = _pricing_shops_for_profile(profile)
    from employees.access import role_url_segment

    item_count = Item.objects.count()
    item_catalog_url = reverse(
        "employees:item_management_catalog",
        kwargs={"role_segment": role_url_segment(profile.role)},
    )

    return render(
        request,
        "items/item_management.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "items_by_category": [],
            "item_count": item_count,
            "use_item_catalog_api": True,
            "item_catalog_url": item_catalog_url,
            "pricing_shops": pricing_shops,
            "edit_pricing_shops": edit_pricing_shops,
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "open_edit_modal": open_edit_modal,
            "edit_item": edit_item,
            "module_permissions": caps,
        },
    )


@active_employee_required
@require_http_methods(["GET"])
def item_management_catalog(request, role_segment):
    """Paginated item-management catalog."""
    from employees.access import get_profile_for_request, role_url_segment
    from employees.module_permissions import require_module_permission

    from .services import build_item_management_catalog_page

    profile = get_profile_for_request(request)
    if profile is None or not profile.is_active_employee:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if role_url_segment(profile.role) != role_segment:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    denied = require_module_permission(
        request, profile, "item-management", "view", as_json=True
    )
    if denied is not None:
        return denied

    payload = build_item_management_catalog_page(
        q=request.GET.get("q") or "",
        page=request.GET.get("page") or 1,
        page_size=request.GET.get("page_size") or 48,
        sort=request.GET.get("sort") or "category",
    )
    return JsonResponse(payload)


def _parse_request_shop_ids(request, *, allow_csv=True):
    """Collect shop ids from repeated shop_id params and optional shop_ids CSV."""
    raw_values = []
    if hasattr(request, "GET"):
        raw_values.extend(request.GET.getlist("shop_id"))
        if allow_csv and request.GET.get("shop_ids"):
            raw_values.append(request.GET.get("shop_ids"))
    if request.method == "POST":
        raw_values.extend(request.POST.getlist("shop_id"))
        filter_csv = (request.POST.get("filter_shop_ids") or "").strip()
        if filter_csv:
            raw_values.append(filter_csv)
        if allow_csv and request.POST.get("shop_ids"):
            raw_values.append(request.POST.get("shop_ids"))

    ids = []
    seen = set()
    for raw in raw_values:
        parts = [raw] if isinstance(raw, int) else str(raw or "").replace(";", ",").split(",")
        for part in parts:
            value = str(part).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            ids.append(value)
    return ids


def _stock_redirect(path, mode, *, shop_id="", shop_ids=None, requested_from_shop_id=""):
    from urllib.parse import urlencode

    params = [("mode", mode)]
    ids = []
    if shop_ids:
        ids = [str(sid).strip() for sid in shop_ids if str(sid).strip()]
    elif shop_id:
        ids = [str(shop_id).strip()]
    for sid in ids:
        params.append(("shop_id", sid))
    if mode == "request" and requested_from_shop_id:
        params.append(("requested_from_shop_id", str(requested_from_shop_id)))
    return redirect(f"{path}?{urlencode(params)}")


def _stock_next_url(path, mode, *, shop_id="", shop_ids=None, requested_from_shop_id=""):
    from urllib.parse import urlencode

    params = [("mode", mode)]
    ids = []
    if shop_ids:
        ids = [str(sid).strip() for sid in shop_ids if str(sid).strip()]
    elif shop_id:
        ids = [str(shop_id).strip()]
    for sid in ids:
        params.append(("shop_id", sid))
    if mode == "request" and requested_from_shop_id:
        params.append(("requested_from_shop_id", str(requested_from_shop_id)))
    return f"{path}?{urlencode(params)}"


def _wants_json_response(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    requested_with = (request.headers.get("X-Requested-With") or "").lower()
    return (
        "application/json" in accept
        or requested_with == "xmlhttprequest"
        or (request.POST.get("ajax") or request.GET.get("ajax") or "") == "1"
    )


def _parse_report_date(raw, *, fallback=None):
    from datetime import date, datetime

    from django.utils import timezone

    today = timezone.localdate() if fallback is None else fallback
    value = (raw or "").strip()
    if not value:
        return today
    try:
        return date.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return today


def _parse_report_month(raw, *, fallback=None):
    from datetime import date, datetime

    from django.utils import timezone

    today = timezone.localdate() if fallback is None else fallback
    value = (raw or "").strip()
    if not value:
        return today.replace(day=1)
    try:
        parsed = datetime.strptime(value, "%Y-%m").date()
        return parsed.replace(day=1)
    except ValueError:
        return today.replace(day=1)


def _parse_report_year(raw, *, fallback=None):
    from django.utils import timezone

    today = timezone.localdate() if fallback is None else fallback
    value = (raw or "").strip()
    if not value:
        return today.year
    try:
        year = int(value)
    except (TypeError, ValueError):
        return today.year
    if year < 2000 or year > 2100:
        return today.year
    return year


def _report_range_bounds(request):
    """Return (range_type, start_dt, end_dt, filter_context) for stock report filters."""
    from calendar import monthrange
    from datetime import date, datetime, time, timedelta

    from django.utils import timezone

    today = timezone.localdate()
    range_type = (request.GET.get("range") or "day").strip().lower()
    if range_type not in ("day", "period", "month", "year"):
        range_type = "day"

    tz = timezone.get_current_timezone()

    def aware_start(day):
        return timezone.make_aware(datetime.combine(day, time.min), tz)

    def aware_end_exclusive(day):
        return aware_start(day) + timedelta(days=1)

    def base_context(**extra):
        ctx = {
            "report_range": range_type,
            "report_date_value": today.isoformat(),
            "report_date_from": today.isoformat(),
            "report_date_to": today.isoformat(),
            "report_month_value": today.strftime("%Y-%m"),
            "report_year_value": f"{today.year}-01",
        }
        ctx.update(extra)
        return ctx

    if range_type == "period":
        date_from = _parse_report_date(request.GET.get("date_from"), fallback=today)
        date_to = _parse_report_date(request.GET.get("date_to"), fallback=today)
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        start = aware_start(date_from)
        end = aware_end_exclusive(date_to)
        label = (
            f"{date_from.strftime('%d %b %Y')} – {date_to.strftime('%d %b %Y')}"
        )
        return (
            range_type,
            start,
            end,
            base_context(
                report_date=date_from,
                report_date_value=date_from.isoformat(),
                report_date_from=date_from.isoformat(),
                report_date_to=date_to.isoformat(),
                report_period_label=label,
            ),
        )

    if range_type == "month":
        month_start = _parse_report_month(request.GET.get("month"), fallback=today)
        last_day = monthrange(month_start.year, month_start.month)[1]
        month_end = month_start.replace(day=last_day)
        start = aware_start(month_start)
        end = aware_end_exclusive(month_end)
        return (
            range_type,
            start,
            end,
            base_context(
                report_date=month_start,
                report_month_value=month_start.strftime("%Y-%m"),
                report_year_value=f"{month_start.year}-01",
                report_period_label=month_start.strftime("%B %Y"),
            ),
        )

    if range_type == "year":
        year_raw = (request.GET.get("year") or "").strip()
        if "-" in year_raw:
            year = _parse_report_month(year_raw, fallback=today).year
        else:
            year = _parse_report_year(year_raw, fallback=today)
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        start = aware_start(year_start)
        end = aware_end_exclusive(year_end)
        return (
            range_type,
            start,
            end,
            base_context(
                report_date=year_start,
                report_year_value=f"{year}-01",
                report_period_label=str(year),
            ),
        )

    report_date = _parse_report_date(request.GET.get("date"), fallback=today)
    start = aware_start(report_date)
    end = aware_end_exclusive(report_date)
    return (
        "day",
        start,
        end,
        base_context(
            report_range="day",
            report_date=report_date,
            report_date_value=report_date.isoformat(),
            report_date_from=report_date.isoformat(),
            report_date_to=report_date.isoformat(),
            report_month_value=report_date.strftime("%Y-%m"),
            report_year_value=f"{report_date.year}-01",
            report_period_label=report_date.strftime("%d %b %Y"),
        ),
    )


def _parse_id_list(raw_values):
    ids = []
    seen = set()
    for value in raw_values:
        value = (value or "").strip()
        if not value:
            continue
        try:
            pk = int(value)
        except (TypeError, ValueError):
            continue
        if pk in seen:
            continue
        seen.add(pk)
        ids.append(pk)
    return ids


def _movement_qty_by_item(item_ids, shop_ids, start, end):
    """Sum movement line quantities by item and type for a window. Request is separate from in/out."""
    from django.db.models import Sum

    from .models import StockMovementLine, StockMovementType

    totals = {
        item_id: {"in": 0, "out": 0, "request": 0}
        for item_id in item_ids
    }
    if not item_ids or not shop_ids or start >= end:
        return totals

    rows = (
        StockMovementLine.objects.filter(
            item_id__in=item_ids,
            movement__shop_id__in=shop_ids,
            movement__created_at__gte=start,
            movement__created_at__lt=end,
            movement__movement_type__in=[
                StockMovementType.IN,
                StockMovementType.OUT,
                StockMovementType.REQUEST,
            ],
        )
        .values("item_id", "movement__movement_type")
        .annotate(total=Sum("quantity"))
    )
    for row in rows:
        bucket = totals.get(row["item_id"])
        if bucket is None:
            continue
        movement_type = row["movement__movement_type"]
        if movement_type in bucket:
            bucket[movement_type] = int(row["total"] or 0)
    return totals


def _sale_qty_by_item(items, shop_ids, start, end):
    """
    Sale quantities for stock items.

    Prefer MY-SHOP receipt lines (item FK) — accurate and indexed.
    Fall back to legacy POS SaleLine matching by product name.
    """
    from django.db.models import Sum

    from pos.models import SaleLine
    from shops.models import ShopReceiptKind, ShopReceiptLine

    totals = {item.pk: 0 for item in items}
    if not items or start >= end:
        return totals

    item_ids = [item.pk for item in items]
    receipt_qs = ShopReceiptLine.objects.filter(
        item_id__in=item_ids,
        receipt__created_at__gte=start,
        receipt__created_at__lt=end,
        receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
    )
    if shop_ids:
        receipt_qs = receipt_qs.filter(receipt__shop_id__in=shop_ids)

    for row in receipt_qs.values("item_id").annotate(total=Sum("quantity")):
        item_id = row["item_id"]
        if item_id in totals:
            totals[item_id] += int(row["total"] or 0)

    name_to_ids = {}
    for item in items:
        key = (item.name or "").strip().lower()
        if key:
            name_to_ids.setdefault(key, []).append(item.pk)
    if not name_to_ids:
        return totals

    # Bound the POS scan to known item names instead of loading all sale lines.
    names = [(item.name or "").strip() for item in items if (item.name or "").strip()]
    sale_lines = SaleLine.objects.filter(
        sale__sold_at__gte=start,
        sale__sold_at__lt=end,
        product_name__in=names,
    )
    if shop_ids:
        sale_lines = sale_lines.filter(
            sale__employee__assigned_shops__in=shop_ids
        ).distinct()

    for row in sale_lines.values("product_name").annotate(total=Sum("quantity")):
        key = (row["product_name"] or "").strip().lower()
        for item_id in name_to_ids.get(key, []):
            totals[item_id] += int(row["total"] or 0)
    return totals


def _current_stock_by_item(item_ids, shop_ids):
    from django.db.models import Sum

    from .models import ShopStock

    totals = {item_id: 0 for item_id in item_ids}
    if not item_ids or not shop_ids:
        return totals
    rows = (
        ShopStock.objects.filter(item_id__in=item_ids, shop_id__in=shop_ids)
        .values("item_id")
        .annotate(total=Sum("quantity"))
    )
    for row in rows:
        totals[row["item_id"]] = int(row["total"] or 0)
    return totals


def _build_item_report_rows(items, shop_ids, day_start, day_end):
    """
    Build per-item report rows for a period.

    closing = current - after_in + after_out + after_sale
    starting = closing - period_in + period_out + period_sale
    Request is tracked separately and does not change starting/closing.
    """
    from datetime import timedelta

    from django.utils import timezone

    item_ids = [item.pk for item in items]
    if not item_ids:
        return []

    now = timezone.now()
    far_future = now + timedelta(days=3650)

    current = _current_stock_by_item(item_ids, shop_ids)
    period_moves = _movement_qty_by_item(item_ids, shop_ids, day_start, day_end)
    after_moves = _movement_qty_by_item(item_ids, shop_ids, day_end, far_future)
    period_sales = _sale_qty_by_item(items, shop_ids, day_start, day_end)
    after_sales = _sale_qty_by_item(items, shop_ids, day_end, far_future)

    rows = []
    for item in items:
        period = period_moves[item.pk]
        after = after_moves[item.pk]
        stock_in = period["in"]
        stock_out = period["out"]
        stock_request = period["request"]
        stock_sale = period_sales[item.pk]
        closing = (
            current[item.pk]
            - after["in"]
            + after["out"]
            + after_sales[item.pk]
        )
        starting = closing - stock_in + stock_out + stock_sale
        # Only include stocks that had activity in the filtered period.
        if not (stock_in or stock_out or stock_request or stock_sale):
            continue
        rows.append(
            {
                "item": item,
                "starting_stock": starting,
                "stock_request": stock_request,
                "stock_in": stock_in,
                "stock_out": stock_out,
                "stock_sale": stock_sale,
                "closing_stock": closing,
            }
        )
    return rows


def _transfer_direction(movement, shop_ids):
    """In/out relative to the shops in the current filter."""
    from .models import StockMovementType

    if movement.movement_type != StockMovementType.REQUEST or not shop_ids:
        return ""
    shop_set = set(shop_ids)
    dest_match = movement.shop_id in shop_set
    source_match = (
        movement.requested_from_shop_id in shop_set
        if movement.requested_from_shop_id
        else False
    )
    if dest_match and source_match:
        return "both"
    if dest_match:
        return "in"
    if source_match:
        return "out"
    return ""


def _transfer_event_label(*, event_type, direction):
    if direction == "in":
        base = "Transfer in"
    elif direction == "out":
        base = "Transfer out"
    else:
        base = "Transfer"
    if event_type == "request":
        return f"{base} (requested)"
    return base


def _request_transfer_counts_toward_units(movement) -> bool:
    """Fulfilled requests are counted when stock moves (responded_at), not when submitted."""
    from .models import StockRequestStatus

    return movement.request_status != StockRequestStatus.FULFILLED


def _timeline_event_from_movement_line(
    *,
    movement,
    line,
    happened_at,
    event_type,
    event_label,
    actor,
    counts_toward_transfer=True,
    transfer_direction="",
):
    parties = _movement_parties_for_line(movement=movement, line=line)
    from .models import StockMovementType

    return {
        "happened_at": happened_at,
        "event_type": event_type,
        "event_label": event_label,
        "shop_name": movement.shop.name if movement.shop else "—",
        "from_shop_name": (
            movement.requested_from_shop.name
            if movement.movement_type == StockMovementType.REQUEST
            and movement.requested_from_shop
            else ""
        ),
        "item_name": line.item.name,
        "item_category": line.item.category,
        "item_id": line.item_id,
        "quantity": line.quantity,
        "reason": line.get_reason_display() if line.reason else "",
        "payment_status": (
            line.get_payment_status_display() if line.payment_status else ""
        ),
        "note": line.note or "",
        "by": _employee_display_name(actor),
        "serial_numbers": _movement_serial_numbers(line.serial_numbers),
        "movement_id": movement.pk,
        "counts_toward_transfer": counts_toward_transfer,
        "transfer_direction": transfer_direction,
        **parties,
    }


def _build_movement_timeline(
    *,
    shop_ids,
    day_start,
    day_end,
    item_mode,
    selected_categories,
    selected_item_ids,
    report_items,
):
    """
    Chronological stock events for the filtered period (oldest first).
    Stock in, stock out, and request come from movements; sales are separate events.
    Accepted stock requests also appear when stock moves (responded_at), not only when submitted.
    """
    from django.db.models import Prefetch, Q

    from .models import (
        StockMovement,
        StockMovementLine,
        StockMovementType,
        StockRequestStatus,
    )

    events = []
    units_in = 0
    units_out = 0
    units_request = 0
    units_sale = 0

    if not shop_ids:
        return events, units_in, units_out, units_request, units_sale

    line_qs = StockMovementLine.objects.select_related("item").order_by("id")
    movement_filter = Q(
        created_at__gte=day_start,
        created_at__lt=day_end,
        shop_id__in=shop_ids,
    )

    if item_mode == "category" and selected_categories:
        movement_filter &= Q(lines__item__category__in=selected_categories)
        line_qs = line_qs.filter(item__category__in=selected_categories)
    elif item_mode == "items" and selected_item_ids:
        movement_filter &= Q(lines__item_id__in=selected_item_ids)
        line_qs = line_qs.filter(item_id__in=selected_item_ids)

    movements = (
        StockMovement.objects.filter(movement_filter)
        .distinct()
        .select_related(
            "shop",
            "requested_from_shop",
            "created_by__user",
        )
        .prefetch_related(Prefetch("lines", queryset=line_qs))
        .order_by("created_at", "pk")
    )

    type_labels = {
        StockMovementType.IN: "Stock in",
        StockMovementType.OUT: "Stock out",
        StockMovementType.REQUEST: "Stock request",
    }

    for movement in movements:
        for line in movement.lines.all():
            counts_toward_transfer = False
            transfer_direction = ""
            if movement.movement_type == StockMovementType.REQUEST:
                counts_toward_transfer = _request_transfer_counts_toward_units(
                    movement
                )
                transfer_direction = _transfer_direction(movement, shop_ids)
            event_type = movement.movement_type
            event_label = type_labels.get(
                movement.movement_type, movement.get_movement_type_display()
            )
            if transfer_direction:
                event_label = _transfer_event_label(
                    event_type=event_type,
                    direction=transfer_direction,
                )
            events.append(
                _timeline_event_from_movement_line(
                    movement=movement,
                    line=line,
                    happened_at=movement.created_at,
                    event_type=event_type,
                    event_label=event_label,
                    actor=movement.created_by,
                    counts_toward_transfer=counts_toward_transfer,
                    transfer_direction=transfer_direction,
                )
            )
            if movement.movement_type == StockMovementType.IN:
                units_in += line.quantity
            elif movement.movement_type == StockMovementType.OUT:
                units_out += line.quantity
            elif (
                movement.movement_type == StockMovementType.REQUEST
                and _request_transfer_counts_toward_units(movement)
            ):
                units_request += line.quantity

    fulfilled_line_qs = StockMovementLine.objects.select_related("item").order_by("id")
    fulfilled_filter = Q(
        movement_type=StockMovementType.REQUEST,
        request_status=StockRequestStatus.FULFILLED,
        responded_at__gte=day_start,
        responded_at__lt=day_end,
    ) & (Q(shop_id__in=shop_ids) | Q(requested_from_shop_id__in=shop_ids))

    if item_mode == "category" and selected_categories:
        fulfilled_filter &= Q(lines__item__category__in=selected_categories)
        fulfilled_line_qs = fulfilled_line_qs.filter(
            item__category__in=selected_categories
        )
    elif item_mode == "items" and selected_item_ids:
        fulfilled_filter &= Q(lines__item_id__in=selected_item_ids)
        fulfilled_line_qs = fulfilled_line_qs.filter(item_id__in=selected_item_ids)

    fulfilled_movements = (
        StockMovement.objects.filter(fulfilled_filter)
        .distinct()
        .select_related(
            "shop",
            "requested_from_shop",
            "responded_by__user",
        )
        .prefetch_related(Prefetch("lines", queryset=fulfilled_line_qs))
        .order_by("responded_at", "pk")
    )

    for movement in fulfilled_movements:
        for line in movement.lines.all():
            if line.quantity <= 0:
                continue
            transfer_direction = _transfer_direction(movement, shop_ids)
            event_label = _transfer_event_label(
                event_type="transfer_fulfilled",
                direction=transfer_direction,
            )
            events.append(
                _timeline_event_from_movement_line(
                    movement=movement,
                    line=line,
                    happened_at=movement.responded_at,
                    event_type="transfer_fulfilled",
                    event_label=event_label,
                    actor=movement.responded_by,
                    transfer_direction=transfer_direction,
                )
            )
            units_request += line.quantity

    # Sales as separate timeline events (never mixed into stock out).
    from pos.models import SaleLine
    from shops.models import ShopReceiptKind, ShopReceiptLine

    item_name_set = {(item.name or "").strip().lower() for item in report_items}
    item_by_name = {
        (item.name or "").strip().lower(): item for item in report_items
    }
    item_by_id = {item.pk: item for item in report_items}

    receipt_lines = (
        ShopReceiptLine.objects.filter(
            receipt__created_at__gte=day_start,
            receipt__created_at__lt=day_end,
            receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
            receipt__shop_id__in=shop_ids,
        )
        .select_related(
            "receipt",
            "receipt__shop",
            "receipt__created_by__user",
            "receipt__client",
            "item",
        )
        .order_by("receipt__created_at", "id")
    )
    if item_mode == "category" and selected_categories:
        receipt_lines = receipt_lines.filter(item__category__in=selected_categories)
    elif item_mode == "items" and selected_item_ids:
        receipt_lines = receipt_lines.filter(item_id__in=selected_item_ids)

    for line in receipt_lines.iterator(chunk_size=500):
        matched = item_by_id.get(line.item_id) or item_by_name.get(
            (line.item_name or "").strip().lower()
        )
        parties = _movement_parties_for_receipt(receipt=line.receipt)
        events.append(
            {
                "happened_at": line.receipt.created_at,
                "event_type": "sale",
                "event_label": "Stock sale",
                "shop_name": (
                    line.receipt.shop.name if line.receipt.shop_id else "—"
                ),
                "from_shop_name": "",
                "item_name": line.item_name or (matched.name if matched else "—"),
                "item_category": (
                    matched.category
                    if matched
                    else (line.item.category if line.item_id and line.item else "")
                ),
                "item_id": line.item_id or (matched.pk if matched else None),
                "quantity": line.quantity,
                "reason": "",
                "payment_status": parties["pay"] if parties["pay"] != "—" else "",
                "note": "",
                "by": _employee_display_name(line.receipt.created_by),
                "serial_numbers": _movement_serial_numbers(line.serial_numbers),
                "movement_id": None,
                **parties,
            }
        )
        units_sale += line.quantity

    if item_mode == "category" and selected_categories:
        allowed_names = {
            (item.name or "").strip().lower()
            for item in report_items
            if item.category in selected_categories
        }
    elif item_mode == "items" and selected_item_ids:
        selected_set = set(selected_item_ids)
        allowed_names = {
            (item.name or "").strip().lower()
            for item in report_items
            if item.pk in selected_set
        }
    else:
        allowed_names = item_name_set

    sale_lines = (
        SaleLine.objects.filter(
            sale__sold_at__gte=day_start,
            sale__sold_at__lt=day_end,
        )
        .select_related("sale", "sale__employee__user")
        .order_by("sale__sold_at", "id")
    )
    if shop_ids:
        sale_lines = sale_lines.filter(
            sale__employee__assigned_shops__in=shop_ids
        ).distinct()
    if allowed_names:
        sale_lines = sale_lines.filter(
            product_name__in=[
                (item.name or "").strip()
                for item in report_items
                if (item.name or "").strip().lower() in allowed_names
            ]
        )

    for line in sale_lines.iterator(chunk_size=500):
        key = (line.product_name or "").strip().lower()
        if allowed_names and key not in allowed_names:
            continue
        matched = item_by_name.get(key)
        parties = _movement_parties_for_pos_sale(sale=line.sale)
        events.append(
            {
                "happened_at": line.sale.sold_at,
                "event_type": "sale",
                "event_label": "Stock sale",
                "shop_name": "—",
                "from_shop_name": "",
                "item_name": line.product_name or (matched.name if matched else "—"),
                "item_category": matched.category if matched else "",
                "item_id": matched.pk if matched else None,
                "quantity": line.quantity,
                "reason": "",
                "payment_status": "",
                "note": "",
                "by": _employee_display_name(line.sale.employee),
                "serial_numbers": [],
                "movement_id": None,
                **parties,
            }
        )
        units_sale += line.quantity

    events.sort(key=lambda row: (row["happened_at"], row.get("movement_id") or 0))
    return events, units_in, units_out, units_request, units_sale


def _movement_serial_numbers(raw) -> list[str]:
    seen = set()
    serials = []
    for value in raw or []:
        serial = str(value or "").strip().upper()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        serials.append(serial)
    return serials


def _movement_supplier_label(line):
    name = (getattr(line, "supplier_name", None) or "").strip()
    return name or "—"


def _movement_pay_label(*, line=None, movement=None, receipt=None):
    if receipt is not None:
        method = (getattr(receipt, "payment_method", None) or "").strip()
        if method:
            return receipt.get_payment_method_display()
        return "—"
    if line is not None:
        payment = (getattr(line, "payment_status", None) or "").strip()
        if payment:
            return line.get_payment_status_display()
        refund = (getattr(line, "refund", None) or "").strip().lower()
        if refund == "yes":
            amount = getattr(line, "refund_amount", None)
            if amount is not None:
                return f"Refund {amount}"
            return "Refund"
        if refund == "no":
            return "No refund"
    if movement is not None:
        payment = (getattr(movement, "payment_status", None) or "").strip()
        if payment:
            return movement.get_payment_status_display()
    return "—"


def _movement_parties_for_line(*, movement, line):
    from .models import StockMovementType

    shop_name = movement.shop.name if movement.shop else "—"
    supplier = _movement_supplier_label(line)

    if movement.movement_type == StockMovementType.IN:
        return {
            "from_label": supplier,
            "to_label": shop_name,
            "seller": supplier,
            "pay": _movement_pay_label(line=line, movement=movement),
        }
    if movement.movement_type == StockMovementType.OUT:
        reason = line.get_reason_display() if line.reason else "—"
        return {
            "from_label": shop_name,
            "to_label": reason,
            "seller": "—",
            "pay": _movement_pay_label(line=line),
        }
    from_shop = (
        movement.requested_from_shop.name if movement.requested_from_shop else "—"
    )
    return {
        "from_label": from_shop,
        "to_label": shop_name,
        "seller": "—",
        "pay": "—",
    }


def _movement_parties_for_receipt(*, receipt):
    shop_name = receipt.shop.name if receipt.shop_id else "—"
    client_name = (receipt.client_name or "").strip()
    if not client_name and receipt.client_id and receipt.client:
        client_name = (receipt.client.full_name or "").strip()
    return {
        "from_label": shop_name,
        "to_label": client_name or "—",
        "seller": _employee_display_name(receipt.created_by),
        "pay": _movement_pay_label(receipt=receipt),
    }


def _movement_parties_for_pos_sale(*, sale):
    return {
        "from_label": "—",
        "to_label": "—",
        "seller": _employee_display_name(sale.employee),
        "pay": "—",
    }


MOVEMENT_EVENT_FILTERS = frozenset({"all", "in", "out", "sale", "transfer"})

MOVEMENT_EVENT_FILTER_TYPES = {
    "in": frozenset({"in"}),
    "out": frozenset({"out"}),
    "sale": frozenset({"sale"}),
    "transfer": frozenset({"request", "transfer_fulfilled"}),
}


def _parse_movement_event_filter(raw):
    event_filter = (raw or "all").strip().lower()
    if event_filter not in MOVEMENT_EVENT_FILTERS:
        return "all"
    return event_filter


def _filter_movement_events(events, event_filter):
    allowed = MOVEMENT_EVENT_FILTER_TYPES.get(event_filter)
    if not allowed:
        return events
    return [event for event in events if event.get("event_type") in allowed]


def _filter_timeline_display_events(events):
    """Timeline rows: show fulfilled transfer-in only (no pending requests or transfer-out)."""
    kept = []
    for event in events:
        event_type = event.get("event_type")
        if event_type == "request":
            continue
        if event_type == "transfer_fulfilled" and event.get(
            "transfer_direction"
        ) not in ("in", "both"):
            continue
        kept.append(event)
    return kept


def _summarize_movement_events(events):
    units_in = sum(
        event["quantity"] for event in events if event.get("event_type") == "in"
    )
    units_out = sum(
        event["quantity"] for event in events if event.get("event_type") == "out"
    )
    units_transfer_in = sum(
        event["quantity"]
        for event in events
        if event.get("event_type") in ("request", "transfer_fulfilled")
        and event.get("transfer_direction") in ("in", "both")
    )
    units_transfer_out = sum(
        event["quantity"]
        for event in events
        if event.get("event_type") in ("request", "transfer_fulfilled")
        and event.get("transfer_direction") in ("out", "both")
    )
    units_request = units_transfer_in + units_transfer_out
    units_sale = sum(
        event["quantity"] for event in events if event.get("event_type") == "sale"
    )
    return units_in, units_out, units_request, units_sale, units_transfer_in, units_transfer_out


MOVEMENT_VIEW_BY = frozenset({"timeline", "item"})


def _parse_movement_view_by(raw):
    view_by = (raw or "item").strip().lower()
    if view_by not in MOVEMENT_VIEW_BY:
        return "item"
    return view_by


def _group_movement_events_by_item(events, shop_ids):
    groups = {}
    for event in events:
        item_id = event.get("item_id")
        item_name = event.get("item_name") or "—"
        item_category = event.get("item_category") or ""
        key = item_id if item_id is not None else f"name:{item_name.strip().lower()}"

        row = groups.get(key)
        if row is None:
            row = {
                "item_id": item_id,
                "item_name": item_name,
                "item_category": item_category,
                "event_count": 0,
                "units_in": 0,
                "units_out": 0,
                "units_transfer_in": 0,
                "units_transfer_out": 0,
                "units_sale": 0,
                "current_stock": 0,
                "last_at": event["happened_at"],
            }
            groups[key] = row

        row["event_count"] += 1
        event_type = event.get("event_type")
        quantity = int(event.get("quantity") or 0)
        if event_type == "in":
            row["units_in"] += quantity
        elif event_type == "out":
            row["units_out"] += quantity
        elif event_type in ("request", "transfer_fulfilled"):
            direction = event.get("transfer_direction")
            if direction in ("in", "both"):
                row["units_transfer_in"] += quantity
            if direction in ("out", "both"):
                row["units_transfer_out"] += quantity
        elif event_type == "sale":
            row["units_sale"] += quantity
        if event["happened_at"] > row["last_at"]:
            row["last_at"] = event["happened_at"]

    item_ids = [row["item_id"] for row in groups.values() if row.get("item_id")]
    stock_by_item = _current_stock_by_item(item_ids, shop_ids)
    for row in groups.values():
        item_id = row.get("item_id")
        if item_id:
            row["current_stock"] = stock_by_item.get(item_id, 0)

    return sorted(
        groups.values(),
        key=lambda row: (row["item_name"].lower(), row.get("item_id") or 0),
    )


def _movements_report_params(
    *,
    range_type,
    filter_context,
    item_mode,
    event_filter,
    view_by,
    selected_shop_ids,
    selected_categories=None,
    selected_item_ids=None,
    **overrides,
):
    params = {
        "range": range_type,
        "item_mode": item_mode or "all",
        "event_type": event_filter,
        "view_by": view_by,
    }
    if selected_shop_ids:
        params["shop_id"] = selected_shop_ids[0]
    if range_type == "day":
        params["date"] = filter_context["report_date_value"]
    elif range_type == "period":
        params["date_from"] = filter_context["report_date_from"]
        params["date_to"] = filter_context["report_date_to"]
    elif range_type == "month":
        params["month"] = filter_context["report_month_value"]
    elif range_type == "year":
        params["year"] = filter_context["report_year_value"][:4]
    if item_mode == "category" and selected_categories:
        params["category"] = selected_categories[0]
    if item_mode == "items" and selected_item_ids:
        params["item_id"] = selected_item_ids[0]
    params.update(overrides)
    return {
        key: value
        for key, value in params.items()
        if value not in (None, "")
    }


def stock_report(request, profile, meta, module, *, page_mode="report"):
    from employees.models import SHOP_ASSIGNABLE_ROLES

    if page_mode not in ("report", "movements"):
        page_mode = "report"

    range_type, day_start, day_end, filter_context = _report_range_bounds(request)

    filter_shops = actionable_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    selected_shop_ids = [
        pk for pk in _parse_id_list(request.GET.getlist("shop_id")) if pk in shops_by_id
    ]
    active_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]

    item_mode = (request.GET.get("item_mode") or "all").strip().lower()
    if item_mode not in ("all", "category", "items"):
        item_mode = "all"

    categories = list(
        Item.objects.order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )
    selected_categories = [
        value.strip()
        for value in request.GET.getlist("category")
        if (value or "").strip() and (value or "").strip() in set(categories)
    ]

    all_items = list(
        Item.objects.order_by("category", "name").only("id", "name", "category")
    )
    items_by_id = {item.pk: item for item in all_items}
    selected_item_ids = [
        pk for pk in _parse_id_list(request.GET.getlist("item_id")) if pk in items_by_id
    ]

    # Incomplete category/item picks fall back to All (the default).
    if item_mode == "category" and not selected_categories:
        item_mode = "all"
    elif item_mode == "items" and not selected_item_ids:
        item_mode = "all"

    item_qs = Item.objects.order_by("category", "name")
    if item_mode == "category":
        item_qs = item_qs.filter(category__in=selected_categories)
    elif item_mode == "items":
        item_qs = item_qs.filter(pk__in=selected_item_ids)

    report_items = list(item_qs.only("id", "name", "category", "is_suspended"))
    filter_items_json = json.dumps(
        [
            {"id": item.pk, "name": item.name, "category": item.category}
            for item in all_items
        ]
    )
    selected_filter_items = [items_by_id[pk] for pk in selected_item_ids if pk in items_by_id]

    no_shop_access = (
        profile.role in SHOP_ASSIGNABLE_ROLES and not filter_shops
    )
    shop_ids_for_query = [] if no_shop_access else active_shop_ids

    movement_events = []
    units_in = 0
    units_out = 0
    units_request = 0
    units_transfer_in = 0
    units_transfer_out = 0
    units_sale = 0
    item_report_rows = []
    totals = {
        "starting_stock": 0,
        "stock_request": 0,
        "stock_in": 0,
        "stock_out": 0,
        "stock_sale": 0,
        "closing_stock": 0,
    }

    is_movements = page_mode == "movements"
    event_filter = _parse_movement_event_filter(
        request.GET.get("event_type") if is_movements else "all"
    )
    view_by = _parse_movement_view_by(
        request.GET.get("view_by") if is_movements else "timeline"
    )
    is_item_movement_detail = (
        is_movements
        and view_by == "timeline"
        and item_mode == "items"
        and len(selected_item_ids) == 1
    )
    is_item_movement_summary = (
        is_movements and view_by == "item" and not is_item_movement_detail
    )
    movement_item_rows = []

    if is_movements:
        (
            movement_events,
            units_in,
            units_out,
            units_request,
            units_sale,
        ) = _build_movement_timeline(
            shop_ids=shop_ids_for_query,
            day_start=day_start,
            day_end=day_end,
            item_mode=item_mode,
            selected_categories=selected_categories,
            selected_item_ids=selected_item_ids,
            report_items=report_items if item_mode != "all" else all_items,
        )
        if event_filter != "all":
            movement_events = _filter_movement_events(movement_events, event_filter)
        if view_by == "timeline":
            movement_events = _filter_timeline_display_events(movement_events)
        (
            units_in,
            units_out,
            units_request,
            units_sale,
            units_transfer_in,
            units_transfer_out,
        ) = _summarize_movement_events(movement_events)
        if is_item_movement_summary:
            movement_item_rows = _group_movement_events_by_item(
                movement_events, shop_ids_for_query
            )
    else:
        item_report_rows = _build_item_report_rows(
            report_items, shop_ids_for_query, day_start, day_end
        )
        for row in item_report_rows:
            totals["starting_stock"] += row["starting_stock"]
            totals["stock_request"] += row["stock_request"]
            totals["stock_in"] += row["stock_in"]
            totals["stock_out"] += row["stock_out"]
            totals["stock_sale"] += row["stock_sale"]
            totals["closing_stock"] += row["closing_stock"]
        units_in = totals["stock_in"]
        units_out = totals["stock_out"]
        units_request = totals["stock_request"]
        units_sale = totals["stock_sale"]

    from employees.workspace import sidebar_for_stock_management, stock_management_url

    report_params = _movements_report_params(
        range_type=range_type,
        filter_context=filter_context,
        item_mode=item_mode,
        event_filter=event_filter,
        view_by=view_by if is_movements else "timeline",
        selected_shop_ids=selected_shop_ids,
        selected_categories=selected_categories,
        selected_item_ids=selected_item_ids,
    )
    movements_back_url = ""
    if is_item_movement_detail:
        back_params = _movements_report_params(
            range_type=range_type,
            filter_context=filter_context,
            item_mode="all",
            event_filter=event_filter,
            view_by="item",
            selected_shop_ids=selected_shop_ids,
            selected_categories=selected_categories,
            selected_item_ids=[],
        )
        movements_back_url = stock_management_url(
            profile.role, "movements", report_params=back_params
        )
    elif is_item_movement_summary:
        for row in movement_item_rows:
            if not row.get("item_id"):
                row["detail_url"] = ""
                continue
            detail_params = _movements_report_params(
                range_type=range_type,
                filter_context=filter_context,
                item_mode="items",
                event_filter=event_filter,
                view_by="timeline",
                selected_shop_ids=selected_shop_ids,
                selected_categories=selected_categories,
                selected_item_ids=[row["item_id"]],
                item_id=row["item_id"],
            )
            row["detail_url"] = stock_management_url(
                profile.role, "movements", report_params=detail_params
            )

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode=page_mode,
        report_params=report_params,
        profile=profile,
    )

    range_labels = {
        "day": "Single day",
        "period": "Period",
        "month": "Month",
        "year": "Year",
    }

    return render(
        request,
        "items/stock_report.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "stock_mode": page_mode,
            "is_movements_page": is_movements,
            "movement_events": movement_events,
            "item_report_rows": item_report_rows,
            "item_report_totals": totals,
            "movement_count": len(movement_events),
            "item_count": len(item_report_rows),
            "units_in": units_in,
            "units_out": units_out,
            "units_request": units_request,
            "units_transfer_in": units_transfer_in,
            "units_transfer_out": units_transfer_out,
            "units_sale": units_sale,
            "report_range_label": range_labels[range_type],
            "filter_shops": filter_shops,
            "selected_shop_ids": set(selected_shop_ids),
            "item_mode": item_mode,
            "categories": categories,
            "selected_categories": set(selected_categories),
            "filter_items_json": filter_items_json,
            "selected_filter_items": selected_filter_items,
            "selected_item_ids": set(selected_item_ids),
            "event_filter": event_filter,
            "view_by": view_by,
            "is_item_movement_summary": is_item_movement_summary,
            "is_item_movement_detail": is_item_movement_detail,
            "movement_item_rows": movement_item_rows,
            "movement_item_count": len(movement_item_rows),
            "detail_item": (
                items_by_id.get(selected_item_ids[0])
                if is_item_movement_detail
                else None
            ),
            "movements_back_url": movements_back_url,
            **filter_context,
        },
    )


def stock_low_stock_settings(request, profile, meta, module):
    """Per-item low stock notification thresholds."""
    from django.db.models import Sum

    from employees.models import EmployeeRole
    from employees.workspace import sidebar_for_stock_management, stock_management_url

    from .models import Item, ShopStock

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="low-stock",
        profile=profile,
    )
    can_edit = profile.role in (
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.IT_SUPPORT,
    )
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "").lower()
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (request.POST.get("ajax") or "") == "1"
    )

    if request.method == "POST":
        if not can_edit:
            if wants_json:
                return JsonResponse(
                    {"ok": False, "error": "You cannot change low stock settings."},
                    status=403,
                )
            messages.error(request, "You cannot change low stock settings.")
            return redirect(stock_management_url(profile.role, "low-stock"))

        action = (request.POST.get("action") or "").strip()
        if action == "save_low_stock":
            try:
                item_id = int((request.POST.get("item_id") or "").strip())
            except (TypeError, ValueError):
                item_id = 0
            item = Item.objects.filter(pk=item_id).first()
            if item is None:
                if wants_json:
                    return JsonResponse(
                        {"ok": False, "error": "Item not found."}, status=404
                    )
                messages.error(request, "Item not found.")
                return redirect(stock_management_url(profile.role, "low-stock"))

            notify = (request.POST.get("notify") or "").strip() in (
                "1",
                "true",
                "True",
                "on",
                "yes",
            )
            raw_threshold = (request.POST.get("threshold") or "").strip()
            try:
                threshold = int(raw_threshold or 0)
            except (TypeError, ValueError):
                if wants_json:
                    return JsonResponse(
                        {"ok": False, "error": "Threshold must be a whole number."},
                        status=400,
                    )
                messages.error(request, "Threshold must be a whole number.")
                return redirect(stock_management_url(profile.role, "low-stock"))
            if threshold < 0:
                threshold = 0
            if notify and threshold < 1:
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": "Set a threshold of at least 1 to enable alerts.",
                        },
                        status=400,
                    )
                messages.error(
                    request, "Set a threshold of at least 1 to enable alerts."
                )
                return redirect(stock_management_url(profile.role, "low-stock"))

            item.low_stock_notify = notify
            item.low_stock_threshold = threshold
            item.save(
                update_fields=["low_stock_notify", "low_stock_threshold", "updated_at"]
            )
            total_units = (
                ShopStock.objects.filter(item=item).aggregate(total=Sum("quantity"))[
                    "total"
                ]
                or 0
            )
            is_low = bool(notify and total_units <= threshold)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "item_id": item.pk,
                        "notify": item.low_stock_notify,
                        "threshold": item.low_stock_threshold,
                        "total_units": int(total_units),
                        "is_low": is_low,
                    }
                )
            messages.success(request, f"Low stock settings saved for {item.name}.")
            return redirect(stock_management_url(profile.role, "low-stock"))

        if wants_json:
            return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect(stock_management_url(profile.role, "low-stock"))

    stock_totals = {
        row["item_id"]: int(row["total"] or 0)
        for row in ShopStock.objects.values("item_id").annotate(total=Sum("quantity"))
    }
    low_stock_items = []
    notify_count = 0
    for item in Item.objects.order_by("category", "name"):
        total_units = stock_totals.get(item.pk, 0)
        notify = bool(item.low_stock_notify)
        threshold = int(item.low_stock_threshold or 0)
        if notify:
            notify_count += 1
        low_stock_items.append(
            {
                "item": item,
                "total_units": total_units,
                "notify": notify,
                "threshold": threshold,
                "is_low": bool(notify and total_units <= threshold),
            }
        )

    return render(
        request,
        "items/stock_low_stock_settings.html",
        {
            "page_meta": meta,
            "page_module": module,
            "page_sidebar": page_sidebar,
            "stock_mode": "low-stock",
            "can_edit_low_stock": can_edit,
            "low_stock_items": low_stock_items,
            "low_stock_item_count": len(low_stock_items),
            "low_stock_notify_count": notify_count,
            "stock_settings_url": stock_management_url(profile.role, "settings"),
        },
    )


def stock_settings(request, profile, meta, module):
    """Configure which stock in/out/request fields are compulsory."""
    from employees.models import EmployeeRole
    from employees.workspace import sidebar_for_stock_management, stock_management_url
    from shops.services import (
        get_company_stock_settings,
        set_company_stock_setting,
        stock_settings_as_dict,
    )

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="settings",
        profile=profile,
    )
    can_edit = profile.role in (
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.IT_SUPPORT,
    )
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "").lower()
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (request.POST.get("ajax") or "") == "1"
    )

    if request.method == "POST":
        if not can_edit:
            if wants_json:
                return JsonResponse(
                    {"ok": False, "error": "You cannot change stock settings."},
                    status=403,
                )
            messages.error(request, "You cannot change stock settings.")
            return redirect(stock_management_url(profile.role, "settings"))

        action = (request.POST.get("action") or "").strip()
        if action == "toggle_stock_setting":
            field = (request.POST.get("field") or "").strip()
            enabled = (request.POST.get("enabled") or "").strip() in (
                "1",
                "true",
                "True",
                "on",
                "yes",
            )
            try:
                row = set_company_stock_setting(field=field, enabled=enabled)
            except ValidationError as exc:
                message = (
                    exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                )
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(stock_management_url(profile.role, "settings"))
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "field": field,
                        "enabled": bool(getattr(row, field)),
                        "settings": stock_settings_as_dict(row),
                    }
                )
            messages.success(request, "Stock setting updated.")
            return redirect(stock_management_url(profile.role, "settings"))

        if wants_json:
            return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect(stock_management_url(profile.role, "settings"))

    settings_row = get_company_stock_settings()
    setting_groups = (
        {
            "key": "in",
            "title": "Stock In",
            "summary": "Buying stock into a shop",
            "icon": "package-plus",
            "open_url": stock_management_url(profile.role, "in"),
            "open_label": "Open Stock In",
            "always_required": (
                (
                    "Quantity / serials",
                    "At least one line with qty, or serials when tracked",
                ),
                ("Shop", "Which shop receives the stock"),
            ),
            "toggles": (
                {
                    "field": "require_buying_price_on_in",
                    "label": "Unit buying price",
                    "hint": "Cost of one unit (not the invoice total) on every stocked line",
                    "enabled": settings_row.require_buying_price_on_in,
                },
                {
                    "field": "require_supplier_on_in",
                    "label": "Supplier phone & name",
                    "hint": "Country dial + 9-digit phone and supplier name",
                    "enabled": settings_row.require_supplier_on_in,
                },
                {
                    "field": "require_payment_status_on_in",
                    "label": "Payment status",
                    "hint": "Unpaid, paid, or partial",
                    "enabled": settings_row.require_payment_status_on_in,
                },
            ),
        },
        {
            "key": "out",
            "title": "Stock Out",
            "summary": "Removing stock from a shop",
            "icon": "package-minus",
            "open_url": stock_management_url(profile.role, "out"),
            "open_label": "Open Stock Out",
            "always_required": (
                (
                    "Quantity / serials",
                    "Units to remove; serials when the item tracks them",
                ),
                ("Shop", "Which shop the stock leaves from"),
            ),
            "toggles": (
                {
                    "field": "require_reason_on_out",
                    "label": "Reason",
                    "hint": "Waste, transfer, display, or return",
                    "enabled": settings_row.require_reason_on_out,
                },
                {
                    "field": "require_refund_on_out",
                    "label": "Refund details",
                    "hint": "Yes/no, and amount when refund is yes",
                    "enabled": settings_row.require_refund_on_out,
                },
            ),
        },
        {
            "key": "request",
            "title": "Request Stock",
            "summary": "Asking another shop for stock",
            "icon": "clipboard-list",
            "open_url": stock_management_url(profile.role, "request"),
            "open_label": "Open Request",
            "always_required": (
                (
                    "Quantity",
                    "At least one line with quantity greater than zero",
                ),
                ("Requesting shop", "Who is requesting"),
                (
                    "From shop",
                    "Shop(s) you are requesting from (must differ)",
                ),
            ),
            "toggles": (),
        },
    )
    enabled_count = sum(
        1
        for group in setting_groups
        for toggle in group["toggles"]
        if toggle["enabled"]
    )
    toggle_count = sum(len(group["toggles"]) for group in setting_groups)

    return render(
        request,
        "items/stock_settings.html",
        {
            "page_meta": meta,
            "page_module": module,
            "page_sidebar": page_sidebar,
            "stock_mode": "settings",
            "can_edit_stock_settings": can_edit,
            "stock_setting_groups": setting_groups,
            "stock_settings_enabled_count": enabled_count,
            "stock_settings_toggle_count": toggle_count,
            "stock_requirements_json": json.dumps(settings_row.as_requirements_dict()),
            "low_stock_settings_url": stock_management_url(profile.role, "low-stock"),
        },
    )


@require_http_methods(["GET", "POST"])
def stock_management(request, profile, meta, module, page_sidebar):
    from employees.models import EmployeeRole
    from employees.module_permissions import require_module_permission
    from shops.models import Shop

    from .models import ShopStock

    mode = (request.GET.get("mode") or request.POST.get("mode") or "view").strip().lower()
    if mode not in (
        "view",
        "in",
        "out",
        "request",
        "report",
        "movements",
        "serials",
        "serial-movements",
        "return-clients",
        "settings",
        "low-stock",
    ):
        mode = "view"

    # Return-clients / serial-movements share the serials permission key.
    # Settings / low-stock are reference pages — anyone who can view stock may open them.
    permission_mode = (
        "serials"
        if mode in ("return-clients", "serial-movements")
        else "view"
        if mode in ("settings", "low-stock")
        else mode
    )
    denied = require_module_permission(
        request, profile, "stock-management", permission_mode
    )
    if denied is not None:
        return denied

    # Stock In / Out / Request / Report / Movements / Serials: shop-manager and IT support.
    if mode not in ("view", "settings", "low-stock") and profile.role not in (
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.IT_SUPPORT,
    ):
        return _stock_redirect(request.path, "view")

    if mode == "settings":
        return stock_settings(request, profile, meta, module)

    if mode == "low-stock":
        return stock_low_stock_settings(request, profile, meta, module)

    if mode in ("report", "movements"):
        return stock_report(request, profile, meta, module, page_mode=mode)

    if mode == "serials":
        return stock_serials(request, profile, meta, module)

    if mode == "serial-movements":
        return stock_serial_movements(request, profile, meta, module)

    if mode == "return-clients":
        return stock_serial_returns(request, profile, meta, module)

    all_shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )
    action_shops = actionable_shops_for_profile(profile)
    # Current stock: all shops. Stock in / out / request: only shops allocated to the employee.
    shops = all_shops if mode == "view" else action_shops
    shops_by_id = {str(shop.pk): shop for shop in shops}

    def _resolve_shop(raw):
        raw = (raw or "").strip()
        return shops_by_id.get(raw)

    requested_shop_ids = _parse_request_shop_ids(request)
    selected_shops = []
    for raw_id in requested_shop_ids:
        shop = _resolve_shop(raw_id)
        if shop is not None:
            selected_shops.append(shop)
    # View mode keeps a single shop filter.
    if mode == "view":
        selected_shops = selected_shops[:1]
    # Drop unknown / unallocated ids; empty selected_shops means all shops.
    if mode in ("in", "out") and selected_shops and len(selected_shops) >= len(shops):
        # Selecting every allocated shop is the same as "all shops".
        selected_shops = []

    selected_shop = selected_shops[0] if len(selected_shops) == 1 else None
    selected_shop_id = str(selected_shop.pk) if selected_shop else ""
    selected_shop_ids = [shop.pk for shop in selected_shops]
    selected_shop_id_set = set(selected_shop_ids)
    shop_filter_active = mode in ("in", "out") and bool(selected_shops)
    requested_from_id = (
        request.GET.get("requested_from_shop_id")
        or request.POST.get("requested_from_shop_id")
        or ""
    ).strip()

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode=mode,
        shop_ids=selected_shop_ids,
        requested_from_shop_id=requested_from_id if mode == "request" else "",
        profile=profile,
    )

    if request.method == "POST":
        wants_json = _wants_json_response(request)
        action_mode = (request.POST.get("mode") or mode).strip().lower()
        if action_mode not in ("in", "out", "request"):
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "Choose Stock In, Stock Out, or Request Stock first.",
                    },
                    status=400,
                )
            messages.error(request, "Choose Stock In, Stock Out, or Request Stock first.")
            return _stock_redirect(
                request.path, "view", shop_ids=selected_shop_ids[:1]
            )
        denied = require_module_permission(
            request, profile, "stock-management", action_mode, as_json=wants_json
        )
        if denied is not None:
            return denied
        shop_id = (request.POST.get("shop_id") or "").strip()
        requested_from_post = (request.POST.get("requested_from_shop_id") or "").strip()
        redirect_shop_ids = []
        if action_mode == "request":
            if shop_id:
                redirect_shop_ids = [shop_id]
        else:
            filter_csv = (request.POST.get("filter_shop_ids") or "").strip()
            if filter_csv:
                redirect_shop_ids = [
                    part.strip()
                    for part in filter_csv.replace(";", ",").split(",")
                    if part.strip() and part.strip() in shops_by_id
                ]
            elif selected_shop_ids:
                redirect_shop_ids = [str(sid) for sid in selected_shop_ids]
        next_url = _stock_next_url(
            request.path,
            action_mode,
            shop_ids=redirect_shop_ids,
            requested_from_shop_id=requested_from_post,
        )
        try:
            apply_stock_movement(profile, action_mode, request.POST)
        except ValidationError as exc:
            errors = (
                list(exc.messages)
                if hasattr(exc, "messages")
                else [str(exc)]
            )
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": errors[0] if errors else "Could not submit stock.",
                        "errors": errors,
                    },
                    status=400,
                )
            for message in errors:
                messages.error(request, message)
            return _stock_redirect(
                request.path,
                action_mode,
                shop_ids=redirect_shop_ids,
                requested_from_shop_id=requested_from_post,
            )

        labels = {
            "in": "Stock in submitted successfully.",
            "out": "Stock out submitted successfully.",
            "request": "Stock request submitted successfully.",
        }
        success_message = labels[action_mode]
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": success_message,
                    "next": next_url,
                }
            )
        messages.success(request, success_message)
        return _stock_redirect(
            request.path,
            action_mode,
            shop_ids=redirect_shop_ids,
            requested_from_shop_id=requested_from_post,
        )

    if mode == "request":
        # Request mode uses shop_id as the requesting shop (single).
        selected_shop = _resolve_shop(
            requested_shop_ids[0] if requested_shop_ids else ""
        )
        selected_shop_id = str(selected_shop.pk) if selected_shop else ""
        selected_shops = [selected_shop] if selected_shop else []
        selected_shop_ids = [selected_shop.pk] if selected_shop else []
        selected_shop_id_set = set(selected_shop_ids)
        shop_filter_active = False

    requested_from_shop = None
    if mode == "request":
        requested_from_shop = _resolve_shop(requested_from_id)
        if (
            selected_shop
            and requested_from_shop
            and selected_shop.pk == requested_from_shop.pk
        ):
            requested_from_shop = None

    request_pair_ready = bool(
        mode == "request" and selected_shop and requested_from_shop
    )

    # Current stock, stock in/out/request (all shops) use the catalog API.
    use_stock_catalog_api = False
    if mode == "view" and all_shops:
        use_stock_catalog_api = True
    elif mode in ("in", "out") and shops:
        use_stock_catalog_api = True
    elif mode == "request" and request_pair_ready:
        use_stock_catalog_api = True

    items_by_category = []
    item_count = Item.objects.count()
    category_count = 0
    shop_total_units = 0
    display_shops = []
    # View: optional single shop. Stock in/out: optional multi-shop filter.
    show_all_shops = mode == "view" and selected_shop is None and bool(all_shops)
    if mode in ("in", "out") and shops:
        show_all_shops = (not selected_shops and len(shops) > 1) or len(selected_shops) > 1
    elif mode == "request":
        show_all_shops = False

    from django.db.models import Sum

    if mode == "view":
        display_shops = [selected_shop] if selected_shop else all_shops
        if selected_shop:
            shop_total_units = (
                ShopStock.objects.filter(shop=selected_shop).aggregate(
                    total=Sum("quantity")
                )["total"]
                or 0
            )
        elif all_shops:
            shop_total_units = (
                ShopStock.objects.filter(
                    shop_id__in=[shop.pk for shop in all_shops]
                ).aggregate(total=Sum("quantity"))["total"]
                or 0
            )
        category_count = (
            Item.objects.order_by("category").values("category").distinct().count()
        )
    elif mode in ("in", "out") and shops:
        display_shops = list(selected_shops) if selected_shops else list(shops)
        shop_total_units = (
            ShopStock.objects.filter(
                shop_id__in=[shop.pk for shop in display_shops]
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        category_count = (
            Item.objects.order_by("category").values("category").distinct().count()
        )
    elif mode == "request" and request_pair_ready:
        # Only the requesting shop and the shop being asked to supply.
        display_shops = [selected_shop, requested_from_shop]
        shop_total_units = (
            ShopStock.objects.filter(
                shop_id__in=[selected_shop.pk, requested_from_shop.pk]
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        category_count = (
            Item.objects.order_by("category").values("category").distinct().count()
        )
    elif use_stock_catalog_api and selected_shop is not None:
        display_shops = [selected_shop]
        shop_total_units = (
            ShopStock.objects.filter(shop=selected_shop).aggregate(total=Sum("quantity"))[
                "total"
            ]
            or 0
        )
    else:
        # Action mode without required shop selection — empty shell.
        display_shops = list(selected_shops) if selected_shops else (
            [selected_shop] if selected_shop else []
        )

    from employees.access import role_url_segment

    stock_catalog_url = ""
    if use_stock_catalog_api:
        stock_catalog_url = reverse(
            "employees:stock_management_catalog",
            kwargs={"role_segment": role_url_segment(profile.role)},
        )

    import json as _json

    catalog_shops_json = _json.dumps(
        [{"id": shop.pk, "name": shop.name} for shop in display_shops]
    )
    selected_shop_ids_json = _json.dumps(selected_shop_ids)
    selected_shop_ids_csv = ",".join(str(sid) for sid in selected_shop_ids)

    from shops.services import get_company_stock_settings

    stock_requirements_json = _json.dumps(
        get_company_stock_settings().as_requirements_dict()
    )

    if shop_filter_active:
        if len(selected_shops) == 1:
            shop_filter_label = selected_shops[0].name
        elif len(selected_shops) <= 3:
            shop_filter_label = ", ".join(shop.name for shop in selected_shops)
        else:
            shop_filter_label = f"{len(selected_shops)} shops"
    elif mode == "request" and request_pair_ready:
        shop_filter_label = f"{selected_shop.name} ← {requested_from_shop.name}"
    elif mode == "request":
        shop_filter_label = "Choose shops"
    else:
        shop_filter_label = "All shops"

    return render(
        request,
        "items/stock_management.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "items_by_category": items_by_category,
            "shops": shops,
            "all_shops": all_shops,
            "display_shops": display_shops,
            "show_all_shops": show_all_shops,
            "selected_shop": selected_shop,
            "selected_shops": selected_shops,
            "selected_shop_ids": selected_shop_ids,
            "selected_shop_id_set": selected_shop_id_set,
            "selected_shop_ids_csv": selected_shop_ids_csv,
            "selected_shop_ids_json": selected_shop_ids_json,
            "shop_filter_active": shop_filter_active,
            "shop_filter_label": shop_filter_label,
            "requested_from_shop": requested_from_shop,
            "request_pair_ready": request_pair_ready,
            "item_count": item_count,
            "category_count": category_count,
            "total_units": shop_total_units,
            "stock_mode": mode,
            "is_read_only": mode == "view",
            "countries": COUNTRY_DIAL_CODES,
            "supplier_search_url": reverse("employees:supplier_search"),
            "serial_search_url": reverse("employees:serial_search"),
            "serial_check_url": reverse("employees:serial_in_stock_check"),
            "stock_catalog_url": stock_catalog_url,
            "use_stock_catalog_api": use_stock_catalog_api,
            "catalog_shops_json": catalog_shops_json,
            "stock_requirements_json": stock_requirements_json,
            "stock_print_url": reverse(
                "employees:stock_management_print",
                kwargs={"role_segment": role_url_segment(profile.role)},
            ),
        },
    )


@active_employee_required
@require_http_methods(["GET"])
def stock_management_catalog(request, role_segment):
    """Paginated stock-management catalog for view/in/out/request modes."""
    from employees.access import get_profile_for_request, role_url_segment
    from employees.models import EmployeeRole
    from employees.module_permissions import require_module_permission
    from shops.models import Shop

    profile = get_profile_for_request(request)
    if profile is None or not profile.is_active_employee:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if role_url_segment(profile.role) != role_segment:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    mode = (request.GET.get("mode") or "in").strip().lower()
    if mode not in ("in", "out", "request", "view"):
        return JsonResponse({"ok": False, "error": "invalid_mode"}, status=400)

    denied = require_module_permission(
        request, profile, "stock-management", mode, as_json=True
    )
    if denied is not None:
        return denied

    try:
        shop_id = int(request.GET.get("shop_id") or 0)
    except (TypeError, ValueError):
        shop_id = 0
    try:
        from_id = int(request.GET.get("requested_from_shop_id") or 0)
    except (TypeError, ValueError):
        from_id = 0

    requested_shop_ids = []
    for raw in _parse_request_shop_ids(request):
        try:
            requested_shop_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not requested_shop_ids and shop_id:
        requested_shop_ids = [shop_id]

    if mode == "view":
        all_shops = list(
            Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
        )
        shops_by_id = {shop.pk: shop for shop in all_shops}
        view_shop_id = requested_shop_ids[0] if requested_shop_ids else 0
        if view_shop_id and view_shop_id not in shops_by_id:
            return JsonResponse({"ok": False, "error": "shop_required"}, status=400)
        payload = build_stock_catalog_page(
            shop_id=view_shop_id or None,
            shop_ids=None if view_shop_id else [shop.pk for shop in all_shops],
            mode="view",
            q=request.GET.get("q") or "",
            page=request.GET.get("page") or 1,
            page_size=request.GET.get("page_size") or 48,
            include_suspended=True,
        )
        return JsonResponse(payload)

    if profile.role not in (
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.IT_SUPPORT,
    ):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    action_shops = {shop.pk: shop for shop in actionable_shops_for_profile(profile)}
    # Stock in/out: optional multi shop_id filter.
    # Request: only the requesting shop + the shop being asked to supply.
    if mode in ("in", "out", "request"):
        if not action_shops:
            return JsonResponse({"ok": False, "error": "shop_required"}, status=400)
        if mode == "request":
            requesting_id = requested_shop_ids[0] if requested_shop_ids else shop_id
            if not requesting_id or requesting_id not in action_shops:
                return JsonResponse({"ok": False, "error": "shop_required"}, status=400)
            if not from_id or from_id not in action_shops:
                return JsonResponse({"ok": False, "error": "shop_required"}, status=400)
            if from_id == requesting_id:
                return JsonResponse(
                    {"ok": False, "error": "from_shop_must_differ"}, status=400
                )
            catalog_shop_ids = [requesting_id, from_id]
            prefer_shop_id = None
        elif requested_shop_ids:
            catalog_shop_ids = [
                sid for sid in requested_shop_ids if sid in action_shops
            ]
            if not catalog_shop_ids:
                return JsonResponse({"ok": False, "error": "shop_required"}, status=400)
            # Selecting every allocated shop is the same as all shops.
            if len(catalog_shop_ids) >= len(action_shops):
                catalog_shop_ids = list(action_shops.keys())
                prefer_shop_id = None
            else:
                prefer_shop_id = (
                    catalog_shop_ids[0] if len(catalog_shop_ids) == 1 else None
                )
        else:
            catalog_shop_ids = list(action_shops.keys())
            prefer_shop_id = None
        payload = build_stock_catalog_page(
            shop_id=prefer_shop_id,
            shop_ids=catalog_shop_ids,
            mode=mode,
            q=request.GET.get("q") or "",
            page=request.GET.get("page") or 1,
            page_size=request.GET.get("page_size") or 48,
            include_suspended=True,
        )
        return JsonResponse(payload)

    if shop_id not in action_shops:
        return JsonResponse({"ok": False, "error": "shop_required"}, status=400)

    payload = build_stock_catalog_page(
        shop_id=shop_id,
        mode=mode,
        q=request.GET.get("q") or "",
        page=request.GET.get("page") or 1,
        page_size=request.GET.get("page_size") or 48,
        include_suspended=True,
    )
    return JsonResponse(payload)


def _serial_client_info(receipt):
    client = receipt.client
    client_name = ""
    client_phone = ""
    if client is not None:
        client_name = (client.full_name or "").strip()
        client_phone = (client.phone_number or "").strip()
    if not client_name:
        client_name = (receipt.client_name or "").strip()
    if not client_phone:
        client_phone = (receipt.client_phone or "").strip()
    return {
        "client_name": client_name or "Walk-in",
        "client_phone": client_phone,
        "receipt_number": receipt.receipt_number,
        "shop_id": receipt.shop_id,
        "shop_name": receipt.shop.name if receipt.shop_id else "—",
        "kind_label": receipt.get_kind_display(),
    }


def _serial_list_contains(raw, serial_key: str) -> bool:
    return bool(serial_key) and serial_key in _movement_serial_numbers(raw)


def _serial_unit_state(serial, sale_by_serial, return_by_serial):
    from .models import ItemSerialStatus

    key = str(serial.serial_number or "").strip().upper()
    sale = sale_by_serial.get(key) or sale_by_serial.get(serial.serial_number)
    returned = return_by_serial.get(key) or return_by_serial.get(serial.serial_number)
    override = (getattr(serial, "status_override", None) or "").strip().lower()
    if override in ItemSerialStatus.values:
        if override == ItemSerialStatus.SOLD:
            event = sale or returned
        elif override == ItemSerialStatus.RETURNED:
            event = returned or sale
        else:
            event = sale or returned
        return override, ItemSerialStatus(override).label, event
    if sale is not None:
        return "sold", "Sold", sale
    if returned is not None:
        return "returned", "Returned", returned
    if serial.is_available:
        return "in_stock", "In stock", None
    return "out", "Stocked out", None


def _serial_sale_lookup(item):
    """Map serial_number → latest active sale/credit info for an item."""
    from shops.models import ShopReceiptKind, ShopReceiptLine, ShopReceiptStatus

    lines = (
        ShopReceiptLine.objects.filter(
            item=item,
            receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
        )
        .exclude(receipt__status=ShopReceiptStatus.CANCELLED)
        .select_related("receipt", "receipt__client", "receipt__shop")
        .order_by("-receipt__created_at", "-id")
    )
    sale_by_serial = {}
    for line in lines:
        info = {
            **_serial_client_info(line.receipt),
            "sold_at": line.receipt.created_at,
        }
        for serial in line.remaining_serial_numbers:
            key = str(serial).strip().upper()
            if key and key not in sale_by_serial:
                sale_by_serial[key] = info
    return sale_by_serial


def _serial_return_lookup(item):
    """Map serial_number → latest return info (original sale client + when returned)."""
    from shops.models import ShopReceiptKind, ShopReceiptLine

    lines = (
        ShopReceiptLine.objects.filter(
            item=item,
            returned_quantity__gt=0,
            receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
        )
        .select_related("receipt", "receipt__client", "receipt__shop")
        .order_by(
            F("receipt__last_returned_at").desc(nulls_last=True),
            "-receipt__created_at",
            "-id",
        )
    )
    return_by_serial = {}
    for line in lines:
        receipt = line.receipt
        returned_at = receipt.last_returned_at or receipt.created_at
        info = {
            **_serial_client_info(receipt),
            "returned_at": returned_at,
            "sold_at": receipt.created_at,
        }
        for serial in line.returned_serial_numbers or []:
            key = str(serial).strip().upper()
            if key and key not in return_by_serial:
                return_by_serial[key] = info
    return return_by_serial


def stock_serials(request, profile, meta, module):
    """List serial-tracked items with in-stock counts for allocated shops."""
    from django.db.models import Count, Q

    from employees.access import role_url_segment

    from .models import ItemSerial

    search = (request.GET.get("q") or "").strip()

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="serials",
        shop_id="",
        profile=profile,
    )

    display_shops = _serial_shops_for_profile(profile)
    shop_ids = [shop.pk for shop in display_shops]
    shops_label = _serial_shops_label(display_shops)

    items = []
    shop_in_map: dict[int, dict[int, int]] = {}
    if shop_ids:
        items_qs = Item.objects.filter(track_serial_number=True).annotate(
            serial_total=Count(
                "serials",
                filter=Q(serials__shop_id__in=shop_ids),
                distinct=True,
            ),
            serial_in_stock=Count(
                "serials",
                filter=Q(serials__is_available=True, serials__shop_id__in=shop_ids),
                distinct=True,
            ),
            serial_out=Count(
                "serials",
                filter=Q(serials__is_available=False, serials__shop_id__in=shop_ids),
                distinct=True,
            ),
        )
        items_qs = items_qs.filter(serial_total__gt=0).order_by("category", "name")
        if search:
            items_qs = items_qs.filter(
                Q(name__icontains=search) | Q(category__icontains=search)
            )

        items = list(items_qs)
        item_ids = [item.pk for item in items]
        if item_ids:
            for item_id, shop_id, qty in (
                ItemSerial.objects.filter(
                    item_id__in=item_ids,
                    is_available=True,
                    shop_id__in=shop_ids,
                )
                .values("item_id", "shop_id")
                .annotate(qty=Count("id"))
                .values_list("item_id", "shop_id", "qty")
            ):
                shop_in_map.setdefault(item_id, {})[shop_id] = int(qty)

    segment = role_url_segment(profile.role)
    rows = []
    for item in items:
        per_shop = [
            int(shop_in_map.get(item.pk, {}).get(shop.pk, 0)) for shop in display_shops
        ]
        rows.append(
            {
                "item": item,
                "shop_in_stock": per_shop,
                "in_stock": item.serial_in_stock,
                "out": item.serial_out,
                "total": item.serial_total,
                "detail_url": reverse(
                    "employees:stock_serial_detail",
                    kwargs={
                        "role_segment": segment,
                        "item_id": item.pk,
                    },
                ),
            }
        )

    return render(
        request,
        "items/stock_serials.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "rows": rows,
            "item_count": len(rows),
            "search": search,
            "display_shops": display_shops,
            "show_all_shops": len(display_shops) > 1,
            "shops_label": shops_label,
            "selected_shop_id": "",
            "stock_mode": "serials",
        },
    )


def _employee_display_name(profile):
    if profile is None:
        return "—"
    user = getattr(profile, "user", None)
    if user is not None:
        name = (user.get_full_name() or "").strip() or (user.username or "").strip()
        if name:
            return name
    employee_id = (getattr(profile, "employee_id", None) or "").strip()
    return employee_id or "—"


def _returned_serial_line_queryset(*, shop_id="", client_id=None, client_phone=""):
    from shops.models import ShopReceiptKind, ShopReceiptLine
    from shops.services import _normalize_phone

    lines = (
        ShopReceiptLine.objects.filter(
            returned_quantity__gt=0,
            receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
        )
        .select_related(
            "item",
            "receipt",
            "receipt__shop",
            "receipt__client",
            "receipt__created_by",
            "receipt__created_by__user",
            "receipt__last_returned_by",
            "receipt__last_returned_by__user",
        )
        .order_by(
            F("receipt__last_returned_at").desc(nulls_last=True),
            "-receipt__created_at",
            "-id",
        )
    )
    if str(shop_id).isdigit():
        lines = lines.filter(receipt__shop_id=int(shop_id))
    if client_id is not None:
        lines = lines.filter(receipt__client_id=client_id)
    elif client_phone:
        normalized = _normalize_phone(client_phone)
        if normalized:
            lines = lines.filter(
                Q(receipt__client__phone_normalized=normalized)
                | Q(receipt__client_phone__icontains=normalized[-9:])
            )
        else:
            lines = lines.filter(receipt__client_phone__iexact=client_phone)
    return lines


def _client_info_from_receipt(receipt):
    from shops.services import find_client_by_phone

    client = receipt.client
    client_name = ""
    client_phone = ""
    client_id = None
    if client is not None:
        client_id = client.pk
        client_name = (client.full_name or "").strip()
        client_phone = (client.phone_number or "").strip()
    if not client_name:
        client_name = (receipt.client_name or "").strip()
    if not client_phone:
        client_phone = (receipt.client_phone or "").strip()
    if client_id is None and client_phone:
        matched = find_client_by_phone(client_phone)
        if matched is not None:
            client_id = matched.pk
            if not client_name:
                client_name = (matched.full_name or "").strip()
            if not client_phone:
                client_phone = (matched.phone_number or "").strip()
    return {
        "client_id": client_id,
        "client_name": client_name or "Walk-in",
        "client_phone": client_phone,
    }


def _iter_returned_serial_rows(lines):
    for line in lines:
        returned_serials = [
            str(s).strip()
            for s in (line.returned_serial_numbers or [])
            if str(s).strip()
        ]
        if not returned_serials:
            continue
        receipt = line.receipt
        client_info = _client_info_from_receipt(receipt)
        item_name = (line.item.name if line.item_id else "") or line.item_name
        item_category = (line.item.category if line.item_id else "") or ""
        for serial in returned_serials:
            yield {
                **client_info,
                "item_name": item_name,
                "item_category": item_category,
                "serial_number": serial,
                "receipt_number": receipt.receipt_number,
                "shop_id": receipt.shop_id,
                "shop_name": receipt.shop.name if receipt.shop_id else "—",
                "bought_at": receipt.created_at,
                "amount_paid": line.unit_price,
                "served_by": _employee_display_name(receipt.created_by),
                "returned_at": receipt.last_returned_at or receipt.created_at,
                "received_by": _employee_display_name(receipt.last_returned_by),
            }


def stock_serial_movements(request, profile, meta, module):
    """Chronological stock events that include serial numbers, across all shops."""
    from employees.models import SHOP_ASSIGNABLE_ROLES
    from shops.models import Shop

    from .models import Item

    search = (request.GET.get("q") or "").strip()
    range_type, day_start, day_end, filter_context = _report_range_bounds(request)

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="serial-movements",
        shop_id="",
        profile=profile,
    )

    display_shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )
    shop_ids = [shop.pk for shop in display_shops]
    no_shop_access = profile.role in SHOP_ASSIGNABLE_ROLES and not display_shops
    shop_ids_for_query = [] if no_shop_access else shop_ids

    serial_items = list(
        Item.objects.filter(track_serial_number=True).order_by("category", "name")
    )
    movement_events = []
    if serial_items:
        movement_events, _, _, _, _ = _build_movement_timeline(
            shop_ids=shop_ids_for_query,
            day_start=day_start,
            day_end=day_end,
            item_mode="items",
            selected_categories=[],
            selected_item_ids=[item.pk for item in serial_items],
            report_items=serial_items,
        )
    movement_events = [
        event
        for event in movement_events
        if event.get("serial_numbers")
    ]

    search_key = search.upper()
    rows = []
    for event in movement_events:
        for serial in event["serial_numbers"]:
            if search_key and search_key not in serial:
                continue
            rows.append(
                {
                    "happened_at": event["happened_at"],
                    "event_type": event["event_type"],
                    "event_label": event["event_label"],
                    "item_name": event["item_name"],
                    "item_category": event["item_category"],
                    "serial_number": serial,
                    "from_label": event.get("from_label", "—"),
                    "to_label": event.get("to_label", "—"),
                    "by": event.get("by", "—"),
                }
            )

    rows.sort(key=lambda row: row["happened_at"], reverse=True)

    return render(
        request,
        "items/stock_serial_movements.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "rows": rows,
            "row_count": len(rows),
            "search": search,
            "display_shops": display_shops,
            "stock_mode": "serial-movements",
            **filter_context,
        },
    )


def stock_serial_returns(request, profile, meta, module):
    """Clients who returned serial-tracked items across all shops."""
    from employees.access import role_url_segment
    from shops.models import Shop

    search = (request.GET.get("q") or "").strip()

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="return-clients",
        shop_id="",
        profile=profile,
    )

    display_shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )
    shop_index = {shop.pk: index for index, shop in enumerate(display_shops)}

    lines = _returned_serial_line_queryset(shop_id="")
    clients = {}
    for row in _iter_returned_serial_rows(lines):
        key = (
            f"id:{row['client_id']}"
            if row["client_id"]
            else f"phone:{(row['client_phone'] or row['client_name']).strip().lower()}"
        )
        entry = clients.get(key)
        if entry is None:
            entry = {
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "client_phone": row["client_phone"],
                "return_count": 0,
                "shop_returns": [0] * len(display_shops),
                "last_returned_at": row["returned_at"],
            }
            clients[key] = entry
        entry["return_count"] += 1
        shop_pk = row.get("shop_id")
        if shop_pk in shop_index:
            entry["shop_returns"][shop_index[shop_pk]] += 1
        if row["returned_at"] and (
            entry["last_returned_at"] is None
            or row["returned_at"] > entry["last_returned_at"]
        ):
            entry["last_returned_at"] = row["returned_at"]
            entry["client_name"] = row["client_name"]
            entry["client_phone"] = row["client_phone"]

    segment = role_url_segment(profile.role)
    rows = []
    for entry in clients.values():
        if search:
            needle = search.lower()
            hay = f"{entry['client_name']} {entry['client_phone']}".lower()
            if needle not in hay:
                continue
        if entry["client_id"]:
            detail_url = reverse(
                "employees:stock_serial_return_client",
                kwargs={
                    "role_segment": segment,
                    "client_id": entry["client_id"],
                },
            )
        else:
            detail_url = reverse(
                "employees:stock_serial_return_guest",
                kwargs={"role_segment": segment},
            )
            from urllib.parse import urlencode

            detail_url = (
                f"{detail_url}?{urlencode({'phone': entry['client_phone'] or '', 'name': entry['client_name']})}"
            )
        rows.append({**entry, "detail_url": detail_url})

    rows.sort(
        key=lambda r: (
            r["last_returned_at"] is None,
            -(r["last_returned_at"].timestamp() if r["last_returned_at"] else 0),
            (r["client_name"] or "").lower(),
        )
    )

    return render(
        request,
        "items/stock_serial_returns.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "rows": rows,
            "client_count": len(rows),
            "search": search,
            "display_shops": display_shops,
            "show_all_shops": len(display_shops) > 1,
            "selected_shop_id": "",
            "stock_mode": "return-clients",
        },
    )


def stock_serial_return_client(
    request, profile, meta, module, *, client_id=None, guest_phone="", guest_name=""
):
    """All returned serial items for one client across all shops."""
    from employees.access import role_url_segment
    from shops.models import Client

    search = (request.GET.get("q") or "").strip()

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="return-clients",
        shop_id="",
        profile=profile,
    )

    client = None
    if client_id is not None:
        client = get_object_or_404(Client, pk=client_id)
        client_name = (client.full_name or "").strip() or "Client"
        client_phone = (client.phone_number or "").strip()
        lines = _returned_serial_line_queryset(shop_id="", client_id=client.pk)
    else:
        guest_phone = (guest_phone or request.GET.get("phone") or "").strip()
        guest_name = (guest_name or request.GET.get("name") or "").strip()
        if not guest_phone and not guest_name:
            raise Http404("Client not found.")
        client_name = guest_name or "Walk-in"
        client_phone = guest_phone
        lines = _returned_serial_line_queryset(
            shop_id="", client_phone=guest_phone or guest_name
        )

    rows = []
    for row in _iter_returned_serial_rows(lines):
        if client_id is None:
            # Guest pages: keep rows matching this phone/name group.
            phone_match = (row["client_phone"] or "").strip() == client_phone
            name_match = (row["client_name"] or "").strip().lower() == client_name.lower()
            if client_phone and not phone_match:
                continue
            if not client_phone and not name_match:
                continue
        if search:
            needle = search.lower()
            hay = " ".join(
                [
                    row["serial_number"],
                    row["item_name"],
                    row["item_category"],
                    row["receipt_number"],
                    row["shop_name"],
                    row["served_by"],
                    row["received_by"],
                ]
            ).lower()
            if needle not in hay:
                continue
        rows.append(row)

    segment = role_url_segment(profile.role)
    list_url = reverse(
        "employees:workspace_module",
        kwargs={"role_segment": segment, "module_slug": "stock-management"},
    )
    list_href = f"{list_url}?mode=return-clients"

    return render(
        request,
        "items/stock_serial_return_client.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "client_name": client_name,
            "client_phone": client_phone,
            "rows": rows,
            "row_count": len(rows),
            "search": search,
            "list_href": list_href,
            "stock_mode": "return-clients",
        },
    )


def stock_serial_detail(request, profile, meta, module, item_id):
    """Show serial numbers for one item at the employee's allocated shops."""
    from employees.access import role_url_segment

    from .models import ItemSerial

    item = get_object_or_404(Item, pk=item_id, track_serial_number=True)
    search = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "all").strip().lower()
    if status_filter not in ("all", "in_stock", "sold", "returned", "out"):
        status_filter = "all"

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="serials",
        shop_id="",
        profile=profile,
    )

    display_shops = _serial_shops_for_profile(profile)
    shop_ids = {shop.pk for shop in display_shops}
    shops_label = _serial_shops_label(display_shops)

    serials_qs = ItemSerial.objects.filter(item=item).select_related("shop")
    if shop_ids:
        serials_qs = serials_qs.filter(
            Q(shop_id__in=shop_ids) | Q(shop__isnull=True)
        )
    else:
        serials_qs = serials_qs.none()
    if search:
        serials_qs = serials_qs.filter(serial_number__icontains=search)

    sale_by_serial = _serial_sale_lookup(item)
    return_by_serial = _serial_return_lookup(item)
    rows = []
    in_stock_count = 0
    sold_count = 0
    returned_count = 0
    out_count = 0

    segment = role_url_segment(profile.role)
    for serial in serials_qs:
        # Still on an active sale → sold. After a return, do not flip back to
        # "In stock" — keep status as Returned until the unit is sold again.
        status, status_label, event = _serial_unit_state(
            serial, sale_by_serial, return_by_serial
        )
        event_shop_id = event.get("shop_id") if event else None
        if shop_ids and serial.shop_id not in shop_ids and event_shop_id not in shop_ids:
            continue
        if status == "sold":
            sold_count += 1
        elif status == "returned":
            returned_count += 1
        elif status == "in_stock":
            in_stock_count += 1
        else:
            out_count += 1

        if status_filter != "all" and status != status_filter:
            continue

        rows.append(
            {
                "serial_number": serial.serial_number,
                "status": status,
                "status_label": status_label,
                "shop_name": serial.shop.name if serial.shop_id else "—",
                "client_name": event["client_name"] if event else "",
                "client_phone": event["client_phone"] if event else "",
                "receipt_number": event["receipt_number"] if event else "",
                "sold_at": event.get("sold_at") if event else None,
                "returned_at": event.get("returned_at") if event else None,
                "kind_label": event["kind_label"] if event else "",
                "sale_shop_name": event["shop_name"] if event else "",
                "history_url": reverse(
                    "employees:stock_serial_history",
                    kwargs={
                        "role_segment": segment,
                        "item_id": item.pk,
                        "serial_number": serial.serial_number,
                    },
                ),
            }
        )
    list_url = reverse(
        "employees:workspace_module",
        kwargs={"role_segment": segment, "module_slug": "stock-management"},
    )
    list_href = f"{list_url}?mode=serials"

    return render(
        request,
        "items/stock_serial_detail.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "item": item,
            "rows": rows,
            "row_count": len(rows),
            "in_stock_count": in_stock_count,
            "sold_count": sold_count,
            "returned_count": returned_count,
            "out_count": out_count,
            "search": search,
            "status_filter": status_filter,
            "list_href": list_href,
            "display_shops": display_shops,
            "shops_label": shops_label,
            "stock_mode": "serials",
        },
    )


def _build_serial_history_events(*, item, serial, shop_ids):
    """All stock/sale/return events for one serial, oldest first."""
    from shops.models import ShopReceiptKind, ShopReceiptLine, ShopReceiptStatus

    from .models import StockMovementLine, StockMovementType, StockRequestStatus

    serial_key = str(serial.serial_number or "").strip().upper()
    events = []
    type_labels = {
        StockMovementType.IN: "Stock in",
        StockMovementType.OUT: "Stock out",
        StockMovementType.REQUEST: "Stock request",
    }

    lines = (
        StockMovementLine.objects.filter(item=item)
        .select_related(
            "item",
            "movement",
            "movement__shop",
            "movement__requested_from_shop",
            "movement__created_by__user",
            "movement__responded_by__user",
        )
        .order_by("movement__created_at", "id")
    )
    for line in lines:
        if not _serial_list_contains(line.serial_numbers, serial_key):
            continue
        movement = line.movement
        counts_toward_transfer = False
        transfer_direction = ""
        if movement.movement_type == StockMovementType.REQUEST:
            counts_toward_transfer = _request_transfer_counts_toward_units(movement)
            transfer_direction = _transfer_direction(movement, shop_ids)
        event_type = movement.movement_type
        event_label = type_labels.get(
            movement.movement_type, movement.get_movement_type_display()
        )
        if transfer_direction:
            event_label = _transfer_event_label(
                event_type=event_type,
                direction=transfer_direction,
            )
        event = _timeline_event_from_movement_line(
            movement=movement,
            line=line,
            happened_at=movement.created_at,
            event_type=event_type,
            event_label=event_label,
            actor=movement.created_by,
            counts_toward_transfer=counts_toward_transfer,
            transfer_direction=transfer_direction,
        )
        event["detail"] = (line.note or "").strip()
        events.append(event)
        if (
            movement.movement_type == StockMovementType.REQUEST
            and movement.request_status == StockRequestStatus.FULFILLED
            and movement.responded_at
        ):
            transfer_direction = _transfer_direction(movement, shop_ids)
            fulfilled = _timeline_event_from_movement_line(
                movement=movement,
                line=line,
                happened_at=movement.responded_at,
                event_type="transfer_fulfilled",
                event_label=_transfer_event_label(
                    event_type="transfer_fulfilled",
                    direction=transfer_direction,
                ),
                actor=movement.responded_by,
                transfer_direction=transfer_direction,
            )
            fulfilled["detail"] = (line.note or "").strip()
            events.append(fulfilled)

    receipt_lines = (
        ShopReceiptLine.objects.filter(
            item=item,
            receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
        )
        .exclude(receipt__status=ShopReceiptStatus.CANCELLED)
        .select_related(
            "receipt",
            "receipt__shop",
            "receipt__created_by__user",
            "receipt__last_returned_by__user",
            "receipt__client",
            "item",
        )
        .order_by("receipt__created_at", "id")
    )
    for line in receipt_lines:
        receipt = line.receipt
        parties = _movement_parties_for_receipt(receipt=receipt)
        if _serial_list_contains(line.serial_numbers, serial_key):
            events.append(
                {
                    "happened_at": receipt.created_at,
                    "event_type": "sale",
                    "event_label": "Stock sale",
                    "from_label": parties["from_label"],
                    "to_label": parties["to_label"],
                    "by": _employee_display_name(receipt.created_by),
                    "detail": receipt.receipt_number or "",
                    "movement_id": None,
                }
            )
        if _serial_list_contains(line.returned_serial_numbers, serial_key):
            events.append(
                {
                    "happened_at": receipt.last_returned_at or receipt.created_at,
                    "event_type": "returned",
                    "event_label": "Returned",
                    "from_label": parties["to_label"],
                    "to_label": parties["from_label"],
                    "by": _employee_display_name(receipt.last_returned_by),
                    "detail": receipt.receipt_number or "",
                    "movement_id": None,
                }
            )

    if not any(event.get("event_type") == "in" for event in events) and serial.created_at:
        events.append(
            {
                "happened_at": serial.created_at,
                "event_type": "in",
                "event_label": "Registered",
                "from_label": "—",
                "to_label": serial.shop.name if serial.shop_id else "—",
                "by": "—",
                "detail": "",
                "movement_id": None,
            }
        )

    events.sort(key=lambda row: (row["happened_at"], row.get("movement_id") or 0))
    return events


def stock_serial_history(request, profile, meta, module, item_id, serial_number):
    """Show every movement for one serial from registration to now."""
    from employees.access import role_url_segment
    from shops.models import Shop

    from .models import ItemSerial, ItemSerialStatus

    item = get_object_or_404(Item, pk=item_id, track_serial_number=True)
    serial = get_object_or_404(
        ItemSerial.objects.select_related("shop", "item"),
        item=item,
        serial_number__iexact=(serial_number or "").strip(),
    )

    if request.method == "POST":
        try:
            message = apply_serial_status(
                profile=profile,
                serial=serial,
                new_status=request.POST.get("status") or "",
            )
        except ValidationError as exc:
            messages.error(
                request,
                exc.messages[0] if getattr(exc, "messages", None) else str(exc),
            )
        else:
            messages.success(request, message)
        segment = role_url_segment(profile.role)
        return redirect(
            "employees:stock_serial_history",
            role_segment=segment,
            item_id=item.pk,
            serial_number=serial.serial_number,
        )

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="serials",
        shop_id="",
        profile=profile,
    )

    display_shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )
    shop_ids = [shop.pk for shop in display_shops]
    rows = _build_serial_history_events(
        item=item, serial=serial, shop_ids=shop_ids
    )

    sale_by_serial = _serial_sale_lookup(item)
    return_by_serial = _serial_return_lookup(item)
    status, status_label, event = _serial_unit_state(
        serial, sale_by_serial, return_by_serial
    )

    segment = role_url_segment(profile.role)
    list_href = reverse(
        "employees:stock_serial_detail",
        kwargs={"role_segment": segment, "item_id": item.pk},
    )

    return render(
        request,
        "items/stock_serial_history.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "item": item,
            "serial": serial,
            "rows": rows,
            "row_count": len(rows),
            "unit_status": status,
            "unit_status_label": status_label,
            "unit_shop_name": (
                event["shop_name"]
                if event and status in ("sold", "returned")
                else (serial.shop.name if serial.shop_id else "—")
            ),
            "status_choices": ItemSerialStatus.choices,
            "status_is_manual": bool((serial.status_override or "").strip()),
            "list_href": list_href,
            "stock_mode": "serials",
        },
    )


@active_employee_required
@require_GET
def stock_management_print(request, role_segment):
    """Printable stock list: items only, items+prices, or items+stock."""
    from employees.access import get_profile_for_request, role_url_segment
    from employees.module_permissions import require_module_permission
    from shops.models import Shop
    from shops.services import get_company_profile
    from django.utils import timezone

    profile = get_profile_for_request(request)
    if profile is None or not profile.is_active_employee:
        raise Http404("Not found.")
    if role_url_segment(profile.role) != role_segment:
        raise Http404("Not found.")

    denied = require_module_permission(request, profile, "stock-management", "view")
    if denied is not None:
        return denied

    layout = (request.GET.get("layout") or "items").strip().lower()
    if layout not in ("items", "prices", "stock"):
        layout = "items"

    paper = (request.GET.get("paper") or "a4").strip().lower()
    if paper in ("58",):
        paper = "50"
    if paper not in ("a4", "80", "50"):
        paper = "a4"

    all_shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )
    shops_by_id = {shop.pk: shop for shop in all_shops}

    selected_shops = []
    if layout in ("prices", "stock"):
        raw_ids = request.GET.getlist("shop_id")
        if not raw_ids and request.GET.get("shop_ids"):
            raw_ids = [
                part.strip()
                for part in str(request.GET.get("shop_ids") or "").split(",")
                if part.strip()
            ]
        for raw in raw_ids:
            try:
                shop_id = int(raw)
            except (TypeError, ValueError):
                continue
            shop = shops_by_id.get(shop_id)
            if shop is not None:
                selected_shops.append(shop)
        if not selected_shops:
            if (request.GET.get("estimate") or "").strip() == "1":
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "Select at least one shop to print prices or stock.",
                    },
                    status=400,
                )
            return render(
                request,
                "items/stock_print.html",
                {
                    "error": "Select at least one shop to print prices or stock.",
                    "layout": layout,
                    "paper": paper,
                    "document": None,
                    "printed_at": timezone.localtime(),
                    "company_name": "",
                    "auto_print": False,
                    "is_download": False,
                    "a4_page_estimate": 1,
                },
                status=400,
            )

    document = build_stock_print_document(layout=layout, shops=selected_shops)
    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    printed_at = timezone.localtime()
    as_download = (request.GET.get("download") or "").strip() == "1"
    as_estimate = (request.GET.get("estimate") or "").strip() == "1"

    if as_estimate:
        pages = int(document.get("a4_page_estimate") or estimate_stock_print_a4_pages(document))
        return JsonResponse(
            {
                "ok": True,
                "paper": "a4",
                "layout": layout,
                "item_count": document.get("item_count") or 0,
                "category_count": len(document.get("categories") or []),
                "a4_page_estimate": pages,
                "shop_label": document.get("shop_label") or "",
            }
        )

    if as_download:
        paper = "a4"
        pdf_bytes = build_stock_print_pdf(
            document=document,
            company_name=company_name,
            printed_at=printed_at,
        )
        stamp = printed_at.strftime("%Y-%m-%d")
        layout_slug = layout.replace(" ", "-")
        filename = f"stock-list-a4-{layout_slug}-{stamp}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

    context = {
        "document": document,
        "layout": layout,
        "paper": paper,
        "error": "",
        "printed_at": printed_at,
        "company_name": company_name,
        "a4_page_estimate": document.get("a4_page_estimate")
        or estimate_stock_print_a4_pages(document),
        "auto_print": (request.GET.get("auto") or "").strip() == "1",
        "is_download": False,
    }

    return render(request, "items/stock_print.html", context)
