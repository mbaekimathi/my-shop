"""Online stock take: live physical counts vs frozen book qty (not Stock Take)."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
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


def resolve_online_stock_take_date(raw=None) -> date:
    """Parse YYYY-MM-DD; default to today's local date."""
    today = timezone.localdate()
    if raw in (None, ""):
        return today
    text = str(raw).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return today


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


def _baseline_system(*, entry, live_system: Decimal) -> Decimal:
    """Book qty used for variance: frozen at count time, else live fallback."""
    if entry is None or entry.physical_qty is None:
        return live_system
    if entry.system_qty_at_count is None:
        return live_system
    return _quantize(entry.system_qty_at_count)


def build_online_stock_take_catalog(*, shop, count_date: date | None = None) -> dict:
    """All active items for one shop/day, grouped by category, with frozen variance.

    Counted items stay in their category groups. ``filled_lines`` powers the
    floating cart summary (most recently updated first).
    """
    count_date = count_date or timezone.localdate()
    if shop is None:
        return {
            "filled_lines": [],
            "groups": [],
            "item_count": 0,
            "filled_count": 0,
            "match_count": 0,
            "excess_count": 0,
            "short_count": 0,
            "count_date": count_date,
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
        for row in OnlineStockTakeEntry.objects.filter(
            shop=shop, count_date=count_date
        ).only(
            "item_id",
            "physical_qty",
            "system_qty_at_count",
            "updated_at",
            "updated_by_label",
            "count_date",
        )
    }

    groups: OrderedDict[str, list] = OrderedDict()
    filled_lines = []
    filled = 0
    match_count = 0
    excess_count = 0
    short_count = 0

    for item in items:
        category = (item.category or "").strip() or "Uncategorised"
        live_system = stock_map.get(item.pk, _ZERO)
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
        "filled_lines": filled_lines,
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
        "count_date": count_date,
    }


def build_online_stock_take_sync(
    *, shop, count_date: date | None = None
) -> dict:
    """Lightweight payload of current filled counts for cross-device live sync."""
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
        OnlineStockTakeEntry.objects.filter(shop=shop, count_date=count_date)
        .exclude(physical_qty__isnull=True)
        .only(
            "item_id",
            "physical_qty",
            "system_qty_at_count",
            "updated_at",
            "updated_by_label",
        )
        .order_by("-updated_at", "item_id")
    )
    item_ids = [row.item_id for row in entries]
    stock_map = {
        row.item_id: _quantize(row.quantity)
        for row in ShopStock.objects.filter(shop=shop, item_id__in=item_ids).only(
            "item_id", "quantity"
        )
    }
    item_meta = {
        row.pk: row
        for row in Item.objects.filter(pk__in=item_ids).only("id", "name", "category")
    }

    lines = []
    max_ts = None
    for entry in entries:
        item = item_meta.get(entry.item_id)
        if item is None:
            continue
        live_system = stock_map.get(entry.item_id, _ZERO)
        physical = _quantize(entry.physical_qty)
        system = _baseline_system(entry=entry, live_system=live_system)
        variance = variance_payload(system=system, physical=physical)
        updated_at = entry.updated_at
        if updated_at is not None and (max_ts is None or updated_at > max_ts):
            max_ts = updated_at
        lines.append(
            {
                "item_id": entry.item_id,
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
    count_date: date | None = None,
    profile=None,
    updated_by_label: str = "",
) -> dict:
    """Autosave one physical count for a shop/day against book qty frozen at save."""
    count_date = count_date or timezone.localdate()
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
    live_system = _quantize(
        ShopStock.objects.filter(shop=shop, item=item)
        .values_list("quantity", flat=True)
        .first()
        or _ZERO
    )
    label = (updated_by_label or "").strip()[:160]

    if physical is None:
        OnlineStockTakeEntry.objects.filter(
            shop=shop, item=item, count_date=count_date
        ).delete()
        updated_at = None
        system = live_system
    else:
        # Refresh freeze on every save so a recount baselines against now.
        entry, _created = OnlineStockTakeEntry.objects.update_or_create(
            shop=shop,
            item=item,
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
    sync = build_online_stock_take_sync(shop=shop, count_date=count_date)
    return {
        "item_id": item.pk,
        "name": item.name,
        "category": (item.category or "").strip() or "Uncategorised",
        "system_qty": _qty_label(system),
        "live_system_qty": _qty_label(live_system),
        "physical_qty": _qty_label(physical) if physical is not None else "",
        "updated_by_label": label if physical is not None else "",
        "updated_at": (
            timezone.localtime(updated_at).isoformat() if updated_at is not None else ""
        ),
        "saved_at": timezone.localtime().strftime("%H:%M:%S"),
        "count_date": count_date.isoformat(),
        "revision": sync["revision"],
        **variance,
    }
