"""Activity analytics for stock-management actions (in / out / request / responses)."""

from __future__ import annotations

from django.db.models import Prefetch, Q
from django.utils import timezone

from items.models import (
    StockMovement,
    StockMovementLine,
    StockMovementType,
    StockRequestStatus,
)


def _actor_label(profile) -> str:
    if profile is None:
        return "System"
    user = getattr(profile, "user", None)
    if user:
        return user.get_full_name() or profile.employee_id or user.username or "Staff"
    return profile.employee_id or "Staff"


def _movement_tone(movement_type: str, *, request_status: str = "") -> str:
    if movement_type == StockMovementType.IN:
        return "good"
    if movement_type == StockMovementType.OUT:
        return "bad"
    if request_status == StockRequestStatus.DECLINED:
        return "bad"
    if request_status == StockRequestStatus.FULFILLED:
        return "good"
    return "warn"


def _movement_icon(movement_type: str, *, request_status: str = "") -> str:
    if movement_type == StockMovementType.IN:
        return "package-plus"
    if movement_type == StockMovementType.OUT:
        return "package-minus"
    if request_status == StockRequestStatus.DECLINED:
        return "ban"
    if request_status == StockRequestStatus.FULFILLED:
        return "check-circle"
    return "clipboard-list"


def stock_activity_kind_options() -> list[dict]:
    return [
        {"slug": "all", "label": "All activities"},
        {"slug": "in", "label": "Stock in"},
        {"slug": "out", "label": "Stock out"},
        {"slug": "request", "label": "Requests"},
        {"slug": "approved", "label": "Approved requests"},
        {"slug": "declined", "label": "Declined requests"},
    ]


def build_stock_activity_audits(
    *,
    profile,
    start=None,
    end=None,
    shop_id: int = 0,
    employee_id: int = 0,
    kind: str = "",
    shops=None,
    employees=None,
) -> dict:
    """Filtered chronological feed of stock-management employee activity."""
    from employees.models import EmployeeProfile, EmployeeStatus

    kind_slug = (kind or "all").strip().lower()
    valid = {option["slug"] for option in stock_activity_kind_options()}
    if kind_slug not in valid:
        kind_slug = "all"

    allowed_shop_ids = [shop.pk for shop in (shops or [])]
    qs = StockMovement.objects.select_related(
        "shop",
        "requested_from_shop",
        "created_by",
        "created_by__user",
        "responded_by",
        "responded_by__user",
    ).prefetch_related(
        Prefetch(
            "lines",
            queryset=StockMovementLine.objects.select_related("item"),
            to_attr="prefetched_lines",
        )
    )

    if allowed_shop_ids:
        qs = qs.filter(
            Q(shop_id__in=allowed_shop_ids)
            | Q(requested_from_shop_id__in=allowed_shop_ids)
        )
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    if shop_id:
        qs = qs.filter(Q(shop_id=shop_id) | Q(requested_from_shop_id=shop_id))
    if employee_id:
        qs = qs.filter(
            Q(created_by_id=employee_id) | Q(responded_by_id=employee_id)
        )

    if kind_slug == "in":
        qs = qs.filter(movement_type=StockMovementType.IN)
    elif kind_slug == "out":
        qs = qs.filter(movement_type=StockMovementType.OUT)
    elif kind_slug == "request":
        qs = qs.filter(movement_type=StockMovementType.REQUEST)
    elif kind_slug == "approved":
        qs = qs.filter(
            movement_type=StockMovementType.REQUEST,
            request_status=StockRequestStatus.FULFILLED,
        )
    elif kind_slug == "declined":
        qs = qs.filter(
            movement_type=StockMovementType.REQUEST,
            request_status=StockRequestStatus.DECLINED,
        )

    movements = list(qs.order_by("-created_at", "-pk")[:500])

    rows = []
    for movement in movements:
        # Prefer prefetched attribute; fall back to related manager.
        lines = getattr(movement, "prefetched_lines", None)
        if lines is None:
            lines = list(movement.lines.all())
        item_names = [line.item.name for line in lines[:3] if getattr(line, "item", None)]
        extra = max(0, len(lines) - len(item_names))
        if item_names and extra:
            detail_items = f"{', '.join(item_names)} +{extra}"
        elif item_names:
            detail_items = ", ".join(item_names)
        else:
            detail_items = "No line items"

        qty = 0
        for line in lines:
            try:
                qty += float(line.quantity or 0)
            except (TypeError, ValueError):
                pass
        qty_label = str(int(qty)) if qty == int(qty) else f"{qty:.3f}".rstrip("0").rstrip(".")

        shop_label = movement.shop.name if movement.shop_id else "—"
        if movement.movement_type == StockMovementType.REQUEST:
            from_name = (
                movement.requested_from_shop.name
                if movement.requested_from_shop_id
                else "—"
            )
            shop_label = f"{shop_label} → {from_name}"
            status = movement.get_request_status_display() or "Pending"
            kind_label = f"Request · {status}"
            actor = _actor_label(movement.created_by)
            if movement.responded_by_id and movement.request_status in {
                StockRequestStatus.FULFILLED,
                StockRequestStatus.DECLINED,
            }:
                actor = (
                    f"{_actor_label(movement.created_by)} → "
                    f"{_actor_label(movement.responded_by)}"
                )
        else:
            kind_label = movement.get_movement_type_display()
            actor = _actor_label(movement.created_by)

        note = (movement.notes or "").strip()
        detail = f"{qty_label} units · {detail_items}"
        if note:
            detail = f"{detail} · {note[:80]}"

        rows.append(
            {
                "when_label": timezone.localtime(movement.created_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "kind": movement.movement_type,
                "kind_label": kind_label,
                "icon": _movement_icon(
                    movement.movement_type, request_status=movement.request_status or ""
                ),
                "tone": _movement_tone(
                    movement.movement_type, request_status=movement.request_status or ""
                ),
                "shop": shop_label,
                "detail": detail,
                "actor": actor,
                "qty": qty_label,
            }
        )

    by_type = {}
    for movement in movements:
        key = movement.movement_type
        by_type[key] = by_type.get(key, 0) + 1

    filter_employees = list(employees or [])
    if not filter_employees:
        filter_employees = list(
            EmployeeProfile.objects.filter(status=EmployeeStatus.ACTIVE)
            .select_related("user")
            .order_by("employee_id")[:300]
        )

    return {
        "rows": rows,
        "event_count": len(rows),
        "empty_message": "No stock activity matches these filters.",
        "kind_filter": kind_slug,
        "kind_options": stock_activity_kind_options(),
        "selected_shop_id": shop_id or 0,
        "selected_employee_id": employee_id or 0,
        "filter_shops": list(shops or []),
        "filter_employees": filter_employees,
        "summary_board": {
            "hero": {
                "label": "Activities",
                "value": str(len(rows)),
                "hint": "In selected period",
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Stock in",
                    "value": str(by_type.get(StockMovementType.IN, 0)),
                    "icon": "package-plus",
                    "tone": "good",
                },
                {
                    "label": "Stock out",
                    "value": str(by_type.get(StockMovementType.OUT, 0)),
                    "icon": "package-minus",
                    "tone": "bad",
                },
                {
                    "label": "Requests",
                    "value": str(by_type.get(StockMovementType.REQUEST, 0)),
                    "icon": "clipboard-list",
                    "tone": "warn",
                },
            ],
        },
    }
