"""HTTP client for the WhatsApp bridge (local VPS or remote helper)."""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import urllib.error
import urllib.request
import json

from django.conf import settings

from .constants import (
    BRIDGE_STATUS_CONNECTED,
    BRIDGE_STATUS_DISCONNECTED,
    BRIDGE_STATUS_QR_PENDING,
)
from .models import WhatsAppBridgeState

logger = logging.getLogger(__name__)


def _bridge_base_url() -> str:
    return (getattr(settings, "WHATSAPP_BRIDGE_URL", "") or "http://127.0.0.1:3100").rstrip(
        "/"
    )


def _bridge_secret() -> str:
    return (getattr(settings, "WHATSAPP_BRIDGE_SECRET", "") or "").strip()


def bridge_is_local() -> bool:
    host = (urlparse(_bridge_base_url()).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"} or not host


def bridge_deploy_hints() -> dict[str, str]:
    """UI copy for VPS (local bridge) vs cPanel (remote bridge)."""
    from .launcher import autostart_enabled

    local = bridge_is_local()
    if local and autostart_enabled():
        return {
            "mode": "local-auto",
            "connect_help": "WhatsApp is starting automatically on this server. Scan the QR when it appears.",
            "help_text": "Starting WhatsApp helper automatically…",
            "cmd": "",
            "note": "No manual start needed. Keep Node.js installed on the server; you only scan the QR once.",
            "autostart": True,
        }
    if local:
        return {
            "mode": "local",
            "connect_help": "Start the WhatsApp helper on this server, then scan the QR code with your phone.",
            "help_text": "WhatsApp helper is not running yet.",
            "cmd": "cd whatsapp-bridge\nnpm start",
            "note": "On a VPS, keep the helper running with systemd or PM2. You only scan the QR once.",
            "autostart": False,
        }
    return {
        "mode": "remote",
        "connect_help": "This site talks to a remote WhatsApp helper. Keep that helper online, then scan the QR here.",
        "help_text": "Cannot reach the remote WhatsApp helper yet.",
        "cmd": (
            "# On the VPS / PC that runs WhatsApp Web:\n"
            "cd whatsapp-bridge\n"
            "set WHATSAPP_BRIDGE_HOST=0.0.0.0\n"
            "set WHATSAPP_BRIDGE_SECRET=your-secret\n"
            "npm start\n"
            "# Point Django WHATSAPP_BRIDGE_URL at that host (HTTPS tunnel OK)"
        ),
        "note": (
            "Shared cPanel cannot run Chrome. Run the helper on a small VPS or office PC, "
            "set the same WHATSAPP_BRIDGE_SECRET on both sides, and use a public HTTPS URL "
            "(or Cloudflare Tunnel / ngrok)."
        ),
        "autostart": False,
    }


def _request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{_bridge_base_url()}{path}"
    data = None
    headers = {"Accept": "application/json"}
    secret = _bridge_secret()
    if secret:
        headers["X-Bridge-Secret"] = secret
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            pass
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"error": body or str(exc)}
        parsed.setdefault("ok", False)
        parsed.setdefault("error", str(exc))
        parsed["_http_status"] = exc.code
        return parsed
    except Exception as exc:
        logger.warning("WhatsApp bridge request failed: %s %s — %s", method, path, exc)
        msg = str(exc)
        if "10061" in msg or "actively refused" in msg.lower() or "Connection refused" in msg:
            if bridge_is_local():
                msg = "WhatsApp helper is not running on this computer."
            else:
                msg = (
                    "Cannot reach the remote WhatsApp helper. "
                    "Check WHATSAPP_BRIDGE_URL and that the helper is online."
                )
        return {"ok": False, "error": msg, "unreachable": True}


def fetch_bridge_status() -> dict[str, Any]:
    """Poll the Node bridge and mirror state into WhatsAppBridgeState."""
    result = _request("GET", "/status", timeout=3.0)
    row = WhatsAppBridgeState.get_solo()

    if result.get("unreachable") or (
        not result.get("ok", True) and "state" not in result
    ):
        from .launcher import ensure_bridge_running

        launch = ensure_bridge_running()
        if launch.get("started") or launch.get("booting"):
            # Give Chromium a moment, then re-check.
            import time

            time.sleep(1.2)
            result = _request("GET", "/status", timeout=3.0)

        if result.get("unreachable") or (
            not result.get("ok", True) and "state" not in result
        ):
            row.status = BRIDGE_STATUS_DISCONNECTED
            row.qr_data_url = ""
            row.wa_phone = ""
            if launch.get("error"):
                row.last_error = str(launch["error"])
            elif launch.get("booting") or launch.get("started"):
                row.last_error = "WhatsApp helper is starting…"
            else:
                row.last_error = result.get("error") or "Bridge unreachable"
            row.save(
                update_fields=[
                    "status",
                    "qr_data_url",
                    "wa_phone",
                    "last_error",
                    "updated_at",
                ]
            )
            payload = bridge_state_as_dict(row)
            payload.update(bridge_deploy_hints())
            payload["launch"] = launch
            return payload

    state = (result.get("state") or "").strip().lower()
    row.qr_data_url = (result.get("qr_data_url") or result.get("qr") or "")[:200000]
    row.wa_phone = str(result.get("phone") or "")[:40]
    row.last_error = (result.get("error") or "").strip()

    if state == BRIDGE_STATUS_CONNECTED:
        row.status = BRIDGE_STATUS_CONNECTED
    elif state == BRIDGE_STATUS_QR_PENDING or bool(row.qr_data_url):
        row.status = BRIDGE_STATUS_QR_PENDING
    else:
        row.status = BRIDGE_STATUS_DISCONNECTED

    row.save(
        update_fields=[
            "status",
            "qr_data_url",
            "wa_phone",
            "last_error",
            "updated_at",
        ]
    )
    payload = bridge_state_as_dict(row)
    payload.update(bridge_deploy_hints())
    return payload


def bridge_state_as_dict(row: WhatsAppBridgeState | None = None) -> dict[str, Any]:
    row = row or WhatsAppBridgeState.get_solo()
    payload = {
        "status": row.status,
        "qr_data_url": row.qr_data_url or "",
        "wa_phone": row.wa_phone or "",
        "last_error": row.last_error or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "connected": row.status == BRIDGE_STATUS_CONNECTED,
    }
    payload.update(bridge_deploy_hints())
    return payload


def _attach_media(payload: dict[str, Any], media_path: str | None) -> None:
    """Prefer base64 so cPanel Django can send images to a remote bridge."""
    path_text = (media_path or "").strip()
    if not path_text:
        return
    path = Path(path_text)
    if path.is_file():
        raw = path.read_bytes()
        # Cap ~4MB to keep shared-host request bodies reasonable.
        max_bytes = int(getattr(settings, "WHATSAPP_MEDIA_MAX_BYTES", 4 * 1024 * 1024))
        if len(raw) > max_bytes:
            raise ValueError("Image is too large to send. Use a photo under 4 MB.")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        payload["mediaBase64"] = base64.b64encode(raw).decode("ascii")
        payload["mediaMime"] = mime
        payload["mediaFilename"] = path.name[:120]
        return
    # Same-machine fallback when the file path is only valid on the bridge host.
    if bridge_is_local():
        payload["mediaPath"] = path_text


def send_whatsapp_message(
    *,
    phone: str = "",
    text: str,
    media_path: str | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": text or ""}
    if chat_id:
        payload["chatId"] = chat_id
    if phone:
        payload["phone"] = phone
    try:
        _attach_media(payload, media_path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result = _request("POST", "/send", payload=payload, timeout=90.0)
    if result.get("ok"):
        return result
    return {
        "ok": False,
        "error": result.get("error") or "Send failed",
        "unreachable": bool(result.get("unreachable")),
    }


def fetch_whatsapp_contacts(
    *,
    search: str = "",
    include_groups: bool = True,
) -> dict[str, Any]:
    """Pull contacts + groups from the linked personal WhatsApp account."""
    from urllib.parse import urlencode

    query = urlencode(
        {
            "includeGroups": "1" if include_groups else "0",
            "q": (search or "").strip(),
        }
    )
    result = _request("GET", f"/contacts?{query}", timeout=120.0)
    if result.get("unreachable"):
        return {
            "ok": False,
            "error": result.get("error") or "WhatsApp helper is not running.",
            "contacts": [],
            "groups": [],
        }
    if not result.get("ok", True) and "contacts" not in result:
        err = result.get("error") or "Could not load WhatsApp contacts."
        if "Cannot GET /contacts" in str(err):
            err = (
                "WhatsApp helper is outdated. Stop it and run `npm start` again "
                "in the whatsapp-bridge folder."
            )
        return {
            "ok": False,
            "error": err,
            "contacts": [],
            "groups": [],
        }
    contacts = result.get("contacts") or []
    groups = result.get("groups") or []
    return {
        "ok": True,
        "count": len(contacts),
        "group_count": len(groups),
        "contacts": contacts,
        "groups": groups,
        "error": "",
    }


def logout_bridge() -> dict[str, Any]:
    result = _request("POST", "/logout", payload={}, timeout=30.0)
    row = WhatsAppBridgeState.get_solo()
    row.status = BRIDGE_STATUS_DISCONNECTED
    row.qr_data_url = ""
    row.wa_phone = ""
    row.last_error = "" if result.get("ok") else (result.get("error") or "")
    row.save(
        update_fields=[
            "status",
            "qr_data_url",
            "wa_phone",
            "last_error",
            "updated_at",
        ]
    )
    return {"ok": bool(result.get("ok")), "error": result.get("error") or ""}
