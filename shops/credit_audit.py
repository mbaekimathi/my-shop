"""Audit trail for client credit accounts — payments and credit-note changes."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.http import Http404
from django.utils import timezone

from employees.analytics_services import _due_amount, _money_ksh, _zero
from shops.models import (
    Client,
    ClientCreditAccountEvent,
    ClientCreditAccountEventKind,
    MpesaStkPayment,
    MpesaStkPurpose,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptStatus,
)


def _actor_label(profile) -> str:
    if profile is None:
        return "Client"
    user = getattr(profile, "user", None)
    if user:
        return user.get_full_name() or profile.employee_id or user.username or "Staff"
    return profile.employee_id or "Staff"


def _event_tone(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value in {"payment_cash", "payment_mpesa"}:
        return "good"
    if value in {"due_date_set", "due_date_changed"}:
        return "warn"
    if value in {"items_returned", "credit_cancelled"}:
        return "bad"
    return "neutral"


def _event_icon(kind: str) -> str:
    value = (kind or "").strip().lower()
    icons = {
        "credit_issued": "file-plus",
        "payment_cash": "banknote",
        "payment_mpesa": "smartphone",
        "due_date_set": "calendar-plus",
        "due_date_changed": "calendar-clock",
        "items_returned": "undo-2",
        "credit_cancelled": "ban",
    }
    return icons.get(value, "activity")


def log_client_credit_event(
    *,
    client_id: int,
    kind: str,
    occurred_at=None,
    shop=None,
    receipt=None,
    stk_payment=None,
    amount=None,
    detail: str = "",
    meta=None,
    actor=None,
) -> ClientCreditAccountEvent:
    """Persist one client credit account audit event."""
    payload = dict(meta or {})
    if stk_payment is not None and stk_payment.pk:
        payload.setdefault("stk_payment_id", stk_payment.pk)
    return ClientCreditAccountEvent.objects.create(
        client_id=int(client_id),
        shop=shop,
        receipt=receipt,
        stk_payment=stk_payment,
        kind=kind,
        amount=amount,
        detail=(detail or "")[:255],
        meta=payload,
        actor=actor,
        occurred_at=occurred_at or timezone.now(),
    )


def log_credit_receipt_issued(*, receipt: ShopReceipt) -> None:
    if receipt.client_id is None or receipt.kind != ShopReceiptKind.CREDIT:
        return
    detail_bits = [receipt.receipt_number]
    if receipt.shop:
        detail_bits.append(receipt.shop.name)
    if receipt.credit_due_date:
        detail_bits.append(f"Pay by {receipt.credit_due_date.strftime('%d %b %Y')}")
    log_client_credit_event(
        client_id=receipt.client_id,
        kind=ClientCreditAccountEventKind.CREDIT_ISSUED,
        occurred_at=receipt.created_at,
        shop=receipt.shop,
        receipt=receipt,
        amount=receipt.total,
        detail=" · ".join(detail_bits),
        actor=receipt.created_by,
    )
    if receipt.credit_due_date:
        log_client_credit_event(
            client_id=receipt.client_id,
            kind=ClientCreditAccountEventKind.DUE_DATE_SET,
            occurred_at=receipt.created_at,
            shop=receipt.shop,
            receipt=receipt,
            detail=f"Pay by {receipt.credit_due_date.strftime('%d %b %Y')} on {receipt.receipt_number}",
            meta={"due_date": receipt.credit_due_date.isoformat()},
            actor=receipt.created_by,
        )


def log_credit_payment(
    *,
    client_id: int,
    receipt: ShopReceipt,
    amount: Decimal,
    payment_method: str,
    actor=None,
    stk_payment=None,
    mpesa_receipt_number: str = "",
    occurred_at=None,
) -> None:
    method = (payment_method or "cash").strip().lower()
    kind = (
        ClientCreditAccountEventKind.PAYMENT_MPESA
        if method == "mpesa"
        else ClientCreditAccountEventKind.PAYMENT_CASH
    )
    ref_bit = f" · Ref {mpesa_receipt_number}" if mpesa_receipt_number else ""
    due_after = _due_amount(receipt.total, receipt.amount_paid)
    detail = (
        f"{_money_ksh(amount)} on {receipt.receipt_number}{ref_bit} · "
        f"Due after {_money_ksh(due_after)}"
    )
    log_client_credit_event(
        client_id=client_id,
        kind=kind,
        occurred_at=occurred_at,
        shop=receipt.shop,
        receipt=receipt,
        stk_payment=stk_payment,
        amount=amount,
        detail=detail,
        actor=actor,
        meta={"mpesa_receipt_number": mpesa_receipt_number or ""},
    )


def log_credit_due_date_change(
    *,
    receipt: ShopReceipt,
    old_date,
    new_date,
    actor=None,
) -> None:
    if receipt.client_id is None:
        return
    kind = (
        ClientCreditAccountEventKind.DUE_DATE_SET
        if old_date is None
        else ClientCreditAccountEventKind.DUE_DATE_CHANGED
    )
    if old_date is None:
        detail = f"Pay by {new_date.strftime('%d %b %Y')} on {receipt.receipt_number}"
    else:
        detail = (
            f"{receipt.receipt_number}: "
            f"{old_date.strftime('%d %b %Y')} → {new_date.strftime('%d %b %Y')}"
        )
    log_client_credit_event(
        client_id=receipt.client_id,
        kind=kind,
        shop=receipt.shop,
        receipt=receipt,
        detail=detail,
        actor=actor,
        meta={
            "old_due_date": old_date.isoformat() if old_date else "",
            "new_due_date": new_date.isoformat(),
        },
    )


def log_credit_return(
    *,
    receipt: ShopReceipt,
    summary: str,
    actor=None,
    occurred_at=None,
) -> None:
    if receipt.client_id is None or receipt.kind != ShopReceiptKind.CREDIT:
        return
    kind = (
        ClientCreditAccountEventKind.CREDIT_CANCELLED
        if receipt.status == ShopReceiptStatus.CANCELLED
        else ClientCreditAccountEventKind.ITEMS_RETURNED
    )
    log_client_credit_event(
        client_id=receipt.client_id,
        kind=kind,
        occurred_at=occurred_at or receipt.last_returned_at or timezone.now(),
        shop=receipt.shop,
        receipt=receipt,
        amount=receipt.total,
        detail=(summary or receipt.receipt_number)[:255],
        actor=actor or receipt.last_returned_by,
    )


def ensure_client_credit_audit_backfill(*, client_id: int, shop_ids: list[int]) -> None:
    """Backfill audit events from existing receipts and M-Pesa records (idempotent)."""
    shop_ids = [int(value) for value in shop_ids or [] if int(value) > 0]
    if not shop_ids:
        return

    receipts = list(
        ShopReceipt.objects.filter(
            client_id=client_id,
            kind=ShopReceiptKind.CREDIT,
            shop_id__in=shop_ids,
        )
        .select_related("shop", "created_by", "created_by__user", "last_returned_by")
        .order_by("created_at", "pk")
    )
    if not receipts:
        return

    receipt_ids = [row.pk for row in receipts]
    existing_kinds_by_receipt: dict[int, set[str]] = {}
    for receipt_id, kind in ClientCreditAccountEvent.objects.filter(
        receipt_id__in=receipt_ids
    ).values_list("receipt_id", "kind"):
        existing_kinds_by_receipt.setdefault(receipt_id, set()).add(kind)

    logged_stk_ids = set(
        ClientCreditAccountEvent.objects.filter(
            client_id=client_id,
            stk_payment_id__isnull=False,
        ).values_list("stk_payment_id", flat=True)
    )

    stk_rows = list(
        MpesaStkPayment.objects.filter(
            purpose=MpesaStkPurpose.CREDIT,
            account_kind="credit",
            account_id=client_id,
            applied=True,
            receipt__shop_id__in=shop_ids,
        )
        .select_related("receipt", "receipt__shop", "created_by", "created_by__user")
        .order_by("completed_at", "updated_at", "created_at", "pk")
    )

    with transaction.atomic():
        for receipt in receipts:
            kinds = existing_kinds_by_receipt.get(receipt.pk, set())
            if ClientCreditAccountEventKind.CREDIT_ISSUED not in kinds:
                log_credit_receipt_issued(receipt=receipt)
                kinds.add(ClientCreditAccountEventKind.CREDIT_ISSUED)
                if receipt.credit_due_date:
                    kinds.add(ClientCreditAccountEventKind.DUE_DATE_SET)

            if receipt.last_returned_at:
                return_kind = (
                    ClientCreditAccountEventKind.CREDIT_CANCELLED
                    if receipt.status == ShopReceiptStatus.CANCELLED
                    else ClientCreditAccountEventKind.ITEMS_RETURNED
                )
                if return_kind not in kinds:
                    summary = f"Return on {receipt.receipt_number}"
                    if receipt.status == ShopReceiptStatus.CANCELLED:
                        summary = f"{receipt.receipt_number} fully returned and cancelled"
                    log_credit_return(
                        receipt=receipt,
                        summary=summary,
                        occurred_at=receipt.last_returned_at,
                    )
                    kinds.add(return_kind)

            stk_for_receipt = [row for row in stk_rows if row.receipt_id == receipt.pk]
            stk_total = sum((Decimal(row.amount or 0) for row in stk_for_receipt), _zero())
            for row in stk_for_receipt:
                if row.pk in logged_stk_ids:
                    continue
                when = row.completed_at or row.updated_at or row.created_at
                log_credit_payment(
                    client_id=client_id,
                    receipt=receipt,
                    amount=Decimal(row.amount or 0),
                    payment_method="mpesa",
                    actor=row.created_by,
                    stk_payment=row,
                    mpesa_receipt_number=row.mpesa_receipt_number or "",
                    occurred_at=when,
                )
                logged_stk_ids.add(row.pk)

            paid = Decimal(receipt.amount_paid or 0)
            cash_total = (paid - stk_total).quantize(Decimal("0.01"))
            has_cash_event = ClientCreditAccountEventKind.PAYMENT_CASH in kinds
            if cash_total > 0 and not has_cash_event:
                log_client_credit_event(
                    client_id=client_id,
                    kind=ClientCreditAccountEventKind.PAYMENT_CASH,
                    occurred_at=receipt.created_at,
                    shop=receipt.shop,
                    receipt=receipt,
                    amount=cash_total,
                    detail=(
                        f"{_money_ksh(cash_total)} on {receipt.receipt_number} "
                        f"(recorded before audit trail)"
                    ),
                    meta={"synthetic": True},
                )


def build_client_credit_audit_trail(*, profile, client_id: int) -> dict:
    """Chronological audit feed for one client credit account."""
    from employees.analytics_services import actionable_shops_for_profile

    shop_ids = [shop.pk for shop in actionable_shops_for_profile(profile)]
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise Http404("Client not found.")

    ensure_client_credit_audit_backfill(client_id=client.pk, shop_ids=shop_ids)

    events = list(
        ClientCreditAccountEvent.objects.filter(
            client_id=client.pk,
            shop_id__in=shop_ids,
        )
        .select_related(
            "shop",
            "receipt",
            "actor",
            "actor__user",
            "stk_payment",
        )
        .order_by("-occurred_at", "-pk")
    )

    rows = []
    payment_count = 0
    change_count = 0
    for event in events:
        kind = event.kind
        if kind in {
            ClientCreditAccountEventKind.PAYMENT_CASH,
            ClientCreditAccountEventKind.PAYMENT_MPESA,
        }:
            payment_count += 1
        elif kind in {
            ClientCreditAccountEventKind.DUE_DATE_SET,
            ClientCreditAccountEventKind.DUE_DATE_CHANGED,
            ClientCreditAccountEventKind.ITEMS_RETURNED,
            ClientCreditAccountEventKind.CREDIT_CANCELLED,
        }:
            change_count += 1
        when = timezone.localtime(event.occurred_at)
        rows.append(
            {
                "id": event.pk,
                "when": when,
                "when_label": when.strftime("%d %b %Y · %H:%M"),
                "kind": kind,
                "kind_label": event.get_kind_display(),
                "tone": _event_tone(kind),
                "icon": _event_icon(kind),
                "receipt_number": event.receipt.receipt_number if event.receipt else "—",
                "shop": event.shop.name if event.shop else "—",
                "amount": _money_ksh(event.amount) if event.amount is not None else "—",
                "detail": event.detail or event.get_kind_display(),
                "actor": _actor_label(event.actor),
                "synthetic": bool((event.meta or {}).get("synthetic")),
            }
        )

    return {
        "client": client,
        "rows": rows,
        "event_count": len(rows),
        "payment_count": payment_count,
        "change_count": change_count,
        "empty_message": "No credit payments or account changes recorded yet.",
    }


def client_credit_audit_url(role, client_id, *, query: str = "") -> str:
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        "employees:analytics_client_credit_audit",
        kwargs={
            "role_segment": role_url_segment(role),
            "client_id": int(client_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href
