"""All-shops stock movement day page (chatbot /all/stock/, /chat/stock/).

Buckets one local day's stock ledger into the sections the page offers —
stock in, stock out, traded in, traded out, transfers and suppliers —
each with unit counts and a money value.

Three ledgers, picked by ``scope``/``kind``:

- ``scope="shops"`` + ``kind="items"`` — the shop-floor ``StockMovement``
  pipeline (all shops combined, or one shop via ``shop_id``; a shop also
  sees transfers where it is the receiving side).
- ``scope="shops"`` + ``kind="shelf"`` — per-shop ``ShelfStockMovement``
  rows (registers count as stock in; transfers/trades ride on ``reason``).
- ``scope="store"`` — the central-store ``StoreStockMovement`` ledger
  (item stock only; POS rows mirror shop receipts, trades ride on
  ``reason``, and identical notes group into one movement card).

Classification mirrors the stock-management reports: ``entry_source``
wins on the shop ledger, with legacy rows falling back to the per-line
``reason`` (older trade movements predate ``entry_source``). A
trade-tagged customer return ("Trade return on …" notes) counts as
traded in.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .myshop_models import (
    Item,
    ShelfItem,
    ShelfStockMovement,
    Shop,
    StockMovement,
    StockMovementLine,
    StoreStockMovement,
)

SECTION_ORDER = ("in", "out", "trade_in", "trade_out", "transfers")

SECTION_LABELS = {
    "in": "Stock in",
    "out": "Stock out",
    "trade_in": "Traded in",
    "trade_out": "Traded out",
    "transfers": "Transfers",
}

# ?view= values that render a section detail page ("" is the summary board,
# "suppliers" and "report" are the two extra views).
VIEW_KEYS = frozenset(SECTION_ORDER) | {"suppliers", "report"}

SCOPE_KEYS = ("shops", "store")
SCOPE_LABELS = {"shops": "Shops", "store": "Company store"}
KIND_KEYS = ("items", "shelf")
KIND_LABELS = {"items": "Item stock", "shelf": "Shelf stock"}

_SRC_TRADE_IN = {"trade_in", "trade_exchange"}
_SRC_TRADE_OUT = {"trade_out", "trade_in", "trade_exchange"}
_REASON_TRADE = {"trade_in", "trade_out"}

_REASON_LABELS = {
    "waste": "Waste",
    "transfer": "Transfer",
    "display": "Display",
    "return": "Supplier return",
    "trade_out": "Trade out",
    "trade_in": "Trade in",
    "gift": "Gift",
    "custom": "Custom",
}

_SOURCE_LABELS = {
    "buy_items": "Buy items",
    "stock_management": "Stock management",
    "customer_return": "Customer return",
    "trade_out": "Trade out",
    "trade_in": "Trade in",
    "trade_exchange": "Trade exchange",
}

_SHELF_TYPE_LABELS = {
    "register": "Registered",
    "in": "Shelf stock in",
    "out": "Shelf stock out",
}

_PAYMENT_LABELS = {"unpaid": "Unpaid", "paid": "Paid", "partial": "Partial"}
_REQUEST_LABELS = {
    "pending": "Pending",
    "fulfilled": "Fulfilled",
    "declined": "Declined",
}

CENTRAL_STORE_LABEL = "Company store"


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _day_bounds(day):
    tz = timezone.get_current_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _employee_names(ids: set) -> dict:
    if not ids:
        return {}
    from employees.models import EmployeeProfile

    names = {}
    for profile in (
        EmployeeProfile.objects.filter(pk__in=ids).select_related("user")
    ):
        user = getattr(profile, "user", None)
        if user is None:
            names[profile.pk] = ""
        else:
            names[profile.pk] = (
                user.get_full_name() or user.username or ""
            )
    return names


def _shop_names(ids: set) -> dict:
    if not ids:
        return {}
    return {s.pk: s.name for s in Shop.objects.filter(pk__in=ids)}


def _fresh_sections() -> dict:
    return {
        key: {"key": key, "label": label, "rows": [],
              "moves": 0, "units": Decimal("0"), "value": Decimal("0")}
        for key, label in SECTION_LABELS.items()
    }


def _empty_result() -> dict:
    return {
        "sections": [_fresh_sections()[k] for k in SECTION_ORDER],
        "suppliers": [],
    }


def _add_supplier(
    suppliers: dict, name, phone, move_id, qty, value, line_row, shop_name,
    paid=0,
):
    sup = suppliers.setdefault(
        name,
        {
            "name": name,
            "phone": phone,
            "movement_ids": set(),
            "units": Decimal("0"),
            "value": Decimal("0"),
            "move_value": {},
            "move_paid": {},
            "lines": [],
        },
    )
    sup["movement_ids"].add(move_id)
    sup["units"] += qty
    sup["value"] += value
    # Per-movement totals so a supplier's due is max(0, their value in the
    # movement − the movement's amount_paid) — same rule as the analytics
    # supplier ledgers (_due_amount / _registered_supplier_balance).
    sup["move_value"][move_id] = (
        sup["move_value"].get(move_id, Decimal("0")) + value
    )
    sup["move_paid"][move_id] = _dec(paid)
    sup["lines"].append(line_row | {"shop": shop_name})
    if not sup["phone"] and phone:
        sup["phone"] = phone


def _supplier_rows(suppliers: dict) -> list:
    rows = []
    for s in suppliers.values():
        due = sum(
            max(
                Decimal("0"),
                v - s["move_paid"].get(move_id, Decimal("0")),
            )
            for move_id, v in s["move_value"].items()
        )
        rows.append(
            {
                "name": s["name"],
                "phone": s["phone"],
                "moves": len(s["movement_ids"]),
                "units": s["units"],
                "value": s["value"],
                "paid": sum(s["move_paid"].values(), Decimal("0")),
                "due": due,
                "lines": s["lines"],
            }
        )
    return sorted(rows, key=lambda r: (-r["value"], r["name"].lower()))


# ---------------------------------------------------------------------------
# Shop-floor item ledger (StockMovement)
# ---------------------------------------------------------------------------


def _bucket(mv, reasons: set) -> str:
    src = (mv.entry_source or "").strip()
    if mv.movement_type == "request":
        return "transfers"
    if mv.movement_type == "in":
        if src in _SRC_TRADE_IN or "trade_in" in reasons:
            return "trade_in"
        if src == "customer_return" and "trade" in (mv.notes or "").lower():
            return "trade_in"
        return "in"
    if mv.movement_type == "out":
        if src in _SRC_TRADE_OUT or reasons & _REASON_TRADE:
            return "trade_out"
        return "out"
    return "in"


def _line_value(bucket: str, line) -> Decimal:
    qty = _dec(line.quantity)
    if bucket in ("in", "trade_in"):
        unit = line.buying_price if line.buying_price is not None else line.unit_cost
    else:
        unit = line.unit_cost or line.buying_price or 0
    return qty * _dec(unit)


def _build_item_day(start, end, shop_id=None) -> dict:
    qs = StockMovement.objects.filter(
        created_at__gte=start, created_at__lt=end
    )
    if shop_id:
        qs = qs.filter(Q(shop_id=shop_id) | Q(requested_from_shop_id=shop_id))
    moves = list(qs.order_by("created_at", "id"))
    if not moves:
        return _empty_result()

    move_ids = [m.pk for m in moves]
    lines_by_move: dict[int, list] = {}
    for line in (
        StockMovementLine.objects.filter(movement_id__in=move_ids)
        .order_by("id")
    ):
        lines_by_move.setdefault(line.movement_id, []).append(line)

    shop_names = _shop_names(
        {
            sid
            for m in moves
            for sid in (m.shop_id, m.requested_from_shop_id)
            if sid
        }
    )
    emp_names = _employee_names(
        {m.created_by_id for m in moves if m.created_by_id}
    )

    sections = _fresh_sections()
    suppliers: dict[str, dict] = {}

    for mv in moves:
        mlines = lines_by_move.get(mv.pk, [])
        reasons = {
            (l.reason or "").strip() for l in mlines if (l.reason or "").strip()
        }
        bucket = _bucket(mv, reasons)
        shop_name = shop_names.get(mv.shop_id, "—")

        line_rows = []
        qty_total = Decimal("0")
        value_total = Decimal("0")
        for line in mlines:
            qty = _dec(line.quantity)
            if qty == 0:
                continue  # zero-qty lines carry no movement
            value = _line_value(bucket, line)
            qty_total += qty
            value_total += value
            supplier = (line.supplier_name or "").strip()
            phone = " ".join(
                bit
                for bit in (
                    (line.supplier_phone_country_code or "").strip(),
                    (line.supplier_phone_number or "").strip(),
                )
                if bit
            )
            line_rows.append(
                {
                    "item": line.item_name
                    or (f"Item #{line.item_id}" if line.item_id else "Item"),
                    "qty": qty,
                    "unit": (
                        line.buying_price
                        if bucket in ("in", "trade_in")
                        else (line.unit_cost or line.buying_price)
                    ),
                    "value": value,
                    "reason": _REASON_LABELS.get(
                        (line.reason or "").strip(), ""
                    ),
                    "supplier": supplier,
                    "supplier_phone": phone,
                    "serials": len(line.serial_numbers or []),
                    "note": (line.note or "").strip(),
                }
            )
            if bucket == "in" and supplier:
                _add_supplier(
                    suppliers, supplier, phone, mv.pk, qty, value,
                    line_rows[-1], shop_name, paid=mv.amount_paid,
                )

        if not line_rows:
            continue  # nothing actually moved — hide the movement
        if mv.movement_type == "request":
            status = _REQUEST_LABELS.get(
                (mv.request_status or "").strip(), mv.request_status or ""
            )
            head = (
                f"{shop_name} → "
                f"{shop_names.get(mv.requested_from_shop_id, '—')}"
            )
        else:
            status = _PAYMENT_LABELS.get(
                (mv.payment_status or "").strip(), ""
            )
            head = shop_name

        sections[bucket]["rows"].append(
            {
                "id": mv.pk,
                "ts": mv.created_at.isoformat(),
                "when": timezone.localtime(mv.created_at).strftime("%H:%M"),
                "head": head,
                "status": status,
                "source": _SOURCE_LABELS.get(
                    (mv.entry_source or "").strip(), ""
                ),
                "by": emp_names.get(mv.created_by_id, ""),
                "note": (mv.notes or "").strip(),
                "qty": qty_total,
                "value": value_total,
                "lines": line_rows,
            }
        )
        sections[bucket]["moves"] += 1
        sections[bucket]["units"] += qty_total
        sections[bucket]["value"] += value_total

    return {
        "sections": [sections[key] for key in SECTION_ORDER],
        "suppliers": _supplier_rows(suppliers),
    }


# ---------------------------------------------------------------------------
# Shelf ledger (ShelfStockMovement) — one row per shelf item moved
# ---------------------------------------------------------------------------


def _shelf_bucket(mv) -> str:
    reason = (mv.reason or "").strip()
    mt = mv.movement_type
    if reason == "transfer":
        return "transfers"
    if mt in ("in", "register"):
        return "trade_in" if reason == "trade_in" else "in"
    # out
    if reason in _REASON_TRADE:
        return "trade_out"
    return "out"


def _build_shelf_day(start, end, shop_id=None) -> dict:
    item_qs = ShelfItem.objects.all()
    if shop_id:
        item_qs = item_qs.filter(shop_id=shop_id)
    items = {i.pk: i for i in item_qs}
    if not items:
        return _empty_result()

    moves = list(
        ShelfStockMovement.objects.filter(
            shelf_item_id__in=list(items),
            created_at__gte=start,
            created_at__lt=end,
        ).order_by("created_at", "id")
    )
    if not moves:
        return _empty_result()

    shop_names = _shop_names({i.shop_id for i in items.values()})
    emp_names = _employee_names(
        {m.created_by_id for m in moves if m.created_by_id}
    )

    sections = _fresh_sections()
    suppliers: dict[str, dict] = {}

    for mv in moves:
        item = items.get(mv.shelf_item_id)
        shop_name = shop_names.get(getattr(item, "shop_id", None), "—")
        bucket = _shelf_bucket(mv)
        qty = _dec(mv.quantity)
        if qty == 0:
            continue  # zero-qty rows carry no movement
        unit = mv.buying_price
        value = qty * _dec(unit)

        supplier = (mv.supplier_name or "").strip()
        phone = (mv.supplier_phone or "").strip()
        line_row = {
            "item": item.name if item else "Deleted item",
            "qty": qty,
            "unit": unit,
            "value": value,
            "reason": _REASON_LABELS.get((mv.reason or "").strip(), ""),
            "supplier": supplier,
            "supplier_phone": phone,
            "serials": 0,
            "note": "",
        }
        if bucket == "in" and supplier:
            _add_supplier(
                suppliers, supplier, phone, mv.pk, qty, value,
                line_row, shop_name, paid=mv.amount_paid,
            )

        status = _PAYMENT_LABELS.get((mv.payment_status or "").strip(), "")
        sections[bucket]["rows"].append(
            {
                "id": mv.pk,
                "ts": mv.created_at.isoformat(),
                "when": timezone.localtime(mv.created_at).strftime("%H:%M"),
                "head": shop_name,
                "status": status,
                "source": _SHELF_TYPE_LABELS.get(mv.movement_type, ""),
                "by": emp_names.get(mv.created_by_id, ""),
                "note": (mv.note or "").strip(),
                "qty": qty,
                "value": value,
                "lines": [line_row],
            }
        )
        sections[bucket]["moves"] += 1
        sections[bucket]["units"] += qty
        sections[bucket]["value"] += value

    return {
        "sections": [sections[key] for key in SECTION_ORDER],
        "suppliers": _supplier_rows(suppliers),
    }


# ---------------------------------------------------------------------------
# Central store ledger (StoreStockMovement) — item stock only
# ---------------------------------------------------------------------------


def _store_bucket(mv) -> str:
    reason = (mv.reason or "").strip()
    if reason == "transfer":
        return "transfers"
    if mv.movement_type == "in":
        return "trade_in" if reason == "trade_in" else "in"
    if reason in _REASON_TRADE:
        return "trade_out"
    return "out"


def _build_store_day(start, end) -> dict:
    rows = list(
        StoreStockMovement.objects.filter(
            created_at__gte=start, created_at__lt=end
        ).order_by("created_at", "id")
    )
    if not rows:
        return _empty_result()

    item_names = {
        i.pk: i.name
        for i in Item.objects.filter(
            pk__in={r.item_id for r in rows}
        )
    }
    emp_names = _employee_names(
        {
            eid
            for r in rows
            for eid in (r.created_by_id, r.received_by_id)
            if eid
        }
    )

    # One ledger row per item — group rows that share (note, second) into a
    # single movement card so a multi-line receipt reads as one movement.
    groups: list[dict] = []
    for r in rows:
        key = (
            (r.note or "").strip(),
            timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M"),
            (r.source or "").strip(),
            r.created_by_id,
            r.movement_type,
        )
        if groups and groups[-1]["key"] == key:
            groups[-1]["rows"].append(r)
        else:
            groups.append({"key": key, "rows": [r]})

    sections = _fresh_sections()
    suppliers: dict[str, dict] = {}

    for group in groups:
        members = group["rows"]
        # Grouped rows share a note/receipt so they land in one bucket —
        # key off the first row.
        bucket = _store_bucket(members[0])

        line_rows = []
        qty_total = Decimal("0")
        value_total = Decimal("0")
        group_notes = []
        status = ""
        for r in members:
            rbucket = _store_bucket(r)
            qty = _dec(r.quantity)
            if qty == 0:
                continue  # zero-qty rows carry no movement
            unit = r.buying_price
            value = qty * _dec(unit)
            qty_total += qty
            value_total += value
            supplier = (r.supplier_name or "").strip()
            phone = (r.supplier_phone or "").strip()
            if not status:
                status = _PAYMENT_LABELS.get(
                    (r.payment_status or "").strip(), ""
                )
            note = (r.note or "").strip()
            if note and note not in group_notes:
                group_notes.append(note)
            line_rows.append(
                {
                    "item": item_names.get(
                        r.item_id, f"Item #{r.item_id}"
                    ),
                    "qty": qty,
                    "unit": unit,
                    "value": value,
                    "reason": _REASON_LABELS.get(
                        (r.reason or "").strip(), ""
                    ),
                    "supplier": supplier,
                    "supplier_phone": phone,
                    "serials": 0,
                    "note": "",
                }
            )
            if rbucket == "in" and supplier:
                # StoreStockMovement carries no amount_paid — a row marked
                # paid counts fully settled, anything else is fully due.
                _add_supplier(
                    suppliers, supplier, phone, r.pk, qty, value,
                    line_rows[-1], CENTRAL_STORE_LABEL,
                    paid=value
                    if (r.payment_status or "").strip() == "paid"
                    else 0,
                )
            sections[bucket]["units"] += qty
            sections[bucket]["value"] += value

        if not line_rows:
            continue  # nothing actually moved — hide the group
        first = members[0]
        by = emp_names.get(first.created_by_id, "")
        received = emp_names.get(first.received_by_id, "")
        sections[bucket]["rows"].append(
            {
                "id": first.pk,
                "ts": first.created_at.isoformat(),
                "when": timezone.localtime(first.created_at).strftime(
                    "%H:%M"
                ),
                "head": CENTRAL_STORE_LABEL,
                "status": status,
                "source": (
                    "POS receipt" if (first.source or "").strip() else ""
                ),
                "by": by if by == received or not received else (
                    f"{by} → {received}" if by else received
                ),
                "note": " · ".join(group_notes),
                "qty": qty_total,
                "value": value_total,
                "lines": line_rows,
            }
        )
        sections[bucket]["moves"] += 1

    return {
        "sections": [sections[key] for key in SECTION_ORDER],
        "suppliers": _supplier_rows(suppliers),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_stock_day(day, *, shop_id=None, kind="items", scope="shops") -> dict:
    """Section totals + per-movement detail rows for one local day."""
    start, end = _day_bounds(day)
    if scope == "store":
        return _build_store_day(start, end)
    if kind == "shelf":
        return _build_shelf_day(start, end, shop_id)
    return _build_item_day(start, end, shop_id)
