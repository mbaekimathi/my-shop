"""Celery / sync / cron processor for staggered WhatsApp sends."""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from celery import shared_task
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .twilio import (
    _db_safe_error,
    friendly_send_error,
    is_auth_error,
    is_retryable_error,
    send_whatsapp_message,
)
from .campaigns import promote_due_campaigns, refresh_campaign_counts
from .constants import (
    CAMPAIGN_CANCELLED,
    CAMPAIGN_DONE,
    CAMPAIGN_QUEUED,
    CAMPAIGN_SENDING,
    MAX_SEND_ATTEMPTS,
    MSG_FAILED,
    MSG_PENDING,
    MSG_SENT,
    SEND_CONCURRENCY,
    SEND_DELAY_MAX_SECONDS,
    SEND_DELAY_MIN_SECONDS,
    SEND_RETRY_BACKOFF_SECONDS,
)
from .models import BroadcastCampaign, OutboundMessage
from .replies import note_outbound_sent

logger = logging.getLogger(__name__)


def _finish_campaign_if_complete(campaign: BroadcastCampaign) -> bool:
    """Mark done + drop image when no pending messages remain. Returns True if done."""
    if campaign.status == CAMPAIGN_CANCELLED:
        return True
    refresh_campaign_counts(campaign)
    campaign.refresh_from_db()
    if campaign.status == CAMPAIGN_CANCELLED:
        return True
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
    with transaction.atomic():
        campaign = (
            BroadcastCampaign.objects.select_for_update()
            .filter(pk=campaign_id)
            .first()
        )
        if campaign is None:
            return f"campaign {campaign_id} missing"
        if campaign.status == CAMPAIGN_CANCELLED:
            return f"campaign {campaign_id} cancelled"
        if campaign.status not in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING}:
            return f"campaign {campaign_id} {campaign.status}"
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

    concurrency = max(1, int(getattr(settings, "COMMS_SEND_CONCURRENCY", SEND_CONCURRENCY) or SEND_CONCURRENCY))
    attempted = 0

    def _is_cancelled() -> bool:
        return (
            BroadcastCampaign.objects.filter(pk=campaign_id)
            .values_list("status", flat=True)
            .first()
            == CAMPAIGN_CANCELLED
        )

    def _worker(msg: OutboundMessage) -> None:
        try:
            close_old_connections()
            delay = random.uniform(SEND_DELAY_MIN_SECONDS, SEND_DELAY_MAX_SECONDS)
            time.sleep(delay)
            if _is_cancelled():
                return
            _send_one(msg, skip_poll=True)
        finally:
            close_old_connections()

    if concurrency <= 1:
        for index, message in enumerate(pending):
            if _is_cancelled():
                return f"campaign {campaign_id} cancelled"
            if index > 0:
                time.sleep(random.uniform(SEND_DELAY_MIN_SECONDS, SEND_DELAY_MAX_SECONDS))
                if _is_cancelled():
                    return f"campaign {campaign_id} cancelled"
            _send_one(message, skip_poll=True)
            attempted += 1
    else:
        pool = ThreadPoolExecutor(max_workers=concurrency)
        try:
            futures = {pool.submit(_worker, msg): msg for msg in pending}
            for future in as_completed(futures):
                attempted += 1
                if _is_cancelled():
                    pool.shutdown(wait=False, cancel_futures=True)
                    return f"campaign {campaign_id} cancelled"
                exc = future.exception()
                if exc:
                    logger.warning("Send worker error: %s", exc)
        except (KeyboardInterrupt, SystemExit):
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True)

    campaign = BroadcastCampaign.objects.get(pk=campaign_id)
    if _finish_campaign_if_complete(campaign):
        return f"campaign {campaign_id} done"
    return f"campaign {campaign_id} progress ({attempted} attempted)"


def process_pending_queue(*, limit: int | None = None) -> dict[str, int | str]:
    """
    Drain queued/sending campaigns in small batches.
    Safe for cPanel cron (short PHP/Python time limits).
    """
    budget = limit
    if budget is None:
        budget = int(getattr(settings, "COMMS_CRON_BATCH_SIZE", 15) or 15)
    budget = max(1, int(budget))
    promote_due_campaigns()

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


def _send_one(message: OutboundMessage, *, skip_poll: bool = False) -> None:
    """Retry a send until Twilio accepts it, or until the error cannot succeed."""
    last_error = ""
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        campaign_status = (
            BroadcastCampaign.objects.filter(pk=message.campaign_id)
            .values_list("status", flat=True)
            .first()
        )
        if campaign_status == CAMPAIGN_CANCELLED:
            return
        if attempt > 1:
            delay = SEND_RETRY_BACKOFF_SECONDS[
                min(attempt - 2, len(SEND_RETRY_BACKOFF_SECONDS) - 1)
            ]
            time.sleep(delay)

        media = (message.image_path or "").strip() or None
        dest = (message.phone or "").strip()
        kwargs = {
            "text": message.body,
            "media_path": media,
            "skip_poll": skip_poll,
        }
        if "@g.us" in dest or "@c.us" in dest or "@lid" in dest:
            kwargs["chat_id"] = dest
        else:
            kwargs["phone"] = dest
        result = send_whatsapp_message(**kwargs)
        last_error = _db_safe_error(
            friendly_send_error(result.get("error") or "Send failed")
        )
        retryable = bool(result.get("retryable", is_retryable_error(last_error)))
        with transaction.atomic():
            locked = OutboundMessage.objects.select_for_update().get(pk=message.pk)
            if locked.status != MSG_PENDING:
                return
            locked.attempt_count = attempt
            if result.get("ok"):
                locked.status = MSG_SENT
                locked.error = ""
                locked.sent_at = timezone.now()
                locked.wa_message_id = str(result.get("messageId") or "")[:200]
                locked.wa_chat_id = str(result.get("chatId") or "")[:120]
                locked.provider_status = str(result.get("status") or "queued")[:40]
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
                        "provider_status",
                        "body",
                        "image_path",
                        "updated_at",
                    ]
                )
                note_outbound_sent()
                return

            locked.error = last_error
            stop_now = is_auth_error(last_error) or not retryable or attempt >= MAX_SEND_ATTEMPTS
            if stop_now:
                locked.status = MSG_FAILED
            else:
                locked.error = ""
            locked.save(
                update_fields=["attempt_count", "status", "error", "updated_at"]
            )

        if is_auth_error(last_error) or not retryable:
            logger.warning(
                "OutboundMessage %s failed without retry: %s",
                message.pk,
                last_error,
            )
            return

    logger.warning(
        "OutboundMessage %s failed after %s attempts: %s",
        message.pk,
        MAX_SEND_ATTEMPTS,
        last_error,
    )


@shared_task(
    name="communications.process_campaign",
    bind=True,
    max_retries=0,
    soft_time_limit=60 * 60,
    time_limit=60 * 60 + 120,
)
def process_campaign(self, campaign_id: int) -> str:
    return process_campaign_sync(campaign_id)
