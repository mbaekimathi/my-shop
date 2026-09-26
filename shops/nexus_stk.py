"""Nexus collections STK helpers (any Nexus-issued collection API key)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    CompanyDarajaSettings,
    MpesaStkPayment,
    MpesaStkPurpose,
    MpesaStkStatus,
    StkProvider,
)
from .services import _money, get_daraja_settings


def nexus_stk_url() -> str:
    return (
        getattr(settings, "NEXUS_STK_URL", None)
        or "https://fin.richcom.co.ke/api/v1/collections/stk/"
    ).strip()


def _nexus_request(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 20,
) -> dict:
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request_obj = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
            detail = (
                payload.get("detail")
                or payload.get("message")
                or payload.get("error")
                or ""
            )
            if isinstance(detail, list):
                detail = "; ".join(str(part) for part in detail)
        except Exception:
            detail = ""
        message = str(detail).strip() or "Nexus collections API rejected the request."
        raise ValidationError(message) from exc
    except urllib.error.URLError as exc:
        raise ValidationError(
            "Could not reach Nexus collections API. Check your internet connection."
        ) from exc

    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("Nexus collections API returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        return {"data": payload}
    return payload


def verify_nexus_collection_api_key(api_key: str) -> dict:
    key = (api_key or "").strip()
    if not key:
        raise ValidationError("Nexus collection API key is required.")
    if len(key) < 8:
        raise ValidationError("Enter the full API key from your Nexus collection account.")
    payload = _nexus_request(nexus_stk_url(), api_key=key, method="GET")
    collection_id = (
        payload.get("collection_id")
        or payload.get("collectionId")
        or payload.get("bill_ref_number")
        or payload.get("BillRefNumber")
        or ""
    )
    if isinstance(collection_id, str):
        collection_id = collection_id.strip()
    else:
        collection_id = str(collection_id or "").strip()
    return {"ok": True, "collection_id": collection_id, "payload": payload}


def _party_phone(phone: str) -> str:
    from shops.daraja_stk import _party_phone as daraja_party_phone

    return daraja_party_phone(phone)


def _pick_str(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _nexus_status_from_payload(payload: dict) -> tuple[str, str, str]:
    """Return (result_code, result_desc, mpesa_receipt) from a Nexus status body."""
    status = _pick_str(payload, "status", "state", "payment_status").lower()
    receipt = _pick_str(
        payload,
        "mpesa_receipt_number",
        "mpesa_receipt",
        "MpesaReceiptNumber",
        "receipt_number",
        "receipt",
    )
    desc = _pick_str(payload, "result_desc", "message", "detail", "customer_message")
    success_flag = payload.get("success")
    if success_flag is True or status in ("success", "completed", "paid", "ok"):
        return "0", desc or "Payment confirmed.", receipt
    if status in ("cancelled", "canceled"):
        return "1032", desc or "Payment cancelled.", receipt
    if status in ("expired", "timeout"):
        return "1037", desc or "Payment expired.", receipt
    if success_flag is False or status in ("failed", "error", "rejected"):
        return "1", desc or "Payment failed.", receipt
    return "", desc, receipt


def initiate_nexus_stk_push(
    *,
    purpose: str,
    amount,
    phone: str,
    account_reference: str = "",
    description: str = "",
    shop=None,
    profile=None,
    account_kind: str = "",
    account_id: int | None = None,
    receipt=None,
) -> MpesaStkPayment:
    row = get_daraja_settings()
    if not row.is_ready_for_stk():
        reason = row.stk_not_ready_reason() or "STK Push is not ready."
        raise ValidationError(reason)

    pay_amount = _money(amount)
    if pay_amount < Decimal("1.00"):
        raise ValidationError("M-Pesa amount must be at least KSh 1.00.")

    party = _party_phone(phone)
    api_key = (row.nexus_api_key or "").strip()
    reference = (account_reference or "MYSHOP").strip()[:40] or "MYSHOP"
    desc = (description or "Payment").strip()[:80] or "Payment"

    payment = MpesaStkPayment.objects.create(
        purpose=purpose,
        shop=shop,
        amount=pay_amount,
        phone=party,
        account_reference=reference,
        description=desc,
        account_kind=account_kind or "",
        account_id=account_id,
        receipt=receipt,
        created_by=profile,
        status=MpesaStkStatus.PENDING,
        stk_provider=StkProvider.NEXUS,
    )

    body = {
        "phone": party,
        "amount": f"{pay_amount:.2f}",
    }
    try:
        payload = _nexus_request(
            nexus_stk_url(),
            api_key=api_key,
            method="POST",
            body=body,
        )
    except ValidationError as exc:
        payment.status = MpesaStkStatus.FAILED
        payment.result_desc = (
            "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        )
        payment.completed_at = timezone.now()
        payment.save(
            update_fields=["status", "result_desc", "completed_at", "updated_at"]
        )
        raise

    checkout_id = _pick_str(
        payload,
        "checkout_request_id",
        "CheckoutRequestID",
        "stk_request_id",
        "request_id",
        "id",
        "transaction_id",
    )
    merchant_id = _pick_str(
        payload,
        "merchant_request_id",
        "MerchantRequestID",
        "reference",
    )
    result_code, result_desc, receipt_no = _nexus_status_from_payload(payload)

    payment.merchant_request_id = merchant_id
    payment.checkout_request_id = checkout_id or payment.public_id
    if result_code == "0":
        payment.status = MpesaStkStatus.SUCCESS
        payment.mpesa_receipt_number = receipt_no
        payment.result_code = "0"
        payment.result_desc = result_desc or "Payment confirmed."
        payment.completed_at = timezone.now()
        if payment.purpose == MpesaStkPurpose.DEVELOPER and not payment.applied:
            from shops.services import mark_developer_subscription_paid

            mark_developer_subscription_paid(
                mpesa_receipt=payment.mpesa_receipt_number or receipt_no,
                paid_at=payment.completed_at,
            )
            payment.applied = True
    elif result_code:
        payment.result_code = result_code
        payment.result_desc = result_desc
        if result_code in ("1032",):
            payment.status = MpesaStkStatus.CANCELLED
        elif result_code in ("1037",):
            payment.status = MpesaStkStatus.EXPIRED
        else:
            payment.status = MpesaStkStatus.FAILED
        payment.completed_at = timezone.now()
    else:
        payment.result_desc = (
            result_desc
            or _pick_str(payload, "ResponseDescription", "customer_message")
            or "STK Push sent. Waiting for customer confirmation."
        )
    payment.save(
        update_fields=[
            "merchant_request_id",
            "checkout_request_id",
            "status",
            "result_code",
            "result_desc",
            "mpesa_receipt_number",
            "completed_at",
            "applied",
            "updated_at",
        ]
    )

    if payment.status == MpesaStkStatus.FAILED:
        raise ValidationError(payment.result_desc or "Nexus STK Push was not accepted.")

    return payment


def query_nexus_stk_status(payment: MpesaStkPayment) -> MpesaStkPayment:
    """Best-effort status poll for Nexus STK (API shape may vary by deployment)."""
    from shops.daraja_stk import _apply_stk_outcome

    row = get_daraja_settings()
    api_key = (row.nexus_api_key or "").strip()
    if not api_key:
        raise ValidationError("Nexus collection API key is missing.")

    ref = (payment.checkout_request_id or payment.public_id or "").strip()
    base = nexus_stk_url().rstrip("/")
    candidates = [
        f"{base}/{ref}/",
        f"{base}?checkout_request_id={ref}",
        f"{base}?id={ref}",
        f"{base}?reference={ref}",
    ]

    payment.last_status_query_at = timezone.now()
    payment.save(update_fields=["last_status_query_at", "updated_at"])

    payload = None
    for url in candidates:
        try:
            payload = _nexus_request(url, api_key=api_key, method="GET")
        except ValidationError:
            continue
        if payload:
            break

    if not payload:
        return payment

    result_code, result_desc, receipt_no = _nexus_status_from_payload(payload)
    if not result_code:
        if result_desc:
            payment.result_desc = result_desc
            payment.save(update_fields=["result_desc", "updated_at"])
        return payment

    return _apply_stk_outcome(
        payment,
        result_code=result_code,
        result_desc=result_desc,
        receipt_number=receipt_no,
    )


def payment_uses_nexus(payment: MpesaStkPayment, row: CompanyDarajaSettings | None = None) -> bool:
    provider = (payment.stk_provider or "").strip()
    if provider in (StkProvider.NEXUS, "nexus_rushtech"):
        return True
    if provider == StkProvider.DARAJA:
        return False
    settings_row = row or get_daraja_settings()
    return settings_row.uses_nexus_stk()
