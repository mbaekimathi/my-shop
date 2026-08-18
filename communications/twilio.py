"""Twilio SMS / WhatsApp send — HTTPS only, works on shared hosting."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import re

from django.conf import settings

from shops.services import get_communications_settings, _normalize_phone

from .constants import BRIDGE_STATUS_CONNECTED, BRIDGE_STATUS_DISCONNECTED

logger = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TWILIO_ACCOUNT_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}.json"
TWILIO_MESSAGE_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages/{msid}.json"
_ACCOUNT_SID_RE = re.compile(r"AC[0-9a-fA-F]{32}")
_WA_LID_RE = re.compile(r"^[A-Za-z]{2}\.\d{6,}$")


def _clean_account_sid(value: str) -> str:
    raw = (value or "").strip()
    match = _ACCOUNT_SID_RE.search(raw)
    if match:
        return match.group(0)
    return raw.split()[0] if raw else ""


def is_auth_error(error: str) -> bool:
    text = (error or "").strip().lower()
    return any(
        token in text
        for token in (
            "authenticate",
            "20003",
            "invalid username",
            "auth token",
            "account sid or auth token",
            "daily messages limit",
            "63038",
        )
    )


def is_twilio_fetchable_url(url: str) -> bool:
    """True when Twilio's servers can GET this URL (public http/https, not localhost)."""
    from urllib.parse import urlparse

    from shops.daraja_stk import _is_local_or_private_host

    raw = (url or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return False
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    if not host or _is_local_or_private_host(host):
        return False
    return True


def _is_invalid_media_error(error: str) -> bool:
    text = (error or "").strip().lower()
    return any(
        token in text
        for token in (
            "21620",
            "21619",
            "invalid media url",
            "unable to retrieve media",
        )
    )


def is_retryable_error(error: str) -> bool:
    if is_auth_error(error):
        return False
    text = (error or "").strip().lower()
    if not text:
        return True
    if any(
        token in text
        for token in (
            "could not reach",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "too many requests",
            "429",
            "502",
            "503",
            "504",
            "connection",
        )
    ):
        return True
    if any(
        token in text
        for token in (
            "invalid",
            "not a valid",
            "unsubscribed",
            "landline",
            "permission",
            "disabled",
            "21408",
            "63007",
            "63015",
            "63016",
            "21620",
            "21619",
            "sandbox",
            "has not joined",
        )
    ):
        return False
    return True


def friendly_send_error(error: str) -> str:
    raw = (error or "").strip() or "Send failed"
    text = raw.lower()
    if is_auth_error(raw):
        return (
            "Twilio rejected the Account SID or Auth Token. "
            "Open Twilio settings and save the live credentials from console.twilio.com."
        )
    if "21408" in text or "region indicated" in text:
        return (
            "Twilio blocked SMS to Kenya (21408). This page sends WhatsApp, not SMS. "
            "In Settings, save WhatsApp from as your Twilio sandbox number "
            "(usually whatsapp:+14155238886), then Retry failed."
        )
    if "21620" in text or "21619" in text or "invalid media url" in text:
        return (
            "Twilio could not fetch the item photo (21620). "
            "Localhost image links are not public. The caption can still send without the photo. "
            "Open MY-SHOP via a public HTTPS / ngrok URL if you want photos attached, then Retry failed."
        )
    if "63007" in text or "could not find a channel" in text:
        return (
            "That From number is not a Twilio WhatsApp sender. "
            "In Twilio Console open Messaging > Try it out > Send a WhatsApp message, "
            "copy the sandbox number into WhatsApp from, then Retry failed."
        )
    if "63015" in text or "63016" in text or "from/to pair" in text or "joined the sandbox" in text:
        try:
            join = sandbox_join_info()
        except Exception:
            join = {
                "phrase": "",
                "number": "+14155238886",
            }
        if join.get("phrase"):
            return (
                "Twilio still does not see this customer in your sandbox. "
                "Send the join from the same WhatsApp number shown for this person, "
                f"using '{join['phrase']}' to {join['number']}. "
                "Wait for 'You are all set', then Retry failed. "
                "A join from a different WhatsApp Web account will not count."
            )
        return (
            "Twilio still does not see this customer in your sandbox. "
            f"On that same WhatsApp number, send join control-did to {join.get('number') or '+14155238886'}, "
            "wait for 'You are all set', then Retry failed."
        )
    return raw[:500]


def _e164(value: str) -> str:
    raw = (value or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1].strip()
    kenya = _normalize_phone(raw)
    if kenya:
        return f"+{kenya}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) >= 10:
        return f"+{digits}"
    return ""


def sandbox_join_info(row=None) -> dict[str, str]:
    from urllib.parse import quote

    row = row or get_communications_settings()
    number = _e164(getattr(row, "twilio_whatsapp_from", "") or "") or "+14155238886"
    raw = (getattr(row, "twilio_whatsapp_join_code", "") or "").strip()
    if raw.lower().startswith("join "):
        phrase = " ".join(raw.split())
    elif raw:
        phrase = f"join {raw}"
    else:
        phrase = ""
    digits = "".join(ch for ch in number if ch.isdigit())
    wa_link = f"https://wa.me/{digits}"
    if phrase:
        wa_link = f"{wa_link}?text={quote(phrase)}"
    return {
        "number": number,
        "phrase": phrase,
        "wa_link": wa_link,
    }


def _basic_auth_header(account_sid: str, auth_token: str) -> str:
    import base64

    token = base64.b64encode(f"{account_sid}:{auth_token}".encode("ascii")).decode("ascii")
    return f"Basic {token}"


def _twilio_error_message(exc: HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
    try:
        parsed = json.loads(detail)
        message = parsed.get("message") or parsed.get("error_message") or detail
        code = parsed.get("code")
        if code and str(code) not in str(message):
            message = f"{message} ({code})"
    except json.JSONDecodeError:
        message = detail or str(exc)
    if exc.code == 401 or is_auth_error(str(message)):
        return friendly_send_error("Authenticate")
    return str(message)[:500]


def verify_twilio_credentials(account_sid: str, auth_token: str) -> dict[str, Any]:
    """Check SID/token against Twilio. Network failures are skipped so save still works."""
    sid = _clean_account_sid(account_sid)
    token = (auth_token or "").strip()
    if not sid or not token:
        return {"ok": False, "auth_failed": True, "error": "Account SID and Auth Token are required."}
    request = Request(
        TWILIO_ACCOUNT_API.format(sid=sid),
        method="GET",
        headers={"Authorization": _basic_auth_header(sid, token)},
    )
    try:
        with urlopen(request, timeout=12) as response:
            response.read()
    except HTTPError as exc:
        message = _twilio_error_message(exc)
        return {
            "ok": False,
            "auth_failed": exc.code == 401 or is_auth_error(message),
            "error": message,
        }
    except URLError:
        return {"ok": True, "skipped": True}
    except Exception as exc:
        return {"ok": False, "auth_failed": False, "error": str(exc)[:300]}
    return {"ok": True}


def provider_status() -> dict[str, Any]:
    """Shape matches the old bridge status payload so the workspace UI still works."""
    row = get_communications_settings()
    configured = row.has_twilio_credentials()
    from_number = (row.twilio_whatsapp_from or row.twilio_from_number or "").strip()
    payload = {
        "ok": True,
        "status": BRIDGE_STATUS_CONNECTED if configured else BRIDGE_STATUS_DISCONNECTED,
        "qr_data_url": "",
        "wa_phone": _e164(from_number).lstrip("+") if configured else "",
        "last_error": "" if configured else "Save Twilio credentials in Settings.",
        "channel": "whatsapp",
    }
    payload.update(bridge_deploy_hints())
    return payload


def bridge_state_as_dict(row=None) -> dict[str, Any]:
    return provider_status()


def fetch_bridge_status() -> dict[str, Any]:
    return provider_status()


def bridge_deploy_hints() -> dict[str, str]:
    return {
        "mode": "twilio",
        "autostart": False,
        "connect_help": "Sends go through Twilio over HTTPS. No VPS or QR scan.",
        "help_text": "Open Settings → Twilio and save Account SID, Auth Token, and WhatsApp from.",
        "cmd": "",
        "note": "Use the Twilio WhatsApp sandbox number (usually whatsapp:+14155238886).",
    }


def logout_bridge() -> dict[str, Any]:
    return {"ok": True}


def status_callback_url() -> str:
    """Public HTTPS URL Twilio can POST delivery/read updates to."""
    try:
        from shops.daraja_stk import resolve_callback_base_url

        base = (resolve_callback_base_url() or "").rstrip("/")
    except Exception:
        base = ""
    if not base:
        base = (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/twilio/status/"


def request_signature_ok(request, auth_token: str, signature: str) -> bool:
    import base64
    import hashlib
    import hmac

    token = (auth_token or "").strip()
    sig = (signature or "").strip()
    if not token or not sig:
        return False
    proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "https").split(",")[0].strip()
    host = request.get_host()
    url = f"{proto}://{host}{request.get_full_path()}"
    data = url
    for key in sorted(request.POST.keys()):
        for value in request.POST.getlist(key):
            data += f"{key}{value}"
    expected = base64.b64encode(
        hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, sig)


def apply_message_status(
    *,
    message_sid: str,
    status: str,
    error_code: Any = None,
    error_message: str = "",
) -> bool:
    """Update an outbound row from a Twilio status callback (sent/delivered/read/failed)."""
    from django.utils import timezone

    from .campaigns import refresh_campaign_counts
    from .constants import MSG_CANCELLED, MSG_FAILED, MSG_PENDING, MSG_SENT
    from .models import OutboundMessage

    sid = (message_sid or "").strip()
    state = (status or "").strip().lower()
    if not sid or not state:
        return False
    row = OutboundMessage.objects.filter(wa_message_id=sid).first()
    if row is None or row.status == MSG_CANCELLED:
        return False
    now = timezone.now()
    fields = ["provider_status", "updated_at"]
    row.provider_status = state[:40]
    if state in {"queued", "accepted", "sending", "sent"}:
        if not row.sent_at:
            row.sent_at = now
            fields.append("sent_at")
        if row.status == MSG_PENDING:
            row.status = MSG_SENT
            fields.append("status")
    elif state == "delivered":
        if not row.delivered_at:
            row.delivered_at = now
            fields.append("delivered_at")
        if not row.sent_at:
            row.sent_at = now
            fields.append("sent_at")
        if row.status in {MSG_PENDING, MSG_SENT}:
            row.status = MSG_SENT
            if "status" not in fields:
                fields.append("status")
    elif state == "read":
        if not row.read_at:
            row.read_at = now
            fields.append("read_at")
        if not row.delivered_at:
            row.delivered_at = now
            fields.append("delivered_at")
        if not row.sent_at:
            row.sent_at = now
            fields.append("sent_at")
        if row.status in {MSG_PENDING, MSG_SENT}:
            row.status = MSG_SENT
            if "status" not in fields:
                fields.append("status")
    elif state in {"failed", "undelivered"}:
        row.status = MSG_FAILED
        fields.append("status")
        detail = _delivery_error_text(state, error_code, error_message)
        if detail:
            row.error = _db_safe_error(detail)
            fields.append("error")
    row.save(update_fields=fields)
    refresh_campaign_counts(row.campaign)
    return True


def _db_safe_error(value: str) -> str:
    text = (
        (value or "")
        .replace("\u2192", ">")
        .replace("\u2014", "-")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
    )
    return text.encode("latin-1", "replace").decode("latin-1")[:2000]


def _delivery_error_text(status: str, error_code: Any, error_message: str) -> str:
    raw = (error_message or "").strip()
    if error_code and str(error_code) not in raw:
        raw = f"{raw} ({error_code})".strip() if raw else str(error_code)
    if not raw:
        raw = f"Twilio status: {status or 'failed'}"
    return _db_safe_error(friendly_send_error(raw))


def fetch_twilio_message(account_sid: str, auth_token: str, message_sid: str) -> dict[str, Any] | None:
    sid = _clean_account_sid(account_sid)
    token = (auth_token or "").strip()
    msid = (message_sid or "").strip()
    if not sid or not token or not msid:
        return None
    request = Request(
        TWILIO_MESSAGE_API.format(sid=sid, msid=msid),
        method="GET",
        headers={"Authorization": _basic_auth_header(sid, token)},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        logger.debug("Twilio message fetch failed for %s", msid, exc_info=True)
        return None


def _await_message_outcome(
    account_sid: str,
    auth_token: str,
    message_sid: str,
    *,
    attempts: int = 4,
    delay: float = 0.9,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for index in range(max(1, attempts)):
        if index:
            time.sleep(delay)
        last = fetch_twilio_message(account_sid, auth_token, message_sid) or last
        status = str(last.get("status") or "").lower()
        if last.get("error_code") or status in {"delivered", "read", "failed", "undelivered"}:
            return last
    return last


def sync_outbound_delivery_status(*, limit: int = 25) -> int:
    """Pull queued/sent Twilio outcomes. Needed on localhost where webhooks cannot arrive."""
    from .constants import MSG_SENT
    from .models import OutboundMessage

    row = get_communications_settings()
    account_sid = _clean_account_sid(row.twilio_account_sid or "")
    token = (row.twilio_auth_token or "").strip()
    if not account_sid or not token:
        return 0
    messages = list(
        OutboundMessage.objects.filter(status=MSG_SENT)
        .exclude(wa_message_id="")
        .filter(delivered_at__isnull=True)
        .order_by("-id")[:limit]
    )
    updated = 0
    for message in messages:
        info = fetch_twilio_message(account_sid, token, message.wa_message_id)
        if not info:
            continue
        if apply_message_status(
            message_sid=message.wa_message_id,
            status=str(info.get("status") or ""),
            error_code=info.get("error_code"),
            error_message=str(info.get("error_message") or ""),
        ):
            updated += 1
    return updated


def fetch_whatsapp_contacts(*, search: str = "", include_groups: bool = True) -> dict[str, Any]:
    return {"ok": True, "contacts": [], "groups": []}


def _whatsapp_sender(row) -> str:
    raw = (getattr(row, "twilio_whatsapp_from", "") or getattr(row, "twilio_from_number", "") or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1].strip()
    number = _e164(raw)
    return f"whatsapp:{number}" if number else ""


def _looks_like_wa_lid(value: str) -> bool:
    text = (value or "").strip()
    if text.lower().startswith("whatsapp:"):
        text = text.split(":", 1)[1].strip()
    return bool(_WA_LID_RE.match(text))


def _strip_whatsapp_prefix(value: str) -> str:
    raw = (value or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1].strip()
    for suffix in ("@c.us", "@lid"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    return raw


def remember_whatsapp_lid(phone_e164: str, lid: str) -> None:
    key = _e164(phone_e164).lstrip("+")
    ident = _strip_whatsapp_prefix(lid)
    if not key or not _looks_like_wa_lid(ident):
        return
    row = get_communications_settings()
    lids = dict(getattr(row, "twilio_whatsapp_lids", None) or {})
    if lids.get(key) == ident:
        return
    lids[key] = ident
    row.twilio_whatsapp_lids = lids
    row.save(update_fields=["twilio_whatsapp_lids", "updated_at"])
    from shops.services import _invalidate_communications_settings_cache

    _invalidate_communications_settings_cache()


def _stored_lid_for_phone(row, phone_e164: str) -> str:
    lids = getattr(row, "twilio_whatsapp_lids", None) or {}
    if not isinstance(lids, dict):
        return ""
    key = _e164(phone_e164).lstrip("+")
    return str(lids.get(key) or lids.get(f"+{key}") or "")


def _latest_sandbox_join_lid(account_sid: str, auth_token: str, sandbox_from: str) -> str:
    request = Request(
        TWILIO_API.format(sid=account_sid) + "?PageSize=40",
        method="GET",
        headers={"Authorization": _basic_auth_header(account_sid, auth_token)},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        logger.debug("Could not list Twilio messages for sandbox join LID", exc_info=True)
        return ""
    want = (sandbox_from or "").lower()
    for item in payload.get("messages") or []:
        if str(item.get("direction") or "") != "inbound":
            continue
        if want and str(item.get("to") or "").lower() != want:
            continue
        body = str(item.get("body") or "").strip().lower()
        if not body.startswith("join"):
            continue
        ident = _strip_whatsapp_prefix(str(item.get("from") or ""))
        if _looks_like_wa_lid(ident):
            return ident
    return ""


def _submit_twilio_message(
    *,
    account_sid: str,
    auth_token: str,
    from_value: str,
    to_value: str,
    text: str,
    media_path: str = "",
    skip_poll: bool = False,
) -> dict[str, Any]:
    body = [
        ("To", to_value),
        ("From", from_value),
        ("Body", text or ""),
    ]
    media = (media_path or "").strip()
    if media and not is_twilio_fetchable_url(media):
        logger.info("Skipping MediaUrl Twilio cannot fetch: %s", media)
        media = ""
    if media:
        body.append(("MediaUrl", media))
    callback = status_callback_url()
    if callback:
        body.append(("StatusCallback", callback))
        for event in ("sent", "delivered", "undelivered", "failed", "read"):
            body.append(("StatusCallbackEvent", event))
    request = Request(
        TWILIO_API.format(sid=account_sid),
        data=urlencode(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": _basic_auth_header(account_sid, auth_token),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        message = _twilio_error_message(exc)
        if media and _is_invalid_media_error(message):
            logger.warning("Twilio rejected MediaUrl, retrying text-only: %s", message)
            return _submit_twilio_message(
                account_sid=account_sid,
                auth_token=auth_token,
                from_value=from_value,
                to_value=to_value,
                text=text,
                media_path="",
                skip_poll=skip_poll,
            )
        logger.warning("Twilio send failed: %s", message)
        return {
            "ok": False,
            "retryable": is_retryable_error(message),
            "error": friendly_send_error(message),
        }
    except URLError as exc:
        message = f"Could not reach Twilio: {exc.reason}"
        return {"ok": False, "retryable": True, "error": message}
    except Exception as exc:
        message = str(exc)
        return {
            "ok": False,
            "retryable": is_retryable_error(message),
            "error": message[:500],
        }

    sid = str(payload.get("sid") or "")
    status = str(payload.get("status") or "")
    error_code = payload.get("error_code")
    if sid and not skip_poll and not error_code and status not in {"failed", "undelivered", "delivered", "read"}:
        settled = _await_message_outcome(account_sid, auth_token, sid)
        if settled:
            status = str(settled.get("status") or status)
            error_code = settled.get("error_code") or error_code
            payload = {**payload, **settled}
    if error_code or status in {"failed", "undelivered"}:
        message = _delivery_error_text(
            status,
            error_code,
            payload.get("error_message") or "",
        )
        retry_hint = f"{error_code} {payload.get('error_message') or ''} {status} {message}"
        if media and _is_invalid_media_error(retry_hint):
            logger.warning("Twilio rejected MediaUrl, retrying text-only: %s", message)
            return _submit_twilio_message(
                account_sid=account_sid,
                auth_token=auth_token,
                from_value=from_value,
                to_value=to_value,
                text=text,
                media_path="",
                skip_poll=skip_poll,
            )
        logger.warning("Twilio send failed: %s", message)
        return {
            "ok": False,
            "retryable": is_retryable_error(retry_hint),
            "error": message,
            "messageId": sid,
            "status": status,
            "error_code": error_code,
        }
    return {
        "ok": True,
        "messageId": sid,
        "chatId": to_value,
        "status": status,
    }


def send_whatsapp_message(
    *,
    phone: str = "",
    text: str,
    media_path: str | None = None,
    chat_id: str | None = None,
    skip_poll: bool = False,
) -> dict[str, Any]:
    dest = _strip_whatsapp_prefix(phone or chat_id or "")
    if "@g.us" in dest:
        return {
            "ok": False,
            "retryable": False,
            "error": "Twilio cannot send to personal WhatsApp groups.",
        }

    row = get_communications_settings()
    account_sid = _clean_account_sid(row.twilio_account_sid or "")
    auth_token = (row.twilio_auth_token or "").strip()
    if not account_sid or not auth_token or not row.has_twilio_credentials():
        return {
            "ok": False,
            "retryable": False,
            "error": "Twilio is not configured. Save Account SID, Auth Token, and WhatsApp from.",
        }

    from_value = _whatsapp_sender(row)
    if not from_value:
        return {
            "ok": False,
            "retryable": False,
            "error": (
                "Set WhatsApp from in Settings. Use your Twilio sandbox number, "
                "usually whatsapp:+14155238886."
            ),
        }

    to_number = ""
    if _looks_like_wa_lid(dest):
        to_value = f"whatsapp:{dest}"
    else:
        to_number = _e164(dest)
        if not to_number:
            return {
                "ok": False,
                "retryable": False,
                "error": "Missing recipient phone number.",
            }
        lid = _stored_lid_for_phone(row, to_number)
        to_value = f"whatsapp:{lid}" if lid else f"whatsapp:{to_number}"

    result = _submit_twilio_message(
        account_sid=account_sid,
        auth_token=auth_token,
        from_value=from_value,
        to_value=to_value,
        text=text,
        media_path=media_path or "",
        skip_poll=skip_poll,
    )
    error_code = str(result.get("error_code") or "")
    if (
        to_number
        and not result.get("ok")
        and (error_code == "63015" or "sandbox" in (result.get("error") or "").lower())
    ):
        lid = _latest_sandbox_join_lid(account_sid, auth_token, from_value)
        lid_to = f"whatsapp:{lid}" if lid else ""
        if lid_to and lid_to != to_value:
            remember_whatsapp_lid(to_number, lid)
            result = _submit_twilio_message(
                account_sid=account_sid,
                auth_token=auth_token,
                from_value=from_value,
                to_value=lid_to,
                text=text,
                media_path=media_path or "",
                skip_poll=skip_poll,
            )
    return result
