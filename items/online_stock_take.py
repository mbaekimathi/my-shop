"""Online stock take: live physical counts vs frozen book qty (not Stock Take)."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from items.models import (
    Item,
    OnlineStockTakeEntry,
    OnlineStockTakeSavedCount,
    ShopStock,
)

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


def _assert_editable_count_day(count_date: date) -> None:
    """Counts are only writable on their own day — other days are view-only."""
    if count_date != timezone.localdate():
        raise ValidationError(
            "Counts can only be updated on the day they were taken."
        )


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
    _assert_editable_count_day(count_date)
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


# ---------------------------------------------------------------------------
# Saved counts — a snapshot of one shop/day count kept for the record.


def _snapshot_line(line: dict) -> dict:
    """Flat, JSON-safe copy of one counted line for the saved payload."""
    return {
        "item_id": line.get("item_id"),
        "name": line.get("name") or "",
        "category": line.get("category") or "",
        "system_qty": str(line.get("system_qty") or ""),
        "physical_qty": str(line.get("physical_qty") or ""),
        "variance": line.get("variance"),
        "variance_kind": line.get("variance_kind"),
        "variance_label": line.get("variance_label"),
        "updated_by_label": (line.get("updated_by_label") or "").strip(),
    }


def record_online_stock_take_save(
    *,
    shop,
    count_date: date,
    stock_kind: str,
    catalog: dict,
    profile=None,
    saved_by_label: str = "",
):
    """Freeze the shop/day counted lines into a ``OnlineStockTakeSavedCount``.

    ``catalog`` is the live ``build_online_stock_take_catalog`` /
    ``build_shelf_online_take_catalog`` payload — its ``filled_lines`` become
    the saved lines so the record survives later recounts.
    """
    if shop is None:
        raise ValidationError("Select a shop for this stock count.")
    if stock_kind not in (
        OnlineStockTakeSavedCount.ITEM,
        OnlineStockTakeSavedCount.SHELF,
    ):
        stock_kind = OnlineStockTakeSavedCount.ITEM
    _assert_editable_count_day(count_date)
    filled = int(catalog.get("filled_count") or 0)
    if filled <= 0:
        raise ValidationError("Count at least one item before saving.")
    lines = [
        _snapshot_line(line) for line in (catalog.get("filled_lines") or [])
    ]
    record, _created = OnlineStockTakeSavedCount.objects.update_or_create(
        shop=shop,
        stock_kind=stock_kind,
        count_date=count_date,
        defaults={
            "saved_at": timezone.now(),
            "item_count": int(catalog.get("item_count") or 0),
            "filled_count": filled,
            "match_count": int(catalog.get("match_count") or 0),
            "excess_count": int(catalog.get("excess_count") or 0),
            "short_count": int(catalog.get("short_count") or 0),
            "saved_by": profile,
            "saved_by_label": (saved_by_label or "").strip()[:160],
            "payload": {"lines": lines},
        },
    )
    return record


def list_saved_online_stock_takes(*, stock_kind, shop=None, shops=None, limit=40):
    """Recent saved counts, newest first — one shop or the in-scope shop set.

    Rows are stamped ``is_today`` so the page can mark same-day records as
    open/updatable and older ones as locked.
    """
    saved = OnlineStockTakeSavedCount.objects.filter(stock_kind=stock_kind)
    if shop is not None:
        saved = saved.filter(shop=shop)
    elif shops is not None:
        saved = saved.filter(shop__in=shops)
    today = timezone.localdate()
    rows = list(
        saved.select_related("shop").order_by("-saved_at", "-id")[:limit]
    )
    for row in rows:
        row.is_today = row.count_date == today
    return rows


def saved_count_summary(record) -> dict:
    """Compact dict for the save-count JSON response / saved-list rendering."""
    saved_local = (
        timezone.localtime(record.saved_at) if record.saved_at else None
    )
    return {
        "id": record.pk,
        "shop_id": record.shop_id,
        "count_date": record.count_date.isoformat(),
        "count_date_label": record.count_date.strftime("%d %b %Y"),
        "is_today": record.count_date == timezone.localdate(),
        "shop_name": getattr(record.shop, "name", "") or "",
        "item_count": record.item_count,
        "filled_count": record.filled_count,
        "match_count": record.match_count,
        "excess_count": record.excess_count,
        "short_count": record.short_count,
        "saved_by_label": (record.saved_by_label or "").strip(),
        "saved_at_label": (
            saved_local.strftime("%d %b %Y %H:%M") if saved_local else ""
        ),
    }


_COUNT_HEADERS = [
    "Item",
    "Category",
    "Counted by",
    "System qty",
    "Counted qty",
    "Variance",
]
_COUNT_NUMERIC_FROM = 3


def _count_row_cells(lines) -> list:
    rows = []
    for line in lines or []:
        rows.append(
            [
                line.get("name") or "Item",
                (line.get("category") or "").strip() or "—",
                (line.get("updated_by_label") or "").strip() or "—",
                str(line.get("system_qty") or "0"),
                str(line.get("physical_qty") or "0"),
                (line.get("variance_label") or "").strip() or "—",
            ]
        )
    return rows


def build_online_take_count_document(
    *,
    kind_label: str,
    filename_prefix: str,
    shop=None,
    count_date: date | None = None,
    catalog: dict | None = None,
    saved=None,
) -> dict:
    """Print/PDF document for one shop/day count (live or a saved snapshot).

    Mirrors ``_shelf_report_document``: ``_movement_print_tables`` renders the
    report table for the a4/80mm/58mm sheet, ``pdf_params`` feeds
    ``build_stock_report_pdf`` for the A4 download.
    """
    from django.utils import timezone as dj_timezone
    from django.utils.text import slugify

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    logo_path = ""
    if getattr(company, "logo", None):
        try:
            logo_path = company.logo.path
        except Exception:
            logo_path = ""

    saved_label = ""
    if saved is not None:
        shop = saved.shop
        count_date = saved.count_date
        lines = list((saved.payload or {}).get("lines") or [])
        counters = {
            "item_count": saved.item_count,
            "filled_count": saved.filled_count,
            "match_count": saved.match_count,
            "excess_count": saved.excess_count,
            "short_count": saved.short_count,
        }
        saved_local = (
            timezone.localtime(saved.saved_at) if saved.saved_at else None
        )
        saved_label = (
            f"Saved {saved_local.strftime('%d %b %Y %H:%M')}"
            if saved_local is not None
            else "Saved"
        )
        if (saved.saved_by_label or "").strip():
            saved_label = f"{saved_label} by {saved.saved_by_label.strip()}"
    else:
        catalog = catalog or {}
        lines = list(catalog.get("filled_lines") or [])
        counters = {
            "item_count": catalog.get("item_count") or 0,
            "filled_count": catalog.get("filled_count") or 0,
            "match_count": catalog.get("match_count") or 0,
            "excess_count": catalog.get("excess_count") or 0,
            "short_count": catalog.get("short_count") or 0,
        }

    count_date = count_date or timezone.localdate()
    period_label = count_date.strftime("%d %b %Y")
    shop_label = getattr(shop, "name", "") or "—"
    rows = _count_row_cells(lines)
    subtitle = (
        "Physical count vs the book qty frozen when each item was counted"
        f" — {counters['filled_count']} of {counters['item_count']} items counted"
        f" ({counters['match_count']} match · {counters['excess_count']} excess"
        f" · {counters['short_count']} short)."
    )
    if saved_label:
        subtitle = f"{saved_label}. {subtitle}"
    generated_at = dj_timezone.localtime(dj_timezone.now())
    slug = slugify(shop_label) or "shop"
    filename = f"{filename_prefix}-{slug}-{count_date.isoformat()}.pdf"

    pdf_params = {
        "company_name": company_name,
        "page_mode": "report",
        "period_label": period_label,
        "event_filter_label": kind_label,
        "view_label": "Counted items",
        "shop_label": shop_label,
        "report_kind": "actual",
        "generated_at": generated_at,
        "logo_path": logo_path,
        "company_phone": (getattr(company, "phone_number", None) or "").strip(),
        "company_email": (getattr(company, "email", None) or "").strip(),
        "company_location": (getattr(company, "location", None) or "").strip(),
        "report_headers": _COUNT_HEADERS,
        "report_rows": rows,
        "report_numeric_from": _COUNT_NUMERIC_FROM,
    }
    return {
        "filename": filename,
        "page_mode": "report",
        "report_kind": "actual",
        "period_label": period_label,
        "shop_label": shop_label,
        "event_filter_label": kind_label,
        "view_label": "Counted items",
        "generated_at": generated_at,
        "item_count": counters["filled_count"],
        "report_table_title": "Counted items",
        "report_table_subtitle": subtitle,
        "report_numeric_from": _COUNT_NUMERIC_FROM,
        "report_headers": _COUNT_HEADERS,
        "report_rows": rows,
        "pdf_params": pdf_params,
    }
