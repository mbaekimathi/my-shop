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
from items.models import StoreStockMovementType
from items.store_stock_services import (
    build_store_stock_report,
    list_store_stock_items,
    parse_store_stock_qty_lines,
    record_store_stock_lines,
    search_active_employees,
    search_store_items,
)


def _actor_label(profile):
    if profile is None:
        return ""
    name = profile.user.get_full_name() or profile.user.username
    return f"{profile.employee_id} — {name}"


def _render_store_stock_page(request, *, mode: str):
    profile = get_profile_for_request(request)
    is_in = mode == StoreStockMovementType.IN
    redirect_name = "employees:store_stock_in" if is_in else "employees:store_stock_out"

    if request.method == "POST":
        try:
            lines = parse_store_stock_qty_lines(request.POST)
            movements = record_store_stock_lines(
                mode=mode,
                actor=profile,
                lines=lines,
                received_by_id=request.POST.get("received_by_id"),
                note=request.POST.get("note") or "",
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
            return redirect(redirect_name)

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


def shelf_mode_shops():
    """Active shops whose effective POS settings have Shelf mode on."""
    from shops.models import Shop
    from shops.services import get_effective_pos_settings

    return [
        shop
        for shop in Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
        if getattr(get_effective_pos_settings(shop), "enable_shelf_mode", False)
    ]


@role_required(EmployeeRole.STORE_MANAGER)
@require_http_methods(["GET", "POST"])
def store_shelf_stock(request):
    """Store Manager: register and adjust shelf stock for any shop in Shelf mode."""
    from items.shelf_stock_services import (
        SHELF_UNIT_CHOICES,
        apply_shelf_action,
        list_shelf_items,
        recent_shelf_movements,
        shelf_categories,
    )

    profile = get_profile_for_request(request)
    if not employee_may(profile, "my-shop", "shelf_stock"):
        messages.error(request, "You do not have permission to manage shelf stock.")
        return redirect("employees:role_store_manager")

    shops = shelf_mode_shops()
    wanted = str(request.POST.get("shop") or request.GET.get("shop") or "")
    shop = next((row for row in shops if str(row.pk) == wanted), None)

    if request.method == "POST":
        if shop is None:
            messages.error(
                request,
                "That shop is not in Shelf mode. Pick a shop and try again."
                if shops
                else "Turn on Shelf or Both under Settings → POS → Stock first.",
            )
            return redirect("employees:store_shelf_stock")
        try:
            messages.success(
                request, apply_shelf_action(shop=shop, actor=profile, data=request.POST)
            )
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return redirect(f"{reverse('employees:store_shelf_stock')}?shop={shop.pk}")

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
            "shops": shops,
            "shop": shop,
            "shelf_items": shelf_items,
            "shelf_item_count": len(shelf_items),
            "shelf_low_count": sum(1 for row in shelf_items if row["is_low"]),
            "shelf_movements": recent_shelf_movements(shop=shop) if shop else [],
            "item_stock_tracking": bool(getattr(pos, "enable_stock_tracking", False)),
            "shelf_needs_staff_id": False,
            "shelf_shop_field": True,
            "shelf_unit_choices": SHELF_UNIT_CHOICES,
            "shelf_categories": shelf_categories(),
            "page_sidebar": sidebar_for_store_stock(
                EmployeeRole.STORE_MANAGER, profile=profile, active="shelf"
            ),
        },
    )


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_report(request):
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
@require_GET
def store_stock_item_search(request):
    results = search_store_items(query=request.GET.get("q") or "", limit=12)
    return JsonResponse({"ok": True, "results": results})


@role_required(EmployeeRole.STORE_MANAGER)
@require_GET
def store_stock_employee_search(request):
    results = search_active_employees(query=request.GET.get("q") or "", limit=12)
    return JsonResponse({"ok": True, "results": results})
