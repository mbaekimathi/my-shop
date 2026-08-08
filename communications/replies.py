"""Inbound WhatsApp replies: live from bridge, matched to MY-SHOP sends (no DB rows)."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shops.models import Client
from shops.services import _normalize_phone

from .bridge import _request
from .constants import MSG_SENT
from .ephemeral import (
    bust_outbound_index,
    get_cached_outbound_index,
    get_read_state,
    mark_phones_read,
    set_cached_outbound_index,
)
from .models import InboundReply, OutboundMessage


def _discard_legacy_db_replies() -> None:
    """One-time cleanup: stop growing the unused InboundReply table."""
    from django.core.cache import cache

    flag = "comms:inbound_db_purged:v1"
    if cache.get(flag):
        return
    try:
        InboundReply.objects.all().delete()
    except Exception:
        pass
    cache.set(flag, 1, timeout=60 * 60 * 24 * 30)


def _parse_created_at(raw) -> datetime:
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


def _serialize_outbound_row(message: OutboundMessage) -> dict[str, Any]:
    keys = _destination_keys(message.phone, message.wa_chat_id)
    client = message.client
    if client is not None:
        client_phone = _normalize_phone(
            client.phone_normalized or client.phone_number or ""
        )
        keys |= _destination_keys(client_phone or "", "")
    return {
        "id": message.pk,
        "campaign_id": message.campaign_id,
        "keys": list(keys),
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "client_id": message.client_id,
        "client_name": message.client_name
        or (client.full_name if client else "")
        or "",
    }


def _outbound_index(*, use_cache: bool = True) -> list[dict[str, Any]]:
    if use_cache:
        cached = get_cached_outbound_index()
        if cached is not None:
            return cached

    rows = (
        OutboundMessage.objects.filter(status=MSG_SENT, sent_at__isnull=False)
        .select_related("client")
        .only(
            "id",
            "campaign_id",
            "phone",
            "wa_chat_id",
            "wa_message_id",
            "sent_at",
            "client_id",
            "client_name",
            "client__full_name",
            "client__phone_normalized",
            "client__phone_number",
        )
        .order_by("-sent_at")
    )
    index = [_serialize_outbound_row(message) for message in rows]
    index = [row for row in index if row.get("keys")]
    set_cached_outbound_index(index)
    return index


def _match_app_outbound(
    *,
    phone: str,
    chat_id: str,
    created_at: datetime,
    index: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Only accept replies from destinations we messaged from MY-SHOP."""
    incoming_keys = _destination_keys(phone, chat_id)
    if not incoming_keys:
        return None
    rows = index if index is not None else _outbound_index()
    for row in rows:
        row_keys = set(row.get("keys") or [])
        if not (incoming_keys & row_keys):
            continue
        sent_raw = row.get("sent_at")
        sent_at = _parse_created_at(sent_raw) if sent_raw else None
        if sent_at and created_at and created_at < sent_at:
            continue
        return row
    return None


def _fetch_bridge_items() -> tuple[bool, list[dict[str, Any]], str]:
    """Live-scan WhatsApp via the bridge, then return inbound items."""
    try:
        # Prefer an explicit live Store scan.
        result = _request("POST", "/inbound/scan", timeout=30.0)
        if result.get("unreachable"):
            return False, [], result.get("error") or "WhatsApp helper is not running."
        if result.get("_http_status") in (404, 405) or (
            not result.get("ok", True) and "items" not in result
        ):
            result = _request("GET", "/inbound", timeout=30.0)
    except Exception as exc:
        return False, [], str(exc)
    if result.get("unreachable") or not result.get("ok", True):
        return False, [], result.get("error") or "Could not fetch replies."
    return True, list(result.get("items") or []), ""


def _matched_replies(
    *,
    index: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ok, items, error = _fetch_bridge_items()
    meta = {"ok": ok, "error": error, "saved": 0, "skipped": 0}
    if not ok:
        return [], meta

    outbound = index if index is not None else _outbound_index()
    if not outbound:
        meta["skipped_non_app"] = True
        return [], meta

    matched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        wa_id = str(item.get("id") or "").strip()
        if not wa_id or wa_id in seen_ids:
            continue
        seen_ids.add(wa_id)

        chat_id = str(item.get("chatId") or "").strip()
        phone = _normalize_phone(item.get("phone") or "") or ""
        if not phone and chat_id:
            phone = _normalize_phone(chat_id.split("@")[0] if "@" in chat_id else chat_id)
        if not phone:
            phone = "".join(ch for ch in chat_id if ch.isdigit()) or chat_id[:40]
        if not phone:
            continue

        created_at = _parse_created_at(item.get("timestamp"))
        outbound_row = _match_app_outbound(
            phone=phone,
            chat_id=chat_id,
            created_at=created_at,
            index=outbound,
        )
        if outbound_row is None:
            meta["skipped"] += 1
            continue

        name = (item.get("name") or "").strip() or outbound_row.get("client_name") or ""
        matched.append(
            {
                "wa_message_id": wa_id,
                "chat_id": chat_id,
                "phone": phone[:40],
                "sender_name": name[:200],
                "body": str(item.get("body") or "")[:4000],
                "created_at": created_at,
                "outbound_id": outbound_row.get("id"),
                "campaign_id": outbound_row.get("campaign_id"),
                "client_id": outbound_row.get("client_id"),
                "client_name": outbound_row.get("client_name") or "",
            }
        )
        meta["saved"] += 1

    return matched, meta


def purge_non_app_replies() -> int:
    """No-op for DB (replies are not stored). Kept for call-site compatibility."""
    return 0


def pull_inbound_replies() -> dict[str, Any]:
    """Fetch bridge replies and keep only ones that answer an app send (in memory)."""
    _discard_legacy_db_replies()
    _matched, meta = _matched_replies()
    return {
        "ok": bool(meta.get("ok")),
        "saved": int(meta.get("saved") or 0),
        "skipped": int(meta.get("skipped") or 0),
        "error": meta.get("error") or "",
        "skipped_non_app": bool(meta.get("skipped_non_app")),
    }


def _thread_is_unread(phone: str, last_at: datetime | None, read_state: dict[str, str]) -> bool:
    if last_at is None:
        return False
    stamped = read_state.get(str(phone or "").strip())
    if not stamped:
        return True
    read_at = _parse_created_at(stamped)
    return last_at > read_at


def unread_reply_count() -> int:
    """Unique app-reply threads with at least one unread reply."""
    matched, meta = _matched_replies()
    if not meta.get("ok"):
        return 0
    read_state = get_read_state()
    latest: dict[str, datetime] = {}
    for row in matched:
        phone = row["phone"]
        created = row["created_at"]
        prev = latest.get(phone)
        if prev is None or created > prev:
            latest[phone] = created
    return sum(
        1 for phone, last_at in latest.items() if _thread_is_unread(phone, last_at, read_state)
    )


def mark_inbox_read() -> int:
    matched, meta = _matched_replies()
    if not meta.get("ok"):
        return 0
    phones = sorted({row["phone"] for row in matched if row.get("phone")})
    mark_phones_read(phones, when_iso=timezone.now().isoformat())
    return len(phones)


def inbox_threads(*, mark_read: bool = False) -> dict[str, Any]:
    """One row per phone that replied to a MY-SHOP WhatsApp send."""
    _discard_legacy_db_replies()
    matched, meta = _matched_replies()
    if not meta.get("ok"):
        return {
            "ok": False,
            "error": meta.get("error") or "Could not load replies.",
            "unread_count": 0,
            "count": 0,
            "threads": [],
        }

    read_state = get_read_state()
    by_phone: dict[str, list[dict[str, Any]]] = {}
    for row in matched:
        by_phone.setdefault(row["phone"], []).append(row)

    threads = []
    unread_before = 0
    for phone, rows in by_phone.items():
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        latest = rows[0]
        unread = sum(
            1
            for row in rows
            if _thread_is_unread(phone, row["created_at"], read_state)
        )
        if unread:
            unread_before += 1

        client_id = latest.get("client_id")
        name = latest.get("client_name") or latest.get("sender_name") or phone
        if not latest.get("client_name") and client_id:
            client = Client.objects.filter(pk=client_id).only("full_name").first()
            if client and client.full_name:
                name = client.full_name
        elif not latest.get("client_name"):
            client = _client_for_phone(phone)
            if client:
                client_id = client.pk
                name = client.full_name or name

        threads.append(
            {
                "phone": phone,
                "client_id": client_id,
                "full_name": name,
                "body": latest.get("body") or "",
                "reply_count": len(rows),
                "unread_count": 0 if mark_read else unread,
                "last_at": latest["created_at"].isoformat() if latest.get("created_at") else None,
                "chat_id": latest.get("chat_id") or "",
            }
        )

    threads.sort(key=lambda row: row.get("last_at") or "", reverse=True)

    if mark_read:
        mark_phones_read(
            [row["phone"] for row in threads],
            when_iso=timezone.now().isoformat(),
        )

    return {
        "ok": True,
        "unread_count": 0 if mark_read else unread_before,
        "count": len(threads),
        "threads": threads,
    }


def matched_reply_stats() -> dict[str, Any]:
    """Reply metrics for analytics (computed live, not stored)."""
    matched, meta = _matched_replies()
    if not meta.get("ok"):
        return {
            "replied": 0,
            "unread_replies": 0,
            "by_campaign": {},
            "error": meta.get("error") or "",
        }

    outbound_ids = {
        int(row["outbound_id"])
        for row in matched
        if row.get("outbound_id") is not None
    }
    by_campaign: dict[int, set[int]] = {}
    for row in matched:
        campaign_id = row.get("campaign_id")
        outbound_id = row.get("outbound_id")
        if campaign_id is None or outbound_id is None:
            continue
        by_campaign.setdefault(int(campaign_id), set()).add(int(outbound_id))

    read_state = get_read_state()
    latest: dict[str, datetime] = {}
    for row in matched:
        phone = row["phone"]
        created = row["created_at"]
        prev = latest.get(phone)
        if prev is None or created > prev:
            latest[phone] = created
    unread = sum(
        1 for phone, last_at in latest.items() if _thread_is_unread(phone, last_at, read_state)
    )

    return {
        "replied": len(outbound_ids),
        "unread_replies": unread,
        "by_campaign": {cid: len(ids) for cid, ids in by_campaign.items()},
        "error": "",
    }


def note_outbound_sent() -> None:
    """Invalidate outbound match cache after a successful send."""
    bust_outbound_index()
