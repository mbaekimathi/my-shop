"""Shelf-stock count features for IT support / stock management.

Shelf equivalents of the item-stock count tools:
- low stock alert thresholds (``ShelfItem.low_stock_threshold``)
- shelf stock take (create -> count -> compare -> optional adjust)
- shelf online stock take (daily autosaved counts, same contract as items)
- shelf print document (grouped shop -> category -> rows)

Nothing here adjusts stock unless ``apply_shelf_take_adjustments`` is called;
counts are comparison records first, just like the item-stock take.
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from items.models import (
    ShelfItem,
    ShelfOnlineStockTakeEntry,
    ShelfStockMovementType,
    ShelfStockTake,
    ShelfStockTakeEntry,
    StockTakeStatus,
)
from items.online_stock_take import (
    _assert_editable_count_day,
    _baseline_system,
    _qty_label,
    _quantize,
    resolve_online_stock_take_date,
    variance_payload,
)
from items.shelf_stock_services import (
    list_shelf_items,
    parse_shelf_qty,
    record_shelf_movement,
)

_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Shelf low stock alerts


def build_shelf_low_stock_groups(shops) -> list[dict]:
    """Per-shop shelf item rows with qty vs alert threshold."""
    groups = []
    for shop in shops:
        rows = list_shelf_items(shop=shop)
        groups.append(
            {
                "shop": shop,
                "rows": rows,
                "item_count": len(rows),
                "low_count": sum(1 for row in rows if row["is_low"]),
            }
        )
    return groups


def save_shelf_low_stock_threshold(*, shop, shelf_item_id, raw_threshold) -> ShelfItem:
    try:
        item_pk = int(shelf_item_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Shelf item not found.") from exc
    item = (
        ShelfItem.objects.select_for_update()
        .filter(pk=item_pk, shop=shop, is_archived=False)
        .first()
    )
    if item is None:
        raise ValidationError("Shelf item not found for this shop.")
    text = (raw_threshold or "").strip()
    if text == "":
        threshold = _ZERO
    else:
        try:
            threshold = _quantize(text)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError("Alert level must be a number.") from exc
        if threshold < 0:
            raise ValidationError("Alert level cannot be negative.")
    item.low_stock_threshold = threshold
    item.save(update_fields=["low_stock_threshold", "updated_at"])
    return item


# ---------------------------------------------------------------------------
# Shelf stock take


@transaction.atomic
def create_shelf_stock_take(*, shop, profile=None, note="") -> ShelfStockTake:
    """Snapshot every live shelf item at a shop into a new open take."""
    take = ShelfStockTake.objects.create(
        shop=shop,
        created_by=profile,
        note=(note or "").strip()[:240],
    )
    items = ShelfItem.objects.filter(shop=shop, is_archived=False).order_by(
        "category", "name", "id"
    )
    ShelfStockTakeEntry.objects.bulk_create(
        [
            ShelfStockTakeEntry(
                take=take,
                shelf_item=item,
                system_qty=item.quantity,
            )
            for item in items
        ]
    )
    return take


def shelf_take_rows(take) -> list[dict]:
    """Entry rows with variance vs the frozen system qty."""
    rows = []
    for entry in take.entries.select_related("shelf_item"):
        physical = (
            _quantize(entry.counted_qty)
            if entry.counted_qty is not None
            else None
        )
        system = _quantize(entry.system_qty)
        variance = variance_payload(system=system, physical=physical)
        item = entry.shelf_item
        rows.append(
            {
                "entry_id": entry.pk,
                "name": item.name,
                "category": (item.category or "").strip() or "Uncategorised",
                "unit": item.unit,
                "system_qty": _qty_label(system),
                "counted_qty": (
                    _qty_label(physical) if physical is not None else ""
                ),
                **variance,
            }
        )
    return rows


def shelf_take_summary(take) -> dict:
    entries = take.entries.all()
    filled = sum(1 for e in entries if e.counted_qty is not None)
    return {
        "entry_count": len(entries),
        "filled_count": filled,
        "variance_count": sum(
            1
            for e in entries
            if e.counted_qty is not None
            and _quantize(e.counted_qty) != _quantize(e.system_qty)
        ),
    }


@transaction.atomic
def submit_shelf_take_counts(*, take, counts: dict, profile=None) -> ShelfStockTake:
    """Save counted qty per entry {entry_id: raw}; close when all filled."""
    if take.status != StockTakeStatus.OPEN:
        raise ValidationError("This shelf stock take is already closed.")
    entries = {
        entry.pk: entry
        for entry in take.entries.select_for_update().select_related("shelf_item")
    }
    dirty = []
    for entry in entries.values():
        raw = counts.get(entry.pk)
        if raw is None:
            continue
        text = str(raw).strip()
        if text == "":
            entry.counted_qty = None
        else:
            qty = _quantize(text)
            if qty < 0:
                raise ValidationError(
                    f"Count for {entry.shelf_item.name} cannot be negative."
                )
            entry.counted_qty = qty
        dirty.append(entry)
    if dirty:
        ShelfStockTakeEntry.objects.bulk_update(dirty, ["counted_qty"])
    if all(entry.counted_qty is not None for entry in entries.values()):
        take.status = StockTakeStatus.CLOSED
        take.closed_at = timezone.now()
        take.closed_by = profile
        take.save(update_fields=["status", "closed_at", "closed_by"])
    return take


@transaction.atomic
def apply_shelf_take_adjustments(*, take, profile=None) -> int:
    """Post IN/OUT shelf movements so book qty matches the closed count."""
    if take.status != StockTakeStatus.CLOSED:
        raise ValidationError("Submit the count before applying adjustments.")
    if take.applied_at is not None:
        raise ValidationError("Adjustments were already applied for this take.")
    moved = 0
    note = f"Shelf stock take #{take.pk} adjustment"
    for entry in take.entries.select_related("shelf_item"):
        if entry.counted_qty is None:
            continue
        diff = _quantize(entry.counted_qty) - _quantize(entry.system_qty)
        if diff == 0:
            continue
        mode = (
            ShelfStockMovementType.IN if diff > 0 else ShelfStockMovementType.OUT
        )
        record_shelf_movement(
            shop=take.shop,
            shelf_item_id=entry.shelf_item_id,
            mode=mode,
            qty=abs(diff),
            actor=profile,
            note=note,
        )
        moved += 1
    take.applied_at = timezone.now()
    take.applied_by = profile
    take.save(update_fields=["applied_at", "applied_by"])
    return moved


# ---------------------------------------------------------------------------
# Shelf online stock take (same JSON contract as the item version)


def build_shelf_online_take_catalog(*, shop, count_date=None) -> dict:
    count_date = count_date or timezone.localdate()
    empty = {
        "filled_lines": [],
        "groups": [],
        "item_count": 0,
        "filled_count": 0,
        "match_count": 0,
        "excess_count": 0,
        "short_count": 0,
        "count_date": count_date,
    }
    if shop is None:
        return empty

    items = list(
        ShelfItem.objects.filter(shop=shop, is_archived=False)
        .only("id", "name", "category", "quantity")
        .order_by("category", "name", "id")
    )
    entry_map = {
        row.shelf_item_id: row
        for row in ShelfOnlineStockTakeEntry.objects.filter(
            shop=shop, count_date=count_date
        ).only(
            "shelf_item_id",
            "physical_qty",
            "system_qty_at_count",
            "updated_at",
            "updated_by_label",
            "count_date",
        )
    }

    groups: OrderedDict[str, list] = OrderedDict()
    filled_lines = []
    filled = match_count = excess_count = short_count = 0

    for item in items:
        category = (item.category or "").strip() or "Uncategorised"
        live_system = _quantize(item.quantity)
        entry = entry_map.get(item.pk)
        physical = (
            _quantize(entry.physical_qty)
            if entry is not None and entry.physical_qty is not None
            else None
        )
        system = _baseline_system(entry=entry, live_system=live_system)
        variance = variance_payload(system=system, physical=physical)
        line = {
            "item_id": item.pk,
            "name": item.name,
            "category": category,
            "system_qty": _qty_label(system),
            "live_system_qty": _qty_label(live_system),
            "physical_qty": (
                _qty_label(physical) if physical is not None else ""
            ),
            "updated_at": entry.updated_at if entry is not None else None,
            "updated_by_label": (
                (entry.updated_by_label or "").strip()
                if entry is not None and physical is not None
                else ""
            ),
            **variance,
        }
        groups.setdefault(category, []).append(line)
        if physical is not None:
            filled += 1
            if variance["variance_kind"] == "match":
                match_count += 1
            elif variance["variance_kind"] == "excess":
                excess_count += 1
            elif variance["variance_kind"] == "short":
                short_count += 1
            filled_lines.append(line)

    filled_lines.sort(
        key=lambda row: (
            -(row["updated_at"].timestamp() if row["updated_at"] else 0),
            (row["name"] or "").lower(),
            row["item_id"],
        )
    )
    return {
        **empty,
        "filled_lines": filled_lines,
        "groups": [
            {"category": category, "lines": lines, "count": len(lines)}
            for category, lines in groups.items()
        ],
        "item_count": len(items),
        "filled_count": filled,
        "match_count": match_count,
        "excess_count": excess_count,
        "short_count": short_count,
    }


def build_shelf_online_take_sync(*, shop, count_date=None) -> dict:
    count_date = count_date or timezone.localdate()
    if shop is None:
        return {
            "shop_id": None,
            "count_date": count_date.isoformat(),
            "revision": "0:",
            "lines": [],
            "filled_count": 0,
        }

    entries = list(
        ShelfOnlineStockTakeEntry.objects.filter(shop=shop, count_date=count_date)
        .exclude(physical_qty__isnull=True)
        .select_related("shelf_item")
        .only(
            "shelf_item_id",
            "physical_qty",
            "system_qty_at_count",
            "updated_at",
            "updated_by_label",
            "shelf_item__name",
            "shelf_item__category",
            "shelf_item__quantity",
        )
        .order_by("-updated_at", "shelf_item_id")
    )

    lines = []
    max_ts = None
    for entry in entries:
        item = entry.shelf_item
        if item is None or item.is_archived:
            continue
        live_system = _quantize(item.quantity)
        physical = _quantize(entry.physical_qty)
        system = _baseline_system(entry=entry, live_system=live_system)
        variance = variance_payload(system=system, physical=physical)
        updated_at = entry.updated_at
        if updated_at is not None and (max_ts is None or updated_at > max_ts):
            max_ts = updated_at
        lines.append(
            {
                "item_id": entry.shelf_item_id,
                "name": item.name,
                "category": (item.category or "").strip() or "Uncategorised",
                "system_qty": _qty_label(system),
                "live_system_qty": _qty_label(live_system),
                "physical_qty": _qty_label(physical),
                "updated_by_label": (entry.updated_by_label or "").strip(),
                "updated_at": (
                    timezone.localtime(updated_at).isoformat()
                    if updated_at is not None
                    else ""
                ),
                **variance,
            }
        )

    revision = (
        f"{len(lines)}:"
        f"{timezone.localtime(max_ts).isoformat() if max_ts is not None else ''}"
    )
    return {
        "shop_id": shop.pk,
        "count_date": count_date.isoformat(),
        "revision": revision,
        "lines": lines,
        "filled_count": len(lines),
    }


@transaction.atomic
def save_shelf_online_take_line(
    *,
    shop,
    item_id,
    qty,
    count_date=None,
    profile=None,
    updated_by_label: str = "",
) -> dict:
    """Autosave one shelf count for a shop/day; mirrors the item contract."""
    count_date = count_date or timezone.localdate()
    _assert_editable_count_day(count_date)
    try:
        item_pk = int(item_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose a shelf item to count.") from exc

    item = (
        ShelfItem.objects.filter(pk=item_pk, shop=shop, is_archived=False)
        .only("id", "name", "category", "quantity")
        .first()
    )
    if item is None:
        raise ValidationError("That shelf item was not found.")

    raw = qty
    if raw in (None, ""):
        physical = None
    else:
        try:
            physical = _quantize(raw)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError(f"Invalid count for {item.name}.") from exc
        if physical < 0:
            raise ValidationError(f"Count for {item.name} cannot be negative.")

    live_system = _quantize(item.quantity)
    label = (updated_by_label or "").strip()[:160]

    if physical is None:
        ShelfOnlineStockTakeEntry.objects.filter(
            shop=shop, shelf_item=item, count_date=count_date
        ).delete()
        updated_at = None
        system = live_system
    else:
        entry, _created = ShelfOnlineStockTakeEntry.objects.update_or_create(
            shop=shop,
            shelf_item=item,
            count_date=count_date,
            defaults={
                "physical_qty": physical,
                "system_qty_at_count": live_system,
                "updated_by": profile,
                "updated_by_label": label,
            },
        )
        updated_at = entry.updated_at
        system = live_system

    variance = variance_payload(system=system, physical=physical)
    sync = build_shelf_online_take_sync(shop=shop, count_date=count_date)
    return {
        "item_id": item.pk,
        "name": item.name,
        "category": (item.category or "").strip() or "Uncategorised",
        "system_qty": _qty_label(system),
        "live_system_qty": _qty_label(live_system),
        "physical_qty": _qty_label(physical) if physical is not None else "",
        "updated_by_label": label if physical is not None else "",
        "updated_at": (
            timezone.localtime(updated_at).isoformat()
            if updated_at is not None
            else ""
        ),
        "saved_at": timezone.localtime().strftime("%H:%M:%S"),
        "count_date": count_date.isoformat(),
        "revision": sync["revision"],
        **variance,
    }


# ---------------------------------------------------------------------------
# Shelf print document (Item / Unit / Qty / Alert at, grouped per shop)


def build_shelf_print_document(*, shops) -> dict:
    shops = [shop for shop in shops if shop is not None]
    categories = []
    item_count = 0
    for shop in shops:
        by_category: OrderedDict[str, list] = OrderedDict()
        for item in (
            ShelfItem.objects.filter(shop=shop, is_archived=False)
            .only("name", "category", "unit", "quantity", "low_stock_threshold")
            .order_by("category", "name", "id")
        ):
            category = (item.category or "").strip() or "Uncategorised"
            threshold = _quantize(item.low_stock_threshold or _ZERO)
            by_category.setdefault(category, []).append(
                {
                    "name": item.name,
                    "category": category,
                    "unit": item.unit or "—",
                    "qty": _qty_label(_quantize(item.quantity)),
                    "threshold": _qty_label(threshold) if threshold > 0 else "—",
                }
            )
            item_count += 1
        for category, rows in by_category.items():
            label = category if len(shops) == 1 else f"{shop.name} — {category}"
            categories.append({"name": label, "rows": rows})

    shop_label = ", ".join(shop.name for shop in shops)
    return {
        "layout": "shelf",
        "title": "Shelf stock list",
        "shop_label": shop_label,
        "shops": shops,
        "categories": categories,
        "item_count": item_count,
        "balance_day": "",
        "a4_dual": False,
        "a4_page_estimate": max(1, -(-item_count // 40)) if item_count else 1,
    }


__all__ = [
    "apply_shelf_take_adjustments",
    "build_shelf_low_stock_groups",
    "build_shelf_online_take_catalog",
    "build_shelf_online_take_sync",
    "build_shelf_print_document",
    "create_shelf_stock_take",
    "parse_shelf_qty",
    "resolve_online_stock_take_date",
    "save_shelf_low_stock_threshold",
    "save_shelf_online_take_line",
    "shelf_take_rows",
    "shelf_take_summary",
    "submit_shelf_take_counts",
]
