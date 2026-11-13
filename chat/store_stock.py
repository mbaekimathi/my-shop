"""Central store stock in/out/report — mirrors MY-SHOP store_stock_services.

Writes `items_storestock` / `items_storestockmovement` only. Never touches
shop floor stock (`items_shopstock`, `items_item.stock`, serials, suppliers).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .myshop_models import Item, StoreStock, StoreStockMovement

QTY_QUANTUM = Decimal("0.001")
ZERO = Decimal("0")
MODE_IN = "in"
MODE_OUT = "out"


class StoreError(Exception):
    """User-facing store stock error."""


def _parse_qty(raw) -> Decimal:
    try:
        qty = Decimal(str(raw or "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise StoreError("Enter a valid quantity.") from exc
    if qty <= 0:
        raise StoreError("Quantity must be greater than zero.")
    return qty.quantize(QTY_QUANTUM)


def store_qty_for_item(item_id) -> Decimal:
    row = StoreStock.objects.filter(item_id=item_id).only("quantity").first()
    return row.quantity if row is not None else ZERO


def list_store_items(*, query: str = "") -> list[dict]:
    """Active catalog items with current central-store qty."""
    q = (query or "").strip()
    items = (
        Item.objects.filter(is_suspended=False)
        .order_by("category", "name")
        .only("id", "name", "category")
    )
    if q:
        filt = Q(name__icontains=q) | Q(category__icontains=q)
        if q.isdigit():
            filt = filt | Q(pk=int(q))
        items = items.filter(filt)
    item_list = list(items)
    qty_by_item = {
        row.item_id: row.quantity
        for row in StoreStock.objects.filter(
            item_id__in=[item.pk for item in item_list]
        ).only("item_id", "quantity")
    }
    rows = []
    for item in item_list:
        qty = qty_by_item.get(item.pk) or ZERO
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "label": f"{item.name} · {item.category}",
                "store_qty": str(qty),
            }
        )
    return rows


def search_store_items(*, query: str, limit: int = 12) -> list[dict]:
    rows = list_store_items(query=query)
    return rows[: max(1, min(int(limit or 12), 30))]


def search_active_employees(*, query: str, limit: int = 12) -> list[dict]:
    q = (query or "").strip()
    limit = max(1, min(int(limit or 12), 30))
    if connection.vendor == "sqlite":
        full_name = "(u.first_name || ' ' || u.last_name)"
    else:
        full_name = "CONCAT(u.first_name, ' ', u.last_name)"
    sql = (
        "SELECT ep.id, u.first_name, u.last_name, u.username "
        "FROM employees_employeeprofile ep "
        "JOIN auth_user u ON u.id = ep.user_id "
        "WHERE ep.status = 'active' "
    )
    params: list = []
    if q:
        sql += (
            "AND (u.first_name LIKE %s OR u.last_name LIKE %s "
            f"OR u.username LIKE %s OR {full_name} LIKE %s) "
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql += "ORDER BY u.first_name, u.last_name, u.username LIMIT %s"
    params.append(limit)
    rows = []
    with connection.cursor() as cur:
        cur.execute(sql, params)
        for pk, first, last, username in cur.fetchall():
            name = f"{first or ''} {last or ''}".strip() or (username or "").strip()
            if not name:
                continue
            rows.append({"id": pk, "name": name, "label": name})
    return rows


def _employee_display_name(emp_pk) -> str:
    if not emp_pk:
        return "—"
    with connection.cursor() as cur:
        cur.execute(
            "SELECT u.first_name, u.last_name, u.username "
            "FROM employees_employeeprofile ep "
            "JOIN auth_user u ON u.id = ep.user_id "
            "WHERE ep.id=%s LIMIT 1",
            [emp_pk],
        )
        row = cur.fetchone()
    if row is None:
        return "—"
    name = f"{row[0] or ''} {row[1] or ''}".strip() or (row[2] or "").strip()
    return name or "—"


def _resolve_item(item_id) -> Item:
    try:
        item = Item.objects.get(pk=int(item_id), is_suspended=False)
    except (TypeError, ValueError, Item.DoesNotExist) as exc:
        raise StoreError("Select a valid item.") from exc
    return item


def _resolve_counterpart(received_by_id, *, mode: str) -> int:
    try:
        pk = int(received_by_id)
    except (TypeError, ValueError) as exc:
        if mode == MODE_IN:
            raise StoreError("Select who delivered the stock.") from exc
        raise StoreError("Select the employee who received the stock.") from exc
    with connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM employees_employeeprofile "
            "WHERE id=%s AND status='active' LIMIT 1",
            [pk],
        )
        row = cur.fetchone()
    if row is None:
        if mode == MODE_IN:
            raise StoreError("Select who delivered the stock.")
        raise StoreError("Select the employee who received the stock.")
    return int(row[0])


def _parse_lines(raw_lines) -> list[tuple[int, Decimal]]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise StoreError("Enter a quantity for at least one item.")
    lines = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        item_raw = raw.get("item_id")
        try:
            item_id = int(item_raw)
        except (TypeError, ValueError):
            continue
        text = str(raw.get("quantity") or "").strip()
        if not text:
            continue
        try:
            qty = _parse_qty(text)
        except StoreError as exc:
            raise StoreError(f"Invalid quantity for item {item_id}.") from exc
        lines.append((item_id, qty))
    if not lines:
        raise StoreError("Enter a quantity for at least one item.")
    return lines


@transaction.atomic
def record_store_stock_in(
    *,
    actor_id: int,
    item_id,
    quantity,
    received_by_id,
    note: str = "",
) -> StoreStockMovement:
    if not actor_id:
        raise StoreError("Sign in to record store stock in.")
    item = _resolve_item(item_id)
    qty = _parse_qty(quantity)
    receiver_id = _resolve_counterpart(received_by_id, mode=MODE_IN)
    stock, _ = StoreStock.objects.select_for_update().get_or_create(
        item_id=item.pk,
        defaults={"quantity": ZERO},
    )
    stock.quantity = (stock.quantity or ZERO) + qty
    stock.save(update_fields=["quantity", "updated_at"])
    return StoreStockMovement.objects.create(
        movement_type=MODE_IN,
        item_id=item.pk,
        quantity=qty,
        balance_after=stock.quantity,
        created_by_id=(actor_id or None),
        received_by_id=receiver_id,
        note=(note or "").strip()[:255],
    )


@transaction.atomic
def record_store_stock_out(
    *,
    actor_id: int,
    item_id,
    quantity,
    received_by_id,
    note: str = "",
) -> StoreStockMovement:
    if not actor_id:
        raise StoreError("Sign in to record store stock out.")
    item = _resolve_item(item_id)
    qty = _parse_qty(quantity)
    receiver_id = _resolve_counterpart(received_by_id, mode=MODE_OUT)
    stock, _ = StoreStock.objects.select_for_update().get_or_create(
        item_id=item.pk,
        defaults={"quantity": ZERO},
    )
    current = stock.quantity or ZERO
    if qty > current:
        raise StoreError(
            f"Store only has {current} available — cannot stock out {qty}."
        )
    stock.quantity = current - qty
    stock.save(update_fields=["quantity", "updated_at"])
    return StoreStockMovement.objects.create(
        movement_type=MODE_OUT,
        item_id=item.pk,
        quantity=qty,
        balance_after=stock.quantity,
        created_by_id=(actor_id or None),
        received_by_id=receiver_id,
        note=(note or "").strip()[:255],
    )


@transaction.atomic
def record_store_stock_lines(
    *,
    mode: str,
    actor_id: int,
    lines,
    received_by_id,
    note: str = "",
) -> list[StoreStockMovement]:
    mode = (mode or "").strip().lower()
    if mode not in {MODE_IN, MODE_OUT}:
        raise StoreError("Unknown store stock mode.")
    parsed = _parse_lines(lines)
    movements = []
    for item_id, qty in parsed:
        if mode == MODE_IN:
            movements.append(
                record_store_stock_in(
                    actor_id=actor_id,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=received_by_id,
                    note=note,
                )
            )
        else:
            movements.append(
                record_store_stock_out(
                    actor_id=actor_id,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=received_by_id,
                    note=note,
                )
            )
    return movements


def _movement_payload(movement: StoreStockMovement, item: Item | None = None) -> dict:
    if item is None:
        item = Item.objects.filter(pk=movement.item_id).only("id", "name", "category").first()
    counterpart_label = (
        "Delivered by" if movement.movement_type == MODE_IN else "Received by"
    )
    return {
        "id": movement.pk,
        "movement_type": movement.movement_type,
        "movement_label": (
            "Stock in" if movement.movement_type == MODE_IN else "Stock out"
        ),
        "item_id": movement.item_id,
        "item_name": item.name if item else f"Item #{movement.item_id}",
        "item_category": item.category if item else "",
        "quantity": str(movement.quantity),
        "balance_after": str(movement.balance_after),
        "recorded_by": _employee_display_name(movement.created_by_id),
        "counterpart_label": counterpart_label,
        "counterpart": _employee_display_name(movement.received_by_id),
        "note": (movement.note or "").strip(),
        "when": timezone.localtime(movement.created_at).strftime("%d %b %Y · %H:%M"),
    }


def serialize_movements(movements: list[StoreStockMovement]) -> list[dict]:
    item_ids = {m.item_id for m in movements}
    items = {
        item.pk: item
        for item in Item.objects.filter(pk__in=item_ids).only("id", "name", "category")
    }
    return [_movement_payload(m, items.get(m.item_id)) for m in movements]


def store_stock_card(movements: list[StoreStockMovement], *, mode: str) -> dict:
    """WhatsApp-style card meta for a store stock-in / stock-out batch."""
    rows = serialize_movements(movements)
    if not rows:
        return {}
    first = rows[0]
    return {
        "store_stock": {
            "mode": mode,
            "title": "Store stock in" if mode == MODE_IN else "Store stock out",
            "items": [
                {
                    "name": row["item_name"],
                    "qty": row["quantity"],
                    "balance": row["balance_after"],
                }
                for row in rows
            ],
            "counterpart_label": first["counterpart_label"],
            "counterpart": first["counterpart"],
            "recorded_by": first["recorded_by"],
            "note": first["note"],
            "when": first["when"],
        }
    }


def store_stock_body(movements: list[StoreStockMovement], *, mode: str) -> str:
    rows = serialize_movements(movements)
    summary = ", ".join(f"{row['quantity']}× {row['item_name']}" for row in rows)
    if mode == MODE_IN:
        return f"Store stock in: {summary}"
    return f"Store stock out: {summary}"


def build_store_feed(*, limit: int = 40) -> list[dict]:
    """Recent store movements grouped into WhatsApp-style reference cards."""
    limit = max(1, min(int(limit or 40), 100))
    # Pull extra rows so batches still fill the feed after grouping.
    raw = list(StoreStockMovement.objects.order_by("-created_at", "-id")[: limit * 4])
    if not raw:
        return []

    groups: list[list[StoreStockMovement]] = []
    for movement in raw:
        if not groups:
            groups.append([movement])
            continue
        head = groups[-1][0]
        same_batch = (
            movement.movement_type == head.movement_type
            and movement.created_by_id == head.created_by_id
            and movement.received_by_id == head.received_by_id
            and (movement.note or "") == (head.note or "")
            and abs((movement.created_at - head.created_at).total_seconds()) <= 2
        )
        if same_batch:
            groups[-1].append(movement)
        else:
            groups.append([movement])

    feed = []
    for group in groups[:limit]:
        # Chronological item order inside the card (oldest line first).
        ordered = list(reversed(group))
        mode = ordered[0].movement_type
        card = store_stock_card(ordered, mode=mode)
        body = store_stock_body(ordered, mode=mode)
        feed.append(
            {
                "id": ordered[-1].pk,
                "body": body,
                "card": card,
                "when": timezone.localtime(ordered[-1].created_at).strftime(
                    "%H:%M"
                ),
                "day": timezone.localtime(ordered[-1].created_at).strftime(
                    "%d %b %Y"
                ),
            }
        )
    return feed


def _parse_day(raw: str):
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _range_bounds(params) -> tuple:
    """Return (start, end) datetimes in local tz, or (None, None) for all-time."""
    range_key = (params.get("range") or "all").strip().lower()
    tz = timezone.get_current_timezone()
    now = timezone.localtime()
    today = now.date()

    if range_key == "today":
        start = timezone.make_aware(datetime.combine(today, time.min), tz)
        end = start + timedelta(days=1)
        return start, end
    if range_key == "week":
        start_day = today - timedelta(days=today.weekday())
        start = timezone.make_aware(datetime.combine(start_day, time.min), tz)
        end = start + timedelta(days=7)
        return start, end
    if range_key == "month":
        start = timezone.make_aware(
            datetime.combine(today.replace(day=1), time.min), tz
        )
        if today.month == 12:
            end_day = today.replace(year=today.year + 1, month=1, day=1)
        else:
            end_day = today.replace(month=today.month + 1, day=1)
        end = timezone.make_aware(datetime.combine(end_day, time.min), tz)
        return start, end
    if range_key == "custom":
        day_from = _parse_day(params.get("from") or "")
        day_to = _parse_day(params.get("to") or "")
        if day_from is None and day_to is None:
            return None, None
        if day_from is None:
            day_from = day_to
        if day_to is None:
            day_to = day_from
        if day_to < day_from:
            day_from, day_to = day_to, day_from
        start = timezone.make_aware(datetime.combine(day_from, time.min), tz)
        end = timezone.make_aware(
            datetime.combine(day_to + timedelta(days=1), time.min), tz
        )
        return start, end
    return None, None


def build_store_stock_report(params) -> dict:
    """Period + type + item filtered central-store movement report."""
    start, end = _range_bounds(params)
    raw_item_id = (params.get("item_id") or "").strip()
    selected_item_id = None
    selected_item = None
    if raw_item_id.isdigit():
        selected_item = (
            Item.objects.filter(pk=int(raw_item_id), is_suspended=False)
            .only("id", "name", "category")
            .first()
        )
        if selected_item is not None:
            selected_item_id = selected_item.pk

    movement_type = (params.get("type") or "all").strip().lower()
    if movement_type not in {"all", MODE_IN, MODE_OUT}:
        movement_type = "all"

    qs = StoreStockMovement.objects.all()
    if start is not None and end is not None:
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    if selected_item_id is not None:
        qs = qs.filter(item_id=selected_item_id)
    if movement_type in {MODE_IN, MODE_OUT}:
        qs = qs.filter(movement_type=movement_type)

    qs = qs.order_by("-created_at", "-id")
    in_total = qs.filter(movement_type=MODE_IN).aggregate(total=Sum("quantity"))[
        "total"
    ] or ZERO
    out_total = qs.filter(movement_type=MODE_OUT).aggregate(total=Sum("quantity"))[
        "total"
    ] or ZERO
    movements = list(qs[:500])
    rows = serialize_movements(movements)

    current_store_qty = None
    if selected_item_id is not None:
        current_store_qty = store_qty_for_item(selected_item_id)

    return {
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(movements) >= 500,
        "movement_type": movement_type,
        "selected_item_id": selected_item_id or "",
        "selected_item_label": (
            f"{selected_item.name} · {selected_item.category}"
            if selected_item is not None
            else ""
        ),
        "current_store_qty": (
            str(current_store_qty) if current_store_qty is not None else ""
        ),
        "total_in": str(in_total.quantize(QTY_QUANTUM)),
        "total_out": str(out_total.quantize(QTY_QUANTUM)),
        "range": (params.get("range") or "all").strip().lower() or "all",
    }
