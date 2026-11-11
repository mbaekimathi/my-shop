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
    CHANNEL_MESSAGE,
    CHANNEL_SMS,
    CHANNEL_WHATSAPP,
    MESSAGE_STATUS_ACTIVE,
    TRIGGER_CHANNEL_ALL,
    TRIGGER_CHANNEL_BOTH,
    TRIGGER_CHANNEL_VALUES,
    TRIGGER_CREDIT_FOLLOW_UP,
    TRIGGER_CREDIT_FOLLOW_UP_TODAY,
    TRIGGER_DELIVERY_COMPLETE,
    TRIGGER_DELIVERY_ON_WAY,
    TRIGGER_LABEL_PREFIX,
    TRIGGER_SUPPLIER_PAYMENT,
    TRIGGER_SUPPLY,
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


def _receipt_item_rows(receipt, *, empty_label: str = "- Items") -> str:
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
    return "\n".join(item_rows) if item_rows else empty_label


def _receipt_shop_name(receipt) -> str:
    shop_name = ""
    if getattr(receipt, "shop_id", None) and getattr(receipt, "shop", None):
        shop_name = (receipt.shop.name or "").strip()
    return shop_name or "our shop"


def build_credit_sale_notification(receipt, *, request=None) -> str:
    """Customer-facing notice after a credit sale from the shop cart."""
    shop_name = _receipt_shop_name(receipt)
    items_block = _receipt_item_rows(receipt, empty_label="- Items on credit")
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


def _receipt_balance_due(receipt) -> Decimal:
    due = (
        Decimal(getattr(receipt, "total", 0) or 0)
        - Decimal(getattr(receipt, "amount_paid", 0) or 0)
        - Decimal(getattr(receipt, "trade_exchange_value", 0) or 0)
    )
    if due < 0:
        return Decimal("0")
    return due


def _append_credit_pay_link(parts: list[str], receipt, *, request=None) -> None:
    pay_url = _credit_account_pay_url(receipt, request=request)
    if not pay_url:
        return
    parts.extend(
        [
            "",
            "You can view your credit account and pay with M-Pesa using this link:",
            pay_url,
        ]
    )


def _client_first_name(client) -> str:
    raw = (getattr(client, "full_name", "") or "").strip()
    if not raw:
        return "Customer"
    return raw.split()[0].title()


def _client_credit_account_url(client, *, request=None) -> str:
    client_id = getattr(client, "pk", None)
    if not client_id:
        return ""
    try:
        from shops.credit_note import client_credit_note_url

        url = (client_credit_note_url(int(client_id), request=request) or "").strip()
    except Exception:
        logger.exception("Credit account URL failed for client %s", client_id)
        return ""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return url


def _append_client_credit_pay_link(parts: list[str], client, *, request=None) -> None:
    pay_url = _client_credit_account_url(client, request=request)
    if not pay_url:
        return
    parts.extend(
        [
            "",
            "You can view your credit account and pay with M-Pesa using this link:",
            pay_url,
        ]
    )


def build_credit_follow_up_updated_notification(
    client, *, old_date=None, balance=None, request=None
) -> str:
    """Customer notice when an account follow-up date is set/changed."""
    new_label = ""
    if client.credit_follow_up_date:
        new_label = client.credit_follow_up_date.strftime("%d %b %Y")
    old_label = old_date.strftime("%d %b %Y") if old_date else ""
    parts = [
        f"Hi {_client_first_name(client)},",
        "",
        "A payment follow-up was scheduled for your credit account.",
        "",
        f"Amount due: {_kes(balance)}",
    ]
    if old_label and new_label and old_label != new_label:
        parts.append(f"Previous follow-up: {old_label}")
    if new_label:
        parts.append(f"Follow-up date: {new_label}")
    _append_client_credit_pay_link(parts, client, request=request)
    parts.extend(["", "Thank you."])
    return "\n".join(parts).strip()


def build_credit_follow_up_today_notification(
    client, *, balance=None, request=None
) -> str:
    """Customer notice on the morning of their account follow-up date."""
    follow_label = ""
    if client.credit_follow_up_date:
        follow_label = client.credit_follow_up_date.strftime("%d %b %Y")
    parts = [
        f"Hi {_client_first_name(client)},",
        "",
        "Friendly reminder — we are following up on your credit account today.",
        "",
        f"Amount due: {_kes(balance)}",
    ]
    if follow_label:
        parts.append(f"Follow-up date: {follow_label}")
    _append_client_credit_pay_link(parts, client, request=request)
    parts.extend(["", "Thank you."])
    return "\n".join(parts).strip()


# Backward-compatible aliases used by older tests/call sites.
build_credit_due_date_updated_notification = build_credit_follow_up_updated_notification
build_credit_due_today_notification = build_credit_follow_up_today_notification


def _client_open_credit_shop(client):
    """Shop from an open credit receipt — used for custom message draft scope."""
    from shops.models import ShopReceipt, ShopReceiptKind, ShopReceiptStatus

    receipt = (
        ShopReceipt.objects.filter(
            client_id=getattr(client, "pk", None),
            kind=ShopReceiptKind.CREDIT,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .select_related("shop")
        .order_by("-created_at")
        .first()
    )
    return getattr(receipt, "shop", None) if receipt is not None else None


def render_credit_account_trigger_message(
    template: str,
    client,
    *,
    balance=None,
    old_date=None,
    link: str = "",
    request=None,
    shop=None,
) -> str:
    """Expand {placeholders} for account-level credit follow-up drafts."""
    full_name = (getattr(client, "full_name", "") or "").strip()
    first = _client_first_name(client)
    shop_name = (getattr(shop, "name", "") or "").strip() or "our shop"
    follow = getattr(client, "credit_follow_up_date", None)
    follow_label = follow.strftime("%d %b %Y") if follow else ""
    previous_label = old_date.strftime("%d %b %Y") if old_date else ""
    replacements = {
        "{first_name}": first,
        "{full_name}": full_name or "Customer",
        "{shop_name}": shop_name,
        "{total}": _kes(balance),
        "{balance}": _kes(balance),
        "{receipt_number}": "",
        "{due_date}": follow_label,
        "{follow_up_date}": follow_label,
        "{previous_follow_up}": previous_label,
        "{account_link}": "",
        "{trade_link}": "",
        "{receipt_link}": "",
        "{quotation_link}": "",
        "{location}": "",
        "{rider_name}": "",
        "{receiver_name}": "",
    }
    body = template or ""
    url = (link or "").strip()
    if "{account_link}" in body or "{account_link}" in url:
        replacements["{account_link}"] = _client_credit_account_url(
            client, request=request
        )
    for key, value in replacements.items():
        body = body.replace(key, value)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    for key, value in replacements.items():
        url = url.replace(key, value)
    url = url.strip()
    if url and url not in body:
        body = f"{body}\n\n{url}" if body else url
    return body


def _queue_client_triggered_messages(
    client,
    *,
    trigger: str,
    balance=None,
    old_date=None,
    request=None,
    shop=None,
) -> list[int]:
    """Create triggered campaigns for a client-scoped credit event."""
    from .models import TriggeredMessage

    try:
        row = get_communications_settings()
        shop_obj = shop if shop is not None else _client_open_credit_shop(client)
        drafts = list(
            TriggeredMessage.objects.filter(
                trigger=trigger,
                status=MESSAGE_STATUS_ACTIVE,
                **_triggered_message_scope(shop_obj),
            )
        )
        if not drafts:
            return []
        phone = (getattr(client, "phone_number", "") or "").strip()
        email = (getattr(client, "email", "") or "").strip()
        if not phone and not email:
            return []
        due_now = []
        for draft in drafts:
            channels = [
                channel
                for channel in _trigger_channels(draft)
                if _triggered_channel_ready(row, channel)
            ]
            if CHANNEL_MESSAGE in channels and not email:
                channels = [c for c in channels if c != CHANNEL_MESSAGE]
            if CHANNEL_WHATSAPP in channels or CHANNEL_SMS in channels:
                if not phone:
                    channels = [
                        c
                        for c in channels
                        if c not in {CHANNEL_WHATSAPP, CHANNEL_SMS}
                    ]
            if not channels:
                continue
            body = render_credit_account_trigger_message(
                draft.body,
                client,
                balance=balance,
                old_date=old_date,
                link=draft.link,
                request=request,
                shop=shop_obj,
            )
            if not body:
                continue
            campaign_id = _queue_triggered_send(
                phone=phone or email,
                body=body,
                receipt=None,
                delay_minutes=draft.delay_minutes,
                label=f"{TRIGGER_LABEL_PREFIX}{draft.get_trigger_display()}",
                channels=channels,
                party_name=getattr(client, "full_name", "") or "",
                shop=shop_obj,
                email=email,
                client=client,
            )
            if campaign_id:
                due_now.append(campaign_id)
        return due_now
    except Exception:
        logger.exception(
            "Triggered messages failed for client %s trigger %s",
            getattr(client, "pk", "?"),
            trigger,
        )
        return []
    finally:
        _close_thread_connections()


def maybe_send_credit_follow_up_updated(
    client, *, old_date=None, balance=None, request=None
) -> dict:
    """Notify the client that their account follow-up date was set/changed."""
    row = get_communications_settings()
    if not row.enable_automations:
        return {"sent": False, "reason": "Automations are off."}
    if not row.auto_credit_due:
        return {
            "sent": False,
            "reason": "Turn on Credit due dates under Communications automations.",
        }
    if not _ready_customer_channels(row):
        return {
            "sent": False,
            "reason": "No ready send channel (WhatsApp, SMS, or email).",
        }
    phone = (getattr(client, "phone_number", "") or "").strip()
    email = (getattr(client, "email", "") or "").strip()
    if not phone and not email:
        logger.warning(
            "Credit follow-up update notice skipped for client %s: missing contact.",
            getattr(client, "pk", None),
        )
        return {
            "sent": False,
            "reason": "Client has no phone or email on file.",
        }

    shop = _client_open_credit_shop(client)
    if _deliverable_triggered_draft_exists_for_trigger(
        TRIGGER_CREDIT_FOLLOW_UP, shop, row
    ):
        campaign_ids = _queue_client_triggered_messages(
            client,
            trigger=TRIGGER_CREDIT_FOLLOW_UP,
            balance=balance,
            old_date=old_date,
            request=request,
            shop=shop,
        )
        if campaign_ids:
            thread = threading.Thread(
                target=_dispatch_queued_triggered,
                args=(campaign_ids,),
                daemon=True,
                name=f"comms-credit-follow-up-updated-{getattr(client, 'pk', 'new')}",
            )
            thread.start()
            return {"sent": True, "reason": ""}
        return {
            "sent": False,
            "reason": "Credit follow-up draft could not be queued.",
        }

    body = build_credit_follow_up_updated_notification(
        client, old_date=old_date, balance=balance, request=request
    )
    if not body:
        return {"sent": False, "reason": "Could not build follow-up notice."}
    _queue_customer_notice(
        phone=phone,
        email=email,
        body=body,
        subject="Credit follow-up scheduled",
        name=f"comms-credit-follow-up-updated-{getattr(client, 'pk', 'new')}",
    )
    return {"sent": True, "reason": ""}


maybe_send_credit_due_date_updated = maybe_send_credit_follow_up_updated


def send_credit_due_today_reminders(*, today=None) -> dict:
    """Send morning-of-follow-up notices for clients with open credit accounts."""
    from shops.models import Client, ShopReceipt, ShopReceiptKind, ShopReceiptStatus

    row = get_communications_settings()
    if not row.enable_automations or not row.auto_credit_due:
        return {"ok": True, "sent": 0, "skipped": 0, "reason": "disabled"}

    day = today or timezone.localdate()
    clients = (
        Client.objects.filter(credit_follow_up_date=day)
        .exclude(credit_follow_up_reminder_sent_on=day)
        .order_by("id")
    )

    sent = 0
    skipped = 0
    for client in clients.iterator(chunk_size=50):
        balance = Decimal("0")
        for receipt in ShopReceipt.objects.filter(
            client_id=client.pk,
            kind=ShopReceiptKind.CREDIT,
        ).exclude(status=ShopReceiptStatus.CANCELLED):
            due = _receipt_balance_due(receipt)
            if due > 0:
                balance += due
        if balance <= 0:
            skipped += 1
            continue
        phone = (client.phone_number or "").strip()
        email = (client.email or "").strip()
        if not phone and not email:
            skipped += 1
            logger.warning(
                "Credit follow-up-today notice skipped for client %s: missing contact.",
                client.pk,
            )
            continue

        shop = _client_open_credit_shop(client)
        queued = False
        if _deliverable_triggered_draft_exists_for_trigger(
            TRIGGER_CREDIT_FOLLOW_UP_TODAY, shop, row
        ):
            campaign_ids = _queue_client_triggered_messages(
                client,
                trigger=TRIGGER_CREDIT_FOLLOW_UP_TODAY,
                balance=balance,
                request=None,
                shop=shop,
            )
            if campaign_ids:
                thread = threading.Thread(
                    target=_dispatch_queued_triggered,
                    args=(campaign_ids,),
                    daemon=True,
                    name=f"comms-credit-follow-up-today-{client.pk}",
                )
                thread.start()
                queued = True
            else:
                skipped += 1
                continue
        else:
            body = build_credit_follow_up_today_notification(
                client, balance=balance
            )
            if not body:
                skipped += 1
                continue
            _queue_customer_notice(
                phone=phone,
                email=email,
                body=body,
                subject="Credit follow-up today",
                name=f"comms-credit-follow-up-today-{client.pk}",
            )
            queued = True

        if queued:
            client.credit_follow_up_reminder_sent_on = day
            client.save(
                update_fields=["credit_follow_up_reminder_sent_on", "updated_at"]
            )
            sent += 1
    return {"ok": True, "sent": sent, "skipped": skipped, "day": day.isoformat()}


def build_sale_notification(receipt, *, request=None) -> str:
    """Customer-facing notice after a cash/M-Pesa sale — same style as credit."""
    shop_name = _receipt_shop_name(receipt)
    items_block = _receipt_item_rows(receipt, empty_label="- Items purchased")
    parts = [
        f"Hi {_first_name(receipt)},",
        "",
        f"Sale at {shop_name}.",
        "",
        items_block,
        "",
        f"Total paid: {_kes(receipt.total)}",
    ]
    receipt_no = (receipt.receipt_number or "").strip()
    if receipt_no:
        parts.extend(["", f"Receipt {receipt_no}"])
    receipt_url = _receipt_doc_url(receipt, quotation=False, request=request)
    if receipt_url:
        parts.extend(["", "View your receipt:", receipt_url])
    parts.extend(["", "Thank you."])
    return "\n".join(parts).strip()


def _delivery_rider_name(delivery) -> str:
    rider = getattr(delivery, "accepted_by", None)
    if rider is None:
        return ""
    user = getattr(rider, "user", None)
    return (
        (user.get_full_name() if user is not None else "") or ""
    ).strip() or (getattr(user, "username", "") or "").strip()


def build_delivery_on_way_notification(delivery, *, request=None) -> str:
    """Customer notice when a rider accepts and is on the way."""
    receipt = getattr(delivery, "receipt", None)
    shop_name = _receipt_shop_name(receipt) if receipt is not None else "our shop"
    first = _first_name(receipt) if receipt is not None else "Customer"
    location = (getattr(delivery, "location_text", "") or "").strip() or "your location"
    rider_name = _delivery_rider_name(delivery)
    parts = [
        f"Hi {first},",
        "",
        f"Good news — your delivery from {shop_name} is on the way.",
        "",
        f"Drop-off: {location}",
    ]
    if rider_name:
        parts.append(f"Rider: {rider_name}")
    if receipt is not None:
        receipt_no = (receipt.receipt_number or "").strip()
        if receipt_no:
            parts.extend(["", f"Receipt {receipt_no}"])
    parts.extend(["", "Thank you."])
    return "\n".join(parts).strip()


def _is_unpaid_credit_delivery(delivery) -> bool:
    from shops.models import ShopDeliveryStatus, ShopReceiptKind

    receipt = getattr(delivery, "receipt", None)
    if receipt is None:
        return False
    return (
        receipt.kind == ShopReceiptKind.CREDIT
        and getattr(delivery, "status", "") == ShopDeliveryStatus.DELIVERED
    )


def _append_credit_note_link(body: str, delivery, *, request=None) -> str:
    """Add the credit-note pay link for unpaid credit drop-offs (one link only)."""
    if not _is_unpaid_credit_delivery(delivery):
        return (body or "").strip()
    receipt = getattr(delivery, "receipt", None)
    pay_url = _credit_account_pay_url(receipt, request=request)
    text = (body or "").strip()
    if not pay_url or pay_url in text:
        return text
    return (
        f"{text}\n\n"
        "Payment still outstanding. Check your balance and pay anytime here:\n"
        f"{pay_url}"
    ).strip()


def build_delivery_complete_notification(delivery, *, request=None) -> str:
    """Customer notice when a rider marks the delivery as delivered."""
    from shops.models import ShopDeliveryStatus, ShopReceiptKind

    receipt = getattr(delivery, "receipt", None)
    shop_name = _receipt_shop_name(receipt) if receipt is not None else "our shop"
    first = _first_name(receipt) if receipt is not None else "Customer"
    location = (getattr(delivery, "location_text", "") or "").strip() or "your location"
    receiver = (getattr(delivery, "receiver_name", "") or "").strip()
    rider_name = _delivery_rider_name(delivery)
    parts = [
        f"Hi {first},",
        "",
        f"Your delivery from {shop_name} has been completed.",
        "",
        f"Drop-off: {location}",
    ]
    if receiver:
        parts.append(f"Received by: {receiver}")
    if rider_name:
        parts.append(f"Rider: {rider_name}")
    if (
        receipt is not None
        and receipt.kind == ShopReceiptKind.CREDIT
        and getattr(delivery, "status", "") == ShopDeliveryStatus.DELIVERED_PAID
    ):
        ref = (getattr(delivery, "payment_reference", "") or "").strip()
        if ref:
            parts.extend(["", f"Payment reference: {ref}"])
        else:
            parts.extend(["", "Payment collected on delivery."])
    elif _is_unpaid_credit_delivery(delivery):
        parts.extend(["", "Goods delivered — payment still outstanding."])
    if receipt is not None:
        receipt_no = (receipt.receipt_number or "").strip()
        if receipt_no:
            parts.extend(["", f"Receipt {receipt_no}"])
    parts.extend(["", "Thank you."])
    return _append_credit_note_link("\n".join(parts), delivery, request=request)


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
    """Send a sale/quotation/credit notice on enabled channels when automations are on."""
    row = get_communications_settings()
    if not row.enable_automations:
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
    email = ""
    if receipt.client_id:
        email = (getattr(receipt.client, "email", "") or "").strip()
    if kind == ShopReceiptKind.CREDIT:
        body = build_credit_sale_notification(receipt, request=request)
        subject = "Credit sale notice"
    elif kind == ShopReceiptKind.SALE:
        body = build_sale_notification(receipt, request=request)
        subject = "Sale receipt"
    else:
        body = (message or "").strip()
        subject = "Quotation"
    if not body:
        return
    if not phone and not email:
        if kind in {ShopReceiptKind.CREDIT, ShopReceiptKind.SALE}:
            logger.warning(
                "%s notice skipped for receipt %s: no client phone or email.",
                kind.title(),
                receipt.pk,
            )
        return
    _queue_customer_notice(
        phone=phone,
        email=email,
        body=body,
        subject=subject,
        name=f"comms-auto-receipt-{receipt.pk}",
    )


def maybe_send_delivery_on_way(delivery, *, request=None) -> None:
    """Notify the customer that their rider is on the way (SMS / WhatsApp / email)."""
    row = get_communications_settings()
    if not row.enable_automations or not row.auto_delivery_on_way:
        return
    receipt = getattr(delivery, "receipt", None)
    if receipt is None:
        return
    if _deliverable_triggered_draft_exists_for_trigger(
        TRIGGER_DELIVERY_ON_WAY, getattr(receipt, "shop", None), row
    ):
        return
    phone = (receipt.client_phone or "").strip()
    if not phone and receipt.client_id:
        phone = (getattr(receipt.client, "phone_number", "") or "").strip()
    email = ""
    if receipt.client_id:
        email = (getattr(receipt.client, "email", "") or "").strip()
    body = build_delivery_on_way_notification(delivery, request=request)
    if not body or (not phone and not email):
        logger.warning(
            "Delivery on-way notice skipped for delivery %s: missing contact.",
            getattr(delivery, "pk", None),
        )
        return
    _queue_customer_notice(
        phone=phone,
        email=email,
        body=body,
        subject="Your delivery is on the way",
        name=f"comms-delivery-on-way-{getattr(delivery, 'pk', 'new')}",
    )


def maybe_send_delivery_complete(delivery, *, request=None) -> None:
    """Notify the customer that their delivery was completed (SMS / WhatsApp / email)."""
    row = get_communications_settings()
    if not row.enable_automations or not row.auto_delivery_complete:
        return
    receipt = getattr(delivery, "receipt", None)
    if receipt is None:
        return
    if _deliverable_triggered_draft_exists_for_trigger(
        TRIGGER_DELIVERY_COMPLETE, getattr(receipt, "shop", None), row
    ):
        return
    phone = (receipt.client_phone or "").strip()
    if not phone and receipt.client_id:
        phone = (getattr(receipt.client, "phone_number", "") or "").strip()
    email = ""
    if receipt.client_id:
        email = (getattr(receipt.client, "email", "") or "").strip()
    body = build_delivery_complete_notification(delivery, request=request)
    if not body or (not phone and not email):
        logger.warning(
            "Delivery complete notice skipped for delivery %s: missing contact.",
            getattr(delivery, "pk", None),
        )
        return
    _queue_customer_notice(
        phone=phone,
        email=email,
        body=body,
        subject="Your delivery is complete",
        name=f"comms-delivery-complete-{getattr(delivery, 'pk', 'new')}",
    )

def credit_whatsapp_required() -> bool:
    row = get_communications_settings()
    return bool(
        row.enable_automations
        and row.auto_payment_reminder
        and row.has_twilio_credentials()
    )


def _deliverable_triggered_draft_exists(receipt, row) -> bool:
    """An active draft for this receipt kind with at least one ready channel."""
    return _deliverable_triggered_draft_exists_for_trigger(
        receipt.kind, getattr(receipt, "shop", None), row
    )


def _deliverable_triggered_draft_exists_for_trigger(trigger, shop, row) -> bool:
    """True when an active draft for ``trigger`` can send on a ready channel."""
    from .models import TriggeredMessage

    try:
        drafts = TriggeredMessage.objects.filter(
            trigger=trigger,
            status=MESSAGE_STATUS_ACTIVE,
            **_triggered_message_scope(shop),
        )
        for draft in drafts:
            if any(
                _triggered_channel_ready(row, channel)
                for channel in _trigger_channels(draft)
            ):
                return True
    except Exception:
        logger.exception(
            "Triggered draft check failed for trigger %s",
            trigger,
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
    payment = _movement_payment_line(movement, total=total)
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


def _movement_payment_line(movement, *, total) -> str:
    """Paid vs unpaid wording for a stock movement, with the balance due."""
    from items.models import StockPaymentStatus

    status = (getattr(movement, "payment_status", "") or "").strip()
    if not status:
        statuses = {
            (line.payment_status or "").strip() for line in movement.lines.all()
        }
        statuses.discard("")
        if statuses == {StockPaymentStatus.PAID}:
            status = StockPaymentStatus.PAID
        elif statuses and StockPaymentStatus.PAID in statuses:
            status = StockPaymentStatus.PARTIAL
        elif statuses:
            status = next(iter(statuses))
    paid = Decimal(getattr(movement, "amount_paid", 0) or 0)
    due = Decimal(total or 0) - paid
    if due < 0:
        due = Decimal("0")
    if status == StockPaymentStatus.PAID or due <= 0:
        return "Payment: Paid in full. Thank you."
    if status == StockPaymentStatus.PARTIAL or paid > 0:
        return (
            f"Payment: Part paid — balance {_kes(due)}. "
            "We'll confirm once it's settled."
        )
    return (
        f"Payment: Unpaid — balance {_kes(due)}. "
        "We'll confirm once it's settled."
    )


def _supplier_notice_channels(row) -> list[str]:
    """Delivery channels ready for supplier notices (WhatsApp and/or SMS)."""
    return [
        channel
        for channel in (CHANNEL_WHATSAPP, CHANNEL_SMS)
        if _triggered_channel_ready(row, channel)
    ]


def _queue_supplier_notice(
    phone: str, body: str, *, label: str, party_name: str = "", shop=None
) -> None:
    """Persist a supplier notice campaign, then deliver it off the request path.

    Rows are created inline so a worker recycle can never lose the send;
    only provider delivery runs on a daemon thread and the cron queue
    worker finishes anything left queued.
    """
    row = get_communications_settings()
    channels = _supplier_notice_channels(row)
    if not channels:
        return
    campaign_id = _queue_triggered_send(
        phone=phone,
        body=body,
        receipt=None,
        delay_minutes=0,
        label=label,
        channels=channels,
        party_name=party_name,
        shop=shop,
    )
    if not campaign_id:
        return
    thread = threading.Thread(
        target=_dispatch_queued_triggered,
        args=([campaign_id],),
        daemon=True,
        name="comms-supplier-notice",
    )
    thread.start()


def _supplier_trigger_drafts(trigger: str, *, shop=None):
    """Active supplier-facing drafts for a trigger, scoped like receipt drafts."""
    from .models import TriggeredMessage

    return list(
        TriggeredMessage.objects.filter(
            trigger=trigger,
            status=MESSAGE_STATUS_ACTIVE,
            **_triggered_message_scope(shop),
        )
    )


def _queue_supplier_drafts(
    *, phone, party_name, drafts, row, render_data, shop=None
) -> list[int]:
    """Queue supplier drafts via the durable campaign path; return due-now pks."""
    due_now = []
    for draft in drafts:
        channels = [
            channel
            for channel in _trigger_channels(draft)
            if _triggered_channel_ready(row, channel)
        ]
        if not channels:
            continue
        body = render_supplier_trigger_message(
            draft.body, link=draft.link, **render_data
        )
        if not body:
            continue
        campaign_id = _queue_triggered_send(
            phone=phone,
            body=body,
            receipt=None,
            delay_minutes=draft.delay_minutes,
            label=f"{TRIGGER_LABEL_PREFIX}{draft.get_trigger_display()}",
            channels=channels,
            party_name=party_name,
            shop=shop,
        )
        if campaign_id:
            due_now.append(campaign_id)
    return due_now


def _dispatch_supplier_campaigns(campaign_ids, *, name: str) -> None:
    if not campaign_ids:
        return
    thread = threading.Thread(
        target=_dispatch_queued_triggered,
        args=(campaign_ids,),
        daemon=True,
        name=name,
    )
    thread.start()


def _movement_supply_total(movement) -> Decimal:
    total = Decimal("0")
    for line in movement.lines.all():
        total += Decimal(line.buying_price or 0) * Decimal(line.quantity or 0)
    return total.quantize(Decimal("0.01"))


def _movement_doc_ref(movement) -> str:
    from shops.services import DOC_NUMBER_PREFIX, format_simple_doc_number

    return format_simple_doc_number(DOC_NUMBER_PREFIX["stock_in"], movement.pk)


def maybe_send_stock_supplier_notice(movement, *, shop=None) -> None:
    """Message the supplier after Buy stock when that automation is on.

    An active "Supply received" draft supersedes the built-in message —
    same as receipt drafts superseding the legacy receipt share.
    """
    from items.models import StockEntrySource, StockMovementType

    row = get_communications_settings()
    if not row.enable_automations:
        return
    if getattr(movement, "movement_type", "") != StockMovementType.IN:
        return
    if getattr(movement, "entry_source", "") not in {
        StockEntrySource.BUY_ITEMS,
        StockEntrySource.STOCK_MANAGEMENT,
    }:
        return
    phone = _stock_supplier_phone(movement)
    if not phone:
        logger.warning(
            "Buy stock supplier notice skipped for movement %s: no supplier phone.",
            getattr(movement, "pk", None),
        )
        return
    first = movement.lines.first()
    supplier_name = (getattr(first, "supplier_name", "") or "").strip()
    shop_obj = shop or getattr(movement, "shop", None)
    drafts = _supplier_trigger_drafts(TRIGGER_SUPPLY, shop=shop_obj)
    if drafts:
        total = _movement_supply_total(movement)
        due = total - Decimal(getattr(movement, "amount_paid", 0) or 0)
        due_now = _queue_supplier_drafts(
            phone=phone,
            party_name=supplier_name,
            drafts=drafts,
            row=row,
            render_data={
                "supplier_name": supplier_name,
                "total": total,
                "balance": max(due, Decimal("0")),
                "receipt_ref": _movement_doc_ref(movement),
                "shop_name": getattr(shop_obj, "name", ""),
            },
            shop=shop_obj,
        )
        _dispatch_supplier_campaigns(
            due_now, name=f"comms-supplier-supply-{movement.pk}"
        )
        return
    if not row.auto_stock_supplier:
        return
    body = build_stock_supplier_notification(movement, shop=shop)
    if not body:
        return
    _queue_supplier_notice(
        phone,
        body,
        label="Supplier notice · Buy stock",
        party_name=supplier_name,
        shop=shop_obj,
    )


def maybe_send_expense_supplier_notice(expense=None, *, expenses=None, shop=None) -> None:
    """Message the supplier after Register expense when that automation is on."""
    row = get_communications_settings()
    if not row.enable_automations or not row.auto_expense_supplier:
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
                "Expense supplier notice skipped for expense %s: no supplier phone.",
                getattr(first, "pk", None),
            )
        return
    _queue_supplier_notice(
        phone,
        body,
        label="Supplier notice · Expense",
        party_name=(getattr(first, "supplier_name", "") or "").strip(),
        shop=shop or getattr(first, "shop", None),
    )


def build_supplier_payment_notification(
    supplier_name: str, *, amount, balance_after, receipt_ref: str = ""
) -> str:
    """Supplier-facing confirmation when a payment is applied to their account."""
    name = _party_first_name(supplier_name, fallback="there")
    parts = [
        f"Hi {name},",
        "",
        f"We paid {_kes(amount)} for your supplies.",
    ]
    ref = (receipt_ref or "").strip()
    if ref:
        parts.append(f"Ref {ref}")
    remaining = Decimal(balance_after or 0)
    if remaining > 0:
        parts.append(f"Balance left: {_kes(remaining)}")
    else:
        parts.append("Your account is now fully settled.")
    parts.extend(["", "Thank you."])
    return "\n".join(parts).strip()


def maybe_send_supplier_payment_notice(
    supplier,
    *,
    kind: str,
    amount,
    balance_after,
    receipt_ref: str = "",
    shop=None,
) -> None:
    """Message a supplier when a payment is applied to their supplies/expenses.

    An active "Supplier paid" draft supersedes the built-in message.
    """
    row = get_communications_settings()
    if not row.enable_automations:
        return
    phone = _supplier_whatsapp_phone(
        dial=getattr(supplier, "phone_country_code", ""),
        national=getattr(supplier, "phone_number", ""),
    )
    if not phone:
        logger.warning(
            "Supplier payment notice skipped for %s: no supplier phone.",
            getattr(supplier, "pk", "?"),
        )
        return
    supplier_name = (getattr(supplier, "name", "") or "").strip()
    drafts = _supplier_trigger_drafts(TRIGGER_SUPPLIER_PAYMENT, shop=shop)
    if drafts:
        due_now = _queue_supplier_drafts(
            phone=phone,
            party_name=supplier_name,
            drafts=drafts,
            row=row,
            render_data={
                "supplier_name": supplier_name,
                "total": amount,
                "balance": balance_after,
                "receipt_ref": receipt_ref,
                "shop_name": getattr(shop, "name", ""),
            },
            shop=shop,
        )
        _dispatch_supplier_campaigns(
            due_now, name=f"comms-supplier-pay-{getattr(supplier, 'pk', 'x')}"
        )
        return
    enabled = (
        row.auto_expense_supplier if kind == "expense" else row.auto_stock_supplier
    )
    if not enabled:
        return
    body = build_supplier_payment_notification(
        supplier_name,
        amount=amount,
        balance_after=balance_after,
        receipt_ref=receipt_ref,
    )
    if not body:
        return
    _queue_supplier_notice(
        phone,
        body,
        label="Supplier notice · Payment",
        party_name=supplier_name,
        shop=shop,
    )


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


def _trade_account_url(receipt, *, request=None) -> str:
    """Signed public URL for the receipt client's trade account, or "" if none."""
    client_id = getattr(receipt, "client_id", None)
    if not client_id:
        return ""
    try:
        from shops.trade_link import client_trade_url

        url = (client_trade_url(int(client_id), request=request) or "").strip()
    except Exception:
        logger.exception(
            "Trade account URL failed for receipt %s", getattr(receipt, "pk", None)
        )
        return ""
    if not url.lower().startswith(("http://", "https://")):
        logger.warning(
            "Trade account URL is not clickable for receipt %s.",
            getattr(receipt, "pk", None),
        )
        return ""
    return url


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
    template: str, receipt, *, link: str = "", request=None, delivery=None
) -> str:
    """Expand {placeholders} for a triggered message — plain str.replace only."""
    client_name = (getattr(receipt, "client_name", "") or "").strip()
    first = client_name.split()[0].title() if client_name else "Customer"
    shop_name = (
        getattr(getattr(receipt, "shop", None), "name", "") or ""
    ).strip() or "our shop"
    due_date = getattr(receipt, "credit_due_date", None)
    due = (
        Decimal(getattr(receipt, "total", 0) or 0)
        - Decimal(getattr(receipt, "amount_paid", 0) or 0)
        - Decimal(getattr(receipt, "trade_exchange_value", 0) or 0)
    )
    if due < 0:
        due = Decimal("0")
    location = ""
    rider_name = ""
    receiver_name = ""
    if delivery is not None:
        location = (getattr(delivery, "location_text", "") or "").strip()
        receiver_name = (getattr(delivery, "receiver_name", "") or "").strip()
        rider_name = _delivery_rider_name(delivery)
    replacements = {
        "{first_name}": first,
        "{full_name}": client_name or "Customer",
        "{shop_name}": shop_name,
        "{total}": _kes(getattr(receipt, "total", None)),
        "{balance}": _kes(due),
        "{receipt_number}": (getattr(receipt, "receipt_number", "") or "").strip(),
        "{due_date}": due_date.strftime("%d %b %Y") if due_date else "",
        "{account_link}": "",
        "{trade_link}": "",
        "{receipt_link}": "",
        "{quotation_link}": "",
        "{location}": location,
        "{rider_name}": rider_name,
        "{receiver_name}": receiver_name,
    }
    body = template or ""
    url = (link or "").strip()
    if "{account_link}" in body or "{account_link}" in url:
        replacements["{account_link}"] = _credit_account_pay_url(
            receipt, request=request
        )
    if "{trade_link}" in body or "{trade_link}" in url:
        replacements["{trade_link}"] = _trade_account_url(
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
    # One link only — skip append when the body already has this URL
    # (e.g. body used {receipt_link} and the Link field is also {receipt_link}).
    if url and url not in body:
        body = f"{body}\n\n{url}" if body else url
    return body


def render_supplier_trigger_message(
    template: str,
    *,
    supplier_name: str = "",
    total=None,
    balance=None,
    receipt_ref: str = "",
    shop_name: str = "",
    link: str = "",
) -> str:
    """Expand {placeholders} for a supplier-facing triggered message."""
    name = (supplier_name or "").strip()
    replacements = {
        "{first_name}": name.split()[0].title() if name else "there",
        "{full_name}": name or "there",
        "{shop_name}": (shop_name or "").strip() or "our shop",
        "{total}": _kes(total),
        "{balance}": _kes(balance),
        "{receipt_number}": (receipt_ref or "").strip(),
        "{due_date}": "",
        "{account_link}": "",
        "{trade_link}": "",
        "{receipt_link}": "",
        "{quotation_link}": "",
    }
    body = template or ""
    url = (link or "").strip()
    for key, value in replacements.items():
        body = body.replace(key, value)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    for key, value in replacements.items():
        url = url.replace(key, value)
    url = url.strip()
    if url and url not in body:
        body = f"{body}\n\n{url}" if body else url
    return body


def _message_channel_ready(row) -> bool:
    return bool(row.enable_message and (row.message_from_name or "").strip())


def _triggered_channel_ready(row, channel: str) -> bool:
    """Whether the company enabled this channel and saved working credentials."""
    if channel == CHANNEL_SMS:
        return bool(row.enable_sms and row.has_sms_credentials())
    if channel == CHANNEL_MESSAGE:
        return _message_channel_ready(row)
    return bool(row.enable_whatsapp and row.has_twilio_credentials())


def _trigger_channels(draft) -> list[str]:
    """Outbound channels for a draft; unknown values fall back to WhatsApp."""
    value = (getattr(draft, "channel", "") or "").strip()
    if value == TRIGGER_CHANNEL_ALL:
        return [CHANNEL_WHATSAPP, CHANNEL_SMS, CHANNEL_MESSAGE]
    if value == TRIGGER_CHANNEL_BOTH:
        return [CHANNEL_WHATSAPP, CHANNEL_SMS]
    if value in {CHANNEL_WHATSAPP, CHANNEL_SMS, CHANNEL_MESSAGE}:
        return [value]
    return [CHANNEL_WHATSAPP]


def _ready_customer_channels(row) -> list[str]:
    """Channels for automatic customer notices (sale / credit / delivery).

    WhatsApp follows the legacy credit rule: Twilio credentials are enough.
    SMS and email still require their channel toggles plus credentials.
    """
    channels = []
    if row.has_twilio_credentials():
        channels.append(CHANNEL_WHATSAPP)
    if row.enable_sms and row.has_sms_credentials():
        channels.append(CHANNEL_SMS)
    if _message_channel_ready(row):
        channels.append(CHANNEL_MESSAGE)
    return channels


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
    if not _ready_customer_channels(row):
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


def maybe_send_delivery_triggered_messages(delivery, *, request=None) -> None:
    """Queue active 'delivery on the way' drafts when a rider accepts."""
    receipt = getattr(delivery, "receipt", None)
    if receipt is None:
        return
    row = get_communications_settings()
    if not row.enable_automations:
        return
    if not _ready_customer_channels(row):
        return
    campaign_ids = _queue_triggered_messages_for_trigger(
        receipt,
        request,
        trigger=TRIGGER_DELIVERY_ON_WAY,
        delivery=delivery,
    )
    if not campaign_ids:
        return
    thread = threading.Thread(
        target=_dispatch_queued_triggered,
        args=(campaign_ids,),
        daemon=True,
        name=f"comms-delivery-triggered-{getattr(delivery, 'pk', 'new')}",
    )
    thread.start()


def maybe_send_delivery_complete_triggered_messages(delivery, *, request=None) -> None:
    """Queue active 'delivery completed' drafts when a rider marks delivered."""
    receipt = getattr(delivery, "receipt", None)
    if receipt is None:
        return
    row = get_communications_settings()
    if not row.enable_automations:
        return
    if not _ready_customer_channels(row):
        return
    campaign_ids = _queue_triggered_messages_for_trigger(
        receipt,
        request,
        trigger=TRIGGER_DELIVERY_COMPLETE,
        delivery=delivery,
    )
    if not campaign_ids:
        return
    thread = threading.Thread(
        target=_dispatch_queued_triggered,
        args=(campaign_ids,),
        daemon=True,
        name=f"comms-delivery-complete-triggered-{getattr(delivery, 'pk', 'new')}",
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
    return _queue_triggered_messages_for_trigger(
        receipt, request, trigger=receipt.kind
    )


def _queue_triggered_messages_for_trigger(
    receipt, request, *, trigger: str, delivery=None
) -> list[int]:
    """Create triggered campaigns for an explicit trigger value."""
    from .models import TriggeredMessage

    try:
        row = get_communications_settings()
        drafts = list(
            TriggeredMessage.objects.filter(
                trigger=trigger,
                status=MESSAGE_STATUS_ACTIVE,
                **_triggered_message_scope(getattr(receipt, "shop", None)),
            )
        )
        if not drafts:
            return []
        phone = (receipt.client_phone or "").strip()
        if not phone and receipt.client_id:
            phone = (getattr(receipt.client, "phone_number", "") or "").strip()
        email = ""
        if receipt.client_id:
            email = (getattr(receipt.client, "email", "") or "").strip()
        if not phone and not email:
            return []
        due_now = []
        for draft in drafts:
            channels = [
                channel
                for channel in _trigger_channels(draft)
                if _triggered_channel_ready(row, channel)
            ]
            if CHANNEL_MESSAGE in channels and not email:
                channels = [c for c in channels if c != CHANNEL_MESSAGE]
            if CHANNEL_WHATSAPP in channels or CHANNEL_SMS in channels:
                if not phone:
                    channels = [
                        c
                        for c in channels
                        if c not in {CHANNEL_WHATSAPP, CHANNEL_SMS}
                    ]
            if not channels:
                continue
            body = render_trigger_message(
                draft.body,
                receipt,
                link=draft.link,
                request=request,
                delivery=delivery,
            )
            if trigger == TRIGGER_DELIVERY_COMPLETE and delivery is not None:
                body = _append_credit_note_link(body, delivery, request=request)
            if not body:
                continue
            campaign_id = _queue_triggered_send(
                phone=phone or email,
                body=body,
                receipt=receipt,
                delay_minutes=draft.delay_minutes,
                label=f"{TRIGGER_LABEL_PREFIX}{draft.get_trigger_display()}",
                channels=channels,
                email=email,
            )
            if campaign_id:
                due_now.append(campaign_id)
        return due_now
    except Exception:
        logger.exception(
            "Triggered messages failed for receipt %s trigger %s",
            getattr(receipt, "pk", "?"),
            trigger,
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


def _ensure_company_drafts(defaults, *, active: bool = True) -> int:
    """Create missing company TriggeredMessage rows from ``(trigger, body, link)``."""
    from .constants import (
        MESSAGE_STATUS_ACTIVE,
        MESSAGE_STATUS_DRAFT,
        TRIGGER_CHANNEL_SMS,
    )
    from .models import TriggeredMessage

    created = 0
    status = MESSAGE_STATUS_ACTIVE if active else MESSAGE_STATUS_DRAFT
    for trigger, body, link in defaults:
        exists = TriggeredMessage.objects.filter(
            shop__isnull=True, trigger=trigger
        ).exists()
        if exists:
            continue
        TriggeredMessage.objects.create(
            shop=None,
            trigger=trigger,
            delay_minutes=0,
            body=body,
            link=(link or "").strip(),
            channel=TRIGGER_CHANNEL_SMS,
            status=status,
        )
        created += 1
    return created


def ensure_company_delivery_drafts(*, active: bool = True) -> int:
    """Create missing company Delivery on-the-way / completed message drafts."""
    from .constants import DEFAULT_DELIVERY_DRAFTS

    return _ensure_company_drafts(DEFAULT_DELIVERY_DRAFTS, active=active)


def ensure_company_credit_drafts(*, active: bool = True) -> int:
    """Create missing company Credit sale / follow-up drafts (with account link)."""
    from .constants import DEFAULT_CREDIT_DRAFTS

    return _ensure_company_drafts(DEFAULT_CREDIT_DRAFTS, active=active)


def _queue_triggered_send(
    *,
    phone: str,
    body: str,
    receipt,
    delay_minutes: int,
    label: str,
    channels: list[str],
    party_name: str = "",
    shop=None,
    email: str = "",
    client=None,
) -> int | None:
    """Persist a one-recipient campaign; return its pk when it is due to send now."""
    from datetime import timedelta

    from .constants import CAMPAIGN_DRAFT, CAMPAIGN_QUEUED, MSG_PENDING
    from .models import BroadcastCampaign, OutboundMessage

    delay = max(0, int(delay_minutes or 0))
    due_at = timezone.now() + timedelta(minutes=delay)
    client_obj = client if client is not None else getattr(receipt, "client", None)
    campaign = BroadcastCampaign.objects.create(
        created_by=getattr(receipt, "created_by", None) if receipt is not None else None,
        body_template=body,
        filters={
            "triggered_message": True,
            "trigger_label": label,
            "send_after": due_at.isoformat(),
            "receipt_id": getattr(receipt, "pk", None) if receipt is not None else None,
            "client_id": getattr(client_obj, "pk", None),
            "shop_id": (
                getattr(receipt, "shop_id", None)
                if receipt is not None
                else None
            )
            or getattr(shop, "pk", None),
            "email": (email or "").strip(),
        },
        status=CAMPAIGN_QUEUED if delay <= 0 else CAMPAIGN_DRAFT,
        recipient_count=1,
    )
    recipient_name = (party_name or "").strip() or (
        getattr(receipt, "client_name", "") if receipt is not None else ""
    ).strip() or (getattr(client_obj, "full_name", "") or "").strip()
    email_addr = (email or "").strip()
    rows = []
    for channel in channels:
        dest = email_addr if channel == CHANNEL_MESSAGE and email_addr else phone
        if not dest:
            continue
        rows.append(
            OutboundMessage(
                campaign=campaign,
                channel=channel,
                client=client_obj if getattr(client_obj, "pk", None) else None,
                client_name=recipient_name,
                phone=dest,
                body=body,
                status=MSG_PENDING,
            )
        )
    if not rows:
        campaign.delete()
        return None
    OutboundMessage.objects.bulk_create(rows)
    campaign.recipient_count = len(rows)
    campaign.save(update_fields=["recipient_count"])
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
    "{trade_link}": "Each customer receives their own trade account link.",
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
    """Legacy WhatsApp-only helper — prefer ``_queue_customer_notice``."""
    _queue_customer_notice(phone=phone, email="", body=body, name=name)


def _queue_customer_notice(
    *,
    phone: str,
    body: str,
    name: str,
    email: str = "",
    subject: str = "",
) -> None:
    """Fan out a customer notice across every ready WhatsApp / SMS / email channel."""
    row = get_communications_settings()
    channels = _ready_customer_channels(row)
    if not channels:
        return
    phone = (phone or "").strip()
    email = (email or "").strip()
    jobs = []
    for channel in channels:
        if channel in {CHANNEL_WHATSAPP, CHANNEL_SMS} and not phone:
            continue
        if channel == CHANNEL_MESSAGE and not email:
            continue
        jobs.append(channel)
    if not jobs:
        return
    thread = threading.Thread(
        target=_send_customer_notice_quietly,
        args=(phone, email, body, jobs, subject),
        daemon=True,
        name=name,
    )
    thread.start()


def _send_customer_notice_quietly(
    phone: str, email: str, body: str, channels: list[str], subject: str
) -> None:
    row = get_communications_settings()
    for channel in channels:
        try:
            if channel == CHANNEL_SMS:
                from .sms import send_sms

                result = send_sms(phone=phone, message=body)
            elif channel == CHANNEL_MESSAGE:
                from .email_send import send_customer_email

                result = send_customer_email(
                    to_email=email,
                    body=body,
                    subject=subject or "Order update",
                    from_name=row.message_from_name or "",
                    reply_to=row.message_reply_to or "",
                )
            else:
                from .twilio import send_whatsapp_message

                result = send_whatsapp_message(phone=phone, text=body, skip_poll=True)
            if not result.get("ok"):
                err = str(result.get("error") or "")
                if channel == CHANNEL_WHATSAPP and (
                    "sandbox" in err.lower() or "63015" in err or "63016" in err
                ):
                    logger.info("Auto WhatsApp share skipped (sandbox): %s", err)
                else:
                    logger.warning("Auto %s notice failed: %s", channel, err)
        except Exception:
            logger.exception("Auto %s notice failed", channel)
