"""SMS provider connectivity checks and sending — HTTPS only, works on shared hosting.

Credential field mapping (CompanyCommunicationsSettings):
- Africa's Talking: sms_api_key = username, sms_api_secret = apiKey.
  sms_api_base_url may point at https://api.sandbox.africastalking.com.
- Twilio: sms_api_key = Account SID, sms_api_secret = Auth Token.
  Blank values fall back to the Twilio (WhatsApp) credentials; the From number
  falls back to twilio_from_number, else sms_sender_id.
- BlessedTexts: sms_api_key = API key, sms_sender_id = sender ID
  (e.g. "23107"). sms_api_base_url may override https://sms.blessedtexts.com.
- Custom HTTP: provider-specific; there is no generic endpoint to probe.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shops.models import SmsProvider

logger = logging.getLogger(__name__)

AT_API_BASE = "https://api.africastalking.com"
BLESSED_TEXTS_API_BASE = "https://sms.blessedtexts.com"

# Cloudflare blocks the default "Python-urllib/x.y" signature (error 1010).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _skipped() -> dict[str, Any]:
    return {"ok": True, "skipped": True, "auth_failed": False, "error": "", "detail": ""}


def _fail(error: str, *, auth_failed: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "skipped": False,
        "auth_failed": auth_failed,
        "error": (error or "")[:300],
        "detail": "",
    }


def _base_url(value: str, default: str) -> str:
    return (value or "").strip().rstrip("/") or default


def _blessed_texts_base(api_base_url: str) -> str:
    """Base URL for BlessedTexts; tolerate users pasting the full /api/sms/v1 path."""
    base = _base_url(api_base_url, BLESSED_TEXTS_API_BASE)
    if base.endswith("/api/sms/v1"):
        base = base[: -len("/api/sms/v1")]
    return base


def _read_error_body(exc: HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
    try:
        parsed = json.loads(detail)
        if isinstance(parsed, dict):
            message = (
                parsed.get("message")
                or parsed.get("status_desc")
                or parsed.get("error_message")
                or parsed.get("error")
                or ""
            )
            code = parsed.get("code") or parsed.get("status_code") or ""
            if message and code and str(code) not in str(message):
                message = f"{message} ({code})"
            if message:
                return str(message)
    except json.JSONDecodeError:
        pass
    return detail or str(exc)


def _post_json(url: str, payload: dict, *, timeout: int = 15) -> tuple[Any, str]:
    """POST a JSON body. Returns (parsed_json, error_text)."""
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "null"), ""
    except HTTPError as exc:
        return None, _read_error_body(exc)
    except URLError as exc:
        return None, f"Could not reach SMS provider: {exc.reason}"
    except Exception as exc:
        return None, str(exc)


def _verify_africas_talking(
    username: str, api_key: str, api_base_url: str = ""
) -> dict[str, Any]:
    """GET /version1/user — returns the account balance when credentials are valid."""
    username = (username or "").strip()
    api_key = (api_key or "").strip()
    if not username or not api_key:
        return _fail(
            "Africa's Talking username (API key field) and apiKey (API secret field) are required.",
            auth_failed=True,
        )
    url = f"{_base_url(api_base_url, AT_API_BASE)}/version1/user?{urlencode({'username': username})}"
    request = Request(
        url,
        method="GET",
        headers={
            "apiKey": api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        return _fail(_read_error_body(exc), auth_failed=exc.code in (400, 401, 403))
    except URLError:
        return _skipped()
    except Exception as exc:
        return _fail(str(exc))
    balance = str((payload.get("UserData") or {}).get("balance") or "").strip()
    return {
        "ok": True,
        "skipped": False,
        "auth_failed": False,
        "error": "",
        "detail": f"Account balance: {balance}" if balance else "",
    }


def _verify_twilio(api_key: str, api_secret: str, row=None) -> dict[str, Any]:
    sid = (api_key or "").strip() or getattr(row, "twilio_account_sid", "") or ""
    token = (api_secret or "").strip() or getattr(row, "twilio_auth_token", "") or ""
    if not sid or not token:
        return _fail(
            "Account SID and Auth Token are required for Twilio SMS.",
            auth_failed=True,
        )
    from .twilio import verify_twilio_credentials

    check = verify_twilio_credentials(sid, token)
    if check.get("skipped"):
        return _skipped()
    if check.get("ok"):
        return {
            "ok": True,
            "skipped": False,
            "auth_failed": False,
            "error": "",
            "detail": "",
        }
    return _fail(
        check.get("error") or "Twilio rejected these credentials.",
        auth_failed=bool(check.get("auth_failed")),
    )


def _verify_blessed_texts(api_key: str, api_base_url: str = "") -> dict[str, Any]:
    """POST /api/sms/v1/credit-balance — returns SMS credit balance when valid."""
    api_key = (api_key or "").strip()
    if not api_key:
        return _fail("BlessedTexts API key is required.", auth_failed=True)
    url = f"{_blessed_texts_base(api_base_url)}/api/sms/v1/credit-balance"
    payload, error = _post_json(url, {"api_key": api_key})
    if payload is None:
        if error.startswith("Could not reach"):
            return _skipped()
        return _fail(error)
    if not isinstance(payload, dict):
        return _fail("Unexpected response from BlessedTexts.")
    code = str(payload.get("status_code") or "")
    if code == "1000":
        balance = str(payload.get("balance") or "").strip()
        return {
            "ok": True,
            "skipped": False,
            "auth_failed": False,
            "error": "",
            "detail": f"SMS balance: {balance}" if balance else "",
        }
    return _fail(
        str(payload.get("status_desc") or f"BlessedTexts status {code}"),
        auth_failed=code in {"1001", "1002"},
    )


def verify_sms_credentials(
    provider: str,
    api_key: str,
    api_secret: str,
    *,
    api_base_url: str = "",
    row=None,
) -> dict[str, Any]:
    """Ping the SMS provider with these credentials.

    Returns {ok, skipped, auth_failed, error, detail}. skipped=True means the
    provider could not be reached (or has no probe endpoint) — the caller
    should not treat that as a credential failure.
    """
    provider = (provider or "").strip()
    if provider == SmsProvider.AFRICAS_TALKING:
        return _verify_africas_talking(api_key, api_secret, api_base_url)
    if provider == SmsProvider.TWILIO:
        return _verify_twilio(api_key, api_secret, row)
    if provider == SmsProvider.BLESSED_TEXTS:
        return _verify_blessed_texts(api_key, api_base_url)
    return _skipped()


def _sms_failure_retryable(*parts: str) -> bool:
    """True unless the provider error text describes a permanent rejection."""
    text = " ".join(str(part or "") for part in parts).lower()
    return not any(
        token in text
        for token in (
            "invalid",
            "insufficient",
            "balance",
            "api key",
            "unauthorized",
            "forbidden",
            "not allowed",
            "permission",
            "blocked",
            "blacklist",
            "sender",
            "not supported",
        )
    )


def _sms_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [row for row in results if not row.get("ok")]
    errors = [str(row.get("error") or row.get("status_desc") or "Send failed") for row in failed]
    payload: dict[str, Any] = {
        "ok": bool(results) and not errors,
        "results": results,
        "error": "; ".join(errors)[:300],
        "messageId": next(
            (str(row.get("message_id") or "") for row in results if row.get("ok")), ""
        ),
    }
    # When every failed recipient carries an explicit classification, surface it
    # so the worker stops retrying permanent errors instead of burning attempts.
    if failed and all("retryable" in row for row in failed):
        payload["retryable"] = any(row["retryable"] for row in failed)
    return payload


def _blessed_texts_send(row, numbers: list[str], text: str) -> dict[str, Any]:
    url = f"{_blessed_texts_base(row.sms_api_base_url)}/api/sms/v1/sendsms"
    payload, error = _post_json(
        url,
        {
            "api_key": (row.sms_api_key or "").strip(),
            "sender_id": (row.sms_sender_id or "").strip(),
            "message": text,
            "phone": ",".join(numbers),
        },
        timeout=30,
    )
    if payload is None:
        logger.warning("BlessedTexts send failed: %s", error)
        return _sms_result([{"ok": False, "error": error}])
    items = payload if isinstance(payload, list) else [payload]
    results = []
    for item in items:
        item = item if isinstance(item, dict) else {}
        code = str(item.get("status_code") or "")
        desc = str(item.get("status_desc") or "")
        row_result = {
            "ok": code == "1000",
            "phone": str(item.get("phone") or ""),
            "status": code,
            "status_desc": desc,
            "message_id": str(item.get("message_id") or ""),
            "cost": str(item.get("message_cost") or ""),
            "error": "" if code == "1000" else desc or f"Status {code}",
        }
        if code != "1000":
            row_result["retryable"] = _sms_failure_retryable(desc, code)
        results.append(row_result)
    if not results:
        return _sms_result([{"ok": False, "error": "Empty response from BlessedTexts."}])
    return _sms_result(results)


def _africas_talking_send(row, numbers: list[str], text: str) -> dict[str, Any]:
    url = f"{_base_url(row.sms_api_base_url, AT_API_BASE)}/version1/messaging"
    body = urlencode(
        {
            "username": (row.sms_api_key or "").strip(),
            "to": ",".join(f"+{number}" for number in numbers),
            "message": text,
            "from": (row.sms_sender_id or "").strip(),
        }
    )
    request = Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "apiKey": (row.sms_api_secret or "").strip(),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        return _sms_result([{"ok": False, "error": _read_error_body(exc)}])
    except URLError as exc:
        return _sms_result(
            [{"ok": False, "error": f"Could not reach Africa's Talking: {exc.reason}"}]
        )
    except Exception as exc:
        return _sms_result([{"ok": False, "error": str(exc)}])
    data = payload.get("SMSMessageData") or {}
    recipients = data.get("Recipients") or []
    results = []
    for item in recipients:
        if not isinstance(item, dict):
            continue
        ok = (
            str(item.get("statusCode") or "") == "101"
            or str(item.get("status") or "").lower() == "success"
        )
        status = str(item.get("status") or "")
        row_result = {
            "ok": ok,
            "phone": str(item.get("number") or ""),
            "status": status,
            "message_id": str(item.get("messageId") or ""),
            "cost": str(item.get("cost") or ""),
            "error": "" if ok else status or "Send failed",
        }
        if not ok:
            row_result["retryable"] = _sms_failure_retryable(status)
        results.append(row_result)
    if not results:
        message = str(data.get("Message") or "Empty response from Africa's Talking.")
        return _sms_result([{"ok": False, "error": message}])
    return _sms_result(results)


def _twilio_sms_send(row, numbers: list[str], text: str) -> dict[str, Any]:
    from .twilio import _clean_account_sid, _submit_twilio_message

    sid = _clean_account_sid(row.sms_api_key or "") or _clean_account_sid(
        row.twilio_account_sid or ""
    )
    token = (row.sms_api_secret or "").strip() or (row.twilio_auth_token or "").strip()
    from_number = (row.twilio_from_number or "").strip() or (
        row.sms_sender_id or ""
    ).strip()
    if not sid or not token or not from_number:
        return _sms_result(
            [
                {
                    "ok": False,
                    "error": "Twilio Account SID, Auth Token, and From number are required.",
                }
            ]
        )
    results = []
    for number in numbers:
        sent = _submit_twilio_message(
            account_sid=sid,
            auth_token=token,
            from_value=from_number,
            to_value=f"+{number}",
            text=text,
            skip_poll=True,
        )
        results.append(
            {
                "ok": bool(sent.get("ok")),
                "phone": f"+{number}",
                "status": str(sent.get("status") or ""),
                "message_id": str(sent.get("messageId") or ""),
                "error": str(sent.get("error") or ""),
            }
        )
    return _sms_result(results)


def send_sms(*, phone: str, message: str, row=None) -> dict[str, Any]:
    """Send a text via the configured SMS provider.

    phone may be a comma-separated list of numbers. Returns
    {ok, results, error, messageId}.
    """
    from shops.services import _normalize_phone, get_communications_settings

    row = row or get_communications_settings()
    text = (message or "").strip()
    if not text:
        return {"ok": False, "results": [], "error": "Message is required.", "retryable": False}
    numbers = []
    for part in str(phone or "").split(","):
        normalized = _normalize_phone(part)
        if normalized and normalized not in numbers:
            numbers.append(normalized)
    if not numbers:
        return {
            "ok": False,
            "results": [],
            "error": "No valid recipient phone number.",
            "retryable": False,
        }
    if not row.has_sms_credentials():
        return {
            "ok": False,
            "results": [],
            "error": "SMS credentials are not configured.",
            "retryable": False,
        }

    provider = (row.sms_provider or "").strip()
    if provider == SmsProvider.BLESSED_TEXTS:
        return _blessed_texts_send(row, numbers, text)
    if provider == SmsProvider.AFRICAS_TALKING:
        return _africas_talking_send(row, numbers, text)
    if provider == SmsProvider.TWILIO:
        return _twilio_sms_send(row, numbers, text)
    return {
        "ok": False,
        "results": [],
        "error": f"Unsupported SMS provider: {provider or 'none'}.",
        "retryable": False,
    }
