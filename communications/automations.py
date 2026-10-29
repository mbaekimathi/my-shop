"""Event-driven Twilio shares from POS receipts."""

from __future__ import annotations

import logging
import re
import threading
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.utils import timezone

from shops.models import ShopReceiptKind
from shops.services import _qty_label, get_communications_settings

from .constants import (
    CHANNEL_SMS,
    CHANNEL_WHATSAPP,
    MESSAGE_STATUS_ACTIVE,
    TRIGGER_CHANNEL_BOTH,
    TRIGGER_CHANNEL_VALUES,
)

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
        qty = _qty_label(line.quantity)
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
    if _deliverable_triggered_draft_exists(receipt, row):
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


def _deliverable_triggered_draft_exists(receipt, row) -> bool:
    """An active draft for this receipt kind with at least one ready channel.

    When true, the drafted message supersedes the legacy auto receipt share so
    the customer gets one message, not two.
    """
    from .models import TriggeredMessage

    try:
        drafts = TriggeredMessage.objects.filter(
            trigger=receipt.kind,
            status=MESSAGE_STATUS_ACTIVE,
            **_triggered_message_scope(getattr(receipt, "shop", None)),
        )
        for draft in drafts:
            if any(
                _triggered_channel_ready(row, channel)
                for channel in _trigger_channels(draft)
            ):
                return True
    except Exception:
        logger.exception(
            "Triggered draft check failed for receipt %s",
            getattr(receipt, "pk", "?"),
        )
    return False


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
        qty = Decimal(line.quantity or 0)
        qty_label = _qty_label(qty)
        name = str(getattr(getattr(line, "item", None), "name", None) or "Item").strip()
        line_total = Decimal(line.buying_price or 0) * qty
        total += line_total
        item_rows.append(f"- {name} x{qty_label}  {_kes(line_total)}")
    extra = len(lines) - len(shown)
    if extra > 0:
        extra_total = sum(
            (Decimal(line.buying_price or 0) * Decimal(line.quantity or 0) for line in lines[12:]),
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


WHATSAPP_MEDIA_CAPTION_LIMIT = 1024
WHATSAPP_TEXT_LIMIT = 4096
DEFAULT_CATALOGUE_TEMPLATE = "Hi {first_name},"


def _item_catalogue_line(item) -> str:
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
    if category:
        return f"{name} · {category} · {_kes(price)}"
    return f"{name} · {_kes(price)}"


def build_catalogue_items_block(items) -> str:
    """Item names and prices inserted when a template includes {items}."""
    chosen = [item for item in (items or []) if item is not None]
    if not chosen:
        return ""
    if len(chosen) == 1:
        item = chosen[0]
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
        parts = [name]
        if category:
            parts.append(category)
        parts.append(_kes(price))
        return "\n".join(parts)
    return "\n".join(_item_catalogue_line(item) for item in chosen)


def apply_catalogue_message_template(
    template: str, items, *, card: bool = False
) -> str:
    """Expand a custom share template. Item details go on the card image when card=True."""
    chosen = [item for item in (items or []) if item is not None]
    body = (template or "").strip() or DEFAULT_CATALOGUE_TEMPLATE
    if "{items}" in body:
        replacement = "" if card else build_catalogue_items_block(chosen)
        body = body.replace("{items}", replacement)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def build_catalogue_share_caption(items) -> str:
    """One WhatsApp caption listing every selected item."""
    return apply_catalogue_message_template(DEFAULT_CATALOGUE_TEMPLATE, items)


def fit_whatsapp_caption(text: str, *, has_media: bool = False) -> str:
    limit = WHATSAPP_MEDIA_CAPTION_LIMIT if has_media else WHATSAPP_TEXT_LIMIT
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 1)].rstrip() + "…"


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


def _receipt_doc_url(receipt, *, quotation: bool, request=None) -> str:
    """Signed public URL for this receipt's document page, or "" if mismatched."""
    is_quotation = getattr(receipt, "kind", "") == ShopReceiptKind.QUOTATION
    if is_quotation != quotation:
        return ""
    receipt_id = getattr(receipt, "pk", None)
    if not receipt_id:
        return ""
    try:
        from shops.receipt_link import client_receipt_doc_url

        url = (
            client_receipt_doc_url(
                int(receipt_id), quotation=quotation, request=request
            )
            or ""
        ).strip()
    except Exception:
        logger.exception(
            "Receipt document URL failed for receipt %s", receipt_id
        )
        return ""
    if not url.lower().startswith(("http://", "https://")):
        logger.warning(
            "Receipt document URL is not clickable for receipt %s.", receipt_id
        )
        return ""
    return url


def render_trigger_message(
    template: str, receipt, *, link: str = "", request=None
) -> str:
    """Expand {placeholders} for a triggered message — plain str.replace only."""
    client_name = (getattr(receipt, "client_name", "") or "").strip()
    first = client_name.split()[0].title() if client_name else "Customer"
    shop_name = (
        getattr(getattr(receipt, "shop", None), "name", "") or ""
    ).strip() or "our shop"
    due_date = getattr(receipt, "credit_due_date", None)
    replacements = {
        "{first_name}": first,
        "{full_name}": client_name or "Customer",
        "{shop_name}": shop_name,
        "{total}": _kes(getattr(receipt, "total", None)),
        "{receipt_number}": (getattr(receipt, "receipt_number", "") or "").strip(),
        "{due_date}": due_date.strftime("%d %b %Y") if due_date else "",
        "{account_link}": "",
        "{receipt_link}": "",
        "{quotation_link}": "",
    }
    body = template or ""
    url = (link or "").strip()
    if "{account_link}" in body or "{account_link}" in url:
        replacements["{account_link}"] = _credit_account_pay_url(
            receipt, request=request
        )
    if "{receipt_link}" in body or "{receipt_link}" in url:
        replacements["{receipt_link}"] = _receipt_doc_url(
            receipt, quotation=False, request=request
        )
    if "{quotation_link}" in body or "{quotation_link}" in url:
        replacements["{quotation_link}"] = _receipt_doc_url(
            receipt, quotation=True, request=request
        )
    for key, value in replacements.items():
        body = body.replace(key, value)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    for key, value in replacements.items():
        url = url.replace(key, value)
    url = url.strip()
    if url:
        body = f"{body}\n\n{url}" if body else url
    return body


def _triggered_channel_ready(row, channel: str) -> bool:
    """Whether the company enabled this channel and saved working credentials."""
    if channel == CHANNEL_SMS:
        return bool(row.enable_sms and row.has_sms_credentials())
    return bool(row.enable_whatsapp and row.has_twilio_credentials())


def _trigger_channels(draft) -> list[str]:
    """Outbound channels for a draft; unknown values fall back to WhatsApp."""
    value = (getattr(draft, "channel", "") or "").strip()
    if value == TRIGGER_CHANNEL_BOTH:
        return [CHANNEL_WHATSAPP, CHANNEL_SMS]
    if value in {CHANNEL_WHATSAPP, CHANNEL_SMS}:
        return [value]
    return [CHANNEL_WHATSAPP]


def maybe_send_triggered_messages(receipt, *, request=None) -> None:
    """Queue active message drafts whose trigger matches this receipt kind.

    Drafts are resolved and queued campaigns created inline — a few fast
    writes — so a send is never lost if the worker process recycles after
    the response (Passenger/cPanel kills daemon threads). Only provider
    delivery runs on a background thread; the cron worker remains the
    durable fallback for anything left queued.
    """
    kind = receipt.kind
    if kind not in {
        ShopReceiptKind.SALE,
        ShopReceiptKind.QUOTATION,
        ShopReceiptKind.CREDIT,
        ShopReceiptKind.TRADE_OUT,
        ShopReceiptKind.TRADE_IN,
    }:
        return
    row = get_communications_settings()
    if not row.enable_automations:
        return
    if not any(
        _triggered_channel_ready(row, channel)
        for channel in (CHANNEL_WHATSAPP, CHANNEL_SMS)
    ):
        return
    campaign_ids = _queue_triggered_messages(receipt, request)
    if not campaign_ids:
        return
    thread = threading.Thread(
        target=_dispatch_queued_triggered,
        args=(campaign_ids,),
        daemon=True,
        name=f"comms-triggered-{getattr(receipt, 'pk', 'new')}",
    )
    thread.start()


def _close_thread_connections() -> None:
    """Drop this thread's DB handles — never inside an open transaction."""
    if connection.in_atomic_block:
        return
    close_old_connections()


def _dispatch_queued_triggered(campaign_ids) -> None:
    """Deliver queued triggered campaigns on a background thread.

    If this thread is killed mid-flight the campaigns stay QUEUED and the
    next `process_comms_queue` run finishes them — nothing is lost.
    """
    try:
        for campaign_id in campaign_ids:
            _dispatch_triggered_send(campaign_id)
    finally:
        _close_thread_connections()


def _queue_triggered_messages(receipt, request) -> list[int]:
    """Create triggered campaigns for this receipt; return pks due to send now."""
    from .models import TriggeredMessage

    try:
        row = get_communications_settings()
        drafts = list(
            TriggeredMessage.objects.filter(
                trigger=receipt.kind,
                status=MESSAGE_STATUS_ACTIVE,
                **_triggered_message_scope(getattr(receipt, "shop", None)),
            )
        )
        if not drafts:
            return []
        phone = (receipt.client_phone or "").strip()
        if not phone and receipt.client_id:
            phone = (getattr(receipt.client, "phone_number", "") or "").strip()
        if not phone:
            return []
        due_now = []
        for draft in drafts:
            channels = [
                channel
                for channel in _trigger_channels(draft)
                if _triggered_channel_ready(row, channel)
            ]
            if not channels:
                continue
            body = render_trigger_message(
                draft.body, receipt, link=draft.link, request=request
            )
            if not body:
                continue
            campaign_id = _queue_triggered_send(
                phone=phone,
                body=body,
                receipt=receipt,
                delay_minutes=draft.delay_minutes,
                label=f"Auto message · {draft.get_trigger_display()}",
                channels=channels,
            )
            if campaign_id:
                due_now.append(campaign_id)
        return due_now
    except Exception:
        logger.exception(
            "Triggered messages failed for receipt %s",
            getattr(receipt, "pk", "?"),
        )
        return []
    finally:
        _close_thread_connections()


def _triggered_message_scope(shop) -> dict:
    """Shop with custom messages on reads its own drafts; else company drafts."""
    if getattr(shop, "pk", None) is None:
        return {"shop__isnull": True}
    from shops.models import ShopPosSettings

    custom = (
        ShopPosSettings.objects.filter(shop_id=shop.pk)
        .values_list("override_messages", flat=True)
        .first()
    )
    return {"shop_id": shop.pk} if custom else {"shop__isnull": True}


def seed_shop_triggered_messages(shop) -> int:
    """Copy company message drafts into the shop scope (only while it has none)."""
    from .models import TriggeredMessage

    if shop is None or TriggeredMessage.objects.filter(shop_id=shop.pk).exists():
        return 0
    rows = [
        TriggeredMessage(
            shop=shop,
            trigger=draft.trigger,
            delay_minutes=draft.delay_minutes,
            body=draft.body,
            link=draft.link,
            channel=draft.channel,
            status=draft.status,
        )
        for draft in TriggeredMessage.objects.filter(shop__isnull=True)
    ]
    TriggeredMessage.objects.bulk_create(rows)
    return len(rows)


def _queue_triggered_send(
    *,
    phone: str,
    body: str,
    receipt,
    delay_minutes: int,
    label: str,
    channels: list[str],
) -> int | None:
    """Persist a one-recipient campaign; return its pk when it is due to send now."""
    from datetime import timedelta

    from .constants import CAMPAIGN_DRAFT, CAMPAIGN_QUEUED, MSG_PENDING
    from .models import BroadcastCampaign, OutboundMessage

    delay = max(0, int(delay_minutes or 0))
    due_at = timezone.now() + timedelta(minutes=delay)
    campaign = BroadcastCampaign.objects.create(
        created_by=getattr(receipt, "created_by", None),
        body_template=body,
        filters={
            "triggered_message": True,
            "trigger_label": label,
            "send_after": due_at.isoformat(),
            "receipt_id": getattr(receipt, "pk", None),
        },
        status=CAMPAIGN_QUEUED if delay <= 0 else CAMPAIGN_DRAFT,
        recipient_count=1,
    )
    client = getattr(receipt, "client", None)
    OutboundMessage.objects.bulk_create(
        [
            OutboundMessage(
                campaign=campaign,
                channel=channel,
                client=client if getattr(client, "pk", None) else None,
                client_name=(getattr(receipt, "client_name", "") or "").strip(),
                phone=phone,
                body=body,
                status=MSG_PENDING,
            )
            for channel in channels
        ]
    )
    if delay <= 0:
        return campaign.pk
    timer = threading.Timer(delay * 60, _promote_due_sends)
    timer.daemon = True
    timer.start()
    return None


def _dispatch_triggered_send(campaign_id: int) -> None:
    """Send a triggered campaign without making the customer wait for cron.

    Triggered sends are one or two messages and this already runs on a daemon
    thread off the request path, so when the queue would otherwise sit until
    the next `process_comms_queue` cron tick we deliver on this thread. When a
    real worker exists (Celery/Redis, or inline mode) the normal dispatcher is
    used instead. Broadcasts keep using `enqueue_campaign` unchanged.
    """
    from django.conf import settings

    from .campaigns import enqueue_campaign
    from .tasks import process_campaign_sync

    mode = (getattr(settings, "COMMS_SEND_MODE", "auto") or "auto").strip().lower()
    celery_ready = mode in {"auto", "celery"} and getattr(
        settings, "REDIS_ENABLED", False
    )
    inline_mode = mode == "inline" or (
        mode == "auto"
        and (
            getattr(settings, "DEBUG", False)
            or getattr(settings, "COMMS_INLINE_SEND", False)
        )
    )
    if celery_ready or inline_mode:
        try:
            enqueue_campaign(campaign_id)
        except Exception:
            logger.exception(
                "Triggered send enqueue failed for campaign %s", campaign_id
            )
        return
    try:
        process_campaign_sync(campaign_id)
    except Exception:
        logger.exception(
            "Triggered send inline processing failed for campaign %s", campaign_id
        )


def _promote_due_sends() -> None:
    try:
        from .campaigns import promote_due_campaigns

        promote_due_campaigns()
    except Exception:
        logger.exception("Scheduled message promotion failed")


def _delay_parts(delay_minutes: int) -> tuple[int, str]:
    minutes = max(0, int(delay_minutes or 0))
    if minutes and minutes % 1440 == 0:
        return minutes // 1440, "days"
    if minutes and minutes % 60 == 0:
        return minutes // 60, "hours"
    return minutes, "minutes"


def _timing_label(delay_minutes: int) -> str:
    from .constants import TRIGGER_DELAY_UNITS

    amount, unit = _delay_parts(delay_minutes)
    if not amount:
        return "Immediately"
    unit_label = dict(
        (value, label.rstrip("s") if amount == 1 else label)
        for value, label, _ in TRIGGER_DELAY_UNITS
    ).get(unit, unit)
    return f"After {amount} {unit_label}"


_LINK_TOKEN_LABELS = {
    "{account_link}": "Each customer receives their own credit account link.",
    "{receipt_link}": "Each customer receives a private link to this receipt.",
    "{quotation_link}": "Each customer receives a private link to this quotation.",
}


def triggered_message_as_dict(row) -> dict:
    amount, unit = _delay_parts(row.delay_minutes)
    link = (row.link or "").strip()
    return {
        "id": row.pk,
        "trigger": row.trigger,
        "trigger_label": row.get_trigger_display(),
        "delay_minutes": row.delay_minutes,
        "delay_amount": amount,
        "delay_unit": unit,
        "timing_label": _timing_label(row.delay_minutes),
        "body": row.body,
        "link": row.link,
        "link_is_account": link == "{account_link}",
        "link_label": _LINK_TOKEN_LABELS.get(link, ""),
        "channel": row.channel,
        "channel_label": row.get_channel_display(),
        "status": row.status,
        "status_label": row.get_status_display(),
        "is_active": row.status == MESSAGE_STATUS_ACTIVE,
    }


def _triggered_delay_minutes(data) -> int:
    from .constants import TRIGGER_DELAY_MAX_MINUTES, TRIGGER_DELAY_UNITS

    timing = (data.get("send_timing") or "now").strip()
    if timing != "later":
        return 0
    try:
        amount = int(data.get("delay_amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    unit = (data.get("delay_unit") or "minutes").strip()
    factor = {value: mult for value, _, mult in TRIGGER_DELAY_UNITS}.get(unit, 1)
    minutes = max(0, amount) * factor
    if minutes <= 0 or minutes > TRIGGER_DELAY_MAX_MINUTES:
        raise ValidationError("Enter a send delay between 1 minute and 30 days.")
    return minutes


def save_triggered_message(data, *, shop=None):
    """Create or update a TriggeredMessage from the settings form POST.

    ``shop=None`` edits company messages; a Shop edits only that shop's own.
    """
    from .constants import (
        MESSAGE_STATUS_ACTIVE,
        MESSAGE_STATUS_DRAFT,
        TRIGGER_MESSAGE_CHOICES,
    )
    from .models import TriggeredMessage

    scope_id = shop.pk if shop is not None else None
    draft = None
    raw_id = (data.get("draft_id") or "").strip()
    if raw_id:
        draft = TriggeredMessage.objects.filter(
            pk=raw_id, shop_id=scope_id
        ).first()
        if draft is None:
            raise ValidationError("Message not found.")

    trigger = (data.get("trigger") or "").strip()
    if trigger not in {value for value, _ in TRIGGER_MESSAGE_CHOICES}:
        raise ValidationError("Choose when this message should send.")

    body = (data.get("body") or "").strip()
    if not body:
        raise ValidationError("Write the message to send.")

    link = (data.get("link") or "").strip()[:500]
    if link and link.startswith("{"):
        pass  # {account_link}-style token — resolves per customer at send time
    elif link and not link.lower().startswith(("http://", "https://")):
        link = f"https://{link}"

    status = (data.get("status") or "").strip()
    if status not in {MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_ACTIVE}:
        status = MESSAGE_STATUS_DRAFT

    delay_minutes = _triggered_delay_minutes(data)

    channel = (data.get("channel") or "").strip()
    if channel and channel not in TRIGGER_CHANNEL_VALUES:
        raise ValidationError("Choose how this message should be sent.")

    if draft is None:
        draft = TriggeredMessage(trigger=trigger, shop=shop)
    draft.trigger = trigger
    draft.delay_minutes = delay_minutes
    draft.body = body
    draft.link = link
    if channel:
        draft.channel = channel
    draft.status = status
    draft.save()
    return draft


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

        result = send_whatsapp_message(phone=phone, text=body, skip_poll=True)
        if not result.get("ok"):
            err = str(result.get("error") or "")
            if "sandbox" in err.lower() or "63015" in err or "63016" in err:
                logger.info("Auto WhatsApp share skipped (sandbox): %s", err)
            else:
                logger.warning("Auto WhatsApp share failed: %s", err)
    except Exception:
        logger.exception("Auto WhatsApp share failed")
