"""Public signed receipt / quotation document pages for customers."""

from decimal import Decimal

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from employees.analytics_services import _due_amount, _money_ksh
from shops.credit_note import credit_note_branding, client_credit_note_url
from shops.models import ShopReceipt, ShopReceiptKind, ShopReceiptStatus
from shops.receipt_link import unsign_receipt_doc_token
from shops.services import _qty_label

_DOC_TITLES = {
    ShopReceiptKind.SALE: "Sales receipt",
    ShopReceiptKind.CREDIT: "Credit sale receipt",
    ShopReceiptKind.TRADE_OUT: "Trade-out receipt",
    ShopReceiptKind.TRADE_IN: "Trade-in receipt",
}


def _receipt_from_token(token: str) -> ShopReceipt:
    receipt_id = unsign_receipt_doc_token(token)
    receipt = (
        ShopReceipt.objects.filter(pk=receipt_id)
        .select_related("shop", "client", "created_by", "created_by__user")
        .prefetch_related("lines")
        .first()
    )
    if receipt is None:
        raise Http404("Document not found.")
    return receipt


def build_receipt_doc(receipt: ShopReceipt, *, request=None) -> dict:
    lines = []
    for line in receipt.lines.all():
        qty = Decimal(line.quantity or 0) - Decimal(line.returned_quantity or 0)
        if qty <= 0:
            continue
        unit = Decimal(line.unit_price or 0)
        lines.append(
            {
                "name": line.item_name or "Item",
                "qty": _qty_label(qty),
                "unit": _money_ksh(unit),
                "total": _money_ksh((unit * qty).quantize(Decimal("0.01"))),
            }
        )
    total = Decimal(receipt.total or 0)
    paid = Decimal(receipt.amount_paid or 0)
    due = _due_amount(total, paid)
    cancelled = receipt.status == ShopReceiptStatus.CANCELLED
    is_credit = receipt.kind == ShopReceiptKind.CREDIT
    credit_account_url = ""
    if is_credit and receipt.client_id:
        credit_account_url = client_credit_note_url(
            int(receipt.client_id), request=request
        )
    cashier = ""
    if receipt.created_by_id and getattr(receipt.created_by, "user", None):
        cashier = (
            receipt.created_by.user.get_full_name()
            or receipt.created_by.employee_id
            or receipt.created_by.user.username
        )
    return {
        "doc_title": "Quotation"
        if receipt.kind == ShopReceiptKind.QUOTATION
        else _DOC_TITLES.get(receipt.kind, "Receipt"),
        "receipt_number": receipt.receipt_number,
        "kind_label": receipt.get_kind_display(),
        "status_label": receipt.get_status_display(),
        "status_tone": "bad" if cancelled else "ok",
        "cancelled": cancelled,
        "client_name": (receipt.client_name or "").strip()
        or getattr(receipt.client, "full_name", "")
        or "Customer",
        "client_phone": (receipt.client_phone or "").strip()
        or getattr(receipt.client, "phone_number", "")
        or "",
        "shop_name": receipt.shop.name if receipt.shop else "",
        "cashier": cashier,
        "when": receipt.created_at,
        "payment_label": receipt.get_payment_method_display(),
        "mpesa_receipt_number": receipt.mpesa_receipt_number or "",
        "credit_due_date": receipt.credit_due_date,
        "is_credit": is_credit,
        "lines": lines,
        "subtotal": _money_ksh(receipt.subtotal or 0),
        "tax_amount": _money_ksh(receipt.tax_amount or 0),
        "total": _money_ksh(total),
        "paid": _money_ksh(paid),
        "due": _money_ksh(due),
        "has_due": due > 0 and not cancelled,
        "credit_account_url": credit_account_url,
    }


@require_GET
def receipt_doc_page(request, token, *, quotation: bool = False):
    receipt = _receipt_from_token(token)
    is_quotation = receipt.kind == ShopReceiptKind.QUOTATION
    if is_quotation != quotation:
        raise Http404("Document not found.")
    return render(
        request,
        "shops/receipt_doc.html",
        {
            **credit_note_branding(request=request),
            **build_receipt_doc(receipt, request=request),
        },
    )
