"""Inbound WhatsApp replies stored from Twilio (webhook or inbox-open sync)."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shops.models import Client
from shops.services import _normalize_phone

from .constants import MSG_SENT
from .ephemeral import bust_outbound_index
from .models import InboundReply, OutboundMessage


def _parse_created_at(raw) -> datetime:
    if isinstance(raw, datetime):
        if timezone.is_naive(raw):
            return timezone.make_aware(raw, dt_timezone.utc)
        return raw
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1_000_000_000_000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    text = str(raw or "").strip()
    if text:
        parsed = parse_datetime(text)
        if parsed is not None:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, dt_timezone.utc)
            return parsed
        try:
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(text)
            if parsed is not None:
                if timezone.is_naive(parsed):
                    return timezone.make_aware(parsed, dt_timezone.utc)
                return parsed
        except (TypeError, ValueError, OverflowError):
            pass
    return timezone.now()


def _client_for_phone(phone: str):
    if not phone:
        return None
    digits = _normalize_phone(phone) or "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return None
    return (
        Client.objects.filter(
            Q(phone_normalized=digits) | Q(phone_number__icontains=digits[-9:])
        )
        .order_by("id")
        .first()
    )


def _destination_keys(phone: str = "", chat_id: str = "") -> set[str]:
    keys: set[str] = set()
    for raw in (phone, chat_id):
        text = str(raw or "").strip()
        if not text:
            continue
        keys.add(text.lower())
        digits = _normalize_phone(text) or "".join(ch for ch in text if ch.isdigit())
        if digits:
            keys.add(digits)
            if len(digits) >= 9:
                keys.add(digits[-9:])
            keys.add(f"{digits}@c.us")
        if "@" in text:
            keys.add(text.split("@", 1)[0].lower())
    return {k for k in keys if k}


def _is_sandbox_join(body: str) -> bool:
    text = (body or "").strip().lower()
    return text.startswith("join ") or text == "join"


def _match_outbound_for_inbound(
    *,
    phone: str,
    chat_id: str,
    created_at: datetime,
) -> OutboundMessage | None:
    incoming_keys = _destination_keys(phone, chat_id)
    digits = [key for key in incoming_keys if key.isdigit()]
    query = Q()
    for value in digits:
        if len(value) >= 9:
            query |= Q(phone__endswith=value[-9:])
        query |= Q(phone=value)
    chat = str(chat_id or "").strip()
    if chat:
        query |= Q(wa_chat_id__iexact=chat)
    if not query:
        return None
    rows = (
        OutboundMessage.objects.filter(status=MSG_SENT, sent_at__isnull=False)
        .filter(query)
        .select_related("client")
        .order_by("-sent_at")
    )
    for row in rows[:25]:
        if created_at and row.sent_at and created_at < row.sent_at:
            continue
        return row
    return None


def record_inbound_reply(
    *,
    message_sid: str,
    from_value: str = "",
    wa_id: str = "",
    body: str = "",
    sender_name: str = "",
    created_at=None,
    num_media: int = 0,
) -> InboundReply | None:
    """Save one Twilio inbound message. Skips sandbox join texts. Cheap DB writes only."""
    from .twilio import (
        _e164,
        _looks_like_wa_lid,
        _strip_whatsapp_prefix,
        remember_whatsapp_lid,
    )
    from shops.services import get_communications_settings

    sid = str(message_sid or "").strip()
    if not sid:
        return None

    text = str(body or "").strip()
    if _is_sandbox_join(text):
        return None
    media_count = 0
    try:
        media_count = int(num_media or 0)
    except (TypeError, ValueError):
        media_count = 0
    if not text and media_count > 0:
        text = "[Media]"
    if not text:
        return None

    ident = _strip_whatsapp_prefix(from_value)
    wa_digits = _normalize_phone(wa_id) or "".join(ch for ch in str(wa_id or "") if ch.isdigit())
    phone = ""
    chat_id = ident[:120]
    if _looks_like_wa_lid(ident):
        lids = getattr(get_communications_settings(), "twilio_whatsapp_lids", None) or {}
        if isinstance(lids, dict):
            for stored_phone, stored_lid in lids.items():
                if str(stored_lid) == ident:
                    phone = _normalize_phone(stored_phone) or str(stored_phone)
                    break
        if wa_digits:
            phone = phone or wa_digits
            e164 = _e164(wa_digits)
            if e164:
                remember_whatsapp_lid(e164, ident)
    else:
        phone = _normalize_phone(ident) or wa_digits or "".join(ch for ch in ident if ch.isdigit())
    if not phone:
        phone = ident[:40]
    phone = str(phone)[:40]
    if not phone:
        return None

    when = _parse_created_at(created_at)
    outbound = _match_outbound_for_inbound(phone=phone, chat_id=chat_id, created_at=when)
    client = outbound.client if outbound is not None else _client_for_phone(phone)
    name = (sender_name or "").strip()
    if not name and client is not None:
        name = client.full_name or ""
    if not name and outbound is not None:
        name = outbound.client_name or ""

    row, created = InboundReply.objects.update_or_create(
        wa_message_id=sid[:200],
        defaults={
            "chat_id": chat_id,
            "phone": phone,
            "sender_name": name[:200],
            "body": text[:4000],
            "client": client,
            "outbound_message": outbound,
            "created_at": when,
        },
    )
    if not created and row.read_at:
        # Keep an already-read row read when Twilio syncs it again.
        pass
    return row


def pull_inbound_replies() -> dict[str, Any]:
    """Compatibility wrapper: persist recent Twilio inbound messages."""
    from .twilio import sync_inbound_replies

    saved = sync_inbound_replies()
    return {"ok": True, "saved": saved, "skipped": 0, "error": "", "skipped_non_app": False}


def _digits(value: str) -> str:
    return _normalize_phone(value) or "".join(ch for ch in (value or "") if ch.isdigit())


def thread_key(phone: str = "", chat_id: str = "") -> str:
    digits = _digits(phone) or _digits(chat_id)
    if len(digits) >= 9:
        return digits[-9:]
    return (chat_id or phone or "").strip().lower()[:40]


def _format_phone(phone: str) -> str:
    from shops.services import format_kenya_phone

    digits = _digits(phone)
    if digits:
        return format_kenya_phone(digits) or phone
    return (phone or "").strip()


def _initials(name: str) -> str:
    parts = [part for part in (name or "").split() if part]
    if len(parts) >= 2:
        return f"{parts[0][0]}{parts[1][0]}".upper()
    if parts:
        return parts[0][:1].upper()
    return "#"


def _tone(name: str) -> int:
    return (sum(ord(ch) for ch in (name or "")) % 6) + 1


def _clock(value: datetime | None) -> str:
    if value is None:
        return ""
    return timezone.localtime(value).strftime("%H:%M")


def _list_when(value: datetime | None) -> str:
    if value is None:
        return ""
    local = timezone.localtime(value)
    today = timezone.localtime(timezone.now()).date()
    day = local.date()
    if day == today:
        return local.strftime("%H:%M")
    if (today - day).days == 1:
        return "Yesterday"
    if 1 < (today - day).days < 7:
        return local.strftime("%a")
    return local.strftime("%d/%m/%Y")


def _day_label(value: datetime | None) -> str:
    if value is None:
        return ""
    local = timezone.localtime(value)
    today = timezone.localtime(timezone.now()).date()
    day = local.date()
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    return local.strftime("%d %B %Y")


def _format_when(value: datetime | None) -> str:
    if value is None:
        return ""
    local = timezone.localtime(value)
    return local.strftime("%d %b %Y, %H:%M")


def _tick_for_outbound(row: OutboundMessage) -> str:
    from .constants import MSG_FAILED, MSG_PENDING

    if row.status == MSG_FAILED:
        return "error"
    if row.status == MSG_PENDING:
        return "pending"
    if row.read_at:
        return "read"
    if row.delivered_at:
        return "delivered"
    return "sent"


def _image_src(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith(("http://", "https://", "/")):
        return raw[:500]
    return ""


def unread_reply_count() -> int:
    """Unique phones with at least one unread inbound reply."""
    return (
        InboundReply.objects.filter(read_at__isnull=True)
        .values("phone")
        .distinct()
        .count()
    )


def mark_inbox_read() -> int:
    now = timezone.now()
    phones = list(
        InboundReply.objects.filter(read_at__isnull=True)
        .values_list("phone", flat=True)
        .distinct()
    )
    InboundReply.objects.filter(read_at__isnull=True).update(read_at=now)
    return len(phones)


def mark_phone_read(phone: str) -> int:
    key = thread_key(phone)
    if not key:
        return 0
    now = timezone.now()
    rows = InboundReply.objects.filter(read_at__isnull=True)
    matched = [row.pk for row in rows if thread_key(row.phone, row.chat_id) == key]
    if not matched:
        return 0
    return InboundReply.objects.filter(pk__in=matched).update(read_at=now)


def inbox_threads(*, mark_read: bool = False, limit: int = 400) -> dict[str, Any]:
    """One conversation per phone, with inbound replies and our outbound sends."""
    from .constants import MSG_CANCELLED

    inbound_rows = list(
        InboundReply.objects.select_related("client").order_by("-created_at", "-id")[
            : max(1, limit)
        ]
    )
    outbound_rows = list(
        OutboundMessage.objects.exclude(status=MSG_CANCELLED)
        .select_related("client")
        .order_by("-id")[: max(1, limit)]
    )

    buckets: dict[str, dict[str, Any]] = {}

    def bucket(phone: str, chat_id: str = "") -> dict[str, Any] | None:
        key = thread_key(phone, chat_id)
        if not key:
            return None
        row = buckets.get(key)
        if row is None:
            row = {
                "key": key,
                "phone": "",
                "chat_id": chat_id or "",
                "client_id": None,
                "full_name": "",
                "messages": [],
            }
            buckets[key] = row
        return row

    for item in inbound_rows:
        row = bucket(item.phone, item.chat_id)
        if row is None:
            continue
        if not row["phone"]:
            row["phone"] = item.phone
        if item.chat_id:
            row["chat_id"] = item.chat_id
        if item.client_id:
            row["client_id"] = item.client_id
        name = (item.client.full_name if item.client else "") or item.sender_name
        if name and not row["full_name"]:
            row["full_name"] = name
        created = item.created_at
        row["messages"].append(
            {
                "id": f"in-{item.pk}",
                "direction": "in",
                "body": item.body or "",
                "image_url": "",
                "created_at": created.isoformat() if created else None,
                "time_label": _clock(created),
                "day_key": timezone.localtime(created).date().isoformat() if created else "",
                "day_label": _day_label(created),
                "unread": item.read_at is None and not mark_read,
                "tick": "",
                "error": "",
                "status": "received",
            }
        )

    for item in outbound_rows:
        row = bucket(item.phone, item.wa_chat_id)
        if row is None:
            continue
        if not row["phone"]:
            row["phone"] = item.phone
        if item.wa_chat_id:
            row["chat_id"] = item.wa_chat_id
        if item.client_id:
            row["client_id"] = item.client_id
        name = (item.client.full_name if item.client else "") or item.client_name
        if name and not row["full_name"]:
            row["full_name"] = name
        created = item.sent_at or item.created_at
        row["messages"].append(
            {
                "id": f"out-{item.pk}",
                "direction": "out",
                "body": item.body or "",
                "image_url": _image_src(item.image_path),
                "created_at": created.isoformat() if created else None,
                "time_label": _clock(created),
                "day_key": timezone.localtime(created).date().isoformat() if created else "",
                "day_label": _day_label(created),
                "unread": False,
                "tick": _tick_for_outbound(item),
                "error": item.error or "",
                "status": item.status,
            }
        )

    threads = []
    unread_before = 0
    for row in buckets.values():
        messages = [
            msg
            for msg in row["messages"]
            if (msg.get("body") or msg.get("image_url")) and msg.get("created_at")
        ]
        messages.sort(key=lambda msg: (msg.get("created_at") or "", msg.get("id") or ""))
        if not messages:
            continue
        latest = messages[-1]
        unread = sum(1 for msg in messages if msg.get("unread"))
        if unread:
            unread_before += 1
        phone = row["phone"] or row["key"]
        name = row["full_name"] or _format_phone(phone) or phone
        preview = latest.get("body") or ("Photo" if latest.get("image_url") else "")
        if latest.get("direction") == "out" and preview:
            preview = f"You: {preview}"
        threads.append(
            {
                "phone": phone,
                "display_phone": _format_phone(phone),
                "client_id": row["client_id"],
                "full_name": name,
                "initials": _initials(name),
                "tone": _tone(name),
                "body": preview,
                "reply_count": len(messages),
                "unread_count": 0 if mark_read else unread,
                "last_at": latest.get("created_at"),
                "last_label": _list_when(_parse_created_at(latest.get("created_at"))),
                "chat_id": row["chat_id"] or "",
                "messages": messages,
            }
        )

    threads.sort(key=lambda row: row.get("last_at") or "", reverse=True)
    threads = threads[:80]

    if mark_read:
        mark_inbox_read()

    return {
        "ok": True,
        "unread_count": 0 if mark_read else unread_before,
        "count": len(threads),
        "threads": threads,
    }


def send_inbox_reply(
    *,
    profile,
    phone: str,
    body: str,
    image=None,
    request=None,
) -> OutboundMessage:
    """Send one WhatsApp reply from the inbox and store it on the conversation."""
    from .automations import WHATSAPP_TEXT_LIMIT
    from .campaigns import file_whatsapp_media_url
    from .constants import CAMPAIGN_DONE, MSG_FAILED, MSG_PENDING, MSG_SENT
    from .models import BroadcastCampaign
    from .twilio import send_whatsapp_message

    text = (body or "").strip()
    dest = (phone or "").strip()
    if not dest:
        raise ValueError("Choose a chat first.")
    if not text and image is None:
        raise ValueError("Type a message.")
    if len(text) > WHATSAPP_TEXT_LIMIT:
        raise ValueError("That message is too long for WhatsApp.")

    client = _client_for_phone(dest)
    name = (client.full_name if client else "") or dest
    now = timezone.now()
    campaign = BroadcastCampaign.objects.create(
        created_by=profile,
        body_template=text or "Photo",
        filters={"inbox_reply": True, "client_ids": [client.pk] if client else []},
        status=CAMPAIGN_DONE,
        recipient_count=1,
        started_at=now,
        finished_at=now,
    )
    if image is not None:
        campaign.image = image
        campaign.save(update_fields=["image"])

    media_url = file_whatsapp_media_url(campaign.image, request=request) if campaign.image else ""
    image_path = ""
    if campaign.image:
        try:
            image_path = (campaign.image.url or "")[:500]
        except Exception:
            image_path = media_url

    message = OutboundMessage.objects.create(
        campaign=campaign,
        client=client,
        client_name=name[:200],
        phone=dest[:40],
        body=text,
        image_path=image_path,
        status=MSG_PENDING,
    )
    result = send_whatsapp_message(
        phone=dest,
        text=text,
        media_path=media_url or None,
        skip_poll=True,
    )
    if result.get("ok"):
        message.status = MSG_SENT
        message.wa_message_id = str(result.get("messageId") or "")[:200]
        message.wa_chat_id = str(result.get("chatId") or "")[:120]
        message.sent_at = timezone.now()
        message.provider_status = str(result.get("status") or "sent")[:40]
        campaign.sent_count = 1
        note_outbound_sent()
    else:
        from .twilio import _db_safe_error

        message.status = MSG_FAILED
        message.error = _db_safe_error(str(result.get("error") or "Send failed"))
        campaign.failed_count = 1
        campaign.save(update_fields=["failed_count"])
        message.save(
            update_fields=["status", "error", "image_path", "updated_at"]
        )
        raise ValueError(result.get("error") or "Send failed")
    campaign.save(
        update_fields=["sent_count", "failed_count", "status", "finished_at"]
    )
    message.save(
        update_fields=[
            "status",
            "wa_message_id",
            "wa_chat_id",
            "sent_at",
            "provider_status",
            "image_path",
            "updated_at",
        ]
    )
    return message


def matched_reply_stats() -> dict[str, Any]:
    """Reply metrics for analytics (from stored inbound rows)."""
    outbound_ids = set(
        InboundReply.objects.exclude(outbound_message_id=None).values_list(
            "outbound_message_id", flat=True
        )
    )
    by_campaign: dict[int, set[int]] = {}
    campaign_rows = (
        InboundReply.objects.exclude(outbound_message_id=None)
        .exclude(outbound_message__campaign_id=None)
        .values_list("outbound_message__campaign_id", "outbound_message_id")
    )
    for campaign_id, outbound_id in campaign_rows:
        by_campaign.setdefault(int(campaign_id), set()).add(int(outbound_id))

    return {
        "replied": len(outbound_ids),
        "unread_replies": unread_reply_count(),
        "by_campaign": {cid: len(ids) for cid, ids in by_campaign.items()},
        "error": "",
    }


def note_outbound_sent() -> None:
    """Invalidate outbound match cache after a successful send."""
    bust_outbound_index()
