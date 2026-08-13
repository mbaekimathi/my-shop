"""Campaign create + enqueue helpers."""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from .bridge import bridge_state_as_dict, fetch_bridge_status
from .constants import (
    BRIDGE_STATUS_CONNECTED,
    CAMPAIGN_QUEUED,
    CAMPAIGN_SENDING,
    MSG_PENDING,
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
        raise ValueError("WhatsApp is not connected. Scan the QR code first.")

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


def campaign_as_dict(campaign: BroadcastCampaign) -> dict[str, Any]:
    messages = list(
        campaign.messages.order_by("-updated_at", "id")[:100].values(
            "id",
            "client_id",
            "client_name",
            "phone",
            "body",
            "status",
            "attempt_count",
            "error",
            "sent_at",
            "created_at",
            "updated_at",
        )
    )
    for row in messages:
        for key in ("sent_at", "created_at", "updated_at"):
            if row.get(key):
                row[key] = row[key].isoformat()
    return {
        "id": campaign.pk,
        "status": campaign.status,
        "body_template": campaign.body_template,
        "filters": campaign.filters or {},
        "recipient_count": campaign.recipient_count,
        "sent_count": campaign.sent_count,
        "failed_count": campaign.failed_count,
        "has_image": bool(campaign.image),
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "messages": messages,
        "bridge": bridge_state_as_dict(),
    }


def recent_campaigns(limit: int = 10) -> list[dict[str, Any]]:
    rows = BroadcastCampaign.objects.order_by("-created_at")[:limit]
    return [
        {
            "id": c.pk,
            "status": c.status,
            "recipient_count": c.recipient_count,
            "sent_count": c.sent_count,
            "failed_count": c.failed_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "body_preview": (c.body_template or "")[:80],
        }
        for c in rows
    ]


def refresh_campaign_counts(campaign: BroadcastCampaign) -> None:
    from .constants import MSG_FAILED, MSG_MANUAL_REVIEW, MSG_SENT

    sent = campaign.messages.filter(status=MSG_SENT).count()
    failed = campaign.messages.filter(
        status__in=[MSG_FAILED, MSG_MANUAL_REVIEW]
    ).count()
    campaign.sent_count = sent
    campaign.failed_count = failed
    if campaign.status == CAMPAIGN_SENDING and not campaign.messages.filter(
        status=MSG_PENDING
    ).exists():
        from .constants import CAMPAIGN_DONE

        campaign.status = CAMPAIGN_DONE
        campaign.finished_at = timezone.now()
    campaign.save(
        update_fields=[
            "sent_count",
            "failed_count",
            "status",
            "finished_at",
        ]
    )
