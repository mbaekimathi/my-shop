"""Settle trade-out receipts via cash payment or item exchange."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shops.models import ShopPaymentMethod, ShopReceiptKind, ShopReceiptStatus


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def trade_balance_due(receipt) -> Decimal:
    """Outstanding trade value after cash collections and exchange stock-ins."""
    due = (
        Decimal(receipt.total or 0)
        - Decimal(receipt.amount_paid or 0)
        - Decimal(getattr(receipt, "trade_exchange_value", 0) or 0)
    )
    if due < 0:
        return Decimal("0.00")
    return due.quantize(Decimal("0.01"))


def _sync_sale_payment_fields(receipt) -> None:
    cash = _money(receipt.cash_amount)
    mpesa = _money(receipt.mpesa_amount)
    paid = _money(receipt.amount_paid)
    if cash > 0 and mpesa > 0:
        receipt.payment_method = ShopPaymentMethod.BOTH
        return
    if mpesa > 0:
        receipt.payment_method = ShopPaymentMethod.MPESA
        return
    if cash > 0:
        receipt.payment_method = ShopPaymentMethod.CASH
        return
    if (receipt.mpesa_receipt_number or "").strip():
        receipt.payment_method = ShopPaymentMethod.MPESA
        receipt.mpesa_amount = paid
        return
    receipt.payment_method = ShopPaymentMethod.CASH
    receipt.cash_amount = paid


def _append_settlement(receipt, event: dict) -> None:
    events = list(receipt.trade_settlements or [])
    events.append(event)
    receipt.trade_settlements = events


def finalize_trade_if_cleared(receipt) -> str:
    """
    When balance is cleared:
    - with any cash/M-Pesa collected → convert to sale
    - exchange-only → mark confirmed, keep trade_out
    Returns action: '', 'converted_sale', or 'confirmed'.
    """
    if getattr(receipt, "kind", None) != ShopReceiptKind.TRADE_OUT:
        return ""
    if getattr(receipt, "status", None) == ShopReceiptStatus.CANCELLED:
        return ""
    if trade_balance_due(receipt) > 0:
        return ""

    paid = _money(receipt.amount_paid)
    if paid > 0 or _money(receipt.total) <= 0:
        receipt.kind = ShopReceiptKind.SALE
        receipt.settled_from_trade = True
        _sync_sale_payment_fields(receipt)
        if receipt.status == ShopReceiptStatus.ACTIVE:
            receipt.status = ShopReceiptStatus.CONFIRMED
        return "converted_sale"

    if receipt.status != ShopReceiptStatus.CONFIRMED:
        receipt.status = ShopReceiptStatus.CONFIRMED
        return "confirmed"
    return ""


def record_trade_payment(
    receipt,
    *,
    amount,
    payment_method: str,
    mpesa_receipt_number: str = "",
    actor=None,
) -> dict:
    """Apply cash/M-Pesa toward a trade-out and convert when fully paid."""
    if receipt.kind != ShopReceiptKind.TRADE_OUT:
        raise ValidationError("Only open trade-out receipts can be converted to a sale.")
    if receipt.status == ShopReceiptStatus.CANCELLED:
        raise ValidationError("This trade-out was cancelled.")

    apply = _money(amount)
    if apply <= 0:
        raise ValidationError("Enter an amount greater than zero.")
    due = trade_balance_due(receipt)
    if due <= 0:
        raise ValidationError("This trade-out is already cleared.")
    if apply > due:
        raise ValidationError(f"Amount exceeds balance left (KSh {due}).")

    method = (payment_method or "cash").strip().lower()
    if method not in {ShopPaymentMethod.CASH, ShopPaymentMethod.MPESA}:
        raise ValidationError("Choose cash or M-Pesa.")

    receipt.amount_paid = _money(Decimal(receipt.amount_paid or 0) + apply)
    update_fields = ["amount_paid", "trade_settlements"]
    if method == ShopPaymentMethod.MPESA:
        receipt.mpesa_amount = _money(Decimal(receipt.mpesa_amount or 0) + apply)
        update_fields.append("mpesa_amount")
        ref = (mpesa_receipt_number or "").strip()
        if ref and not (receipt.mpesa_receipt_number or "").strip():
            receipt.mpesa_receipt_number = ref
            update_fields.append("mpesa_receipt_number")
    else:
        receipt.cash_amount = _money(Decimal(receipt.cash_amount or 0) + apply)
        update_fields.append("cash_amount")

    _append_settlement(
        receipt,
        {
            "type": "payment",
            "amount": str(apply),
            "method": method,
            "mpesa_receipt_number": (mpesa_receipt_number or "").strip(),
            "at": timezone.now().isoformat(),
            "by_id": getattr(actor, "pk", None),
        },
    )

    action = finalize_trade_if_cleared(receipt)
    if action == "converted_sale":
        for field in (
            "kind",
            "payment_method",
            "cash_amount",
            "mpesa_amount",
            "settled_from_trade",
            "status",
        ):
            if field not in update_fields:
                update_fields.append(field)
    elif action == "confirmed" and "status" not in update_fields:
        update_fields.append("status")

    receipt.save(update_fields=update_fields)
    return {
        "converted": action == "converted_sale",
        "confirmed": action == "confirmed",
        "balance": trade_balance_due(receipt),
        "amount_paid": _money(receipt.amount_paid),
        "exchange_value": _money(getattr(receipt, "trade_exchange_value", 0)),
    }


@transaction.atomic
def record_trade_exchange(
    receipt,
    *,
    item_id: int,
    quantity: int,
    buying_price,
    serial_numbers=None,
    actor=None,
) -> dict:
    """
    Stock in an exchange item against a trade-out.

    Deducts buying_price × qty from the outstanding trade balance and creates
    a stock-in movement labelled trade exchange.
    """
    from items.models import (
        Item,
        ItemSerial,
        ShopStock,
        StockEntrySource,
        StockMovement,
        StockMovementLine,
        StockMovementType,
        StockPaymentStatus,
    )
    from items.services import apply_stock_in_average_cost, _normalize_serial_list

    if receipt.kind != ShopReceiptKind.TRADE_OUT:
        raise ValidationError("Only open trade-out receipts accept exchange items.")
    if receipt.status == ShopReceiptStatus.CANCELLED:
        raise ValidationError("This trade-out was cancelled.")

    due = trade_balance_due(receipt)
    if due <= 0:
        raise ValidationError("This trade-out is already cleared.")

    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        raise ValidationError("Enter a quantity greater than zero.")

    unit = _money(buying_price)
    if unit < 0:
        raise ValidationError("Buying price cannot be negative.")
    value = (unit * qty).quantize(Decimal("0.01"))
    if value <= 0:
        raise ValidationError("Exchange value must be greater than zero.")
    if value > due:
        raise ValidationError(
            f"Exchange value KSh {value} exceeds balance left (KSh {due})."
        )

    item = (
        Item.objects.select_for_update()
        .filter(pk=item_id, is_suspended=False)
        .first()
    )
    if item is None:
        raise ValidationError("Select a valid item to stock in.")

    serials = _normalize_serial_list(serial_numbers or [])
    if item.track_serial_number:
        if len(serials) != qty:
            raise ValidationError(
                f"“{item.name}” needs {qty} serial number(s) for this exchange."
            )

    shop = receipt.shop
    shop_stock, _ = ShopStock.objects.select_for_update().get_or_create(
        shop=shop,
        item=item,
        defaults={"quantity": 0},
    )

    if item.track_serial_number:
        existing = {
            row.serial_number: row
            for row in ItemSerial.objects.select_for_update().filter(
                item=item, serial_number__in=serials
            )
        }
        for serial in serials:
            row = existing.get(serial)
            if row is None:
                ItemSerial.objects.create(
                    item=item,
                    shop=shop,
                    serial_number=serial,
                    is_available=True,
                )
            else:
                if row.is_available and row.shop_id and row.shop_id != shop.pk:
                    raise ValidationError(
                        f"Serial {serial} is already in stock at another shop."
                    )
                row.is_available = True
                row.shop = shop
                row.save(update_fields=["is_available", "shop", "updated_at"])

    apply_stock_in_average_cost(shop_stock, qty=qty, unit_cost=unit)
    shop_stock.quantity += qty
    shop_stock.save(update_fields=["quantity", "average_cost", "updated_at"])
    item.stock += qty
    item.save(update_fields=["stock", "updated_at"])

    movement = StockMovement.objects.create(
        movement_type=StockMovementType.IN,
        entry_source=StockEntrySource.TRADE_EXCHANGE,
        shop=shop,
        created_by=actor,
        notes=f"Trade exchange for {receipt.receipt_number}",
        payment_status=StockPaymentStatus.PAID,
        amount_paid=value,
        supplier_notified=True,
    )
    StockMovementLine.objects.create(
        movement=movement,
        item=item,
        quantity=qty,
        buying_price=unit,
        unit_cost=unit,
        payment_status=StockPaymentStatus.PAID,
        note=f"Trade exchange · {receipt.receipt_number}",
        serial_numbers=serials,
    )

    receipt.trade_exchange_value = _money(
        Decimal(receipt.trade_exchange_value or 0) + value
    )
    update_fields = ["trade_exchange_value", "trade_settlements"]
    _append_settlement(
        receipt,
        {
            "type": "exchange",
            "value": str(value),
            "item_id": item.pk,
            "item_name": item.name,
            "qty": qty,
            "buying_price": str(unit),
            "stock_movement_id": movement.pk,
            "serial_numbers": serials,
            "at": timezone.now().isoformat(),
            "by_id": getattr(actor, "pk", None),
        },
    )

    action = finalize_trade_if_cleared(receipt)
    if action == "converted_sale":
        for field in (
            "kind",
            "payment_method",
            "cash_amount",
            "mpesa_amount",
            "settled_from_trade",
            "status",
        ):
            if field not in update_fields:
                update_fields.append(field)
    elif action == "confirmed" and "status" not in update_fields:
        update_fields.append("status")

    receipt.save(update_fields=update_fields)
    return {
        "converted": action == "converted_sale",
        "confirmed": action == "confirmed",
        "balance": trade_balance_due(receipt),
        "exchange_value": _money(receipt.trade_exchange_value),
        "amount_paid": _money(receipt.amount_paid),
        "item_name": item.name,
        "quantity": qty,
        "value": value,
        "stock_movement_id": movement.pk,
        "stock_updates": [
            {
                "id": item.pk,
                "quantity": int(shop_stock.quantity or 0),
            }
        ],
    }


def _clamp_trade_applied_to_total(receipt) -> list[str]:
    """
    After a trade return lowers the receipt total, trim cash/exchange that now
    exceeds the remaining trade value. Prefer trimming cash first, then exchange.
    """
    total = _money(receipt.total)
    paid = _money(receipt.amount_paid)
    exchange = _money(getattr(receipt, "trade_exchange_value", 0))
    applied = paid + exchange
    if applied <= total:
        return []

    excess = applied - total
    update_fields: list[str] = []

    if paid > 0 and excess > 0:
        trim = min(paid, excess)
        receipt.amount_paid = _money(paid - trim)
        update_fields.append("amount_paid")
        # Keep till splits aligned when possible.
        cash = _money(receipt.cash_amount)
        mpesa = _money(receipt.mpesa_amount)
        if cash + mpesa > 0:
            # Trim cash first, then M-Pesa.
            cash_trim = min(cash, trim)
            receipt.cash_amount = _money(cash - cash_trim)
            leftover = trim - cash_trim
            if leftover > 0:
                receipt.mpesa_amount = _money(max(Decimal("0.00"), mpesa - leftover))
            update_fields.extend(["cash_amount", "mpesa_amount"])
        excess -= trim
        paid = _money(receipt.amount_paid)

    if exchange > 0 and excess > 0:
        trim = min(exchange, excess)
        receipt.trade_exchange_value = _money(exchange - trim)
        update_fields.append("trade_exchange_value")

    return list(dict.fromkeys(update_fields))


@transaction.atomic
def record_trade_return(receipt, *, lines, actor=None) -> dict:
    """
    Return traded-out item(s) to shop stock.

    Restocks inventory, writes a CUSTOMER_RETURN stock movement (stock report /
    stock movement), updates the trade totals, and logs the event on the trade.
    """
    from shops.services import return_shop_receipt_items

    is_open_trade = receipt.kind == ShopReceiptKind.TRADE_OUT
    is_settled_trade = (
        receipt.kind == ShopReceiptKind.SALE
        and bool(getattr(receipt, "settled_from_trade", False))
    )
    if not is_open_trade and not is_settled_trade:
        raise ValidationError("Only trade receipts can be returned from Tradings.")
    if receipt.status == ShopReceiptStatus.CANCELLED:
        raise ValidationError("This trade was already fully returned.")

    if not isinstance(lines, list) or not lines:
        raise ValidationError("Select at least one item to return.")

    result = return_shop_receipt_items(
        shop=receipt.shop,
        receipt_id=receipt.pk,
        payload={"lines": lines},
        actor=actor,
    )
    receipt.refresh_from_db()

    returned_units = sum(int(row.get("qty") or row.get("quantity") or 0) for row in lines)
    names = []
    for row in lines:
        label = (row.get("item_name") or row.get("name") or "").strip()
        if label:
            names.append(label)
    detail_label = ", ".join(names[:3]) if names else f"{returned_units} unit(s)"

    update_fields: list[str] = []
    if hasattr(receipt, "trade_settlements"):
        _append_settlement(
            receipt,
            {
                "type": "return",
                "qty": returned_units,
                "lines": [
                    {
                        "line_id": row.get("line_id") or row.get("id"),
                        "qty": row.get("qty") or row.get("quantity"),
                        "serials": row.get("serials")
                        or row.get("serial_numbers")
                        or [],
                    }
                    for row in lines
                ],
                "detail": detail_label,
                "at": timezone.now().isoformat(),
                "by_id": getattr(actor, "pk", None),
            },
        )
        update_fields.append("trade_settlements")

    converted = False
    confirmed = False
    if receipt.kind == ShopReceiptKind.TRADE_OUT:
        update_fields.extend(_clamp_trade_applied_to_total(receipt))
        action = finalize_trade_if_cleared(receipt)
        converted = action == "converted_sale"
        confirmed = action == "confirmed"
        if converted:
            for field in (
                "kind",
                "payment_method",
                "cash_amount",
                "mpesa_amount",
                "settled_from_trade",
                "status",
            ):
                if field not in update_fields:
                    update_fields.append(field)
        elif confirmed and "status" not in update_fields:
            update_fields.append("status")

    if update_fields:
        receipt.save(update_fields=list(dict.fromkeys(update_fields)))

    balance = (
        trade_balance_due(receipt)
        if receipt.kind == ShopReceiptKind.TRADE_OUT
        else Decimal("0.00")
    )
    return {
        **result,
        "converted": converted,
        "confirmed": confirmed,
        "balance": balance,
        "amount_paid": _money(receipt.amount_paid),
        "exchange_value": _money(getattr(receipt, "trade_exchange_value", 0)),
        "returned_units": returned_units,
    }
