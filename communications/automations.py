"""Event-driven Twilio shares from POS receipts."""

from __future__ import annotations

import logging
import threading
from decimal import Decimal, InvalidOperation

from shops.models import ShopReceiptKind
from shops.services import get_communications_settings

logger = logging.getLogger(__name__)


def _kes(value) -> str:
    try:
        amount = Decimal(value or 0).quantize(Decimal("1"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"KSh {amount:,.0f}"


def _first_name(receipt) -> str:
    raw = (receipt.client_name or "").strip()
    if not raw:
        return "Customer"
    return raw.split()[0].title()


def build_credit_sale_notification(receipt, *, request=None) -> str:
    """Customer-facing WhatsApp notice after a credit sale from the shop cart."""
    shop_name = ""
    if getattr(receipt, "shop_id", None) and getattr(receipt, "shop", None):
        shop_name = (receipt.shop.name or "").strip()
    shop_name = shop_name or "our shop"
    all_lines = list(receipt.lines.all())
    shown = all_lines[:12]
    item_rows = []
    for line in shown:
        qty = line.quantity
        name = (line.item_name or "Item").strip()
        item_rows.append(f"- {name} x{qty}  {_kes(line.line_total)}")
    extra = len(all_lines) - len(shown)
    if extra > 0:
        item_rows.append(f"- and {extra} more item(s)")
    items_block = "\n".join(item_rows) if item_rows else "- Items on credit"
    due = ""
    if receipt.credit_due_date:
        due = receipt.credit_due_date.strftime("%d %b %Y")
    parts = [
        f"Hi {_first_name(receipt)},",
        "",
        f"Credit sale at {shop_name}.",
        "",
        items_block,
        "",
        f"Total due: {_kes(receipt.total)}",
    ]
    if due:
        parts.append(f"Pay by: {due}")
    receipt_no = (receipt.receipt_number or "").strip()
    if receipt_no:
        parts.extend(["", f"Receipt {receipt_no}"])
    pay_url = _credit_account_pay_url(receipt, request=request)
    if pay_url:
        parts.extend(
            [
                "",
                "You can view your credit account and pay with M-Pesa using this link:",
                pay_url,
            ]
        )
    parts.extend(["", "Thank you."])
    return "\n".join(parts).strip()


def _credit_account_pay_url(receipt, *, request=None) -> str:
    client_id = getattr(receipt, "client_id", None)
    if not client_id:
        return ""
    try:
        from shops.credit_note import client_credit_note_url

        url = (client_credit_note_url(int(client_id), request=request) or "").strip()
    except Exception:
        logger.exception(
            "Credit account URL failed for receipt %s", getattr(receipt, "pk", None)
        )
        return ""
    if not url.lower().startswith(("http://", "https://")):
        logger.warning(
            "Credit account URL is not clickable for receipt %s.",
            getattr(receipt, "pk", None),
        )
        return ""
    return url


def maybe_send_receipt_share(receipt, *, message: str = "", request=None) -> None:
    """Send a sale/quotation/credit receipt via Twilio when automations are on."""
    row = get_communications_settings()
    if not row.enable_automations or not row.has_twilio_credentials():
        return
    kind = receipt.kind
    if kind == ShopReceiptKind.SALE and not row.auto_sale_receipt:
        return
    if kind == ShopReceiptKind.QUOTATION and not row.auto_quotation:
        return
    if kind == ShopReceiptKind.CREDIT and not row.auto_payment_reminder:
        return
    if kind not in {
        ShopReceiptKind.SALE,
        ShopReceiptKind.QUOTATION,
        ShopReceiptKind.CREDIT,
    }:
        return
    phone = (receipt.client_phone or "").strip()
    if not phone and receipt.client_id:
        phone = (getattr(receipt.client, "phone_number", "") or "").strip()
    if kind == ShopReceiptKind.CREDIT:
        body = build_credit_sale_notification(receipt, request=request)
    else:
        body = (message or "").strip()
    if not phone or not body:
        if kind == ShopReceiptKind.CREDIT and not phone:
            logger.warning(
                "Credit WhatsApp skipped for receipt %s: no client phone.",
                receipt.pk,
            )
        return
    _queue_send(phone, body, name=f"comms-auto-receipt-{receipt.pk}")


def credit_whatsapp_required() -> bool:
    row = get_communications_settings()
    return bool(
        row.enable_automations
        and row.auto_payment_reminder
        and row.has_twilio_credentials()
    )


def _party_first_name(raw: str, *, fallback: str = "there") -> str:
    name = (raw or "").strip()
    if not name:
        return fallback
    return name.split()[0].title()


def _supplier_whatsapp_phone(*, dial: str = "", national: str = "") -> str:
    national = (national or "").strip()
    dial = (dial or "").strip()
    if national.startswith("+"):
        return national
    if not national and not dial:
        return ""
    if dial.startswith("+"):
        return f"{dial}{national.lstrip('0')}" if national else dial
    combined = f"{dial}{national}".strip()
    return combined


def _stock_supplier_phone(movement) -> str:
    for line in movement.lines.all():
        phone = _supplier_whatsapp_phone(
            dial=line.supplier_phone_country_code,
            national=line.supplier_phone_number,
        )
        if phone:
            return phone
    return ""


def build_stock_supplier_notification(movement, *, shop=None) -> str:
    """WhatsApp notice to the supplier after Buy stock."""
    shop_obj = shop or getattr(movement, "shop", None)
    shop_name = (getattr(shop_obj, "name", None) or "").strip() or "our shop"
    lines = list(movement.lines.select_related("item").all())
    first = lines[0] if lines else None
    supplier = _party_first_name(
        getattr(first, "supplier_name", "") if first else "",
        fallback="there",
    )
    shown = lines[:12]
    item_rows = []
    total = Decimal("0")
    for line in shown:
        qty = int(line.quantity or 0)
        name = str(getattr(getattr(line, "item", None), "name", None) or "Item").strip()
        line_total = Decimal(line.buying_price or 0) * qty
        total += line_total
        item_rows.append(f"- {name} x{qty}  {_kes(line_total)}")
    extra = len(lines) - len(shown)
    if extra > 0:
        extra_total = sum(
            (Decimal(line.buying_price or 0) * int(line.quantity or 0) for line in lines[12:]),
            Decimal("0"),
        )
        total += extra_total
        item_rows.append(f"- and {extra} more item(s)")
    items_block = "\n".join(item_rows) if item_rows else "- Stock received"
    payment = ""
    if first and (first.payment_status or "").strip():
        from items.models import StockPaymentStatus

        payment = dict(StockPaymentStatus.choices).get(
            first.payment_status, first.payment_status.replace("_", " ").title()
        )
    from shops.services import DOC_NUMBER_PREFIX, format_simple_doc_number

    ref = format_simple_doc_number(DOC_NUMBER_PREFIX["stock_in"], movement.pk)
    parts = [
        f"Hi {supplier},",
        "",
        f"We received stock at {shop_name}.",
        "",
        items_block,
        "",
        f"Total: {_kes(total)}",
    ]
    if payment:
        parts.append(f"Payment: {payment}")
    parts.extend(["", f"Ref {ref}", "", "Thank you."])
    return "\n".join(parts).strip()


def build_expense_supplier_notification(expenses, *, shop=None) -> str:
    """WhatsApp notice to the supplier after Register expense."""
    rows = [row for row in (expenses or []) if row is not None]
    first = rows[0] if rows else None
    if first is None:
        return ""
    shop_obj = shop or getattr(first, "shop", None)
    shop_name = (getattr(shop_obj, "name", None) or "").strip() or "our shop"
    supplier = _party_first_name(first.supplier_name, fallback="there")
    shown = rows[:12]
    item_rows = []
    total = Decimal("0")
    for row in shown:
        name = (row.name or "Expense").strip()
        category = ""
        if hasattr(row, "get_category_display"):
            category = (row.get_category_display() or "").strip()
        label = f"{name} ({category})" if category and category.lower() not in name.lower() else name
        amount = Decimal(row.amount or 0)
        total += amount
        item_rows.append(f"- {label}  {_kes(amount)}")
    extra = len(rows) - len(shown)
    if extra > 0:
        total += sum((Decimal(row.amount or 0) for row in rows[12:]), Decimal("0"))
        item_rows.append(f"- and {extra} more item(s)")
    payment = ""
    if hasattr(first, "get_payment_status_display"):
        payment = first.get_payment_status_display()
    from shops.services import DOC_NUMBER_PREFIX, format_simple_doc_number

    ref = format_simple_doc_number(DOC_NUMBER_PREFIX["expense"], first.pk)
    if len(rows) > 1 and getattr(rows[-1], "pk", None):
        ref = f"{ref}-{rows[-1].pk}"
    parts = [
        f"Hi {supplier},",
        "",
        f"We recorded an expense at {shop_name}.",
        "",
        "\n".join(item_rows) if item_rows else "- Expense recorded",
        "",
        f"Total: {_kes(total)}",
    ]
    if payment:
        parts.append(f"Payment: {payment}")
    parts.extend(["", f"Ref {ref}", "", "Thank you."])
    return "\n".join(parts).strip()


def maybe_send_stock_supplier_notice(movement, *, shop=None) -> None:
    """Send Buy stock WhatsApp to the supplier when that automation is on."""
    from items.models import StockEntrySource, StockMovementType

    row = get_communications_settings()
    if not row.enable_automations or not row.auto_stock_supplier:
        return
    if not row.has_twilio_credentials():
        return
    if getattr(movement, "movement_type", "") != StockMovementType.IN:
        return
    if getattr(movement, "entry_source", "") != StockEntrySource.BUY_ITEMS:
        return
    phone = _stock_supplier_phone(movement)
    body = build_stock_supplier_notification(movement, shop=shop)
    if not phone or not body:
        if not phone:
            logger.warning(
                "Buy stock WhatsApp skipped for movement %s: no supplier phone.",
                getattr(movement, "pk", None),
            )
        return
    _queue_send(phone, body, name=f"comms-auto-stock-{movement.pk}")


def maybe_send_expense_supplier_notice(expense=None, *, expenses=None, shop=None) -> None:
    """Send Register expense WhatsApp to the supplier when that automation is on."""
    row = get_communications_settings()
    if not row.enable_automations or not row.auto_expense_supplier:
        return
    if not row.has_twilio_credentials():
        return
    rows = [item for item in (expenses or []) if item is not None]
    if not rows and expense is not None:
        rows = [expense]
    first = rows[0] if rows else None
    if first is None:
        return
    phone = _supplier_whatsapp_phone(
        dial=first.supplier_phone_country_code,
        national=first.supplier_phone_number,
    )
    body = build_expense_supplier_notification(rows, shop=shop)
    if not phone or not body:
        if not phone:
            logger.warning(
                "Expense WhatsApp skipped for expense %s: no supplier phone.",
                getattr(first, "pk", None),
            )
        return
    _queue_send(phone, body, name=f"comms-auto-expense-{first.pk}")


def build_item_catalogue_caption(item) -> str:
    """Customer-facing caption for a catalogue item share."""
    name = (getattr(item, "name", None) or "").strip() or "Item"
    category = (getattr(item, "category", None) or "").strip()
    price = Decimal("0")
    try:
        if hasattr(item, "resolve_list_price"):
            price = item.resolve_list_price()
        elif getattr(item, "shop_price", None) is not None:
            price = item.shop_price
    except (InvalidOperation, TypeError, ValueError):
        price = Decimal("0")
    parts = ["Hi {first_name},", "", name]
    if category:
        parts.append(category)
    parts.append(_kes(price))
    return "\n".join(parts).strip()


def maybe_send_new_item_catalogue(item, *, request=None) -> None:
    """WhatsApp a newly registered item to the saved automation audience."""
    row = get_communications_settings()
    if not row.enable_automations or not row.auto_item_catalogue:
        return
    if not row.has_twilio_credentials():
        return
    if item is None or getattr(item, "is_suspended", False):
        return
    profile = getattr(item, "created_by", None)
    if profile is None:
        return
    try:
        from .campaigns import create_catalogue_campaign

        create_catalogue_campaign(
            profile=profile,
            items=[item],
            filters={
                "audience_type": row.automation_audience_type or "sale",
                "last_purchase_days": row.automation_last_purchase_days or "",
                "shop_id": row.automation_shop_id or "",
            },
            request=request,
        )
    except ValueError:
        return
    except Exception:
        logger.exception(
            "New item WhatsApp share failed for item %s",
            getattr(item, "pk", None),
        )


def _queue_send(phone: str, body: str, *, name: str) -> None:
    thread = threading.Thread(
        target=_send_quietly,
        args=(phone, body),
        daemon=True,
        name=name,
    )
    thread.start()


def _send_quietly(phone: str, body: str) -> None:
    try:
        from .twilio import send_whatsapp_message

        result = send_whatsapp_message(phone=phone, text=body)
        if not result.get("ok"):
            logger.warning("Auto WhatsApp share failed: %s", result.get("error"))
    except Exception:
        logger.exception("Auto WhatsApp share failed")
