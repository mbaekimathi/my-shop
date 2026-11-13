"""Rider delivery board — mirrors MY-SHOP shops.services delivery paths.

Reads/writes unmanaged `shops_shopdelivery` (+ assignee M2M). Does not change
receipt kind/status. Customer notice automations stay in MY-SHOP.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .myshop_models import Shop, ShopDelivery, ShopDeliveryAssignedRider, ShopReceipt

KIND_SALE = "sale"
KIND_CREDIT = "credit"

KIND_LABELS = {
    KIND_SALE: "Sale",
    KIND_CREDIT: "Credit",
    "quotation": "Quotation",
    "trade_out": "Trade out",
    "trade_in": "Trade in",
}

STATUS_LABELS = {
    ShopDelivery.STATUS_OPEN: "Open",
    ShopDelivery.STATUS_ACCEPTED: "En route",
    ShopDelivery.STATUS_DELIVERED: "Delivered",
    ShopDelivery.STATUS_DELIVERED_PAID: "Delivered · paid",
    ShopDelivery.STATUS_COMPLETED: "Completed",
    ShopDelivery.STATUS_CANCELLED: "Cancelled",
}


class RiderError(Exception):
    """User-facing rider delivery error."""


def _money(value) -> str:
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    return f"{amount:,.2f}"


def _iso(dt) -> str:
    if not dt:
        return ""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt.isoformat()


def _parse_payment_at(raw):
    text = (raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    if "T" not in normalized and " " in normalized:
        normalized = normalized.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RiderError("Enter a valid payment date and time.") from exc
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _assigned_ids(delivery_id: int) -> set[int]:
    return {
        int(row.employeeprofile_id)
        for row in ShopDeliveryAssignedRider.objects.filter(
            shopdelivery_id=delivery_id
        ).only("employeeprofile_id")
    }


def delivery_available_to_rider(delivery: ShopDelivery, rider_id: int) -> bool:
    """True when an open delivery is on this rider's board."""
    if delivery.status != ShopDelivery.STATUS_OPEN:
        return False
    assigned = _assigned_ids(delivery.pk)
    if not assigned:
        return True
    return int(rider_id) in assigned


def _delivery_qs():
    return ShopDelivery.objects.all()


def _receipt_map(receipt_ids: list[int]) -> dict[int, ShopReceipt]:
    if not receipt_ids:
        return {}
    return {
        row.pk: row
        for row in ShopReceipt.objects.filter(pk__in=receipt_ids).only(
            "id",
            "receipt_number",
            "kind",
            "client_name",
            "client_phone",
            "total",
            "amount_paid",
        )
    }


def _shop_map(shop_ids: list[int]) -> dict[int, Shop]:
    if not shop_ids:
        return {}
    return {
        row.pk: row
        for row in Shop.objects.filter(pk__in=shop_ids).only("id", "name")
    }


def open_deliveries_for_rider(rider_id: int) -> list[ShopDelivery]:
    rows = list(
        _delivery_qs()
        .filter(status=ShopDelivery.STATUS_OPEN)
        .order_by("-created_at")
    )
    return [row for row in rows if delivery_available_to_rider(row, rider_id)]


def rider_owned_deliveries(rider_id: int, *, statuses=None) -> list[ShopDelivery]:
    qs = _delivery_qs().filter(accepted_by_id=rider_id)
    if statuses is not None:
        qs = qs.filter(status__in=list(statuses))
    return list(qs.order_by("-accepted_at", "-created_at"))


def _timing(delivery: ShopDelivery) -> dict:
    actual = None
    if delivery.accepted_at and delivery.completed_at:
        seconds = (delivery.completed_at - delivery.accepted_at).total_seconds()
        if seconds >= 0:
            actual = int(round(seconds / 60.0))
    expected = int(delivery.eta_minutes) if delivery.eta_minutes is not None else None
    variance = None
    variance_label = ""
    variance_tone = ""
    if actual is not None and expected is not None:
        variance = actual - expected
        if variance == 0:
            variance_label = "On time"
            variance_tone = "early"
        elif variance < 0:
            variance_label = f"{abs(variance)} min early"
            variance_tone = "early"
        else:
            variance_label = f"{variance} min late"
            variance_tone = "near" if variance <= 5 else "late"
    return {
        "actual_minutes": actual,
        "expected_minutes": expected,
        "variance_minutes": variance,
        "variance_label": variance_label,
        "variance_tone": variance_tone,
    }


def serialize_delivery(
    delivery: ShopDelivery,
    *,
    board: str,
    receipt: ShopReceipt | None = None,
    shop: Shop | None = None,
    assigned: bool = False,
) -> dict:
    """Serialize a delivery card. Open board hides client contact."""
    if receipt is None:
        receipt = ShopReceipt.objects.filter(pk=delivery.receipt_id).first()
    if shop is None:
        shop = Shop.objects.filter(pk=delivery.shop_id).only("id", "name").first()

    kind = (receipt.kind if receipt else "") or ""
    reveal = board != "open"
    client_name = ((receipt.client_name if receipt else "") or "").strip()
    client_phone = ((receipt.client_phone if receipt else "") or "").strip()
    total = receipt.total if receipt else Decimal("0")
    amount_paid = receipt.amount_paid if receipt else Decimal("0")
    due = Decimal(total or 0) - Decimal(amount_paid or 0)
    if due <= 0:
        due = Decimal(total or 0)

    status = delivery.status
    if status == ShopDelivery.STATUS_DELIVERED and kind == KIND_CREDIT:
        status_label = "Delivered · unpaid"
    else:
        status_label = STATUS_LABELS.get(status, status.replace("_", " ").title())

    timing = _timing(delivery)
    route_bits = []
    if delivery.distance_km is not None:
        route_bits.append(f"{delivery.distance_km} km")
    if delivery.eta_minutes is not None:
        route_bits.append(f"~{delivery.eta_minutes} min")

    payload = {
        "id": int(delivery.pk),
        "board": board,
        "status": status,
        "status_label": status_label,
        "assigned": bool(assigned),
        "shop_id": int(delivery.shop_id),
        "shop_name": shop.name if shop else "Shop",
        "receipt_id": int(delivery.receipt_id),
        "receipt_number": (receipt.receipt_number if receipt else "") or "",
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind.replace("_", " ").title() or "Order"),
        "location_text": delivery.location_text or "",
        "maps_url": delivery.maps_url or "",
        "distance_km": str(delivery.distance_km) if delivery.distance_km is not None else "",
        "eta_minutes": delivery.eta_minutes,
        "route_label": " · ".join(route_bits),
        "expected_at": _iso(delivery.expected_at),
        "accepted_at": _iso(delivery.accepted_at),
        "completed_at": _iso(delivery.completed_at),
        "created_at": _iso(delivery.created_at),
        "total": _money(total),
        "amount_due": _money(due),
        "receiver_name": (delivery.receiver_name or "").strip(),
        "payment_reference": (delivery.payment_reference or "").strip(),
        "payment_amount": (
            _money(delivery.payment_amount)
            if delivery.payment_amount is not None
            else ""
        ),
        "payment_at": _iso(delivery.payment_at),
        "client_name": client_name if reveal else "",
        "client_phone": client_phone if reveal else "",
        "client_hidden": not reveal,
        **timing,
    }
    return payload


def _hydrate(rows: list[ShopDelivery], board: str, rider_id: int | None = None) -> list[dict]:
    receipts = _receipt_map([row.receipt_id for row in rows])
    shops = _shop_map([row.shop_id for row in rows])
    assigned_by_delivery: dict[int, set[int]] = {}
    if board == "open" and rows:
        for link in ShopDeliveryAssignedRider.objects.filter(
            shopdelivery_id__in=[row.pk for row in rows]
        ).only("shopdelivery_id", "employeeprofile_id"):
            assigned_by_delivery.setdefault(int(link.shopdelivery_id), set()).add(
                int(link.employeeprofile_id)
            )
    out = []
    for row in rows:
        assigned_ids = assigned_by_delivery.get(int(row.pk), set())
        assigned = bool(
            rider_id is not None and assigned_ids and int(rider_id) in assigned_ids
        )
        out.append(
            serialize_delivery(
                row,
                board=board,
                receipt=receipts.get(row.receipt_id),
                shop=shops.get(row.shop_id),
                assigned=assigned,
            )
        )
    return out


def build_rider_board(rider_id: int) -> dict:
    """Open / active / completed snapshots for the WhatsApp rider UI."""
    open_rows = open_deliveries_for_rider(rider_id)
    active_rows = rider_owned_deliveries(
        rider_id, statuses=[ShopDelivery.STATUS_ACCEPTED]
    )
    done_rows = rider_owned_deliveries(
        rider_id, statuses=list(ShopDelivery.DONE_STATUSES)
    )[:50]
    return {
        "open": _hydrate(open_rows, "open", rider_id=rider_id),
        "active": _hydrate(active_rows, "active"),
        "completed": _hydrate(done_rows, "completed"),
        "open_count": len(open_rows),
        "active_count": len(active_rows),
        "completed_count": len(done_rows),
    }


def rider_alert_payload(rider_id: int) -> dict:
    """Compact open-board snapshot for polling / sound alerts."""
    rows = []
    for delivery in open_deliveries_for_rider(rider_id):
        assigned_ids = _assigned_ids(delivery.pk)
        shop = Shop.objects.filter(pk=delivery.shop_id).only("name").first()
        receipt = (
            ShopReceipt.objects.filter(pk=delivery.receipt_id)
            .only("receipt_number")
            .first()
        )
        rows.append(
            {
                "id": int(delivery.pk),
                "assigned": bool(assigned_ids) and int(rider_id) in assigned_ids,
                "shop": shop.name if shop else "Shop",
                "receipt_number": (receipt.receipt_number if receipt else "") or "",
                "location_text": delivery.location_text or "",
                "created_at": _iso(delivery.created_at),
            }
        )
    return {"ok": True, "open": rows, "open_count": len(rows)}


def accept_delivery(*, delivery_id: int, rider_id: int) -> dict:
    """First rider to accept claims the job."""
    with transaction.atomic():
        delivery = (
            ShopDelivery.objects.select_for_update()
            .filter(pk=delivery_id)
            .first()
        )
        if delivery is None:
            raise RiderError("Delivery request was not found.")
        if delivery.status != ShopDelivery.STATUS_OPEN:
            raise RiderError("That delivery was already accepted.")
        if not delivery_available_to_rider(delivery, rider_id):
            raise RiderError("You are not assigned to this delivery.")
        now = timezone.now()
        delivery.status = ShopDelivery.STATUS_ACCEPTED
        delivery.accepted_by_id = int(rider_id)
        delivery.accepted_at = now
        if delivery.eta_minutes:
            delivery.expected_at = now + timedelta(minutes=int(delivery.eta_minutes))
        else:
            delivery.expected_at = None
        delivery.updated_at = now
        delivery.save(
            update_fields=[
                "status",
                "accepted_by_id",
                "accepted_at",
                "expected_at",
                "updated_at",
            ]
        )
    return serialize_delivery(delivery, board="active")


def complete_delivery(
    *,
    delivery_id: int,
    rider_id: int,
    receiver_name: str = "",
    payment_reference: str = "",
    payment_at=None,
    payment_amount=None,
) -> dict:
    """Mark accepted delivery delivered. Does not change receipt settlement."""
    with transaction.atomic():
        delivery = (
            ShopDelivery.objects.select_for_update()
            .filter(pk=delivery_id)
            .first()
        )
        if delivery is None:
            raise RiderError("Delivery request was not found.")
        if int(delivery.accepted_by_id or 0) != int(rider_id):
            raise RiderError("Only the accepting rider can complete this delivery.")
        if delivery.status != ShopDelivery.STATUS_ACCEPTED:
            raise RiderError("Only accepted deliveries can be marked done.")

        receipt = ShopReceipt.objects.filter(pk=delivery.receipt_id).first()
        if receipt is None:
            raise RiderError("Linked receipt was not found.")
        kind = (receipt.kind or "").strip().lower()
        receiver = (receiver_name or "").strip()
        if not receiver and kind == KIND_SALE:
            receiver = (receipt.client_name or "").strip()
        if kind == KIND_SALE and not receiver:
            raise RiderError("Enter the name of the person who received the goods.")

        reference = (payment_reference or "").strip()[:64]
        paid_at = payment_at
        if isinstance(paid_at, str):
            paid_at = _parse_payment_at(paid_at)
        amount = None
        if payment_amount not in (None, ""):
            try:
                amount = Decimal(str(payment_amount)).quantize(Decimal("0.01"))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise RiderError("Enter a valid payment amount.") from exc
            if amount < 0:
                raise RiderError("Payment amount cannot be negative.")

        if kind == KIND_CREDIT:
            if reference:
                status = ShopDelivery.STATUS_DELIVERED_PAID
                if paid_at is None:
                    paid_at = timezone.now()
                if amount is None:
                    due = Decimal(receipt.total or 0) - Decimal(receipt.amount_paid or 0)
                    amount = due if due > 0 else Decimal(receipt.total or 0)
            else:
                status = ShopDelivery.STATUS_DELIVERED
                reference = ""
                paid_at = None
                amount = None
        else:
            status = ShopDelivery.STATUS_DELIVERED
            reference = ""
            paid_at = None
            amount = None

        now = timezone.now()
        delivery.status = status
        delivery.completed_at = now
        delivery.receiver_name = receiver[:200]
        delivery.payment_reference = reference
        delivery.payment_at = paid_at
        delivery.payment_amount = amount
        delivery.updated_at = now
        delivery.save(
            update_fields=[
                "status",
                "completed_at",
                "receiver_name",
                "payment_reference",
                "payment_at",
                "payment_amount",
                "updated_at",
            ]
        )
    return serialize_delivery(delivery, board="completed", receipt=receipt)
