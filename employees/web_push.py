"""Web Push helpers — VAPID keys, subscription storage, and delivery."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.urls import reverse

logger = logging.getLogger(__name__)


def _keys_file() -> Path:
    return Path(settings.BASE_DIR) / ".vapid_keys.json"


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def generate_vapid_keys() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    public_raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(public_raw), _b64url(private_raw)


def get_vapid_keys() -> tuple[str, str] | tuple[None, None]:
    public = (getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", "") or "").strip()
    private = (getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "") or "").strip()
    if public and private:
        return public, private

    path = _keys_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            public = (data.get("public_key") or "").strip()
            private = (data.get("private_key") or "").strip()
            if public and private:
                return public, private
        except Exception:
            logger.exception("Failed reading %s", path)

    try:
        public, private = generate_vapid_keys()
        path.write_text(
            json.dumps(
                {"public_key": public, "private_key": private},
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.warning(
            "Generated Web Push VAPID keys at %s — set WEB_PUSH_VAPID_* in .env for production.",
            path,
        )
        return public, private
    except Exception:
        logger.exception("Unable to generate Web Push VAPID keys")
        return None, None


def vapid_public_key() -> str:
    public, _private = get_vapid_keys()
    return public or ""


def vapid_claims() -> dict:
    contact = (
        getattr(settings, "WEB_PUSH_VAPID_ADMIN_EMAIL", "") or ""
    ).strip() or "mailto:admin@localhost"
    if not contact.startswith("mailto:"):
        contact = f"mailto:{contact}"
    return {"sub": contact}


def web_push_enabled() -> bool:
    public, private = get_vapid_keys()
    return bool(public and private)


def _subscription_info(row) -> dict:
    return {
        "endpoint": row.endpoint,
        "keys": {
            "p256dh": row.p256dh,
            "auth": row.auth,
        },
    }


def send_web_push_payload(
    *,
    subscriptions,
    title: str,
    body: str = "",
    url: str = "/",
    tag: str = "",
    icon: str = "/static/icons/myshop-192.png",
) -> int:
    """Send a notification to each subscription. Returns success count."""
    if not web_push_enabled():
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush is not installed; skipping Web Push send")
        return 0

    public, private = get_vapid_keys()
    if not public or not private:
        return 0

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": url or "/",
            "tag": tag or "myshop",
            "icon": icon,
            "badge": "/static/icons/myshop-192.png",
        }
    )
    claims = vapid_claims()
    sent = 0
    stale_ids: list[int] = []

    for row in subscriptions:
        try:
            webpush(
                subscription_info=_subscription_info(row),
                data=payload,
                vapid_private_key=private,
                vapid_claims=claims,
                vapid_public_key=public,
                ttl=60 * 60,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                stale_ids.append(int(row.pk))
            else:
                logger.info("Web Push failed for %s: %s", row.pk, exc)
        except Exception:
            logger.exception("Web Push unexpected error for subscription %s", row.pk)

    if stale_ids:
        from .models import WebPushSubscription

        WebPushSubscription.objects.filter(pk__in=stale_ids).delete()

    return sent


def subscriptions_for_employees(employee_ids):
    from .models import WebPushSubscription

    ids = [int(pk) for pk in employee_ids if pk]
    if not ids:
        return WebPushSubscription.objects.none()
    return WebPushSubscription.objects.filter(employee_id__in=ids)


def subscriptions_for_shop(shop_id: int):
    from .models import WebPushSubscription

    return WebPushSubscription.objects.filter(
        Q(shop_id=shop_id) | Q(employee__assigned_shops__id=shop_id)
    ).distinct()


def notify_employees(
    employees,
    *,
    title: str,
    body: str = "",
    url: str = "/",
    tag: str = "",
) -> int:
    ids = []
    for row in employees or []:
        pk = getattr(row, "pk", row)
        if pk:
            ids.append(int(pk))
    return send_web_push_payload(
        subscriptions=subscriptions_for_employees(ids),
        title=title,
        body=body,
        url=url,
        tag=tag,
    )


def notify_shop(
    shop,
    *,
    title: str,
    body: str = "",
    url: str = "/",
    tag: str = "",
) -> int:
    shop_id = int(getattr(shop, "pk", shop) or 0)
    if not shop_id:
        return 0
    return send_web_push_payload(
        subscriptions=subscriptions_for_shop(shop_id),
        title=title,
        body=body,
        url=url,
        tag=tag,
    )


def notify_new_delivery(delivery) -> int:
    """Push open delivery alerts to eligible riders."""
    from .models import EmployeeProfile, EmployeeRole, EmployeeStatus

    assigned = list(delivery.assigned_riders.all())
    if assigned:
        riders = assigned
    else:
        riders = list(
            EmployeeProfile.objects.filter(
                role=EmployeeRole.RIDER,
                status=EmployeeStatus.ACTIVE,
            )
        )
    if not riders:
        return 0

    shop_name = getattr(getattr(delivery, "shop", None), "name", "") or "Shop"
    receipt_no = getattr(getattr(delivery, "receipt", None), "receipt_number", "") or ""
    location = (getattr(delivery, "location_text", "") or "").strip()
    body_parts = [shop_name]
    if receipt_no:
        body_parts.append(receipt_no)
    if location:
        body_parts.append(location)
    try:
        url = reverse("employees:role_rider")
    except Exception:
        url = "/rider/"
    return notify_employees(
        riders,
        title="New delivery request",
        body=" · ".join(body_parts),
        url=url,
        tag=f"delivery-{delivery.pk}",
    )


def notify_stock_request_created(movement) -> int:
    """Alert the receiving shop that a transfer request arrived."""
    receiver = getattr(movement, "requested_from_shop", None)
    if receiver is None:
        return 0
    sender_name = getattr(getattr(movement, "shop", None), "name", "") or "Another shop"
    try:
        url = reverse(
            "employees:my_shop_stock_requests",
            kwargs={"shop_id": receiver.pk},
        )
    except Exception:
        url = "/"
    return notify_shop(
        receiver,
        title="Incoming stock transfer",
        body=f"Request from {sender_name}",
        url=url,
        tag=f"stock-req-{movement.pk}",
    )


def notify_stock_request_decided(movement) -> int:
    """Alert the requesting shop when a transfer is accepted or declined."""
    sender = getattr(movement, "shop", None)
    if sender is None:
        return 0
    status = (getattr(movement, "request_status", "") or "").strip().lower()
    if status == "fulfilled":
        title = "Stock transfer accepted"
        verb = "accepted"
    elif status == "declined":
        title = "Stock transfer declined"
        verb = "declined"
    else:
        return 0
    other = getattr(getattr(movement, "requested_from_shop", None), "name", "") or "shop"
    try:
        url = reverse(
            "employees:my_shop_stock_requests",
            kwargs={"shop_id": sender.pk},
        )
    except Exception:
        url = "/"
    return notify_shop(
        sender,
        title=title,
        body=f"{other} {verb} your transfer request",
        url=url,
        tag=f"stock-dec-{movement.pk}",
    )
