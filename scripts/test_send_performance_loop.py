"""
WhatsApp send-pipeline performance loop.

Measures campaign processing → completion timing using mock Twilio
calls (no real messages sent). Creates campaigns directly in the DB
to avoid needing Twilio credentials for campaign creation.

Usage:
  python scripts/test_send_performance_loop.py
  python scripts/test_send_performance_loop.py --recipients 50 --iterations 5
  python scripts/test_send_performance_loop.py --recipients 20 --concurrency 1   # sequential baseline
  python scripts/test_send_performance_loop.py --recipients 20 --concurrency 5   # faster
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def django_ready() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
    import django

    django.setup()


def fake_send(**kwargs):
    """Simulates a successful Twilio API call with ~100ms latency."""
    time.sleep(0.1)
    return {
        "ok": True,
        "messageId": f"SM_FAKE_{time.monotonic_ns()}",
        "chatId": kwargs.get("phone") or kwargs.get("chat_id") or "unknown",
        "status": "queued",
    }


def run_campaign(recipient_count: int, concurrency: int) -> dict:
    """Create a campaign with N messages directly, process it, return timing."""
    from django.test import override_settings

    from communications.constants import CAMPAIGN_DONE, CAMPAIGN_QUEUED, MSG_FAILED, MSG_PENDING, MSG_SENT
    from communications.models import BroadcastCampaign, OutboundMessage
    from communications.tasks import process_campaign_sync

    campaign = BroadcastCampaign.objects.create(
        body_template="Hi, perf test.",
        status=CAMPAIGN_QUEUED,
        recipient_count=recipient_count,
    )
    OutboundMessage.objects.bulk_create([
        OutboundMessage(
            campaign=campaign,
            client_name=f"User {i + 1}",
            phone=f"+2547{90000000 + i}",
            body=f"Hi User {i + 1}, perf test.",
            status=MSG_PENDING,
        )
        for i in range(recipient_count)
    ])

    with patch("communications.tasks.send_whatsapp_message", side_effect=fake_send):
        t0 = time.perf_counter()
        with override_settings(COMMS_SEND_CONCURRENCY=concurrency):
            result = process_campaign_sync(campaign.pk)
        elapsed = time.perf_counter() - t0

    campaign.refresh_from_db()
    sent = OutboundMessage.objects.filter(campaign=campaign, status=MSG_SENT).count()
    failed = OutboundMessage.objects.filter(campaign=campaign, status=MSG_FAILED).count()
    pending = OutboundMessage.objects.filter(campaign=campaign, status=MSG_PENDING).count()

    return {
        "campaign_id": campaign.pk,
        "recipients": recipient_count,
        "concurrency": concurrency,
        "elapsed_ms": elapsed * 1000,
        "per_message_ms": (elapsed * 1000) / max(1, recipient_count),
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "done": campaign.status == CAMPAIGN_DONE,
        "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="WhatsApp send performance loop")
    parser.add_argument("--recipients", type=int, default=20, help="Number of recipients per campaign")
    parser.add_argument("--iterations", type=int, default=3, help="Number of test campaigns to run")
    parser.add_argument("--concurrency", type=int, default=0, help="Override SEND_CONCURRENCY (0=use default)")
    args = parser.parse_args()

    django_ready()
    from communications.constants import SEND_CONCURRENCY

    concurrency = args.concurrency if args.concurrency > 0 else SEND_CONCURRENCY
    print(f"[INFO] recipients={args.recipients} iterations={args.iterations} concurrency={concurrency}")
    print()

    results = []
    ok_all = True
    for i in range(1, args.iterations + 1):
        row = run_campaign(args.recipients, concurrency)
        results.append(row)
        ok = row["done"] and row["sent"] == row["recipients"] and row["failed"] == 0
        ok_all = ok_all and ok
        print(
            f"[{'PASS' if ok else 'FAIL'}] iter={i} "
            f"campaign={row['campaign_id']} "
            f"sent={row['sent']}/{row['recipients']} "
            f"elapsed={row['elapsed_ms']:.0f}ms "
            f"per_msg={row['per_message_ms']:.0f}ms"
        )
        if not ok:
            print(f"  failed={row['failed']} pending={row['pending']} result={row['result']}")

    print()
    elapsed_samples = [r["elapsed_ms"] for r in results]
    per_msg_samples = [r["per_message_ms"] for r in results]
    print(f"[STATS] {args.recipients} recipients x {concurrency} workers")
    print(f"  elapsed  median={statistics.median(elapsed_samples):.0f}ms  "
          f"min={min(elapsed_samples):.0f}ms  max={max(elapsed_samples):.0f}ms")
    print(f"  per-msg  median={statistics.median(per_msg_samples):.0f}ms  "
          f"min={min(per_msg_samples):.0f}ms  max={max(per_msg_samples):.0f}ms")

    old_per_msg_ms = 12500 + 3600  # old avg delay + polling overhead
    old_total = args.recipients * old_per_msg_ms
    print()
    print(f"[COMPARE] old estimate (seq 5-20s delay + 3.6s poll): ~{old_total / 1000:.0f}s for {args.recipients} msgs")
    print(f"[COMPARE] new actual: ~{statistics.median(elapsed_samples) / 1000:.1f}s")
    speedup = old_total / max(1, statistics.median(elapsed_samples))
    print(f"[COMPARE] speedup: ~{speedup:.1f}x faster")

    if ok_all:
        print()
        print("[PASS] send performance loop")
        return 0
    print()
    print("[FAIL] send performance loop")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
