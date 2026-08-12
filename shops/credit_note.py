"""Signed public links for client credit notes and self-service M-Pesa payment."""

from __future__ import annotations

import base64
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.http import Http404
from django.urls import reverse

from employees.analytics_services import (
    _client_account_summary_board,
    _due_amount,
    _money_ksh,
    _payment_status_for_due,
    _status_tone,
    _zero,
)
from shops.models import Client, ShopReceipt, ShopReceiptKind, ShopReceiptStatus

_CREDIT_NOTE_SIGNER = Signer(salt="myshop-client-credit-note-v1")


def sign_client_credit_token(client_id: int) -> str:
    raw = _CREDIT_NOTE_SIGNER.sign(str(int(client_id)))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def unsign_client_credit_token(token: str) -> int:
    value = (token or "").strip()
    if not value:
        raise Http404("Credit note link is invalid.")
    pad = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(f"{value}{pad}").decode("utf-8")
        client_id = int(_CREDIT_NOTE_SIGNER.unsign(raw))
    except (BadSignature, ValueError, UnicodeDecodeError) as exc:
        raise Http404("Credit note link is invalid or expired.") from exc
    if client_id <= 0:
        raise Http404("Credit note link is invalid.")
    return client_id


def client_credit_note_path(client_id: int) -> str:
    return reverse(
        "core:credit_note",
        kwargs={"token": sign_client_credit_token(client_id)},
    )


def client_credit_note_url(client_id: int, *, request=None) -> str:
    path = client_credit_note_path(client_id)
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def credit_note_branding(*, request=None) -> dict:
    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (company.name or "").strip() or "MY-SHOP"
    site_domain = ""
    site_origin = ""
    if request is not None:
        site_domain = (request.get_host() or "").strip()
        site_origin = (request.build_absolute_uri("/") or "").rstrip("/")
    logo_url = ""
    if company.logo:
        try:
            logo_url = company.logo.url
            if request is not None and logo_url.startswith("/"):
                logo_url = request.build_absolute_uri(logo_url)
        except Exception:
            logo_url = ""
    return {
        "company_name": company_name,
        "company_logo_url": logo_url,
        "site_domain": site_domain,
        "site_origin": site_origin,
    }


def credit_note_share_message(
    *,
    client_name: str,
    balance: str,
    url: str,
    company_name: str = "MY-SHOP",
) -> str:
    name = (client_name or "there").strip()
    brand = (company_name or "MY-SHOP").strip()
    return (
        f"Hello {name},\n\n"
        f"Your credit balance at {brand} is {balance}.\n"
        f"View your credit notes and pay with M-Pesa here:\n{url}\n\n"
        f"— {brand}"
    )


def credit_note_share_context(
    *,
    request,
    client_id: int,
    client_name: str,
    balance: str,
) -> dict:
    branding = credit_note_branding(request=request)
    url = client_credit_note_url(client_id, request=request)
    message = credit_note_share_message(
        client_name=client_name,
        balance=balance,
        url=url,
        company_name=branding["company_name"],
    )
    from shops.services import _whatsapp_url

    return {
        **branding,
        "credit_note_url": url,
        "credit_note_share_message": message,
        "credit_note_whatsapp_url": _whatsapp_url(
            phone="",
            text=message,
        ),
    }


def _credit_receipt_rows(client_id: int) -> tuple[list[dict], Decimal, int, Decimal, set[int]]:
    receipts = list(
        ShopReceipt.objects.filter(
            client_id=client_id,
            kind=ShopReceiptKind.CREDIT,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .select_related("shop", "created_by", "created_by__user")
        .prefetch_related("lines")
        .order_by("-created_at")
    )
    balance = _zero()
    open_count = 0
    total_paid = _zero()
    shop_ids_seen: set[int] = set()
    rows = []
    for row in receipts:
        cashier = ""
        if row.created_by and row.created_by.user:
            cashier = (
                row.created_by.user.get_full_name()
                or row.created_by.employee_id
                or row.created_by.user.username
            )
        lines = []
        for line in row.lines.all():
            qty = int(line.quantity or 0) - int(line.returned_quantity or 0)
            if qty <= 0:
                continue
            unit = Decimal(line.unit_price or 0)
            lines.append(
                {
                    "name": line.item_name or "Item",
                    "qty": qty,
                    "unit": _money_ksh(unit),
                    "total": _money_ksh((unit * qty).quantize(Decimal("0.01"))),
                }
            )
        total = Decimal(row.total or 0)
        paid = Decimal(row.amount_paid or 0)
        due = _due_amount(total, paid)
        balance += due
        total_paid += paid
        if row.shop_id:
            shop_ids_seen.add(row.shop_id)
        if due > 0:
            open_count += 1
        status = _payment_status_for_due(due, paid)
        rows.append(
            {
                "id": f"credit-{row.pk}",
                "number": row.receipt_number,
                "shop": row.shop.name if row.shop else "—",
                "status": status,
                "status_tone": _status_tone(status),
                "total": _money_ksh(total),
                "paid": _money_ksh(paid),
                "due": _money_ksh(due),
                "due_raw": str(due),
                "can_pay": due > 0,
                "when": row.created_at,
                "cashier": cashier or "—",
                "item_count": len(lines),
                "lines": lines,
            }
        )
    return rows, balance, open_count, total_paid, shop_ids_seen


def build_client_credit_note_account(*, client_id: int) -> dict:
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise Http404("Client not found.")
    rows, balance, open_count, total_paid, shop_ids_seen = _credit_receipt_rows(client.pk)
    return {
        "client": client,
        "balance": _money_ksh(balance),
        "balance_raw": str(balance),
        "credit_count": open_count,
        "receipt_count": len(rows),
        "can_pay": balance > 0,
        "summary_board": _client_account_summary_board(
            balance=balance,
            open_count=open_count,
            receipt_count=len(rows),
            total_paid=total_paid,
            shop_count=len(shop_ids_seen),
        ),
        "rows": rows,
        "ledger_title": "Your credit notes",
        "empty_message": "You have no credit notes on file.",
    }


def apply_client_credit_note_payment(
    *,
    client_id: int,
    amount,
    phone: str = "",
    stk_payment_id: str = "",
) -> dict:
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise ValidationError("Client not found.")

    from employees.analytics_services import _parse_pay_amount

    pay_amount = _parse_pay_amount(amount)
    remaining = pay_amount
    mpesa_receipt_number = ""
    cleared = 0

    with transaction.atomic():
        from shops.daraja_stk import require_successful_stk, stk_ready

        if not stk_ready():
            raise ValidationError("M-Pesa payments are not available right now.")
        expected_phone = phone or client.phone_number or client.phone_normalized
        stk_payment = require_successful_stk(
            public_id=stk_payment_id,
            expected_amount=pay_amount,
            expected_phone=expected_phone,
            purpose="credit",
        )
        if stk_payment.applied:
            raise ValidationError("This M-Pesa payment was already applied.")
        if stk_payment.account_kind and stk_payment.account_kind != "credit":
            raise ValidationError("M-Pesa payment is not for a credit account.")
        if stk_payment.account_id and int(stk_payment.account_id) != int(client.pk):
            raise ValidationError("M-Pesa payment belongs to a different client.")
        mpesa_receipt_number = stk_payment.mpesa_receipt_number or ""

        receipts = list(
            ShopReceipt.objects.select_for_update()
            .filter(
                client_id=client.pk,
                kind=ShopReceiptKind.CREDIT,
            )
            .exclude(status=ShopReceiptStatus.CANCELLED)
            .order_by("created_at", "pk")
        )
        balance_before = sum(
            (_due_amount(row.total, row.amount_paid) for row in receipts),
            _zero(),
        )
        if balance_before <= 0:
            raise ValidationError("This account has no balance due.")
        if pay_amount > balance_before:
            raise ValidationError(
                f"Amount exceeds balance due ({_money_ksh(balance_before)})."
            )
        for receipt in receipts:
            if remaining <= 0:
                break
            due = _due_amount(receipt.total, receipt.amount_paid)
            if due <= 0:
                continue
            apply = min(remaining, due)
            receipt.amount_paid = (
                Decimal(receipt.amount_paid or 0) + apply
            ).quantize(Decimal("0.01"))
            update_fields = ["amount_paid"]
            if mpesa_receipt_number and not receipt.mpesa_receipt_number:
                receipt.mpesa_receipt_number = mpesa_receipt_number
                update_fields.append("mpesa_receipt_number")
            receipt.save(update_fields=update_fields)
            remaining = (remaining - apply).quantize(Decimal("0.01"))
            cleared += 1
            from shops.credit_audit import log_credit_payment

            log_credit_payment(
                client_id=client.pk,
                receipt=receipt,
                amount=apply,
                payment_method="mpesa",
                actor=None,
                stk_payment=stk_payment,
                mpesa_receipt_number=mpesa_receipt_number,
            )

        stk_payment.applied = True
        stk_payment.save(update_fields=["applied", "updated_at"])

        balance_after = sum(
            (_due_amount(row.total, row.amount_paid) for row in receipts),
            _zero(),
        )
        ref_bit = f" · Ref {mpesa_receipt_number}" if mpesa_receipt_number else ""
        return {
            "ok": True,
            "kind": "credit",
            "account_id": client.pk,
            "cleared": cleared,
            "payment_method": "mpesa",
            "mpesa_receipt_number": mpesa_receipt_number,
            "balance_before": _money_ksh(balance_before),
            "balance_after": _money_ksh(balance_after),
            "message": (
                f"M-Pesa payment of {_money_ksh(pay_amount)} recorded{ref_bit}. "
                f"Balance is now {_money_ksh(balance_after)}."
            ),
        }
