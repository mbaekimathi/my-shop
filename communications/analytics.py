"""Campaign send analytics for the communications sidebar."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .constants import MSG_FAILED, MSG_MANUAL_REVIEW, MSG_PENDING, MSG_SENT
from .models import BroadcastCampaign, OutboundMessage
from .replies import matched_reply_stats


def analytics_payload() -> dict[str, Any]:
    messages = OutboundMessage.objects.all()
    sent = messages.filter(status=MSG_SENT).count()
    failed = messages.filter(status__in=[MSG_FAILED, MSG_MANUAL_REVIEW]).count()
    pending = messages.filter(status=MSG_PENDING).count()
    total = messages.count()

    reply_stats = matched_reply_stats()
    replied = int(reply_stats.get("replied") or 0)
    reply_rate = round((replied / sent) * 100, 1) if sent else 0.0
    by_campaign = reply_stats.get("by_campaign") or {}

    fail_rows = (
        OutboundMessage.objects.filter(status__in=[MSG_FAILED, MSG_MANUAL_REVIEW])
        .exclude(error="")
        .values_list("error", flat=True)[:200]
    )
    reason_counter: Counter[str] = Counter()
    for raw in fail_rows:
        text = " ".join(str(raw or "").split())
        if not text:
            text = "Unknown error"
        if len(text) > 90:
            text = text[:87] + "…"
        reason_counter[text] += 1
    top_reasons = [
        {"reason": reason, "count": count}
        for reason, count in reason_counter.most_common(8)
    ]

    campaigns = []
    for campaign in BroadcastCampaign.objects.order_by("-created_at")[:12]:
        campaigns.append(
            {
                "id": campaign.pk,
                "status": campaign.status,
                "body_preview": (campaign.body_template or "")[:80],
                "recipient_count": campaign.recipient_count,
                "sent_count": campaign.sent_count,
                "failed_count": campaign.failed_count,
                "replied_count": int(by_campaign.get(campaign.pk) or 0),
                "created_at": campaign.created_at.isoformat()
                if campaign.created_at
                else None,
            }
        )

    return {
        "ok": True,
        "summary": {
            "total": total,
            "sent": sent,
            "delivered": sent,  # personal WA send success ≈ delivered
            "failed": failed,
            "pending": pending,
            "replied": replied,
            "reply_rate": reply_rate,
            "unread_replies": int(reply_stats.get("unread_replies") or 0),
            "campaigns": BroadcastCampaign.objects.count(),
        },
        "fail_reasons": top_reasons,
        "campaigns": campaigns,
    }
