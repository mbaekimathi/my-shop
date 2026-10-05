"""Audit trail for item-management register / edit / suspend / delete actions."""

from __future__ import annotations

from django.db.models import Count, Prefetch
from django.utils import timezone

from items.models import Item, ItemActivityEvent, ItemActivityKind


def _actor_label(profile) -> str:
    if profile is None:
        return "System"
    user = getattr(profile, "user", None)
    if user:
        return user.get_full_name() or profile.employee_id or user.username or "Staff"
    return profile.employee_id or "Staff"


def _event_tone(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value == ItemActivityKind.REGISTERED:
        return "good"
    if value in {ItemActivityKind.SUSPENDED, ItemActivityKind.DELETED}:
        return "bad"
    if value in {ItemActivityKind.EDITED, ItemActivityKind.DISCOUNT_UPDATED}:
        return "warn"
    return "neutral"


def _event_icon(kind: str) -> str:
    icons = {
        ItemActivityKind.REGISTERED: "plus-circle",
        ItemActivityKind.EDITED: "pencil",
        ItemActivityKind.DISCOUNT_UPDATED: "badge-percent",
        ItemActivityKind.SUSPENDED: "circle-pause",
        ItemActivityKind.UNSUSPENDED: "circle-play",
        ItemActivityKind.DELETED: "trash-2",
    }
    return icons.get((kind or "").strip().lower(), "activity")


def log_item_activity(
    *,
    kind: str,
    actor=None,
    item=None,
    item_name: str = "",
    item_category: str = "",
    detail: str = "",
    meta=None,
    shop_ids=None,
    occurred_at=None,
) -> ItemActivityEvent:
    """Persist one item-management activity event."""
    name = (item_name or "").strip()
    category = (item_category or "").strip()
    if item is not None:
        name = name or (item.name or "").strip()
        category = category or (item.category or "").strip()

    event = ItemActivityEvent.objects.create(
        item=item if getattr(item, "pk", None) else None,
        item_name=name[:200],
        item_category=category[:120],
        kind=kind,
        detail=(detail or "")[:255],
        meta=dict(meta or {}),
        actor=actor,
        occurred_at=occurred_at or timezone.now(),
    )

    ids = []
    seen = set()
    for raw in shop_ids or []:
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid in seen:
            continue
        seen.add(sid)
        ids.append(sid)
    if ids:
        event.shops.set(ids)
    return event


def _snapshot_item(item: Item) -> dict:
    prices = {
        str(shop_id): f"{price:.2f}"
        for shop_id, price in item.shop_prices.values_list("shop_id", "price")
    }
    return {
        "category": item.category or "",
        "name": item.name or "",
        "description": (item.description or "").strip(),
        "minimum_selling_price": f"{item.minimum_selling_price:.2f}",
        "shop_price": f"{item.shop_price:.2f}",
        "track_serial_number": bool(item.track_serial_number),
        "is_suspended": bool(item.is_suspended),
        "discount_min_qty": int(item.discount_min_qty or 0),
        "discount_amount": f"{(item.discount_amount or 0):.2f}",
        "shop_prices": prices,
        "has_image": bool(item.image),
    }


def _diff_summary(before: dict, after: dict) -> tuple[str, list[int]]:
    """Build a short human detail and list of shops whose prices changed."""
    changes = []
    shop_ids = []

    for field, label in (
        ("category", "category"),
        ("name", "name"),
        ("description", "description"),
        ("minimum_selling_price", "minimum price"),
        ("shop_price", "list price"),
        ("track_serial_number", "serial tracking"),
        ("discount_min_qty", "wholesale qty"),
        ("discount_amount", "discount amount"),
        ("has_image", "image"),
    ):
        if before.get(field) != after.get(field):
            changes.append(label)

    before_prices = before.get("shop_prices") or {}
    after_prices = after.get("shop_prices") or {}
    all_shop_keys = set(before_prices) | set(after_prices)
    for key in sorted(all_shop_keys, key=lambda value: int(value) if str(value).isdigit() else 0):
        if before_prices.get(key) != after_prices.get(key):
            try:
                shop_ids.append(int(key))
            except (TypeError, ValueError):
                pass
    if shop_ids:
        changes.append("shop prices")

    if not changes:
        return "No field changes", shop_ids
    if len(changes) == 1:
        return f"Updated {changes[0]}", shop_ids
    if len(changes) == 2:
        return f"Updated {changes[0]} and {changes[1]}", shop_ids
    return f"Updated {', '.join(changes[:3])} +{len(changes) - 3} more", shop_ids


def log_item_registered(item: Item, *, actor=None, shop_ids=None) -> ItemActivityEvent:
    ids = list(shop_ids or [])
    if not ids:
        ids = list(item.shop_prices.values_list("shop_id", flat=True))
    return log_item_activity(
        kind=ItemActivityKind.REGISTERED,
        actor=actor,
        item=item,
        detail=f"Registered in {item.category}" if item.category else "Registered item",
        meta={"shop_price": f"{item.shop_price:.2f}"},
        shop_ids=ids,
    )


def log_item_edited(
    item: Item,
    *,
    actor=None,
    before: dict | None = None,
    after: dict | None = None,
) -> ItemActivityEvent:
    before = before or {}
    after = after or _snapshot_item(item)
    detail, shop_ids = _diff_summary(before, after)
    return log_item_activity(
        kind=ItemActivityKind.EDITED,
        actor=actor,
        item=item,
        detail=detail,
        meta={"before": before, "after": after},
        shop_ids=shop_ids or list(item.shop_prices.values_list("shop_id", flat=True)),
    )


def log_item_discount_updated(
    item: Item,
    *,
    actor=None,
    before: dict | None = None,
) -> ItemActivityEvent:
    after = {
        "discount_min_qty": int(item.discount_min_qty or 0),
        "discount_amount": f"{(item.discount_amount or 0):.2f}",
    }
    before = before or {}
    bits = []
    if before.get("discount_min_qty") != after["discount_min_qty"]:
        bits.append(f"from qty {after['discount_min_qty']}")
    if before.get("discount_amount") != after["discount_amount"]:
        bits.append(f"amount KSh {after['discount_amount']}")
    detail = "Updated wholesale discount"
    if bits:
        detail = f"Updated wholesale discount ({', '.join(bits)})"
    return log_item_activity(
        kind=ItemActivityKind.DISCOUNT_UPDATED,
        actor=actor,
        item=item,
        detail=detail,
        meta={"before": before, "after": after},
        shop_ids=list(item.shop_prices.values_list("shop_id", flat=True)),
    )


def log_item_suspend_toggled(item: Item, *, actor=None) -> ItemActivityEvent:
    kind = (
        ItemActivityKind.SUSPENDED
        if item.is_suspended
        else ItemActivityKind.UNSUSPENDED
    )
    return log_item_activity(
        kind=kind,
        actor=actor,
        item=item,
        detail="Suspended item" if item.is_suspended else "Reactivated item",
        shop_ids=list(item.shop_prices.values_list("shop_id", flat=True)),
    )


def log_item_deleted(item: Item, *, actor=None) -> ItemActivityEvent:
    shop_ids = list(item.shop_prices.values_list("shop_id", flat=True))
    return log_item_activity(
        kind=ItemActivityKind.DELETED,
        actor=actor,
        item=item,
        item_name=item.name,
        item_category=item.category,
        detail=f"Deleted from {item.category}" if item.category else "Deleted item",
        meta={"item_id": item.pk},
        shop_ids=shop_ids,
    )


def item_activity_kind_options() -> list[dict]:
    return [{"slug": "all", "label": "All activities"}] + [
        {"slug": choice.value, "label": choice.label}
        for choice in ItemActivityKind
    ]


def build_item_activity_audits(
    *,
    profile,
    request=None,
    start=None,
    end=None,
    shop_id: int = 0,
    employee_id: int = 0,
    kind: str = "",
    shops=None,
    employees=None,
) -> dict:
    """Filtered chronological feed of item-management activity."""
    from employees.models import EmployeeProfile, EmployeeStatus

    kind_slug = (kind or "all").strip().lower()
    valid_kinds = {choice.value for choice in ItemActivityKind}
    if kind_slug not in valid_kinds and kind_slug != "all":
        kind_slug = "all"

    qs = ItemActivityEvent.objects.select_related(
        "actor", "actor__user", "item"
    ).prefetch_related(
        Prefetch("shops", to_attr="prefetched_shops")
    )

    if start is not None:
        qs = qs.filter(occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(occurred_at__lt=end)
    if kind_slug != "all":
        qs = qs.filter(kind=kind_slug)
    if employee_id:
        qs = qs.filter(actor_id=employee_id)
    if shop_id:
        qs = qs.filter(shops__pk=shop_id).distinct()

    events = list(qs[:500])

    rows = []
    for event in events:
        shop_names = [shop.name for shop in getattr(event, "prefetched_shops", [])]
        if not shop_names:
            shop_label = "All shops"
        elif len(shop_names) == 1:
            shop_label = shop_names[0]
        else:
            shop_label = f"{shop_names[0]} +{len(shop_names) - 1}"
        rows.append(
            {
                "when_label": timezone.localtime(event.occurred_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "item": event.item_name or (event.item.name if event.item_id else "—"),
                "category": event.item_category or "—",
                "kind": event.kind,
                "kind_label": event.get_kind_display(),
                "icon": _event_icon(event.kind),
                "tone": _event_tone(event.kind),
                "detail": event.detail or "—",
                "shop": shop_label,
                "actor": _actor_label(event.actor),
            }
        )

    counts = (
        ItemActivityEvent.objects.filter(
            pk__in=[event.pk for event in events]
        )
        .values("kind")
        .annotate(total=Count("pk"))
    )
    by_kind = {row["kind"]: row["total"] for row in counts}

    filter_shops = list(shops or [])
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
        "empty_message": "No item activity matches these filters.",
        "kind_filter": kind_slug,
        "kind_options": item_activity_kind_options(),
        "selected_shop_id": shop_id or 0,
        "selected_employee_id": employee_id or 0,
        "filter_shops": filter_shops,
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
                    "label": "Registered",
                    "value": str(by_kind.get(ItemActivityKind.REGISTERED, 0)),
                    "icon": "plus-circle",
                    "tone": "good",
                },
                {
                    "label": "Edited",
                    "value": str(
                        by_kind.get(ItemActivityKind.EDITED, 0)
                        + by_kind.get(ItemActivityKind.DISCOUNT_UPDATED, 0)
                    ),
                    "icon": "pencil",
                    "tone": "warn",
                },
                {
                    "label": "Suspended",
                    "value": str(by_kind.get(ItemActivityKind.SUSPENDED, 0)),
                    "icon": "circle-pause",
                    "tone": "bad",
                },
                {
                    "label": "Deleted",
                    "value": str(by_kind.get(ItemActivityKind.DELETED, 0)),
                    "icon": "trash-2",
                    "tone": "bad",
                },
            ],
        },
    }


# Re-export snapshot helper for callers that need before/after diffs.
snapshot_item_for_audit = _snapshot_item
