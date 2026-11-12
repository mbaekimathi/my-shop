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
