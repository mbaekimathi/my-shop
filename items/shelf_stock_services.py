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


def list_shelf_items(*, shop) -> list[dict]:
    items = list(
        ShelfItem.objects.filter(shop=shop, is_archived=False).order_by(
            "category", "name"
        )
    )
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


def recent_shelf_movements(*, shop, limit: int = 30) -> list[ShelfStockMovement]:
    return list(
        ShelfStockMovement.objects.filter(shelf_item__shop=shop)
        .select_related("shelf_item", "created_by__user")
        .order_by("-created_at", "-id")[:limit]
    )


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
    if existing is not None and not existing.is_archived:
        raise ValidationError(f"“{existing.name}” is already on the shelf list.")
    try:
        if existing is not None:
            existing.name = name
            existing.category = category
            existing.unit = unit
            existing.quantity = qty
            existing.low_stock_threshold = threshold
            existing.is_archived = False
            update_fields = [
                "name",
                "category",
                "unit",
                "quantity",
                "low_stock_threshold",
                "is_archived",
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
        .filter(pk=shelf_item_id, shop=shop, is_archived=False)
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
                or reason_clean == StockOutReason.TRADE_IN
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
        .filter(shop=shop, name__iexact=clean, is_archived=False)
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
        .filter(pk=shelf_item_id, shop=from_shop, is_archived=False)
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
    record_shelf_movement(
        shop=to_shop,
        shelf_item_id=target.pk,
        mode=ShelfStockMovementType.IN,
        qty=amount,
        actor=actor,
        note=note or f"Received from {from_shop.name}",
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
    raise ValidationError("Unknown shelf action.")
