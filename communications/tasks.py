"""Celery / sync / cron processor for staggered WhatsApp sends."""

from __future__ import annotations

import logging
import random
import time

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .bridge import send_whatsapp_message
from .campaigns import refresh_campaign_counts
from .constants import (
    CAMPAIGN_DONE,
    CAMPAIGN_QUEUED,
    CAMPAIGN_SENDING,
    MAX_SEND_ATTEMPTS,
    MSG_MANUAL_REVIEW,
    MSG_PENDING,
    MSG_SENT,
    SEND_DELAY_MAX_SECONDS,
    SEND_DELAY_MIN_SECONDS,
)
from .models import BroadcastCampaign, OutboundMessage
from .replies import note_outbound_sent

logger = logging.getLogger(__name__)


def _finish_campaign_if_complete(campaign: BroadcastCampaign) -> bool:
    """Mark done + drop image when no pending messages remain. Returns True if done."""
    refresh_campaign_counts(campaign)
    still_pending = OutboundMessage.objects.filter(
        campaign_id=campaign.pk, status=MSG_PENDING
    ).exists()
    if still_pending:
        if campaign.status != CAMPAIGN_SENDING:
            campaign.status = CAMPAIGN_SENDING
            campaign.save(update_fields=["status"])
        return False

    campaign.status = CAMPAIGN_DONE
    campaign.finished_at = timezone.now()
    campaign.save(update_fields=["status", "finished_at"])
    if campaign.image:
        try:
            campaign.image.delete(save=False)
        except Exception:
            pass
        campaign.image = None
        campaign.save(update_fields=["image"])
    return True


def process_campaign_sync(
    campaign_id: int,
    *,
    max_messages: int | None = None,
) -> str:
    campaign = BroadcastCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return f"campaign {campaign_id} missing"

    campaign.status = CAMPAIGN_SENDING
    if not campaign.started_at:
        campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at"])

    pending_qs = OutboundMessage.objects.filter(
        campaign_id=campaign_id, status=MSG_PENDING
    ).order_by("id")
    if max_messages is not None:
        pending = list(pending_qs[: max(0, int(max_messages))])
    else:
        pending = list(pending_qs)

    for index, message in enumerate(pending):
        if index > 0:
            delay = random.uniform(SEND_DELAY_MIN_SECONDS, SEND_DELAY_MAX_SECONDS)
            time.sleep(delay)
        _send_one(message)

    campaign = BroadcastCampaign.objects.get(pk=campaign_id)
    if _finish_campaign_if_complete(campaign):
        return f"campaign {campaign_id} done"
    return f"campaign {campaign_id} progress ({len(pending)} attempted)"


def process_pending_queue(*, limit: int | None = None) -> dict[str, int | str]:
    """
    Drain queued/sending campaigns in small batches.
    Safe for cPanel cron (short PHP/Python time limits).
    """
    budget = limit
    if budget is None:
        budget = int(getattr(settings, "COMMS_CRON_BATCH_SIZE", 15) or 15)
    budget = max(1, int(budget))

    campaigns = list(
        BroadcastCampaign.objects.filter(
            status__in=[CAMPAIGN_QUEUED, CAMPAIGN_SENDING]
        ).order_by("id")[:20]
    )
    attempted = 0
    finished = 0
    for campaign in campaigns:
        if attempted >= budget:
            break
        before = OutboundMessage.objects.filter(
            campaign_id=campaign.pk, status=MSG_PENDING
        ).count()
        if before <= 0:
            if _finish_campaign_if_complete(campaign):
                finished += 1
            continue
        take = min(before, budget - attempted)
        process_campaign_sync(campaign.pk, max_messages=take)
        after = OutboundMessage.objects.filter(
            campaign_id=campaign.pk, status=MSG_PENDING
        ).count()
        attempted += max(0, before - after)
        if after <= 0:
            finished += 1

    return {
        "ok": True,
        "attempted": attempted,
        "finished_campaigns": finished,
        "remaining_budget": max(0, budget - attempted),
    }


def _send_one(message: OutboundMessage) -> None:
    """Send once; on failure retry once after another random delay, then manual_review."""
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        if attempt > 1:
            delay = random.uniform(SEND_DELAY_MIN_SECONDS, SEND_DELAY_MAX_SECONDS)
            time.sleep(delay)

        media = (message.image_path or "").strip() or None
        dest = (message.phone or "").strip()
        kwargs = {
            "text": message.body,
            "media_path": media,
        }
        if "@g.us" in dest or "@c.us" in dest or "@lid" in dest:
            kwargs["chat_id"] = dest
        else:
            kwargs["phone"] = dest
        result = send_whatsapp_message(**kwargs)
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            locked.attempt_count = attempt
            if result.get("ok"):
                locked.status = MSG_SENT
                locked.error = ""
                locked.sent_at = timezone.now()
                locked.wa_message_id = str(result.get("messageId") or "")[:200]
                locked.wa_chat_id = str(result.get("chatId") or "")[:120]
                # Drop bulky payload after send — matching only needs ids/phone/time.
                locked.body = ""
                locked.image_path = ""
                locked.save(
                    update_fields=[
                        "attempt_count",
                        "status",
                        "error",
                        "sent_at",
                        "wa_message_id",
                        "wa_chat_id",
                        "body",
                        "image_path",
                        "updated_at",
                    ]
                )
                note_outbound_sent()
                return

            locked.error = (result.get("error") or "Send failed")[:2000]
            if attempt >= MAX_SEND_ATTEMPTS:
                locked.status = MSG_MANUAL_REVIEW
            else:
                locked.status = MSG_PENDING
            locked.save(
                update_fields=["attempt_count", "status", "error", "updated_at"]
            )

        if attempt >= MAX_SEND_ATTEMPTS:
            logger.warning(
                "OutboundMessage %s flagged for manual review: %s",
                message.pk,
                result.get("error"),
            )
            return


@shared_task(
    name="communications.process_campaign",
    bind=True,
    max_retries=0,
    soft_time_limit=60 * 60,
    time_limit=60 * 60 + 120,
)
def process_campaign(self, campaign_id: int) -> str:
    return process_campaign_sync(campaign_id)
