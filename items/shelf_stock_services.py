"""Shelf mode: a shop's own stock list, registered and adjusted by hand."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from items.models import (
    ShelfItem,
    ShelfStockMovement,
    ShelfStockMovementType,
    StockOutReason,
    StockPaymentStatus,
)
from items.store_stock_services import _parse_buying_price

_MAX_QTY = Decimal("999999999.999")


def _qty_display(value) -> str:
    value = Decimal(value or 0)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


SHELF_UNIT_CHOICES = (
    "PCS",
    "KG",
    "G",
    "LITRES",
    "ML",
    "BOXES",
    "BAGS",
    "SACKS",
    "PACKETS",
    "BOTTLES",
    "CRATES",
    "TRAYS",
    "DOZEN",
    "TINS",
    "CARTONS",
    "BUNDLES",
    "ROLLS",
    "METRES",
    "SETS",
    "PAIRS",
)


def shelf_categories() -> list[str]:
    return list(
        ShelfItem.objects.exclude(category="")
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )


def parse_shelf_qty(raw, *, allow_zero=False, label="Quantity") -> Decimal:
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if not text:
        if allow_zero:
            return Decimal("0")
        raise ValidationError(f"Enter a {label.lower()}.")
    try:
        qty = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{label} must be a number.") from exc
    if not qty.is_finite():
        raise ValidationError(f"{label} must be a number.")
    qty = qty.quantize(Decimal("0.001"))
    if qty < 0 or (qty == 0 and not allow_zero):
        raise ValidationError(f"{label} must be more than 0.")
    if qty > _MAX_QTY:
        raise ValidationError(f"{label} is too large.")
    return qty


def _shelf_item_queryset(*, shop=None, shops=None, suspended: bool = False):
    qs = ShelfItem.objects.filter(
        is_archived=False, is_suspended=suspended
    ).select_related("shop")
    if shop is not None:
        return qs.filter(shop=shop).order_by("category", "name")
    if shops is not None:
        return qs.filter(shop__in=shops).order_by(
            "category", "shop__name", "name"
        )
    raise ValidationError("Pick a shelf-mode shop.")


def _shelf_item_rows(items) -> list[dict]:
    last_price = {}
    for shelf_item_id, price in (
        ShelfStockMovement.objects.filter(
            shelf_item__in=items, buying_price__isnull=False
        )
        .order_by("shelf_item_id", "-created_at", "-id")
        .values_list("shelf_item_id", "buying_price")
    ):
        last_price.setdefault(shelf_item_id, price)
    rows = []
    for item in items:
        threshold = Decimal(item.low_stock_threshold or 0)
        price = last_price.get(item.pk)
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "shop_id": item.shop_id,
                "shop_name": item.shop.name,
                "category": item.category,
                "unit": item.unit,
                "quantity": item.quantity,
                "quantity_display": _qty_display(item.quantity),
                "low_stock_threshold_display": _qty_display(threshold),
                "is_low": threshold > 0 and item.quantity <= threshold,
                "image_url": item.public_image_url(),
                "last_buying_price": price,
                "last_buying_price_display": (
                    str(price) if price is not None else ""
                ),
                "search_text": f"{item.name} {item.category} {item.unit}".lower(),
            }
        )
    return rows


def list_shelf_items(*, shop=None, shops=None) -> list[dict]:
    return _shelf_item_rows(
        list(_shelf_item_queryset(shop=shop, shops=shops, suspended=False))
    )


def list_suspended_shelf_items(*, shop=None, shops=None) -> list[dict]:
    return _shelf_item_rows(
        list(_shelf_item_queryset(shop=shop, shops=shops, suspended=True))
    )


def recent_shelf_movements(
    *, shop=None, shops=None, limit: int = 30
) -> list[ShelfStockMovement]:
    qs = ShelfStockMovement.objects.select_related(
        "shelf_item", "shelf_item__shop", "created_by__user"
    )
    if shop is not None:
        qs = qs.filter(shelf_item__shop=shop)
    elif shops is not None:
        qs = qs.filter(shelf_item__shop__in=shops)
    return list(qs.order_by("-created_at", "-id")[:limit])


def shelf_movements_in_period(
    *, shop=None, shops=None, day_start, day_end
) -> list[ShelfStockMovement]:
    """Every shelf transaction in [day_start, day_end), newest first."""
    qs = ShelfStockMovement.objects.filter(
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).select_related("shelf_item", "shelf_item__shop", "created_by__user")
    if shop is not None:
        qs = qs.filter(shelf_item__shop=shop)
    elif shops is not None:
        qs = qs.filter(shelf_item__shop__in=shops)
    return list(qs.order_by("-created_at", "-id"))


def _signed_qty(value) -> str:
    value = Decimal(value or 0)
    if value > 0:
        return f"+{_qty_display(value)}"
    if value < 0:
        return f"−{_qty_display(-value)}"
    return "0"


def build_shelf_stock_report(*, shop, day_start, day_end, item_id=None) -> dict:
    """Opening / in / transfers / trades / out / closing per shelf item.

    ``day_start``/``day_end`` bound the report period (end exclusive).
    Starting and closing balances come from each item's ``balance_after``
    trail; movements inside the period bucket as:

    - ``in`` — stock ins and register entries, excluding trade-ins and
      transfer receipts.
    - ``transfers`` — inter-shop transfer legs (``reason='transfer'``),
      shown net: received − sent. Pre-stamp rows fall back to the
      auto "Sent to" / "Received from" note.
    - ``trades`` — ``trade_in`` − ``trade_out``, shown net.
    - ``out`` — stock outs that are neither trades nor transfers.

    ``item_id`` narrows the rows to one shelf item for this shop.
    """
    items = list(
        ShelfItem.objects.filter(shop=shop).order_by("category", "name")
    )
    item_options = [
        {"id": item.pk, "name": item.name}
        for item in items
        if not item.is_archived
    ]
    selected_item_id = (
        item_id if item_id in {item.pk for item in items} else None
    )
    report_items = (
        [item for item in items if item.pk == selected_item_id]
        if selected_item_id is not None
        else items
    )

    bounds = {}  # shelf_item_id -> [balance before day_start, balance before day_end]
    buckets = {}  # shelf_item_id -> per-kind period sums
    for mv in (
        ShelfStockMovement.objects.filter(
            shelf_item__shop=shop,
            created_at__lt=day_end,
        )
        .order_by("shelf_item_id", "created_at", "id")
        .only(
            "shelf_item_id",
            "movement_type",
            "reason",
            "note",
            "quantity",
            "balance_after",
            "created_at",
        )
        .iterator()
    ):
        slot = bounds.setdefault(mv.shelf_item_id, [None, None])
        slot[1] = mv.balance_after
        if mv.created_at < day_start:
            slot[0] = mv.balance_after
            continue
        bucket = buckets.setdefault(
            mv.shelf_item_id,
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
        note = (mv.note or "").strip().lower()
        if mv.movement_type == ShelfStockMovementType.OUT:
            if reason == StockOutReason.TRADE_OUT:
                bucket["trade_out"] += mv.quantity
            elif (
                reason == StockOutReason.TRANSFER
                or note.startswith("sent to ")
            ):
                bucket["transfer_out"] += mv.quantity
            else:
                bucket["out"] += mv.quantity
        elif mv.movement_type == ShelfStockMovementType.IN:
            if reason == StockOutReason.TRADE_IN:
                bucket["trade_in"] += mv.quantity
            elif (
                reason == StockOutReason.TRANSFER
                or note.startswith("received from ")
            ):
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
        slot = bounds.get(item.pk) or [None, None]
        start = slot[0] if slot[0] is not None else Decimal("0")
        close = slot[1] if slot[1] is not None else Decimal("0")
        bucket = buckets.get(item.pk)
        if item.is_archived and bucket is None and start == 0 and close == 0:
            continue
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
                "unit": item.unit,
                "is_suspended": item.is_suspended,
                "is_archived": item.is_archived,
                "image_url": item.public_image_url(),
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


@transaction.atomic
def register_shelf_item(
    *,
    shop,
    actor=None,
    name,
    category="",
    unit="",
    opening_qty=None,
    low_stock_threshold=None,
    image=None,
) -> ShelfItem:
    name = " ".join(str(name or "").split()).upper()
    if not name:
        raise ValidationError("Enter the shelf item name.")
    if len(name) > 200:
        raise ValidationError("Shelf item name is too long.")
    category = " ".join(str(category or "").split()).upper()[:120]
    unit = " ".join(str(unit or "").split()).upper()[:40]
    qty = parse_shelf_qty(opening_qty, allow_zero=True, label="Opening quantity")
    threshold = parse_shelf_qty(
        low_stock_threshold, allow_zero=True, label="Low stock alert"
    )

    existing = (
        ShelfItem.objects.select_for_update()
        .filter(shop=shop, name__iexact=name)
        .first()
    )
    if (
        existing is not None
        and not existing.is_archived
        and not existing.is_suspended
    ):
        raise ValidationError(f"“{existing.name}” is already on the shelf list.")
    try:
        if existing is not None:
            existing.name = name
            existing.category = category
            existing.unit = unit
            existing.quantity = qty
            existing.low_stock_threshold = threshold
            existing.is_archived = False
            existing.is_suspended = False
            update_fields = [
                "name",
                "category",
                "unit",
                "quantity",
                "low_stock_threshold",
                "is_archived",
                "is_suspended",
                "updated_at",
            ]
            if image is not None:
                existing.image = image
                update_fields.append("image")
            existing.save(update_fields=update_fields)
            item = existing
        else:
            item = ShelfItem.objects.create(
                shop=shop,
                name=name,
                category=category,
                unit=unit,
                quantity=qty,
                low_stock_threshold=threshold,
                image=image,
                created_by=actor,
            )
    except IntegrityError as exc:
        raise ValidationError(f"“{name}” is already on the shelf list.") from exc
    ShelfStockMovement.objects.create(
        shelf_item=item,
        movement_type=ShelfStockMovementType.REGISTER,
        quantity=qty,
        balance_after=qty,
        created_by=actor,
    )
    return item


@transaction.atomic
def record_shelf_movement(
    *,
    shop,
    shelf_item_id,
    mode,
    qty,
    actor=None,
    note="",
    supplier_name: str = "",
    supplier_phone: str = "",
    payment_status: str = "",
    buying_price=None,
    reason: str = "",
    refund: str = "",
    refund_amount=None,
) -> ShelfStockMovement:
    if mode not in (ShelfStockMovementType.IN, ShelfStockMovementType.OUT):
        raise ValidationError("Choose stock in or stock out.")
    amount = parse_shelf_qty(qty)
    item = (
        ShelfItem.objects.select_for_update()
        .filter(
            pk=shelf_item_id,
            shop=shop,
            is_archived=False,
            is_suspended=False,
        )
        .first()
    )
    if item is None:
        raise ValidationError("Shelf item not found for this shop.")
    reason_clean = (reason or "").strip().lower()
    keep_person = (
        mode == ShelfStockMovementType.IN
        or reason_clean == StockOutReason.TRADE_OUT
    )
    if mode == ShelfStockMovementType.OUT:
        if amount > item.quantity:
            raise ValidationError(
                f"Only {_qty_display(item.quantity)} of “{item.name}” on the shelf."
            )
        item.quantity -= amount
    else:
        item.quantity += amount
    item.save(update_fields=["quantity", "updated_at"])
    status_clean = (
        (payment_status or "").strip().lower()[:16]
        if mode == ShelfStockMovementType.IN
        else ""
    )
    paid_amount = Decimal("0")
    if (
        status_clean == StockPaymentStatus.PAID
        and buying_price is not None
    ):
        paid_amount = (Decimal(buying_price) * amount).quantize(
            Decimal("0.01")
        )
    return ShelfStockMovement.objects.create(
        shelf_item=item,
        movement_type=mode,
        quantity=amount,
        balance_after=item.quantity,
        supplier_name=(
            (supplier_name or "").strip().upper()[:160] if keep_person else ""
        ),
        supplier_phone=(
            (supplier_phone or "").strip()[:40] if keep_person else ""
        ),
        payment_status=status_clean,
        amount_paid=paid_amount,
        buying_price=(
            buying_price if mode == ShelfStockMovementType.IN else None
        ),
        reason=(
            reason_clean[:16]
            if reason_clean in {choice.value for choice in StockOutReason}
            and (
                mode == ShelfStockMovementType.OUT
                or reason_clean
                in (StockOutReason.TRADE_IN, StockOutReason.TRANSFER)
            )
            else ""
        ),
        refund=(
            (refund or "").strip().lower()[:8]
            if mode == ShelfStockMovementType.OUT
            and (refund or "").strip().lower() in ("yes", "no")
            else ""
        ),
        refund_amount=(
            refund_amount
            if mode == ShelfStockMovementType.OUT
            and (refund or "").strip().lower() == "yes"
            else None
        ),
        note=str(note or "").strip()[:255],
        created_by=actor,
    )


@transaction.atomic
def stock_in_shelf_item_by_name(
    *, shop, actor=None, name, unit="", qty, buying_price=None, note=""
) -> tuple[ShelfItem, ShelfStockMovement, bool]:
    """Stock in by name: reuse the matching shelf item, else register it."""
    amount = parse_shelf_qty(qty)
    clean = " ".join(str(name or "").split())
    if not clean:
        raise ValidationError("Enter the shelf item name.")
    item = (
        ShelfItem.objects.select_for_update()
        .filter(shop=shop, name__iexact=clean, is_archived=False)
        .first()
    )
    registered = False
    if item is not None and item.is_suspended:
        item.is_suspended = False
        item.save(update_fields=["is_suspended", "updated_at"])
    if item is None:
        item = register_shelf_item(
            shop=shop,
            actor=actor,
            name=clean,
            unit=unit,
            opening_qty=0,
        )
        registered = True
    movement = record_shelf_movement(
        shop=shop,
        shelf_item_id=item.pk,
        mode=ShelfStockMovementType.IN,
        qty=amount,
        actor=actor,
        note=note,
        buying_price=buying_price,
    )
    return item, movement, registered


@transaction.atomic
def stock_out_shelf_item_by_name(
    *, shop, actor=None, name, qty, note=""
) -> tuple[ShelfItem, ShelfStockMovement]:
    """Stock out by name: the item must already be on the shop's shelf list."""
    amount = parse_shelf_qty(qty)
    clean = " ".join(str(name or "").split())
    if not clean:
        raise ValidationError("Enter the shelf item name.")
    item = (
        ShelfItem.objects.select_for_update()
        .filter(
            shop=shop,
            name__iexact=clean,
            is_archived=False,
            is_suspended=False,
        )
        .first()
    )
    if item is None:
        raise ValidationError(f"“{clean.upper()}” is not on the shelf list.")
    movement = record_shelf_movement(
        shop=shop,
        shelf_item_id=item.pk,
        mode=ShelfStockMovementType.OUT,
        qty=amount,
        actor=actor,
        note=note,
    )
    return item, movement


@transaction.atomic
def transfer_shelf_item(
    *, from_shop, to_shop, shelf_item_id, qty, actor=None, note=""
) -> tuple[ShelfItem, ShelfItem]:
    """Move shelf qty between shops: stock out at source, stock in at target.

    Auto-registers the item on the target shelf (qty 0 + REGISTER entry) when
    it is not already listed there.
    """
    item = (
        ShelfItem.objects.select_for_update()
        .filter(
            pk=shelf_item_id,
            shop=from_shop,
            is_archived=False,
            is_suspended=False,
        )
        .first()
    )
    if item is None:
        raise ValidationError("Shelf item not found for this shop.")
    amount = parse_shelf_qty(qty)
    record_shelf_movement(
        shop=from_shop,
        shelf_item_id=item.pk,
        mode=ShelfStockMovementType.OUT,
        qty=amount,
        actor=actor,
        note=note or f"Sent to {to_shop.name}",
        reason=StockOutReason.TRANSFER,
    )
    target = (
        ShelfItem.objects.filter(shop=to_shop, name__iexact=item.name).first()
    )
    if target is None or target.is_archived:
        target = register_shelf_item(
            shop=to_shop,
            actor=actor,
            name=item.name,
            unit=item.unit,
            opening_qty=0,
            low_stock_threshold=item.low_stock_threshold,
        )
    elif target.is_suspended:
        target.is_suspended = False
        target.save(update_fields=["is_suspended", "updated_at"])
    record_shelf_movement(
        shop=to_shop,
        shelf_item_id=target.pk,
        mode=ShelfStockMovementType.IN,
        qty=amount,
        actor=actor,
        note=note or f"Received from {from_shop.name}",
        reason=StockOutReason.TRANSFER,
    )
    return item, target


@transaction.atomic
def archive_shelf_item(*, shop, shelf_item_id) -> ShelfItem:
    item = (
        ShelfItem.objects.select_for_update()
        .filter(pk=shelf_item_id, shop=shop, is_archived=False)
        .first()
    )
    if item is None:
        raise ValidationError("Shelf item not found for this shop.")
    item.is_archived = True
    item.save(update_fields=["is_archived", "updated_at"])
    return item


@transaction.atomic
def update_shelf_item(
    *,
    shop,
    shelf_item_id,
    name,
    category="",
    unit="",
    low_stock_threshold=None,
    image=None,
    clear_image=False,
) -> ShelfItem:
    """Edit a shelf item's details (not its quantity — use in/out for that)."""
    item = (
        ShelfItem.objects.select_for_update()
        .filter(pk=shelf_item_id, shop=shop, is_archived=False)
        .first()
    )
    if item is None:
        raise ValidationError("Shelf item not found for this shop.")
    name = " ".join(str(name or "").split()).upper()
    if not name:
        raise ValidationError("Enter the shelf item name.")
    if len(name) > 200:
        raise ValidationError("Shelf item name is too long.")
    clash = (
        ShelfItem.objects.filter(shop=shop, name__iexact=name)
        .exclude(pk=item.pk)
        .first()
    )
    if clash is not None:
        raise ValidationError(f"“{clash.name}” is already on the shelf list.")
    item.name = name
    item.category = " ".join(str(category or "").split()).upper()[:120]
    item.unit = " ".join(str(unit or "").split()).upper()[:40]
    item.low_stock_threshold = parse_shelf_qty(
        low_stock_threshold, allow_zero=True, label="Low stock alert"
    )
    update_fields = [
        "name",
        "category",
        "unit",
        "low_stock_threshold",
        "updated_at",
    ]
    if image is not None:
        item.image = image
        update_fields.append("image")
    elif clear_image and item.image:
        item.image = None
        update_fields.append("image")
    item.save(update_fields=update_fields)
    return item


@transaction.atomic
def set_shelf_item_suspended(
    *, shop, shelf_item_id, suspended: bool
) -> ShelfItem:
    item = (
        ShelfItem.objects.select_for_update()
        .filter(pk=shelf_item_id, shop=shop, is_archived=False)
        .first()
    )
    if item is None:
        raise ValidationError("Shelf item not found for this shop.")
    if item.is_suspended == suspended:
        raise ValidationError(
            f"“{item.name}” is already "
            f"{'suspended' if suspended else 'on the shelf list'}."
        )
    item.is_suspended = suspended
    item.save(update_fields=["is_suspended", "updated_at"])
    return item


def _shelf_requirement(name: str) -> bool:
    """Read a shelf-mode compulsory-field flag from CompanyStockSettings."""
    from shops.services import get_company_stock_settings

    return bool(
        getattr(get_company_stock_settings(), f"shelf_{name}", False)
    )


def apply_shelf_action(*, shop, actor, data, files=None, to_shop=None) -> str:
    """Run one shelf page action (register / in / out / transfer / archive); returns a success message."""
    action = (data.get("action") or "").strip()
    if action == "register":
        item = register_shelf_item(
            shop=shop,
            actor=actor,
            name=data.get("name"),
            category=data.get("category"),
            unit=data.get("unit"),
            opening_qty=data.get("opening_qty"),
            low_stock_threshold=data.get("low_stock_threshold"),
            image=files.get("image") if files else None,
        )
        return f"“{item.name}” added to the shelf list."
    if action == "in_new":
        if (
            _shelf_requirement("require_buying_price_on_in")
            and not str(data.get("price") or "").strip()
        ):
            raise ValidationError("Enter the unit buying price.")
        item, movement, registered = stock_in_shelf_item_by_name(
            shop=shop,
            actor=actor,
            name=data.get("name"),
            unit=data.get("unit"),
            qty=data.get("qty"),
            buying_price=_parse_buying_price(
                data.get("price"), data.get("name") or "item"
            ),
            note=data.get("note") or "",
        )
        qty_text = f"{movement.quantity.normalize():f}"
        if registered:
            return (
                f"“{item.name}” added to the shelf list — "
                f"{qty_text} stocked in."
            )
        return f"Added {qty_text} to “{item.name}”."
    if action == "out_new":
        item, movement = stock_out_shelf_item_by_name(
            shop=shop,
            actor=actor,
            name=data.get("name"),
            qty=data.get("qty"),
            note=data.get("note") or "",
        )
        return (
            f"Removed {movement.quantity.normalize():f} "
            f"from “{item.name}”."
        )
    if action in (ShelfStockMovementType.IN, ShelfStockMovementType.OUT):
        if (
            action == ShelfStockMovementType.IN
            and _shelf_requirement("require_buying_price_on_in")
            and not str(data.get("price") or "").strip()
        ):
            raise ValidationError("Enter the unit buying price.")
        movement = record_shelf_movement(
            shop=shop,
            shelf_item_id=data.get("shelf_item_id"),
            mode=action,
            qty=data.get("qty"),
            actor=actor,
            note=data.get("note") or "",
            buying_price=(
                _parse_buying_price(
                    data.get("price"), data.get("shelf_item_id")
                )
                if action == ShelfStockMovementType.IN
                else None
            ),
        )
        is_in = action == ShelfStockMovementType.IN
        return (
            f"{'Added' if is_in else 'Removed'} {movement.quantity.normalize():f} "
            f"{'to' if is_in else 'from'} “{movement.shelf_item.name}”."
        )
    if action == "transfer":
        if to_shop is None or to_shop.pk == shop.pk:
            raise ValidationError("Pick another shop to send the shelf stock to.")
        if (
            _shelf_requirement("require_note_on_request")
            and not str(data.get("note") or "").strip()
        ):
            raise ValidationError("Enter a transfer note.")
        item, _target = transfer_shelf_item(
            from_shop=shop,
            to_shop=to_shop,
            shelf_item_id=data.get("shelf_item_id"),
            qty=data.get("qty"),
            actor=actor,
            note=data.get("note") or "",
        )
        return f"Sent “{item.name}” to {to_shop.name}."
    if action == "archive":
        item = archive_shelf_item(shop=shop, shelf_item_id=data.get("shelf_item_id"))
        return f"“{item.name}” removed from the shelf list."
    if action == "edit":
        item = update_shelf_item(
            shop=shop,
            shelf_item_id=data.get("shelf_item_id"),
            name=data.get("name"),
            category=data.get("category"),
            unit=data.get("unit"),
            low_stock_threshold=data.get("low_stock_threshold"),
            image=files.get("image") if files else None,
            clear_image=(data.get("clear_image") or "").strip() in ("1", "on"),
        )
        return f"“{item.name}” updated."
    if action == "suspend":
        item = set_shelf_item_suspended(
            shop=shop, shelf_item_id=data.get("shelf_item_id"), suspended=True
        )
        return f"“{item.name}” suspended — find it under Suspended items."
    if action == "unsuspend":
        item = set_shelf_item_suspended(
            shop=shop, shelf_item_id=data.get("shelf_item_id"), suspended=False
        )
        return f"“{item.name}” is back on the shelf list."
    raise ValidationError("Unknown shelf action.")


def apply_shelf_stock_lines(
    *, shop=None, actor, mode, lines, allowed_shop_ids=None
) -> dict:
    """Stock in/out many shelf lines in one post.

    Each ``line`` is either a table row (``shelf_item_id`` + qty/price/note)
    or the quick-add strip (``name`` + ``shop`` + unit/qty/price/note).
    ``shop`` may be None on an all-shops page — the row's shelf item (or the
    line's ``shop`` pick) then carries the target shop. ``allowed_shop_ids``
    bounds the scope when given. Per-line errors are collected and returned
    with a done count and total moved.
    """
    if mode not in (ShelfStockMovementType.IN, ShelfStockMovementType.OUT):
        raise ValidationError("Choose stock in or stock out.")
    done = 0
    total = Decimal("0")
    errors: list[str] = []
    price_required = (
        mode == ShelfStockMovementType.IN
        and _shelf_requirement("require_buying_price_on_in")
    )
    for line in lines:
        name = " ".join(str(line.get("name") or "").split())
        label = name or f"Item {line.get('shelf_item_id')}"
        try:
            if mode == ShelfStockMovementType.IN:
                if price_required and not str(line.get("price") or "").strip():
                    raise ValidationError("Enter the unit buying price.")
            if name:
                line_shop = line.get("shop") or shop
                if line_shop is None:
                    raise ValidationError("Pick the shop for this item.")
                if (
                    allowed_shop_ids is not None
                    and line_shop.pk not in allowed_shop_ids
                ):
                    raise ValidationError("That shop is not in your shelf scope.")
                if mode == ShelfStockMovementType.IN:
                    _item, movement, _registered = stock_in_shelf_item_by_name(
                        shop=line_shop,
                        actor=actor,
                        name=name,
                        unit=line.get("unit") or "",
                        qty=line.get("qty"),
                        buying_price=_parse_buying_price(
                            line.get("price"), label
                        ),
                        note=line.get("note") or "",
                    )
                else:
                    _item, movement = stock_out_shelf_item_by_name(
                        shop=line_shop,
                        actor=actor,
                        name=name,
                        qty=line.get("qty"),
                        note=line.get("note") or "",
                    )
            elif not str(line.get("shelf_item_id") or "").strip():
                raise ValidationError("Enter the shelf item name.")
            else:
                item = (
                    ShelfItem.objects.filter(
                        pk=line.get("shelf_item_id"), is_archived=False
                    )
                    .select_related("shop")
                    .first()
                )
                if item is None or (
                    shop is not None and item.shop_id != shop.pk
                ):
                    raise ValidationError("Shelf item not found for this shop.")
                if (
                    allowed_shop_ids is not None
                    and item.shop_id not in allowed_shop_ids
                ):
                    raise ValidationError(
                        "Shelf item is not in your shop scope."
                    )
                movement = record_shelf_movement(
                    shop=item.shop,
                    shelf_item_id=item.pk,
                    mode=mode,
                    qty=line.get("qty"),
                    actor=actor,
                    note=line.get("note") or "",
                    buying_price=(
                        _parse_buying_price(line.get("price"), label)
                        if mode == ShelfStockMovementType.IN
                        else None
                    ),
                )
        except ValidationError as exc:
            for message in getattr(exc, "messages", [str(exc)]):
                errors.append(f"{label}: {message}")
        else:
            done += 1
            total += movement.quantity
    return {"done": done, "total": total, "errors": errors}
