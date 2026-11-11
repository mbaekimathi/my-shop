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
    if value in {
        "due_date_set",
        "due_date_changed",
        "follow_up_set",
        "follow_up_changed",
    }:
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
        "follow_up_set": "calendar-plus",
        "follow_up_changed": "calendar-clock",
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
        meta={
            "mpesa_receipt_number": mpesa_receipt_number or "",
            "cleared": due_after <= 0,
            "due_after": str(due_after),
        },
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


def log_credit_follow_up_change(
    *,
    client_id: int,
    old_date,
    new_date,
    actor=None,
    shop=None,
) -> None:
    if not client_id:
        return
    kind = (
        ClientCreditAccountEventKind.FOLLOW_UP_SET
        if old_date is None
        else ClientCreditAccountEventKind.FOLLOW_UP_CHANGED
    )
    if old_date is None:
        detail = f"Account follow-up set for {new_date.strftime('%d %b %Y')}"
    else:
        detail = (
            "Account follow-up: "
            f"{old_date.strftime('%d %b %Y')} → {new_date.strftime('%d %b %Y')}"
        )
    log_client_credit_event(
        client_id=client_id,
        kind=kind,
        shop=shop,
        receipt=None,
        detail=detail,
        actor=actor,
        meta={
            "old_follow_up_date": old_date.isoformat() if old_date else "",
            "new_follow_up_date": new_date.isoformat(),
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


def build_client_credit_audit_trail(*, profile, client_id: int, request=None) -> dict:
    """Chronological audit feed for one client credit account (optionally period-scoped)."""
    from employees.analytics_services import (
        _allocated_shop_filter,
        _date_filter_context,
        _within_created_range,
        actionable_shops_for_profile,
    )

    shop_filter = _allocated_shop_filter(profile, request)
    shop_ids = shop_filter["active_shop_ids"]
    if not shop_ids:
        shop_ids = [shop.pk for shop in actionable_shops_for_profile(profile)]
    date_filter = _date_filter_context(request, allow_all_time=True)
    start, end = date_filter.get("start"), date_filter.get("end")
    period_label = date_filter.get("report_period_label") or "All time"

    client = Client.objects.filter(pk=client_id).first()
    if client is None or not shop_ids:
        raise Http404("Client not found.")

    if not (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exists()
    ):
        raise Http404("Client not found.")

    ensure_client_credit_audit_backfill(client_id=client.pk, shop_ids=shop_ids)

    period_receipt_ids = list(
        _within_created_range(
            ShopReceipt.objects.filter(
                client_id=client.pk,
                kind=ShopReceiptKind.CREDIT,
                shop_id__in=shop_ids,
            ).exclude(status=ShopReceiptStatus.CANCELLED),
            start,
            end,
        ).values_list("pk", flat=True)
    )

    events_qs = ClientCreditAccountEvent.objects.filter(
        client_id=client.pk,
        shop_id__in=shop_ids,
    )
    if start is not None and end is not None:
        # Only activity for credit receipts in the filtered period — never
        # surface transactions tied to credits outside the date filter.
        events_qs = events_qs.filter(receipt_id__in=period_receipt_ids)
    events = list(
        events_qs.select_related(
            "shop",
            "receipt",
            "actor",
            "actor__user",
            "stk_payment",
        ).order_by("-occurred_at", "-pk")
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

    scoped = start is not None and end is not None
    empty_message = (
        f"No credit payments or account changes for receipts in {period_label}."
        if scoped
        else "No credit payments or account changes recorded yet."
    )

    return {
        "client": client,
        "rows": rows,
        "event_count": len(rows),
        "payment_count": payment_count,
        "change_count": change_count,
        "empty_message": empty_message,
        **date_filter,
    }


CREDIT_AUDIT_EXCLUDED_KINDS = frozenset({ClientCreditAccountEventKind.CREDIT_ISSUED})


def credit_audit_event_options() -> list[dict]:
    """Filter choices for the credit audits page (credit sales stay hidden)."""
    options = [{"slug": "", "label": "All events", "kinds": None}]
    for value, label in ClientCreditAccountEventKind.choices:
        if value in CREDIT_AUDIT_EXCLUDED_KINDS:
            continue
        options.append({"slug": value, "label": label, "kinds": [value]})
    return options


def parse_credit_audit_event_filter(raw) -> dict:
    slug = (raw or "").strip().lower()
    if slug in {"", "all"}:
        return credit_audit_event_options()[0]
    for option in credit_audit_event_options():
        if option["slug"] == slug:
            return option
    return credit_audit_event_options()[0]


def build_credit_audits(*, profile, request=None, event_kind: str = "") -> dict:
    """Chronological non-sale audit feed for all accessible credit accounts."""
    from employees.analytics_services import (
        _date_filter_context,
        _within_created_range,
        actionable_shops_for_profile,
    )
    from employees.models import EmployeeProfile, EmployeeStatus

    event_option = parse_credit_audit_event_filter(event_kind)
    event_filter = event_option["slug"]
    event_filter_label = event_option["label"]
    event_kinds = event_option["kinds"]
    date_filter = _date_filter_context(request)
    start, end = date_filter.get("start"), date_filter.get("end")
    filter_shops = list(actionable_shops_for_profile(profile))
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    allowed_shop_ids = [shop.pk for shop in filter_shops]

    try:
        shop_filter_id = int((getattr(request, "GET", {}) or {}).get("shop_id") or 0)
    except (TypeError, ValueError):
        shop_filter_id = 0
    if shop_filter_id and shop_filter_id not in shops_by_id:
        shop_filter_id = 0

    try:
        employee_filter_id = int(
            (getattr(request, "GET", {}) or {}).get("employee_id") or 0
        )
    except (TypeError, ValueError):
        employee_filter_id = 0

    filter_employees = list(
        EmployeeProfile.objects.filter(status=EmployeeStatus.ACTIVE)
        .select_related("user")
        .only(
            "id",
            "employee_id",
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        )
        .order_by("employee_id")[:300]
    )
    if employee_filter_id and not any(
        emp.pk == employee_filter_id for emp in filter_employees
    ):
        employee_filter_id = 0

    shop_ids = [shop_filter_id] if shop_filter_id else allowed_shop_ids
    period_bits = [date_filter.get("report_period_label") or ""]
    if event_filter:
        period_bits.append(event_filter_label)
    period_label = " · ".join(bit for bit in period_bits if bit)
    empty_message = (
        "No matching credit events recorded yet."
        if event_filter or shop_filter_id or employee_filter_id
        else "No credit account changes recorded yet."
    )
    empty = {
        **date_filter,
        "headline": "Activity analytics",
        "lead": (
            "Payments, due-date updates, returns, and cancellations on credit "
            "receipts. Filter by period, shop, and employee."
        ),
        "period_label": period_label,
        "event_options": credit_audit_event_options(),
        "event_filter": event_filter,
        "event_filter_label": event_filter_label,
        "filter_shops": filter_shops,
        "filter_employees": filter_employees,
        "selected_shop_id": shop_filter_id or 0,
        "selected_employee_id": employee_filter_id or 0,
        "download_href": "",
        "rows": [],
        "event_count": 0,
        "payment_count": 0,
        "change_count": 0,
        "empty_message": empty_message,
        "summary_board": {
            "hero": {
                "label": "Recorded changes",
                "value": "0",
                "hint": "No activity yet",
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Payments",
                    "value": "0",
                    "hint": "Cash · M-Pesa",
                    "icon": "wallet",
                    "tone": "cash",
                },
                {
                    "label": "Other changes",
                    "value": "0",
                    "hint": "Due dates · returns",
                    "icon": "history",
                    "tone": "warn",
                },
                {
                    "label": "Paid amount",
                    "value": _money_ksh(0),
                    "hint": "Collected on credits",
                    "icon": "banknote",
                    "tone": "good",
                },
                {
                    "label": "Clients",
                    "value": "0",
                    "hint": "With audit activity",
                    "icon": "contact",
                    "tone": "shops",
                },
            ],
        },
    }
    if not shop_ids:
        return empty

    # Read existing audit events only — no per-client backfill on page load.
    from django.db.models import Q

    events_qs = ClientCreditAccountEvent.objects.filter(
        Q(shop_id__in=shop_ids)
        | Q(
            shop_id__isnull=True,
            kind__in=[
                ClientCreditAccountEventKind.FOLLOW_UP_SET,
                ClientCreditAccountEventKind.FOLLOW_UP_CHANGED,
            ],
        )
    ).exclude(kind__in=CREDIT_AUDIT_EXCLUDED_KINDS)
    if event_kinds:
        events_qs = events_qs.filter(kind__in=event_kinds)
    if employee_filter_id:
        events_qs = events_qs.filter(actor_id=employee_filter_id)
    events_qs = _within_created_range(
        events_qs, start, end, lookup="occurred_at"
    )
    events = list(
        events_qs.select_related(
            "client",
            "shop",
            "receipt",
            "actor",
            "actor__user",
        )
        .only(
            "id",
            "kind",
            "amount",
            "detail",
            "meta",
            "occurred_at",
            "client_id",
            "client__full_name",
            "shop_id",
            "shop__name",
            "receipt_id",
            "receipt__receipt_number",
            "actor_id",
            "actor__employee_id",
            "actor__user__first_name",
            "actor__user__last_name",
            "actor__user__username",
        )
        .order_by("-occurred_at", "-pk")[:1000]
    )

    rows = []
    payment_count = 0
    change_count = 0
    paid_total = _zero()
    client_names: set[str] = set()
    for event in events:
        kind = event.kind
        if kind in {
            ClientCreditAccountEventKind.PAYMENT_CASH,
            ClientCreditAccountEventKind.PAYMENT_MPESA,
        }:
            payment_count += 1
            if event.amount is not None:
                paid_total += Decimal(event.amount or 0)
        else:
            change_count += 1
        when = timezone.localtime(event.occurred_at)
        client_name = event.client.full_name if event.client else "—"
        if event.client_id:
            client_names.add(client_name)
        rows.append(
            {
                "id": event.pk,
                "when_label": when.strftime("%d %b %Y · %H:%M"),
                "client": client_name,
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

    event_count = len(rows)
    client_count = len(client_names)
    from employees.analytics_services import _pdf_download_href

    raw_query = ""
    if request is not None:
        raw_query = request.GET.urlencode()
    return {
        **date_filter,
        "headline": "Activity analytics",
        "lead": (
            "Payments, due-date updates, returns, and cancellations on credit "
            "receipts. Filter by period, shop, and employee."
        ),
        "download_href": _pdf_download_href(raw_query),
        "period_label": period_label,
        "event_options": credit_audit_event_options(),
        "event_filter": event_filter,
        "event_filter_label": event_filter_label,
        "filter_shops": filter_shops,
        "filter_employees": filter_employees,
        "selected_shop_id": shop_filter_id or 0,
        "selected_employee_id": employee_filter_id or 0,
        "rows": rows,
        "event_count": event_count,
        "payment_count": payment_count,
        "change_count": change_count,
        "empty_message": empty_message,
        "summary_board": {
            "hero": {
                "label": "Recorded changes",
                "value": str(event_count),
                "hint": (
                    f"{payment_count} payment{'s' if payment_count != 1 else ''} · "
                    f"{change_count} other change{'s' if change_count != 1 else ''}"
                ),
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Payments",
                    "value": str(payment_count),
                    "hint": "Cash · M-Pesa",
                    "icon": "wallet",
                    "tone": "cash",
                },
                {
                    "label": "Other changes",
                    "value": str(change_count),
                    "hint": "Due dates · returns",
                    "icon": "history",
                    "tone": "warn" if change_count else "good",
                },
                {
                    "label": "Paid amount",
                    "value": _money_ksh(paid_total),
                    "hint": "Collected on credits",
                    "icon": "banknote",
                    "tone": "good",
                },
                {
                    "label": "Clients",
                    "value": str(client_count),
                    "hint": "With audit activity",
                    "icon": "contact",
                    "tone": "shops",
                },
            ],
        },
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


CREDIT_FOLLOW_UP_OUTCOME_OPTIONS = (
    {"slug": "", "label": "All outcomes"},
    {"slug": "follow_up", "label": "Follow-up set"},
    {"slug": "cleared", "label": "Credit cleared"},
)

_FOLLOW_UP_EVENT_KINDS = frozenset(
    {
        ClientCreditAccountEventKind.FOLLOW_UP_SET,
        ClientCreditAccountEventKind.FOLLOW_UP_CHANGED,
    }
)
_CLEAR_EVENT_KINDS = frozenset(
    {
        ClientCreditAccountEventKind.PAYMENT_CASH,
        ClientCreditAccountEventKind.PAYMENT_MPESA,
        ClientCreditAccountEventKind.CREDIT_CANCELLED,
    }
)


def _event_cleared_credit(event) -> bool:
    """True when this event closed the credit (paid in full or cancelled)."""
    if _event_cleared_credit_fast(event):
        return True
    if event.kind not in {
        ClientCreditAccountEventKind.PAYMENT_CASH,
        ClientCreditAccountEventKind.PAYMENT_MPESA,
    }:
        return False
    receipt = getattr(event, "receipt", None)
    if receipt is None:
        return False
    return _due_amount(receipt.total, receipt.amount_paid) <= 0


def _event_cleared_credit_fast(event) -> bool:
    """Cleared check from event fields only — no related receipt fetch."""
    kind = event.kind
    if kind == ClientCreditAccountEventKind.CREDIT_CANCELLED:
        return True
    if kind not in {
        ClientCreditAccountEventKind.PAYMENT_CASH,
        ClientCreditAccountEventKind.PAYMENT_MPESA,
    }:
        return False
    meta = event.meta or {}
    if meta.get("cleared") is True:
        return True
    due_after = meta.get("due_after")
    if due_after is not None:
        try:
            return Decimal(str(due_after)) <= 0
        except Exception:
            pass
    return "Due after KSh 0" in (event.detail or "")


def build_credit_follow_ups_page(*, profile, request=None) -> dict:
    """List credit clients for allocated shops with period follow-up/clear status."""
    from django.db.models import Q
    from django.urls import reverse

    from employees.access import role_url_segment
    from employees.analytics_services import (
        _date_filter_context,
        _within_created_range,
        actionable_shops_for_profile,
    )

    date_filter = _date_filter_context(request)
    start, end = date_filter.get("start"), date_filter.get("end")
    filter_shops = list(actionable_shops_for_profile(profile))
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    allowed_shop_ids = [shop.pk for shop in filter_shops]

    getter = getattr(request, "GET", {}) or {}
    try:
        shop_filter_id = int(getter.get("shop_id") or 0)
    except (TypeError, ValueError):
        shop_filter_id = 0
    if shop_filter_id and shop_filter_id not in shops_by_id:
        shop_filter_id = 0

    outcome_raw = (getter.get("outcome") or "").strip().lower()
    if outcome_raw not in {"follow_up", "cleared"}:
        outcome_raw = ""

    shop_ids = [shop_filter_id] if shop_filter_id else allowed_shop_ids
    period_bits = [date_filter.get("report_period_label") or ""]
    if outcome_raw == "follow_up":
        period_bits.append("Follow-up set")
    elif outcome_raw == "cleared":
        period_bits.append("Credit cleared")
    period_label = " · ".join(bit for bit in period_bits if bit)

    role_segment = role_url_segment(profile.role)
    account_href_template = reverse(
        "employees:analytics_client_credit",
        kwargs={"role_segment": role_segment, "client_id": 0},
    )

    def _account_href(client_id: int) -> str:
        return account_href_template.replace("/0/", f"/{int(client_id)}/")

    def _empty(*, message: str) -> dict:
        return {
            **date_filter,
            "headline": "Credit follow-up",
            "lead": (
                "Credit clients for your shops that had a follow-up set "
                "or a credit cleared in the selected period."
            ),
            "period_label": period_label,
            "outcome_options": list(CREDIT_FOLLOW_UP_OUTCOME_OPTIONS),
            "outcome_filter": outcome_raw,
            "filter_shops": filter_shops,
            "selected_shop_id": shop_filter_id or 0,
            "rows": [],
            "follow_up_count": 0,
            "cleared_count": 0,
            "client_count": 0,
            "empty_message": message,
            "summary_board": {
                "hero": {
                    "label": "Clients updated",
                    "value": "0",
                    "hint": "No activity yet",
                    "tone": "neutral",
                },
                "tiles": [
                    {
                        "label": "Follow-ups",
                        "value": "0",
                        "hint": "In this period",
                        "icon": "calendar-clock",
                        "tone": "warn",
                    },
                    {
                        "label": "Cleared",
                        "value": "0",
                        "hint": "In this period",
                        "icon": "badge-check",
                        "tone": "good",
                    },
                    {
                        "label": "Shops",
                        "value": str(len(shop_ids)),
                        "hint": "In your allocation",
                        "icon": "store",
                        "tone": "shops",
                    },
                ],
            },
        }

    if not shop_ids:
        return _empty(message="No shops allocated to your account.")

    from django.db.models import Max, Sum
    from django.db.models.functions import Coalesce

    credit_base = ShopReceipt.objects.filter(
        shop_id__in=shop_ids,
        kind=ShopReceiptKind.CREDIT,
        client_id__isnull=False,
    ).exclude(status=ShopReceiptStatus.CANCELLED)

    balance_rows = list(
        credit_base.values("client_id").annotate(
            total_sum=Coalesce(Sum("total"), _zero()),
            paid_sum=Coalesce(Sum("amount_paid"), _zero()),
            latest_receipt_id=Max("id"),
        )
    )
    if not balance_rows:
        return _empty(message="No follow-ups or cleared credits in this period.")

    credit_client_ids = [int(row["client_id"]) for row in balance_rows]
    balance_by_client = {
        int(row["client_id"]): _due_amount(row["total_sum"], row["paid_sum"])
        for row in balance_rows
    }
    latest_receipt_ids = [
        int(row["latest_receipt_id"])
        for row in balance_rows
        if row.get("latest_receipt_id")
    ]
    shop_by_client = {
        int(client_id): (shop_name or "").strip() or "—"
        for client_id, shop_name in ShopReceipt.objects.filter(
            pk__in=latest_receipt_ids
        ).values_list("client_id", "shop__name")
    }

    clients = {
        row.pk: row
        for row in Client.objects.filter(pk__in=credit_client_ids).only(
            "id", "full_name", "credit_follow_up_date"
        )
    }

    by_client: dict[int, dict] = {}
    for client_id in credit_client_ids:
        client = clients.get(client_id)
        follow_up_date = ""
        if client and client.credit_follow_up_date:
            follow_up_date = client.credit_follow_up_date.strftime("%d %b %Y")
        by_client[client_id] = {
            "client_id": client_id,
            "client": (client.full_name if client else "") or "—",
            "shop": shop_by_client.get(client_id, "—"),
            "balance": balance_by_client.get(client_id, _zero()),
            "follow_up": False,
            "cleared": False,
            "follow_up_date": follow_up_date,
            "when_label": "—",
            "when_sort": None,
            "actor": "—",
            "detail": "No follow-up or clearance in this period.",
            "amount": "—",
            "href": _account_href(client_id),
            "outcomes": [],
        }

    # Follow-up/clear events are written at save time — no audit backfill.

    events_qs = ClientCreditAccountEvent.objects.filter(
        client_id__in=credit_client_ids,
        kind__in=list(_FOLLOW_UP_EVENT_KINDS | _CLEAR_EVENT_KINDS),
    ).filter(
        Q(shop_id__in=shop_ids)
        | Q(shop_id__isnull=True, kind__in=list(_FOLLOW_UP_EVENT_KINDS))
    )
    events_qs = _within_created_range(events_qs, start, end, lookup="occurred_at")
    events = list(
        events_qs.select_related("shop", "actor", "actor__user").order_by(
            "-occurred_at", "-pk"
        )
    )

    for event in events:
        row = by_client.get(event.client_id)
        if row is None:
            continue
        is_follow_up = event.kind in _FOLLOW_UP_EVENT_KINDS
        is_cleared = _event_cleared_credit_fast(event)
        if event.kind in {
            ClientCreditAccountEventKind.PAYMENT_CASH,
            ClientCreditAccountEventKind.PAYMENT_MPESA,
        } and not is_cleared:
            continue
        if not is_follow_up and not is_cleared:
            continue
        if is_follow_up:
            row["follow_up"] = True
        if is_cleared:
            row["cleared"] = True
        when = timezone.localtime(event.occurred_at)
        if row["when_sort"] is None or event.occurred_at > row["when_sort"]:
            row["when_label"] = when.strftime("%d %b %Y · %H:%M")
            row["when_sort"] = event.occurred_at
            row["actor"] = _actor_label(event.actor)
            row["detail"] = event.detail or event.get_kind_display()
            if event.amount is not None:
                row["amount"] = _money_ksh(event.amount)
            if event.shop:
                row["shop"] = event.shop.name

    rows = []
    follow_up_count = 0
    cleared_count = 0
    for row in by_client.values():
        # Only credits that were followed up or cleared in this period.
        if not row["follow_up"] and not row["cleared"]:
            continue
        if outcome_raw == "follow_up" and not row["follow_up"]:
            continue
        if outcome_raw == "cleared" and not row["cleared"]:
            continue
        outcomes = []
        if row["follow_up"]:
            outcomes.append(
                {
                    "label": "Follow-up set",
                    "tone": "warn",
                    "icon": "calendar-clock",
                }
            )
            follow_up_count += 1
        if row["cleared"]:
            outcomes.append(
                {
                    "label": "Credit cleared",
                    "tone": "good",
                    "icon": "badge-check",
                }
            )
            cleared_count += 1
        row["outcomes"] = outcomes
        row["balance_label"] = _money_ksh(row["balance"])
        if row["cleared"] and not row["follow_up"]:
            row["tone"] = "good"
        elif row["follow_up"]:
            row["tone"] = "warn"
        else:
            row["tone"] = "neutral"
        rows.append(row)

    rows.sort(
        key=lambda item: (
            item["when_sort"] is None,
            -(item["when_sort"].timestamp() if item["when_sort"] else 0),
            item["client"].lower(),
        )
    )
    for row in rows:
        row.pop("when_sort", None)
        row.pop("balance", None)

    client_count = len(rows)
    return {
        **date_filter,
        "headline": "Credit follow-up",
        "lead": (
            "Credit clients for your shops that had a follow-up set "
            "or a credit cleared in the selected period."
        ),
        "period_label": period_label,
        "outcome_options": list(CREDIT_FOLLOW_UP_OUTCOME_OPTIONS),
        "outcome_filter": outcome_raw,
        "filter_shops": filter_shops,
        "selected_shop_id": shop_filter_id or 0,
        "rows": rows,
        "follow_up_count": follow_up_count,
        "cleared_count": cleared_count,
        "client_count": client_count,
        "empty_message": (
            "No matching follow-ups or cleared credits for this filter."
            if outcome_raw or shop_filter_id
            else "No follow-ups or cleared credits in this period."
        ),
        "summary_board": {
            "hero": {
                "label": "Clients updated",
                "value": str(client_count),
                "hint": (
                    f"{follow_up_count} follow-up{'s' if follow_up_count != 1 else ''} · "
                    f"{cleared_count} cleared"
                ),
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Follow-ups",
                    "value": str(follow_up_count),
                    "hint": "In this period",
                    "icon": "calendar-clock",
                    "tone": "warn" if follow_up_count else "neutral",
                },
                {
                    "label": "Cleared",
                    "value": str(cleared_count),
                    "hint": "In this period",
                    "icon": "badge-check",
                    "tone": "good" if cleared_count else "neutral",
                },
                {
                    "label": "Shops",
                    "value": str(len(shop_ids)),
                    "hint": "In your allocation",
                    "icon": "store",
                    "tone": "shops",
                },
            ],
        },
    }
