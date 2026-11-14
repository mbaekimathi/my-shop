"""Shelf mode: a shop's own stock list, registered and adjusted by hand."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from items.models import ShelfItem, ShelfStockMovement, ShelfStockMovementType

_MAX_QTY = Decimal("999999999.999")


def _qty_display(value) -> str:
    value = Decimal(value or 0)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


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
    rows = []
    for item in ShelfItem.objects.filter(shop=shop, is_archived=False).order_by("name"):
        threshold = Decimal(item.low_stock_threshold or 0)
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "unit": item.unit,
                "quantity": item.quantity,
                "quantity_display": _qty_display(item.quantity),
                "low_stock_threshold_display": _qty_display(threshold),
                "is_low": threshold > 0 and item.quantity <= threshold,
                "search_text": f"{item.name} {item.unit}".lower(),
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
    *, shop, actor=None, name, unit="", opening_qty=None, low_stock_threshold=None
) -> ShelfItem:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValidationError("Enter the shelf item name.")
    if len(name) > 200:
        raise ValidationError("Shelf item name is too long.")
    unit = " ".join(str(unit or "").split())[:40]
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
            existing.unit = unit
            existing.quantity = qty
            existing.low_stock_threshold = threshold
            existing.is_archived = False
            existing.save(
                update_fields=[
                    "name",
                    "unit",
                    "quantity",
                    "low_stock_threshold",
                    "is_archived",
                    "updated_at",
                ]
            )
            item = existing
        else:
            item = ShelfItem.objects.create(
                shop=shop,
                name=name,
                unit=unit,
                quantity=qty,
                low_stock_threshold=threshold,
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
    *, shop, shelf_item_id, mode, qty, actor=None, note=""
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
    if mode == ShelfStockMovementType.OUT:
        if amount > item.quantity:
            raise ValidationError(
                f"Only {_qty_display(item.quantity)} of “{item.name}” on the shelf."
            )
        item.quantity -= amount
    else:
        item.quantity += amount
    item.save(update_fields=["quantity", "updated_at"])
    return ShelfStockMovement.objects.create(
        shelf_item=item,
        movement_type=mode,
        quantity=amount,
        balance_after=item.quantity,
        note=str(note or "").strip()[:255],
        created_by=actor,
    )


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


def apply_shelf_action(*, shop, actor, data) -> str:
    """Run one shelf page action (register / in / out / archive); returns a success message."""
    action = (data.get("action") or "").strip()
    if action == "register":
        item = register_shelf_item(
            shop=shop,
            actor=actor,
            name=data.get("name"),
            unit=data.get("unit"),
            opening_qty=data.get("opening_qty"),
            low_stock_threshold=data.get("low_stock_threshold"),
        )
        return f"“{item.name}” added to the shelf list."
    if action in (ShelfStockMovementType.IN, ShelfStockMovementType.OUT):
        movement = record_shelf_movement(
            shop=shop,
            shelf_item_id=data.get("shelf_item_id"),
            mode=action,
            qty=data.get("qty"),
            actor=actor,
            note=data.get("note") or "",
        )
        is_in = action == ShelfStockMovementType.IN
        return (
            f"{'Added' if is_in else 'Removed'} {movement.quantity.normalize():f} "
            f"{'to' if is_in else 'from'} “{movement.shelf_item.name}”."
        )
    if action == "archive":
        item = archive_shelf_item(shop=shop, shelf_item_id=data.get("shelf_item_id"))
        return f"“{item.name}” removed from the shelf list."
    raise ValidationError("Unknown shelf action.")
