"""Settle credit receipts and convert them to sales when fully paid."""

from decimal import Decimal

from shops.models import ShopPaymentMethod, ShopReceiptKind, ShopReceiptStatus


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _due(total, paid) -> Decimal:
    due = Decimal(total or 0) - Decimal(paid or 0)
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


def convert_settled_credit_to_sale(receipt) -> bool:
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
    _sync_sale_payment_fields(receipt)
    return True


def record_credit_collection(
    receipt,
    *,
    amount,
    payment_method: str,
    mpesa_receipt_number: str = "",
) -> bool:
    """Apply a collection to a credit receipt and convert it when fully paid."""
    apply = _money(amount)
    method = (payment_method or "cash").strip().lower()
    receipt.amount_paid = _money(Decimal(receipt.amount_paid or 0) + apply)
    update_fields = ["amount_paid"]
    if method == "mpesa":
        receipt.mpesa_amount = _money(Decimal(receipt.mpesa_amount or 0) + apply)
        update_fields.append("mpesa_amount")
        ref = (mpesa_receipt_number or "").strip()
        if ref and not (receipt.mpesa_receipt_number or "").strip():
            receipt.mpesa_receipt_number = ref
            update_fields.append("mpesa_receipt_number")
    else:
        receipt.cash_amount = _money(Decimal(receipt.cash_amount or 0) + apply)
        update_fields.append("cash_amount")

    converted = convert_settled_credit_to_sale(receipt)
    if converted:
        for field in (
            "kind",
            "payment_method",
            "credit_due_date",
            "cash_amount",
            "mpesa_amount",
        ):
            if field not in update_fields:
                update_fields.append(field)
    receipt.save(update_fields=update_fields)
    return converted
