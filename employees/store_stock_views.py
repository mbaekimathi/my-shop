"""Company Store Manager central store stock in/out pages (no shop stock changes)."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from employees.access import get_profile_for_request, role_required
from employees.models import EmployeeRole
from employees.module_permissions import employee_may, require_module_permission
from employees.workspace import sidebar_for_store_stock
from items.models import (
    ShelfStockMovementType,
    StockOutReason,
    StoreStockMovementType,
)
from items.store_stock_services import (
    build_store_item_stock_report,
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
    return redirect("employees:role_company_store_manager")


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_GET
def store_stock_items(request):
    """Item stock home: current central-store quantity per catalog item."""
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    denied = require_module_permission(
        request, profile, "stock-management", "view"
    )
    if denied is not None:
        return denied
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
                EmployeeRole.COMPANY_STORE_MANAGER, profile=profile, active="items"
            ),
        },
    )


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_GET
def store_stock_movement(request):
    """Item stock movement log — the raw in/out ledger for the central store."""
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    from items.movement_page import build_movement_ledger_page

    profile = get_profile_for_request(request)
    denied = require_module_permission(
        request, profile, "stock-management", "movements"
    )
    if denied is not None:
        return denied
    response, context = build_movement_ledger_page(
        request,
        source="store",
        page_title="Item stock movement",
    )
    if response is not None:
        return response
    context.update(
        {
            "profile": profile,
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
                EmployeeRole.COMPANY_STORE_MANAGER, profile=profile, active="movement"
            ),
        }
    )
    return render(request, "employees/store_stock_movement.html", context)


def _render_store_stock_page(request, *, mode: str):
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    is_in = mode == StoreStockMovementType.IN
    denied = require_module_permission(
        request, profile, "stock-management", "in" if is_in else "out"
    )
    if denied is not None:
        return denied

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
                EmployeeRole.COMPANY_STORE_MANAGER,
                profile=profile,
                active="in" if is_in else "out",
            ),
        },
    )


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_stock_in(request):
    return _render_store_stock_page(request, mode=StoreStockMovementType.IN)


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_stock_out(request):
    return _render_store_stock_page(request, mode=StoreStockMovementType.OUT)


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_stock_trade(request):
    """Item stock trade: give items to or receive items from one person."""
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    if not (
        employee_may(profile, "stock-management", "in")
        or employee_may(profile, "stock-management", "out")
    ):
        messages.error(
            request, "You do not have permission to record store trades."
        )
        return redirect("employees:role_company_store_manager")

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
            required = "in" if kind == StockOutReason.TRADE_IN else "out"
            if not employee_may(profile, "stock-management", required):
                raise ValidationError(
                    "You do not have permission to record that trade."
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
                EmployeeRole.COMPANY_STORE_MANAGER, profile=profile, active="trade"
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


SHELF_PAGE_MODES = (
    "stock", "in", "out", "trade", "movements", "report", "audits", "suspended",
)


def _shelf_report_download(*, shop, report, filter_context):
    """PDF download of the per-item shelf report (same engine as stock reports)."""
    import re

    from django.http import HttpResponse
    from django.utils import timezone

    from items.services import build_stock_report_pdf
    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    logo_path = ""
    if getattr(company, "logo", None):
        try:
            logo_path = company.logo.path
        except Exception:
            logo_path = ""

    period_label = (
        (filter_context.get("report_period_label") or "").strip() or "—"
    )
    headers = [
        "Item", "Unit", "Start", "In", "Transfers", "Trades", "Out", "Close",
    ]
    rows = []
    for row in report["rows"]:
        label = row["name"]
        if row["category"]:
            label = f"{label} · {row['category']}"
        flags = []
        if row["is_suspended"]:
            flags.append("suspended")
        if row["is_archived"]:
            flags.append("archived")
        if flags:
            label = f"{label} ({', '.join(flags)})"
        rows.append(
            [
                label,
                row["unit"] or "—",
                row["start_display"],
                row["in_display"],
                row["transfers_display"],
                row["trades_display"],
                row["out_display"],
                row["close_display"],
            ]
        )
    if rows:
        totals = report["totals"]
        rows.append(
            [
                "Total",
                "",
                totals["start_display"],
                totals["in_display"],
                totals["transfers_display"],
                totals["trades_display"],
                totals["out_display"],
                totals["close_display"],
            ]
        )

    pdf_bytes = build_stock_report_pdf(
        company_name=company_name,
        page_mode="report",
        period_label=period_label,
        event_filter_label="Shelf stock",
        view_label="By item",
        shop_label=shop.name,
        logo_path=logo_path,
        company_phone=(getattr(company, "phone_number", None) or "").strip(),
        company_email=(getattr(company, "email", None) or "").strip(),
        company_location=(getattr(company, "location", None) or "").strip(),
        report_headers=headers,
        report_rows=rows,
    )
    safe_period = (
        re.sub(r"[^\w\-]+", "-", period_label, flags=re.UNICODE)
        .strip("-")
        .lower()
        or "export"
    )
    stamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d-%H%M")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="shelf-stock-report-{safe_period}-{stamp}.pdf"'
    )
    response["Content-Length"] = str(len(pdf_bytes))
    return response


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_shelf_stock(request):
    """Company Store Manager: register and adjust shelf stock for any shop in Shelf mode."""
    from decimal import Decimal

    from items.shelf_stock_services import (
        SHELF_UNIT_CHOICES,
        apply_shelf_action,
        list_shelf_items,
        list_suspended_shelf_items,
        record_shelf_movement,
        shelf_categories,
        shelf_movements_in_period,
    )

    profile = get_profile_for_request(request)
    if not employee_may(profile, "my-shop", "shelf_stock"):
        messages.error(request, "You do not have permission to manage shelf stock.")
        return redirect("employees:role_company_store_manager")

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

    if mode == "movements" and shops:
        from items.movement_page import build_movement_ledger_page

        response, context = build_movement_ledger_page(
            request,
            source="shelf",
            filter_shops=shops,
            base_params=[("mode", "movements")],
            shop_params=("shop_id", "shop"),
            page_title="Shelf stock movement",
        )
        if response is not None:
            return response
        selected = context.get("selected_shop_ids") or set()
        modal_shop = shop
        if len(selected) == 1:
            modal_shop = next(
                (row for row in shops if row.pk in selected), modal_shop
            )
        if modal_shop is None:
            modal_shop = shops[0]
        modal_items = list_shelf_items(shop=modal_shop)
        context.update(
            {
                "profile": profile,
                "meta": {
                    "title": "Shelf stock movement",
                    "headline": "Shelf stock movement",
                    "summary": (
                        "Shelf stock changes per shop. Shelf items are not "
                        "sold, so they do not show on the shop page."
                    ),
                    "icon": "layers",
                },
                "shelf_mode": mode,
                "shops": shops,
                "shop": modal_shop,
                "shelf_items": modal_items,
                "shelf_item_count": len(modal_items),
                "stock_req": _stock_requirements(),
                "stock_out_reasons": _stock_out_reasons(),
                "shelf_needs_staff_id": False,
                "shelf_shop_field": True,
                "shelf_unit_choices": SHELF_UNIT_CHOICES,
                "shelf_categories": shelf_categories(),
                "page_sidebar": sidebar_for_store_stock(
                    EmployeeRole.COMPANY_STORE_MANAGER,
                    profile=profile,
                    active="shelf-movements",
                ),
            }
        )
        return render(request, "employees/store_shelf_movement.html", context)

    if shop is None and shops:
        shop = shops[0]
    shelf_items = list_shelf_items(shop=shop) if shop else []
    suspended_items = (
        list_suspended_shelf_items(shop=shop)
        if shop and mode == "suspended"
        else []
    )
    pos = None
    if shop is not None:
        from shops.services import get_effective_pos_settings

        pos = get_effective_pos_settings(shop)
    meta = {
        "title": "Shelf stock",
        "headline": "Shelf stock",
        "summary": (
            "Register the stock on each shop's shelves and add or remove "
            "quantities by hand. Shelf items are not sold, so they do "
            "not show on the shop page."
        ),
        "icon": "layers",
    }
    if mode == "suspended":
        meta = {
            "title": "Suspended shelf items",
            "headline": "Suspended shelf items",
            "summary": (
                "Shelf items paused at this shop — they can't be stocked in "
                "or out until you unsuspend them."
            ),
            "icon": "circle-pause",
        }
    elif mode == "report":
        meta = {
            "title": "Shelf stock report",
            "headline": "Shelf stock report",
            "summary": (
                "Opening, movement and closing shelf stock for the period "
                "you pick — filter by day, period, month, year or item, "
                "then download the report."
            ),
            "icon": "bar-chart-3",
        }
    elif mode == "audits":
        meta = {
            "title": "Shelf stock audits",
            "headline": "Shelf stock audits",
            "summary": (
                "Every shelf transaction for this shop in the period you "
                "pick — filter by day, period, month or year."
            ),
            "icon": "clipboard-list",
        }
    audit_filter = {}
    audit_range = "day"
    shelf_audit_movements = []
    shelf_report = None
    shelf_report_download_url = ""
    if shop is not None and mode in ("audits", "report"):
        from items.views import _report_range_bounds

        audit_range, audit_start, audit_end, audit_filter = _report_range_bounds(
            request
        )
        if mode == "audits":
            shelf_audit_movements = shelf_movements_in_period(
                shop=shop, day_start=audit_start, day_end=audit_end
            )
        else:
            from items.shelf_stock_services import build_shelf_stock_report

            raw_item = (request.GET.get("item") or "").strip()
            shelf_report = build_shelf_stock_report(
                shop=shop,
                day_start=audit_start,
                day_end=audit_end,
                item_id=int(raw_item) if raw_item.isdigit() else None,
            )
            if (request.GET.get("download") or "").strip() == "1":
                return _shelf_report_download(
                    shop=shop,
                    report=shelf_report,
                    filter_context=audit_filter,
                )
            from urllib.parse import urlencode

            download_params = [
                ("mode", "report"),
                ("shop", str(shop.pk)),
                ("range", audit_range),
            ]
            for key in ("date", "date_from", "date_to", "month", "year"):
                value = (request.GET.get(key) or "").strip()
                if value:
                    download_params.append((key, value))
            if shelf_report["selected_item_id"] is not None:
                download_params.append(
                    ("item", str(shelf_report["selected_item_id"]))
                )
            download_params.append(("download", "1"))
            shelf_report_download_url = f"?{urlencode(download_params)}"
    return render(
        request,
        "employees/store_shelf_stock.html",
        {
            "profile": profile,
            "meta": meta,
            "shelf_mode": mode,
            "shops": shops,
            "shop": shop,
            "shelf_items": shelf_items,
            "suspended_items": suspended_items,
            "suspended_item_count": len(suspended_items),
            "shelf_manage": mode == "stock",
            "shelf_item_count": len(shelf_items),
            "shelf_low_count": sum(1 for row in shelf_items if row["is_low"]),
            "shelf_total_display": str(
                sum((row["quantity"] for row in shelf_items), start=Decimal("0"))
            ),
            "shelf_audits": mode == "audits",
            "shelf_audit_movements": shelf_audit_movements,
            "shelf_audit_count": len(shelf_audit_movements),
            "shelf_report": shelf_report,
            "shelf_report_download_url": shelf_report_download_url,
            "report_range_label": {
                "day": "Single day",
                "period": "Period",
                "month": "Month",
                "year": "Year",
            }[audit_range],
            **audit_filter,
            "item_stock_tracking": bool(getattr(pos, "enable_stock_tracking", False)),
            "stock_req": _stock_requirements(),
            "stock_out_reasons": _stock_out_reasons(),
            "shelf_needs_staff_id": False,
            "shelf_shop_field": True,
            "shelf_unit_choices": SHELF_UNIT_CHOICES,
            "shelf_categories": shelf_categories(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.COMPANY_STORE_MANAGER,
                profile=profile,
                active=f"shelf-{mode}",
            ),
        },
    )


def _store_report_download(*, report, filter_context):
    """PDF download of the per-item store report (same engine as shelf reports)."""
    import re

    from django.http import HttpResponse
    from django.utils import timezone

    from items.services import build_stock_report_pdf
    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    logo_path = ""
    if getattr(company, "logo", None):
        try:
            logo_path = company.logo.path
        except Exception:
            logo_path = ""

    period_label = (
        (filter_context.get("report_period_label") or "").strip() or "—"
    )
    headers = [
        "Item", "Category", "Start", "In", "Transfers", "Trades", "Out", "Close",
    ]
    rows = []
    for row in report["rows"]:
        label = row["name"]
        if row["is_suspended"]:
            label = f"{label} (suspended)"
        rows.append(
            [
                label,
                row["category"] or "—",
                row["start_display"],
                row["in_display"],
                row["transfers_display"],
                row["trades_display"],
                row["out_display"],
                row["close_display"],
            ]
        )
    if rows:
        totals = report["totals"]
        rows.append(
            [
                "Total",
                "",
                totals["start_display"],
                totals["in_display"],
                totals["transfers_display"],
                totals["trades_display"],
                totals["out_display"],
                totals["close_display"],
            ]
        )

    pdf_bytes = build_stock_report_pdf(
        company_name=company_name,
        page_mode="report",
        period_label=period_label,
        event_filter_label="Item stock",
        view_label="By item",
        shop_label="Central store",
        logo_path=logo_path,
        company_phone=(getattr(company, "phone_number", None) or "").strip(),
        company_email=(getattr(company, "email", None) or "").strip(),
        company_location=(getattr(company, "location", None) or "").strip(),
        report_headers=headers,
        report_rows=rows,
    )
    safe_period = (
        re.sub(r"[^\w\-]+", "-", period_label, flags=re.UNICODE)
        .strip("-")
        .lower()
        or "export"
    )
    stamp = timezone.localtime(timezone.now()).strftime("%Y-%m-%d-%H%M")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="store-stock-report-{safe_period}-{stamp}.pdf"'
    )
    response["Content-Length"] = str(len(pdf_bytes))
    return response


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_GET
def store_stock_report(request):
    denied = _shelf_only_redirect(request)
    if denied is not None:
        return denied
    profile = get_profile_for_request(request)
    denied = require_module_permission(
        request, profile, "stock-management", "report"
    )
    if denied is not None:
        return denied
    page = build_store_stock_report(request=request)
    download_params = [
        (key, value)
        for key in (
            "range",
            "date",
            "date_from",
            "date_to",
            "month",
            "year",
            "time_from",
            "time_to",
        )
        if (value := (request.GET.get(key) or "").strip())
    ]
    if page["selected_item_id"] != "":
        download_params.append(("item_id", str(page["selected_item_id"])))
    download_params.append(("download", "1"))
    from urllib.parse import urlencode

    store_report_download_url = f"?{urlencode(download_params)}"
    if (request.GET.get("download") or "").strip() == "1":
        return _store_report_download(
            report=build_store_item_stock_report(
                day_start=page.get("start"),
                day_end=page.get("end"),
                item_id=page["selected_item_id"] or None,
            ),
            filter_context=page,
        )
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
            "store_report_download_url": store_report_download_url,
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.COMPANY_STORE_MANAGER, profile=profile, active="report"
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


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
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
    denied = require_module_permission(
        request,
        get_profile_for_request(request),
        "item-management",
        "edit",
        as_json=True,
    )
    if denied is not None:
        return denied
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


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_GET
def store_stock_item_search(request):
    denied = _store_stock_read_denied(request)
    if denied is not None:
        return denied
    results = (
        search_store_items(query=request.GET.get("q") or "", limit=12)
        if item_stock_tracking_enabled()
        else []
    )
    return JsonResponse({"ok": True, "results": results})


@role_required(EmployeeRole.COMPANY_STORE_MANAGER)
@require_GET
def store_stock_employee_search(request):
    denied = _store_stock_read_denied(request)
    if denied is not None:
        return denied
    results = search_active_employees(query=request.GET.get("q") or "", limit=12)
    return JsonResponse({"ok": True, "results": results})


def _store_stock_read_denied(request):
    """JSON 403 when the profile has no stock-management capability at all."""
    profile = get_profile_for_request(request)
    if any(
        employee_may(profile, "stock-management", key)
        for key in ("view", "in", "out")
    ):
        return None
    return JsonResponse(
        {"ok": False, "error": "You do not have permission for this action."},
        status=403,
    )
