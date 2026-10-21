"""Role dashboard overview: transfer notifications, quick performance, opening balances."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from items.models import StockMovement, StockMovementType, StockRequestStatus
from items.services import actionable_shops_for_profile
from shops.models import ShopDaySession, ShopReceipt, ShopReceiptKind, ShopReceiptStatus

TRANSFER_NOTIFICATION_LIMIT = 8
RECENT_DECISION_DAYS = 7
TREND_DAYS = 7

_ZERO = Decimal("0.00")
_SALES_KINDS = (ShopReceiptKind.SALE, ShopReceiptKind.CREDIT)


def _day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def _pct_change(current: Decimal, previous: Decimal):
    if previous <= 0:
        return None
    return round(float((current - previous) / previous * 100), 1)


def _units(movement) -> int:
    return int(sum((line.quantity or 0) for line in movement.lines.all()))


def _stock_requests_url(shop_id) -> str:
    return reverse("employees:my_shop_stock_requests", kwargs={"shop_id": shop_id})


def _transfer_notifications(shop_ids, *, now):
    if not shop_ids:
        return {"items": [], "incoming_count": 0, "outgoing_count": 0, "total_count": 0}

    base = StockMovement.objects.filter(movement_type=StockMovementType.REQUEST)
    pending = base.filter(request_status=StockRequestStatus.PENDING)
    incoming_qs = pending.filter(requested_from_shop_id__in=shop_ids)
    outgoing_qs = pending.filter(shop_id__in=shop_ids).exclude(
        requested_from_shop_id__in=shop_ids
    )
    decisions_qs = base.filter(
        shop_id__in=shop_ids,
        request_status__in=(StockRequestStatus.FULFILLED, StockRequestStatus.DECLINED),
        responded_at__gte=now - timedelta(days=RECENT_DECISION_DAYS),
    )

    related = ("shop", "requested_from_shop", "responded_by__user", "created_by__user")
    items = []
    for movement in (
        incoming_qs.select_related(*related)
        .prefetch_related("lines")
        .order_by("-created_at")[:TRANSFER_NOTIFICATION_LIMIT]
    ):
        items.append(
            {
                "id": movement.pk,
                "direction": "incoming",
                "tone": "warn" if not movement.supplier_notified else "info",
                "icon": "package-open",
                "title": f"Incoming from {movement.shop.name if movement.shop else 'another shop'}",
                "detail": f"To {movement.requested_from_shop.name} · awaiting confirmation",
                "units": _units(movement),
                "unseen": not movement.supplier_notified,
                "at": movement.created_at,
                "href": _stock_requests_url(movement.requested_from_shop_id),
            }
        )
    for movement in (
        outgoing_qs.select_related(*related)
        .prefetch_related("lines")
        .order_by("-created_at")[:TRANSFER_NOTIFICATION_LIMIT]
    ):
        target = movement.requested_from_shop.name if movement.requested_from_shop else "another shop"
        items.append(
            {
                "id": movement.pk,
                "direction": "outgoing",
                "tone": "info",
                "icon": "truck",
                "title": f"Sent to {target}",
                "detail": f"From {movement.shop.name} · waiting for receipt",
                "units": _units(movement),
                "unseen": False,
                "at": movement.created_at,
                "href": _stock_requests_url(movement.shop_id),
            }
        )
    for movement in (
        decisions_qs.select_related(*related)
        .prefetch_related("lines")
        .order_by("-responded_at")[:TRANSFER_NOTIFICATION_LIMIT]
    ):
        accepted = movement.request_status == StockRequestStatus.FULFILLED
        target = movement.requested_from_shop.name if movement.requested_from_shop else "another shop"
        items.append(
            {
                "id": movement.pk,
                "direction": "decision",
                "tone": "ok" if accepted else "danger",
                "icon": "circle-check" if accepted else "circle-x",
                "title": f"{target} {'received' if accepted else 'declined'} transfer",
                "detail": f"From {movement.shop.name}"
                + (f" · {movement.response_note}" if movement.response_note and not accepted else ""),
                "units": _units(movement),
                "unseen": not movement.requester_notified,
                "at": movement.responded_at or movement.created_at,
                "href": _stock_requests_url(movement.shop_id),
            }
        )

    items.sort(key=lambda row: (not row["unseen"], -row["at"].timestamp()))
    incoming_count = incoming_qs.count()
    outgoing_count = outgoing_qs.count()
    return {
        "items": items[:TRANSFER_NOTIFICATION_LIMIT],
        "incoming_count": incoming_count,
        "outgoing_count": outgoing_count,
        "unseen_count": sum(1 for row in items if row["unseen"]),
        "total_count": incoming_count + outgoing_count,
    }


def _performance(shop_ids, *, today):
    trend_start, _ = _day_bounds(today - timedelta(days=TREND_DAYS - 1))
    _, today_end = _day_bounds(today)

    receipts = ShopReceipt.objects.filter(
        shop_id__in=shop_ids, kind__in=_SALES_KINDS
    ).exclude(status=ShopReceiptStatus.CANCELLED)

    window = receipts.filter(created_at__gte=trend_start, created_at__lt=today_end)
    # Bucket in Python: TruncDate needs MySQL timezone tables, often missing on shared hosts.
    by_day = {}
    for created_at, total in window.values_list("created_at", "total"):
        bucket = by_day.setdefault(
            timezone.localtime(created_at).date(), {"amount": _ZERO, "count": 0}
        )
        bucket["amount"] += Decimal(total or 0)
        bucket["count"] += 1

    trend = []
    for offset in range(TREND_DAYS - 1, -1, -1):
        day = today - timedelta(days=offset)
        row = by_day.get(day) or {}
        trend.append(
            {
                "day": day,
                "amount": Decimal(row.get("amount") or 0),
                "count": int(row.get("count") or 0),
            }
        )
    peak = max((point["amount"] for point in trend), default=_ZERO)
    for point in trend:
        point["pct"] = int(point["amount"] / peak * 100) if peak > 0 else 0

    today_point = trend[-1]
    yesterday_point = trend[-2] if len(trend) > 1 else {"amount": _ZERO, "count": 0}
    week_total = sum((point["amount"] for point in trend), _ZERO)
    week_count = sum(point["count"] for point in trend)

    pending_confirm = receipts.filter(
        kind=ShopReceiptKind.SALE, status=ShopReceiptStatus.ACTIVE
    ).count()
    credit_outstanding = sum(
        (
            Decimal(row["total"] or 0) - Decimal(row["amount_paid"] or 0)
            for row in receipts.filter(kind=ShopReceiptKind.CREDIT).values(
                "total", "amount_paid"
            )
        ),
        _ZERO,
    )

    return {
        "today_sales": today_point["amount"],
        "today_receipts": today_point["count"],
        "yesterday_sales": yesterday_point["amount"],
        "today_change_pct": _pct_change(today_point["amount"], yesterday_point["amount"]),
        "week_sales": week_total,
        "week_receipts": week_count,
        "avg_receipt": (week_total / week_count) if week_count else _ZERO,
        "pending_confirm": pending_confirm,
        "credit_outstanding": max(credit_outstanding, _ZERO),
        "trend": trend,
    }


def _opening_balances(shops, *, today):
    today_start, today_end = _day_bounds(today)
    shop_ids = [shop.pk for shop in shops]
    sessions = {}
    for session in ShopDaySession.objects.filter(shop_id__in=shop_ids).filter(
        Q(closed_at__isnull=True) | Q(opened_at__gte=today_start, opened_at__lt=today_end)
    ).order_by("opened_at"):
        # Latest session wins (ordered ascending).
        sessions[session.shop_id] = session

    rows = []
    total_cash = total_mpesa = _ZERO
    opened_count = 0
    for shop in shops:
        session = sessions.get(shop.pk)
        if session is None:
            rows.append({"shop": shop, "state": "not_opened", "session": None})
            continue
        cash = Decimal(session.opening_cash or 0)
        mpesa = Decimal(session.opening_mpesa or 0)
        stale = timezone.localtime(session.opened_at).date() < today
        state = "stale" if stale and session.is_open else ("open" if session.is_open else "closed")
        if not stale:
            total_cash += cash
            total_mpesa += mpesa
            opened_count += 1
        rows.append(
            {
                "shop": shop,
                "state": state,
                "session": session,
                "opening_cash": cash,
                "opening_mpesa": mpesa,
                "opening_total": cash + mpesa,
                "opened_at": session.opened_at,
            }
        )
    return {
        "rows": rows,
        "total_cash": total_cash,
        "total_mpesa": total_mpesa,
        "total": total_cash + total_mpesa,
        "opened_count": opened_count,
        "shop_count": len(shops),
    }


def build_dashboard_overview(profile) -> dict:
    """Context for the dashboard overview partial, scoped to the profile's shops."""
    if profile is None:
        return {"has_shops": False}
    shops = actionable_shops_for_profile(profile)
    shop_ids = [shop.pk for shop in shops]
    now = timezone.now()
    today = timezone.localdate()
    if not shop_ids:
        return {"has_shops": False, "today": today}
    return {
        "has_shops": True,
        "today": today,
        "shops": shops,
        "transfers": _transfer_notifications(shop_ids, now=now),
        "performance": _performance(shop_ids, today=today),
        "opening": _opening_balances(shops, today=today),
    }
