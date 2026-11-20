"""Central store stock in/out — separate from shop stock / apply_stock_movement."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from employees.models import EmployeeProfile, EmployeeStatus
from items.models import (
    Item,
    StockOutReason,
    StockPaymentStatus,
    StoreStock,
    StoreStockMovement,
    StoreStockMovementType,
)

# StoreStockMovement.source value for rows that mirror shop-floor receipts.
STORE_SOURCE_POS = "pos"


def _parse_qty(raw) -> Decimal:
    try:
        qty = Decimal(str(raw or "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid quantity.") from exc
    if qty <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    return qty.quantize(Decimal("0.001"))


def store_qty_for_item(item_id) -> Decimal:
    row = StoreStock.objects.filter(item_id=item_id).only("quantity").first()
    return row.quantity if row is not None else Decimal("0")


def list_store_stock_items(*, query: str = "") -> list[dict]:
    """Active catalog items with current central-store qty for the list UI."""
    q = (query or "").strip()
    items = (
        Item.objects.filter(is_suspended=False)
        .order_by("category", "name")
        .only("id", "name", "category", "stock_count_enabled")
    )
    if q:
        filt = Q(name__icontains=q) | Q(category__icontains=q)
        if q.isdigit():
            filt = filt | Q(pk=int(q))
        items = items.filter(filt)
    item_ids = items.values_list("id", flat=True)
    qty_by_item = {
        row.item_id: row.quantity
        for row in StoreStock.objects.filter(item_id__in=item_ids)
        .only("item_id", "quantity")
    }
    last_price = {}
    for move_item_id, price in (
        StoreStockMovement.objects.filter(
            item_id__in=item_ids, buying_price__isnull=False
        )
        .order_by("item_id", "-created_at", "-id")
        .values_list("item_id", "buying_price")
    ):
        last_price.setdefault(move_item_id, price)
    rows = []
    for item in items:
        qty = qty_by_item.get(item.pk) or Decimal("0")
        price = last_price.get(item.pk)
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "label": f"{item.name} · {item.category}",
                "store_qty": qty,
                "store_qty_display": str(qty),
                "last_buying_price": price,
                "last_buying_price_display": (
                    str(price) if price is not None else ""
                ),
                "stock_count_enabled": bool(item.stock_count_enabled),
                "search_text": f"{item.name} {item.category} {item.pk}".lower(),
            }
        )
    return rows


def search_store_items(*, query: str, limit: int = 12) -> list[dict]:
    rows = list_store_stock_items(query=query)
    limited = rows[: max(1, min(int(limit or 12), 30))]
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "label": row["label"],
            "store_qty": row["store_qty_display"],
        }
        for row in limited
    ]


def parse_supplier_payment_details(
    post_data, *, stock_req, field_prefix: str = ""
) -> dict:
    """Read supplier name/phone + payment status from a stock-in payload.

    Honours CompanyStockSettings: when require_supplier_on_in /
    require_payment_status_on_in are off the fields are optional.
    field_prefix="shelf_" reads the shelf-mode rules instead.
    """
    name = str(post_data.get("supplier_name") or "").strip().upper()
    phone = str(post_data.get("supplier_phone") or "").strip()
    payment = str(post_data.get("payment_status") or "").strip().lower()
    if getattr(stock_req, f"{field_prefix}require_supplier_on_in", False):
        if not name:
            raise ValidationError("Enter the supplier name.")
        if not phone:
            raise ValidationError("Enter the supplier phone number.")
    valid = {choice.value for choice in StockPaymentStatus}
    if getattr(stock_req, f"{field_prefix}require_payment_status_on_in", False):
        if payment not in valid:
            raise ValidationError(
                "Choose a payment status (unpaid, paid, or partial)."
            )
    elif payment not in valid:
        payment = ""
    return {
        "supplier_name": name[:160],
        "supplier_phone": phone[:40],
        "payment_status": payment,
    }


def parse_stock_out_details(
    post_data, *, stock_req, field_prefix: str = ""
) -> dict:
    """Read reason / refund from a stock-out payload.

    Same rules as the per-shop stock-out panel: the reason is one of
    StockOutReason (custom needs a note), refund is yes/no (yes needs an
    amount), and CompanyStockSettings.require_reason_on_out /
    require_refund_on_out decide whether the fields are compulsory.
    field_prefix="shelf_" reads the shelf-mode rules instead.
    """
    from items.services import _parse_price

    reason = str(post_data.get("reason") or "").strip().lower()
    note = str(post_data.get("note") or "").strip()
    valid_reasons = {choice.value for choice in StockOutReason}
    if getattr(stock_req, f"{field_prefix}require_reason_on_out", False):
        if reason not in valid_reasons:
            raise ValidationError(
                "Choose a reason (waste, transfer, display, supplier return, "
                "trade out, trade in, or custom)."
            )
    elif reason not in valid_reasons:
        reason = ""
    if reason == StockOutReason.CUSTOM:
        if not note:
            raise ValidationError("Enter a custom reason.")
        if len(note) > 200:
            raise ValidationError(
                "Custom reason must be 200 characters or fewer."
            )
    elif reason:
        # Preset reasons carry no free-text note from this form.
        note = ""

    refund = str(post_data.get("refund") or "").strip().lower()
    refund_amount = None
    if getattr(stock_req, f"{field_prefix}require_refund_on_out", False):
        if refund not in ("yes", "no"):
            raise ValidationError(
                "Choose whether a refund applies (yes or no)."
            )
    elif refund not in ("yes", "no"):
        refund = ""
    if refund == "yes":
        refund_amount = _parse_price(
            post_data.get("refund_amount"), "Refund amount"
        )
        if refund_amount <= 0:
            raise ValidationError("Refund amount must be greater than zero.")
    return {
        "reason": reason,
        "note": note,
        "refund": refund,
        "refund_amount": refund_amount,
    }


def _parse_buying_price(raw, item_raw) -> Decimal | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        price = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid buying price for item {item_raw}."
        ) from exc
    if price < 0:
        raise ValidationError(
            f"Buying price cannot be negative for item {item_raw}."
        )
    return price.quantize(Decimal("0.01"))


def parse_store_stock_qty_lines(
    post_data, *, require_price: bool = False
) -> list[tuple[int, Decimal, Decimal | None]]:
    """Read qty_<item_id> (+ optional price_<item_id>) fields; skip blanks/zeros."""
    lines = []
    for key, raw in (post_data or {}).items():
        if not str(key).startswith("qty_"):
            continue
        item_raw = str(key)[4:].strip()
        if not item_raw.isdigit():
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            qty = _parse_qty(text)
        except ValidationError:
            raise ValidationError(f"Invalid quantity for item {item_raw}.")
        price = _parse_buying_price(post_data.get(f"price_{item_raw}"), item_raw)
        if require_price and price is None:
            raise ValidationError(
                f"Enter a buying price for item {item_raw}."
            )
        lines.append((int(item_raw), qty, price))
    if not lines:
        raise ValidationError("Enter a quantity for at least one item.")
    return lines


@transaction.atomic
def record_store_stock_lines(
    *,
    mode: str,
    actor: EmployeeProfile,
    lines: list[tuple[int, Decimal, Decimal | None]],
    received_by_id,
    note: str = "",
    supplier_name: str = "",
    supplier_phone: str = "",
    payment_status: str = "",
    reason: str = "",
    refund: str = "",
    refund_amount=None,
) -> list[StoreStockMovement]:
    mode = (mode or "").strip().lower()
    if mode not in {StoreStockMovementType.IN, StoreStockMovementType.OUT}:
        raise ValidationError("Unknown store stock mode.")
    if not lines:
        raise ValidationError("Enter a quantity for at least one item.")
    movements = []
    for item_id, qty, buying_price in lines:
        if mode == StoreStockMovementType.IN:
            movements.append(
                record_store_stock_in(
                    actor=actor,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=received_by_id,
                    note=note,
                    supplier_name=supplier_name,
                    supplier_phone=supplier_phone,
                    payment_status=payment_status,
                    buying_price=buying_price,
                    reason=reason,
                )
            )
        else:
            movements.append(
                record_store_stock_out(
                    actor=actor,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=received_by_id,
                    note=note,
                    reason=reason,
                    refund=refund,
                    refund_amount=refund_amount,
                    supplier_name=supplier_name,
                    supplier_phone=supplier_phone,
                )
            )
    return movements


def search_active_employees(*, query: str, limit: int = 12) -> list[dict]:
    q = (query or "").strip()
    people = (
        EmployeeProfile.objects.filter(status=EmployeeStatus.ACTIVE)
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "user__username")
    )
    if q:
        people = people.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
        )
    rows = []
    for profile in people[: max(1, min(int(limit or 12), 30))]:
        name = (profile.user.get_full_name() or profile.user.username or "").strip()
        if not name:
            continue
        rows.append(
            {
                "id": profile.pk,
                "name": name,
                "label": name,
            }
        )
    return rows


def _resolve_item(item_id) -> Item:
    try:
        item = Item.objects.get(pk=int(item_id), is_suspended=False)
    except (TypeError, ValueError, Item.DoesNotExist) as exc:
        raise ValidationError("Select a valid item.") from exc
    return item


def set_item_stock_count_enabled(*, item_id, enabled: bool) -> Item:
    """Toggle whether POS sales/returns move this item's StoreStock count.

    Kept in sync with the Sale stock tracking pick: flipping the item-stock
    page switch also flips deduct_stock_on_sale and marks the item custom so
    the company-default sync won't switch it back.
    """
    item = _resolve_item(item_id)
    item.stock_count_enabled = bool(enabled)
    item.deduct_stock_on_sale = bool(enabled)
    item.sale_tracking_custom = True
    item.save(
        update_fields=[
            "stock_count_enabled",
            "deduct_stock_on_sale",
            "sale_tracking_custom",
            "updated_at",
        ]
    )
    return item


def _resolve_counterpart(received_by_id, *, mode: str) -> EmployeeProfile:
    try:
        profile = EmployeeProfile.objects.select_related("user").get(
            pk=int(received_by_id),
            status=EmployeeStatus.ACTIVE,
        )
    except (TypeError, ValueError, EmployeeProfile.DoesNotExist) as exc:
        if mode == StoreStockMovementType.IN:
            raise ValidationError("Select who delivered the stock.") from exc
        raise ValidationError("Select the employee who received the stock.") from exc
    return profile


@transaction.atomic
def record_store_stock_in(
    *,
    actor: EmployeeProfile,
    item_id,
    quantity,
    received_by_id,
    note: str = "",
    supplier_name: str = "",
    supplier_phone: str = "",
    payment_status: str = "",
    buying_price=None,
    reason: str = "",
) -> StoreStockMovement:
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError("Sign in to record store stock in.")
    item = _resolve_item(item_id)
    qty = _parse_qty(quantity)
    reason = (reason or "").strip().lower()
    if reason not in {choice.value for choice in StockOutReason}:
        reason = ""
    receiver = (
        _resolve_counterpart(received_by_id, mode=StoreStockMovementType.IN)
        if received_by_id
        else actor
    )
    stock, _ = StoreStock.objects.select_for_update().get_or_create(item=item)
    stock.quantity = (stock.quantity or Decimal("0")) + qty
    stock.save(update_fields=["quantity", "updated_at"])
    return StoreStockMovement.objects.create(
        movement_type=StoreStockMovementType.IN,
        item=item,
        quantity=qty,
        balance_after=stock.quantity,
        created_by=actor,
        received_by=receiver,
        supplier_name=(supplier_name or "").strip().upper()[:160],
        supplier_phone=(supplier_phone or "").strip()[:40],
        payment_status=(payment_status or "").strip().lower()[:16],
        buying_price=buying_price,
        reason=reason,
        note=(note or "").strip()[:255],
    )


@transaction.atomic
def record_store_stock_out(
    *,
    actor: EmployeeProfile,
    item_id,
    quantity,
    received_by_id,
    note: str = "",
    reason: str = "",
    refund: str = "",
    refund_amount=None,
    supplier_name: str = "",
    supplier_phone: str = "",
) -> StoreStockMovement:
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError("Sign in to record store stock out.")
    item = _resolve_item(item_id)
    qty = _parse_qty(quantity)
    receiver = (
        _resolve_counterpart(received_by_id, mode=StoreStockMovementType.OUT)
        if received_by_id
        else actor
    )
    stock, _ = StoreStock.objects.select_for_update().get_or_create(item=item)
    current = stock.quantity or Decimal("0")
    if qty > current:
        raise ValidationError(
            f"Store only has {current} available — cannot stock out {qty}."
        )
    stock.quantity = current - qty
    stock.save(update_fields=["quantity", "updated_at"])
    reason = (reason or "").strip().lower()
    if reason not in {choice.value for choice in StockOutReason}:
        reason = ""
    refund = (refund or "").strip().lower()
    if refund not in ("yes", "no"):
        refund = ""
    if refund != "yes":
        refund_amount = None
    return StoreStockMovement.objects.create(
        movement_type=StoreStockMovementType.OUT,
        item=item,
        quantity=qty,
        balance_after=stock.quantity,
        created_by=actor,
        received_by=receiver,
        supplier_name=(supplier_name or "").strip().upper()[:160],
        supplier_phone=(supplier_phone or "").strip()[:40],
        reason=reason,
        refund=refund,
        refund_amount=refund_amount,
        note=(note or "").strip()[:255],
    )


def record_store_receipt_movement(
    *,
    actor: EmployeeProfile,
    item: Item,
    quantity,
    movement_type: str,
    note: str = "",
    reason: str = "",
) -> StoreStockMovement:
    """Mirror a shop-floor receipt line into the central store ledger.

    Called while POS item stock tracking is on: sales / trade outs / trade-in
    returns deduct (OUT); trade ins, trade exchanges and customer returns add
    back (IN). Unlike record_store_stock_out this never blocks on the store
    balance — the shop floor stock check is authoritative — so the store
    quantity can go negative when the item was never stocked into the
    central store.
    """
    if item is None or not getattr(item, "stock_count_enabled", True) or not getattr(
        item, "deduct_stock_on_sale", True
    ):
        # Item opted out of stock counting on the item-stock page (or of sale
        # deduction on the stock tracking page) — its counts are manual only;
        # POS receipts don't move them.
        return None
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError("Sign in to update store stock.")
    qty = _parse_qty(quantity)
    movement_type = (movement_type or "").strip().lower()
    if movement_type not in {
        StoreStockMovementType.IN,
        StoreStockMovementType.OUT,
    }:
        raise ValidationError("Unknown store stock mode.")
    reason = (reason or "").strip().lower()
    if reason not in {choice.value for choice in StockOutReason}:
        reason = ""
    stock, _ = StoreStock.objects.select_for_update().get_or_create(item=item)
    delta = qty if movement_type == StoreStockMovementType.IN else -qty
    stock.quantity = (stock.quantity or Decimal("0")) + delta
    stock.save(update_fields=["quantity", "updated_at"])
    return StoreStockMovement.objects.create(
        movement_type=movement_type,
        item=item,
        quantity=qty,
        balance_after=stock.quantity,
        created_by=actor,
        received_by=actor,
        reason=reason,
        source=STORE_SOURCE_POS,
        note=(note or "").strip()[:255],
    )


def parse_store_trade_lines(
    post_data, *, require_price: bool = False
) -> tuple[str, list[tuple[int, Decimal, Decimal | None]]]:
    """Read a one-way store trade: trade_kind plus qty_<id>/price_<id> rows."""
    kind = str((post_data or {}).get("trade_kind") or "").strip().lower()
    if kind not in {StockOutReason.TRADE_OUT, StockOutReason.TRADE_IN}:
        raise ValidationError(
            "Choose the trade direction — trade out or trade in."
        )
    return kind, parse_store_stock_qty_lines(
        post_data,
        require_price=require_price and kind == StockOutReason.TRADE_IN,
    )


@transaction.atomic
def record_store_trade(
    *,
    actor: EmployeeProfile,
    kind: str,
    person_name: str = "",
    person_phone: str = "",
    lines: list[tuple[int, Decimal, Decimal | None]],
    note: str = "",
    require_person: bool = False,
) -> list[StoreStockMovement]:
    """One-way store trade with one person.

    trade_out: items leave the store to the person (stock out tagged
    trade_out). trade_in: items enter the store from the person (stock
    in tagged trade_in, optional unit value per line).
    """
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError("Sign in to record a store trade.")
    kind = (kind or "").strip().lower()
    if kind not in {StockOutReason.TRADE_OUT, StockOutReason.TRADE_IN}:
        raise ValidationError(
            "Choose the trade direction — trade out or trade in."
        )
    if not lines:
        raise ValidationError("Enter a quantity for at least one item.")
    person_name = (person_name or "").strip().upper()[:160]
    person_phone = (person_phone or "").strip()[:40]
    if require_person and kind == StockOutReason.TRADE_IN:
        if not person_name:
            raise ValidationError("Enter the other person's name.")
        if not person_phone:
            raise ValidationError("Enter the other person's phone number.")
    movements = []
    for item_id, qty, buying_price in lines:
        if kind == StockOutReason.TRADE_OUT:
            movements.append(
                record_store_stock_out(
                    actor=actor,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=None,
                    note=note,
                    reason=StockOutReason.TRADE_OUT,
                    supplier_name=person_name,
                    supplier_phone=person_phone,
                )
            )
        else:
            movements.append(
                record_store_stock_in(
                    actor=actor,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=None,
                    note=note,
                    reason=StockOutReason.TRADE_IN,
                    supplier_name=person_name,
                    supplier_phone=person_phone,
                    buying_price=buying_price,
                )
            )
    return movements


def _employee_display_name(profile) -> str:
    if profile is None:
        return "—"
    user = getattr(profile, "user", None)
    if user is None:
        return "—"
    return (user.get_full_name() or user.username or "—").strip() or "—"


def build_store_stock_report(*, request) -> dict:
    """Period + item filtered central-store movement report."""
    from django.db.models import Sum
    from django.utils import timezone
    from employees.analytics_services import _date_filter_context

    date_filter = _date_filter_context(
        request, allow_all_time=True, default_all_time=True
    )
    start = date_filter.get("start")
    end = date_filter.get("end")

    raw_item_id = (request.GET.get("item_id") or "").strip()
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

    movement_type = (request.GET.get("type") or "all").strip().lower()
    if movement_type not in {"all", "in", "out"}:
        movement_type = "all"

    qs = StoreStockMovement.objects.select_related(
        "item",
        "created_by__user",
        "received_by__user",
    )
    if start is not None and end is not None:
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    if selected_item_id is not None:
        qs = qs.filter(item_id=selected_item_id)
    if movement_type in {StoreStockMovementType.IN, StoreStockMovementType.OUT}:
        qs = qs.filter(movement_type=movement_type)

    qs = qs.order_by("-created_at", "-id")
    in_total = qs.filter(
        movement_type=StoreStockMovementType.IN
    ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    out_total = qs.filter(
        movement_type=StoreStockMovementType.OUT
    ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    movements = list(qs[:500])

    rows = []
    for movement in movements:
        counterpart_label = (
            "Delivered by"
            if movement.movement_type == StoreStockMovementType.IN
            else "Received by"
        )
        rows.append(
            {
                "id": movement.pk,
                "when": timezone.localtime(movement.created_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "movement_type": movement.movement_type,
                "movement_label": (
                    "Stock in"
                    if movement.movement_type == StoreStockMovementType.IN
                    else "Stock out"
                ),
                "item_id": movement.item_id,
                "item_name": movement.item.name,
                "item_category": movement.item.category,
                "quantity": movement.quantity,
                "quantity_display": str(movement.quantity),
                "balance_after": movement.balance_after,
                "balance_after_display": str(movement.balance_after),
                "recorded_by": _employee_display_name(movement.created_by),
                "counterpart_label": counterpart_label,
                "counterpart": _employee_display_name(movement.received_by),
                "supplier_name": (movement.supplier_name or "").strip(),
                "supplier_phone": (movement.supplier_phone or "").strip(),
                "payment_status": (movement.payment_status or "").strip(),
                "payment_label": (
                    movement.get_payment_status_display()
                    if movement.payment_status
                    else ""
                ),
                "buying_price_display": (
                    str(movement.buying_price)
                    if movement.buying_price is not None
                    else ""
                ),
                "reason": (movement.reason or "").strip(),
                "reason_label": (
                    movement.get_reason_display()
                    if movement.reason
                    else ""
                ),
                "refund": (movement.refund or "").strip(),
                "refund_amount_display": (
                    str(movement.refund_amount)
                    if movement.refund_amount is not None
                    else ""
                ),
                "note": (movement.note or "").strip(),
                "search_text": (
                    f"{movement.item.name} {movement.item.category} "
                    f"{movement.movement_type} {movement.note} "
                    f"{movement.supplier_name} {movement.supplier_phone}"
                ).lower(),
            }
        )

    item_options = list_store_stock_items()
    current_store_qty = None
    if selected_item_id is not None:
        current_store_qty = store_qty_for_item(selected_item_id)

    return {
        **date_filter,
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
        "item_options": item_options,
        "current_store_qty": (
            str(current_store_qty) if current_store_qty is not None else ""
        ),
        "total_in": in_total,
        "total_out": out_total,
        "total_in_display": str(in_total),
        "total_out_display": str(out_total),
        "type_options": (
            ("all", "All movements"),
            ("in", "Stock in"),
            ("out", "Stock out"),
        ),
    }


def build_store_item_stock_report(*, day_start, day_end, item_id=None) -> dict:
    """Opening / in / transfers / trades / out / closing per central-store item.

    ``day_start``/``day_end`` bound the report period (end exclusive); either
    may be ``None`` for an open-ended side (all-time). Starting and closing
    balances come from each item's ``balance_after`` trail; movements inside
    the period bucket as:

    - ``in`` — stock ins, excluding trade-ins and transfer receipts.
    - ``transfers`` — ``reason='transfer'`` legs, net received − sent.
    - ``trades`` — ``trade_in`` − ``trade_out``, shown net.
    - ``out`` — stock outs that are neither trades nor transfers.

    ``item_id`` narrows the rows to one catalog item.
    """
    from items.shelf_stock_services import _qty_display, _signed_qty

    items = list(
        Item.objects.order_by("category", "name").only(
            "id", "name", "category", "is_suspended"
        )
    )
    item_options = [
        {"id": item.pk, "name": item.name}
        for item in items
        if not item.is_suspended
    ]
    selected_item_id = (
        item_id if item_id in {item.pk for item in items} else None
    )
    report_items = (
        [item for item in items if item.pk == selected_item_id]
        if selected_item_id is not None
        else items
    )

    movements = StoreStockMovement.objects.order_by(
        "item_id", "created_at", "id"
    ).only(
        "item_id",
        "movement_type",
        "reason",
        "quantity",
        "balance_after",
        "created_at",
    )
    if day_end is not None:
        movements = movements.filter(created_at__lt=day_end)

    bounds = {}  # item_id -> [balance before day_start, balance before day_end]
    buckets = {}  # item_id -> per-kind period sums
    for mv in movements.iterator():
        slot = bounds.setdefault(mv.item_id, [None, None])
        slot[1] = mv.balance_after
        if day_start is not None and mv.created_at < day_start:
            slot[0] = mv.balance_after
            continue
        bucket = buckets.setdefault(
            mv.item_id,
            {
                "in": Decimal("0"),
                "out": Decimal("0"),
                "transfer_in": Decimal("0"),
                "transfer_out": Decimal("0"),
                "trade_in": Decimal("0"),
                "trade_out": Decimal("0"),
            },
        )
        reason = (mv.reason or "").strip().lower()
        if mv.movement_type == StoreStockMovementType.OUT:
            if reason == StockOutReason.TRADE_OUT:
                bucket["trade_out"] += mv.quantity
            elif reason == StockOutReason.TRANSFER:
                bucket["transfer_out"] += mv.quantity
            else:
                bucket["out"] += mv.quantity
        elif mv.movement_type == StoreStockMovementType.IN:
            if reason == StockOutReason.TRADE_IN:
                bucket["trade_in"] += mv.quantity
            elif reason == StockOutReason.TRANSFER:
                bucket["transfer_in"] += mv.quantity
            else:
                bucket["in"] += mv.quantity
        else:
            bucket["in"] += mv.quantity

    rows = []
    totals = {
        key: Decimal("0")
        for key in ("start", "in", "transfers", "trades", "out", "close")
    }
    for item in report_items:
        slot = bounds.get(item.pk)
        bucket = buckets.get(item.pk)
        if slot is None and bucket is None:
            continue  # never moved through the central store
        start = slot[0] if slot and slot[0] is not None else Decimal("0")
        close = slot[1] if slot and slot[1] is not None else Decimal("0")
        bucket = bucket or {}
        in_qty = bucket.get("in", Decimal("0"))
        out_qty = bucket.get("out", Decimal("0"))
        transfers = bucket.get("transfer_in", Decimal("0")) - bucket.get(
            "transfer_out", Decimal("0")
        )
        trades = bucket.get("trade_in", Decimal("0")) - bucket.get(
            "trade_out", Decimal("0")
        )
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "is_suspended": item.is_suspended,
                "start": start,
                "in": in_qty,
                "transfers": transfers,
                "trades": trades,
                "out": out_qty,
                "close": close,
                "start_display": _qty_display(start),
                "in_display": _qty_display(in_qty),
                "transfers_display": _signed_qty(transfers),
                "trades_display": _signed_qty(trades),
                "out_display": _qty_display(out_qty),
                "close_display": _qty_display(close),
            }
        )
        totals["start"] += start
        totals["in"] += in_qty
        totals["transfers"] += transfers
        totals["trades"] += trades
        totals["out"] += out_qty
        totals["close"] += close

    return {
        "rows": rows,
        "row_count": len(rows),
        "item_options": item_options,
        "selected_item_id": selected_item_id,
        "totals": {
            "start_display": _qty_display(totals["start"]),
            "in_display": _qty_display(totals["in"]),
            "transfers_display": _signed_qty(totals["transfers"]),
            "trades_display": _signed_qty(totals["trades"]),
            "out_display": _qty_display(totals["out"]),
            "close_display": _qty_display(totals["close"]),
        },
    }
