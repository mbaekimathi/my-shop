"""Customer email / Message-channel delivery via Django SMTP."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)


def send_customer_email(
    *,
    to_email: str,
    body: str,
    subject: str = "",
    from_name: str = "",
    reply_to: str = "",
) -> dict:
    """Send a plain-text customer notice. Returns {ok, error?}."""
    destination = (to_email or "").strip()
    text = (body or "").strip()
    if not destination or "@" not in destination:
        return {"ok": False, "error": "Customer email is missing.", "retryable": False}
    if not text:
        return {"ok": False, "error": "Message body is empty.", "retryable": False}

    display = (from_name or "").strip() or "MY-SHOP"
    default_from = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    if not default_from:
        default_from = "noreply@localhost"
    from_email = f"{display} <{default_from}>"
    reply = (reply_to or "").strip()
    headers = {}
    if reply:
        headers["Reply-To"] = reply

    try:
        message = EmailMessage(
            subject=(subject or "Order update").strip()[:200],
            body=text,
            from_email=from_email,
            to=[destination],
            headers=headers or None,
        )
        message.send(fail_silently=False)
    except Exception as exc:
        logger.exception("Customer email failed to %s", destination)
        return {"ok": False, "error": str(exc) or "Email send failed.", "retryable": True}
    return {"ok": True}
