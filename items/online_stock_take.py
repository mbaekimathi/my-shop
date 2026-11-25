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


def _sheet_order_key(row: dict) -> tuple:
    """Count-sheet order: uncounted rows A-Z, counted rows sink to the bottom."""
    return (
        row["physical_qty"] != "",
        (row["name"] or "").lower(),
        row["item_id"],
    )


def build_online_stock_take_catalog(*, shop, count_date: date | None = None) -> dict:
    """All active items for one shop/day, grouped by category, with frozen variance.

    Counted items stay in their category groups. ``filled_lines`` powers the
    floating cart summary (most recently updated first).
    """
    count_date = count_date or timezone.localdate()
    if shop is None:
        return {
            "filled_lines": [],
            "lines": [],
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
    lines = []
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
        lines.append(line)
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
    # Flat sheet — the page is one report-style table, not category cards.
    # Uncounted rows first A-Z; counted rows sink to the bottom (of the
    # sheet, and of each category group).
    lines.sort(key=_sheet_order_key)
    for group_lines in groups.values():
        group_lines.sort(key=_sheet_order_key)

    return {
        "filled_lines": filled_lines,
        "lines": lines,
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
    """Lightweight payload of current filled counts for cross-device live sync.

    ``system`` maps every active item's live book qty at this shop so
    uncounted rows on the page keep comparing against current stock.
    """
    count_date = count_date or timezone.localdate()
    if shop is None:
        return {
            "shop_id": None,
            "count_date": count_date.isoformat(),
            "revision": "0:",
            "lines": [],
            "filled_count": 0,
            "system": {},
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
        for row in ShopStock.objects.filter(shop=shop).only(
            "item_id", "quantity"
        )
    }
    system_by_item = {
        str(pk): _qty_label(stock_map.get(pk, _ZERO))
        for pk in Item.objects.filter(is_suspended=False).values_list(
            "id", flat=True
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
        "system": system_by_item,
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
    """Recent saved counts, newest first — one shop or the in-scope shop set."""
    saved = OnlineStockTakeSavedCount.objects.filter(stock_kind=stock_kind)
    if shop is not None:
        saved = saved.filter(shop=shop)
    elif shops is not None:
        saved = saved.filter(shop__in=shops)
    return list(
        saved.select_related("shop").order_by("-saved_at", "-id")[:limit]
    )


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
    "Counted by",
    "System qty",
    "Counted qty",
    "Variance",
]
_COUNT_NUMERIC_FROM = 2


_COUNT_KIND_ORDER = {"excess": 0, "short": 1, "match": 2}


def _ordered_count_lines(lines) -> list:
    """Report order — excess first, then short, then match."""
    return sorted(
        lines or [],
        key=lambda line: _COUNT_KIND_ORDER.get(
            line.get("variance_kind"), len(_COUNT_KIND_ORDER)
        ),
    )


def _count_row_cells(lines) -> list:
    rows = []
    for line in _ordered_count_lines(lines):
        rows.append(
            [
                line.get("name") or "Item",
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
    ordered_lines = _ordered_count_lines(lines)
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
        "kind_label": kind_label,
        "count_lines": ordered_lines,
        "counters": counters,
        "saved_label": saved_label,
        "subtitle": subtitle,
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
        "count_lines": ordered_lines,
        "counters": counters,
        "report_table_title": "Counted items",
        "report_table_subtitle": subtitle,
        "report_numeric_from": _COUNT_NUMERIC_FROM,
        "report_headers": _COUNT_HEADERS,
        "report_rows": rows,
        "pdf_params": pdf_params,
    }


# ---------------------------------------------------------------------------
# Stock count PDF — dedicated modern A4 layout for screen + print viewing.


def build_online_take_count_pdf(document: dict) -> bytes:
    """Branded A4 PDF for a count sheet (live or saved snapshot).

    Built for reading on phones/tablets as much as print: few columns, roomy
    9pt body type, colour-coded variance, KPI cards and a sign-off block.
    """
    from io import BytesIO
    from pathlib import Path

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from core.pdf_fonts import (
        MANROPE_PDF,
        MANROPE_PDF_BOLD,
        register_manrope_pdf_fonts,
    )

    register_manrope_pdf_fonts()

    params = document.get("pdf_params") or {}
    lines = document.get("count_lines") or []
    counters = document.get("counters") or {}
    kind_label = (document.get("event_filter_label") or "Stock count").strip()
    period_label = params.get("period_label") or "—"
    shop_label = params.get("shop_label") or "—"
    subtitle = params.get("subtitle") or ""
    saved_label = params.get("saved_label") or ""
    company_name = (params.get("company_name") or "MY-SHOP").strip() or "MY-SHOP"
    generated = params.get("generated_at")
    if generated is None:
        generated = timezone.localtime(timezone.now())
    stamp = generated.strftime("%d %b %Y · %H:%M")

    resolved_logo = ""
    logo_path = params.get("logo_path") or ""
    if logo_path and Path(logo_path).is_file():
        resolved_logo = logo_path

    page_size = A4
    width, height = page_size
    left_m = 12 * mm
    right_m = 12 * mm
    top_m = 24 * mm
    bottom_m = 15 * mm

    ink = colors.HexColor("#0f172a")
    muted = colors.HexColor("#5b6b7c")
    mist = colors.HexColor("#8b98a9")
    line = colors.HexColor("#e2e8f0")
    line_strong = colors.HexColor("#cbd5e1")
    soft = colors.HexColor("#f8fafc")
    accent = colors.HexColor("#0f766e")
    match_c = colors.HexColor("#15803d")
    excess_c = colors.HexColor("#b45309")
    short_c = colors.HexColor("#b91c1c")
    kind_colors = {
        "match": match_c,
        "excess": excess_c,
        "short": short_c,
    }

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=top_m,
        bottomMargin=bottom_m,
        title=f"{company_name} — {kind_label} {period_label}",
        author=company_name,
        subject=f"{kind_label} — {shop_label} — {period_label}",
    )

    styles = getSampleStyleSheet()

    def _esc(text) -> str:
        return (
            str(text if text is not None else "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    kicker_style = ParagraphStyle(
        "CountKicker",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=6.4,
        textColor=accent,
        leading=8,
        spaceAfter=0,
    )
    section_title = ParagraphStyle(
        "CountSection",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=11,
        textColor=ink,
        leading=13,
        spaceAfter=1,
    )
    section_sub = ParagraphStyle(
        "CountSectionSub",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7.6,
        textColor=muted,
        leading=9.6,
        spaceAfter=3,
    )
    cell = ParagraphStyle(
        "CountCell",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=8.6,
        textColor=ink,
        leading=10.6,
    )
    cell_bold = ParagraphStyle(
        "CountCellBold", parent=cell, fontName=MANROPE_PDF_BOLD
    )
    cell_right = ParagraphStyle(
        "CountCellRight", parent=cell_bold, alignment=TA_RIGHT
    )
    cell_var = ParagraphStyle(
        "CountCellVariance",
        parent=cell_bold,
        alignment=TA_RIGHT,
    )
    head_cell = ParagraphStyle(
        "CountHeadCell",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=colors.white,
        leading=8.4,
        alignment=TA_LEFT,
    )
    head_cell_right = ParagraphStyle(
        "CountHeadCellRight", parent=head_cell, alignment=TA_RIGHT
    )
    meta_label = ParagraphStyle(
        "CountMetaLabel",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=6,
        textColor=mist,
        leading=7.4,
    )
    meta_value = ParagraphStyle(
        "CountMetaValue",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=8.6,
        textColor=ink,
        leading=10.4,
    )
    kpi_label = ParagraphStyle(
        "CountKpiLabel",
        parent=meta_label,
        textColor=muted,
    )
    kpi_value = ParagraphStyle(
        "CountKpiValue",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=15,
        textColor=ink,
        leading=17,
    )
    sign_label = ParagraphStyle(
        "CountSignLabel",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=muted,
        leading=9,
    )

    class _NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdf_canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            page_count = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_chrome(page_count)
                pdf_canvas.Canvas.showPage(self)
            pdf_canvas.Canvas.save(self)

        def _draw_chrome(self, page_count: int):
            header_h = 17 * mm
            page_no = self._pageNumber

            # Accent rail along the very top edge.
            self.setFillColor(accent)
            self.rect(0, height - 2.6 * mm, width, 2.6 * mm, fill=1, stroke=0)

            # Header: logo + brand left, report meta right.
            text_x = left_m
            if resolved_logo:
                try:
                    logo_box = 10 * mm
                    self.setFillColor(colors.white)
                    self.setStrokeColor(line)
                    self.setLineWidth(0.6)
                    self.roundRect(
                        left_m,
                        height - header_h + 2.6 * mm,
                        logo_box,
                        logo_box,
                        1.4 * mm,
                        fill=1,
                        stroke=1,
                    )
                    self.drawImage(
                        resolved_logo,
                        left_m + 0.8 * mm,
                        height - header_h + 3.4 * mm,
                        width=logo_box - 1.6 * mm,
                        height=logo_box - 1.6 * mm,
                        preserveAspectRatio=True,
                        mask="auto",
                        anchor="c",
                    )
                    text_x = left_m + logo_box + 3 * mm
                except Exception:
                    text_x = left_m

            self.setFillColor(ink)
            self.setFont(MANROPE_PDF_BOLD, 11)
            self.drawString(text_x, height - 9.6 * mm, company_name)
            self.setFillColor(accent)
            self.setFont(MANROPE_PDF_BOLD, 6.4)
            self.drawString(
                text_x,
                height - 13.4 * mm,
                f"{kind_label.upper()} REPORT",
            )

            self.setFillColor(mist)
            self.setFont(MANROPE_PDF_BOLD, 6.2)
            self.drawRightString(
                width - right_m, height - 8.4 * mm, "COUNT DAY"
            )
            self.setFillColor(ink)
            self.setFont(MANROPE_PDF_BOLD, 9)
            self.drawRightString(
                width - right_m, height - 13 * mm, period_label
            )

            self.setStrokeColor(line)
            self.setLineWidth(0.7)
            self.line(
                left_m,
                height - header_h - 1.2 * mm,
                width - right_m,
                height - header_h - 1.2 * mm,
            )

            # Footer: contact + paging.
            self.setStrokeColor(line)
            self.line(left_m, 10.5 * mm, width - right_m, 10.5 * mm)
            self.setFillColor(accent)
            self.circle(
                left_m + 1.1 * mm, 6.4 * mm, 0.9 * mm, fill=1, stroke=0
            )
            contact_bits = [
                bit
                for bit in (
                    params.get("company_phone"),
                    params.get("company_email"),
                    params.get("company_location"),
                )
                if bit
            ]
            contact_line = (
                "  ·  ".join(contact_bits[:2]) if contact_bits else ""
            )
            left_note = f"Generated {stamp}"
            if contact_line:
                left_note = f"{left_note}  ·  {contact_line}"
            self.setFillColor(muted)
            self.setFont(MANROPE_PDF, 6.6)
            self.drawString(left_m + 4 * mm, 6.4 * mm, left_note)
            self.drawString(
                left_m + 4 * mm,
                3.6 * mm,
                f"{kind_label}  ·  {shop_label}",
            )
            self.setFillColor(ink)
            self.setFont(MANROPE_PDF_BOLD, 7.2)
            self.drawRightString(
                width - right_m, 6.4 * mm, f"{page_no} / {page_count}"
            )

    story: list = []

    # ---- Meta strip -------------------------------------------------------
    meta_cells = [
        ("SHOP", shop_label),
        ("COUNT DAY", period_label),
        ("STATUS", saved_label or "Live count"),
        ("GENERATED", stamp),
    ]
    meta_table = Table(
        [
            [
                [
                    Paragraph(_esc(label), meta_label),
                    Paragraph(_esc(value), meta_value),
                ]
                for label, value in meta_cells
            ]
        ],
        colWidths=[doc.width / len(meta_cells)] * len(meta_cells),
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), soft),
                ("BOX", (0, 0), (-1, -1), 0.6, line),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, line),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 3 * mm))

    # ---- KPI cards ---------------------------------------------------------
    kpi_data = [
        (
            "COUNTED",
            f"{counters.get('filled_count', 0)}"
            f" / {counters.get('item_count', 0)}",
            accent,
        ),
        ("MATCH", str(counters.get("match_count", 0)), match_c),
        ("EXCESS", str(counters.get("excess_count", 0)), excess_c),
        ("SHORT", str(counters.get("short_count", 0)), short_c),
    ]
    kpi_cols = []
    for label, value, _color in kpi_data:
        kpi_cols.append(
            [
                Paragraph(_esc(label), kpi_label),
                Paragraph(_esc(value), kpi_value),
            ]
        )
    kpi_table = Table(
        [kpi_cols], colWidths=[doc.width / len(kpi_cols)] * len(kpi_cols)
    )
    kpi_cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, line),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, line),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for idx, (_label, _value, color) in enumerate(kpi_data):
        kpi_cmds.append(("LINEABOVE", (idx, 0), (idx, 0), 1.8, color))
    kpi_table.setStyle(TableStyle(kpi_cmds))
    story.append(kpi_table)
    story.append(Spacer(1, 4 * mm))

    # ---- Counted items table ------------------------------------------------
    story.append(
        KeepTogether(
            [
                Paragraph(_esc("COUNT SHEET"), kicker_style),
                Paragraph(_esc("Counted items"), section_title),
                Paragraph(_esc(subtitle), section_sub),
            ]
        )
    )

    head = [
        Paragraph("ITEM", head_cell),
        Paragraph("COUNTED BY", head_cell),
        Paragraph("SYSTEM QTY", head_cell_right),
        Paragraph("COUNTED QTY", head_cell_right),
        Paragraph("VARIANCE", head_cell_right),
    ]
    body = [head]
    row_kinds = []
    for entry in lines:
        body.append(
            [
                Paragraph(_esc(entry.get("name") or "Item"), cell_bold),
                Paragraph(
                    _esc((entry.get("updated_by_label") or "").strip() or "—"),
                    cell,
                ),
                Paragraph(_esc(entry.get("system_qty") or "0"), cell_right),
                Paragraph(_esc(entry.get("physical_qty") or "0"), cell_right),
                Paragraph(
                    _esc(
                        (entry.get("variance_label") or "").strip() or "—"
                    ),
                    cell_var,
                ),
            ]
        )
        row_kinds.append(entry.get("variance_kind"))

    if len(body) == 1:
        body.append(
            [
                Paragraph("No items counted for this day.", cell),
                Paragraph("—", cell),
                Paragraph("—", cell_right),
                Paragraph("—", cell_right),
                Paragraph("—", cell_var),
            ]
        )
        row_kinds.append(None)

    col_widths = [
        doc.width * 0.30,
        doc.width * 0.20,
        doc.width * 0.15,
        doc.width * 0.15,
        doc.width * 0.20,
    ]
    table = Table(body, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), ink),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 1.6, accent),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, soft]),
        ("BOX", (0, 0), (-1, -1), 0.7, line_strong),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, line),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
    ]
    for r, kind in enumerate(row_kinds, start=1):
        color = kind_colors.get(kind)
        if color is not None:
            cmds.append(("TEXTCOLOR", (4, r), (4, r), color))
    table.setStyle(TableStyle(cmds))
    story.append(table)
    story.append(Spacer(1, 6 * mm))

    # ---- Sign-off ------------------------------------------------------------
    sign = Table(
        [
            ["", "", ""],
            [
                Paragraph("COUNTED BY", sign_label),
                Paragraph("REVIEWED BY", sign_label),
                Paragraph("DATE SIGNED", sign_label),
            ],
        ],
        colWidths=[doc.width * 0.36, doc.width * 0.36, doc.width * 0.28],
    )
    sign.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, line_strong),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph(
                    _esc(
                        "Variance compares the physical count to the book "
                        "qty frozen when each item was counted."
                    ),
                    section_sub,
                ),
                sign,
            ]
        )
    )

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
