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
    CAMPAIGN_DRAFT,
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
    return file_whatsapp_media_url(getattr(item, "image", None), request=request)


def file_whatsapp_media_url(file_field, *, request=None) -> str:
    """Public https URL Twilio can fetch from an ImageField / FileField."""
    if not file_field:
        return ""
    try:
        name = (getattr(file_field, "name", None) or "").strip()
        storage = getattr(file_field, "storage", None)
        if name and storage is not None and not storage.exists(name):
            return ""
        path = (file_field.url or "").strip()
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


def _cover_tile(image, size: int):
    from PIL import Image as PILImage

    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image.resize((size, size), PILImage.Resampling.LANCZOS)
    scale = max(size / src_w, size / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = image.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
    left = max(0, (new_w - size) // 2)
    top = max(0, (new_h - size) // 2)
    return resized.crop((left, top, left + size, top + size))


def compose_catalogue_collage(items, *, max_tiles: int = 6):
    """One JPEG of item photos so WhatsApp can attach every item in a single message."""
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image as PILImage

    opened = []
    try:
        for item in items or []:
            image_field = getattr(item, "image", None)
            if not image_field:
                continue
            try:
                image_field.open("rb")
                img = PILImage.open(image_field).convert("RGB")
            except Exception:
                logger.debug("Could not open catalogue image for collage", extra={"item": getattr(item, "pk", None)})
                continue
            opened.append(img)
            if len(opened) >= max_tiles:
                break
        if len(opened) <= 1:
            return None
        tile = 480
        gap = 12
        cols = 2 if len(opened) <= 4 else 3
        rows = (len(opened) + cols - 1) // cols
        width = cols * tile + (cols + 1) * gap
        height = rows * tile + (rows + 1) * gap
        canvas = PILImage.new("RGB", (width, height), "#102226")
        for index, img in enumerate(opened):
            fitted = _cover_tile(img, tile)
            x = gap + (index % cols) * (tile + gap)
            y = gap + (index // cols) * (tile + gap)
            canvas.paste(fitted, (x, y))
            if fitted is not img:
                fitted.close()
        buf = BytesIO()
        canvas.save(buf, format="JPEG", quality=86, optimize=True)
        canvas.close()
        return ContentFile(buf.getvalue(), name="catalogue-share.jpg")
    except Exception:
        logger.exception("Could not compose catalogue collage")
        return None
    finally:
        for img in opened:
            try:
                img.close()
            except Exception:
                pass


def create_catalogue_campaign(
    *,
    profile,
    items,
    filters: dict | None = None,
    request=None,
    send_after=None,
    schedule: dict | None = None,
    body_template: str | None = None,
) -> BroadcastCampaign:
    """One WhatsApp per recipient with a product-card image and short caption."""
    from datetime import timedelta

    from .automations import apply_catalogue_message_template, fit_whatsapp_caption
    from .catalogue_card import compose_catalogue_card

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

    card = compose_catalogue_card(chosen)
    caption = apply_catalogue_message_template(
        body_template or "", chosen, card=bool(card)
    )
    if not caption:
        raise ValueError("Write a message to send.")

    due_at = send_after or timezone.now()
    if timezone.is_naive(due_at):
        due_at = timezone.make_aware(due_at)
    is_due = due_at <= timezone.now() + timedelta(seconds=15)
    parsed = {
        **parsed,
        "item_ids": [item.pk for item in chosen],
        "send_after": due_at.isoformat(),
        "schedule": schedule or {},
    }
    with transaction.atomic():
        campaign = BroadcastCampaign.objects.create(
            created_by=profile,
            body_template=caption,
            filters=parsed,
            status=CAMPAIGN_QUEUED if is_due else CAMPAIGN_DRAFT,
            recipient_count=len(recipients),
        )
        if card:
            campaign.image.save(
                f"catalogue-{campaign.pk}.jpg",
                card,
                save=True,
            )
        else:
            collage = compose_catalogue_collage(chosen)
            if collage:
                campaign.image.save(
                    f"catalogue-{campaign.pk}.jpg",
                    collage,
                    save=True,
                )
        media = file_whatsapp_media_url(campaign.image, request=request)
        if not media:
            for item in chosen:
                media = item_whatsapp_media_url(item, request=request)
                if media:
                    break
        body_template = fit_whatsapp_caption(caption, has_media=bool(media))
        if body_template != campaign.body_template:
            campaign.body_template = body_template
            campaign.save(update_fields=["body_template"])
        OutboundMessage.objects.bulk_create(
            [
                OutboundMessage(
                    campaign=campaign,
                    client_id=recipient.client_id,
                    client_name=recipient.full_name,
                    phone=(recipient.chat_id or recipient.phone),
                    body=render_placeholders(body_template, recipient),
                    image_path=media,
                    status=MSG_PENDING,
                )
                for recipient in recipients
            ]
        )

    if is_due:
        enqueue_campaign(campaign.pk)
    else:
        logger.info("Campaign %s scheduled for %s", campaign.pk, due_at.isoformat())
    return campaign


def create_catalogue_campaign_series(
    *,
    profile,
    items,
    filters: dict | None = None,
    request=None,
    period_days: int = 1,
    times: int = 1,
    body_template: str | None = None,
) -> list[BroadcastCampaign]:
    """Queue the first send now and later waves evenly across the period."""
    from datetime import timedelta

    days = max(1, min(30, int(period_days or 1)))
    waves = max(1, min(7, int(times or 1)))
    start = timezone.now()
    gap = timedelta(days=days) / (waves - 1) if waves > 1 else timedelta(0)
    schedule = {"period_days": days, "times": waves}
    created = []
    for index in range(waves):
        due = start + (gap * index)
        campaign = create_catalogue_campaign(
            profile=profile,
            items=items,
            filters=filters,
            request=request,
            send_after=due,
            schedule={**schedule, "wave": index + 1},
            body_template=body_template,
        )
        created.append(campaign)
    return created


def promote_due_campaigns() -> int:
    """Move scheduled drafts whose send time has arrived onto the send queue."""
    from datetime import datetime

    now = timezone.now()
    promoted = 0
    for campaign in BroadcastCampaign.objects.filter(status=CAMPAIGN_DRAFT).order_by("id"):
        raw = (campaign.filters or {}).get("send_after") or ""
        if not raw:
            continue
        try:
            due = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if timezone.is_naive(due):
            due = timezone.make_aware(due)
        if due > now:
            continue
        campaign.status = CAMPAIGN_QUEUED
        campaign.save(update_fields=["status"])
        enqueue_campaign(campaign.pk)
        promoted += 1
    return promoted


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


MESSAGE_DISPLAY_LABELS = {
    "queued": "Waiting",
    "pending": "Waiting",
    "sent": "Sent",
    "delivered": "Delivered",
    "viewed": "Viewed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


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


def outbound_status_label(display_status: str) -> str:
    key = (display_status or "").strip().lower()
    return MESSAGE_DISPLAY_LABELS.get(key, (display_status or "").replace("_", " ").title())


def cancel_campaign(campaign_id: int) -> BroadcastCampaign:
    with transaction.atomic():
        campaign = BroadcastCampaign.objects.select_for_update().filter(pk=campaign_id).first()
        if campaign is None:
            raise ValueError("Send not found.")
        if campaign.status not in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING, CAMPAIGN_DRAFT}:
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


def _parse_send_after(raw: str):
    from datetime import datetime

    text = (raw or "").strip()
    if not text:
        return None
    try:
        due = datetime.fromisoformat(text)
    except ValueError:
        return None
    if timezone.is_naive(due):
        due = timezone.make_aware(due)
    return due


def _campaign_kind_label(filters: dict | None) -> str:
    data = filters or {}
    schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
    wave = schedule.get("wave")
    total = schedule.get("times")
    if data.get("item_ids"):
        if wave and total and int(total) > 1:
            return f"Item share · {wave} of {total}"
        return "Item share"
    return "WhatsApp broadcast"


def _campaign_status_label(campaign: BroadcastCampaign, *, is_scheduled: bool) -> str:
    if campaign.status == CAMPAIGN_DRAFT or is_scheduled:
        return "Scheduled"
    if campaign.status == CAMPAIGN_QUEUED:
        return "Queued"
    if campaign.status == CAMPAIGN_SENDING:
        return "Sending"
    if campaign.status == CAMPAIGN_CANCELLED:
        return "Cancelled"
    if campaign.status == CAMPAIGN_DONE:
        return "Sent"
    return (campaign.status or "").replace("_", " ").title()


def _campaign_timing_label(
    campaign: BroadcastCampaign,
    *,
    send_after_label: str,
    is_pending: bool,
    is_scheduled: bool,
) -> str:
    if is_scheduled and send_after_label:
        return f"Sends {send_after_label}"
    if is_pending and campaign.status == CAMPAIGN_SENDING:
        return "Sending now"
    if is_pending:
        return "Waiting in queue"
    if campaign.finished_at:
        return _format_when(campaign.finished_at)
    return _format_when(campaign.created_at)


def _format_when(dt) -> str:
    if not dt:
        return ""
    local = timezone.localtime(dt)
    now = timezone.localtime(timezone.now())
    if local.date() == now.date():
        return f"Today {local.strftime('%H:%M')}"
    if (now.date() - local.date()).days == 1:
        return f"Yesterday {local.strftime('%H:%M')}"
    return local.strftime("%d %b %Y · %H:%M")


def _campaign_is_pending(campaign: BroadcastCampaign, *, pending_count: int) -> bool:
    if campaign.status == CAMPAIGN_DRAFT:
        return True
    if campaign.status in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING}:
        return True
    return pending_count > 0 and campaign.status not in {CAMPAIGN_CANCELLED, CAMPAIGN_DONE}


def _campaign_image_url(campaign: BroadcastCampaign) -> str:
    if not campaign.image:
        return ""
    try:
        return campaign.image.url or ""
    except ValueError:
        return ""


def _message_when_label(display_status: str, row: dict[str, Any]) -> str:
    if display_status == "viewed":
        return row.get("read_label") or ""
    if display_status == "delivered":
        return row.get("delivered_label") or row.get("sent_label") or ""
    if display_status == "sent":
        return row.get("sent_label") or ""
    if display_status == "cancelled":
        return row.get("updated_label") or ""
    if display_status == "failed":
        return row.get("updated_label") or ""
    return row.get("created_label") or ""


def _progress_label(
    *,
    pending_count: int,
    viewed_count: int,
    delivered_count: int,
    sent_count: int,
    failed_count: int,
    cancelled_count: int,
    recipient_count: int,
) -> str:
    parts = []
    if pending_count:
        parts.append(f"{pending_count} waiting")
    if viewed_count:
        parts.append(f"{viewed_count} viewed")
    if delivered_count:
        parts.append(f"{delivered_count} delivered")
    if sent_count:
        parts.append(f"{sent_count} sent")
    if failed_count:
        parts.append(f"{failed_count} failed")
    if cancelled_count:
        parts.append(f"{cancelled_count} cancelled")
    if parts:
        return " · ".join(parts)
    count = int(recipient_count or 0)
    return f"{count} {'person' if count == 1 else 'people'}"


def campaign_as_dict(
    campaign: BroadcastCampaign, *, include_messages: bool = True
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if include_messages:
        messages = list(
            campaign.messages.order_by("id")[:500].values(
                "id",
                "client_id",
                "client_name",
                "phone",
                "body",
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
            row["created_label"] = _format_when(row.get("created_at"))
            row["sent_label"] = _format_when(row.get("sent_at"))
            row["delivered_label"] = _format_when(row.get("delivered_at"))
            row["read_label"] = _format_when(row.get("read_at"))
            row["updated_label"] = _format_when(row.get("updated_at"))
            display = outbound_display_status(
                status=row.get("status") or "",
                read_at=row.get("read_at"),
                delivered_at=row.get("delivered_at"),
            )
            row["display_status"] = display
            row["status_label"] = outbound_status_label(display)
            row["when_label"] = _message_when_label(display, row)
            for key in ("sent_at", "delivered_at", "read_at", "created_at", "updated_at"):
                if row.get(key):
                    row[key] = row[key].isoformat()
    pending_count = campaign.messages.filter(status=MSG_PENDING).count()
    delivered_count = campaign.messages.filter(delivered_at__isnull=False).count()
    viewed_count = campaign.messages.filter(read_at__isnull=False).count()
    cancelled_count = campaign.messages.filter(status=MSG_CANCELLED).count()
    filters = campaign.filters or {}
    send_after = _parse_send_after(str(filters.get("send_after") or ""))
    schedule = filters.get("schedule") if isinstance(filters.get("schedule"), dict) else {}
    now = timezone.now()
    is_scheduled = campaign.status == CAMPAIGN_DRAFT or (
        send_after is not None and send_after > now and pending_count > 0
    )
    image_url = _campaign_image_url(campaign)
    return {
        "id": campaign.pk,
        "status": campaign.status,
        "kind_label": _campaign_kind_label(filters),
        "body_template": campaign.body_template,
        "body_preview": (campaign.body_template or "").replace("\n", " ")[:120],
        "filters": filters,
        "recipient_count": campaign.recipient_count,
        "sent_count": campaign.sent_count,
        "failed_count": campaign.failed_count,
        "pending_count": pending_count,
        "delivered_count": delivered_count,
        "viewed_count": viewed_count,
        "cancelled_count": cancelled_count,
        "progress_label": _progress_label(
            pending_count=pending_count,
            viewed_count=viewed_count,
            delivered_count=delivered_count,
            sent_count=campaign.sent_count,
            failed_count=campaign.failed_count,
            cancelled_count=cancelled_count,
            recipient_count=campaign.recipient_count,
        ),
        "status_label": _campaign_status_label(campaign, is_scheduled=is_scheduled),
        "timing_label": _campaign_timing_label(
            campaign,
            send_after_label=_format_when(send_after) if send_after else "",
            is_pending=_campaign_is_pending(campaign, pending_count=pending_count),
            is_scheduled=is_scheduled,
        ),
        "item_count": len(filters.get("item_ids") or []) if isinstance(filters, dict) else 0,
        "can_cancel": campaign.status
        in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING, CAMPAIGN_DRAFT}
        and pending_count > 0,
        "can_retry": campaign.messages.filter(
            status__in=[MSG_FAILED, MSG_MANUAL_REVIEW]
        ).exists(),
        "has_image": bool(image_url or campaign.image),
        "image_url": image_url,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "created_label": _format_when(campaign.created_at),
        "send_after": send_after.isoformat() if send_after else None,
        "send_after_label": _format_when(send_after) if send_after else "",
        "is_scheduled": is_scheduled,
        "is_pending": _campaign_is_pending(campaign, pending_count=pending_count),
        "schedule_wave": schedule.get("wave"),
        "schedule_total": schedule.get("times"),
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "messages": messages,
        "bridge": bridge_state_as_dict(),
    }


def recent_campaigns(limit: int = 10) -> list[dict[str, Any]]:
    rows = list(BroadcastCampaign.objects.order_by("-created_at")[:limit])
    return [campaign_as_dict(c) for c in rows]


def activities_payload(*, limit: int = 50) -> dict[str, Any]:
    """Split sends into waiting vs history for the Activities page."""
    cap = max(1, limit)
    waiting_qs = list(
        BroadcastCampaign.objects.filter(
            status__in=[CAMPAIGN_DRAFT, CAMPAIGN_QUEUED, CAMPAIGN_SENDING]
        ).order_by("id")[:cap]
    )
    waiting_ids = {row.pk for row in waiting_qs}
    history_qs = list(
        BroadcastCampaign.objects.exclude(pk__in=waiting_ids).order_by("-created_at")[:cap]
    )
    pending = [
        campaign_as_dict(campaign, include_messages=False) for campaign in waiting_qs
    ]
    history = [
        campaign_as_dict(campaign, include_messages=False) for campaign in history_qs
    ]

    def _sort_key(row):
        return row.get("send_after") or row.get("created_at") or ""

    pending.sort(key=_sort_key)
    scheduled = sum(1 for row in pending if row.get("is_scheduled"))
    queued_now = sum(
        1
        for row in pending
        if row.get("status") in {CAMPAIGN_QUEUED, CAMPAIGN_SENDING}
    )
    waiting_people = sum(int(row.get("pending_count") or 0) for row in pending)
    return {
        "pending": pending,
        "history": history,
        "summary": {
            "pending_batches": len(pending),
            "scheduled_batches": scheduled,
            "queued_batches": queued_now,
            "waiting_messages": waiting_people,
            "history_batches": len(history),
        },
    }


def _run_campaign_inline(campaign_id: int) -> None:
    from .tasks import process_campaign_sync

    try:
        process_campaign_sync(campaign_id)
    except Exception:
        logger.exception("Inline campaign %s failed", campaign_id)


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
