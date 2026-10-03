from itertools import groupby
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
import mimetypes

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import F, Q
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from employees.access import active_employee_required, get_profile_for_request
from employees.countries import COUNTRY_DIAL_CODES
from employees.workspace import sidebar_for_stock_management

from .models import Item, ShopItemPrice, ShopStock, StockEntrySource
from .services import (
    actionable_shops_for_profile,
    apply_serial_status,
    apply_stock_movement,
    build_stock_catalog_page,
    build_stock_print_document,
    build_stock_print_pdf,
    build_stock_report_pdf,
    check_serials_already_in_stock,
    create_item,
    delete_item,
    estimate_stock_print_a4_pages,
    list_stock_requests_for_profile,
    summarize_stock_requests_for_profile,
    LOW_STOCK_USAGE_WEEKS,
    search_available_serials,
    search_suppliers,
    threshold_from_weekly_avg as _threshold_from_weekly_avg,
    toggle_item_suspended,
    update_item,
    weekly_usage_avg_by_item_shop as _weekly_usage_avg_by_item_shop,
)

EMPTY_FORM = {
    "category": "",
    "name": "",
    "description": "",
    "minimum_selling_price": "",
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
    profile = get_profile_for_request(request)
    if profile is None:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    allowed = {str(shop.pk) for shop in actionable_shops_for_profile(profile)}
    if shop_id and shop_id not in allowed:
        return JsonResponse({"ok": True, "results": [], "match": match})
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
    profile = get_profile_for_request(request)
    item_id = (request.GET.get("item_id") or "").strip()
    serials = request.GET.getlist("serial") or []
    if not serials:
        raw = (request.GET.get("q") or request.GET.get("serial") or "").strip()
        if raw:
            serials = [raw]
    found = check_serials_already_in_stock(item_id=item_id, serials=serials)
    from employees.models import SHOP_ASSIGNABLE_ROLES

    allowed_shop_ids = {
        shop.pk for shop in actionable_shops_for_profile(profile)
    } if profile is not None else set()
    shop_scoped = (
        profile is not None
        and getattr(profile, "role", None) in SHOP_ASSIGNABLE_ROLES
    )
    ordered = []
    seen = set()
    for raw in serials:
        serial = str(raw or "").strip().upper()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        hit = found.get(serial)
        shop_name = (hit or {}).get("shop_name") or ""
        hit_shop_id = (hit or {}).get("shop_id")
        if hit and allowed_shop_ids and hit_shop_id not in allowed_shop_ids:
            if shop_scoped:
                # Do not confirm existence at shops outside allocation.
                hit = None
                shop_name = ""
            else:
                shop_name = "Another shop"
        ordered.append(
            {
                "serial": serial,
                "in_stock": bool(hit),
                "shop_name": shop_name,
            }
        )
        if len(ordered) >= 12:
            break
    return JsonResponse({"ok": True, "results": ordered})


def _pricing_shops_for_profile(profile):
    """Active shops the signed-in employee may price (allocated when shop-scoped)."""
    return actionable_shops_for_profile(profile)


def _serial_shops_for_profile(profile):
    """Shops the signed-in employee may see on serials pages."""
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


def _form_data_from_post(post, profile) -> dict:
    shops = _pricing_shops_for_profile(profile)
    pricing_mode = (post.get("pricing_mode") or "single").strip().lower()
    if pricing_mode not in ("single", "individual"):
        pricing_mode = "single"
    return {
        "category": post.get("category", "").strip().upper(),
        "name": post.get("name", "").strip().upper(),
        "description": post.get("description", "").strip(),
        "minimum_selling_price": post.get("minimum_selling_price", "").strip(),
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
    from employees.workspace import item_management_url

    mode = (request.GET.get("mode") or "view").strip().lower()
    if mode not in {"view", "discounts"}:
        mode = "view"
    if mode == "discounts":
        return item_discounts(request, profile, meta, module)

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
            form_data = _form_data_from_post(request.POST, profile)
            editable_shop_ids = {
                shop.pk for shop in _pricing_shops_for_profile(profile)
            }
            try:
                create_item(
                    profile,
                    request.POST,
                    request.FILES,
                    editable_shop_ids=editable_shop_ids,
                )
            except ValidationError as exc:
                form_errors = _validation_errors(exc)
                open_register_modal = True
            else:
                messages.success(request, f"Item “{form_data['name']}” registered successfully.")
                return redirect(request.path)

        elif action == "edit":
            edit_item = get_object_or_404(Item, pk=item_id)
            form_data = _form_data_from_post(request.POST, profile)
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
            try:
                delete_item(item)
            except ValidationError as exc:
                for msg in _validation_errors(exc):
                    messages.error(request, msg)
                return redirect(request.path)
            messages.success(request, f"Item “{name}” deleted.")
            return redirect(request.path)

        else:
            messages.error(request, "Unknown action.")
            return redirect(request.path)
    else:
        denied = require_module_permission(request, profile, "item-management", "view")
        if denied is not None:
            return denied

    pricing_shops = _pricing_shops_for_profile(profile)
    edit_pricing_shops = pricing_shops
    from employees.access import role_url_segment

    item_count = Item.objects.count()
    item_categories = [
        category
        for category in (
            Item.objects.order_by("category")
            .values_list("category", flat=True)
            .distinct()
        )
        if category
    ]
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
            "item_categories": item_categories,
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
            "item_discounts_url": item_management_url(profile.role, "discounts"),
        },
    )


@require_http_methods(["GET", "POST"])
def item_discounts(request, profile, meta, module):
    """List items with sale/credit qty advice and editable wholesale discounts."""
    from decimal import Decimal, InvalidOperation

    from employees.module_permissions import (
        employee_may,
        module_capabilities,
        require_module_permission,
    )
    from employees.workspace import item_management_url, sidebar_for_item_management

    from .services import (
        item_wholesale_sale_advice,
        session_average_buying_prices_for_items,
        suggest_wholesale_unit_price,
    )

    denied = require_module_permission(request, profile, "item-management", "view")
    if denied is not None:
        return denied

    can_edit = employee_may(profile, "item-management", "edit")
    wants_json = (
        "application/json" in (request.headers.get("Accept") or "").lower()
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or (request.POST.get("ajax") or "") == "1"
    )

    if request.method == "POST":
        if not can_edit:
            if wants_json:
                return JsonResponse(
                    {"ok": False, "error": "You cannot edit item discounts."},
                    status=403,
                )
            messages.error(request, "You cannot edit item discounts.")
            return redirect(item_management_url(profile.role, "discounts"))

        action = (request.POST.get("action") or "save_discount").strip()
        if action != "save_discount":
            if wants_json:
                return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
            messages.error(request, "Unknown action.")
            return redirect(item_management_url(profile.role, "discounts"))

        item_id = (request.POST.get("item_id") or "").strip()
        item = get_object_or_404(Item, pk=item_id)
        list_price = item.shop_price or Decimal("0")
        min_price = item.minimum_selling_price or Decimal("0")

        def _parse_qty(raw, *, label, minimum=0):
            try:
                value = int(str(raw or "").strip() or "0")
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{label} must be a whole number.") from exc
            if value < minimum:
                raise ValidationError(f"{label} must be at least {minimum}.")
            return value

        def _parse_amount(raw, *, label):
            text = str(raw or "").strip() or "0"
            try:
                value = Decimal(text)
            except (InvalidOperation, ValueError) as exc:
                raise ValidationError(f"{label} must be a valid amount.") from exc
            if value < 0:
                raise ValidationError(f"{label} cannot be negative.")
            return value.quantize(Decimal("0.01"))

        try:
            wholesale_from_qty = _parse_qty(
                request.POST.get("wholesale_from_qty")
                or request.POST.get("discount_min_qty"),
                label="Wholesale from quantity",
                minimum=0,
            )
            # Prefer absolute wholesale price; fall back to legacy amount-off.
            raw_wholesale = (request.POST.get("wholesale_price") or "").strip()
            if raw_wholesale != "":
                wholesale_price = _parse_amount(
                    raw_wholesale, label="Wholesale price"
                )
                if wholesale_from_qty <= 0 and wholesale_price > 0:
                    raise ValidationError(
                        "Enter the quantity from which wholesale price starts."
                    )
                if wholesale_from_qty > 0:
                    if wholesale_price <= 0:
                        raise ValidationError(
                            "Enter the wholesale price after discount."
                        )
                    if list_price > 0 and wholesale_price >= list_price:
                        raise ValidationError(
                            "Wholesale price must be below the list selling price "
                            f"(KSh {list_price})."
                        )
                    if wholesale_price < min_price:
                        raise ValidationError(
                            "Wholesale price cannot be below the minimum "
                            f"selling price (KSh {min_price})."
                        )
                    avg_buy = session_average_buying_prices_for_items(
                        [item.pk],
                        shop_ids=[
                            shop.pk for shop in _pricing_shops_for_profile(profile)
                        ]
                        or None,
                    ).get(item.pk)
                    if avg_buy and wholesale_price <= avg_buy:
                        raise ValidationError(
                            "Wholesale price must stay above the average buying "
                            f"price (KSh {avg_buy})."
                        )
                    discount_amount = (
                        (list_price - wholesale_price).quantize(Decimal("0.01"))
                        if list_price > 0
                        else Decimal("0.00")
                    )
                else:
                    discount_amount = Decimal("0.00")
                    wholesale_price = list_price
            else:
                discount_amount = _parse_amount(
                    request.POST.get("discount_amount"),
                    label="Discount amount",
                )
                if wholesale_from_qty > 0 and discount_amount <= 0:
                    raise ValidationError(
                        "Enter a wholesale price when a quantity threshold is set."
                    )
                if discount_amount > 0 and wholesale_from_qty <= 0:
                    raise ValidationError(
                        "Enter the quantity from which wholesale price starts."
                    )
                wholesale_price = (
                    (list_price - discount_amount).quantize(Decimal("0.01"))
                    if list_price > 0
                    else Decimal("0.00")
                )
                if discount_amount > 0 and wholesale_price < min_price:
                    raise ValidationError(
                        f"Wholesale price would put “{item.name}” below the "
                        f"minimum selling price (KSh {min_price})."
                    )

            # Keep avg_buy_qty aligned with live sale/credit average when available.
            shops_for_advice = list(_pricing_shops_for_profile(profile))
            advice = item_wholesale_sale_advice(
                [item.pk],
                shop_ids=[shop.pk for shop in shops_for_advice] or None,
            ).get(item.pk) or {}
            avg_sold = advice.get("avg_sold_qty") or 0
            avg_buy_qty = max(1, int(float(avg_sold) + 0.5)) if avg_sold else max(
                1, int(item.avg_buy_qty or 1)
            )
        except ValidationError as exc:
            errors = [
                str(message)
                for message in (
                    exc.messages if hasattr(exc, "messages") else [exc]
                )
            ]
            if wants_json:
                return JsonResponse(
                    {"ok": False, "error": errors[0] if errors else "Invalid input."},
                    status=400,
                )
            for error in errors:
                messages.error(request, error)
            return redirect(item_management_url(profile.role, "discounts"))

        item.avg_buy_qty = avg_buy_qty
        item.discount_min_qty = wholesale_from_qty
        item.discount_amount = discount_amount
        item.save(
            update_fields=[
                "avg_buy_qty",
                "discount_min_qty",
                "discount_amount",
                "updated_at",
            ]
        )

        payload = {
            "ok": True,
            "item_id": item.pk,
            "avg_buy_qty": item.avg_buy_qty,
            "wholesale_from_qty": item.discount_min_qty,
            "discount_min_qty": item.discount_min_qty,
            "discount_amount": f"{item.discount_amount:.2f}",
            "wholesale_price": f"{wholesale_price:.2f}",
            "volume_price": f"{item.volume_unit_price(list_price, item.discount_min_qty or 1):.2f}",
        }
        if wants_json:
            return JsonResponse(payload)
        messages.success(request, f"Wholesale discount saved for “{item.name}”.")
        return redirect(item_management_url(profile.role, "discounts"))

    shops = list(_pricing_shops_for_profile(profile))
    items = list(
        Item.objects.only(
            "id",
            "category",
            "name",
            "description",
            "minimum_selling_price",
            "shop_price",
            "use_individual_shop_prices",
            "avg_buy_qty",
            "discount_min_qty",
            "discount_amount",
            "is_suspended",
            "image",
        ).order_by("category", "name")
    )
    item_ids = [item.pk for item in items]
    allowed_shop_ids = {shop.pk for shop in shops}
    sale_advice = item_wholesale_sale_advice(
        item_ids,
        shop_ids=list(allowed_shop_ids) or None,
    )
    avg_buy_prices = session_average_buying_prices_for_items(
        item_ids,
        shop_ids=list(allowed_shop_ids) or None,
    )

    discount_rows = []
    for item in items:
        advice = sale_advice.get(item.pk) or {}
        list_price = item.shop_price or Decimal("0")
        discount_amount = item.discount_amount or Decimal("0")
        wholesale_price = (
            (list_price - discount_amount).quantize(Decimal("0.01"))
            if discount_amount > 0 and list_price > 0
            else list_price
        )
        avg_buy = avg_buy_prices.get(item.pk)
        suggested_from = int(advice.get("suggested_from_qty") or 0)
        suggested_wholesale = suggest_wholesale_unit_price(
            list_price=list_price,
            minimum_selling_price=item.minimum_selling_price,
            avg_buy_price=avg_buy,
            historical_price=advice.get("suggested_wholesale_price"),
        )
        # Still advise a from-qty when we have a valid price band but no sales yet.
        if suggested_wholesale is not None and suggested_from <= 0:
            suggested_from = max(2, int(item.avg_buy_qty or 2))
        buy_floor = f"{avg_buy:.2f}" if avg_buy else ""
        sell_ceiling = f"{list_price:.2f}" if list_price else ""
        discount_rows.append(
            {
                "item": item,
                "avg_buy_price": buy_floor,
                "avg_sold_qty": advice.get("avg_sold_qty") or 0,
                "transaction_count": advice.get("transaction_count")
                or advice.get("line_count")
                or 0,
                "suggested_from_qty": suggested_from,
                "suggested_wholesale_price": (
                    f"{suggested_wholesale:.2f}" if suggested_wholesale is not None else ""
                ),
                "advice_band_label": (
                    f"KSh {buy_floor} – {sell_ceiling}"
                    if buy_floor and sell_ceiling and suggested_wholesale is not None
                    else ""
                ),
                "wholesale_from_qty": int(item.discount_min_qty or 0),
                "wholesale_price": f"{wholesale_price:.2f}",
                "list_price": f"{list_price:.2f}",
                "has_volume_discount": bool(
                    int(item.discount_min_qty or 0) > 0 and discount_amount > 0
                ),
            }
        )

    page_sidebar = sidebar_for_item_management(
        profile.role, profile=profile, active_mode="discounts"
    )
    meta = {
        **meta,
        "title": "Item discounts",
        "headline": "Item discounts",
        "summary": (
            "Sale and credit history advises wholesale quantity and price; "
            "set where wholesale starts and the price after discount."
        ),
    }

    return render(
        request,
        "items/item_discounts.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "discount_rows": discount_rows,
            "item_count": len(discount_rows),
            "item_management_url": item_management_url(profile.role, "view"),
            "can_edit_discounts": can_edit,
            "module_permissions": module_capabilities(profile, "item-management"),
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
        shops=_pricing_shops_for_profile(profile),
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


# SQLite (and large IN lists generally) slows hard once item filters grow.
# Prefer shop/date filters in SQL and membership checks in Python.
_SQL_ITEM_IN_LIMIT = 64


def _item_ids_for_sql(item_ids):
    """Return item_ids for SQL IN, or None to skip IN and filter in Python."""
    if not item_ids:
        return []
    if len(item_ids) <= _SQL_ITEM_IN_LIMIT:
        return list(item_ids)
    return None


def _empty_item_shop_qty(item_ids, shop_ids, keys):
    return {
        (item_id, shop_id): {key: 0 for key in keys}
        for item_id in item_ids
        for shop_id in shop_ids
    }


def _add_item_shop_qty(totals, item_id, shop_id, key, quantity):
    bucket = totals.get((item_id, shop_id))
    if bucket is None:
        return
    bucket[key] = int(bucket.get(key) or 0) + int(quantity or 0)


def _movement_qty_by_item_shop(item_ids, shop_ids, start, end):
    """Stock in/out by (item, shop) for a window. Requests are counted as transfers."""
    from django.db.models import Sum

    from .models import StockEntrySource, StockMovementLine, StockMovementType

    totals = _empty_item_shop_qty(item_ids, shop_ids, ("in", "out"))
    if not item_ids or not shop_ids or start >= end:
        return totals

    sql_item_ids = _item_ids_for_sql(item_ids)
    qs = StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__created_at__gte=start,
        movement__created_at__lt=end,
        movement__movement_type__in=[
            StockMovementType.IN,
            StockMovementType.OUT,
        ],
    )
    # Customer returns are counted in stock_return, not stock_in.
    qs = qs.exclude(movement__entry_source=StockEntrySource.CUSTOMER_RETURN)
    if sql_item_ids is not None:
        qs = qs.filter(item_id__in=sql_item_ids)
    rows = qs.values(
        "item_id", "movement__shop_id", "movement__movement_type"
    ).annotate(total=Sum("quantity"))
    for row in rows:
        _add_item_shop_qty(
            totals,
            row["item_id"],
            row["movement__shop_id"],
            row["movement__movement_type"],
            row["total"],
        )
    return totals


def _transfer_qty_by_item_shop(item_ids, shop_ids, start, end):
    """
    Fulfilled inter-shop transfers by (item, shop), counted when stock moved.

    Transfer in: selected shop is the destination (movement.shop).
    Transfer out: selected shop is the source (movement.requested_from_shop).
    """
    from django.db.models import Sum

    from .models import StockMovementLine, StockMovementType, StockRequestStatus

    totals = _empty_item_shop_qty(item_ids, shop_ids, ("in", "out"))
    if not item_ids or not shop_ids or start >= end:
        return totals

    sql_item_ids = _item_ids_for_sql(item_ids)
    base = StockMovementLine.objects.filter(
        movement__movement_type=StockMovementType.REQUEST,
        movement__request_status=StockRequestStatus.FULFILLED,
        movement__responded_at__gte=start,
        movement__responded_at__lt=end,
        quantity__gt=0,
    )
    if sql_item_ids is not None:
        base = base.filter(item_id__in=sql_item_ids)

    in_rows = (
        base.filter(movement__shop_id__in=shop_ids)
        .values("item_id", "movement__shop_id")
        .annotate(total=Sum("quantity"))
    )
    for row in in_rows:
        _add_item_shop_qty(
            totals, row["item_id"], row["movement__shop_id"], "in", row["total"]
        )

    out_rows = (
        base.filter(movement__requested_from_shop_id__in=shop_ids)
        .values("item_id", "movement__requested_from_shop_id")
        .annotate(total=Sum("quantity"))
    )
    for row in out_rows:
        _add_item_shop_qty(
            totals,
            row["item_id"],
            row["movement__requested_from_shop_id"],
            "out",
            row["total"],
        )
    return totals


def _current_stock_by_item(item_ids, shop_ids):
    from django.db.models import Sum

    from .models import ShopStock

    totals = {item_id: 0 for item_id in item_ids}
    if not item_ids or not shop_ids:
        return totals
    sql_item_ids = _item_ids_for_sql(item_ids)
    qs = ShopStock.objects.filter(shop_id__in=shop_ids)
    if sql_item_ids is not None:
        qs = qs.filter(item_id__in=sql_item_ids)
    rows = qs.values("item_id").annotate(total=Sum("quantity"))
    for row in rows:
        item_id = row["item_id"]
        if item_id in totals:
            totals[item_id] = int(row["total"] or 0)
    return totals


def _current_stock_by_item_shop(item_ids, shop_ids):
    from django.db.models import Sum

    from .models import ShopStock

    totals = {(item_id, shop_id): 0 for item_id in item_ids for shop_id in shop_ids}
    if not item_ids or not shop_ids:
        return totals
    sql_item_ids = _item_ids_for_sql(item_ids)
    qs = ShopStock.objects.filter(shop_id__in=shop_ids)
    if sql_item_ids is not None:
        qs = qs.filter(item_id__in=sql_item_ids)
    rows = (
        qs.order_by()
        .values("item_id", "shop_id")
        .annotate(total=Sum("quantity"))
    )
    for row in rows:
        key = (row["item_id"], row["shop_id"])
        if key in totals:
            totals[key] = int(row["total"] or 0)
    return totals


LOW_STOCK_ITEM_ONLY = (
    "id",
    "name",
    "category",
    "low_stock_notify",
    "low_stock_threshold",
    "is_suspended",
)


def _shop_low_stock_settings(item_ids, shop_ids):
    from .models import ShopStock

    settings = {}
    if not item_ids or not shop_ids:
        return settings
    rows = (
        ShopStock.objects.filter(item_id__in=item_ids, shop_id__in=shop_ids)
        .order_by()
        .values("item_id", "shop_id", "low_stock_threshold", "low_stock_manual")
    )
    for row in rows:
        settings[(row["item_id"], row["shop_id"])] = {
            "threshold": int(row["low_stock_threshold"] or 0),
            "manual": bool(row["low_stock_manual"]),
        }
    return settings


def _sync_shop_thresholds_from_usage(items, shops):
    from .models import ShopStock

    ordered_shops = list(shops)
    item_ids = [item.pk for item in items]
    shop_ids = [shop.pk for shop in ordered_shops]
    usage = _weekly_usage_avg_by_item_shop(item_ids, shop_ids)
    existing = {
        (row.item_id, row.shop_id): row
        for row in ShopStock.objects.filter(
            item_id__in=item_ids, shop_id__in=shop_ids
        ).order_by()
    }
    to_create = []
    to_update = []
    for item in items:
        for shop in ordered_shops:
            threshold = _threshold_from_weekly_avg(
                usage.get((item.pk, shop.pk), 0)
            )
            row = existing.get((item.pk, shop.pk))
            if row is None:
                to_create.append(
                    ShopStock(
                        shop=shop,
                        item=item,
                        quantity=0,
                        low_stock_threshold=threshold,
                        low_stock_manual=True,
                    )
                )
            elif (
                int(row.low_stock_threshold or 0) != threshold
                or not row.low_stock_manual
            ):
                row.low_stock_threshold = threshold
                row.low_stock_manual = True
                to_update.append(row)
    if to_create:
        ShopStock.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        ShopStock.objects.bulk_update(
            to_update, ["low_stock_threshold", "low_stock_manual"]
        )
    return len(items)


def _set_shop_low_stock_threshold(item, shop, threshold, *, manual=True):
    from .models import ShopStock

    ShopStock.objects.update_or_create(
        shop=shop,
        item=item,
        defaults={
            "low_stock_threshold": max(0, int(threshold or 0)),
            "low_stock_manual": bool(manual),
        },
    )


def _low_stock_json(item, shops, *, include_usage=False):
    snapshot = _low_stock_item_snapshot(item, shops, include_usage=include_usage)
    return {
        "ok": True,
        "item_id": item.pk,
        "notify": snapshot["notify"],
        "total_units": snapshot["total_units"],
        "is_low": snapshot["is_low"],
        "shops": snapshot["shops"],
    }


def _low_stock_payloads_from_rows(rows):
    """Split a bulk settings table into per-item JSON snapshots."""
    payloads = []
    current = None
    for row in rows:
        item_id = row["item"].pk
        if current is None or current["item_id"] != item_id:
            if current is not None:
                payloads.append(current)
            current = {
                "ok": True,
                "item_id": item_id,
                "notify": bool(row["notify"]),
                "total_units": 0,
                "is_low": False,
                "shops": [],
            }
        if row["is_item_total"]:
            current["total_units"] = row["total_units"]
            current["is_low"] = row["is_low"]
            continue
        current["shops"].append(
            {
                "shop_id": row["shop_id"],
                "units": row["total_units"],
                "is_low": row["is_low"],
                "threshold": row["threshold"],
                "manual": bool(row["threshold_manual"]),
                "auto_threshold": row["auto_threshold"],
            }
        )
        current["total_units"] += row["total_units"]
        current["is_low"] = current["is_low"] or row["is_low"]
    if current is not None:
        payloads.append(current)
    return payloads


def _build_low_stock_rows(items, shops, *, include_usage=True):
    """Item rows with one shop line each, plus a Total when several shops."""
    ordered_shops = sorted(
        shops,
        key=lambda shop: ((shop.name or "").lower(), shop.pk),
    )
    shop_ids = [shop.pk for shop in ordered_shops]
    item_ids = [item.pk for item in items]
    stock = _current_stock_by_item_shop(item_ids, shop_ids)
    usage = (
        _weekly_usage_avg_by_item_shop(item_ids, shop_ids) if include_usage else {}
    )
    shop_settings = _shop_low_stock_settings(item_ids, shop_ids)
    group_by_shop = len(ordered_shops) > 1
    rows = []
    notify_count = 0
    for item in items:
        notify = bool(item.low_stock_notify)
        item_threshold = int(item.low_stock_threshold or 0)
        if notify:
            notify_count += 1
        shop_rows = []
        total_units = 0
        any_low = False
        shops_for_item = ordered_shops or [None]
        for shop in shops_for_item:
            units = (
                stock.get((item.pk, shop.pk), 0) if shop is not None else 0
            )
            avg_week = (
                usage.get((item.pk, shop.pk), 0) if shop is not None else 0
            )
            stored = (
                shop_settings.get((item.pk, shop.pk)) if shop is not None else None
            )
            auto_threshold = _threshold_from_weekly_avg(avg_week)
            if stored and stored["manual"]:
                threshold = stored["threshold"]
                threshold_manual = True
            elif include_usage:
                threshold = auto_threshold
                threshold_manual = False
            elif stored:
                threshold = stored["threshold"]
                threshold_manual = False
            else:
                threshold = item_threshold
                threshold_manual = False
            if not include_usage:
                auto_threshold = threshold if not threshold_manual else auto_threshold
            is_low = bool(notify and units <= threshold)
            total_units += units
            any_low = any_low or is_low
            shop_rows.append(
                {
                    "item": item,
                    "shop_id": shop.pk if shop is not None else None,
                    "shop_name": shop.name if shop is not None else "—",
                    "total_units": units,
                    "avg_week": avg_week,
                    "notify": notify,
                    "threshold": threshold,
                    "auto_threshold": auto_threshold,
                    "threshold_manual": threshold_manual,
                    "is_low": is_low,
                    "is_item_start": False,
                    "is_item_total": False,
                    "show_threshold": shop is not None,
                    "show_notify": False,
                    "show_sync": False,
                }
            )
        shop_rows[0]["is_item_start"] = True
        shop_rows[0]["show_notify"] = True
        shop_rows[0]["show_sync"] = True
        total_avg_week = sum(int(row["avg_week"] or 0) for row in shop_rows)
        rows.extend(shop_rows)
        if group_by_shop:
            rows.append(
                {
                    "item": item,
                    "shop_id": None,
                    "shop_name": "Total",
                    "total_units": total_units,
                    "avg_week": total_avg_week,
                    "notify": notify,
                    "threshold": None,
                    "auto_threshold": None,
                    "threshold_manual": False,
                    "is_low": any_low,
                    "is_item_start": False,
                    "is_item_total": True,
                    "show_threshold": False,
                    "show_notify": False,
                    "show_sync": False,
                }
            )
    return rows, notify_count, group_by_shop


def _low_stock_item_snapshot(item, shops, *, include_usage=False):
    rows, _notify_count, _group = _build_low_stock_rows(
        [item], shops, include_usage=include_usage
    )
    payload = _low_stock_payloads_from_rows(rows)
    first = payload[0] if payload else {}
    return {
        "notify": bool(item.low_stock_notify),
        "threshold": int(item.low_stock_threshold or 0),
        "total_units": first.get("total_units", 0),
        "is_low": bool(first.get("is_low")),
        "shops": first.get("shops", []),
    }


def _receipt_sale_qty_by_item_shop(item_ids, shop_ids, start, end):
    from django.db.models import Sum

    from shops.models import ShopReceiptKind, ShopReceiptLine

    totals = {(item_id, shop_id): 0 for item_id in item_ids for shop_id in shop_ids}
    if not item_ids or not shop_ids or start >= end:
        return totals
    sql_item_ids = _item_ids_for_sql(item_ids)
    qs = ShopReceiptLine.objects.filter(
        item_id__isnull=False,
        receipt__shop_id__in=shop_ids,
        receipt__created_at__gte=start,
        receipt__created_at__lt=end,
        receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
    )
    if sql_item_ids is not None:
        qs = qs.filter(item_id__in=sql_item_ids)
    rows = qs.values("item_id", "receipt__shop_id").annotate(total=Sum("quantity"))
    for row in rows:
        key = (row["item_id"], row["receipt__shop_id"])
        if key in totals:
            totals[key] += int(row["total"] or 0)
    return totals


def _parse_return_batch_at(raw):
    from django.utils.dateparse import parse_datetime

    if raw is None:
        return None
    if hasattr(raw, "tzinfo"):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    return parse_datetime(text)


def _trade_qty_by_item_shop(item_ids, shop_ids, start, end):
    """
    Trade-tagged units by (item, shop) for a window.

    Includes trade out / exchange stock movements, trade-tagged customer returns,
    and sales converted from cleared trade-out receipts.
    """
    from django.db.models import Q, Sum

    from shops.models import ShopReceiptKind, ShopReceiptLine

    from .models import (
        StockEntrySource,
        StockMovementLine,
        StockOutReason,
    )

    totals = {(item_id, shop_id): 0 for item_id in item_ids for shop_id in shop_ids}
    if not item_ids or not shop_ids or start >= end:
        return totals

    sql_item_ids = _item_ids_for_sql(item_ids)
    trade_filter = (
        Q(movement__entry_source=StockEntrySource.TRADE_OUT)
        | Q(movement__entry_source=StockEntrySource.TRADE_EXCHANGE)
        | Q(reason=StockOutReason.TRADE_OUT)
        | (
            Q(movement__entry_source=StockEntrySource.CUSTOMER_RETURN)
            & (
                Q(note__icontains="trade")
                | Q(movement__notes__icontains="trade")
            )
        )
    )
    line_qs = StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__created_at__gte=start,
        movement__created_at__lt=end,
    ).filter(trade_filter)
    if sql_item_ids is not None:
        line_qs = line_qs.filter(item_id__in=sql_item_ids)
    for row in line_qs.values("item_id", "movement__shop_id").annotate(
        total=Sum("quantity")
    ):
        key = (row["item_id"], row["movement__shop_id"])
        if key in totals:
            totals[key] += int(row["total"] or 0)

    receipt_qs = ShopReceiptLine.objects.filter(
        item_id__isnull=False,
        receipt__shop_id__in=shop_ids,
        receipt__created_at__gte=start,
        receipt__created_at__lt=end,
        receipt__settled_from_trade=True,
        receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
    )
    if sql_item_ids is not None:
        receipt_qs = receipt_qs.filter(item_id__in=sql_item_ids)
    for row in receipt_qs.values("item_id", "receipt__shop_id").annotate(
        total=Sum("quantity")
    ):
        key = (row["item_id"], row["receipt__shop_id"])
        if key in totals:
            totals[key] += int(row["total"] or 0)
    return totals


def _return_qty_by_item_shop(item_ids, shop_ids, start, end):
    """
    Customer return quantities by (item, shop) for a window.

    Prefers per-return batches (accurate when a receipt is returned across days).
    Falls back to last_returned_at + full returned_quantity for legacy rows.
    """
    from django.db.models import Q

    from shops.models import ShopReceiptLine

    totals = {(item_id, shop_id): 0 for item_id in item_ids for shop_id in shop_ids}
    if not item_ids or not shop_ids or start >= end:
        return totals
    sql_item_ids = _item_ids_for_sql(item_ids)
    # Bound the scan: legacy rows by last_returned_at, plus any row that has
    # return_batches (batch timestamps are checked in Python).
    qs = ShopReceiptLine.objects.filter(
        item_id__isnull=False,
        receipt__shop_id__in=shop_ids,
        returned_quantity__gt=0,
    ).filter(
        Q(receipt__last_returned_at__gte=start, receipt__last_returned_at__lt=end)
        | ~Q(return_batches=[])
    )
    if sql_item_ids is not None:
        qs = qs.filter(item_id__in=sql_item_ids)
    rows = qs.values(
        "item_id",
        "receipt__shop_id",
        "returned_quantity",
        "return_batches",
        "receipt__last_returned_at",
    )
    for row in rows:
        key = (row["item_id"], row["receipt__shop_id"])
        if key not in totals:
            continue
        batches = row["return_batches"] or []
        if isinstance(batches, list) and batches:
            for batch in batches:
                if not isinstance(batch, dict):
                    continue
                happened_at = _parse_return_batch_at(batch.get("at"))
                if happened_at is None or happened_at < start or happened_at >= end:
                    continue
                try:
                    qty = int(batch.get("qty") or 0)
                except (TypeError, ValueError):
                    qty = 0
                if qty > 0:
                    totals[key] += qty
            continue
        happened_at = row["receipt__last_returned_at"]
        if happened_at is None or happened_at < start or happened_at >= end:
            continue
        totals[key] += int(row["returned_quantity"] or 0)
    return totals


def _pos_sale_qty_by_item(items, shop_ids, start, end):
    """Legacy POS SaleLine quantities by item name (no shop on the sale)."""
    from django.db.models import Sum

    from pos.models import SaleLine

    totals = {item.pk: 0 for item in items}
    if not items or not shop_ids or start >= end:
        return totals

    name_to_ids = {}
    for item in items:
        key = (item.name or "").strip().lower()
        if key:
            name_to_ids.setdefault(key, []).append(item.pk)
    if not name_to_ids:
        return totals

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


def _sale_qty_by_item(items, shop_ids, start, end):
    """
    Sale quantities for stock items.

    Prefer MY-SHOP receipt lines (item FK) — accurate and indexed.
    Fall back to legacy POS SaleLine matching by product name.
    """
    item_ids = [item.pk for item in items]
    totals = {item.pk: 0 for item in items}
    if not items:
        return totals
    receipt = _receipt_sale_qty_by_item_shop(item_ids, shop_ids, start, end)
    for (item_id, _shop_id), quantity in receipt.items():
        if item_id in totals:
            totals[item_id] += quantity
    pos = _pos_sale_qty_by_item(items, shop_ids, start, end)
    for item_id, quantity in pos.items():
        totals[item_id] += quantity
    return totals


def _collapse_item_shop_qty(keyed, item_ids, keys):
    totals = {item_id: {key: 0 for key in keys} for item_id in item_ids}
    for (item_id, _shop_id), bucket in keyed.items():
        dest = totals.get(item_id)
        if dest is None:
            continue
        if isinstance(bucket, dict):
            for key in keys:
                dest[key] += int(bucket.get(key) or 0)
        else:
            dest[keys[0]] += int(bucket or 0)
    return totals


def _item_report_closing_starting(
    *,
    current,
    after_in,
    after_out,
    after_sale,
    after_transfer_in,
    after_transfer_out,
    after_return,
    stock_in,
    stock_out,
    stock_sale,
    stock_transfer_in,
    stock_transfer_out,
    stock_return,
):
    closing = (
        current
        - after_in
        + after_out
        + after_sale
        - after_transfer_in
        + after_transfer_out
        - after_return
    )
    starting = (
        closing
        - stock_in
        + stock_out
        + stock_sale
        - stock_transfer_in
        + stock_transfer_out
        - stock_return
    )
    return starting, closing


def _paginate_item_report_groups(rows, page, page_size, *, group_by_shop=False):
    """
    Paginate report rows by item group so shop lines for one item stay together.

    Returns (page_rows, total_groups, total_pages, page).
    """
    if not rows:
        return [], 0, 1, 1

    if group_by_shop:
        groups = []
        current = []
        for row in rows:
            if row.get("is_item_start") and current:
                groups.append(current)
                current = [row]
            else:
                current.append(row)
        if current:
            groups.append(current)
    else:
        groups = [[row] for row in rows]

    total_groups = len(groups)
    total_pages = max(1, (total_groups + page_size - 1) // page_size)
    page = max(1, min(int(page or 1), total_pages))
    start = (page - 1) * page_size
    page_rows = []
    for group in groups[start : start + page_size]:
        page_rows.extend(group)
    return page_rows, total_groups, total_pages, page


def _report_page_urls(request, page, total_pages):
    """Build previous/next querystrings for report pagination."""
    if total_pages <= 1:
        return "", ""
    from urllib.parse import urlencode

    base_params = []
    for key, values in request.GET.lists():
        if key == "page":
            continue
        for value in values:
            base_params.append((key, value))
    prev_url = ""
    next_url = ""
    if page > 1:
        prev_url = "?" + urlencode(base_params + [("page", str(page - 1))])
    if page < total_pages:
        next_url = "?" + urlencode(base_params + [("page", str(page + 1))])
    return prev_url, next_url


def _build_item_report_rows(
    items,
    shop_ids,
    day_start,
    day_end,
    *,
    group_by_shop=None,
    shops_by_id=None,
):
    """
    Build item report rows for a period, including shop transfer in/out.

    closing = current - after_in + after_out + after_sale
              - after_transfer_in + after_transfer_out - after_return
    starting = closing - in + out + sale - transfer_in + transfer_out - return
    Transfers are shop-relative and counted at fulfillment (responded_at).
    """
    from datetime import timedelta

    from django.utils import timezone

    item_ids = [item.pk for item in items]
    if not item_ids:
        return []

    if group_by_shop is None:
        group_by_shop = len(shop_ids) > 1

    now = timezone.now()
    far_future = now + timedelta(days=3650)
    # When the report window ends at/after "now", there is no activity after
    # day_end yet — skip the expensive after_* queries entirely.
    need_after = day_end < now

    current_shop = _current_stock_by_item_shop(item_ids, shop_ids)
    period_moves = _movement_qty_by_item_shop(item_ids, shop_ids, day_start, day_end)
    period_transfers = _transfer_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    period_sales_shop = _receipt_sale_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    period_returns_shop = _return_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    period_trade_shop = _trade_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    if need_after:
        after_moves = _movement_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
        after_transfers = _transfer_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
        after_sales_shop = _receipt_sale_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
        after_returns_shop = _return_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
    else:
        after_moves = {}
        after_transfers = {}
        after_sales_shop = {}
        after_returns_shop = {}

    if shops_by_id is None and group_by_shop and shop_ids:
        from shops.models import Shop

        shops_by_id = {
            shop.pk: shop for shop in Shop.objects.filter(pk__in=shop_ids)
        }
    shops_by_id = shops_by_id or {}

    def row_payload(
        item,
        *,
        shop=None,
        starting,
        stock_in,
        stock_transfer_in,
        stock_out,
        stock_transfer_out,
        stock_sale,
        stock_return,
        stock_trade,
        closing,
    ):
        sale_qty = int(stock_sale or 0)
        return_qty = int(stock_return or 0)
        return {
            "item": item,
            "shop": shop,
            "shop_name": shop.name if shop is not None else "",
            "is_item_start": False,
            "is_item_total": False,
            "starting_stock": starting,
            "stock_in": stock_in,
            "stock_transfer_in": stock_transfer_in,
            "stock_out": stock_out,
            "stock_transfer_out": stock_transfer_out,
            "stock_trade": int(stock_trade or 0),
            "stock_sale": sale_qty,
            "stock_return": return_qty,
            "net_sale": max(0, sale_qty - return_qty),
            "closing_stock": closing,
        }

    rows = []
    if group_by_shop:
        ordered_shop_ids = sorted(
            shop_ids,
            key=lambda shop_id: (
                (getattr(shops_by_id.get(shop_id), "name", None) or "").lower(),
                shop_id,
            ),
        )
        for item in items:
            shop_rows = []
            has_data = False
            for shop_id in ordered_shop_ids:
                key = (item.pk, shop_id)
                period = period_moves.get(key) or {"in": 0, "out": 0}
                after = after_moves.get(key) or {"in": 0, "out": 0}
                transfer = period_transfers.get(key) or {"in": 0, "out": 0}
                after_transfer = after_transfers.get(key) or {"in": 0, "out": 0}
                stock_in = period["in"]
                stock_out = period["out"]
                stock_transfer_in = transfer["in"]
                stock_transfer_out = transfer["out"]
                stock_sale = period_sales_shop.get(key) or 0
                stock_return = period_returns_shop.get(key) or 0
                stock_trade = period_trade_shop.get(key) or 0
                starting, closing = _item_report_closing_starting(
                    current=current_shop.get(key) or 0,
                    after_in=after["in"],
                    after_out=after["out"],
                    after_sale=after_sales_shop.get(key) or 0,
                    after_transfer_in=after_transfer["in"],
                    after_transfer_out=after_transfer["out"],
                    after_return=after_returns_shop.get(key) or 0,
                    stock_in=stock_in,
                    stock_out=stock_out,
                    stock_sale=stock_sale,
                    stock_transfer_in=stock_transfer_in,
                    stock_transfer_out=stock_transfer_out,
                    stock_return=stock_return,
                )
                if (
                    starting
                    or closing
                    or stock_in
                    or stock_out
                    or stock_sale
                    or stock_transfer_in
                    or stock_transfer_out
                    or stock_return
                    or stock_trade
                ):
                    has_data = True
                shop_rows.append(
                    row_payload(
                        item,
                        shop=shops_by_id.get(shop_id),
                        starting=starting,
                        stock_in=stock_in,
                        stock_transfer_in=stock_transfer_in,
                        stock_out=stock_out,
                        stock_transfer_out=stock_transfer_out,
                        stock_sale=stock_sale,
                        stock_return=stock_return,
                        stock_trade=stock_trade,
                        closing=closing,
                    )
                )
            if not has_data:
                continue
            shop_rows[0]["is_item_start"] = True
            rows.extend(shop_rows)
            total = row_payload(
                item,
                starting=sum(row["starting_stock"] for row in shop_rows),
                stock_in=sum(row["stock_in"] for row in shop_rows),
                stock_transfer_in=sum(row["stock_transfer_in"] for row in shop_rows),
                stock_out=sum(row["stock_out"] for row in shop_rows),
                stock_transfer_out=sum(row["stock_transfer_out"] for row in shop_rows),
                stock_sale=sum(row["stock_sale"] for row in shop_rows),
                stock_return=sum(row["stock_return"] for row in shop_rows),
                stock_trade=sum(row["stock_trade"] for row in shop_rows),
                closing=sum(row["closing_stock"] for row in shop_rows),
            )
            total["shop_name"] = "Total"
            total["is_item_total"] = True
            rows.append(total)
        return rows

    current = _collapse_item_shop_qty(
        {k: {"qty": v} for k, v in current_shop.items()}, item_ids, ("qty",)
    )
    period_move_item = _collapse_item_shop_qty(period_moves, item_ids, ("in", "out"))
    after_move_item = _collapse_item_shop_qty(after_moves, item_ids, ("in", "out"))
    period_transfer_item = _collapse_item_shop_qty(
        period_transfers, item_ids, ("in", "out")
    )
    after_transfer_item = _collapse_item_shop_qty(
        after_transfers, item_ids, ("in", "out")
    )
    period_sale_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in period_sales_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    after_sale_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in after_sales_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    period_return_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in period_returns_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    after_return_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in after_returns_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    period_trade_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in period_trade_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    pos_period = _pos_sale_qty_by_item(items, shop_ids, day_start, day_end)
    for item_id, quantity in pos_period.items():
        period_sale_item[item_id] = period_sale_item.get(item_id, 0) + quantity
    if need_after:
        pos_after = _pos_sale_qty_by_item(items, shop_ids, day_end, far_future)
        for item_id, quantity in pos_after.items():
            after_sale_item[item_id] = after_sale_item.get(item_id, 0) + quantity

    for item in items:
        period = period_move_item[item.pk]
        after = after_move_item[item.pk]
        transfer = period_transfer_item[item.pk]
        after_transfer = after_transfer_item[item.pk]
        stock_in = period["in"]
        stock_out = period["out"]
        stock_transfer_in = transfer["in"]
        stock_transfer_out = transfer["out"]
        stock_sale = period_sale_item.get(item.pk, 0)
        stock_return = period_return_item.get(item.pk, 0)
        stock_trade = period_trade_item.get(item.pk, 0)
        starting, closing = _item_report_closing_starting(
            current=current[item.pk]["qty"],
            after_in=after["in"],
            after_out=after["out"],
            after_sale=after_sale_item.get(item.pk, 0),
            after_transfer_in=after_transfer["in"],
            after_transfer_out=after_transfer["out"],
            after_return=after_return_item.get(item.pk, 0),
            stock_in=stock_in,
            stock_out=stock_out,
            stock_sale=stock_sale,
            stock_transfer_in=stock_transfer_in,
            stock_transfer_out=stock_transfer_out,
            stock_return=stock_return,
        )
        if not (
            starting
            or closing
            or stock_in
            or stock_out
            or stock_sale
            or stock_transfer_in
            or stock_transfer_out
            or stock_return
            or stock_trade
        ):
            continue
        rows.append(
            row_payload(
                item,
                starting=starting,
                stock_in=stock_in,
                stock_transfer_in=stock_transfer_in,
                stock_out=stock_out,
                stock_transfer_out=stock_transfer_out,
                stock_sale=stock_sale,
                stock_return=stock_return,
                stock_trade=stock_trade,
                closing=closing,
            )
        )
    return rows


def _filter_items_by_search(items, query):
    """Filter Item objects by name/category substring (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return list(items or [])
    matched = []
    for item in items or []:
        hay = f"{getattr(item, 'name', '') or ''} {getattr(item, 'category', '') or ''}"
        if q in hay.lower():
            matched.append(item)
    return matched


def _empty_daily_qty_bucket():
    return {
        "in": 0,
        "out": 0,
        "transfer_in": 0,
        "transfer_out": 0,
        "sale": 0,
        "return": 0,
        "trade": 0,
    }


def _local_report_date(value):
    """Convert a datetime to the local calendar date used by stock reports."""
    from django.utils import timezone

    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value).date()


def _daily_activity_by_item(items, shop_ids, day_start, day_end):
    """
    Aggregate stock activity by (item_id, local date) for Daily report rows.

    Sources match _build_item_report_rows: stock in/out, fulfilled transfers,
    receipt sales (+ legacy POS), returns, and trade-tagged units.

    Bucketing is done in Python (local date) because MySQL TruncDate on related
    DateTimeFields can return NULL on some MariaDB/XAMPP setups.
    """
    from django.db.models import Q

    from shops.models import ShopReceiptKind, ShopReceiptLine

    from .models import (
        StockEntrySource,
        StockMovementLine,
        StockMovementType,
        StockOutReason,
        StockRequestStatus,
    )

    item_ids = [item.pk for item in items]
    daily = {}
    if not item_ids or not shop_ids or day_start >= day_end:
        return daily

    sql_item_ids = _item_ids_for_sql(item_ids)
    item_id_set = set(item_ids)

    def bucket(item_id, day):
        if item_id not in item_id_set or day is None:
            return None
        key = (item_id, day)
        if key not in daily:
            daily[key] = _empty_daily_qty_bucket()
        return daily[key]

    def add_qty(item_id, happened_at, field, quantity):
        qty = int(quantity or 0)
        if qty == 0:
            return
        dest = bucket(item_id, _local_report_date(happened_at))
        if dest is not None:
            dest[field] += qty

    # Stock in / out (exclude customer returns — counted under return).
    move_qs = StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__created_at__gte=day_start,
        movement__created_at__lt=day_end,
        movement__movement_type__in=[
            StockMovementType.IN,
            StockMovementType.OUT,
        ],
    ).exclude(movement__entry_source=StockEntrySource.CUSTOMER_RETURN)
    if sql_item_ids is not None:
        move_qs = move_qs.filter(item_id__in=sql_item_ids)
    for row in move_qs.values(
        "item_id", "quantity", "movement__created_at", "movement__movement_type"
    ):
        field = (
            "in"
            if row["movement__movement_type"] == StockMovementType.IN
            else "out"
            if row["movement__movement_type"] == StockMovementType.OUT
            else None
        )
        if field:
            add_qty(row["item_id"], row["movement__created_at"], field, row["quantity"])

    # Fulfilled transfers counted at responded_at.
    transfer_base = StockMovementLine.objects.filter(
        movement__movement_type=StockMovementType.REQUEST,
        movement__request_status=StockRequestStatus.FULFILLED,
        movement__responded_at__gte=day_start,
        movement__responded_at__lt=day_end,
        quantity__gt=0,
    )
    if sql_item_ids is not None:
        transfer_base = transfer_base.filter(item_id__in=sql_item_ids)

    for row in transfer_base.filter(movement__shop_id__in=shop_ids).values(
        "item_id", "quantity", "movement__responded_at"
    ):
        add_qty(
            row["item_id"],
            row["movement__responded_at"],
            "transfer_in",
            row["quantity"],
        )

    for row in transfer_base.filter(
        movement__requested_from_shop_id__in=shop_ids
    ).values("item_id", "quantity", "movement__responded_at"):
        add_qty(
            row["item_id"],
            row["movement__responded_at"],
            "transfer_out",
            row["quantity"],
        )

    # Receipt sales.
    sale_qs = ShopReceiptLine.objects.filter(
        item_id__isnull=False,
        receipt__shop_id__in=shop_ids,
        receipt__created_at__gte=day_start,
        receipt__created_at__lt=day_end,
        receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
    )
    if sql_item_ids is not None:
        sale_qs = sale_qs.filter(item_id__in=sql_item_ids)
    for row in sale_qs.values("item_id", "quantity", "receipt__created_at"):
        add_qty(row["item_id"], row["receipt__created_at"], "sale", row["quantity"])

    # Legacy POS sales by product name.
    from pos.models import SaleLine

    name_to_ids = {}
    for item in items:
        key = (item.name or "").strip().lower()
        if key:
            name_to_ids.setdefault(key, []).append(item.pk)
    names = [(item.name or "").strip() for item in items if (item.name or "").strip()]
    if names:
        pos_qs = SaleLine.objects.filter(
            sale__sold_at__gte=day_start,
            sale__sold_at__lt=day_end,
            product_name__in=names,
        )
        if shop_ids:
            pos_qs = pos_qs.filter(
                sale__employee__assigned_shops__in=shop_ids
            ).distinct()
        for row in pos_qs.values("product_name", "quantity", "sale__sold_at"):
            key = (row["product_name"] or "").strip().lower()
            for item_id in name_to_ids.get(key, []):
                add_qty(item_id, row["sale__sold_at"], "sale", row["quantity"])

    # Customer returns (batch-aware).
    return_qs = ShopReceiptLine.objects.filter(
        item_id__isnull=False,
        receipt__shop_id__in=shop_ids,
        returned_quantity__gt=0,
    ).filter(
        Q(receipt__last_returned_at__gte=day_start, receipt__last_returned_at__lt=day_end)
        | ~Q(return_batches=[])
    )
    if sql_item_ids is not None:
        return_qs = return_qs.filter(item_id__in=sql_item_ids)
    for row in return_qs.values(
        "item_id",
        "returned_quantity",
        "return_batches",
        "receipt__last_returned_at",
    ):
        item_id = row["item_id"]
        if item_id not in item_id_set:
            continue
        batches = row["return_batches"] or []
        if isinstance(batches, list) and batches:
            for batch in batches:
                if not isinstance(batch, dict):
                    continue
                happened_at = _parse_return_batch_at(batch.get("at"))
                if (
                    happened_at is None
                    or happened_at < day_start
                    or happened_at >= day_end
                ):
                    continue
                try:
                    qty = int(batch.get("qty") or 0)
                except (TypeError, ValueError):
                    qty = 0
                if qty > 0:
                    add_qty(item_id, happened_at, "return", qty)
            continue
        happened_at = row["receipt__last_returned_at"]
        if happened_at is None or happened_at < day_start or happened_at >= day_end:
            continue
        add_qty(item_id, happened_at, "return", row["returned_quantity"])

    # Trade-tagged units (movements + settled trade sales).
    trade_filter = (
        Q(movement__entry_source=StockEntrySource.TRADE_OUT)
        | Q(movement__entry_source=StockEntrySource.TRADE_EXCHANGE)
        | Q(reason=StockOutReason.TRADE_OUT)
        | (
            Q(movement__entry_source=StockEntrySource.CUSTOMER_RETURN)
            & (
                Q(note__icontains="trade")
                | Q(movement__notes__icontains="trade")
            )
        )
    )
    trade_line_qs = StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__created_at__gte=day_start,
        movement__created_at__lt=day_end,
    ).filter(trade_filter)
    if sql_item_ids is not None:
        trade_line_qs = trade_line_qs.filter(item_id__in=sql_item_ids)
    for row in trade_line_qs.values("item_id", "quantity", "movement__created_at"):
        add_qty(row["item_id"], row["movement__created_at"], "trade", row["quantity"])

    trade_receipt_qs = ShopReceiptLine.objects.filter(
        item_id__isnull=False,
        receipt__shop_id__in=shop_ids,
        receipt__created_at__gte=day_start,
        receipt__created_at__lt=day_end,
        receipt__settled_from_trade=True,
        receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
    )
    if sql_item_ids is not None:
        trade_receipt_qs = trade_receipt_qs.filter(item_id__in=sql_item_ids)
    for row in trade_receipt_qs.values("item_id", "quantity", "receipt__created_at"):
        add_qty(row["item_id"], row["receipt__created_at"], "trade", row["quantity"])

    return daily


def _period_starting_stock_by_item(items, shop_ids, day_start, day_end):
    """Starting stock at day_start for each item (shops collapsed)."""
    from datetime import timedelta

    from django.utils import timezone

    item_ids = [item.pk for item in items]
    starting = {item_id: 0 for item_id in item_ids}
    if not item_ids or not shop_ids:
        return starting

    now = timezone.now()
    far_future = now + timedelta(days=3650)
    need_after = day_end < now

    current_shop = _current_stock_by_item_shop(item_ids, shop_ids)
    period_moves = _movement_qty_by_item_shop(item_ids, shop_ids, day_start, day_end)
    period_transfers = _transfer_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    period_sales_shop = _receipt_sale_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    period_returns_shop = _return_qty_by_item_shop(
        item_ids, shop_ids, day_start, day_end
    )
    if need_after:
        after_moves = _movement_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
        after_transfers = _transfer_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
        after_sales_shop = _receipt_sale_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
        after_returns_shop = _return_qty_by_item_shop(
            item_ids, shop_ids, day_end, far_future
        )
    else:
        after_moves = {}
        after_transfers = {}
        after_sales_shop = {}
        after_returns_shop = {}

    current = _collapse_item_shop_qty(
        {k: {"qty": v} for k, v in current_shop.items()}, item_ids, ("qty",)
    )
    period_move_item = _collapse_item_shop_qty(period_moves, item_ids, ("in", "out"))
    after_move_item = _collapse_item_shop_qty(after_moves, item_ids, ("in", "out"))
    period_transfer_item = _collapse_item_shop_qty(
        period_transfers, item_ids, ("in", "out")
    )
    after_transfer_item = _collapse_item_shop_qty(
        after_transfers, item_ids, ("in", "out")
    )
    period_sale_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in period_sales_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    after_sale_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in after_sales_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    period_return_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in period_returns_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    after_return_item = {
        item_id: qty["qty"]
        for item_id, qty in _collapse_item_shop_qty(
            {k: {"qty": v} for k, v in after_returns_shop.items()}, item_ids, ("qty",)
        ).items()
    }
    pos_period = _pos_sale_qty_by_item(items, shop_ids, day_start, day_end)
    for item_id, quantity in pos_period.items():
        period_sale_item[item_id] = period_sale_item.get(item_id, 0) + quantity
    if need_after:
        pos_after = _pos_sale_qty_by_item(items, shop_ids, day_end, far_future)
        for item_id, quantity in pos_after.items():
            after_sale_item[item_id] = after_sale_item.get(item_id, 0) + quantity

    for item in items:
        period = period_move_item[item.pk]
        after = after_move_item[item.pk]
        transfer = period_transfer_item[item.pk]
        after_transfer = after_transfer_item[item.pk]
        stock_in = period["in"]
        stock_out = period["out"]
        stock_transfer_in = transfer["in"]
        stock_transfer_out = transfer["out"]
        stock_sale = period_sale_item.get(item.pk, 0)
        stock_return = period_return_item.get(item.pk, 0)
        start, _closing = _item_report_closing_starting(
            current=current[item.pk]["qty"],
            after_in=after["in"],
            after_out=after["out"],
            after_sale=after_sale_item.get(item.pk, 0),
            after_transfer_in=after_transfer["in"],
            after_transfer_out=after_transfer["out"],
            after_return=after_return_item.get(item.pk, 0),
            stock_in=stock_in,
            stock_out=stock_out,
            stock_sale=stock_sale,
            stock_transfer_in=stock_transfer_in,
            stock_transfer_out=stock_transfer_out,
            stock_return=stock_return,
        )
        starting[item.pk] = start
    return starting


def _build_item_report_daily_rows(items, shop_ids, day_start, day_end):
    """
    One row per (item, local day) with activity in [day_start, day_end).

    Start/Close chain from period starting stock; quiet days are omitted.
    Shops are collapsed (no per-shop split).
    """
    items = list(items or [])
    if not items or not shop_ids or day_start >= day_end:
        return []

    daily = _daily_activity_by_item(items, shop_ids, day_start, day_end)
    if not daily:
        return []

    starting_by_item = _period_starting_stock_by_item(
        items, shop_ids, day_start, day_end
    )
    dates_by_item = {}
    for (item_id, day), bucket in daily.items():
        if not any(bucket.get(key) for key in bucket):
            continue
        dates_by_item.setdefault(item_id, []).append(day)
    for item_id in dates_by_item:
        dates_by_item[item_id].sort()

    rows = []
    for item in items:
        activity_days = dates_by_item.get(item.pk) or []
        if not activity_days:
            continue
        running = int(starting_by_item.get(item.pk) or 0)
        for day in activity_days:
            bucket = daily.get((item.pk, day)) or _empty_daily_qty_bucket()
            stock_in = int(bucket["in"] or 0)
            stock_out = int(bucket["out"] or 0)
            stock_transfer_in = int(bucket["transfer_in"] or 0)
            stock_transfer_out = int(bucket["transfer_out"] or 0)
            stock_sale = int(bucket["sale"] or 0)
            stock_return = int(bucket["return"] or 0)
            stock_trade = int(bucket["trade"] or 0)
            closing = (
                running
                + stock_in
                + stock_transfer_in
                - stock_out
                - stock_transfer_out
                - stock_sale
                + stock_return
            )
            rows.append(
                {
                    "item": item,
                    "report_date": day,
                    "shop": None,
                    "shop_name": "",
                    "is_item_start": False,
                    "is_item_total": False,
                    "is_daily_row": True,
                    "starting_stock": running,
                    "stock_in": stock_in,
                    "stock_transfer_in": stock_transfer_in,
                    "stock_out": stock_out,
                    "stock_transfer_out": stock_transfer_out,
                    "stock_trade": stock_trade,
                    "stock_sale": stock_sale,
                    "stock_return": stock_return,
                    "net_sale": max(0, stock_sale - stock_return),
                    "closing_stock": closing,
                }
            )
            running = closing
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


def _transfer_people_for_movement(movement):
    """Requester and receiver names for inter-shop transfer movements."""
    from .models import StockMovementType

    if movement.movement_type != StockMovementType.REQUEST:
        return "", ""
    return (
        _employee_display_name(movement.created_by),
        _employee_display_name(movement.responded_by),
    )


def _format_transfer_by_label(*, requested_by, received_by):
    req = (requested_by or "").strip()
    rec = (received_by or "").strip()
    parts = []
    if req and req != "—":
        parts.append(f"Requested: {req}")
    if rec and rec != "—":
        parts.append(f"Received: {rec}")
    return " · ".join(parts) if parts else "—"


def _timeline_trade_label(*, movement=None, line=None, receipt=None, note=""):
    """Human label for trade-related stock/receipt activity, else empty."""
    from .models import StockEntrySource, StockOutReason

    source = (getattr(movement, "entry_source", None) or "").strip()
    if source == StockEntrySource.TRADE_OUT:
        return "Trade out"
    if source == StockEntrySource.TRADE_EXCHANGE:
        return "Trade exchange"
    if source == StockEntrySource.CUSTOMER_RETURN:
        text = (note or getattr(line, "note", None) or getattr(movement, "notes", None) or "")
        if "trade return" in text.lower() or "trade" in text.lower():
            return "Trade return"

    reason = (getattr(line, "reason", None) or "").strip()
    if reason == StockOutReason.TRADE_OUT:
        return "Trade out"

    if receipt is not None:
        from shops.models import ShopReceiptKind

        kind = getattr(receipt, "kind", None) or ""
        if kind == ShopReceiptKind.TRADE_OUT:
            return "Trade out"
        if getattr(receipt, "settled_from_trade", False):
            return "Trade sale"

    text = (note or "").strip().lower()
    if text.startswith("trade out"):
        return "Trade out"
    if "trade return" in text:
        return "Trade return"
    if "trade exchange" in text:
        return "Trade exchange"
    return ""


def _trade_receipt_number_from_text(text):
    """Pull receipt number from trade movement notes when present."""
    note = (text or "").strip()
    for prefix in (
        "Trade out · ",
        "Trade out ",
        "Trade return on ",
        "Trade exchange · ",
        "Trade exchange ",
    ):
        if note.startswith(prefix):
            return note[len(prefix) :].strip()
    return ""


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

    requested_by, received_by = _transfer_people_for_movement(movement)
    by_label = _employee_display_name(actor)
    if movement.movement_type == StockMovementType.REQUEST and (
        transfer_direction or event_type == "transfer_fulfilled"
    ):
        by_label = _format_transfer_by_label(
            requested_by=requested_by,
            received_by=received_by,
        )

    note = line.note or ""
    trade_label = _timeline_trade_label(
        movement=movement, line=line, note=note or movement.notes or ""
    )
    receipt_number = _trade_receipt_number_from_text(note) or _trade_receipt_number_from_text(
        movement.notes or ""
    )

    return {
        "happened_at": happened_at,
        "event_type": event_type,
        "event_label": event_label,
        "shop_name": movement.shop.name if movement.shop else "—",
        "shop_id": movement.shop_id,
        "source_shop_id": movement.requested_from_shop_id,
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
        "reason": _stock_out_reason_label(line),
        "payment_status": (
            line.get_payment_status_display() if line.payment_status else ""
        ),
        "note": note,
        "by": by_label,
        "requested_by": requested_by,
        "received_by": received_by,
        "serial_numbers": _movement_serial_numbers(line.serial_numbers),
        "movement_id": movement.pk,
        "receipt_number": receipt_number,
        "receipt_status": "",
        "trade": trade_label,
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
    event_filter="all",
):
    """
    Chronological stock events for the filtered period (oldest first).
    Stock in, stock out, and request come from movements; sales are separate events.
    Accepted stock requests also appear when stock moves (responded_at), not only when submitted.

    event_filter narrows which data sources are queried so filtered views avoid
    loading unrelated sales/returns/transfers for the whole period.
    """
    from django.db.models import Prefetch, Q

    from .models import (
        StockEntrySource,
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

    event_filter = _parse_movement_event_filter(event_filter)
    # Which sources this filter needs from the database.
    need_stock_movements = event_filter in ("all", "in", "out", "return")
    need_fulfilled_transfers = event_filter in ("all", "transfer")
    # Sale/return filters also need the companion type for receipt grouping.
    need_receipt_sales = event_filter in ("all", "sale", "return")
    need_legacy_returns = event_filter in ("all", "sale", "return")
    need_pos_sales = event_filter in ("all", "sale")

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

    if need_stock_movements:
        if event_filter == "in":
            movement_filter &= Q(movement_type=StockMovementType.IN) & ~Q(
                entry_source=StockEntrySource.CUSTOMER_RETURN
            )
        elif event_filter == "out":
            movement_filter &= Q(movement_type=StockMovementType.OUT)
        elif event_filter == "return":
            movement_filter &= Q(
                movement_type=StockMovementType.IN,
                entry_source=StockEntrySource.CUSTOMER_RETURN,
            )
        # event_filter == "all": keep in/out/request submitted rows

        movements = (
            StockMovement.objects.filter(movement_filter)
            .distinct()
            .select_related(
                "shop",
                "requested_from_shop",
                "created_by__user",
                "responded_by__user",
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
            is_customer_return = (
                movement.entry_source == StockEntrySource.CUSTOMER_RETURN
            )
            for line in movement.lines.all():
                counts_toward_transfer = False
                transfer_direction = ""
                if movement.movement_type == StockMovementType.REQUEST:
                    counts_toward_transfer = _request_transfer_counts_toward_units(
                        movement
                    )
                    transfer_direction = _transfer_direction(movement, shop_ids)
                if is_customer_return:
                    event_type = "return"
                    event_label = "Return"
                else:
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
                if is_customer_return:
                    note = (line.note or movement.notes or "").strip()
                    receipt_number = ""
                    for prefix in (
                        "Trade return on ",
                        "Return on ",
                        "Customer return on ",
                    ):
                        if note.startswith(prefix):
                            receipt_number = note[len(prefix) :].strip()
                            break
                    event["receipt_number"] = receipt_number
                    event["reason"] = "Return"
                    event["note"] = note or (
                        f"Return on {receipt_number}"
                        if receipt_number
                        else "Customer return"
                    )
                    event["trade"] = _timeline_trade_label(
                        movement=movement, line=line, note=event["note"]
                    )
                events.append(event)
                if is_customer_return:
                    continue
                if movement.movement_type == StockMovementType.IN:
                    units_in += line.quantity
                elif movement.movement_type == StockMovementType.OUT:
                    units_out += line.quantity
                elif (
                    movement.movement_type == StockMovementType.REQUEST
                    and counts_toward_transfer
                ):
                    units_request += line.quantity

    if need_fulfilled_transfers:
        fulfilled_line_qs = StockMovementLine.objects.select_related("item").order_by(
            "id"
        )
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
                "created_by__user",
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

    # Sales / returns from receipts and legacy POS — skipped for transfer/in/out filters.
    from pos.models import SaleLine
    from shops.models import ShopReceiptKind, ShopReceiptLine, ShopReceiptStatus

    item_by_name = {
        (item.name or "").strip().lower(): item for item in report_items
    }
    item_by_id = {item.pk: item for item in report_items}
    item_name_set = set(item_by_name)

    if need_receipt_sales:
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
                "receipt__last_returned_by__user",
                "receipt__client",
                "item",
            )
            .order_by("receipt__created_at", "id")
        )
        if item_mode == "category" and selected_categories:
            receipt_lines = receipt_lines.filter(
                item__category__in=selected_categories
            )
        elif item_mode == "items" and selected_item_ids:
            receipt_lines = receipt_lines.filter(item_id__in=selected_item_ids)

        for line in receipt_lines.iterator(chunk_size=500):
            matched = item_by_id.get(line.item_id) or item_by_name.get(
                (line.item_name or "").strip().lower()
            )
            parties = _movement_parties_for_receipt(receipt=line.receipt)
            receipt_status = line.receipt.status
            sale_label = "Stock sale"
            if receipt_status == ShopReceiptStatus.CANCELLED:
                sale_label = "Sale (cancelled)"
            elif receipt_status == ShopReceiptStatus.PARTIAL_RETURN:
                sale_label = "Sale (partial return)"
            if line.receipt.kind == ShopReceiptKind.CREDIT:
                sale_label = sale_label.replace("Sale", "Credit sale").replace(
                    "Stock sale", "Credit sale"
                )
            events.append(
                {
                    "happened_at": line.receipt.created_at,
                    "event_type": "sale",
                    "event_label": sale_label,
                    "shop_name": (
                        line.receipt.shop.name if line.receipt.shop_id else "—"
                    ),
                    "shop_id": line.receipt.shop_id,
                    "source_shop_id": None,
                    "from_shop_name": "",
                    "item_name": line.item_name
                    or (matched.name if matched else "—"),
                    "item_category": (
                        matched.category
                        if matched
                        else (
                            line.item.category
                            if line.item_id and line.item
                            else ""
                        )
                    ),
                    "item_id": line.item_id or (matched.pk if matched else None),
                    "quantity": line.quantity,
                    "reason": "",
                    "payment_status": parties["pay"] if parties["pay"] != "—" else "",
                    "note": "",
                    "by": _employee_display_name(line.receipt.created_by),
                    "serial_numbers": _movement_serial_numbers(line.serial_numbers),
                    "movement_id": None,
                    "receipt_number": line.receipt.receipt_number,
                    "receipt_status": receipt_status,
                    "trade": _timeline_trade_label(receipt=line.receipt),
                    **parties,
                }
            )
            units_sale += line.quantity

    if need_legacy_returns:
        # Bound by return date in SQL so year filters do not scan all historic returns.
        legacy_return_lines = (
            ShopReceiptLine.objects.filter(
                returned_quantity__gt=0,
                receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
                receipt__shop_id__in=shop_ids,
            )
            .filter(
                Q(
                    receipt__last_returned_at__gte=day_start,
                    receipt__last_returned_at__lt=day_end,
                )
                | Q(
                    receipt__last_returned_at__isnull=True,
                    receipt__created_at__gte=day_start,
                    receipt__created_at__lt=day_end,
                )
            )
            .select_related(
                "receipt",
                "receipt__shop",
                "receipt__created_by__user",
                "receipt__last_returned_by__user",
                "receipt__client",
                "item",
            )
            .order_by("receipt__last_returned_at", "id")
        )
        if item_mode == "category" and selected_categories:
            legacy_return_lines = legacy_return_lines.filter(
                item__category__in=selected_categories
            )
        elif item_mode == "items" and selected_item_ids:
            legacy_return_lines = legacy_return_lines.filter(
                item_id__in=selected_item_ids
            )

        for line in legacy_return_lines.iterator(chunk_size=500):
            matched = item_by_id.get(line.item_id) or item_by_name.get(
                (line.item_name or "").strip().lower()
            )
            parties = _movement_parties_for_receipt(receipt=line.receipt)
            receipt_status = line.receipt.status
            batches = line.return_batches or []
            if isinstance(batches, list) and batches:
                # Restocked returns with an item already appear via StockMovement.
                if line.item_id:
                    continue
                for batch in batches:
                    if not isinstance(batch, dict):
                        continue
                    happened_at = _parse_return_batch_at(batch.get("at"))
                    if (
                        happened_at is None
                        or happened_at < day_start
                        or happened_at >= day_end
                    ):
                        continue
                    try:
                        qty = int(batch.get("qty") or 0)
                    except (TypeError, ValueError):
                        qty = 0
                    if qty <= 0:
                        continue
                    events.append(
                        {
                            "happened_at": happened_at,
                            "event_type": "return",
                            "event_label": "Return",
                            "shop_name": (
                                line.receipt.shop.name
                                if line.receipt.shop_id
                                else "—"
                            ),
                            "shop_id": line.receipt.shop_id,
                            "source_shop_id": None,
                            "from_shop_name": "",
                            "item_name": line.item_name
                            or (matched.name if matched else "—"),
                            "item_category": (
                                matched.category
                                if matched
                                else (
                                    line.item.category
                                    if line.item_id and line.item
                                    else ""
                                )
                            ),
                            "item_id": line.item_id
                            or (matched.pk if matched else None),
                            "quantity": qty,
                            "reason": "Return",
                            "payment_status": "",
                            "note": f"Return on {line.receipt.receipt_number}",
                            "by": _employee_display_name(
                                line.receipt.last_returned_by
                                or line.receipt.created_by
                            ),
                            "serial_numbers": _movement_serial_numbers(
                                batch.get("serials") or []
                            ),
                            "movement_id": None,
                            "receipt_number": line.receipt.receipt_number,
                            "receipt_status": receipt_status,
                            "trade": _timeline_trade_label(receipt=line.receipt),
                            "from_label": parties.get("to_label", "—"),
                            "to_label": parties.get("from_label", "—"),
                            "seller": parties.get("seller", "—"),
                            "pay": "—",
                        }
                    )
                continue

            happened_at = line.receipt.last_returned_at or line.receipt.created_at
            if (
                happened_at is None
                or happened_at < day_start
                or happened_at >= day_end
            ):
                continue
            events.append(
                {
                    "happened_at": happened_at,
                    "event_type": "return",
                    "event_label": "Return",
                    "shop_name": (
                        line.receipt.shop.name if line.receipt.shop_id else "—"
                    ),
                    "shop_id": line.receipt.shop_id,
                    "source_shop_id": None,
                    "from_shop_name": "",
                    "item_name": line.item_name
                    or (matched.name if matched else "—"),
                    "item_category": (
                        matched.category
                        if matched
                        else (
                            line.item.category
                            if line.item_id and line.item
                            else ""
                        )
                    ),
                    "item_id": line.item_id or (matched.pk if matched else None),
                    "quantity": line.returned_quantity,
                    "reason": "Return",
                    "payment_status": "",
                    "note": f"Return on {line.receipt.receipt_number}",
                    "by": _employee_display_name(
                        line.receipt.last_returned_by or line.receipt.created_by
                    ),
                    "serial_numbers": _movement_serial_numbers(
                        line.returned_serial_numbers
                    ),
                    "movement_id": None,
                    "receipt_number": line.receipt.receipt_number,
                    "receipt_status": receipt_status,
                    "trade": _timeline_trade_label(receipt=line.receipt),
                    "from_label": parties.get("to_label", "—"),
                    "to_label": parties.get("from_label", "—"),
                    "seller": parties.get("seller", "—"),
                    "pay": "—",
                }
            )

    if need_pos_sales:
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
            .prefetch_related("sale__employee__assigned_shops")
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

        shop_id_set = set(shop_ids)
        # Do not use iterator() here — it drops prefetch of assigned_shops.
        for line in sale_lines:
            key = (line.product_name or "").strip().lower()
            if allowed_names and key not in allowed_names:
                continue
            matched = item_by_name.get(key)
            sale_shop_id = None
            sale_shop_name = "—"
            employee = line.sale.employee if line.sale.employee_id else None
            if employee is not None:
                # Prefer prefetched shops; avoid per-row .filter() queries.
                for shop in employee.assigned_shops.all():
                    if shop.pk in shop_id_set:
                        sale_shop_id = shop.pk
                        sale_shop_name = shop.name
                        break
            parties = _movement_parties_for_pos_sale(sale=line.sale)
            events.append(
                {
                    "happened_at": line.sale.sold_at,
                    "event_type": "sale",
                    "event_label": "Stock sale",
                    "shop_name": sale_shop_name,
                    "shop_id": sale_shop_id,
                    "source_shop_id": None,
                    "from_shop_name": "",
                    "item_name": line.product_name
                    or (matched.name if matched else "—"),
                    "item_category": matched.category if matched else "",
                    "item_id": matched.pk if matched else None,
                    "quantity": line.quantity,
                    "reason": "",
                    "payment_status": "",
                    "note": "",
                    "by": _employee_display_name(employee),
                    "serial_numbers": [],
                    "movement_id": None,
                    "receipt_number": "",
                    "receipt_status": "",
                    "trade": "",
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


def _stock_out_reason_label(line):
    from .models import StockOutReason

    reason = (getattr(line, "reason", None) or "").strip()
    if not reason:
        return ""
    if reason == StockOutReason.CUSTOM:
        detail = (getattr(line, "note", None) or "").strip()
        return detail or "Custom"
    return line.get_reason_display()


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
        reason = _stock_out_reason_label(line) or "—"
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


MOVEMENT_EVENT_FILTERS = frozenset({"all", "in", "out", "sale", "transfer", "return"})

MOVEMENT_EVENT_FILTER_TYPES = {
    "in": frozenset({"in"}),
    "out": frozenset({"out"}),
    "sale": frozenset({"sale"}),
    "transfer": frozenset({"transfer_fulfilled"}),
    "return": frozenset({"return"}),
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
    if event_filter not in ("sale", "return"):
        return [event for event in events if event.get("event_type") in allowed]

    # Keep sale + return companions on the same receipt so they stay together.
    matching_receipts = {
        (event.get("receipt_number") or "").strip()
        for event in events
        if event.get("event_type") in allowed
        and (event.get("receipt_number") or "").strip()
    }
    companion_types = frozenset({"sale", "return"})
    filtered = []
    for event in events:
        event_type = event.get("event_type")
        if event_type in allowed:
            filtered.append(event)
            continue
        receipt = (event.get("receipt_number") or "").strip()
        if (
            receipt
            and receipt in matching_receipts
            and event_type in companion_types
        ):
            filtered.append(event)
    return filtered


def _filter_timeline_display_events(events):
    """Timeline: hide pending requests; show fulfilled transfers in and out."""
    return [
        event for event in events if event.get("event_type") != "request"
    ]


def _timeline_event_search_text(event):
    parts = [
        event.get("item_name") or "",
        event.get("item_category") or "",
        event.get("event_label") or "",
        event.get("from_label") or "",
        event.get("to_label") or "",
        event.get("by") or "",
        event.get("requested_by") or "",
        event.get("received_by") or "",
        event.get("receipt_number") or "",
        event.get("trade") or "",
        event.get("note") or "",
    ]
    for serial in event.get("serial_numbers") or []:
        parts.append(str(serial))
    return " ".join(parts).lower()


_RECEIPT_ACTIVITY_SORT = {
    "sale": 0,
    "return": 1,
}


def _group_timeline_events_by_receipt(events):
    """
    Collapse receipt-linked activities into one timeline group per receipt.

    Sales and later returns on the same receipt stay together (sorted under the
    earliest activity time). Events without a receipt number stay as single rows.
    """
    groups = []
    receipt_index = {}

    for event in events:
        receipt = (event.get("receipt_number") or "").strip()
        if not receipt:
            groups.append(
                {
                    "receipt_number": "",
                    "receipt_status": event.get("receipt_status") or "",
                    "happened_at": event["happened_at"],
                    "shop_name": event.get("shop_name") or "",
                    "is_receipt_group": False,
                    "activities": [event],
                    "search_text": _timeline_event_search_text(event),
                }
            )
            continue

        idx = receipt_index.get(receipt)
        if idx is None:
            receipt_index[receipt] = len(groups)
            groups.append(
                {
                    "receipt_number": receipt,
                    "receipt_status": event.get("receipt_status") or "",
                    "happened_at": event["happened_at"],
                    "shop_name": event.get("shop_name") or "",
                    "is_receipt_group": True,
                    "activities": [event],
                    "search_text": _timeline_event_search_text(event),
                }
            )
            continue

        group = groups[idx]
        group["activities"].append(event)
        group["search_text"] = (
            f"{group['search_text']} {_timeline_event_search_text(event)}".strip()
        )
        if event["happened_at"] < group["happened_at"]:
            group["happened_at"] = event["happened_at"]
        status = event.get("receipt_status") or ""
        if status:
            group["receipt_status"] = status

    for group in groups:
        activities = group["activities"]
        activities.sort(
            key=lambda row: (
                _RECEIPT_ACTIVITY_SORT.get(row.get("event_type"), 2),
                row["happened_at"],
                row.get("item_name") or "",
            )
        )
        labels = []
        seen_labels = set()
        for activity in activities:
            label = activity.get("event_label") or ""
            event_type = activity.get("event_type") or "all"
            if label and label not in seen_labels:
                seen_labels.add(label)
                labels.append({"label": label, "event_type": event_type})
        group["activity_labels"] = labels
        group["is_multi"] = len(activities) > 1

    return groups


def _filter_item_summary_movement_events(events):
    """Item summary: same as timeline — fulfilled transfers only, not pending requests."""
    return _filter_timeline_display_events(events)


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
    units_return = sum(
        event["quantity"] for event in events if event.get("event_type") == "return"
    )
    return units_in, units_out, units_request, units_sale, units_transfer_in, units_transfer_out, units_return


MOVEMENT_VIEW_BY = frozenset({"timeline", "item"})
REPORT_VIEW_BY = frozenset({"item", "day"})


def _parse_movement_view_by(raw):
    view_by = (raw or "timeline").strip().lower()
    if view_by not in MOVEMENT_VIEW_BY:
        return "timeline"
    return view_by


def _parse_report_view_by(raw, *, range_type="day"):
    """Report page view: period item summary, or daily rows for multi-day ranges."""
    view_by = (raw or "item").strip().lower()
    if view_by not in REPORT_VIEW_BY:
        view_by = "item"
    if range_type == "day":
        return "item"
    return view_by


def _blank_movement_item_row(
    *,
    item_id,
    item_name,
    item_category,
    happened_at,
    shop_id=None,
    shop_name="",
):
    return {
        "item_id": item_id,
        "item_name": item_name,
        "item_category": item_category,
        "shop_id": shop_id,
        "shop_name": shop_name,
        "is_item_start": False,
        "is_item_total": False,
        "event_count": 0,
        "units_in": 0,
        "units_out": 0,
        "units_transfer_in": 0,
        "units_transfer_out": 0,
        "units_sale": 0,
        "units_return": 0,
        "units_trade": 0,
        "current_stock": 0,
        "last_at": happened_at,
        "detail_url": "",
    }


def _apply_movement_event_to_row(row, event, *, transfer_only=""):
    quantity = int(event.get("quantity") or 0)
    row["event_count"] += 1
    event_type = event.get("event_type")
    if transfer_only == "in":
        row["units_transfer_in"] += quantity
    elif transfer_only == "out":
        row["units_transfer_out"] += quantity
    elif event_type == "in":
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
    elif event_type == "return":
        row["units_return"] += quantity
    if (event.get("trade") or "").strip():
        row["units_trade"] += quantity
    if row["last_at"] is None or event["happened_at"] > row["last_at"]:
        row["last_at"] = event["happened_at"]


def _group_movement_events_by_item(
    events,
    shop_ids,
    *,
    shops_by_id=None,
    group_by_shop=None,
    extra_items=None,
    require_events=False,
):
    if group_by_shop is None:
        group_by_shop = len(shop_ids) > 1
    shops_by_id = shops_by_id or {}
    shop_id_set = set(shop_ids)

    if not group_by_shop:
        groups = {}
        for event in events:
            item_id = event.get("item_id")
            item_name = event.get("item_name") or "—"
            item_category = event.get("item_category") or ""
            key = item_id if item_id is not None else f"name:{item_name.strip().lower()}"
            row = groups.get(key)
            if row is None:
                row = _blank_movement_item_row(
                    item_id=item_id,
                    item_name=item_name,
                    item_category=item_category,
                    happened_at=event["happened_at"],
                )
                groups[key] = row
            _apply_movement_event_to_row(row, event)
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

    if not shops_by_id and shop_ids:
        from shops.models import Shop

        shops_by_id = {
            shop.pk: shop for shop in Shop.objects.filter(pk__in=shop_ids)
        }

    ordered_shop_ids = sorted(
        shop_ids,
        key=lambda shop_id: (
            (getattr(shops_by_id.get(shop_id), "name", None) or "").lower(),
            shop_id,
        ),
    )
    items = {}
    for event in events:
        item_id = event.get("item_id")
        item_name = event.get("item_name") or "—"
        item_category = event.get("item_category") or ""
        item_key = item_id if item_id is not None else f"name:{item_name.strip().lower()}"
        group = items.get(item_key)
        if group is None:
            group = {
                "item_id": item_id,
                "item_name": item_name,
                "item_category": item_category,
                "shops": {},
            }
            items[item_key] = group

        event_type = event.get("event_type")
        targets = []
        if event_type in ("request", "transfer_fulfilled"):
            direction = event.get("transfer_direction")
            dest_id = event.get("shop_id")
            source_id = event.get("source_shop_id")
            if direction in ("in", "both") and dest_id in shop_id_set:
                targets.append((dest_id, "in"))
            if direction in ("out", "both") and source_id in shop_id_set:
                targets.append((source_id, "out"))
            if not targets and dest_id in shop_id_set:
                targets.append((dest_id, ""))
        else:
            dest_id = event.get("shop_id")
            if dest_id in shop_id_set:
                targets.append((dest_id, ""))

        for shop_id, transfer_only in targets:
            row = group["shops"].get(shop_id)
            if row is None:
                shop = shops_by_id.get(shop_id)
                row = _blank_movement_item_row(
                    item_id=item_id,
                    item_name=item_name,
                    item_category=item_category,
                    happened_at=event["happened_at"],
                    shop_id=shop_id,
                    shop_name=shop.name if shop is not None else "—",
                )
                group["shops"][shop_id] = row
            _apply_movement_event_to_row(row, event, transfer_only=transfer_only)

    event_item_ids = [
        group["item_id"] for group in items.values() if group.get("item_id")
    ]
    extra_item_ids = []
    if extra_items and not require_events:
        extra_item_ids = [item.pk for item in extra_items if getattr(item, "pk", None)]
    stock_item_ids = list({*event_item_ids, *extra_item_ids})
    stock_by_shop = _current_stock_by_item_shop(stock_item_ids, shop_ids)
    if extra_items and not require_events:
        for item in extra_items:
            if item.pk in items:
                continue
            if not any(
                stock_by_shop.get((item.pk, shop_id), 0) for shop_id in ordered_shop_ids
            ):
                continue
            items[item.pk] = {
                "item_id": item.pk,
                "item_name": item.name,
                "item_category": item.category,
                "shops": {},
            }

    rows = []
    for group in sorted(
        items.values(),
        key=lambda row: (row["item_name"].lower(), row.get("item_id") or 0),
    ):
        shop_rows = []
        for shop_id in ordered_shop_ids:
            row = group["shops"].get(shop_id)
            if row is None:
                if require_events:
                    continue
                shop = shops_by_id.get(shop_id)
                row = _blank_movement_item_row(
                    item_id=group["item_id"],
                    item_name=group["item_name"],
                    item_category=group["item_category"],
                    happened_at=None,
                    shop_id=shop_id,
                    shop_name=shop.name if shop is not None else "—",
                )
            if group["item_id"]:
                row["current_stock"] = stock_by_shop.get(
                    (group["item_id"], shop_id), 0
                )
            shop_rows.append(row)
        if require_events:
            if not any(row["event_count"] for row in shop_rows):
                continue
        elif not any(
            row["event_count"] or row["current_stock"] for row in shop_rows
        ):
            continue
        shop_rows[0]["is_item_start"] = True
        rows.extend(shop_rows)
        last_times = [row["last_at"] for row in shop_rows if row.get("last_at")]
        total = _blank_movement_item_row(
            item_id=group["item_id"],
            item_name=group["item_name"],
            item_category=group["item_category"],
            happened_at=max(last_times) if last_times else None,
            shop_name="Total",
        )
        total["is_item_total"] = True
        total["event_count"] = sum(row["event_count"] for row in shop_rows)
        total["units_in"] = sum(row["units_in"] for row in shop_rows)
        total["units_out"] = sum(row["units_out"] for row in shop_rows)
        total["units_transfer_in"] = sum(row["units_transfer_in"] for row in shop_rows)
        total["units_transfer_out"] = sum(row["units_transfer_out"] for row in shop_rows)
        total["units_sale"] = sum(row["units_sale"] for row in shop_rows)
        total["units_return"] = sum(row["units_return"] for row in shop_rows)
        total["units_trade"] = sum(row.get("units_trade", 0) for row in shop_rows)
        total["current_stock"] = sum(row["current_stock"] for row in shop_rows)
        rows.append(total)
    return rows


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
    search_q="",
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
    if (item_mode or "all") == "all" and (search_q or "").strip():
        params["q"] = (search_q or "").strip()
    params.update(overrides)
    return {
        key: value
        for key, value in params.items()
        if value not in (None, "")
    }


def _normalize_report_search_q(raw):
    return (raw or "").strip()


def _filter_item_report_rows_by_search(rows, query):
    """Keep item report rows whose name/category/shop match q (whole item groups)."""
    q = (query or "").strip().lower()
    if not q or not rows:
        return list(rows or [])

    matched_ids = set()
    for row in rows:
        item = row.get("item")
        hay = " ".join(
            [
                getattr(item, "name", "") or "",
                getattr(item, "category", "") or "",
                row.get("shop_name") or "",
            ]
        ).lower()
        if q in hay:
            item_id = getattr(item, "pk", None)
            if item_id is not None:
                matched_ids.add(item_id)

    if not matched_ids:
        return []

    return [
        row
        for row in rows
        if getattr(row.get("item"), "pk", None) in matched_ids
    ]


def _filter_movement_item_rows_by_search(rows, query):
    """Keep movement item-summary rows matching q (whole item groups)."""
    q = (query or "").strip().lower()
    if not q or not rows:
        return list(rows or [])

    matched_keys = set()
    for row in rows:
        hay = " ".join(
            [
                row.get("item_name") or "",
                row.get("item_category") or "",
                row.get("shop_name") or "",
            ]
        ).lower()
        if q in hay:
            item_id = row.get("item_id")
            matched_keys.add(
                item_id
                if item_id is not None
                else f"name:{(row.get('item_name') or '').lower()}"
            )

    if not matched_keys:
        return []

    kept = []
    for row in rows:
        item_id = row.get("item_id")
        key = (
            item_id
            if item_id is not None
            else f"name:{(row.get('item_name') or '').lower()}"
        )
        if key in matched_keys:
            kept.append(row)
    return kept


def _filter_movement_events_by_search(events, query):
    q = (query or "").strip().lower()
    if not q or not events:
        return list(events or [])
    return [
        event
        for event in events
        if q in _timeline_event_search_text(event)
    ]


def _stock_report_download_filename(
    page_mode, filter_context, *, event_filter="all", report_kind="actual"
):
    import re

    from django.utils import timezone as dj_timezone

    label = (filter_context.get("report_period_label") or "export").strip()
    safe_period = re.sub(r"[^\w\-]+", "-", label, flags=re.UNICODE).strip("-").lower()
    safe_period = safe_period or "export"
    type_slug = {
        "in": "stock-in",
        "out": "stock-out",
        "sale": "sale",
        "transfer": "transfer",
        "return": "return",
        "all": "all",
    }.get(event_filter or "all", "all")
    kind_slug = "audit" if report_kind == "audit" else "actual"
    stamp = dj_timezone.localtime(dj_timezone.now()).strftime("%Y-%m-%d-%H%M")
    prefix = "stock-movements" if page_mode == "movements" else "stock-report"
    if page_mode == "movements" and type_slug != "all":
        return f"{prefix}-{kind_slug}-{safe_period}-{type_slug}-{stamp}.pdf"
    return f"{prefix}-{kind_slug}-{safe_period}-{stamp}.pdf"


def _format_movement_csv_when(value):
    if value is None:
        return ""
    from django.utils import timezone as dj_timezone
    from django.utils.formats import date_format

    local = dj_timezone.localtime(value) if dj_timezone.is_aware(value) else value
    return date_format(local, "d M Y H:i")


def _format_movement_pdf_date_time(value):
    if value is None:
        return "", ""
    from django.utils import timezone as dj_timezone

    local = dj_timezone.localtime(value) if dj_timezone.is_aware(value) else value
    return local.strftime("%d %b %Y"), local.strftime("%H:%M")


def _parse_report_kind(raw):
    kind = (raw or "actual").strip().lower()
    return "audit" if kind == "audit" else "actual"


def _movement_event_status_label(event):
    from shops.models import ShopReceiptStatus

    status = (event.get("receipt_status") or "").strip()
    if status:
        try:
            return ShopReceiptStatus(status).label
        except ValueError:
            return status.replace("_", " ").title()
    pay = (event.get("payment_status") or event.get("pay") or "").strip()
    if pay and pay != "—":
        return pay
    return "—"


def _movement_event_seller_label(event):
    seller = (event.get("seller") or "").strip()
    if seller and seller != "—":
        return seller
    by = (event.get("by") or "").strip()
    if by and by != "—":
        return by
    received = (event.get("received_by") or "").strip()
    if received and received != "—":
        return received
    requested = (event.get("requested_by") or "").strip()
    if requested and requested != "—":
        return requested
    return "—"


def _movement_event_note_label(event):
    note = (event.get("note") or "").strip()
    if note:
        return note
    reason = (event.get("reason") or "").strip()
    if reason:
        return reason
    trade = (event.get("trade") or "").strip()
    if trade:
        return trade
    return "—"


def _movement_event_stock_delta(event):
    qty = int(event.get("quantity") or 0)
    event_type = (event.get("event_type") or "").strip()
    direction = (event.get("transfer_direction") or "").strip()
    if event_type in ("in", "return"):
        return qty
    if event_type in ("out", "sale"):
        return -qty
    if event_type in ("transfer_fulfilled", "transfer", "request"):
        if direction == "in":
            return qty
        if direction == "out":
            return -qty
        return 0
    return 0


def _movement_event_shop_stock_key(event):
    item_id = event.get("item_id")
    if not item_id:
        return None
    direction = (event.get("transfer_direction") or "").strip()
    if direction == "out" and event.get("source_shop_id"):
        return (item_id, event.get("source_shop_id"))
    shop_id = event.get("shop_id")
    if not shop_id:
        return None
    return (item_id, shop_id)


def _audit_shop_qty_after_events(events, *, shop_ids, day_end):
    """
    Map each event object id -> shop on-hand qty after that event.

    Rewinds current stock through after-period aggregates, then reverse-walks
    the provided events (newest first) so each row gets a post-event balance.
    """
    from datetime import timedelta

    from django.utils import timezone as dj_timezone

    qty_by_event = {}
    if not events:
        return qty_by_event

    item_ids = []
    seen_items = set()
    shop_ids_used = []
    seen_shops = set()
    for event in events:
        item_id = event.get("item_id")
        if item_id and item_id not in seen_items:
            seen_items.add(item_id)
            item_ids.append(item_id)
        key = _movement_event_shop_stock_key(event)
        if key:
            shop_id = key[1]
            if shop_id and shop_id not in seen_shops:
                seen_shops.add(shop_id)
                shop_ids_used.append(shop_id)
    active_shop_ids = shop_ids_used or list(shop_ids or [])
    if not item_ids or not active_shop_ids:
        for event in events:
            qty_by_event[id(event)] = 0
        return qty_by_event

    now = dj_timezone.now()
    far_future = now + timedelta(days=3650)
    current = _current_stock_by_item_shop(item_ids, active_shop_ids)
    balances = dict(current)

    if day_end is not None and day_end < now:
        after_moves = _movement_qty_by_item_shop(
            item_ids, active_shop_ids, day_end, far_future
        )
        after_transfers = _transfer_qty_by_item_shop(
            item_ids, active_shop_ids, day_end, far_future
        )
        after_sales = _receipt_sale_qty_by_item_shop(
            item_ids, active_shop_ids, day_end, far_future
        )
        after_returns = _return_qty_by_item_shop(
            item_ids, active_shop_ids, day_end, far_future
        )
        for key in list(balances.keys()):
            after = after_moves.get(key) or {"in": 0, "out": 0}
            after_transfer = after_transfers.get(key) or {"in": 0, "out": 0}
            _starting, closing = _item_report_closing_starting(
                current=current.get(key) or 0,
                after_in=after["in"],
                after_out=after["out"],
                after_sale=after_sales.get(key) or 0,
                after_transfer_in=after_transfer["in"],
                after_transfer_out=after_transfer["out"],
                after_return=after_returns.get(key) or 0,
                stock_in=0,
                stock_out=0,
                stock_sale=0,
                stock_transfer_in=0,
                stock_transfer_out=0,
                stock_return=0,
            )
            balances[key] = closing

    sorted_events = sorted(
        events,
        key=lambda event: (
            event.get("happened_at") or "",
            (event.get("item_name") or "").lower(),
            event.get("event_type") or "",
            event.get("movement_id") or 0,
        ),
        reverse=True,
    )
    for event in sorted_events:
        key = _movement_event_shop_stock_key(event)
        if not key:
            qty_by_event[id(event)] = 0
            continue
        after_qty = int(balances.get(key) or 0)
        qty_by_event[id(event)] = after_qty
        balances[key] = after_qty - _movement_event_stock_delta(event)
    return qty_by_event


def _movement_event_filter_label(event_filter):
    return {
        "all": "All movements",
        "in": "Stock in",
        "out": "Stock out",
        "sale": "Sale",
        "transfer": "Transfer",
        "return": "Return",
    }.get(event_filter or "all", "All movements")


def _movement_summary_qty_label(event_filter):
    return {
        "all": "Qty",
        "in": "Stocked in",
        "out": "Stocked out",
        "sale": "Sold",
        "transfer": "Transferred",
        "return": "Returned",
    }.get(event_filter or "all", "Qty")


def _event_matches_summary_filter(event, event_filter):
    allowed = MOVEMENT_EVENT_FILTER_TYPES.get(event_filter or "all")
    if not allowed:
        return True
    return event.get("event_type") in allowed


def _item_qty_summary_rows(events, event_filter="all"):
    """Per-item quantity totals for the active movement type."""
    totals = {}
    for event in events:
        if not _event_matches_summary_filter(event, event_filter):
            continue
        name = (event.get("item_name") or "—").strip() or "—"
        category = (event.get("item_category") or "").strip()
        item_id = event.get("item_id")
        key = (name.lower(), category.lower(), name, category)
        qty = int(event.get("quantity") or 0)
        row = totals.get(key)
        if row is None:
            totals[key] = {
                "item_id": item_id,
                "item_name": name,
                "item_category": category,
                "quantity": qty,
            }
        else:
            row["quantity"] += qty
            if row.get("item_id") is None and item_id is not None:
                row["item_id"] = item_id

    rows = sorted(
        totals.values(),
        key=lambda row: (row["item_name"].lower(), row["item_category"].lower()),
    )
    return rows


def _item_qty_summary_by_type_rows(events):
    """When type=all, show each item with In / Out / Transfer / Sale / Return."""
    blanks = {
        "in": 0,
        "out": 0,
        "transfer": 0,
        "sale": 0,
        "return": 0,
    }
    type_map = {
        "in": "in",
        "out": "out",
        "transfer_fulfilled": "transfer",
        "request": "transfer",
        "sale": "sale",
        "return": "return",
    }
    totals = {}
    for event in events:
        bucket = type_map.get(event.get("event_type"))
        if not bucket:
            continue
        name = (event.get("item_name") or "—").strip() or "—"
        category = (event.get("item_category") or "").strip()
        item_id = event.get("item_id")
        key = (name.lower(), category.lower(), name, category)
        row = totals.get(key)
        if row is None:
            row = {
                "item_id": item_id,
                "item_name": name,
                "item_category": category,
                **blanks,
            }
            totals[key] = row
        row[bucket] += int(event.get("quantity") or 0)
        if row.get("item_id") is None and item_id is not None:
            row["item_id"] = item_id

    return sorted(
        totals.values(),
        key=lambda row: (row["item_name"].lower(), row["item_category"].lower()),
    )


def _summary_qty_for_estimate(row, event_filter="all"):
    """Quantity used for estimated buying value on the summary report."""
    if event_filter == "all":
        # Value of units that left stock (out + sold); returns are not added back.
        return int(row.get("out") or 0) + int(row.get("sale") or 0)
    return int(row.get("quantity") or 0)


def _format_money_pdf(value):
    from decimal import Decimal, ROUND_HALF_UP

    try:
        amount = Decimal(str(value or 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except Exception:
        amount = Decimal("0.00")
    return f"{amount:,.2f}"


def _buying_prices_for_summary(rows, shop_ids=None):
    from .services import session_average_buying_prices_for_items

    item_ids = [
        row.get("item_id")
        for row in rows
        if row.get("item_id") is not None
    ]
    return session_average_buying_prices_for_items(item_ids, shop_ids=shop_ids)


def _stock_report_pdf_response(*, filename, pdf_bytes):
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def _build_audit_detail_rows(events, *, shop_ids, day_end):
    """Audit PDF rows: When, Type, Item, Reason, Qty, Actual qty, Missing, Excess, Note."""
    detail_events = sorted(
        events or [],
        key=lambda event: (
            event.get("happened_at") or "",
            (event.get("item_name") or "").lower(),
            event.get("event_type") or "",
        ),
    )
    headers = [
        "When",
        "Type",
        "Item",
        "Reason",
        "Qty",
        "Actual qty",
        "Missing",
        "Excess",
        "Note",
    ]
    rows = []
    for event in detail_events:
        date_value, time_value = _format_movement_pdf_date_time(
            event.get("happened_at")
        )
        when = f"{date_value} {time_value}".strip()
        rows.append(
            [
                when,
                event.get("event_label") or event.get("event_type") or "",
                event.get("item_name") or "—",
                _movement_event_note_label(event),
                event.get("quantity") or 0,
                "",
                "",
                "",
                "",
            ]
        )
    return headers, rows


def _stock_report_download(
    *,
    page_mode,
    filter_context,
    is_item_movement_summary,
    movement_events,
    movement_item_rows,
    movement_item_group_by_shop,
    item_report_rows,
    item_report_group_by_shop,
    event_filter="all",
    view_by="timeline",
    report_kind="actual",
    day_end=None,
    company_name="MY-SHOP",
    shop_label="",
    logo_path="",
    company_phone="",
    company_email="",
    company_location="",
    shop_ids=None,
):
    from decimal import Decimal

    from django.utils import timezone as dj_timezone

    report_kind = _parse_report_kind(report_kind)
    filename = _stock_report_download_filename(
        page_mode,
        filter_context,
        event_filter=event_filter,
        report_kind=report_kind,
    )
    period_label = (filter_context.get("report_period_label") or "").strip() or "—"
    event_label = _movement_event_filter_label(event_filter)
    if page_mode == "report":
        view_label = "Daily breakdown" if view_by == "day" else "Period summary"
    else:
        view_label = "Item summary" if view_by == "item" else "Timeline"
    generated_at = dj_timezone.localtime(dj_timezone.now())

    summary_headers = None
    summary_rows = None
    detail_rows = None
    detail_headers = None
    item_summary_headers = None
    item_summary_rows = None
    report_headers = None
    report_rows = None
    qty_label = _movement_summary_qty_label(event_filter)

    # Audit downloads are a separate trail document — activity rows only.
    if report_kind == "audit" and page_mode == "movements":
        detail_headers, detail_rows = _build_audit_detail_rows(
            movement_events, shop_ids=shop_ids, day_end=day_end
        )
        pdf_bytes = build_stock_report_pdf(
            company_name=company_name,
            page_mode=page_mode,
            period_label=period_label,
            event_filter=event_filter,
            event_filter_label=event_label,
            view_by=view_by,
            view_label=view_label,
            shop_label=shop_label,
            report_kind="audit",
            generated_at=generated_at,
            logo_path=logo_path,
            company_phone=company_phone,
            company_email=company_email,
            company_location=company_location,
            summary_rows=None,
            summary_headers=None,
            summary_qty_label=qty_label,
            detail_rows=detail_rows,
            detail_headers=detail_headers,
            item_summary_rows=None,
            item_summary_headers=None,
            report_rows=None,
            report_headers=None,
        )
        return _stock_report_pdf_response(filename=filename, pdf_bytes=pdf_bytes)

    if page_mode == "movements" and is_item_movement_summary:
        item_summary_headers = ["Item", "Category"]
        if movement_item_group_by_shop:
            item_summary_headers.append("Shop")
        item_summary_headers.extend(
            [
                "Stock",
                "Events",
                "In",
                "Out",
                "T-in",
                "T-out",
                "Trade",
                "Sale",
                "Return",
                "Last date",
                "Last time",
            ]
        )
        item_summary_rows = []
        sorted_items = sorted(
            [row for row in movement_item_rows if not row.get("is_item_total")],
            key=lambda row: (
                (row.get("item_name") or "").lower(),
                (row.get("shop_name") or "").lower(),
                row.get("last_at") or "",
            ),
        )
        for row in sorted_items:
            last_date, last_time = _format_movement_pdf_date_time(row.get("last_at"))
            line = [
                row.get("item_name") or "",
                row.get("item_category") or "",
            ]
            if movement_item_group_by_shop:
                line.append(row.get("shop_name") or "")
            line.extend(
                [
                    row.get("current_stock") or 0,
                    row.get("event_count") or 0,
                    row.get("units_in") or 0,
                    row.get("units_out") or 0,
                    row.get("units_transfer_in") or 0,
                    row.get("units_transfer_out") or 0,
                    row.get("units_trade") or 0,
                    row.get("units_sale") or 0,
                    row.get("units_return") or 0,
                    last_date,
                    last_time,
                ]
            )
            item_summary_rows.append(line)
    elif page_mode == "movements":
        if event_filter == "all":
            typed = _item_qty_summary_by_type_rows(movement_events)
            buy_prices = _buying_prices_for_summary(typed, shop_ids=shop_ids)
            summary_headers = [
                "Item",
                "Category",
                "Stocked in",
                "Stocked out",
                "Transferred",
                "Sold",
                "Returned",
                "Buy price",
                "Est. value",
            ]
            summary_rows = []
            totals = {
                "in": 0,
                "out": 0,
                "transfer": 0,
                "sale": 0,
                "return": 0,
            }
            total_value = Decimal("0.00")
            for row in typed:
                qty_for_value = _summary_qty_for_estimate(row, "all")
                unit_buy = buy_prices.get(row.get("item_id")) or Decimal("0")
                est_value = (unit_buy * Decimal(qty_for_value)).quantize(
                    Decimal("0.01")
                )
                total_value += est_value
                summary_rows.append(
                    [
                        row["item_name"],
                        row["item_category"],
                        row["in"],
                        row["out"],
                        row["transfer"],
                        row["sale"],
                        row["return"],
                        _format_money_pdf(unit_buy),
                        _format_money_pdf(est_value),
                    ]
                )
                for key in totals:
                    totals[key] += row[key]
            if summary_rows:
                summary_rows.append(
                    [
                        "Total",
                        "",
                        totals["in"],
                        totals["out"],
                        totals["transfer"],
                        totals["sale"],
                        totals["return"],
                        "",
                        _format_money_pdf(total_value),
                    ]
                )
        else:
            typed = _item_qty_summary_rows(
                movement_events, event_filter=event_filter
            )
            buy_prices = _buying_prices_for_summary(typed, shop_ids=shop_ids)
            summary_headers = [
                "Item",
                "Category",
                qty_label,
                "Buy price",
                "Est. value",
            ]
            summary_rows = []
            total_qty = 0
            total_value = Decimal("0.00")
            for row in typed:
                qty = int(row.get("quantity") or 0)
                unit_buy = buy_prices.get(row.get("item_id")) or Decimal("0")
                est_value = (unit_buy * Decimal(qty)).quantize(Decimal("0.01"))
                total_qty += qty
                total_value += est_value
                summary_rows.append(
                    [
                        row["item_name"],
                        row["item_category"],
                        qty,
                        _format_money_pdf(unit_buy),
                        _format_money_pdf(est_value),
                    ]
                )
            if summary_rows:
                summary_rows.append(
                    [
                        "Total",
                        "",
                        total_qty,
                        "",
                        _format_money_pdf(total_value),
                    ]
                )

        detail_events = sorted(
            movement_events,
            key=lambda event: (
                event.get("happened_at") or "",
                (event.get("item_name") or "").lower(),
                event.get("event_type") or "",
            ),
        )
        detail_headers = [
            "When",
            "Type",
            "Item",
            "Qty",
            "Receipt",
            "From",
            "To",
            "Seller",
        ]
        detail_rows = []
        for event in detail_events:
            date_value, time_value = _format_movement_pdf_date_time(
                event.get("happened_at")
            )
            detail_rows.append(
                [
                    date_value,
                    time_value,
                    event.get("event_label") or event.get("event_type") or "",
                    event.get("item_name") or "",
                    event.get("quantity") or 0,
                    event.get("receipt_number") or "",
                    event.get("from_label") or "",
                    event.get("to_label") or "",
                    event.get("by") or "",
                ]
            )
    else:
        is_daily_pdf = view_by == "day" or any(
            row.get("is_daily_row") for row in (item_report_rows or [])
        )
        report_headers = []
        if is_daily_pdf:
            report_headers.append("Date")
        report_headers.extend(["Item", "Category"])
        if item_report_group_by_shop and not is_daily_pdf:
            report_headers.append("Shop")
        report_headers.extend(
            [
                "Starting",
                "In",
                "T-in",
                "Out",
                "T-out",
                "Trade",
                "Net sale",
                "Returned",
                "Closing",
            ]
        )
        report_rows = []
        sorted_report = sorted(
            [row for row in item_report_rows if not row.get("is_item_total")],
            key=lambda row: (
                (
                    getattr(row.get("item"), "name", None)
                    or row.get("item_name")
                    or ""
                ).lower(),
                row.get("report_date") or "",
                (row.get("shop_name") or "").lower(),
            ),
        )
        for row in sorted_report:
            item = row.get("item")
            line = []
            if is_daily_pdf:
                report_date = row.get("report_date")
                if report_date:
                    line.append(report_date.strftime("%d %b %Y"))
                else:
                    line.append("")
            line.extend(
                [
                    getattr(item, "name", None) or row.get("item_name") or "",
                    getattr(item, "category", None) or row.get("item_category") or "",
                ]
            )
            if item_report_group_by_shop and not is_daily_pdf:
                line.append(row.get("shop_name") or "")
            line.extend(
                [
                    row.get("starting_stock") or 0,
                    row.get("stock_in") or 0,
                    row.get("stock_transfer_in") or 0,
                    row.get("stock_out") or 0,
                    row.get("stock_transfer_out") or 0,
                    row.get("stock_trade") or 0,
                    row.get("net_sale") or 0,
                    row.get("stock_return") or 0,
                    row.get("closing_stock") or 0,
                ]
            )
            report_rows.append(line)

    pdf_bytes = build_stock_report_pdf(
        company_name=company_name,
        page_mode=page_mode,
        period_label=period_label,
        event_filter=event_filter,
        event_filter_label=event_label,
        view_by=view_by,
        view_label=view_label,
        shop_label=shop_label,
        report_kind=report_kind,
        generated_at=generated_at,
        logo_path=logo_path,
        company_phone=company_phone,
        company_email=company_email,
        company_location=company_location,
        summary_rows=summary_rows,
        summary_headers=summary_headers,
        summary_qty_label=qty_label,
        detail_rows=detail_rows,
        detail_headers=detail_headers,
        item_summary_rows=item_summary_rows,
        item_summary_headers=item_summary_headers,
        report_rows=report_rows,
        report_headers=report_headers,
    )
    return _stock_report_pdf_response(filename=filename, pdf_bytes=pdf_bytes)


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

    search_q = _normalize_report_search_q(request.GET.get("q"))
    if item_mode != "all":
        search_q = ""

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

    # Lazy item-picker catalog: avoid embedding every item on every page load.
    if (request.GET.get("item_picker") or "").strip() == "1":
        from django.http import JsonResponse

        picker_items = list(
            Item.objects.order_by("category", "name").values("id", "name", "category")
        )
        return JsonResponse({"items": picker_items})

    selected_item_ids_raw = _parse_id_list(request.GET.getlist("item_id"))
    # Incomplete category/item picks fall back to All (the default).
    if item_mode == "category" and not selected_categories:
        item_mode = "all"
    elif item_mode == "items" and not selected_item_ids_raw:
        item_mode = "all"

    is_movements = page_mode == "movements"
    event_filter = _parse_movement_event_filter(
        request.GET.get("event_type") if is_movements else "all"
    )
    if is_movements:
        view_by = _parse_movement_view_by(request.GET.get("view_by"))
    else:
        view_by = _parse_report_view_by(
            request.GET.get("view_by"), range_type=range_type
        )

    # Only load the full catalog when the item picker or POS sale matching needs it.
    need_full_item_catalog = item_mode == "items" or (
        is_movements and event_filter in ("all", "sale") and item_mode == "all"
    )
    if need_full_item_catalog:
        all_items = list(
            Item.objects.order_by("category", "name").only("id", "name", "category")
        )
        items_by_id = {item.pk: item for item in all_items}
        filter_items_json = json.dumps(
            [
                {"id": item.pk, "name": item.name, "category": item.category}
                for item in all_items
            ]
        )
    else:
        all_items = []
        items_by_id = {}
        filter_items_json = "[]"
        if selected_item_ids_raw:
            selected_only = list(
                Item.objects.filter(pk__in=selected_item_ids_raw)
                .only("id", "name", "category")
                .order_by("category", "name")
            )
            items_by_id = {item.pk: item for item in selected_only}

    selected_item_ids = [
        pk for pk in selected_item_ids_raw if pk in items_by_id
    ]
    if item_mode == "items" and not selected_item_ids:
        item_mode = "all"

    item_qs = Item.objects.order_by("category", "name")
    if item_mode == "category":
        item_qs = item_qs.filter(category__in=selected_categories)
    elif item_mode == "items":
        item_qs = item_qs.filter(pk__in=selected_item_ids)

    if is_movements and item_mode == "all" and event_filter not in ("all", "sale"):
        # Transfer/in/out/return filters don't need the full item list for matching.
        report_items = []
    elif item_mode == "all" and need_full_item_catalog:
        report_items = all_items
    else:
        report_items = list(
            item_qs.only("id", "name", "category", "is_suspended")
        )
    selected_filter_items = [
        items_by_id[pk] for pk in selected_item_ids if pk in items_by_id
    ]

    no_shop_access = (
        profile.role in SHOP_ASSIGNABLE_ROLES and not filter_shops
    )
    shop_ids_for_query = [] if no_shop_access else active_shop_ids

    movement_events = []
    movement_event_groups = []
    units_in = 0
    units_out = 0
    units_request = 0
    units_transfer_in = 0
    units_transfer_out = 0
    units_sale = 0
    units_return = 0
    item_report_rows = []
    is_daily_report = False
    item_report_show_item_col = True
    totals = {
        "starting_stock": 0,
        "stock_in": 0,
        "stock_transfer_in": 0,
        "stock_out": 0,
        "stock_transfer_out": 0,
        "stock_sale": 0,
        "stock_return": 0,
        "stock_trade": 0,
        "net_sale": 0,
        "closing_stock": 0,
    }

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
    movement_item_totals = {
        "current_stock": 0,
        "event_count": 0,
        "units_in": 0,
        "units_out": 0,
        "units_transfer_in": 0,
        "units_transfer_out": 0,
        "units_sale": 0,
        "units_return": 0,
        "units_trade": 0,
    }

    timeline_page = 1
    timeline_page_size = 100
    timeline_total_groups = 0
    timeline_total_pages = 1
    timeline_prev_url = ""
    timeline_next_url = ""
    report_page = 1
    report_page_size = 50
    report_total_groups = 0
    report_total_pages = 1
    report_prev_url = ""
    report_next_url = ""

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
            report_items=report_items,
            event_filter=event_filter,
        )
        if event_filter != "all":
            movement_events = _filter_movement_events(movement_events, event_filter)
        if view_by == "timeline":
            movement_events = _filter_timeline_display_events(movement_events)
            if search_q:
                movement_events = _filter_movement_events_by_search(
                    movement_events, search_q
                )
            movement_event_groups = _group_timeline_events_by_receipt(
                movement_events
            )
        elif is_item_movement_summary:
            movement_events = _filter_item_summary_movement_events(movement_events)
            if search_q:
                movement_events = _filter_movement_events_by_search(
                    movement_events, search_q
                )
        (
            units_in,
            units_out,
            units_request,
            units_sale,
            units_transfer_in,
            units_transfer_out,
            units_return,
        ) = _summarize_movement_events(movement_events)
        if is_item_movement_summary:
            movement_item_rows = _group_movement_events_by_item(
                movement_events,
                shop_ids_for_query,
                shops_by_id=shops_by_id,
                require_events=True,
            )
            if search_q:
                movement_item_rows = _filter_movement_item_rows_by_search(
                    movement_item_rows, search_q
                )
            for row in movement_item_rows:
                if row.get("is_item_total"):
                    continue
                movement_item_totals["current_stock"] += row["current_stock"]
                movement_item_totals["event_count"] += row["event_count"]
                movement_item_totals["units_in"] += row["units_in"]
                movement_item_totals["units_out"] += row["units_out"]
                movement_item_totals["units_transfer_in"] += row["units_transfer_in"]
                movement_item_totals["units_transfer_out"] += row["units_transfer_out"]
                movement_item_totals["units_sale"] += row["units_sale"]
                movement_item_totals["units_return"] += row["units_return"]
                movement_item_totals["units_trade"] += row.get("units_trade", 0)

        # Paginate timeline HTML only (PDF download keeps the full set).
        is_download = (request.GET.get("download") or "").strip() == "1"
        if view_by == "timeline" and movement_event_groups and not is_download:
            try:
                timeline_page = max(1, int(request.GET.get("page") or 1))
            except (TypeError, ValueError):
                timeline_page = 1
            timeline_total_groups = len(movement_event_groups)
            timeline_total_pages = max(
                1,
                (timeline_total_groups + timeline_page_size - 1)
                // timeline_page_size,
            )
            if timeline_page > timeline_total_pages:
                timeline_page = timeline_total_pages
            start = (timeline_page - 1) * timeline_page_size
            movement_event_groups = movement_event_groups[
                start : start + timeline_page_size
            ]
            timeline_prev_url, timeline_next_url = _report_page_urls(
                request, timeline_page, timeline_total_pages
            )
    else:
        is_daily_report = view_by == "day" and range_type != "day"
        items_for_report = report_items
        if search_q:
            items_for_report = _filter_items_by_search(report_items, search_q)
        if is_daily_report:
            item_report_rows = _build_item_report_daily_rows(
                items_for_report,
                shop_ids_for_query,
                day_start,
                day_end,
            )
        else:
            item_report_rows = _build_item_report_rows(
                items_for_report,
                shop_ids_for_query,
                day_start,
                day_end,
                shops_by_id=shops_by_id,
            )
        for row in item_report_rows:
            if row.get("is_item_total"):
                continue
            if is_daily_report:
                # Start/Close are chained per day — sum only movement columns.
                totals["stock_in"] += row["stock_in"]
                totals["stock_transfer_in"] += row["stock_transfer_in"]
                totals["stock_out"] += row["stock_out"]
                totals["stock_transfer_out"] += row["stock_transfer_out"]
                totals["stock_sale"] += row["stock_sale"]
                totals["stock_return"] += row["stock_return"]
                totals["stock_trade"] += row["stock_trade"]
                totals["net_sale"] += row["net_sale"]
            else:
                totals["starting_stock"] += row["starting_stock"]
                totals["stock_in"] += row["stock_in"]
                totals["stock_transfer_in"] += row["stock_transfer_in"]
                totals["stock_out"] += row["stock_out"]
                totals["stock_transfer_out"] += row["stock_transfer_out"]
                totals["stock_sale"] += row["stock_sale"]
                totals["stock_return"] += row["stock_return"]
                totals["stock_trade"] += row["stock_trade"]
                totals["net_sale"] += row["net_sale"]
                totals["closing_stock"] += row["closing_stock"]
        if is_daily_report and item_report_rows:
            item_ids_in_rows = {
                getattr(row.get("item"), "pk", None)
                for row in item_report_rows
                if not row.get("is_item_total")
            }
            item_report_show_item_col = len(item_ids_in_rows) > 1
            # Single-item daily: show first Start / last Close in the footer.
            if len(item_ids_in_rows) == 1:
                totals["starting_stock"] = item_report_rows[0]["starting_stock"]
                totals["closing_stock"] = item_report_rows[-1]["closing_stock"]
        elif is_daily_report:
            item_report_show_item_col = False
        units_in = totals["stock_in"]
        units_out = totals["stock_out"]
        units_request = totals["stock_transfer_in"] + totals["stock_transfer_out"]
        units_sale = totals["net_sale"]
        units_transfer_in = totals["stock_transfer_in"]
        units_transfer_out = totals["stock_transfer_out"]
        units_return = totals["stock_return"]

        # Paginate report HTML only (PDF download keeps the full set).
        is_download = (request.GET.get("download") or "").strip() == "1"
        if not is_download and item_report_rows:
            try:
                report_page = max(1, int(request.GET.get("page") or 1))
            except (TypeError, ValueError):
                report_page = 1
            group_by_shop = (not is_daily_report) and len(shop_ids_for_query) > 1
            (
                item_report_rows,
                report_total_groups,
                report_total_pages,
                report_page,
            ) = _paginate_item_report_groups(
                item_report_rows,
                report_page,
                report_page_size,
                group_by_shop=group_by_shop,
            )
            report_prev_url, report_next_url = _report_page_urls(
                request, report_page, report_total_pages
            )
        elif item_report_rows:
            if is_daily_report:
                report_total_groups = len(item_report_rows)
            else:
                report_total_groups = len(
                    {
                        getattr(row.get("item"), "pk", None)
                        for row in item_report_rows
                        if not row.get("is_item_total")
                    }
                )

    from employees.workspace import sidebar_for_stock_management, stock_management_url

    report_params = _movements_report_params(
        range_type=range_type,
        filter_context=filter_context,
        item_mode=item_mode,
        event_filter=event_filter,
        view_by=view_by,
        selected_shop_ids=selected_shop_ids,
        selected_categories=selected_categories,
        selected_item_ids=selected_item_ids,
        search_q=search_q,
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
            search_q=search_q,
        )
        movements_back_url = stock_management_url(
            profile.role, "movements", report_params=back_params
        )
    elif is_item_movement_summary:
        for row in movement_item_rows:
            if not row.get("item_id"):
                row["detail_url"] = ""
                continue
            shop_filter = selected_shop_ids
            if row.get("shop_id") and not row.get("is_item_total"):
                shop_filter = [row["shop_id"]]
            detail_params = _movements_report_params(
                range_type=range_type,
                filter_context=filter_context,
                item_mode="items",
                event_filter=event_filter,
                view_by="timeline",
                selected_shop_ids=shop_filter,
                selected_categories=selected_categories,
                selected_item_ids=[row["item_id"]],
                item_id=row["item_id"],
                search_q="",
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

    item_report_group_by_shop = (
        (not is_movements)
        and (not is_daily_report)
        and len(shop_ids_for_query) > 1
    )
    movement_item_group_by_shop = (
        is_item_movement_summary and len(shop_ids_for_query) > 1
    )

    if (request.GET.get("download") or "").strip() == "1":
        from shops.services import get_company_profile

        company = get_company_profile()
        company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
        company_phone = (getattr(company, "phone_number", None) or "").strip()
        company_email = (getattr(company, "email", None) or "").strip()
        company_location = (getattr(company, "location", None) or "").strip()
        logo_path = ""
        if getattr(company, "logo", None):
            try:
                logo_path = company.logo.path
            except Exception:
                logo_path = ""
        if selected_shop_ids:
            shop_label = ", ".join(
                shops_by_id[sid].name
                for sid in selected_shop_ids
                if sid in shops_by_id
            )
        elif len(filter_shops) == 1:
            shop_label = filter_shops[0].name
        else:
            shop_label = "All shops"
        report_kind = _parse_report_kind(request.GET.get("report_kind"))
        return _stock_report_download(
            page_mode=page_mode,
            filter_context=filter_context,
            is_item_movement_summary=is_item_movement_summary,
            movement_events=movement_events,
            movement_item_rows=movement_item_rows,
            movement_item_group_by_shop=movement_item_group_by_shop,
            item_report_rows=item_report_rows,
            item_report_group_by_shop=item_report_group_by_shop,
            event_filter=event_filter,
            view_by=view_by,
            report_kind=report_kind,
            day_end=day_end,
            company_name=company_name,
            shop_label=shop_label,
            logo_path=logo_path,
            company_phone=company_phone,
            company_email=company_email,
            company_location=company_location,
            shop_ids=shop_ids_for_query,
        )

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
            "movement_event_groups": movement_event_groups,
            "item_report_rows": item_report_rows,
            "item_report_totals": totals,
            "item_report_group_by_shop": item_report_group_by_shop,
            "item_report_show_item_col": item_report_show_item_col,
            "is_daily_report": is_daily_report,
            "movement_item_group_by_shop": movement_item_group_by_shop,
            "movement_count": len(movement_events),
            "item_count": len(item_report_rows),
            "units_in": units_in,
            "units_out": units_out,
            "units_request": units_request,
            "units_transfer_in": units_transfer_in,
            "units_transfer_out": units_transfer_out,
            "units_sale": units_sale,
            "units_return": units_return,
            "report_range_label": range_labels[range_type],
            "filter_shops": filter_shops,
            "selected_shop_ids": set(selected_shop_ids),
            "item_mode": item_mode,
            "categories": categories,
            "selected_categories": set(selected_categories),
            "filter_items_json": filter_items_json,
            "item_picker_url": (
                f"{request.path}?mode={page_mode}&item_picker=1"
                if is_movements or page_mode == "report"
                else ""
            ),
            "selected_filter_items": selected_filter_items,
            "selected_item_ids": set(selected_item_ids),
            "search_q": search_q,
            "event_filter": event_filter,
            "view_by": view_by,
            "report_kind": _parse_report_kind(request.GET.get("report_kind")),
            "timeline_page": timeline_page,
            "timeline_page_size": timeline_page_size,
            "timeline_total_groups": timeline_total_groups,
            "timeline_total_pages": timeline_total_pages,
            "timeline_prev_url": timeline_prev_url,
            "timeline_next_url": timeline_next_url,
            "report_page": report_page,
            "report_page_size": report_page_size,
            "report_total_groups": report_total_groups,
            "report_total_pages": report_total_pages,
            "report_prev_url": report_prev_url,
            "report_next_url": report_next_url,
            "is_item_movement_summary": is_item_movement_summary,
            "is_item_movement_detail": is_item_movement_detail,
            "movement_item_rows": movement_item_rows,
            "movement_item_count": len(movement_item_rows),
            "movement_item_totals": movement_item_totals,
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
    from employees.module_permissions import employee_may
    from employees.workspace import sidebar_for_stock_management, stock_management_url

    from .models import Item

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="low-stock",
        profile=profile,
    )
    can_edit = employee_may(profile, "stock-management", "low-stock")
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
        shops = actionable_shops_for_profile(profile)
        shops_by_id = {shop.pk: shop for shop in shops}

        def _load_item():
            try:
                parsed_id = int((request.POST.get("item_id") or "").strip())
            except (TypeError, ValueError):
                parsed_id = 0
            return Item.objects.filter(pk=parsed_id).first()

        if action == "save_low_stock":
            item = _load_item()
            if item is None:
                if wants_json:
                    return JsonResponse(
                        {"ok": False, "error": "Item not found."}, status=404
                    )
                messages.error(request, "Item not found.")
                return redirect(stock_management_url(profile.role, "low-stock"))

            if "notify" in request.POST:
                notify = (request.POST.get("notify") or "").strip() in (
                    "1",
                    "true",
                    "True",
                    "on",
                    "yes",
                )
                item.low_stock_notify = notify
                item.save(update_fields=["low_stock_notify", "updated_at"])

            if "threshold" in request.POST:
                try:
                    shop_id = int((request.POST.get("shop_id") or "").strip())
                except (TypeError, ValueError):
                    shop_id = 0
                shop = shops_by_id.get(shop_id)
                if shop is None:
                    if wants_json:
                        return JsonResponse(
                            {"ok": False, "error": "Shop not found."}, status=404
                        )
                    messages.error(request, "Shop not found.")
                    return redirect(stock_management_url(profile.role, "low-stock"))
                raw_threshold = (request.POST.get("threshold") or "").strip()
                if raw_threshold == "":
                    usage = _weekly_usage_avg_by_item_shop([item.pk], [shop.pk])
                    auto = _threshold_from_weekly_avg(
                        usage.get((item.pk, shop.pk), 0)
                    )
                    _set_shop_low_stock_threshold(item, shop, auto, manual=False)
                else:
                    try:
                        threshold = int(raw_threshold)
                    except (TypeError, ValueError):
                        if wants_json:
                            return JsonResponse(
                                {
                                    "ok": False,
                                    "error": "Threshold must be a whole number.",
                                },
                                status=400,
                            )
                        messages.error(request, "Threshold must be a whole number.")
                        return redirect(
                            stock_management_url(profile.role, "low-stock")
                        )
                    if threshold < 0:
                        threshold = 0
                    _set_shop_low_stock_threshold(item, shop, threshold, manual=True)

            if wants_json:
                return JsonResponse(_low_stock_json(item, shops, include_usage=True))
            messages.success(request, f"Low stock settings saved for {item.name}.")
            return redirect(stock_management_url(profile.role, "low-stock"))

        if action == "sync_low_stock":
            raw_item_id = (request.POST.get("item_id") or "").strip()
            if raw_item_id:
                item = _load_item()
                if item is None:
                    if wants_json:
                        return JsonResponse(
                            {"ok": False, "error": "Item not found."}, status=404
                        )
                    messages.error(request, "Item not found.")
                    return redirect(stock_management_url(profile.role, "low-stock"))
                items = [item]
            else:
                items = list(
                    Item.objects.only(*LOW_STOCK_ITEM_ONLY).order_by("category", "name")
                )
            _sync_shop_thresholds_from_usage(items, shops)
            if wants_json:
                rows, _, _ = _build_low_stock_rows(items, shops)
                return JsonResponse(
                    {
                        "ok": True,
                        "synced": len(items),
                        "items": _low_stock_payloads_from_rows(rows),
                    }
                )
            if len(items) == 1:
                messages.success(
                    request,
                    f"Alert at set from average for {items[0].name}.",
                )
            else:
                messages.success(
                    request,
                    f"Alert at set from average for {len(items)} items.",
                )
            return redirect(stock_management_url(profile.role, "low-stock"))

        if action == "notify_all_low_stock":
            from django.utils import timezone

            notify = (request.POST.get("notify") or "1").strip() in (
                "1",
                "true",
                "True",
                "on",
                "yes",
            )
            updated = Item.objects.update(
                low_stock_notify=notify,
                updated_at=timezone.now(),
            )
            if wants_json:
                return JsonResponse(
                    {"ok": True, "notify": notify, "updated": updated}
                )
            messages.success(
                request,
                "Alerts turned on for all items."
                if notify
                else "Alerts turned off for all items.",
            )
            return redirect(stock_management_url(profile.role, "low-stock"))

        if wants_json:
            return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect(stock_management_url(profile.role, "low-stock"))

    allocated_shops = actionable_shops_for_profile(profile)
    items = list(
        Item.objects.only(*LOW_STOCK_ITEM_ONLY).order_by("category", "name")
    )
    low_stock_items, notify_count, group_by_shop = _build_low_stock_rows(
        items, allocated_shops
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
            "low_stock_item_count": len(items),
            "low_stock_notify_count": notify_count,
            "low_stock_group_by_shop": group_by_shop,
            "low_stock_usage_weeks": LOW_STOCK_USAGE_WEEKS,
            "stock_settings_url": stock_management_url(profile.role, "settings"),
        },
    )


def stock_settings(request, profile, meta, module):
    """Configure which stock in/out/request fields are compulsory."""
    from employees.module_permissions import employee_may
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
    can_edit = employee_may(profile, "stock-management", "settings")
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
                    "hint": "Waste, transfer, display, supplier return, or custom",
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


@require_http_methods(["GET"])
def stock_request_audits(request, profile, meta, module):
    from urllib.parse import urlencode

    from django.http import JsonResponse

    from employees.models import EmployeeRole
    from employees.module_permissions import require_module_permission
    from employees.workspace import sidebar_for_stock_management, stock_management_url

    from .models import Item
    from .services import build_request_item_audit_rows

    denied = require_module_permission(request, profile, "stock-management", "request")
    if denied is not None:
        return denied

    if profile.role not in (
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.IT_SUPPORT,
    ):
        return _stock_redirect(request.path, "view")

    # Lazy item-picker catalog (same pattern as stock report/movements).
    if (request.GET.get("item_picker") or "").strip() == "1":
        picker_items = list(
            Item.objects.order_by("category", "name").values("id", "name", "category")
        )
        return JsonResponse({"items": picker_items})

    range_type, day_start, day_end, filter_context = _report_range_bounds(request)
    filter_shops = actionable_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}

    try:
        shop_filter_id = int(request.GET.get("shop_id") or 0)
    except (TypeError, ValueError):
        shop_filter_id = 0
    if shop_filter_id and shop_filter_id not in shops_by_id:
        shop_filter_id = 0

    status_filter = (request.GET.get("status") or "all").strip().lower()
    if status_filter not in ("all", "pending", "fulfilled", "declined", "approved"):
        status_filter = "all"

    selected_item_ids = _parse_id_list(request.GET.getlist("item_id"))
    selected_item_id = selected_item_ids[0] if selected_item_ids else 0
    selected_item = None
    if selected_item_id:
        selected_item = (
            Item.objects.filter(pk=selected_item_id).only("id", "name", "category").first()
        )
        if selected_item is None:
            selected_item_id = 0

    list_status = None if status_filter == "all" else status_filter
    audit_requests = list_stock_requests_for_profile(
        profile,
        status=list_status,
        start=day_start,
        end=day_end,
        shop_id=shop_filter_id or None,
        item_id=selected_item_id or None,
        limit=250,
    )

    item_audit_rows = []
    item_audit_summary = None
    if selected_item_id:
        item_audit_rows, audit_item, item_audit_summary = build_request_item_audit_rows(
            audit_requests, selected_item_id
        )
        if selected_item is None:
            selected_item = audit_item

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="request-audits",
        profile=profile,
    )

    range_labels = {
        "day": "Single day",
        "period": "Period",
        "month": "Month",
        "year": "Year",
    }
    summary = summarize_stock_requests_for_profile(profile)

    movements_url = ""
    if selected_item_id:
        mov_params = {
            "mode": "movements",
            "range": filter_context["report_range"],
            "view_by": "timeline",
            "event_type": "all",
            "item_mode": "items",
            "item_id": selected_item_id,
            "report_kind": "actual",
        }
        if shop_filter_id:
            mov_params["shop_id"] = shop_filter_id
        if filter_context["report_range"] == "day":
            mov_params["date"] = filter_context["report_date_value"]
        elif filter_context["report_range"] == "period":
            mov_params["date_from"] = filter_context["report_date_from"]
            mov_params["date_to"] = filter_context["report_date_to"]
        elif filter_context["report_range"] == "month":
            mov_params["month"] = filter_context["report_month_value"]
        elif filter_context["report_range"] == "year":
            mov_params["year"] = str(filter_context["report_year_value"])[:4]
        movements_url = f"{request.path}?{urlencode(mov_params)}"

    clear_item_params = request.GET.copy()
    if "item_id" in clear_item_params:
        clear_item_params.setlist("item_id", [])
    clear_item_url = f"{request.path}?{clear_item_params.urlencode()}"

    return render(
        request,
        "items/stock_request_audits.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "stock_mode": "request-audits",
            "audit_requests": audit_requests,
            "audit_request_count": len(audit_requests),
            "request_summary": summary,
            "filter_shops": filter_shops,
            "selected_shop_id": shop_filter_id,
            "status_filter": status_filter,
            "selected_item": selected_item,
            "selected_item_id": selected_item_id,
            "item_audit_rows": item_audit_rows,
            "item_audit_summary": item_audit_summary,
            "item_movements_url": movements_url,
            "clear_item_url": clear_item_url,
            "item_picker_url": f"{request.path}?mode=request-audits&item_picker=1",
            "filter_items_json": "[]",
            "report_range": filter_context["report_range"],
            "report_range_label": range_labels.get(
                filter_context["report_range"], "Single day"
            ),
            "report_period_label": filter_context.get("report_period_label", ""),
            "report_date_value": filter_context["report_date_value"],
            "report_date_from": filter_context["report_date_from"],
            "report_date_to": filter_context["report_date_to"],
            "report_month_value": filter_context["report_month_value"],
            "report_year_value": filter_context["report_year_value"],
            "stock_request_url": stock_management_url(profile.role, "request"),
        },
    )


@require_http_methods(["GET", "POST"])
def stock_management(request, profile, meta, module, page_sidebar):
    from employees.models import EmployeeRole
    from employees.module_permissions import employee_may, require_module_permission

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
        "request-audits",
    ):
        mode = "view"

    # Return-clients / serial-movements share the serials permission key.
    denied = require_module_permission(
        request, profile, "stock-management", mode
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

    if mode == "request-audits":
        return stock_request_audits(request, profile, meta, module)

    if mode in ("report", "movements"):
        return stock_report(request, profile, meta, module, page_mode=mode)

    if mode == "serials":
        return stock_serials(request, profile, meta, module)

    if mode == "serial-movements":
        return stock_serial_movements(request, profile, meta, module)

    if mode == "return-clients":
        return stock_serial_returns(request, profile, meta, module)

    from shops.models import Shop

    shops = actionable_shops_for_profile(profile)
    all_shops = shops
    shops_by_id = {str(shop.pk): shop for shop in shops}
    # Request-from may be any active company shop (parity with MY-SHOP floor).
    request_from_shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )
    request_from_by_id = {str(shop.pk): shop for shop in request_from_shops}

    def _resolve_shop(raw):
        raw = (raw or "").strip()
        return shops_by_id.get(raw)

    def _resolve_request_from_shop(raw):
        raw = (raw or "").strip()
        return request_from_by_id.get(raw)

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
            apply_stock_movement(
                profile,
                action_mode,
                request.POST,
                entry_source=StockEntrySource.STOCK_MANAGEMENT,
            )
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
        requested_from_shop = _resolve_request_from_shop(requested_from_id)
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

    from .models import StockRequestStatus

    request_context = {}
    if mode == "request":
        from employees.workspace import stock_management_url

        request_context = {
            "pending_stock_requests": list_stock_requests_for_profile(
                profile, status=StockRequestStatus.PENDING, limit=25
            ),
            "request_summary": summarize_stock_requests_for_profile(profile),
            "stock_request_audits_url": stock_management_url(
                profile.role, "request-audits"
            ),
        }

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
            "request_from_shops": request_from_shops,
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
            "can_print_stock": employee_may(profile, "stock-management", "print"),
            **request_context,
        },
    )


@active_employee_required
@require_http_methods(["GET"])
def stock_management_catalog(request, role_segment):
    """Paginated stock-management catalog for view/in/out/request modes."""
    from employees.access import get_profile_for_request, role_url_segment
    from employees.models import EmployeeRole
    from employees.module_permissions import require_module_permission

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
        action_shops = {shop.pk: shop for shop in actionable_shops_for_profile(profile)}
        view_shop_id = requested_shop_ids[0] if requested_shop_ids else 0
        if view_shop_id and view_shop_id not in action_shops:
            return JsonResponse({"ok": False, "error": "shop_required"}, status=400)
        payload = build_stock_catalog_page(
            shop_id=view_shop_id or None,
            shop_ids=None if view_shop_id else list(action_shops.keys()),
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
            from shops.models import Shop

            from_shop = (
                Shop.objects.filter(
                    pk=from_id, is_hidden=False, is_suspended=False
                ).first()
                if from_id
                else None
            )
            if from_shop is None:
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
    if sale is not None:
        return "sold", "Sold", sale
    if override in ItemSerialStatus.values:
        event = returned or sale
        return override, ItemSerialStatus(override).label, event
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
    """Map serial_number → latest client receipt return (sold, then returned)."""
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


def _returned_serial_line_queryset(*, shop_id="", shop_ids=None, client_id=None, client_phone=""):
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
    elif shop_ids is not None:
        lines = lines.filter(receipt__shop_id__in=list(shop_ids))
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
    """Chronological stock events that include serial numbers, for allocated shops."""
    from .models import Item

    search = (request.GET.get("q") or "").strip()
    range_type, day_start, day_end, filter_context = _report_range_bounds(request)
    event_filter = _parse_movement_event_filter(request.GET.get("event_type"))

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="serial-movements",
        shop_id="",
        profile=profile,
    )

    filter_shops = _serial_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    selected_shop_ids = [
        pk for pk in _parse_id_list(request.GET.getlist("shop_id")) if pk in shops_by_id
    ]
    active_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]
    display_shops = (
        [shops_by_id[pk] for pk in selected_shop_ids]
        if selected_shop_ids
        else list(filter_shops)
    )
    shops_label = _serial_shops_label(display_shops)
    shop_ids_for_query = active_shop_ids

    serial_items = list(
        Item.objects.filter(track_serial_number=True).order_by("category", "name")
    )
    movement_events = []
    if serial_items and shop_ids_for_query:
        movement_events, _, _, _, _ = _build_movement_timeline(
            shop_ids=shop_ids_for_query,
            day_start=day_start,
            day_end=day_end,
            item_mode="items",
            selected_categories=[],
            selected_item_ids=[item.pk for item in serial_items],
            report_items=serial_items,
            event_filter=event_filter,
        )
    if event_filter != "all":
        movement_events = _filter_movement_events(movement_events, event_filter)
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
            "filter_shops": filter_shops,
            "selected_shop_ids": set(selected_shop_ids),
            "display_shops": display_shops,
            "shops_label": shops_label,
            "event_filter": event_filter,
            "stock_mode": "serial-movements",
            **filter_context,
        },
    )


def stock_serial_returns(request, profile, meta, module):
    """Clients who returned serial-tracked items at allocated shops."""
    from employees.access import role_url_segment

    search = (request.GET.get("q") or "").strip()

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="return-clients",
        shop_id="",
        profile=profile,
    )

    display_shops = _serial_shops_for_profile(profile)
    shops_label = _serial_shops_label(display_shops)
    shop_index = {shop.pk: index for index, shop in enumerate(display_shops)}

    lines = _returned_serial_line_queryset(
        shop_ids=[shop.pk for shop in display_shops]
    )
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
            "shops_label": shops_label,
            "selected_shop_id": "",
            "stock_mode": "return-clients",
        },
    )


def stock_serial_return_client(
    request, profile, meta, module, *, client_id=None, guest_phone="", guest_name=""
):
    """Returned serial items for one client at allocated shops."""
    from employees.access import role_url_segment
    from shops.models import Client

    search = (request.GET.get("q") or "").strip()

    page_sidebar = sidebar_for_stock_management(
        profile.role,
        active_mode="return-clients",
        shop_id="",
        profile=profile,
    )

    display_shops = _serial_shops_for_profile(profile)
    shops_label = _serial_shops_label(display_shops)
    allocated_shop_ids = [shop.pk for shop in display_shops]

    client = None
    if client_id is not None:
        client = get_object_or_404(Client, pk=client_id)
        client_name = (client.full_name or "").strip() or "Client"
        client_phone = (client.phone_number or "").strip()
        lines = _returned_serial_line_queryset(
            shop_ids=allocated_shop_ids, client_id=client.pk
        )
    else:
        guest_phone = (guest_phone or request.GET.get("phone") or "").strip()
        guest_name = (guest_name or request.GET.get("name") or "").strip()
        if not guest_phone and not guest_name:
            raise Http404("Client not found.")
        client_name = guest_name or "Walk-in"
        client_phone = guest_phone
        lines = _returned_serial_line_queryset(
            shop_ids=allocated_shop_ids, client_phone=guest_phone or guest_name
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
            "shops_label": shops_label,
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

    filter_shops = _serial_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    selected_shop_ids = [
        pk for pk in _parse_id_list(request.GET.getlist("shop_id")) if pk in shops_by_id
    ]
    active_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]
    display_shops = (
        [shops_by_id[pk] for pk in selected_shop_ids]
        if selected_shop_ids
        else list(filter_shops)
    )
    shop_ids = set(active_shop_ids)
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
        # Still on an active sale → sold. Client receipt return stays Returned
        # until sold again or status is changed manually.
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
            "filter_shops": filter_shops,
            "selected_shop_ids": set(selected_shop_ids),
            "display_shops": display_shops,
            "shops_label": shops_label,
            "stock_mode": "serials",
        },
    )


def _build_serial_history_events(*, item, serial, shop_ids):
    """All stock/sale/return events for one serial, oldest first."""
    from shops.models import ShopReceiptKind, ShopReceiptLine, ShopReceiptStatus

    from .models import (
        StockMovementLine,
        StockMovementType,
        StockOutReason,
        StockRequestStatus,
    )

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
        if movement.movement_type == StockMovementType.OUT:
            reason = (line.reason or "").strip().lower()
            if reason == StockOutReason.TRANSFER:
                event_type = "transfer_fulfilled"
                event_label = "Transfer"
            elif reason == StockOutReason.RETURN:
                event_label = "Supplier return"
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
        has_return = _serial_list_contains(line.returned_serial_numbers, serial_key)
        has_sale = _serial_list_contains(line.serial_numbers, serial_key) or has_return
        if receipt.status == ShopReceiptStatus.CANCELLED and not has_return:
            continue
        parties = _movement_parties_for_receipt(receipt=receipt)
        if has_sale:
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
        if has_return:
            events.append(
                {
                    "happened_at": receipt.last_returned_at or receipt.created_at,
                    "event_type": "returned",
                    "event_label": "Client return",
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
    from employees.models import SHOP_ASSIGNABLE_ROLES

    from .models import ItemSerial, ItemSerialStatus

    item = get_object_or_404(Item, pk=item_id, track_serial_number=True)
    serial = get_object_or_404(
        ItemSerial.objects.select_related("shop", "item"),
        item=item,
        serial_number__iexact=(serial_number or "").strip(),
    )

    display_shops = _serial_shops_for_profile(profile)
    shop_ids = [shop.pk for shop in display_shops]
    if getattr(profile, "role", None) in SHOP_ASSIGNABLE_ROLES:
        if not shop_ids:
            raise Http404("Serial not found.")
        if serial.shop_id is not None and serial.shop_id not in shop_ids:
            raise Http404("Serial not found.")

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
    from shops.services import get_company_profile
    from django.utils import timezone

    profile = get_profile_for_request(request)
    if profile is None or not profile.is_active_employee:
        raise Http404("Not found.")
    if role_url_segment(profile.role) != role_segment:
        raise Http404("Not found.")

    denied = require_module_permission(request, profile, "stock-management", "print")
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

    all_shops = actionable_shops_for_profile(profile)
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


@require_GET
def item_photo(request, item_id):
    """Serve item photos through Django (works when /media/ is not web-exposed)."""
    item = get_object_or_404(Item, pk=item_id)
    field = item.image
    if not field:
        raise Http404("Photo not found.")
    try:
        name = (getattr(field, "name", None) or "").strip()
        storage = getattr(field, "storage", None)
        if not name or storage is None or not storage.exists(name):
            raise Http404("Photo not found.")
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        response = FileResponse(field.open("rb"), content_type=content_type)
        # Versioned public URLs (?v=filename) handle replacement; keep a short cache.
        response["Cache-Control"] = "public, max-age=3600"
        return response
    except (ValueError, OSError, AttributeError) as exc:
        raise Http404("Photo not found.") from exc
