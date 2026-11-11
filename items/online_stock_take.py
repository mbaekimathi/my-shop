"""Online stock take: live physical counts vs current shop stock (not Stock Take)."""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from items.models import Item, OnlineStockTakeEntry, ShopStock

_ZERO = Decimal("0")


def _quantize(value) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        qty = _ZERO
    return qty.quantize(Decimal("0.001"))


def _qty_label(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pieces_word(abs_diff: Decimal) -> str:
    return "piece" if abs_diff == 1 else "pieces"


def variance_payload(*, system: Decimal, physical: Decimal | None) -> dict:
    if physical is None:
        return {
            "variance": None,
            "variance_kind": None,
            "variance_label": None,
            "match": False,
        }
    diff = _quantize(physical) - _quantize(system)
    abs_diff = abs(diff)
    if diff == 0:
        return {
            "variance": "0",
            "variance_kind": "match",
            "variance_label": "Match",
            "match": True,
        }
    if diff > 0:
        return {
            "variance": _qty_label(diff),
            "variance_kind": "excess",
            "variance_label": (
                f"Excess by {_qty_label(abs_diff)} {_pieces_word(abs_diff)}"
            ),
            "match": False,
        }
    return {
        "variance": _qty_label(diff),
        "variance_kind": "short",
        "variance_label": (
            f"Short by {_qty_label(abs_diff)} {_pieces_word(abs_diff)}"
        ),
        "match": False,
    }


def build_online_stock_take_catalog(*, shop) -> dict:
    """All active items for one shop, grouped by category, with live variance."""
    if shop is None:
        return {
            "groups": [],
            "item_count": 0,
            "filled_count": 0,
            "match_count": 0,
            "excess_count": 0,
            "short_count": 0,
        }

    items = list(
        Item.objects.filter(is_suspended=False)
        .only("id", "name", "category")
        .order_by("category", "name", "id")
    )
    stock_map = {
        row.item_id: _quantize(row.quantity)
        for row in ShopStock.objects.filter(shop=shop).only("item_id", "quantity")
    }
    entry_map = {
        row.item_id: row
        for row in OnlineStockTakeEntry.objects.filter(shop=shop).only(
            "item_id", "physical_qty", "updated_at"
        )
    }

    groups: OrderedDict[str, list] = OrderedDict()
    filled = 0
    match_count = 0
    excess_count = 0
    short_count = 0

    for item in items:
        category = (item.category or "").strip() or "Uncategorised"
        system = stock_map.get(item.pk, _ZERO)
        entry = entry_map.get(item.pk)
        physical = (
            _quantize(entry.physical_qty)
            if entry is not None and entry.physical_qty is not None
            else None
        )
        variance = variance_payload(system=system, physical=physical)
        if physical is not None:
            filled += 1
            if variance["variance_kind"] == "match":
                match_count += 1
            elif variance["variance_kind"] == "excess":
                excess_count += 1
            elif variance["variance_kind"] == "short":
                short_count += 1
        groups.setdefault(category, []).append(
            {
                "item_id": item.pk,
                "name": item.name,
                "category": category,
                "system_qty": _qty_label(system),
                "physical_qty": (
                    _qty_label(physical) if physical is not None else ""
                ),
                **variance,
            }
        )

    return {
        "groups": [
            {
                "category": category,
                "lines": lines,
                "count": len(lines),
            }
            for category, lines in groups.items()
        ],
        "item_count": len(items),
        "filled_count": filled,
        "match_count": match_count,
        "excess_count": excess_count,
        "short_count": short_count,
    }


def _parse_physical_qty(raw, *, item_name: str) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        qty = _quantize(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Invalid count for {item_name}.") from exc
    if qty < 0:
        raise ValidationError(f"Count for {item_name} cannot be negative.")
    return qty


@transaction.atomic
def save_online_stock_take_line(
    *,
    shop,
    item_id,
    qty,
    profile=None,
    updated_by_label: str = "",
) -> dict:
    """Autosave one physical count against live system qty."""
    try:
        item_pk = int(item_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose an item to count.") from exc

    item = (
        Item.objects.filter(pk=item_pk, is_suspended=False)
        .only("id", "name", "category")
        .first()
    )
    if item is None:
        raise ValidationError("That item was not found.")

    physical = _parse_physical_qty(qty, item_name=item.name)
    system = _quantize(
        ShopStock.objects.filter(shop=shop, item=item)
        .values_list("quantity", flat=True)
        .first()
        or _ZERO
    )
    label = (updated_by_label or "").strip()[:160]

    if physical is None:
        OnlineStockTakeEntry.objects.filter(shop=shop, item=item).delete()
    else:
        OnlineStockTakeEntry.objects.update_or_create(
            shop=shop,
            item=item,
            defaults={
                "physical_qty": physical,
                "updated_by": profile,
                "updated_by_label": label,
            },
        )

    variance = variance_payload(system=system, physical=physical)
    return {
        "item_id": item.pk,
        "name": item.name,
        "category": (item.category or "").strip() or "Uncategorised",
        "system_qty": _qty_label(system),
        "physical_qty": _qty_label(physical) if physical is not None else "",
        "saved_at": timezone.localtime().strftime("%H:%M:%S"),
        **variance,
    }
