"""Store Manager central store stock in/out pages (no shop stock changes)."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from employees.access import get_profile_for_request, role_required
from employees.models import EmployeeRole
from employees.module_permissions import employee_may
from employees.workspace import sidebar_for_store_stock
from items.models import (
    ShelfStockMovementType,
    StockOutReason,
    StoreStockMovementType,
)
from items.store_stock_services import (
    build_store_stock_report,
    list_store_stock_items,
    parse_stock_out_details,
    parse_store_stock_qty_lines,
    parse_store_trade_lines,
    parse_supplier_payment_details,
    record_store_stock_lines,
    record_store_trade,
    search_active_employees,
    search_store_items,
    set_item_stock_count_enabled,
)


def _stock_requirements():
    """Company-wide compulsory-field flags for stock in/out."""
    from shops.services import get_company_stock_settings

    return get_company_stock_settings()


def _stock_out_reasons():
    return StockOutReason.choices


def _actor_label(profile):
    if profile is None:
        return ""
    name = profile.user.get_full_name() or profile.user.username
    return f"{profile.employee_id} — {name}"


def item_stock_tracking_enabled():
    """Company stock mode tracks catalog items — off only in Shelf-only mode."""
    from shops.services import get_company_pos_settings

    return bool(
        getattr(get_company_pos_settings(), "enable_stock_tracking", True)
    )


def _shelf_only_redirect(request):
    """Shelf-only stock mode: the central item store is not used."""
    if item_stock_tracking_enabled():
        return None
    messages.info(request, "Shelf mode is on — item store stock is not tracked.")
    if shelf_mode_shops():
        return redirect("employees:store_shelf_stock")
    return redirect("employees:role_store_manager")


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_items(request):
    """Item stock home: current central-store quantity per catalog item."""
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    items = list_store_stock_items()
    return render(
        request,
        "employees/store_stock_items.html",
        {
            "profile": profile,
            "meta": {
                "title": "Item Stock",
                "headline": "Item stock",
                "summary": (
                    "Current stock in the central store. Use Stock in / Stock "
                    "out to move quantities; the shop floor is separate."
                ),
                "icon": "boxes",
            },
            "items": items,
            "item_count": len(items),
            "store_total_display": str(
                sum((row["store_qty"] for row in items), start=0)
            ),
            "actor_label": _actor_label(profile),
            "employee_search_url": reverse("employees:store_stock_employee_search"),
            "stock_in_url": reverse("employees:store_stock_in"),
            "stock_out_url": reverse("employees:store_stock_out"),
            "stock_trade_url": reverse("employees:store_stock_trade"),
            "item_toggle_url": reverse("employees:store_stock_item_count_toggle"),
            "stock_req": _stock_requirements(),
            "stock_out_reasons": _stock_out_reasons(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER, profile=profile, active="items"
            ),
        },
    )


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_movement(request):
    """Item stock movement log — the raw in/out ledger for the central store."""
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    page = build_store_stock_report(request=request)
    return render(
        request,
        "employees/store_stock_movement.html",
        {
            "profile": profile,
            "page": page,
            "meta": {
                "title": "Item Stock Movement",
                "headline": "Item stock movement",
                "summary": (
                    "Every stock in / stock out line recorded for the central "
                    "store, newest first."
                ),
                "icon": "arrow-down-up",
            },
            "report_url": reverse("employees:store_stock_report"),
            "items": list_store_stock_items(),
            "actor_label": _actor_label(profile),
            "employee_search_url": reverse("employees:store_stock_employee_search"),
            "stock_in_url": reverse("employees:store_stock_in"),
            "stock_out_url": reverse("employees:store_stock_out"),
            "stock_trade_url": reverse("employees:store_stock_trade"),
            "item_toggle_url": reverse("employees:store_stock_item_count_toggle"),
            "stock_req": _stock_requirements(),
            "stock_out_reasons": _stock_out_reasons(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER, profile=profile, active="movement"
            ),
        },
    )


def _render_store_stock_page(request, *, mode: str):
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    is_in = mode == StoreStockMovementType.IN

    if request.method == "POST":
        stock_req = _stock_requirements()
        try:
            lines = parse_store_stock_qty_lines(
                request.POST,
                require_price=is_in
                and getattr(stock_req, "require_buying_price_on_in", False),
            )
            if is_in:
                details = parse_supplier_payment_details(
                    request.POST, stock_req=stock_req
                )
                note = request.POST.get("note") or ""
            else:
                details = parse_stock_out_details(
                    request.POST, stock_req=stock_req
                )
                note = details.pop("note", "")
            movements = record_store_stock_lines(
                mode=mode,
                actor=profile,
                lines=lines,
                received_by_id=request.POST.get("received_by_id"),
                note=note,
                **details,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            from decimal import Decimal

            total_qty = sum((row.quantity for row in movements), start=Decimal("0"))
            messages.success(
                request,
                (
                    f"Recorded {len(movements)} store stock "
                    f"{'in' if is_in else 'out'} line"
                    f"{'' if len(movements) == 1 else 's'} "
                    f"({total_qty} units)."
                ),
            )
            return redirect("employees:store_stock_items")

    items = list_store_stock_items()
    return render(
        request,
        "employees/store_stock_form.html",
        {
            "profile": profile,
            "mode": mode,
            "meta": {
                "title": "Store Stock In" if is_in else "Store Stock Out",
                "headline": "Store stock in" if is_in else "Store stock out",
                "summary": (
                    "Enter qty next to each item entering the central store. "
                    "This does not change shop floor stock."
                    if is_in
                    else "Enter qty next to each item leaving the central store. "
                    "This does not change shop floor stock."
                ),
                "icon": "package-plus" if is_in else "package-minus",
            },
            "actor_label": _actor_label(profile),
            "submit_label": "Record stock in" if is_in else "Record stock out",
            "stock_req": _stock_requirements(),
            "stock_out_reasons": _stock_out_reasons(),
            "items": items,
            "item_count": len(items),
            "employee_search_url": reverse("employees:store_stock_employee_search"),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER,
                profile=profile,
                active="in" if is_in else "out",
            ),
        },
    )


@role_required(EmployeeRole.STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_stock_in(request):
    return _render_store_stock_page(request, mode=StoreStockMovementType.IN)


@role_required(EmployeeRole.STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_stock_out(request):
    return _render_store_stock_page(request, mode=StoreStockMovementType.OUT)


@role_required(EmployeeRole.STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_stock_trade(request):
    """Item stock trade: give items to or receive items from one person."""
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)

    if request.method == "POST":
        try:
            kind, lines = parse_store_trade_lines(
                request.POST,
                require_price=getattr(
                    _stock_requirements(),
                    "require_buying_price_on_in",
                    False,
                ),
            )
            movements = record_store_trade(
                actor=profile,
                kind=kind,
                person_name=request.POST.get("person_name"),
                person_phone=request.POST.get("person_phone"),
                lines=lines,
                note=(request.POST.get("note") or "").strip(),
                require_person=bool(
                    getattr(_stock_requirements(), "require_supplier_on_in", False)
                ),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            from decimal import Decimal

            total_qty = sum(
                (row.quantity for row in movements), start=Decimal("0")
            )
            messages.success(
                request,
                (
                    f"Recorded {len(movements)} trade line"
                    f"{'' if len(movements) == 1 else 's'} "
                    f"({total_qty} units)."
                ),
            )
            return redirect("employees:store_stock_items")

    items = list_store_stock_items()
    return render(
        request,
        "employees/store_stock_trade.html",
        {
            "profile": profile,
            "meta": {
                "title": "Item Stock Trade",
                "headline": "Item stock trade",
                "summary": (
                    "Trade items with a person — either they take items out "
                    "of the store or they bring items into it. Shop floor "
                    "stock is separate."
                ),
                "icon": "repeat",
            },
            "items": items,
            "item_count": len(items),
            "actor_label": _actor_label(profile),
            "submit_label": "Record trade",
            "stock_req": _stock_requirements(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER, profile=profile, active="trade"
            ),
        },
    )


def shelf_mode_shops():
    """Active shops whose effective POS settings have Shelf mode on."""
    from shops.models import Shop
    from shops.services import get_effective_pos_settings

    return [
        shop
        for shop in Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
        if getattr(get_effective_pos_settings(shop), "enable_shelf_mode", False)
    ]


SHELF_PAGE_MODES = ("stock", "in", "out", "trade", "movements", "report")


@role_required(EmployeeRole.STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_shelf_stock(request):
    """Store Manager: register and adjust shelf stock for any shop in Shelf mode."""
    from decimal import Decimal

    from items.shelf_stock_services import (
        SHELF_UNIT_CHOICES,
        apply_shelf_action,
        list_shelf_items,
        recent_shelf_movements,
        record_shelf_movement,
        shelf_categories,
    )

    profile = get_profile_for_request(request)
    if not employee_may(profile, "my-shop", "shelf_stock"):
        messages.error(request, "You do not have permission to manage shelf stock.")
        return redirect("employees:role_store_manager")

    shops = shelf_mode_shops()
    mode = (request.POST.get("mode") or request.GET.get("mode") or "stock").strip().lower()
    if mode not in SHELF_PAGE_MODES:
        mode = "stock"
    wanted = str(request.POST.get("shop") or request.GET.get("shop") or "")
    shop = next((row for row in shops if str(row.pk) == wanted), None)

    def _back(*, keep_mode=True):
        params = []
        if shop is not None:
            params.append(f"shop={shop.pk}")
        if keep_mode and mode != "stock":
            params.append(f"mode={mode}")
        query = f"?{'&'.join(params)}" if params else ""
        return redirect(f"{reverse('employees:store_shelf_stock')}{query}")

    if request.method == "POST":
        if shop is None:
            messages.error(
                request,
                "That shop is not in Shelf mode. Pick a shop and try again."
                if shops
                else "Turn on Shelf or Both under Settings → POS → Stock first.",
            )
            return redirect("employees:store_shelf_stock")
        if mode in (ShelfStockMovementType.IN, ShelfStockMovementType.OUT):
            stock_req = _stock_requirements()
            try:
                lines = parse_store_stock_qty_lines(
                    request.POST,
                    require_price=mode == ShelfStockMovementType.IN
                    and getattr(
                        stock_req,
                        "shelf_require_buying_price_on_in",
                        False,
                    ),
                )
                if mode == ShelfStockMovementType.IN:
                    details = parse_supplier_payment_details(
                        request.POST,
                        stock_req=stock_req,
                        field_prefix="shelf_",
                    )
                    note = (request.POST.get("note") or "").strip()[:255]
                else:
                    details = parse_stock_out_details(
                        request.POST,
                        stock_req=stock_req,
                        field_prefix="shelf_",
                    )
                    note = details.pop("note", "")
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
                return _back(keep_mode=False)
            done = 0
            total = Decimal("0")
            for shelf_item_id, qty, buying_price in lines:
                try:
                    movement = record_shelf_movement(
                        shop=shop,
                        shelf_item_id=shelf_item_id,
                        mode=mode,
                        qty=qty,
                        actor=profile,
                        note=note,
                        buying_price=buying_price,
                        **details,
                    )
                except ValidationError as exc:
                    for message in exc.messages:
                        messages.error(request, message)
                else:
                    done += 1
                    total += movement.quantity
            if done:
                messages.success(
                    request,
                    f"Recorded {done} shelf stock {mode} line"
                    f"{'' if done == 1 else 's'} ({total} units).",
                )
            return _back(keep_mode=False)
        if mode == "trade":
            stock_req = _stock_requirements()
            try:
                kind, lines = parse_store_trade_lines(
                    request.POST,
                    require_price=getattr(
                        stock_req,
                        "shelf_require_buying_price_on_in",
                        False,
                    ),
                )
                person_name = (request.POST.get("person_name") or "").strip()
                person_phone = (request.POST.get("person_phone") or "").strip()
                note = (request.POST.get("note") or "").strip()[:255]
                if kind == StockOutReason.TRADE_IN and getattr(
                    stock_req, "shelf_require_supplier_on_in", False
                ):
                    if not person_name:
                        raise ValidationError("Enter the other person's name.")
                    if not person_phone:
                        raise ValidationError(
                            "Enter the other person's phone number."
                        )
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
                return _back(keep_mode=False)
            move_mode = (
                ShelfStockMovementType.OUT
                if kind == StockOutReason.TRADE_OUT
                else ShelfStockMovementType.IN
            )
            done = 0
            total = Decimal("0")
            for shelf_item_id, qty, value in lines:
                try:
                    movement = record_shelf_movement(
                        shop=shop,
                        shelf_item_id=shelf_item_id,
                        mode=move_mode,
                        qty=qty,
                        actor=profile,
                        note=note,
                        supplier_name=person_name,
                        supplier_phone=person_phone,
                        buying_price=value,
                        reason=kind,
                    )
                except ValidationError as exc:
                    for message in exc.messages:
                        messages.error(request, message)
                else:
                    done += 1
                    total += movement.quantity
            if done:
                messages.success(
                    request,
                    f"Recorded {done} shelf trade line"
                    f"{'' if done == 1 else 's'} ({total} units).",
                )
            return _back(keep_mode=False)
        try:
            messages.success(
                request,
                apply_shelf_action(
                    shop=shop,
                    actor=profile,
                    data=request.POST,
                    files=request.FILES,
                ),
            )
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return _back()

    if shop is None and shops:
        shop = shops[0]
    shelf_items = list_shelf_items(shop=shop) if shop else []
    pos = None
    if shop is not None:
        from shops.services import get_effective_pos_settings

        pos = get_effective_pos_settings(shop)
    return render(
        request,
        "employees/store_shelf_stock.html",
        {
            "profile": profile,
            "meta": {
                "title": "Shelf stock",
                "headline": "Shelf stock",
                "summary": (
                    "Register the stock on each shop's shelves and add or remove "
                    "quantities by hand. Shelf items are not sold, so they do "
                    "not show on the shop page."
                ),
                "icon": "layers",
            },
            "shelf_mode": mode,
            "shops": shops,
            "shop": shop,
            "shelf_items": shelf_items,
            "shelf_item_count": len(shelf_items),
            "shelf_low_count": sum(1 for row in shelf_items if row["is_low"]),
            "shelf_total_display": str(
                sum((row["quantity"] for row in shelf_items), start=Decimal("0"))
            ),
            "shelf_movements": (
                recent_shelf_movements(
                    shop=shop, limit=500 if mode == "movements" else 30
                )
                if shop
                else []
            ),
            "item_stock_tracking": bool(getattr(pos, "enable_stock_tracking", False)),
            "stock_req": _stock_requirements(),
            "stock_out_reasons": _stock_out_reasons(),
            "shelf_needs_staff_id": False,
            "shelf_shop_field": True,
            "shelf_unit_choices": SHELF_UNIT_CHOICES,
            "shelf_categories": shelf_categories(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER,
                profile=profile,
                active=f"shelf-{mode}",
            ),
        },
    )


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_report(request):
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    page = build_store_stock_report(request=request)
    return render(
        request,
        "employees/store_stock_report.html",
        {
            "profile": profile,
            "page": page,
            "meta": {
                "title": "Store Report",
                "headline": "Store report",
                "summary": (
                    "Central store stock movements for the selected period and item. "
                    "Shop floor stock is not included."
                ),
                "icon": "clipboard-list",
            },
            "item_search_url": reverse("employees:store_stock_item_search"),
            "items": list_store_stock_items(),
            "actor_label": _actor_label(profile),
            "employee_search_url": reverse("employees:store_stock_employee_search"),
            "stock_in_url": reverse("employees:store_stock_in"),
            "stock_out_url": reverse("employees:store_stock_out"),
            "stock_trade_url": reverse("employees:store_stock_trade"),
            "item_toggle_url": reverse("employees:store_stock_item_count_toggle"),
            "stock_req": _stock_requirements(),
            "stock_out_reasons": _stock_out_reasons(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER, profile=profile, active="report"
            ),
            **{
                key: page[key]
                for key in (
                    "report_range",
                    "report_date_value",
                    "report_date_from",
                    "report_date_to",
                    "report_month_value",
                    "report_year_value",
                    "report_period_label",
                )
                if key in page
            },
        },
    )


@role_required(EmployeeRole.STORE_MANAGER)
@require_http_methods(["POST"])
def store_stock_item_count_toggle(request):
    """Enable/disable stock counting on sale for one item (item-stock page)."""
    if not item_stock_tracking_enabled():
        return JsonResponse(
            {
                "ok": False,
                "error": "Shelf mode is on — item store stock is not tracked.",
            },
            status=400,
        )
    try:
        item_id = int(request.POST.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    enabled = str(request.POST.get("enabled") or "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }
    try:
        item = set_item_stock_count_enabled(item_id=item_id, enabled=enabled)
    except ValidationError as exc:
        return JsonResponse(
            {"ok": False, "error": "; ".join(exc.messages)}, status=400
        )
    return JsonResponse(
        {
            "ok": True,
            "item_id": item.pk,
            "stock_count_enabled": item.stock_count_enabled,
        }
    )


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_item_search(request):
    results = (
        search_store_items(query=request.GET.get("q") or "", limit=12)
        if item_stock_tracking_enabled()
        else []
    )
    return JsonResponse({"ok": True, "results": results})


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_employee_search(request):
    results = search_active_employees(query=request.GET.get("q") or "", limit=12)
    return JsonResponse({"ok": True, "results": results})
