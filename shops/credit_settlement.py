"""Settle credit receipts and convert them to sales when fully paid."""

from decimal import Decimal

from django.utils import timezone

from shops.models import (
    ShopPaymentMethod,
    ShopReceiptKind,
    ShopReceiptStatus,
    payment_method_for_channels,
)


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _due(total, paid) -> Decimal:
    due = Decimal(total or 0) - Decimal(paid or 0)
    if due < 0:
        return Decimal("0.00")
    return due.quantize(Decimal("0.01"))


def _sync_sale_payment_fields(receipt) -> None:
    used = [
        channel
        for channel in ("cash", "mpesa", "card")
        if _money(getattr(receipt, f"{channel}_amount", 0)) > 0
    ]
    if used:
        receipt.payment_method = payment_method_for_channels(used)
        return
    paid = _money(receipt.amount_paid)
    if (receipt.mpesa_receipt_number or "").strip():
        receipt.payment_method = ShopPaymentMethod.MPESA
        receipt.mpesa_amount = paid
        return
    receipt.payment_method = ShopPaymentMethod.CASH
    receipt.cash_amount = paid


def convert_settled_credit_to_sale(receipt, *, paid_at=None) -> bool:
    """Turn a fully paid credit receipt into a sale. Does not save."""
    if getattr(receipt, "kind", None) != ShopReceiptKind.CREDIT:
        return False
    if getattr(receipt, "status", None) == ShopReceiptStatus.CANCELLED:
        return False
    if _due(receipt.total, receipt.amount_paid) > 0:
        return False
    paid = _money(receipt.amount_paid)
    if paid <= 0 and _money(receipt.total) <= 0:
        return False
    receipt.kind = ShopReceiptKind.SALE
    receipt.credit_due_date = None
    receipt.settled_from_credit = True
    receipt.converted_from_kind = ShopReceiptKind.CREDIT
    # The day the credit cleared is the day the sale is recognised.
    receipt.confirmed_payment_at = paid_at or timezone.now()
    _sync_sale_payment_fields(receipt)
    return True


def record_credit_collection(
    receipt,
    *,
    amount,
    payment_method: str,
    mpesa_receipt_number: str = "",
    paid_at=None,
) -> bool:
    """Apply a collection to a credit receipt and convert it when fully paid."""
    apply = _money(amount)
    method = (payment_method or "cash").strip().lower()
    payment_time = paid_at or timezone.now()
    receipt.amount_paid = _money(Decimal(receipt.amount_paid or 0) + apply)
    update_fields = ["amount_paid"]
    # Track the latest payment moment — for a settled credit this is the day
    # it converts to a sale, which is the day its revenue belongs to.
    receipt.confirmed_payment_at = payment_time
    update_fields.append("confirmed_payment_at")
    if method == "mpesa":
        receipt.mpesa_amount = _money(Decimal(receipt.mpesa_amount or 0) + apply)
        update_fields.append("mpesa_amount")
        ref = (mpesa_receipt_number or "").strip()
        if ref and not (receipt.mpesa_receipt_number or "").strip():
            receipt.mpesa_receipt_number = ref
            update_fields.append("mpesa_receipt_number")
    elif method == "card":
        receipt.card_amount = _money(
            Decimal(getattr(receipt, "card_amount", 0) or 0) + apply
        )
        update_fields.append("card_amount")
    else:
        receipt.cash_amount = _money(Decimal(receipt.cash_amount or 0) + apply)
        update_fields.append("cash_amount")

    converted = convert_settled_credit_to_sale(receipt, paid_at=payment_time)
    if converted:
        for field in (
            "kind",
            "payment_method",
            "credit_due_date",
            "cash_amount",
            "mpesa_amount",
            "card_amount",
            "settled_from_credit",
            "converted_from_kind",
        ):
            if field not in update_fields:
                update_fields.append(field)
    receipt.save(update_fields=update_fields)
    return converted
