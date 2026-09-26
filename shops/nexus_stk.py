"""Nexus collections STK helpers (RUSHTech / Richcom collections API).

Contract (Production):
  POST {NEXUS_STK_URL}
  Headers: X-API-Key, Content-Type: application/json
  Body: {"phone": "2547...", "amount": "1500.00"}  (amount as string)
  Collection id (C2B BillRefNumber) is tied to the key; STK uses it automatically.
"""

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
    url = (
        getattr(settings, "NEXUS_STK_URL", None)
        or "https://fin.richcom.co.ke/api/v1/collections/stk/"
    ).strip()
    if not url.endswith("/"):
        url = f"{url}/"
    return url


class _NexusPostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep POST body/method on 301/302 (default urllib may turn POST into GET)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        method = getattr(req, "method", None) or "GET"
        if method in {"POST", "PUT", "PATCH"}:
            return urllib.request.Request(
                newurl,
                data=req.data,
                headers=req.headers,
                method=method,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_NEXUS_URL_OPENER = urllib.request.build_opener(_NexusPostRedirectHandler)


def _nexus_auth_headers(api_key: str, *, auth_mode: str | None = None) -> dict:
    mode = (auth_mode or getattr(settings, "NEXUS_STK_AUTH", "x-api-key") or "x-api-key")
    mode = mode.strip().lower()
    headers = {"Accept": "application/json"}
    if mode in {"bearer", "authorization", "token"}:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["X-API-Key"] = api_key
    return headers


def _nexus_request(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 20,
    auth_mode: str | None = None,
) -> dict:
    headers = _nexus_auth_headers(api_key, auth_mode=auth_mode)
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
        with _NEXUS_URL_OPENER.open(request_obj, timeout=timeout) as response:
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
        if not detail and exc.code == 405:
            detail = f'Method "{method}" not allowed.'
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


def _collection_id_from_payload(payload: dict) -> str:
    collection_id = (
        payload.get("collection_id")
        or payload.get("collectionId")
        or payload.get("bill_ref_number")
        or payload.get("BillRefNumber")
        or ""
    )
    if isinstance(collection_id, str):
        return collection_id.strip()
    return str(collection_id or "").strip()


def _nexus_key_auth_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        token in lowered
        for token in (
            "unauthorized",
            "authentication",
            "authentication credentials were not provided",
            "invalid api",
            "invalid key",
            "invalid collection api key",
            "forbidden",
            "permission denied",
            "credentials",
            "api key",
        )
    )


def _nexus_key_accepted_validation_error(text: str) -> bool:
    """True when the API authenticated the key but rejected probe field values."""
    lowered = (text or "").lower()
    if _nexus_key_auth_error(lowered):
        return False
    return any(
        token in lowered
        for token in (
            "phone",
            "amount",
            "required",
            "invalid",
            "must be",
            "field",
            "minimum",
            "greater than",
        )
    )


def _method_get_not_allowed(text: str) -> bool:
    lowered = (text or "").lower()
    return "method" in lowered and "get" in lowered and "not allowed" in lowered


def _nexus_stk_probe_body() -> dict:
    """Same JSON shape as RUSHTech docs; probe phone/amount only (no live STK to a real customer)."""
    return {"phone": "254700000001", "amount": "1.00"}


def verify_nexus_collection_api_key(api_key: str) -> dict:
    key = (api_key or "").strip()
    if not key:
        raise ValidationError("Nexus collection API key is required.")
    if len(key) < 8:
        raise ValidationError("Enter the full API key from your Nexus collection account.")
    if not key.startswith("cm_"):
        raise ValidationError(
            "Nexus collection keys usually start with cm_. Paste the full key from your Nexus account."
        )

    url = nexus_stk_url()
    try:
        payload = _nexus_request(
            url,
            api_key=key,
            method="POST",
            body=_nexus_stk_probe_body(),
            auth_mode="x-api-key",
        )
        return {
            "ok": True,
            "collection_id": _collection_id_from_payload(payload),
            "payload": payload,
        }
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        if _nexus_key_auth_error(message):
            raise ValidationError(
                f"Nexus rejected this API key. {message}"
            ) from exc
        if _nexus_key_accepted_validation_error(message):
            return {"ok": True, "collection_id": "", "payload": {}}
        if _method_get_not_allowed(message):
            raise ValidationError(
                "Nexus STK endpoint rejected the verify request (POST was treated as GET). "
                "Deploy the latest app code, set NEXUS_STK_URL to "
                "https://fin.richcom.co.ke/api/v1/collections/stk/ in .env, restart Passenger, "
                "then Save & verify again."
            ) from exc
        raise


def _party_phone(phone: str) -> str:
    from shops.daraja_stk import _party_phone as daraja_party_phone

    return daraja_party_phone(phone)


def _pick_str(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _unwrap_nexus_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data")
    if isinstance(inner, dict):
        return {**payload, **inner}
    return payload


def _nexus_callback_url(*, request=None) -> str:
    from shops.daraja_stk import resolve_stk_callback_url

    return resolve_stk_callback_url(request=request)


def _nexus_status_from_payload(payload: dict) -> tuple[str, str, str]:
    payload = _unwrap_nexus_payload(payload)
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
    desc = _pick_str(
        payload,
        "result_desc",
        "ResultDesc",
        "message",
        "detail",
        "customer_message",
        "ResponseDescription",
    )
    result_code_raw = payload.get("ResultCode")
    if result_code_raw is not None and str(result_code_raw).strip() != "":
        code = str(result_code_raw).strip()
        if code in ("0", "00"):
            return "0", desc or "Payment confirmed.", receipt or _pick_str(
                payload, "mpesa_receipt_number", "MpesaReceiptNumber"
            )
        if code in ("1032",):
            return "1032", desc or "Payment cancelled.", receipt
        if code in ("1037",):
            return "1037", desc or "Payment expired.", receipt
        return code, desc or "Payment failed.", receipt

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
    request=None,
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
        "reference": str(payment.public_id),
        "client_reference": str(payment.public_id),
    }
    callback = _nexus_callback_url(request=request)
    if callback:
        body["callback_url"] = callback
        body["CallBackURL"] = callback

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

    payload = _unwrap_nexus_payload(payload)
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
    )
    response_code = str(payload.get("ResponseCode") or "").strip()
    response_desc = _pick_str(payload, "ResponseDescription", "CustomerMessage")
    result_code, result_desc, receipt_no = _nexus_status_from_payload(payload)
    if not result_code and response_code and response_code not in ("0", "00"):
        result_code = response_code
        result_desc = result_desc or response_desc or "STK Push was not accepted."
    elif not result_code and response_code in ("0", "00") and not checkout_id:
        result_desc = result_desc or response_desc

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
    """Poll Nexus STK status (POST probes + optional GET by id)."""
    from shops.daraja_stk import _apply_stk_outcome

    row = get_daraja_settings()
    api_key = (row.nexus_api_key or "").strip()
    if not api_key:
        raise ValidationError("Nexus collection API key is missing.")

    checkout_ref = (payment.checkout_request_id or "").strip()
    public_ref = (payment.public_id or "").strip()
    if checkout_ref == public_ref:
        checkout_ref = ""

    payment.last_status_query_at = timezone.now()
    payment.save(update_fields=["last_status_query_at", "updated_at"])

    base = nexus_stk_url().rstrip("/")
    post_bodies = []
    if checkout_ref:
        post_bodies.extend(
            [
                {"checkout_request_id": checkout_ref},
                {"CheckoutRequestID": checkout_ref},
                {"request_id": checkout_ref},
            ]
        )
    if public_ref:
        post_bodies.append({"reference": public_ref, "client_reference": public_ref})

    payload = None
    for body in post_bodies:
        try:
            payload = _nexus_request(
                nexus_stk_url(),
                api_key=api_key,
                method="POST",
                body=body,
            )
        except ValidationError:
            continue
        if payload:
            break

    if payload is None and checkout_ref and checkout_ref != public_ref:
        try:
            payload = _nexus_request(
                f"{base}/{checkout_ref}/",
                api_key=api_key,
                method="GET",
            )
        except ValidationError:
            payload = None

    if not payload:
        age = (timezone.now() - payment.created_at).total_seconds()
        if age >= 75:
            payment.result_desc = (
                payment.result_desc
                or "STK Push timed out. Customer did not confirm in time."
            )
            payment.save(update_fields=["result_desc", "updated_at"])
        return payment

    payload = _unwrap_nexus_payload(payload)
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
