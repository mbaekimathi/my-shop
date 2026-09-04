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
    get_effective_pos_settings,
    get_daraja_settings,
    verify_daraja_oauth,
)

STK_PUSH_URLS = {
    DarajaEnvironment.SANDBOX: (
        "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    ),
    DarajaEnvironment.PRODUCTION: (
        "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    ),
}
STK_QUERY_URLS = {
    DarajaEnvironment.SANDBOX: (
        "https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query"
    ),
    DarajaEnvironment.PRODUCTION: (
        "https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query"
    ),
}
DARAJA_TOKEN_CACHE_PREFIX = "daraja_oauth_token:v1"
STK_QUERY_MIN_INTERVAL_SECONDS = 12


def _stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
    import base64

    raw = f"{shortcode}{passkey}{timestamp}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _stk_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _resolve_stk_config(row: CompanyDarajaSettings, pos) -> dict:
    """Map POS M-Pesa collection settings to Daraja STK fields."""
    collection = (pos.mpesa_collection_type or "").strip().lower()
    shortcode = (row.shortcode or "").strip()
    till = (pos.mpesa_till_number or "").strip()
    paybill_business = (pos.mpesa_business_number or "").strip()

    # When receipt M-Pesa type is unset, infer from configured numbers.
    if not collection:
        if paybill_business:
            collection = "paybill"
        else:
            collection = "buy_goods"

    if collection == "buy_goods":
        business_shortcode = shortcode
        party_b = till if till else shortcode
        transaction_type = "CustomerBuyGoodsOnline"
    else:
        paybill = paybill_business or shortcode
        business_shortcode = paybill
        party_b = paybill
        transaction_type = "CustomerPayBillOnline"
    if not business_shortcode:
        raise ValidationError("Daraja shortcode is required for STK Push.")
    if not party_b:
        raise ValidationError(
            "Configure M-Pesa paybill or till number in receipt settings before STK Push."
        )
    return {
        "transaction_type": transaction_type,
        "business_shortcode": business_shortcode,
        "party_b": party_b,
    }


def _daraja_token_cache_key(row: CompanyDarajaSettings) -> str:
    env = row.environment or DarajaEnvironment.SANDBOX
    key_hint = (row.consumer_key or "")[:24]
    return f"{DARAJA_TOKEN_CACHE_PREFIX}:{env}:{key_hint}"


def get_daraja_access_token(row: CompanyDarajaSettings | None = None) -> str:
    """OAuth access token with short-lived cache to avoid hammering Safaricom."""
    from django.core.cache import cache

    settings_row = row or get_daraja_settings()
    cache_key = _daraja_token_cache_key(settings_row)
    cached = cache.get(cache_key)
    if cached:
        return cached

    token_data = verify_daraja_oauth(
        consumer_key=settings_row.consumer_key,
        consumer_secret=settings_row.consumer_secret,
        environment=settings_row.environment,
    )
    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in")
    try:
        ttl = int(expires_in) - 60
    except (TypeError, ValueError):
        ttl = 3500
    cache.set(cache_key, access_token, max(ttl, 60))
    return access_token


def invalidate_daraja_access_token_cache() -> None:
    from django.core.cache import cache

    row = get_daraja_settings()
    cache.delete(_daraja_token_cache_key(row))


def _daraja_json_request(
    url: str,
    *,
    access_token: str,
    body: dict,
    timeout: float = 20,
) -> dict:
    request_obj = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request_obj, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _daraja_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return _safaricom_error_message(raw)


def _safaricom_error_message(payload) -> str:
    """Extract a human-readable message from Safaricom JSON or plain text."""
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ""
        if text.startswith("{") or text.startswith("["):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return text[:220]
        else:
            return text[:220]
    if not isinstance(payload, dict):
        return ""
    for key in (
        "errorMessage",
        "error_message",
        "ResponseDescription",
        "CustomerMessage",
        "ResultDesc",
        "error",
    ):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()[:220]
    request_id = (payload.get("requestId") or "").strip()
    error_code = (payload.get("errorCode") or "").strip()
    if error_code:
        return f"{error_code} ({request_id})" if request_id else error_code
    return ""


def _build_stk_security(business_shortcode: str, passkey: str) -> tuple[str, str]:
    timestamp = _stk_timestamp()
    password = _stk_password(business_shortcode, passkey, timestamp)
    return timestamp, password


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
    if getattr(settings, "IS_HOSTED", False):
        return ""
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


def ensure_callback_secret(row: CompanyDarajaSettings | None = None) -> str:
    """Return the Daraja callback URL secret, generating one when missing."""
    import secrets

    from shops.services import _invalidate_daraja_settings_cache

    settings_row = row or get_daraja_settings()
    secret = (settings_row.callback_secret or "").strip()
    if secret:
        return secret
    secret = secrets.token_urlsafe(32)
    settings_row.callback_secret = secret
    settings_row.save(update_fields=["callback_secret", "updated_at"])
    _invalidate_daraja_settings_cache()
    return secret


def callback_secret_matches(value: str) -> bool:
    expected = ensure_callback_secret()
    provided = (value or "").strip()
    if not expected or not provided:
        return False
    import hmac

    return hmac.compare_digest(expected, provided)


def detect_settings_callback_base() -> str:
    """Public HTTPS base learned by AutoHostMiddleware or set in .env."""
    base = (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip().rstrip("/")
    if is_safaricom_callback_base(base):
        return base
    return ""


def persist_public_callback_base(base: str) -> str:
    """Save a public HTTPS callback base when it changes (no-op if same)."""
    from shops.services import _invalidate_daraja_settings_cache

    raw = (base or "").strip().rstrip("/")
    if not raw or not is_safaricom_callback_base(raw):
        return ""
    try:
        normalized = normalize_callback_base_url(raw, allow_local=False)
    except ValidationError:
        return ""
    row = get_daraja_settings()
    current = (row.callback_base_url or "").strip().rstrip("/")
    if current != normalized:
        row.callback_base_url = normalized
        row.save(update_fields=["callback_base_url", "updated_at"])
        _invalidate_daraja_settings_cache()
    return normalized


def _callback_base_candidates(*, request=None) -> list[str]:
    """Ordered callback bases: live domain first, then learned env, DB, ngrok."""
    env_base = (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip()
    row = get_daraja_settings()
    saved = (row.callback_base_url or "").strip()

    candidates: list[str] = []
    if request is not None:
        detected = detect_request_base_url(request)
        if detected:
            candidates.append(detected)
    settings_base = detect_settings_callback_base()
    if settings_base:
        candidates.append(settings_base)
    if saved:
        candidates.append(saved)
    ngrok_base = detect_ngrok_public_base_url()
    if ngrok_base:
        candidates.append(ngrok_base)
    if env_base and env_base not in candidates:
        candidates.append(env_base)
    return candidates


def sync_callback_base_from_request(request, *, persist: bool = True) -> str:
    """
    Auto-pick callback base from the domain in the address bar (or proxy headers).
    Public HTTPS wins; localhost/ngrok fallbacks only when needed.
    """
    public_chosen = ""
    local_chosen = ""
    for candidate in _callback_base_candidates(request=request):
        try:
            if is_safaricom_callback_base(candidate):
                public_chosen = normalize_callback_base_url(candidate, allow_local=False)
                break
            normalized = normalize_callback_base_url(candidate, allow_local=True)
            if normalized and not local_chosen:
                local_chosen = normalized
        except ValidationError:
            continue

    chosen = public_chosen or local_chosen
    if not chosen:
        return (get_daraja_settings().callback_base_url or "").strip()

    if public_chosen and persist:
        persist_public_callback_base(public_chosen)
    elif persist and chosen and is_safaricom_callback_base(chosen):
        persist_public_callback_base(chosen)

    return chosen


def resolve_callback_base_url(*, request=None, persist: bool = False) -> str:
    """Best callback base: current domain → middleware/.env → saved → ngrok."""
    if request is not None:
        synced = sync_callback_base_from_request(request, persist=persist)
        if synced and is_safaricom_callback_base(synced):
            return normalize_callback_base_url(synced, allow_local=False)
        if synced:
            return synced

    for candidate in _callback_base_candidates(request=None):
        if is_safaricom_callback_base(candidate):
            try:
                return normalize_callback_base_url(candidate, allow_local=False)
            except ValidationError:
                continue

    ngrok_base = detect_ngrok_public_base_url()
    if ngrok_base:
        return ngrok_base

    saved = (get_daraja_settings().callback_base_url or "").strip().rstrip("/")
    if saved:
        return saved
    return (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip().rstrip("/")


def _callback_url(*, request=None) -> str:
    """Build Safaricom callback URL from the live domain (never stale localhost)."""
    base = resolve_callback_base_url(request=request, persist=bool(request))
    if not base:
        raise ValidationError(
            "No site URL detected for M-Pesa callbacks. Open the app via your "
            "public HTTPS domain, then try again."
        )
    if not is_safaricom_callback_base(base):
        raise ValidationError(
            "Safaricom cannot reach this site URL "
            f"({base}). Use your hosted HTTPS domain so the callback updates "
            "automatically from the address bar."
        )
    public = normalize_callback_base_url(base, allow_local=False)
    secret = ensure_callback_secret()
    return f"{public}/mpesa/daraja/callback/{secret}/"


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
        reason = row.stk_not_ready_reason()
        if row.enable_stk_push and row.credentials_valid and not row.has_usable_callback_base():
            raise ValidationError(
                "Open MY-SHOP via your public HTTPS domain so the M-Pesa callback "
                "URL updates, then try again."
            )
        if reason == "STK disabled":
            raise ValidationError(
                "STK Push is disabled. Open Company Daraja settings and turn on "
                "Enable STK Push."
            )
        if reason:
            raise ValidationError(f"STK Push is not ready: {reason}.")
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

    access_token = get_daraja_access_token(row)
    pos = get_effective_pos_settings(shop)
    stk_config = _resolve_stk_config(row, pos)
    business_shortcode = stk_config["business_shortcode"]
    timestamp, password = _build_stk_security(business_shortcode, passkey)

    reference = (account_reference or "MYSHOP").strip()[:12] or "MYSHOP"
    if stk_config["transaction_type"] == "CustomerPayBillOnline":
        paybill_account = (pos.mpesa_account_number or "").strip()
        if paybill_account and reference == "MYSHOP":
            reference = paybill_account[:12]
    desc = (description or "Payment").strip()[:40] or "Payment"

    body = {
        "BusinessShortCode": business_shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": stk_config["transaction_type"],
        "Amount": amount_int,
        "PartyA": party,
        "PartyB": stk_config["party_b"],
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
        stk_business_shortcode=business_shortcode,
    )

    request_obj = urllib.request.Request(
        STK_PUSH_URLS[env],
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
        detail = _daraja_http_error_detail(exc)
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
        raise ValidationError(
            payment.result_desc
            or _safaricom_error_message(payload)
            or "Safaricom rejected the STK Push request."
        )

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
def _apply_stk_outcome(
    payment: MpesaStkPayment,
    *,
    result_code: str,
    result_desc: str = "",
    receipt_number: str = "",
    merchant_id: str = "",
    checkout_id: str = "",
) -> MpesaStkPayment:
    """Update one STK payment from callback or STK Push Query."""
    if payment.status == MpesaStkStatus.SUCCESS and payment.mpesa_receipt_number:
        return payment

    payment.result_code = result_code
    if result_desc:
        payment.result_desc = result_desc
    if merchant_id and not payment.merchant_request_id:
        payment.merchant_request_id = merchant_id
    if checkout_id and not payment.checkout_request_id:
        payment.checkout_request_id = checkout_id

    if result_code in ("0", "00"):
        payment.status = MpesaStkStatus.SUCCESS
        if receipt_number:
            payment.mpesa_receipt_number = receipt_number
        payment.completed_at = timezone.now()
        if payment.purpose == MpesaStkPurpose.DEVELOPER and not payment.applied:
            from shops.services import mark_developer_subscription_paid

            mark_developer_subscription_paid(
                mpesa_receipt=payment.mpesa_receipt_number or receipt_number,
                paid_at=payment.completed_at,
            )
            payment.applied = True
    elif result_code in ("1032",):
        payment.status = MpesaStkStatus.CANCELLED
        payment.completed_at = timezone.now()
    elif result_code in ("1037",):
        payment.status = MpesaStkStatus.EXPIRED
        payment.completed_at = timezone.now()
    elif result_code:
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
            "applied",
            "updated_at",
        ]
    )
    return payment


def _stk_query_allowed(payment: MpesaStkPayment) -> bool:
    if payment.last_status_query_at is None:
        return True
    elapsed = (timezone.now() - payment.last_status_query_at).total_seconds()
    return elapsed >= STK_QUERY_MIN_INTERVAL_SECONDS


def query_stk_push_status(payment: MpesaStkPayment) -> MpesaStkPayment:
    """Ask Safaricom for the latest STK result when the callback may be delayed."""
    checkout_id = (payment.checkout_request_id or "").strip()
    if not checkout_id:
        raise ValidationError("STK payment has no checkout request id.")

    row = get_daraja_settings()
    if not row.has_credentials():
        raise ValidationError("Daraja credentials are incomplete.")

    business_shortcode = (payment.stk_business_shortcode or "").strip()
    if not business_shortcode:
        pos = get_company_pos_settings()
        stk_config = _resolve_stk_config(row, pos)
        business_shortcode = stk_config["business_shortcode"]

    passkey = (row.passkey or "").strip()
    env = row.environment or DarajaEnvironment.SANDBOX
    access_token = get_daraja_access_token(row)
    timestamp, password = _build_stk_security(business_shortcode, passkey)

    body = {
        "BusinessShortCode": business_shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_id,
    }

    payment.last_status_query_at = timezone.now()
    payment.save(update_fields=["last_status_query_at", "updated_at"])

    try:
        payload = _daraja_json_request(
            STK_QUERY_URLS[env],
            access_token=access_token,
            body=body,
            timeout=15,
        )
    except urllib.error.HTTPError as exc:
        detail = _daraja_http_error_detail(exc)
        raise ValidationError(
            detail or "Safaricom rejected the STK status query."
        ) from exc
    except urllib.error.URLError as exc:
        raise ValidationError(
            "Could not reach Safaricom Daraja to check STK status."
        ) from exc

    response_code = str(payload.get("ResponseCode") or "").strip()
    if response_code not in ("0", "00"):
        response_desc = (
            payload.get("ResponseDescription")
            or payload.get("errorMessage")
            or "STK status query was not accepted."
        )
        raise ValidationError(str(response_desc).strip())

    result_code = str(payload.get("ResultCode") if payload.get("ResultCode") is not None else "").strip()
    result_desc = (payload.get("ResultDesc") or "").strip()
    merchant_id = (payload.get("MerchantRequestID") or payment.merchant_request_id or "").strip()
    checkout_from_api = (payload.get("CheckoutRequestID") or checkout_id).strip()

    # ResultCode empty means still processing on Safaricom's side.
    if not result_code:
        if result_desc:
            payment.result_desc = result_desc
            payment.save(update_fields=["result_desc", "updated_at"])
        return payment

    with transaction.atomic():
        locked = (
            MpesaStkPayment.objects.select_for_update()
            .filter(pk=payment.pk)
            .first()
        )
        if locked is None:
            return payment
        return _apply_stk_outcome(
            locked,
            result_code=result_code,
            result_desc=result_desc,
            merchant_id=merchant_id,
            checkout_id=checkout_from_api,
        )


def refresh_stk_payment_if_pending(
    payment: MpesaStkPayment | None,
    *,
    min_age_seconds: float = 3,
    force_safaricom: bool = False,
) -> MpesaStkPayment | None:
    """Poll Safaricom when a payment is still pending (callback fallback)."""
    if payment is None:
        return None
    if payment.status != MpesaStkStatus.PENDING:
        return payment
    if not (payment.checkout_request_id or "").strip():
        return payment
    age = (timezone.now() - payment.created_at).total_seconds()
    if age < min_age_seconds:
        return payment
    if not force_safaricom and not _stk_query_allowed(payment):
        return payment
    try:
        payment = query_stk_push_status(payment)
    except ValidationError:
        return payment
    except Exception:
        return payment
    payment.refresh_from_db()
    return payment


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

    receipt_number = ""
    metadata = callback.get("CallbackMetadata") or {}
    items = metadata.get("Item") or metadata.get("item") or []
    if isinstance(items, list):
        for item in items:
            name = (item.get("Name") or item.get("name") or "").strip()
            value = item.get("Value")
            if name == "MpesaReceiptNumber" and value is not None:
                receipt_number = str(value).strip()

    return _apply_stk_outcome(
        payment,
        result_code=result_code,
        result_desc=result_desc or payment.result_desc,
        receipt_number=receipt_number,
        merchant_id=merchant_id,
        checkout_id=checkout_id,
    )


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
    if payment.applied:
        raise ValidationError("This M-Pesa payment was already applied.")
    if Decimal(payment.amount).quantize(Decimal("0.01")) != Decimal(
        expected_amount
    ).quantize(Decimal("0.01")):
        raise ValidationError("M-Pesa paid amount does not match the required amount.")
    if expected_phone:
        expected = _normalize_phone(expected_phone)
        if expected and expected != payment.phone:
            raise ValidationError("M-Pesa phone does not match the client phone.")
    return payment
