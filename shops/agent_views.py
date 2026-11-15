"""Local print agent API — token-authenticated job poll/ack endpoints.

Used when MY-SHOP runs on a hosted server that cannot reach the shop LAN.
A small agent on the shop network polls for pending PrintAgentJob rows,
writes the ESC/POS payload to the Wi‑Fi printer, and acks the result.

Auth: per-shop ``Shop.print_agent_token`` in the ``X-Print-Agent-Token``
header (or ``?token=`` for simple agent setups). No session required.
"""

from __future__ import annotations

import base64
import hmac
import json
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import PrintAgentJob, Shop

POLL_BATCH = 10
CLAIM_STALE_SECONDS = 120
RETENTION_DAYS = 7


def _agent_shop(request, shop_id: int) -> Shop | None:
    token = (
        request.headers.get("X-Print-Agent-Token")
        or request.GET.get("token")
        or ""
    ).strip()
    if not token:
        return None
    shop = Shop.objects.filter(pk=shop_id).only("pk", "print_agent_token",
                                              "wifi_printer_host",
                                              "wifi_printer_port").first()
    if shop is None or not shop.print_agent_token:
        return None
    if not hmac.compare_digest(shop.print_agent_token, token):
        return None
    return shop


def _unauthorized():
    return JsonResponse(
        {"ok": False, "error": "Invalid or missing print agent token."},
        status=401,
    )


@require_GET
def print_agent_poll(request, shop_id: int):
    """Return pending jobs for the agent and mark them claimed."""
    shop = _agent_shop(request, shop_id)
    if shop is None:
        return _unauthorized()

    now = timezone.now()
    # Requeue jobs claimed but never acked (agent crashed mid-print). A lost
    # ack after a successful write can reprint once — acceptable and rare.
    PrintAgentJob.objects.filter(
        shop=shop,
        status=PrintAgentJob.Status.CLAIMED,
        claimed_at__lt=now - timedelta(seconds=CLAIM_STALE_SECONDS),
    ).update(status=PrintAgentJob.Status.PENDING, claimed_at=None)
    # Prune finished jobs so the table stays small on shared hosting.
    PrintAgentJob.objects.filter(
        shop=shop,
        status__in=(PrintAgentJob.Status.DONE, PrintAgentJob.Status.FAILED),
        done_at__lt=now - timedelta(days=RETENTION_DAYS),
    ).delete()

    claimed: list[PrintAgentJob] = []
    candidates = list(
        PrintAgentJob.objects.filter(
            shop=shop, status=PrintAgentJob.Status.PENDING
        ).order_by("pk")[:POLL_BATCH]
    )
    for job in candidates:
        # Conditional update — only the agent that wins the pending→claimed
        # transition gets the job.
        won = PrintAgentJob.objects.filter(
            pk=job.pk, status=PrintAgentJob.Status.PENDING
        ).update(
            status=PrintAgentJob.Status.CLAIMED,
            claimed_at=now,
            attempts=job.attempts + 1,
        )
        if won:
            claimed.append(job)

    return JsonResponse(
        {
            "ok": True,
            "jobs": [
                {
                    "id": job.pk,
                    "host": job.host,
                    "port": job.port,
                    "data": base64.b64encode(bytes(job.data)).decode("ascii"),
                    "attempts": job.attempts + 1,
                }
                for job in claimed
            ],
            "printer": {
                "host": shop.wifi_printer_host or "",
                "port": int(shop.wifi_printer_port or 9100),
            },
        }
    )


@csrf_exempt
@require_POST
def print_agent_ack(request, shop_id: int, job_id: int):
    """Mark a claimed job done or failed. Body: {"status": "done"|"failed", "error": "..."}."""
    shop = _agent_shop(request, shop_id)
    if shop is None:
        return _unauthorized()

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    status = str(payload.get("status") or "").strip().lower()
    if status not in (PrintAgentJob.Status.DONE, PrintAgentJob.Status.FAILED):
        return JsonResponse(
            {"ok": False, "error": "Status must be done or failed."}, status=400
        )
    error = str(payload.get("error") or "")[:255] if status == "failed" else ""

    updated = PrintAgentJob.objects.filter(
        pk=job_id,
        shop=shop,
        status=PrintAgentJob.Status.CLAIMED,
    ).update(
        status=status,
        error=error,
        done_at=timezone.now(),
    )
    if not updated:
        return JsonResponse(
            {"ok": False, "error": "Job not found or not claimed."}, status=404
        )
    return JsonResponse({"ok": True, "job_id": job_id, "status": status})
