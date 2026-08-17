"""Campaign create + enqueue helpers."""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from .twilio import bridge_state_as_dict, fetch_bridge_status
from .constants import (
    BRIDGE_STATUS_CONNECTED,
    CAMPAIGN_CANCELLED,
    CAMPAIGN_DONE,
    CAMPAIGN_QUEUED,
    CAMPAIGN_SENDING,
    MSG_CANCELLED,
    MSG_FAILED,
    MSG_MANUAL_REVIEW,
    MSG_PENDING,
    MSG_SENT,
)
from .models import BroadcastCampaign, OutboundMessage
from .services import constrain_filters_to_profile, query_recipients, render_placeholders

logger = logging.getLogger(__name__)


def create_campaign(
    *,
    profile,
    body_template: str,
    filters: dict | None = None,
    image: UploadedFile | None = None,
) -> BroadcastCampaign:
    body = (body_template or "").strip()
    if not body and not image:
        raise ValueError("Message text or an image is required.")

    status_payload = fetch_bridge_status()
    if status_payload.get("status") != BRIDGE_STATUS_CONNECTED:
        raise ValueError(
            "Twilio is not configured. Save Account SID, Auth Token, and a From number in Settings."
        )

    parsed = constrain_filters_to_profile(filters, profile)
    recipients = query_recipients(parsed)
    if not recipients:
        if parsed.get("client_ids"):
            raise ValueError("None of the selected contacts match this audience.")
        raise ValueError("No recipients match the selected audience.")

    with transaction.atomic():
        campaign = BroadcastCampaign.objects.create(
            created_by=profile,
            body_template=body,
            filters=parsed,
            status=CAMPAIGN_QUEUED,
            recipient_count=len(recipients),
        )
        if image:
            campaign.image = image
            campaign.save(update_fields=["image"])

        image_path = ""
        if campaign.image:
            try:
                image_path = campaign.image.path
            except Exception:
                image_path = campaign.image.name

        OutboundMessage.objects.bulk_create(
            [
                OutboundMessage(
                    campaign=campaign,
                    client_id=r.client_id,
                    client_name=r.full_name,
                    phone=(r.chat_id or r.phone),
                    body=render_placeholders(body, r),
                    image_path=image_path,
                    status=MSG_PENDING,
                )
                for r in recipients
            ]
        )

    enqueue_campaign(campaign.pk)
    return campaign


CATALOGUE_SEND_ITEM_LIMIT = 24


def item_whatsapp_media_url(item, *, request=None) -> str:
    """Public https URL Twilio can fetch. Local file paths are ignored."""
    if not getattr(item, "image", None):
        return ""
    try:
        path = (item.image.url or "").strip()
    except Exception:
        return ""
    if not path:
        return ""
    if path.lower().startswith(("http://", "https://")):
        url = path[:500]
    else:
        from shops.credit_note import public_site_origin

        origin = (public_site_origin(request=request) or "").rstrip("/")
        if not origin:
            return ""
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{origin}{path}"[:500]
    from .twilio import is_twilio_fetchable_url

    if not is_twilio_fetchable_url(url):
        return ""
    return url


def create_catalogue_campaign(
    *,
    profile,
    items,
    filters: dict | None = None,
    request=None,
) -> BroadcastCampaign:
    """One WhatsApp message per recipient × item (caption + public image when available)."""
    from .automations import build_item_catalogue_caption

    chosen = []
    seen = set()
    for item in items or []:
        pk = getattr(item, "pk", None)
        if not pk or pk in seen:
            continue
        if getattr(item, "is_suspended", False):
            continue
        seen.add(pk)
        chosen.append(item)
    if not chosen:
        raise ValueError("Select at least one item to share.")
    if len(chosen) > CATALOGUE_SEND_ITEM_LIMIT:
        raise ValueError(
            f"Select at most {CATALOGUE_SEND_ITEM_LIMIT} items in one send."
        )

    status_payload = fetch_bridge_status()
    if status_payload.get("status") != BRIDGE_STATUS_CONNECTED:
        raise ValueError(
            "Twilio is not configured. Save Account SID, Auth Token, and a From number in Settings."
        )

    parsed = constrain_filters_to_profile(filters, profile)
    recipients = query_recipients(parsed)
    if not recipients:
        if parsed.get("client_ids"):
            raise ValueError("None of the selected contacts match this audience.")
        raise ValueError("No recipients match the selected audience.")

    parts = []
    for item in chosen:
        caption = build_item_catalogue_caption(item)
        if not caption:
            continue
        parts.append((item, caption, item_whatsapp_media_url(item, request=request)))
    if not parts:
        raise ValueError("Select at least one item to share.")

    if len(parts) == 1:
        body_template = parts[0][1]
    else:
        names = ", ".join((item.name or "Item").strip() for item, _, _ in parts[:6])
        extra = len(parts) - 6
        if extra > 0:
            names = f"{names} +{extra} more"
        body_template = f"Share {len(parts)} items: {names}"

    parsed = {**parsed, "item_ids": [item.pk for item, _, _ in parts]}
    with transaction.atomic():
        campaign = BroadcastCampaign.objects.create(
            created_by=profile,
            body_template=body_template,
            filters=parsed,
            status=CAMPAIGN_QUEUED,
            recipient_count=len(recipients),
        )
        OutboundMessage.objects.bulk_create(
            [
                OutboundMessage(
                    campaign=campaign,
                    client_id=recipient.client_id,
                    client_name=recipient.full_name,
                    phone=(recipient.chat_id or recipient.phone),
                    body=render_placeholders(caption, recipient),
                    image_path=media,
                    status=MSG_PENDING,
                )
                for recipient in recipients
                for _item, caption, media in parts
            ]
        )

    enqueue_campaign(campaign.pk)
    return campaign


def enqueue_campaign(campaign_id: int) -> None:
    """
    Start sending via Celery, inline thread, or leave queued for cPanel cron.

    Modes (COMMS_SEND_MODE):
      auto    — Celery if Redis; else inline thread on DEBUG; else cron queue
      celery  — require Redis/Celery
      inline  — background thread in this process
      cron    — leave QUEUED for `manage.py process_comms_queue`
    """
    mode = (getattr(settings, "COMMS_SEND_MODE", "auto") or "auto").strip().lower()

    if mode in {"auto", "celery"} and getattr(settings, "REDIS_ENABLED", False):
        try:
            from .tasks import process_campaign

            process_campaign.delay(campaign_id)
            return
        except Exception as exc:
            logger.warning("Celery enqueue failed for campaign %s: %s", campaign_id, exc)
            if mode == "celery":
                raise RuntimeError(
                    "Cannot start send queue: Celery/Redis failed. "
                    "Check REDIS_URL and the Celery worker."
                ) from exc

    if mode == "cron":
        logger.info("Campaign %s waiting for cron worker (process_comms_queue)", campaign_id)
        return

    if mode == "inline" or (
        mode == "auto"
        and (
            getattr(settings, "DEBUG", False)
            or getattr(settings, "COMMS_INLINE_SEND", False)
        )
    ):
        thread = threading.Thread(
            target=_run_campaign_inline,
            args=(campaign_id,),
            daemon=True,
            name=f"comms-campaign-{campaign_id}",
        )
        thread.start()
        return

    if mode == "auto":
        # Hosted without Redis: rely on cPanel/VPS cron.
        logger.info(
            "Campaign %s queued for cron (set REDIS_URL or COMMS_INLINE_SEND=1 to send immediately)",
            campaign_id,
        )
        return

    raise RuntimeError(
        "Cannot start send queue. Set REDIS_URL, COMMS_INLINE_SEND=1, "
        "or run: python manage.py process_comms_queue"
    )


def _run_campaign_inline(campaign_id: int) -> None:
    from .tasks import process_campaign_sync

    try:
        process_campaign_sync(campaign_id)
    except Exception:
        logger.exception("Inline campaign %s failed", campaign_id)


def outbound_display_status(
    *,
    status: str,
    read_at=None,
    delivered_at=None,
) -> str:
    if status == MSG_CANCELLED:
        return "cancelled"
    if status in {MSG_FAILED, MSG_MANUAL_REVIEW}:
        return "failed"
    if read_at:
        return "viewed"
    if delivered_at:
        return "delivered"
    if status == MSG_SENT:
        return "sent"
    return "queued"


def cancel_campaign(campaign_id: int) -> BroadcastCampaign:
    with transaction.atomic():
        campaign = BroadcastCampaign.objects.select_for_update().filter(pk=campaign_id).first()
        if campaign is None:
            raise ValueError("Send not found.")
        if campaign.status not in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING}:
            raise ValueError("This send has already finished.")
        pending = campaign.messages.filter(status=MSG_PENDING)
        if not pending.exists():
            raise ValueError("Nothing left to cancel — messages already left the queue.")
        now = timezone.now()
        pending.update(status=MSG_CANCELLED, error="Cancelled", updated_at=now)
        campaign.status = CAMPAIGN_CANCELLED
        campaign.finished_at = now
        campaign.save(update_fields=["status", "finished_at"])
    refresh_campaign_counts(campaign)
    campaign.refresh_from_db()
    return campaign


def retry_failed_campaign(campaign_id: int) -> BroadcastCampaign:
    with transaction.atomic():
        campaign = (
            BroadcastCampaign.objects.select_for_update().filter(pk=campaign_id).first()
        )
        if campaign is None:
            raise ValueError("Send not found.")
        if campaign.status == CAMPAIGN_CANCELLED:
            raise ValueError("This send was cancelled.")
        failed = list(
            campaign.messages.filter(status__in=[MSG_FAILED, MSG_MANUAL_REVIEW])
        )
        if not failed:
            raise ValueError("No failed messages to retry.")
        now = timezone.now()
        for row in failed:
            body = (row.body or "").strip() or campaign.body_template
            OutboundMessage.objects.filter(pk=row.pk).update(
                status=MSG_PENDING,
                error="",
                body=body,
                attempt_count=0,
                updated_at=now,
            )
        campaign.status = CAMPAIGN_QUEUED
        campaign.finished_at = None
        campaign.save(update_fields=["status", "finished_at"])
    refresh_campaign_counts(campaign)
    enqueue_campaign(campaign.pk)
    campaign.refresh_from_db()
    return campaign


def campaign_as_dict(campaign: BroadcastCampaign) -> dict[str, Any]:
    messages = list(
        campaign.messages.order_by("id")[:500].values(
            "id",
            "client_id",
            "client_name",
            "phone",
            "status",
            "error",
            "sent_at",
            "delivered_at",
            "read_at",
            "created_at",
            "updated_at",
        )
    )
    for row in messages:
        for key in ("sent_at", "delivered_at", "read_at", "created_at", "updated_at"):
            if row.get(key):
                row[key] = row[key].isoformat()
        row["display_status"] = outbound_display_status(
            status=row.get("status") or "",
            read_at=row.get("read_at"),
            delivered_at=row.get("delivered_at"),
        )
    pending_count = campaign.messages.filter(status=MSG_PENDING).count()
    delivered_count = campaign.messages.filter(delivered_at__isnull=False).count()
    viewed_count = campaign.messages.filter(read_at__isnull=False).count()
    cancelled_count = campaign.messages.filter(status=MSG_CANCELLED).count()
    return {
        "id": campaign.pk,
        "status": campaign.status,
        "body_template": campaign.body_template,
        "body_preview": (campaign.body_template or "").replace("\n", " ")[:80],
        "filters": campaign.filters or {},
        "recipient_count": campaign.recipient_count,
        "sent_count": campaign.sent_count,
        "failed_count": campaign.failed_count,
        "pending_count": pending_count,
        "delivered_count": delivered_count,
        "viewed_count": viewed_count,
        "cancelled_count": cancelled_count,
        "can_cancel": campaign.status in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING}
        and pending_count > 0,
        "can_retry": campaign.messages.filter(
            status__in=[MSG_FAILED, MSG_MANUAL_REVIEW]
        ).exists(),
        "has_image": bool(campaign.image),
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "messages": messages,
        "bridge": bridge_state_as_dict(),
    }


def recent_campaigns(limit: int = 10) -> list[dict[str, Any]]:
    rows = list(BroadcastCampaign.objects.order_by("-created_at")[:limit])
    return [campaign_as_dict(c) for c in rows]


def refresh_campaign_counts(campaign: BroadcastCampaign) -> None:
    sent = campaign.messages.filter(status=MSG_SENT).count()
    failed = campaign.messages.filter(
        status__in=[MSG_FAILED, MSG_MANUAL_REVIEW]
    ).count()
    campaign.sent_count = sent
    campaign.failed_count = failed
    update_fields = ["sent_count", "failed_count"]
    if campaign.status == CAMPAIGN_CANCELLED:
        campaign.save(update_fields=update_fields)
        return
    if campaign.status in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING} and not campaign.messages.filter(
        status=MSG_PENDING
    ).exists():
        campaign.status = CAMPAIGN_DONE
        campaign.finished_at = timezone.now()
        update_fields.extend(["status", "finished_at"])
    campaign.save(update_fields=update_fields)
