"""Safaricom Daraja STK Push helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    CompanyDarajaSettings,
    DarajaEnvironment,
    MpesaStkPayment,
    MpesaStkPurpose,
    MpesaStkStatus,
)
from .services import (
    _normalize_phone,
    _money,
    format_kenya_phone,
    get_company_pos_settings,
    get_daraja_settings,
    verify_daraja_oauth,
)

STK_URLS = {
    DarajaEnvironment.SANDBOX: "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
    DarajaEnvironment.PRODUCTION: "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
}


def _stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
    import base64

    raw = f"{shortcode}{passkey}{timestamp}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _is_local_or_private_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if not h or h in {"localhost", "127.0.0.1", "::1"} or h.endswith(".local"):
        return True
    if h.startswith("192.168.") or h.startswith("10."):
        return True
    if h.startswith("172."):
        try:
            second = int(h.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def detect_request_base_url(request) -> str:
    """Origin the browser is using right now (hosted domain, ngrok, or localhost)."""
    if request is None:
        return ""
    host = (
        (request.META.get("HTTP_X_FORWARDED_HOST") or "").split(",")[0].strip()
        or (request.META.get("HTTP_HOST") or "").split(",")[0].strip()
    )
    if not host:
        try:
            host = (request.get_host() or "").strip()
        except Exception:
            host = ""
    if not host:
        return ""
    proto = (
        (request.META.get("HTTP_X_FORWARDED_PROTO") or "").split(",")[0].strip()
        or ("https" if request.is_secure() else "")
        or (request.scheme or "http")
    ).lower()
    if proto not in {"http", "https"}:
        proto = "http"
    return f"{proto}://{host}".rstrip("/")


def normalize_callback_base_url(value: str, *, allow_local: bool = True) -> str:
    """Normalize a base URL for storage. Local/http allowed when allow_local=True."""
    from urllib.parse import urlparse

    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    scheme = (parsed.scheme or "").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValidationError("Callback base URL must be http:// or https://")
    if not host:
        raise ValidationError("Enter a valid callback base URL.")
    if not allow_local and (scheme != "https" or _is_local_or_private_host(host)):
        raise ValidationError(
            "Safaricom needs a public HTTPS URL. Open this app via your hosted "
            "domain or an ngrok HTTPS link, then save again."
        )
    origin = f"{scheme}://{host}"
    if parsed.port and parsed.port not in (80, 443):
        origin = f"{scheme}://{host}:{parsed.port}"
    path_prefix = (parsed.path or "").rstrip("/")
    return f"{origin}{path_prefix}"


def is_safaricom_callback_base(value: str) -> bool:
    """True when Safaricom can POST to this base (public HTTPS)."""
    from urllib.parse import urlparse

    raw = (value or "").strip()
    if not raw:
        return False
    try:
        normalized = normalize_callback_base_url(raw, allow_local=False)
    except ValidationError:
        return False
    parsed = urlparse(normalized)
    return (parsed.scheme or "").lower() == "https" and not _is_local_or_private_host(
        parsed.hostname or ""
    )


def detect_ngrok_public_base_url() -> str:
    """
    Read the local ngrok agent API for an active HTTPS tunnel.
    Lets STK work even when the admin is still browsing localhost.
    """
    api = (
        getattr(settings, "DARAJA_NGROK_API_URL", "") or "http://127.0.0.1:4040/api/tunnels"
    ).strip()
    try:
        request = urllib.request.Request(api, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return ""

    tunnels = payload.get("tunnels") or []
    https_urls = []
    for tunnel in tunnels:
        public_url = (tunnel.get("public_url") or "").strip().rstrip("/")
        if public_url.lower().startswith("https://"):
            https_urls.append(public_url)
    if not https_urls:
        return ""
    try:
        return normalize_callback_base_url(https_urls[0], allow_local=False)
    except ValidationError:
        return ""


def sync_callback_base_from_request(request, *, persist: bool = True) -> str:
    """
    Auto-pick the best callback base URL:
    - public HTTPS from the browser (hosted / ngrok tab)
    - else active ngrok tunnel from local agent API
    - else env override
    - else current request (including localhost) so local still updates
    """
    from shops.services import _invalidate_daraja_settings_cache

    env_base = (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip()
    detected = detect_request_base_url(request) if request is not None else ""
    ngrok_base = ""

    candidates = []
    if detected and is_safaricom_callback_base(detected):
        candidates.append(detected)
    else:
        ngrok_base = detect_ngrok_public_base_url()
        if ngrok_base:
            candidates.append(ngrok_base)
        if env_base and is_safaricom_callback_base(env_base):
            candidates.append(env_base)
        if detected:
            candidates.append(detected)
        elif env_base:
            candidates.append(env_base)

    chosen = ""
    for candidate in candidates:
        try:
            chosen = normalize_callback_base_url(candidate, allow_local=True)
        except ValidationError:
            continue
        if chosen:
            break

    if not chosen:
        row = get_daraja_settings()
        return (row.callback_base_url or "").strip()

    if not persist:
        return chosen

    row = get_daraja_settings()
    current = (row.callback_base_url or "").strip().rstrip("/")
    # Prefer upgrading localhost → public ngrok when the tunnel appears.
    if current != chosen:
        row.callback_base_url = chosen
        row.save(update_fields=["callback_base_url", "updated_at"])
        _invalidate_daraja_settings_cache()
    return chosen


def resolve_callback_base_url(*, request=None) -> str:
    """Best available callback base: request/ngrok → saved → env."""
    if request is not None:
        synced = sync_callback_base_from_request(request, persist=True)
        if synced:
            return synced
    row = get_daraja_settings()
    base = (row.callback_base_url or "").strip().rstrip("/")
    if is_safaricom_callback_base(base):
        return base
    ngrok_base = detect_ngrok_public_base_url()
    if ngrok_base:
        return ngrok_base
    if base:
        return base
    return (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip().rstrip("/")


def _callback_url(*, request=None) -> str:
    """Build a Safaricom-accepted HTTPS callback URL (never localhost)."""
    base = resolve_callback_base_url(request=request)
    if not base:
        raise ValidationError(
            "No site URL detected for M-Pesa callbacks. Open the app via your "
            "public HTTPS domain or ngrok link, then try again."
        )
    if not is_safaricom_callback_base(base):
        raise ValidationError(
            "Safaricom cannot reach this site URL "
            f"({base}). Open MY-SHOP through your hosted HTTPS domain or an "
            "ngrok HTTPS URL so the callback address updates automatically."
        )
    public = normalize_callback_base_url(base, allow_local=False)
    return f"{public}/mpesa/daraja/callback/"


def validate_callback_base_url(value: str) -> str:
    """Normalize a callback base URL for storage (local allowed)."""
    return normalize_callback_base_url(value, allow_local=True)


def _party_phone(phone_raw: str) -> str:
    normalized = _normalize_phone(phone_raw)
    if not normalized or not normalized.startswith("254") or len(normalized) != 12:
        raise ValidationError(
            "Enter a valid Kenyan phone number for M-Pesa STK Push (e.g. 07XX XXX XXX)."
        )
    return normalized


def stk_ready() -> bool:
    return get_daraja_settings().is_ready_for_stk()


def stk_payment_payload(payment: MpesaStkPayment) -> dict:
    return {
        "id": str(payment.public_id),
        "status": payment.status,
        "status_label": payment.get_status_display(),
        "amount": f"{Decimal(payment.amount):.2f}",
        "phone": format_kenya_phone(payment.phone) or payment.phone,
        "mpesa_receipt_number": payment.mpesa_receipt_number or "",
        "result_desc": payment.result_desc or "",
        "checkout_request_id": payment.checkout_request_id or "",
        "applied": bool(payment.applied),
        "pending": payment.status == MpesaStkStatus.PENDING,
        "success": payment.status == MpesaStkStatus.SUCCESS,
        "failed": payment.status
        in (
            MpesaStkStatus.FAILED,
            MpesaStkStatus.CANCELLED,
            MpesaStkStatus.EXPIRED,
        ),
    }


def initiate_stk_push(
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
    if request is not None:
        sync_callback_base_from_request(request, persist=True)
    row = get_daraja_settings()
    if not row.is_ready_for_stk():
        if row.enable_stk_push and row.credentials_valid and not row.has_usable_callback_base():
            raise ValidationError(
                "Open MY-SHOP via your public HTTPS domain or ngrok link so the "
                "callback URL updates, then try M-Pesa again."
            )
        raise ValidationError(
            "STK Push is not enabled or Daraja credentials are not verified."
        )

    pay_amount = _money(amount)
    if pay_amount < Decimal("1.00"):
        raise ValidationError("M-Pesa amount must be at least KSh 1.00.")
    from decimal import ROUND_HALF_UP

    amount_int = int(pay_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount_int < 1:
        raise ValidationError("M-Pesa amount must be at least KSh 1.00.")
    pay_amount = Decimal(amount_int)

    party = _party_phone(phone)
    shortcode = (row.shortcode or "").strip()
    passkey = (row.passkey or "").strip()
    env = row.environment or DarajaEnvironment.SANDBOX
    callback = _callback_url(request=request)

    token_data = verify_daraja_oauth(
        consumer_key=row.consumer_key,
        consumer_secret=row.consumer_secret,
        environment=env,
    )
    access_token = token_data["access_token"]
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = _stk_password(shortcode, passkey, timestamp)

    pos = get_company_pos_settings()
    collection = (pos.mpesa_collection_type or "").strip().lower()
    if collection == "buy_goods":
        transaction_type = "CustomerBuyGoodsOnline"
    else:
        transaction_type = "CustomerPayBillOnline"

    reference = (account_reference or "MYSHOP").strip()[:12] or "MYSHOP"
    desc = (description or "Payment").strip()[:40] or "Payment"

    body = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": transaction_type,
        "Amount": amount_int,
        "PartyA": party,
        "PartyB": shortcode,
        "PhoneNumber": party,
        "CallBackURL": callback,
        "AccountReference": reference,
        "TransactionDesc": desc,
    }

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
    )

    request_obj = urllib.request.Request(
        STK_URLS[env],
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:220]
        except Exception:
            detail = ""
        payment.status = MpesaStkStatus.FAILED
        payment.result_desc = detail or "STK Push request failed."
        payment.completed_at = timezone.now()
        payment.save(
            update_fields=["status", "result_desc", "completed_at", "updated_at"]
        )
        raise ValidationError(
            detail or "Safaricom rejected the STK Push request. Check Daraja settings."
        ) from exc
    except urllib.error.URLError as exc:
        payment.status = MpesaStkStatus.FAILED
        payment.result_desc = "Could not reach Safaricom Daraja."
        payment.completed_at = timezone.now()
        payment.save(
            update_fields=["status", "result_desc", "completed_at", "updated_at"]
        )
        raise ValidationError(
            "Could not reach Safaricom Daraja. Check your internet connection."
        ) from exc

    merchant_id = (payload.get("MerchantRequestID") or "").strip()
    checkout_id = (payload.get("CheckoutRequestID") or "").strip()
    response_code = str(payload.get("ResponseCode") or "").strip()
    response_desc = (payload.get("ResponseDescription") or payload.get("CustomerMessage") or "").strip()

    if response_code not in ("0", "00") or not checkout_id:
        payment.status = MpesaStkStatus.FAILED
        payment.result_code = response_code
        payment.result_desc = response_desc or "STK Push was not accepted."
        payment.merchant_request_id = merchant_id
        payment.checkout_request_id = checkout_id
        payment.completed_at = timezone.now()
        payment.save(
            update_fields=[
                "status",
                "result_code",
                "result_desc",
                "merchant_request_id",
                "checkout_request_id",
                "completed_at",
                "updated_at",
            ]
        )
        raise ValidationError(payment.result_desc)

    payment.merchant_request_id = merchant_id
    payment.checkout_request_id = checkout_id
    payment.result_code = response_code
    payment.result_desc = response_desc or "STK Push sent. Waiting for customer confirmation."
    payment.save(
        update_fields=[
            "merchant_request_id",
            "checkout_request_id",
            "result_code",
            "result_desc",
            "updated_at",
        ]
    )
    return payment


def get_stk_payment(public_id) -> MpesaStkPayment | None:
    value = str(public_id or "").strip()
    if not value:
        return None
    return MpesaStkPayment.objects.filter(public_id=value).first()


@transaction.atomic
def handle_stk_callback(payload: dict) -> MpesaStkPayment | None:
    """Process Safaricom STK callback body and update the matching payment."""
    body = (payload or {}).get("Body") or payload or {}
    callback = body.get("stkCallback") or body.get("StkCallback") or {}
    checkout_id = (callback.get("CheckoutRequestID") or "").strip()
    merchant_id = (callback.get("MerchantRequestID") or "").strip()
    result_code = str(callback.get("ResultCode") if callback.get("ResultCode") is not None else "").strip()
    result_desc = (callback.get("ResultDesc") or "").strip()

    payment = None
    if checkout_id:
        payment = (
            MpesaStkPayment.objects.select_for_update()
            .filter(checkout_request_id=checkout_id)
            .first()
        )
    if payment is None and merchant_id:
        payment = (
            MpesaStkPayment.objects.select_for_update()
            .filter(merchant_request_id=merchant_id)
            .first()
        )
    if payment is None:
        return None

    if payment.status == MpesaStkStatus.SUCCESS and payment.mpesa_receipt_number:
        return payment

    receipt_number = ""
    metadata = callback.get("CallbackMetadata") or {}
    items = metadata.get("Item") or metadata.get("item") or []
    if isinstance(items, list):
        for item in items:
            name = (item.get("Name") or item.get("name") or "").strip()
            value = item.get("Value")
            if name == "MpesaReceiptNumber" and value is not None:
                receipt_number = str(value).strip()

    payment.result_code = result_code
    payment.result_desc = result_desc or payment.result_desc
    if merchant_id and not payment.merchant_request_id:
        payment.merchant_request_id = merchant_id
    if checkout_id and not payment.checkout_request_id:
        payment.checkout_request_id = checkout_id

    if result_code in ("0", "00"):
        payment.status = MpesaStkStatus.SUCCESS
        payment.mpesa_receipt_number = receipt_number
        payment.completed_at = timezone.now()
    elif result_code in ("1032",):
        payment.status = MpesaStkStatus.CANCELLED
        payment.completed_at = timezone.now()
    elif result_code in ("1037",):
        payment.status = MpesaStkStatus.EXPIRED
        payment.completed_at = timezone.now()
    else:
        payment.status = MpesaStkStatus.FAILED
        payment.completed_at = timezone.now()

    payment.save(
        update_fields=[
            "status",
            "result_code",
            "result_desc",
            "mpesa_receipt_number",
            "merchant_request_id",
            "checkout_request_id",
            "completed_at",
            "updated_at",
        ]
    )
    return payment


def require_successful_stk(
    *,
    public_id,
    expected_amount: Decimal,
    expected_phone: str = "",
    purpose: str = "",
) -> MpesaStkPayment:
    payment = get_stk_payment(public_id)
    if payment is None:
        raise ValidationError("M-Pesa STK payment not found.")
    if purpose and payment.purpose != purpose:
        raise ValidationError("M-Pesa STK payment purpose mismatch.")
    if payment.status != MpesaStkStatus.SUCCESS:
        raise ValidationError(
            payment.result_desc or "M-Pesa payment is not confirmed yet."
        )
    if Decimal(payment.amount).quantize(Decimal("0.01")) != Decimal(
        expected_amount
    ).quantize(Decimal("0.01")):
        raise ValidationError("M-Pesa paid amount does not match the required amount.")
    if expected_phone:
        expected = _normalize_phone(expected_phone)
        if expected and expected != payment.phone:
            raise ValidationError("M-Pesa phone does not match the client phone.")
    return payment
