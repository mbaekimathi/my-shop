import base64
import io
import re
from decimal import Decimal, InvalidOperation
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, transaction
from django.utils import timezone

from employees.countries import COUNTRY_DIAL_CODES

from .models import (
    CompanyCommunicationsSettings,
    CompanyDarajaSettings,
    CompanyPosSettings,
    CompanyProfile,
    CompanyStockSettings,
    CompanyWorkingHoursSettings,
    DarajaEnvironment,
    WORKING_DAY_FIELDS,
    ShopWorkingHoursSettings,
    Expense,
    ExpenseCategory,
    ExpensePaymentStatus,
    ExpenseSupplier,
    Shop,
    ShopPaymentMethod,
    ShopReceiptKind,
    ShopReceiptStatus,
    SmsProvider,
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
PHONE_RE = re.compile(r"^[\d+\-\s()]{7,40}$")
LOGIN_CODE_RE = re.compile(r"^\d{6}$")
MIN_PASSWORD_LENGTH = 6
DEFAULT_COMPANY_NAME = "MY-SHOP"
_SHOP_LOGIN_DUMMY_HASH = None


def _shop_login_dummy_hash() -> str:
    """Stable dummy hash so missing-shop checks cost about the same as a real miss."""
    global _SHOP_LOGIN_DUMMY_HASH
    if _SHOP_LOGIN_DUMMY_HASH is None:
        _SHOP_LOGIN_DUMMY_HASH = make_password("shop-portal-dummy-not-a-real-secret")
    return _SHOP_LOGIN_DUMMY_HASH

POS_SETTING_FIELDS = {
    "enable_sale",
    "enable_credit",
    "enable_quotation",
    "enable_cash",
    "enable_mpesa",
    "enable_cash_mpesa",
    "enable_discount",
    "enable_tax",
    "compulsory_print_on_sale",
    "enable_print_bluetooth",
    "enable_print_usb",
    "enable_print_wifi",
}

PRINT_CHANNELS = ("bluetooth", "usb", "wifi")
RECEIPT_PAPER_WIDTHS = ("80", "58")
RECEIPT_FONT_SIZES = ("small", "medium", "large", "xlarge")
RECEIPT_FONT_WEIGHTS = ("regular", "medium", "bold", "extrabold")
RECEIPT_FONT_SIZE_PX = {
    "small": {"80": "10px", "58": "8.5px"},
    "medium": {"80": "11.5px", "58": "9.5px"},
    "large": {"80": "13px", "58": "11px"},
    "xlarge": {"80": "14.5px", "58": "12px"},
}
RECEIPT_FONT_WEIGHT_CSS = {
    "regular": "400",
    "medium": "600",
    "bold": "700",
    "extrabold": "800",
}
RECEIPT_FORMAT_FIELDS = {
    "sale": "receipt_format_sale",
    "credit": "receipt_format_credit",
    "quotation": "receipt_format_quotation",
}
RECEIPT_FORMAT_DEFAULTS = {
    "sale": "S",
    "credit": "C",
    "quotation": "Q",
}
DOC_NUMBER_PREFIX = {
    "stock_in": "I",
    "delivery": "D",
    "expense": "E",
}
RECEIPT_FORMAT_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")
RECEIPT_QR_CONTENTS = ("website", "receipt_details")
WEBSITE_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

POS_SETTINGS_CACHE_KEY = "company_pos_settings:v1"
POS_SETTINGS_CACHE_TTL = 300
DARAJA_SETTINGS_CACHE_KEY = "company_daraja_settings:v1"
COMMUNICATIONS_SETTINGS_CACHE_KEY = "company_communications_settings:v1"
RECEIPT_QR_PREVIEW_CACHE_KEY = "receipt_qr_preview:v1"
RECEIPT_QR_PREVIEW_CACHE_TTL = 300

COMMUNICATIONS_TOGGLE_FIELDS = {
    "enable_whatsapp",
    "enable_message",
    "enable_sms",
    "enable_automations",
    "enable_bulk_send",
    "auto_sale_receipt",
    "auto_quotation",
    "auto_payment_reminder",
    "auto_credit_due",
}
DARAJA_OAUTH_URLS = {
    DarajaEnvironment.SANDBOX: (
        "https://sandbox.safaricom.co.ke/oauth/v1/generate"
        "?grant_type=client_credentials"
    ),
    DarajaEnvironment.PRODUCTION: (
        "https://api.safaricom.co.ke/oauth/v1/generate"
        "?grant_type=client_credentials"
    ),
}


def _invalidate_pos_settings_cache() -> None:
    cache.delete(POS_SETTINGS_CACHE_KEY)
    cache.delete(RECEIPT_QR_PREVIEW_CACHE_KEY)


def _invalidate_daraja_settings_cache() -> None:
    cache.delete(DARAJA_SETTINGS_CACHE_KEY)


def _invalidate_communications_settings_cache() -> None:
    cache.delete(COMMUNICATIONS_SETTINGS_CACHE_KEY)


def get_company_pos_settings() -> CompanyPosSettings:
    cached = cache.get(POS_SETTINGS_CACHE_KEY)
    if cached is not None:
        return cached
    settings_row, _ = CompanyPosSettings.objects.get_or_create(pk=1)
    cache.set(POS_SETTINGS_CACHE_KEY, settings_row, POS_SETTINGS_CACHE_TTL)
    return settings_row


STOCK_SETTINGS_CACHE_KEY = "company_stock_settings:v2"
STOCK_SETTING_FIELDS = frozenset(
    {
        "require_buying_price_on_in",
        "require_supplier_on_in",
        "require_payment_status_on_in",
        "require_reason_on_out",
        "require_refund_on_out",
        "require_note_on_request",
    }
)


def _invalidate_stock_settings_cache() -> None:
    cache.delete(STOCK_SETTINGS_CACHE_KEY)


def get_company_stock_settings() -> CompanyStockSettings:
    cached = cache.get(STOCK_SETTINGS_CACHE_KEY)
    if isinstance(cached, CompanyStockSettings):
        return cached
    settings_row, _ = CompanyStockSettings.objects.get_or_create(pk=1)
    cache.set(STOCK_SETTINGS_CACHE_KEY, settings_row, POS_SETTINGS_CACHE_TTL)
    return settings_row


def set_company_stock_setting(*, field: str, enabled: bool) -> CompanyStockSettings:
    if field not in STOCK_SETTING_FIELDS:
        raise ValidationError("Unknown stock setting.")
    settings_row = get_company_stock_settings()
    setattr(settings_row, field, bool(enabled))
    settings_row.save(update_fields=[field, "updated_at"])
    _invalidate_stock_settings_cache()
    return get_company_stock_settings()


def stock_settings_as_dict(settings_row: CompanyStockSettings | None = None) -> dict:
    row = settings_row or get_company_stock_settings()
    return {
        "require_buying_price_on_in": bool(row.require_buying_price_on_in),
        "require_supplier_on_in": bool(row.require_supplier_on_in),
        "require_payment_status_on_in": bool(row.require_payment_status_on_in),
        "require_reason_on_out": bool(row.require_reason_on_out),
        "require_refund_on_out": bool(row.require_refund_on_out),
        "require_note_on_request": bool(row.require_note_on_request),
        "requirements": row.as_requirements_dict(),
    }


WORKING_HOURS_DAY_FIELDS = frozenset(field for field, _label, _short in WORKING_DAY_FIELDS)
WEEKDAY_WORK_FIELDS = tuple(field for field, _label, _short in WORKING_DAY_FIELDS)


def _parse_working_time(raw: str):
    from datetime import datetime

    value = (raw or "").strip()
    if not value:
        raise ValidationError("Enter working start and end times.")
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValidationError("Use a valid time (HH:MM).")


def get_company_working_hours_settings() -> CompanyWorkingHoursSettings:
    settings_row, _ = CompanyWorkingHoursSettings.objects.get_or_create(pk=1)
    return settings_row


def working_hours_as_dict(
    settings_row: CompanyWorkingHoursSettings | None = None,
) -> dict:
    row = settings_row or get_company_working_hours_settings()
    days = {
        field: bool(getattr(row, field, False)) for field, _label, _short in WORKING_DAY_FIELDS
    }
    return {
        **days,
        "enabled": bool(row.enabled),
        "start_time": row.start_time.strftime("%H:%M"),
        "end_time": row.end_time.strftime("%H:%M"),
        "working_day_labels": row.working_day_labels(),
    }


def validate_working_hours_payload(data) -> dict:
    cleaned = {}
    for field, _label, _short in WORKING_DAY_FIELDS:
        cleaned[field] = (data.get(field) or "").strip().lower() in (
            "1",
            "on",
            "true",
            "yes",
        )

    if not any(cleaned.values()):
        raise ValidationError("Select at least one working day.")

    cleaned["enabled"] = (data.get("enabled") or "").strip().lower() in (
        "1",
        "on",
        "true",
        "yes",
    )
    return cleaned


def get_shop_working_hours_settings(shop: Shop) -> ShopWorkingHoursSettings:
    company = get_company_working_hours_settings()
    row, _ = ShopWorkingHoursSettings.objects.get_or_create(
        shop=shop,
        defaults={
            "start_time": company.start_time,
            "end_time": company.end_time,
        },
    )
    return row


def get_shop_working_hours_map(shops) -> dict[int, ShopWorkingHoursSettings]:
    company = get_company_working_hours_settings()
    shop_ids = [shop.pk for shop in shops]
    rows = ShopWorkingHoursSettings.objects.filter(shop_id__in=shop_ids)
    existing = {row.shop_id: row for row in rows}
    missing = [shop for shop in shops if shop.pk not in existing]
    if missing:
        ShopWorkingHoursSettings.objects.bulk_create(
            [
                ShopWorkingHoursSettings(
                    shop=shop,
                    start_time=company.start_time,
                    end_time=company.end_time,
                )
                for shop in missing
            ]
        )
        for row in ShopWorkingHoursSettings.objects.filter(shop_id__in=shop_ids):
            existing[row.shop_id] = row
    return existing


def _shop_hours_from_post(post, shop: Shop, fallback: ShopWorkingHoursSettings) -> tuple[str, str]:
    start_time = (post.get(f"shop_{shop.pk}_start_time") or "").strip()
    end_time = (post.get(f"shop_{shop.pk}_end_time") or "").strip()
    if not start_time:
        start_time = fallback.start_time.strftime("%H:%M")
    if not end_time:
        end_time = fallback.end_time.strftime("%H:%M")
    return start_time, end_time


def update_shop_working_hours_from_post(data, shops) -> None:
    company = get_company_working_hours_settings()
    hours_map = get_shop_working_hours_map(shops)
    for shop in shops:
        hours_row = hours_map[shop.pk]
        start_raw = (data.get(f"shop_{shop.pk}_start_time") or "").strip()
        end_raw = (data.get(f"shop_{shop.pk}_end_time") or "").strip()
        if not start_raw:
            start_raw = hours_row.start_time.strftime("%H:%M")
        if not end_raw:
            end_raw = hours_row.end_time.strftime("%H:%M")
        start_time = _parse_working_time(start_raw)
        end_time = _parse_working_time(end_raw)
        if start_time >= end_time:
            raise ValidationError(
                f"{shop.name}: closing time must be after opening time."
            )
        hours_row.start_time = start_time
        hours_row.end_time = end_time
        hours_row.save(update_fields=["start_time", "end_time", "updated_at"])


def update_company_working_hours(data) -> CompanyWorkingHoursSettings:
    settings_row = get_company_working_hours_settings()
    cleaned = validate_working_hours_payload(data)
    for field in WORKING_HOURS_DAY_FIELDS:
        setattr(settings_row, field, cleaned[field])
    settings_row.enabled = cleaned["enabled"]
    settings_row.save(
        update_fields=[
            *WORKING_HOURS_DAY_FIELDS,
            "enabled",
            "updated_at",
        ]
    )
    return settings_row


def save_working_hours_settings(data) -> CompanyWorkingHoursSettings:
    with transaction.atomic():
        settings_row = update_company_working_hours(data)
        update_shop_working_hours_from_post(data, list_active_shops())
    return settings_row


def build_shop_day_prompt(*, shop: Shop) -> dict:
    """Whether the shop floor should show an open/close balances popup."""
    settings_row = get_company_working_hours_settings()
    if not settings_row.enabled:
        return {"show": False}

    now = timezone.localtime()
    weekday_index = now.weekday()
    if weekday_index < 0 or weekday_index >= len(WEEKDAY_WORK_FIELDS):
        return {"show": False}

    if not getattr(settings_row, WEEKDAY_WORK_FIELDS[weekday_index], False):
        return {"show": False}

    open_session = get_open_shop_day(shop)
    is_open = open_session is not None
    now_time = now.time()
    shop_hours = get_shop_working_hours_settings(shop)
    start_time = shop_hours.start_time
    end_time = shop_hours.end_time
    mode = None

    if (
        not is_open
        and start_time <= now_time < end_time
    ):
        mode = "open"
    elif is_open and now_time >= end_time:
        mode = "close"

    if mode is None:
        return {"show": False}

    form_data = {
        "cash_amount": "",
        "mpesa_amount": "",
        "credit_amount": "",
        "stock_confirmed": False,
        "login_code": "",
    }

    return {
        "show": True,
        "mode": mode,
        "auto_open": True,
        "form_data": form_data,
        "start_time": start_time.strftime("%H:%M"),
        "end_time": end_time.strftime("%H:%M"),
    }


def list_shop_day_prompts(*, shops) -> list[dict]:
    """Shops that currently need an open or close balance popup."""
    settings_row = get_company_working_hours_settings()
    if not settings_row.enabled:
        return []

    rows = []
    for shop in shops:
        prompt = build_shop_day_prompt(shop=shop)
        if not prompt.get("show"):
            continue
        rows.append(
            {
                "shop": shop,
                "shop_id": shop.pk,
                "shop_name": shop.name,
                **prompt,
            }
        )
    return rows


def shop_working_hours_status_map(*, shops) -> dict[str, str]:
    """Map shop id → floor status for working-hours UI badges."""
    settings_row = get_company_working_hours_settings()
    if not settings_row.enabled:
        return {}

    statuses = {}
    for shop in shops:
        prompt = build_shop_day_prompt(shop=shop)
        shop_key = str(shop.pk)
        if prompt.get("show"):
            statuses[shop_key] = prompt["mode"]
            continue
        if get_open_shop_day(shop) is not None:
            statuses[shop_key] = "trading"
        else:
            statuses[shop_key] = "idle"
    return statuses


def active_shop_count() -> int:
    return Shop.objects.filter(is_hidden=False, is_suspended=False).count()


def list_active_shops():
    return list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    )


def list_working_hours_shop_rows(*, shops=None, post=None) -> list[dict]:
    """Shops covered by company working hours with live floor status."""
    shops = shops if shops is not None else list_active_shops()
    settings_row = get_company_working_hours_settings()
    hours_map = get_shop_working_hours_map(shops)
    status_map = (
        shop_working_hours_status_map(shops=shops) if settings_row.enabled else {}
    )

    rows = []
    for shop in shops:
        hours_row = hours_map[shop.pk]
        if post is not None:
            start_time, end_time = _shop_hours_from_post(post, shop, hours_row)
        else:
            start_time = hours_row.start_time.strftime("%H:%M")
            end_time = hours_row.end_time.strftime("%H:%M")

        status = status_map.get(str(shop.pk), "")
        if not settings_row.enabled:
            label = "Prompts off"
            tone = "muted"
        elif status == "open":
            label = "Needs opening"
            tone = "open"
        elif status == "close":
            label = "Needs closing"
            tone = "close"
        elif status == "trading":
            label = "Open"
            tone = "trading"
        elif status == "idle":
            label = "Closed"
            tone = "idle"
        else:
            label = "Off hours"
            tone = "muted"

        rows.append(
            {
                "shop": shop,
                "shop_id": shop.pk,
                "shop_name": shop.name,
                "shop_location": shop.location,
                "login_code": shop.login_code,
                "start_time": start_time,
                "end_time": end_time,
                "status": status,
                "status_label": label,
                "status_tone": tone,
                "is_open": get_open_shop_day(shop) is not None,
            }
        )
    return rows


def get_daraja_settings() -> CompanyDarajaSettings:
    cached = cache.get(DARAJA_SETTINGS_CACHE_KEY)
    if cached is not None:
        return cached
    settings_row, _ = CompanyDarajaSettings.objects.get_or_create(pk=1)
    cache.set(DARAJA_SETTINGS_CACHE_KEY, settings_row, POS_SETTINGS_CACHE_TTL)
    return settings_row


def get_communications_settings() -> CompanyCommunicationsSettings:
    cached = cache.get(COMMUNICATIONS_SETTINGS_CACHE_KEY)
    if cached is not None:
        return cached
    settings_row, _ = CompanyCommunicationsSettings.objects.get_or_create(pk=1)
    cache.set(COMMUNICATIONS_SETTINGS_CACHE_KEY, settings_row, POS_SETTINGS_CACHE_TTL)
    return settings_row


def communications_settings_as_dict(
    settings_row: CompanyCommunicationsSettings | None = None,
) -> dict:
    row = settings_row or get_communications_settings()
    return {
        "enable_whatsapp": bool(row.enable_whatsapp),
        "enable_message": bool(row.enable_message),
        "enable_sms": bool(row.enable_sms),
        "enable_automations": bool(row.enable_automations),
        "enable_bulk_send": bool(row.enable_bulk_send),
        "auto_sale_receipt": bool(row.auto_sale_receipt),
        "auto_quotation": bool(row.auto_quotation),
        "auto_payment_reminder": bool(row.auto_payment_reminder),
        "auto_credit_due": bool(row.auto_credit_due),
        "whatsapp_phone_number_id": row.whatsapp_phone_number_id or "",
        "whatsapp_business_account_id": row.whatsapp_business_account_id or "",
        "whatsapp_from_number": row.whatsapp_from_number or "",
        "whatsapp_access_token_set": bool((row.whatsapp_access_token or "").strip()),
        "has_whatsapp_credentials": row.has_whatsapp_credentials(),
        "sms_provider": row.sms_provider or SmsProvider.AFRICAS_TALKING,
        "sms_sender_id": row.sms_sender_id or "",
        "sms_api_base_url": row.sms_api_base_url or "",
        "sms_api_key_set": bool((row.sms_api_key or "").strip()),
        "sms_api_secret_set": bool((row.sms_api_secret or "").strip()),
        "has_sms_credentials": row.has_sms_credentials(),
        "message_from_name": row.message_from_name or "",
        "message_reply_to": row.message_reply_to or "",
        "updated_at": row.updated_at,
    }


def set_communications_setting(*, field: str, enabled: bool) -> CompanyCommunicationsSettings:
    if field not in COMMUNICATIONS_TOGGLE_FIELDS:
        raise ValidationError("Unknown communications setting.")
    row = get_communications_settings()
    if enabled:
        if field == "enable_whatsapp" and not row.has_whatsapp_credentials():
            raise ValidationError(
                "Save WhatsApp credentials below before enabling WhatsApp."
            )
        if field == "enable_sms" and not row.has_sms_credentials():
            raise ValidationError("Save SMS credentials below before enabling Text.")
        if field == "enable_message" and not (row.message_from_name or "").strip():
            raise ValidationError(
                "Set a sender name under Message settings before enabling Message."
            )
        if field == "enable_bulk_send" and not (
            row.enable_whatsapp or row.enable_sms or row.enable_message
        ):
            raise ValidationError(
                "Enable at least one channel (WhatsApp, Message, or Text) before bulk send."
            )
        if field == "enable_automations" and not (
            row.enable_whatsapp or row.enable_sms or row.enable_message
        ):
            raise ValidationError(
                "Enable at least one channel before turning on automations."
            )
    setattr(row, field, bool(enabled))
    update_fields = [field, "updated_at"]
    if field in {"enable_whatsapp", "enable_sms", "enable_message"} and not enabled:
        if not (row.enable_whatsapp or row.enable_sms or row.enable_message):
            row.enable_automations = False
            row.enable_bulk_send = False
            update_fields.extend(["enable_automations", "enable_bulk_send"])
    row.save(update_fields=update_fields)
    _invalidate_communications_settings_cache()
    return get_communications_settings()


def update_whatsapp_settings(
    *,
    phone_number_id: str = "",
    business_account_id: str = "",
    access_token: str = "",
    from_number: str = "",
) -> CompanyCommunicationsSettings:
    row = get_communications_settings()
    phone_number_id = (phone_number_id or "").strip() or row.whatsapp_phone_number_id
    business_account_id = (business_account_id or "").strip()
    access_token = (access_token or "").strip()
    from_number = (from_number or "").strip()

    row.whatsapp_phone_number_id = phone_number_id
    row.whatsapp_business_account_id = business_account_id
    if access_token:
        row.whatsapp_access_token = access_token
    row.whatsapp_from_number = from_number

    if not row.has_whatsapp_credentials():
        raise ValidationError(
            "Phone number ID and access token are required for WhatsApp."
        )

    row.save(
        update_fields=[
            "whatsapp_phone_number_id",
            "whatsapp_business_account_id",
            "whatsapp_access_token",
            "whatsapp_from_number",
            "updated_at",
        ]
    )
    _invalidate_communications_settings_cache()
    return get_communications_settings()


def update_sms_settings(
    *,
    provider: str = "",
    api_key: str = "",
    api_secret: str = "",
    sender_id: str = "",
    api_base_url: str = "",
) -> CompanyCommunicationsSettings:
    row = get_communications_settings()
    provider = (provider or "").strip() or row.sms_provider
    if provider not in {choice.value for choice in SmsProvider}:
        raise ValidationError("Select a valid SMS provider.")
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()
    sender_id = (sender_id or "").strip()
    api_base_url = (api_base_url or "").strip()

    row.sms_provider = provider
    if api_key:
        row.sms_api_key = api_key
    if api_secret:
        row.sms_api_secret = api_secret
    row.sms_sender_id = sender_id
    row.sms_api_base_url = api_base_url

    if not row.has_sms_credentials():
        raise ValidationError("API key and sender ID are required for Text / SMS.")

    row.save(
        update_fields=[
            "sms_provider",
            "sms_api_key",
            "sms_api_secret",
            "sms_sender_id",
            "sms_api_base_url",
            "updated_at",
        ]
    )
    _invalidate_communications_settings_cache()
    return get_communications_settings()


def update_message_channel_settings(
    *,
    from_name: str = "",
    reply_to: str = "",
) -> CompanyCommunicationsSettings:
    row = get_communications_settings()
    from_name = (from_name or "").strip()
    reply_to = (reply_to or "").strip().lower()
    if not from_name:
        raise ValidationError("Enter a sender name for the Message channel.")
    if reply_to:
        try:
            validate_email(reply_to)
        except ValidationError as exc:
            raise ValidationError("Enter a valid reply-to email.") from exc
    row.message_from_name = from_name
    row.message_reply_to = reply_to
    row.save(update_fields=["message_from_name", "message_reply_to", "updated_at"])
    _invalidate_communications_settings_cache()
    return get_communications_settings()


def daraja_settings_as_dict(settings_row: CompanyDarajaSettings | None = None) -> dict:
    row = settings_row or get_daraja_settings()
    from django.conf import settings as dj_settings
    from shops.daraja_stk import _callback_url, is_safaricom_callback_base

    callback_base = (row.callback_base_url or "").strip() or (
        getattr(dj_settings, "DARAJA_CALLBACK_BASE_URL", "") or ""
    ).strip()
    callback_full = ""
    try:
        if is_safaricom_callback_base(callback_base):
            callback_full = _callback_url()
        elif callback_base:
            callback_full = f"{callback_base.rstrip('/')}/mpesa/daraja/callback/"
    except Exception:
        callback_full = ""
    return {
        "enable_stk_push": bool(row.enable_stk_push),
        "environment": row.environment or DarajaEnvironment.SANDBOX,
        "environment_label": row.get_environment_display(),
        "consumer_key": row.consumer_key or "",
        "consumer_key_set": bool((row.consumer_key or "").strip()),
        "consumer_secret_set": bool((row.consumer_secret or "").strip()),
        "passkey_set": bool((row.passkey or "").strip()),
        "shortcode": row.shortcode or "",
        "callback_base_url": callback_base,
        "callback_url": callback_full,
        "callback_is_public": is_safaricom_callback_base(callback_base),
        "credentials_valid": bool(row.credentials_valid),
        "credentials_checked_at": row.credentials_checked_at,
        "last_error": row.last_error or "",
        "has_credentials": row.has_credentials(),
        "has_callback_base": row.has_usable_callback_base(),
        "is_ready_for_stk": row.is_ready_for_stk(),
        "stk_not_ready_reason": row.stk_not_ready_reason(),
    }


def verify_daraja_oauth(*, consumer_key: str, consumer_secret: str, environment: str) -> dict:
    """Call Safaricom OAuth to confirm consumer key/secret for the environment."""
    import json
    import urllib.error
    import urllib.request

    env = (environment or "").strip().lower()
    if env not in DARAJA_OAUTH_URLS:
        raise ValidationError("Choose Sandbox or Production.")
    key = (consumer_key or "").strip()
    secret = (consumer_secret or "").strip()
    if not key or not secret:
        raise ValidationError("Consumer key and consumer secret are required.")

    token = base64.b64encode(f"{key}:{secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        DARAJA_OAUTH_URLS[env],
        method="GET",
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:180]
        except Exception:
            detail = ""
        message = "Safaricom rejected these credentials."
        if detail:
            message = f"{message} {detail}"
        raise ValidationError(message) from exc
    except urllib.error.URLError as exc:
        raise ValidationError(
            "Could not reach Safaricom Daraja. Check your internet connection and try again."
        ) from exc
    except Exception as exc:
        raise ValidationError("Could not verify Daraja credentials.") from exc

    access_token = (payload.get("access_token") or "").strip()
    if not access_token:
        raise ValidationError("Safaricom did not return an access token for these credentials.")
    return {
        "ok": True,
        "access_token": access_token,
        "expires_in": payload.get("expires_in"),
    }


def set_daraja_stk_enabled(*, enabled: bool) -> CompanyDarajaSettings:
    row = get_daraja_settings()
    if enabled and not row.credentials_valid:
        raise ValidationError(
            "Save and verify Daraja credentials before enabling STK Push."
        )
    if enabled and not row.has_credentials():
        raise ValidationError("Complete Daraja credentials before enabling STK Push.")
    if enabled and not row.has_usable_callback_base():
        # Last chance: pick up a running ngrok tunnel before failing.
        from shops.daraja_stk import sync_callback_base_from_request

        sync_callback_base_from_request(None, persist=True)
        row = get_daraja_settings()
    if enabled and not row.has_usable_callback_base():
        raise ValidationError(
            "Start ngrok (ngrok http 8000), refresh this page, then enable STK Push."
        )
    row.enable_stk_push = bool(enabled)
    if not enabled:
        row.last_error = ""
    row.save(update_fields=["enable_stk_push", "last_error", "updated_at"])
    _invalidate_daraja_settings_cache()
    return get_daraja_settings()


def update_daraja_settings(
    *,
    environment: str,
    shortcode: str,
    consumer_key: str = "",
    consumer_secret: str = "",
    passkey: str = "",
    callback_base_url: str = "",
    enable_stk_push=None,
    request=None,
) -> CompanyDarajaSettings:
    """Save Daraja credentials and verify them against Safaricom before keeping them."""
    from django.conf import settings as dj_settings

    from shops.daraja_stk import (
        is_safaricom_callback_base,
        sync_callback_base_from_request,
        validate_callback_base_url,
    )

    env = (environment or "").strip().lower()
    if env not in {choice.value for choice in DarajaEnvironment}:
        raise ValidationError("Choose Sandbox or Production.")

    code = (shortcode or "").strip()
    if not code:
        raise ValidationError("Business shortcode / till / paybill is required.")
    if not re.fullmatch(r"\d+", code):
        raise ValidationError("Shortcode must be digits only.")
    if len(code) < 5 or len(code) > 10:
        raise ValidationError("Enter a valid shortcode (5–10 digits).")

    # Prefer the URL the browser is actually using (hosted or local/ngrok).
    if request is not None:
        callback = sync_callback_base_from_request(request, persist=False)
    else:
        callback = validate_callback_base_url(callback_base_url)
    env_callback = (getattr(dj_settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip()
    if not callback:
        callback = validate_callback_base_url(callback_base_url) or ""

    row = get_daraja_settings()
    key = (consumer_key or "").strip() or (row.consumer_key or "").strip()
    secret = (consumer_secret or "").strip() or (row.consumer_secret or "").strip()
    lipa_passkey = (passkey or "").strip() or (row.passkey or "").strip()

    if not key:
        raise ValidationError("Consumer key is required.")
    if not secret:
        raise ValidationError("Consumer secret is required.")
    if not lipa_passkey:
        raise ValidationError("Lipa Na M-Pesa passkey is required.")

    try:
        verify_daraja_oauth(
            consumer_key=key,
            consumer_secret=secret,
            environment=env,
        )
    except ValidationError as exc:
        row.credentials_valid = False
        row.credentials_checked_at = timezone.now()
        row.last_error = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        if enable_stk_push is True:
            row.enable_stk_push = False
        row.save(
            update_fields=[
                "credentials_valid",
                "credentials_checked_at",
                "last_error",
                "enable_stk_push",
                "updated_at",
            ]
        )
        _invalidate_daraja_settings_cache()
        raise

    row.environment = env
    row.shortcode = code
    if callback:
        row.callback_base_url = callback
    if (consumer_key or "").strip():
        row.consumer_key = key
    if (consumer_secret or "").strip():
        row.consumer_secret = secret
    if (passkey or "").strip():
        row.passkey = lipa_passkey
    row.credentials_valid = True
    row.credentials_checked_at = timezone.now()
    row.last_error = ""
    update_fields = [
        "environment",
        "shortcode",
        "callback_base_url",
        "consumer_key",
        "consumer_secret",
        "passkey",
        "credentials_valid",
        "credentials_checked_at",
        "last_error",
        "updated_at",
    ]
    public_callback = is_safaricom_callback_base(callback or "") or is_safaricom_callback_base(
        env_callback
    )
    if enable_stk_push is not None:
        if enable_stk_push and not (key and secret and lipa_passkey and code):
            raise ValidationError("Complete Daraja credentials before enabling STK Push.")
        if enable_stk_push and not public_callback:
            raise ValidationError(
                "Open this page via your public HTTPS domain or ngrok link first, "
                "then enable STK Push."
            )
        row.enable_stk_push = bool(enable_stk_push)
        update_fields.append("enable_stk_push")
    elif row.enable_stk_push and not public_callback:
        # Still allow credentials save on localhost; just keep STK toggle honest.
        pass
    row.save(update_fields=update_fields)
    _invalidate_daraja_settings_cache()
    return get_daraja_settings()


def get_company_profile() -> CompanyProfile:
    profile_row, _ = CompanyProfile.objects.get_or_create(pk=1)
    return profile_row


def get_company_display_name() -> str:
    """Company profile name for branding, with MY-SHOP as fallback."""
    try:
        name = (get_company_profile().name or "").strip()
    except DatabaseError:
        return DEFAULT_COMPANY_NAME
    return name or DEFAULT_COMPANY_NAME


def validate_company_profile_payload(data, files, *, existing: CompanyProfile) -> dict:
    name = (data.get("name") or "").strip()
    phone_number = (data.get("phone_number") or "").strip()
    email = (data.get("email") or "").strip()
    location = (data.get("location") or "").strip()
    logo = files.get("logo")
    remove_logo = (data.get("remove_logo") or "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )

    errors = []
    cleaned = {}

    if not name:
        errors.append("Company name is required.")
    else:
        cleaned["name"] = name.upper()

    if not phone_number:
        errors.append("Company phone number is required.")
    elif not PHONE_RE.match(phone_number):
        errors.append("Enter a valid company phone number.")
    else:
        cleaned["phone_number"] = phone_number.upper()

    if not email:
        errors.append("Company email is required.")
    else:
        email_value = email.lower()
        try:
            validate_email(email_value)
        except ValidationError:
            errors.append("Enter a valid company email.")
        else:
            cleaned["email"] = email_value

    if not location:
        errors.append("Company location is required.")
    else:
        cleaned["location"] = location.upper()

    if logo:
        if logo.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Company logo must be JPG, PNG, WEBP, or GIF.")
        elif logo.size > MAX_IMAGE_BYTES:
            errors.append("Company logo must be 5 MB or smaller.")
        else:
            cleaned["logo"] = logo
    elif remove_logo and existing.logo:
        cleaned["remove_logo"] = True

    if errors:
        raise ValidationError(errors)

    return cleaned


def update_company_profile(data, files) -> CompanyProfile:
    profile_row = get_company_profile()
    cleaned = validate_company_profile_payload(data, files, existing=profile_row)

    profile_row.name = cleaned["name"]
    profile_row.phone_number = cleaned["phone_number"]
    profile_row.email = cleaned["email"]
    profile_row.location = cleaned["location"]

    update_fields = ["name", "phone_number", "email", "location", "updated_at"]

    if cleaned.get("logo"):
        if profile_row.logo:
            profile_row.logo.delete(save=False)
        profile_row.logo = cleaned["logo"]
        update_fields.append("logo")
    elif cleaned.get("remove_logo"):
        if profile_row.logo:
            profile_row.logo.delete(save=False)
        profile_row.logo = None
        update_fields.append("logo")

    profile_row.save(update_fields=update_fields)
    return profile_row


def set_company_pos_setting(*, field: str, enabled: bool) -> CompanyPosSettings:
    if field not in POS_SETTING_FIELDS:
        raise ValidationError("Unknown POS setting.")
    settings_row = get_company_pos_settings()
    setattr(settings_row, field, bool(enabled))
    settings_row.save(update_fields=[field, "updated_at"])
    _invalidate_pos_settings_cache()
    return settings_row


def set_company_tax_percent(*, percent) -> CompanyPosSettings:
    try:
        rate = Decimal(str(percent).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid tax percentage.") from exc
    if rate < 0 or rate > Decimal("100"):
        raise ValidationError("Tax percentage must be between 0 and 100.")
    settings_row = get_company_pos_settings()
    settings_row.tax_percent = rate.quantize(Decimal("0.01"))
    settings_row.save(update_fields=["tax_percent", "updated_at"])
    _invalidate_pos_settings_cache()
    return settings_row


def set_receipt_paper_width(*, width: str) -> CompanyPosSettings:
    value = (width or "").strip()
    if value not in RECEIPT_PAPER_WIDTHS:
        raise ValidationError("Choose 80 mm or 58 mm receipt paper.")
    settings_row = get_company_pos_settings()
    settings_row.receipt_paper_width = value
    settings_row.save(update_fields=["receipt_paper_width", "updated_at"])
    _invalidate_pos_settings_cache()
    return settings_row


def set_receipt_font_style(*, size: str, weight: str) -> CompanyPosSettings:
    size_value = (size or "").strip().lower()
    weight_value = (weight or "").strip().lower()
    if size_value not in RECEIPT_FONT_SIZES:
        raise ValidationError("Choose a valid receipt font size.")
    if weight_value not in RECEIPT_FONT_WEIGHTS:
        raise ValidationError("Choose a valid receipt font boldness.")
    settings_row = get_company_pos_settings()
    settings_row.receipt_font_size = size_value
    settings_row.receipt_font_weight = weight_value
    settings_row.save(
        update_fields=["receipt_font_size", "receipt_font_weight", "updated_at"]
    )
    _invalidate_pos_settings_cache()
    return settings_row


def receipt_font_style(settings_row: CompanyPosSettings | None = None) -> dict:
    row = settings_row or get_company_pos_settings()
    size = row.receipt_font_size if row.receipt_font_size in RECEIPT_FONT_SIZES else "medium"
    weight = (
        row.receipt_font_weight
        if row.receipt_font_weight in RECEIPT_FONT_WEIGHTS
        else "regular"
    )
    return {
        "size": size,
        "weight": weight,
        "size_px_80": RECEIPT_FONT_SIZE_PX[size]["80"],
        "size_px_58": RECEIPT_FONT_SIZE_PX[size]["58"],
        "weight_css": RECEIPT_FONT_WEIGHT_CSS[weight],
    }


def _normalize_website_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    if not WEBSITE_URL_RE.match(raw):
        raise ValidationError("Enter a valid website URL (e.g. https://example.com).")
    if len(raw) > 255:
        raise ValidationError("Website URL is too long.")
    return raw


def set_receipt_qr_settings(
    *,
    enabled: bool,
    content: str = "website",
    website: str = "",
) -> CompanyPosSettings:
    content_value = (content or "").strip().lower() or "website"
    if content_value not in RECEIPT_QR_CONTENTS:
        raise ValidationError("Choose company website or receipt details for the QR code.")

    settings_row = get_company_pos_settings()
    website_value = (settings_row.receipt_qr_website or "").strip()
    if enabled and content_value == "website":
        website_value = _normalize_website_url(website)
        if not website_value:
            raise ValidationError("Enter the company website URL for the QR code.")
    elif (website or "").strip():
        website_value = _normalize_website_url(website)

    settings_row.enable_receipt_qr = bool(enabled)
    settings_row.receipt_qr_content = content_value
    settings_row.receipt_qr_website = website_value
    settings_row.save(
        update_fields=[
            "enable_receipt_qr",
            "receipt_qr_content",
            "receipt_qr_website",
            "updated_at",
        ]
    )
    _invalidate_pos_settings_cache()
    return settings_row


def receipt_details_qr_payload(
    *,
    receipt_number: str,
    kind_label: str,
    shop_name: str,
    client_name: str = "",
    total: str = "",
    date_label: str = "",
    payment: str = "",
) -> str:
    rows = [
        f"{get_company_display_name()} RECEIPT",
        f"No: {receipt_number}",
        f"Type: {kind_label}",
    ]
    if shop_name:
        rows.append(f"Shop: {shop_name}")
    if date_label:
        rows.append(f"Date: {date_label}")
    if client_name:
        rows.append(f"Client: {client_name}")
    if total:
        rows.append(f"Total: KSh {total}")
    if payment:
        rows.append(f"Paid: {payment}")
    return "\n".join(rows)


def qr_code_data_url(payload: str, *, box_size: int = 4, border: int = 1) -> str:
    text = (payload or "").strip()
    if not text:
        return ""
    try:
        import qrcode
    except ImportError as exc:
        raise ValidationError("QR code support is not installed.") from exc

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=max(2, int(box_size)),
            border=max(1, int(border)),
        )
        qr.add_data(text)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Could not generate the QR code image.") from exc


def receipt_qr_for_settings(
    settings_row: CompanyPosSettings | None = None,
    *,
    preview: dict | None = None,
) -> dict:
    row = settings_row or get_company_pos_settings()
    enabled = bool(row.enable_receipt_qr)
    content = (
        row.receipt_qr_content
        if row.receipt_qr_content in RECEIPT_QR_CONTENTS
        else "website"
    )
    website = (row.receipt_qr_website or "").strip()
    preview = preview or {}
    cache_key = (
        f"{RECEIPT_QR_PREVIEW_CACHE_KEY}:"
        f"{int(enabled)}:{content}:{website}:"
        f"{preview.get('receipt_number') or ''}:"
        f"{preview.get('total') or ''}:"
        f"{preview.get('kind') or ''}"
    )
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    payload = ""
    label = ""
    if enabled:
        if content == "website":
            payload = website
            label = "Scan for website"
        else:
            payload = receipt_details_qr_payload(
                receipt_number=preview.get("receipt_number") or "RECEIPT",
                kind_label=preview.get("kind") or "Sale",
                shop_name=preview.get("shop_name") or "",
                client_name=preview.get("client") or "",
                total=preview.get("total") or "",
                date_label=preview.get("date") or "",
                payment=preview.get("payment") or "",
            )
            label = "Scan for receipt details"
    image = ""
    if payload:
        try:
            image = qr_code_data_url(payload, box_size=6, border=2)
        except ValidationError:
            image = ""
    result = {
        "enabled": enabled,
        "content": content,
        "website": website,
        "payload": payload,
        "label": label,
        "image_data_url": image,
        "ready": bool(image),
    }
    cache.set(cache_key, result, RECEIPT_QR_PREVIEW_CACHE_TTL)
    return result


def receipt_qr_for_receipt(receipt, settings_row: CompanyPosSettings | None = None) -> dict:
    row = settings_row or get_company_pos_settings()
    # Only hit the DB for QR flags when the caller did not pass settings
    # (cached POS settings can lag right after toggles in the settings UI).
    if settings_row is None:
        try:
            row.refresh_from_db(
                fields=["enable_receipt_qr", "receipt_qr_content", "receipt_qr_website"]
            )
        except Exception:
            row = get_company_pos_settings()
    if not row.enable_receipt_qr:
        return {
            "enabled": False,
            "content": row.receipt_qr_content or "website",
            "website": row.receipt_qr_website or "",
            "payload": "",
            "label": "",
            "image_data_url": "",
            "ready": False,
        }

    content = (
        row.receipt_qr_content
        if row.receipt_qr_content in RECEIPT_QR_CONTENTS
        else "website"
    )
    website = (row.receipt_qr_website or "").strip()
    if content == "website":
        payload = website
        label = "Scan for website"
    else:
        payment = ""
        if receipt.kind == ShopReceiptKind.SALE:
            if receipt.payment_method == ShopPaymentMethod.BOTH:
                payment = (
                    f"Cash {_receipt_money(receipt.cash_amount)}"
                    f" + M-Pesa {_receipt_money(receipt.mpesa_amount)}"
                )
            elif receipt.payment_method == ShopPaymentMethod.MPESA:
                payment = f"M-Pesa {_receipt_money(receipt.mpesa_amount)}"
            elif receipt.payment_method == ShopPaymentMethod.CASH:
                payment = f"Cash {_receipt_money(receipt.cash_amount)}"
        payload = receipt_details_qr_payload(
            receipt_number=receipt.receipt_number,
            kind_label=receipt.get_kind_display(),
            shop_name=receipt.shop.name if receipt.shop_id else "",
            client_name=receipt.client_name or "",
            total=_receipt_money(receipt.total),
            date_label=timezone.localtime(receipt.created_at).strftime("%d %b %Y %H:%M"),
            payment=payment,
        )
        label = "Scan for receipt details"

    image = ""
    if payload:
        try:
            image = qr_code_data_url(payload, box_size=6, border=2)
        except ValidationError:
            image = ""
    return {
        "enabled": True,
        "content": content,
        "website": website,
        "payload": payload,
        "label": label,
        "image_data_url": image,
        "ready": bool(image),
    }


def _clean_receipt_format(value: str, *, label: str) -> str:
    cleaned = (value or "").strip().upper()
    if not cleaned:
        raise ValidationError(f"Enter a format for {label}.")
    if not RECEIPT_FORMAT_RE.match(cleaned):
        raise ValidationError(
            f"{label} format must be 1–8 letters or numbers (no spaces or symbols)."
        )
    return cleaned


def set_receipt_number_formats(
    *,
    sale: str | None = None,
    credit: str | None = None,
    quotation: str | None = None,
) -> CompanyPosSettings:
    settings_row = get_company_pos_settings()
    updates = []
    if sale is not None:
        settings_row.receipt_format_sale = _clean_receipt_format(sale, label="Sale")
        updates.append("receipt_format_sale")
    if credit is not None:
        settings_row.receipt_format_credit = _clean_receipt_format(credit, label="Credit")
        updates.append("receipt_format_credit")
    if quotation is not None:
        settings_row.receipt_format_quotation = _clean_receipt_format(
            quotation, label="Quotation"
        )
        updates.append("receipt_format_quotation")
    if not updates:
        raise ValidationError("No receipt formats to update.")
    settings_row.save(update_fields=[*updates, "updated_at"])
    _invalidate_pos_settings_cache()
    return settings_row


def receipt_format_for_kind(
    kind: str, settings_row: CompanyPosSettings | None = None
) -> str:
    row = settings_row or get_company_pos_settings()
    key = (kind or "").strip().lower()
    field = RECEIPT_FORMAT_FIELDS.get(key)
    if not field:
        return RECEIPT_FORMAT_DEFAULTS["sale"]
    value = (getattr(row, field, None) or RECEIPT_FORMAT_DEFAULTS.get(key, "R")).strip()
    return value.upper() or RECEIPT_FORMAT_DEFAULTS.get(key, "R")


def format_simple_doc_number(prefix: str, seq: int) -> str:
    """Build a short document code like S0001 / I0042 (4+ digit sequence)."""
    code = (prefix or "").strip().upper() or "R"
    n = max(1, int(seq or 1))
    width = 4 if n < 10000 else len(str(n))
    return f"{code}{n:0{width}d}"


def preview_receipt_number(
    *,
    kind: str,
    shop_id: int = 12,
    settings_row: CompanyPosSettings | None = None,
) -> str:
    del shop_id  # Preview is shop-agnostic; sequence is illustrative.
    prefix = receipt_format_for_kind(kind, settings_row)
    return format_simple_doc_number(prefix, 1)


def _next_receipt_sequence(*, shop: Shop, kind: str, prefix: str) -> int:
    """
    Next sequence for shop+kind.

    Serializes on the shop row and caches the high-water mark so concurrent
    checkouts do not rescan every historical receipt number.
    """
    from .models import Shop, ShopReceipt

    prefix = (prefix or "").strip().upper()
    Shop.objects.select_for_update().filter(pk=shop.pk).only("pk").first()

    cache_key = f"receipt_seq:v1:{shop.pk}:{kind}:{prefix}"
    cached = cache.get(cache_key)
    if isinstance(cached, int) and cached >= 0:
        nxt = cached + 1
        cache.set(cache_key, nxt, timeout=60 * 60 * 24)
        return nxt

    max_seq = 0
    matched_prefix = False
    qs = ShopReceipt.objects.filter(shop=shop, kind=kind)
    if prefix:
        qs = qs.filter(receipt_number__istartswith=prefix)
    for raw in qs.values_list("receipt_number", flat=True).iterator(chunk_size=500):
        value = (raw or "").strip().upper()
        if prefix and value.startswith(prefix):
            suffix = value[len(prefix) :]
            if suffix.isdigit():
                matched_prefix = True
                max_seq = max(max_seq, int(suffix))
        elif value.isdigit():
            max_seq = max(max_seq, int(value))
    if not matched_prefix and max_seq == 0:
        max_seq = ShopReceipt.objects.filter(shop=shop, kind=kind).count()
    nxt = max_seq + 1
    cache.set(cache_key, nxt, timeout=60 * 60 * 24)
    return nxt


def _next_receipt_number(shop: Shop, *, kind: str) -> str:
    """Next short receipt code for this shop + kind (e.g. S0001)."""
    from .models import ShopReceipt

    prefix = receipt_format_for_kind(kind)
    cache_key = f"receipt_seq:v1:{shop.pk}:{kind}:{prefix}"
    seq = _next_receipt_sequence(shop=shop, kind=kind, prefix=prefix)
    # Guard against rare collisions (manual imports / format changes).
    for _ in range(10000):
        candidate = format_simple_doc_number(prefix, seq)
        if not ShopReceipt.objects.filter(shop=shop, receipt_number=candidate).exists():
            cache.set(cache_key, seq, timeout=60 * 60 * 24)
            return candidate
        seq += 1
    cache.set(cache_key, seq, timeout=60 * 60 * 24)
    # Extremely unlikely fallback.
    return format_simple_doc_number(prefix, seq)


DIGITS_RE = re.compile(r"^\d+$")
MPESA_COLLECTION_TYPES = ("paybill", "buy_goods")


def set_mpesa_payment_details(
    *,
    collection_type: str,
    business_number: str = "",
    account_number: str = "",
    till_number: str = "",
) -> CompanyPosSettings:
    kind = (collection_type or "").strip().lower()
    if kind and kind not in MPESA_COLLECTION_TYPES:
        raise ValidationError("Choose Paybill or Buy Goods.")

    business = (business_number or "").strip()
    account = (account_number or "").strip().upper()
    till = (till_number or "").strip()

    if kind == "paybill":
        if business:
            if not DIGITS_RE.match(business):
                raise ValidationError("Business number must be digits only.")
            if len(business) > 8:
                raise ValidationError("Business number must be at most 8 digits.")
            if len(business) >= 5 and not (5 <= len(business) <= 8):
                raise ValidationError("Business number must be 5–8 digits.")
        if account and len(account) > 40:
            raise ValidationError("Account number is too long.")
        till = ""
    elif kind == "buy_goods":
        if till:
            if not DIGITS_RE.match(till):
                raise ValidationError("Till number must be digits only.")
            if len(till) > 8:
                raise ValidationError("Till number must be at most 8 digits.")
        business = ""
        account = ""
    else:
        business = ""
        account = ""
        till = ""

    settings_row = get_company_pos_settings()
    settings_row.mpesa_collection_type = kind
    settings_row.mpesa_business_number = business
    settings_row.mpesa_account_number = account
    settings_row.mpesa_till_number = till
    settings_row.save(
        update_fields=[
            "mpesa_collection_type",
            "mpesa_business_number",
            "mpesa_account_number",
            "mpesa_till_number",
            "updated_at",
        ]
    )
    _invalidate_pos_settings_cache()
    return settings_row


def pos_settings_as_dict(settings_row: CompanyPosSettings | None = None) -> dict:
    row = settings_row or get_company_pos_settings()
    tax_percent = (
        row.effective_tax_percent() if row.enable_tax else Decimal(row.tax_percent or 0)
    )
    paper = row.receipt_paper_width if row.receipt_paper_width in RECEIPT_PAPER_WIDTHS else "80"
    payment = row.mpesa_payment_details()
    font = receipt_font_style(row)
    return {
        "enable_sale": row.enable_sale,
        "enable_credit": row.enable_credit,
        "enable_quotation": row.enable_quotation,
        "enable_cash": row.enable_cash,
        "enable_mpesa": row.enable_mpesa,
        "enable_cash_mpesa": row.enable_cash_mpesa,
        "enable_discount": row.enable_discount,
        "enable_tax": row.enable_tax,
        "tax_percent": str(Decimal(row.tax_percent or 0).quantize(Decimal("0.01"))),
        "effective_tax_percent": str(tax_percent.quantize(Decimal("0.01"))),
        "compulsory_print_on_sale": row.compulsory_print_on_sale,
        "enable_print_bluetooth": row.enable_print_bluetooth,
        "enable_print_usb": row.enable_print_usb,
        "enable_print_wifi": row.enable_print_wifi,
        "receipt_paper_width": paper,
        "receipt_font_size": font["size"],
        "receipt_font_weight": font["weight"],
        "receipt_font_size_px_80": font["size_px_80"],
        "receipt_font_size_px_58": font["size_px_58"],
        "receipt_font_weight_css": font["weight_css"],
        "receipt_format_sale": receipt_format_for_kind("sale", row),
        "receipt_format_credit": receipt_format_for_kind("credit", row),
        "receipt_format_quotation": receipt_format_for_kind("quotation", row),
        "mpesa_collection_type": payment.get("type") or row.mpesa_collection_type or "",
        "mpesa_business_number": row.mpesa_business_number or "",
        "mpesa_account_number": row.mpesa_account_number or "",
        "mpesa_till_number": row.mpesa_till_number or "",
        "mpesa_payment_details": payment,
        "enable_receipt_qr": bool(row.enable_receipt_qr),
        "receipt_qr_content": (
            row.receipt_qr_content
            if row.receipt_qr_content in RECEIPT_QR_CONTENTS
            else "website"
        ),
        "receipt_qr_website": row.receipt_qr_website or "",
        "kinds": row.enabled_kinds(),
        "payment_methods": row.enabled_payment_methods(),
        "cash_sale_checkout": row.cash_sale_checkout_enabled(),
        "print_channels": row.enabled_print_channels(),
    }

def validate_shop_payload(data, files, *, existing_shop=None) -> dict:
    name = (data.get("name") or "").strip()
    location = (data.get("location") or "").strip()
    email = (data.get("email") or "").strip()
    phone_number = (data.get("phone_number") or "").strip()
    login_code = (data.get("login_code") or "").strip()
    password = data.get("password") or ""
    password_confirm = data.get("password_confirm") or ""
    image = files.get("image")
    remove_image = (data.get("remove_image") or "").strip().lower() in ("1", "true", "on", "yes")

    errors = []
    cleaned = {}

    if not name:
        errors.append("Branch name is required.")
    else:
        cleaned["name"] = name.upper()

    if not location:
        errors.append("Branch location is required.")
    else:
        cleaned["location"] = location.upper()

    if not email:
        errors.append("Branch email is required.")
    else:
        email_value = email.lower()
        try:
            validate_email(email_value)
        except ValidationError:
            errors.append("Enter a valid branch email.")
        else:
            cleaned["email"] = email_value

    if not phone_number:
        errors.append("Branch phone number is required.")
    elif not PHONE_RE.match(phone_number):
        errors.append("Enter a valid branch phone number.")
    else:
        cleaned["phone_number"] = phone_number.upper()

    if not login_code:
        errors.append("Branch login code is required.")
    elif not LOGIN_CODE_RE.match(login_code):
        errors.append("Branch login code must be exactly 6 digits.")
    else:
        conflict = Shop.objects.filter(login_code=login_code)
        if existing_shop is not None:
            conflict = conflict.exclude(pk=existing_shop.pk)
        if conflict.exists():
            errors.append("Branch login code is already in use.")
        else:
            cleaned["login_code"] = login_code

    if existing_shop is None:
        if not password:
            errors.append("Password is required.")
        elif len(password) < MIN_PASSWORD_LENGTH:
            errors.append(
                "Password must be at least 6 characters. "
                "Letters, numbers, and symbols are allowed."
            )
        if password != password_confirm:
            errors.append("Password and confirm password do not match.")
        if password and len(password) >= MIN_PASSWORD_LENGTH and password == password_confirm:
            cleaned["password"] = password
    elif password or password_confirm:
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(
                "Password must be at least 6 characters. "
                "Letters, numbers, and symbols are allowed."
            )
        if password != password_confirm:
            errors.append("Password and confirm password do not match.")
        if password and len(password) >= MIN_PASSWORD_LENGTH and password == password_confirm:
            cleaned["password"] = password

    if image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Branch image must be JPG, PNG, WEBP, or GIF.")
        elif image.size > MAX_IMAGE_BYTES:
            errors.append("Branch image must be 5 MB or smaller.")
        else:
            cleaned["image"] = image
    elif remove_image and existing_shop and existing_shop.image:
        cleaned["remove_image"] = True

    if errors:
        raise ValidationError(errors)

    return cleaned


def create_shop(profile, data, files) -> Shop:
    cleaned = validate_shop_payload(data, files)
    company_hours = get_company_working_hours_settings()
    shop = Shop.objects.create(
        name=cleaned["name"],
        location=cleaned["location"],
        email=cleaned["email"],
        phone_number=cleaned["phone_number"],
        login_code=cleaned["login_code"],
        password_hash=make_password(cleaned["password"]),
        image=cleaned.get("image"),
        created_by=profile,
    )
    ShopWorkingHoursSettings.objects.create(
        shop=shop,
        start_time=company_hours.start_time,
        end_time=company_hours.end_time,
    )
    return shop


def update_shop(shop: Shop, data, files) -> Shop:
    cleaned = validate_shop_payload(data, files, existing_shop=shop)
    shop.name = cleaned["name"]
    shop.location = cleaned["location"]
    shop.email = cleaned["email"]
    shop.phone_number = cleaned["phone_number"]
    shop.login_code = cleaned["login_code"]

    if cleaned.get("password"):
        shop.password_hash = make_password(cleaned["password"])

    if cleaned.get("image"):
        if shop.image:
            shop.image.delete(save=False)
        shop.image = cleaned["image"]
    elif cleaned.get("remove_image"):
        if shop.image:
            shop.image.delete(save=False)
        shop.image = None

    shop.save()
    return shop


def toggle_shop_suspended(shop: Shop) -> Shop:
    shop.is_suspended = not shop.is_suspended
    shop.save(update_fields=["is_suspended", "updated_at"])
    return shop


def toggle_shop_hidden(shop: Shop) -> Shop:
    shop.is_hidden = not shop.is_hidden
    shop.save(update_fields=["is_hidden", "updated_at"])
    return shop


def delete_shop(shop: Shop) -> None:
    if shop.image:
        shop.image.delete(save=False)
    shop.delete()


def verify_shop_password(shop: Shop, password: str) -> bool:
    """Return True when the plain password matches the shop credential."""
    if not shop or not password:
        return False
    return check_password(password, shop.password_hash)


def verify_shop_login_code(shop: Shop, code: str) -> bool:
    """Return True when the 6-digit branch login code matches."""
    if not shop or not code:
        return False
    return bool(LOGIN_CODE_RE.match(code.strip()) and shop.login_code == code.strip())


def authenticate_shop_login(login_code: str, password: str):
    """
    Authenticate a shop portal sign-in.

    Returns the Shop on success, or None when credentials are invalid /
    the shop is suspended or hidden. Uses a dummy password check when the
    shop is missing so timing does not leak existence of a login code.
    """
    code = (login_code or "").strip()
    password = password or ""
    if not LOGIN_CODE_RE.match(code) or len(password) < 1:
        return None

    shop = (
        Shop.objects.filter(login_code=code)
        .only(
            "id",
            "name",
            "location",
            "login_code",
            "password_hash",
            "is_suspended",
            "is_hidden",
        )
        .first()
    )
    if shop is None:
        check_password(password, _shop_login_dummy_hash())
        return None
    if shop.is_hidden or shop.is_suspended:
        check_password(password, shop.password_hash or _shop_login_dummy_hash())
        return None
    if not verify_shop_password(shop, password):
        return None
    return shop


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid amount.") from None
    if amount < 0:
        raise ValidationError("Amounts cannot be negative.")
    return amount


def _normalize_phone(value: str) -> str:
    """Return digits-only Kenyan MSISDN (254XXXXXXXXX) when possible."""
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D+", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = f"254{digits[1:]}"
    elif digits.startswith("7") and len(digits) == 9:
        digits = f"254{digits}"
    elif digits.startswith("1") and len(digits) == 9:
        digits = f"254{digits}"
    if digits.startswith("254") and len(digits) == 12:
        return digits
    return ""


def format_kenya_phone(value: str) -> str:
    """Display/storage form with Kenyan country code, e.g. +2547XXXXXXXX."""
    normalized = _normalize_phone(value)
    if not normalized:
        return (value or "").strip()
    return f"+{normalized}"


def find_client_by_phone(phone: str):
    """Return a Client when the phone matches a registered number."""
    from .models import Client

    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    return Client.objects.filter(phone_normalized=normalized).first()


def search_clients_by_name(query: str, *, limit: int = 8):
    """Live-search registered clients by name (case-insensitive contains)."""
    from .models import Client

    q = (query or "").strip().upper()
    if len(q) < 2:
        return []
    return list(
        Client.objects.filter(full_name__icontains=q).order_by("full_name", "id")[
            : max(1, min(limit, 20))
        ]
    )


def upsert_client(*, full_name: str, phone: str, profile=None):
    """Create or update a client from checkout details."""
    from django.db import IntegrityError

    from .models import Client

    name = (full_name or "").strip().upper()
    phone_display = format_kenya_phone(phone)
    normalized = _normalize_phone(phone)
    if not name or not normalized:
        return None

    try:
        client, created = Client.objects.get_or_create(
            phone_normalized=normalized,
            defaults={
                "full_name": name,
                "phone_number": phone_display,
                "created_by": profile,
            },
        )
    except IntegrityError:
        client = Client.objects.filter(phone_normalized=normalized).first()
        created = False
        if client is None:
            raise

    if created:
        return client

    updates = []
    if name != client.full_name:
        client.full_name = name
        updates.append("full_name")
    if phone_display and phone_display != client.phone_number:
        client.phone_number = phone_display
        updates.append("phone_number")
    if updates:
        updates.append("updated_at")
        client.save(update_fields=updates)
    return client


def _whatsapp_url(*, phone: str, text: str) -> str:
    from urllib.parse import quote

    number = _normalize_phone(phone)
    if not number:
        return ""
    return f"https://wa.me/{number}?text={quote(text)}"


def _receipt_char_width(settings_row: CompanyPosSettings | None = None) -> int:
    row = settings_row or get_company_pos_settings()
    paper = row.receipt_paper_width if row.receipt_paper_width in RECEIPT_PAPER_WIDTHS else "80"
    return 32 if paper == "58" else 42


def _receipt_money(value) -> str:
    """Format receipt amounts as whole shillings (no decimals)."""
    try:
        amount = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"{amount.quantize(Decimal('1')):,.0f}"


def _receipt_pad_line(left: str, right: str, width: int) -> str:
    left = str(left or "")
    right = str(right or "")
    space = width - len(left) - len(right)
    if space < 1:
        keep = max(0, width - len(right) - 1)
        left = left[:keep]
        space = width - len(left) - len(right)
    return f"{left}{' ' * max(1, space)}{right}"


def _receipt_center(text: str, width: int) -> str:
    value = str(text or "").strip()
    if len(value) >= width:
        return value[:width]
    pad = width - len(value)
    return f"{' ' * (pad // 2)}{value}"


def _build_receipt_ticket_data(receipt, lines) -> dict:
    """
    Structured receipt payload matching the Receipt settings HTML preview.

    Used for browser/HTML print and as the source for plain-text ESC/POS.
    """
    pos = get_company_pos_settings()
    company = get_company_profile()
    kind = receipt.get_kind_display()
    shop = receipt.shop
    company_name = (company.name or "").strip() or (shop.name if shop else DEFAULT_COMPANY_NAME)
    company_location = (company.location or "").strip()
    company_phone = (company.phone_number or "").strip()
    if not company_location and shop:
        company_location = (shop.location or "").strip()
    if not company_phone and shop:
        company_phone = (shop.phone_number or "").strip()

    shop_branch = ""
    if shop and shop.name and shop.name.strip().upper() != company_name.upper():
        shop_branch = shop.name.strip()

    cashier = ""
    if receipt.created_by_id and receipt.created_by:
        user = receipt.created_by.user
        cashier = (
            user.get_full_name()
            or getattr(receipt.created_by, "employee_id", "")
            or user.username
            or ""
        )

    created = timezone.localtime(receipt.created_at)
    date_label = created.strftime("%d %b %Y · %H:%M")
    status = getattr(receipt, "status", ShopReceiptStatus.ACTIVE) or ShopReceiptStatus.ACTIVE
    status_label = ""
    if status != ShopReceiptStatus.ACTIVE:
        status_label = (
            receipt.get_status_display()
            if hasattr(receipt, "get_status_display")
            else status.replace("_", " ").title()
        )

    ticket_lines = []
    for line in lines:
        remaining_qty = getattr(line, "remaining_quantity", None)
        if remaining_qty is None:
            returned_qty = int(getattr(line, "returned_quantity", 0) or 0)
            remaining_qty = max(0, int(line.quantity or 0) - returned_qty)
        if remaining_qty <= 0:
            continue
        unit = Decimal(line.unit_price or 0)
        line_total = (unit * remaining_qty).quantize(Decimal("0.01"))
        serials = list(
            getattr(line, "remaining_serial_numbers", None)
            or getattr(line, "serial_numbers", None)
            or []
        )
        ticket_lines.append(
            {
                "name": str(line.item_name or "Item"),
                "qty": int(remaining_qty),
                "price": _receipt_money(unit),
                "total": _receipt_money(line_total),
                "serials": [str(s) for s in serials[:12]],
                "serials_extra": max(0, len(serials) - 12),
            }
        )

    payment = ""
    if receipt.kind == ShopReceiptKind.SALE and receipt.total > 0:
        if receipt.payment_method == ShopPaymentMethod.BOTH:
            payment = (
                f"Cash {_receipt_money(receipt.cash_amount)}"
                f" + M-Pesa {_receipt_money(receipt.mpesa_amount)}"
            )
        elif receipt.payment_method == ShopPaymentMethod.MPESA:
            payment = f"M-Pesa {_receipt_money(receipt.mpesa_amount)}"
        elif receipt.payment_method == ShopPaymentMethod.CASH:
            payment = f"Cash {_receipt_money(receipt.cash_amount)}"

    credit_due_label = ""
    if receipt.kind == ShopReceiptKind.CREDIT and receipt.credit_due_date:
        credit_due_label = receipt.credit_due_date.strftime("%d %b %Y")

    show_tax = bool(receipt.tax_amount and receipt.tax_amount > 0)
    tax_pct = (
        f"{receipt.tax_percent.quantize(Decimal('1')):.0f}" if show_tax else "0"
    )
    payment_details = pos.mpesa_payment_details()
    logo_url = ""
    try:
        if company.logo:
            logo_url = company.logo.url
    except (ValueError, AttributeError):
        logo_url = ""

    return {
        "mark": (company_name or DEFAULT_COMPANY_NAME).upper(),
        "shop_name": company_name.upper(),
        "shop_location": company_location,
        "shop_phone": company_phone,
        "shop_branch": shop_branch,
        "logo_url": logo_url,
        "receipt_number": receipt.receipt_number,
        "kind": kind,
        "date": date_label,
        "client": receipt.client_name or "—",
        "cashier": cashier,
        "status": status_label,
        "lines": ticket_lines,
        "cancelled": bool(
            not ticket_lines and status == ShopReceiptStatus.CANCELLED
        ),
        "subtotal": _receipt_money(receipt.subtotal),
        "tax_percent": tax_pct,
        "tax_amount": _receipt_money(receipt.tax_amount),
        "show_tax": show_tax,
        "total": _receipt_money(receipt.total),
        "payment": payment,
        "credit_due_date": credit_due_label,
        "payment_details": {
            "label": payment_details.get("label") or "",
            "lines": list(payment_details.get("lines") or []),
        },
        "footer": "Thank you for shopping with us",
    }


def _render_receipt_text(ticket: dict, *, paper_width: str | None = None) -> str:
    """Render plain-text receipt (ESC/POS / WhatsApp) from ticket data."""
    pos = get_company_pos_settings()
    if paper_width in RECEIPT_PAPER_WIDTHS:
        width = 32 if paper_width == "58" else 42
    else:
        width = _receipt_char_width(pos)
    rule = "-" * width
    mark = (ticket.get("mark") or DEFAULT_COMPANY_NAME).strip()
    shop_name = (ticket.get("shop_name") or "").strip()
    rows = []
    if mark and mark.upper() != shop_name.upper():
        rows.append(_receipt_center(mark, width))
    rows.append(_receipt_center(shop_name, width))
    if ticket.get("shop_location"):
        rows.append(_receipt_center(ticket["shop_location"], width))
    if ticket.get("shop_phone"):
        rows.append(_receipt_center(ticket["shop_phone"], width))
    if ticket.get("shop_branch"):
        rows.append(_receipt_center(ticket["shop_branch"], width))

    rows.extend(
        [
            rule,
            _receipt_pad_line("Receipt", ticket.get("receipt_number") or "", width),
            _receipt_pad_line("Type", ticket.get("kind") or "", width),
            _receipt_pad_line("Date", ticket.get("date") or "", width),
        ]
    )
    if ticket.get("route_from"):
        rows.append(_receipt_pad_line("From", ticket["route_from"], width))
    if ticket.get("route_to"):
        rows.append(_receipt_pad_line("To", ticket["route_to"], width))
    if ticket.get("client") or (
        ticket.get("party_label") and not ticket.get("route_from")
    ):
        rows.append(
            _receipt_pad_line(
                ticket.get("party_label") or "Client",
                ticket.get("client") or "—",
                width,
            )
        )
    if ticket.get("status"):
        rows.append(_receipt_pad_line("Status", ticket["status"], width))
    if ticket.get("credit_due_date"):
        rows.append(_receipt_pad_line("Pay by", ticket["credit_due_date"], width))
    if ticket.get("cashier"):
        rows.append(_receipt_pad_line("Cashier", ticket["cashier"], width))

    qty_only = bool(ticket.get("qty_only"))

    # Column widths tuned to mirror the HTML preview grid.
    if width <= 32:
        price_w, qty_w, total_w = 7, 3, 6
    else:
        price_w, qty_w, total_w = 8, 3, 7
    right_w = price_w + 1 + qty_w + 1 + total_w
    if qty_only:
        rows.extend([rule, _receipt_pad_line("Item", "Qty", width)])
    else:
        rows.extend(
            [
                rule,
                _receipt_pad_line(
                    "Item",
                    f"{'Price':<{price_w}} {'Qty':<{qty_w}} {'Total':>{total_w}}",
                    width,
                ),
            ]
        )

    if ticket.get("cancelled"):
        rows.append(_receipt_center("*** CANCELLED / RETURNED ***", width))
    else:
        for line in ticket.get("lines") or []:
            name = str(line.get("name") or "Item")
            qty = int(line.get("qty") or 0)
            if qty_only:
                left_max = max(6, width - 4)
                if len(name) > left_max:
                    rows.append(name[:width])
                    rows.append(_receipt_pad_line("", str(qty), width))
                else:
                    rows.append(_receipt_pad_line(name, str(qty), width))
            else:
                price_col = f"@{line.get('price') or '0'}"
                total_label = str(line.get("total") or "0")
                right = f"{price_col:<{price_w}} {qty:<{qty_w}d} {total_label:>{total_w}}"
                left_max = max(6, width - right_w - 1)
                if len(name) > left_max:
                    rows.append(name[:width])
                    rows.append(_receipt_pad_line("", right, width))
                else:
                    rows.append(_receipt_pad_line(name, right, width))
            for serial in line.get("serials") or []:
                rows.append(f"  SN {str(serial)[: max(8, width - 5)]}")
            extra = int(line.get("serials_extra") or 0)
            if extra > 0:
                rows.append(f"  +{extra} more serials")

    rows.append(rule)
    if qty_only:
        units = ticket.get("total_units")
        if units is None:
            units = sum(int(line.get("qty") or 0) for line in (ticket.get("lines") or []))
        rows.append(_receipt_pad_line("UNITS", str(units), width))
    else:
        if ticket.get("show_tax"):
            rows.append(
                _receipt_pad_line(
                    "Subtotal", f"KSh {ticket.get('subtotal') or '0'}", width
                )
            )
            rows.append(
                _receipt_pad_line(
                    f"Tax ({ticket.get('tax_percent') or '0'}%)",
                    f"KSh {ticket.get('tax_amount') or '0'}",
                    width,
                )
            )
        rows.append(
            _receipt_pad_line("TOTAL", f"KSh {ticket.get('total') or '0'}", width)
        )
        if ticket.get("payment"):
            rows.append(_receipt_pad_line("Paid", ticket["payment"], width))

    payment_details = ticket.get("payment_details") or {}
    if payment_details.get("lines"):
        rows.extend(
            [
                rule,
                _receipt_center(
                    f"M-Pesa {payment_details.get('label') or ''}".strip(), width
                ),
            ]
        )
        rows.extend(payment_details["lines"])

    rows.extend(
        [
            rule,
            _receipt_center(ticket.get("footer") or "Thank you for shopping with us", width),
        ]
    )
    return "\n".join(rows)


def _build_receipt_message(receipt, lines) -> str:
    """Build plain-text receipt matching Receipt settings preview layout."""
    return _render_receipt_text(_build_receipt_ticket_data(receipt, lines))


def _supplier_receipt_shop_header(shop: Shop) -> dict:
    """Company / shop header fields shared by supplier receipts."""
    company = get_company_profile()
    company_name = (company.name or "").strip() or (shop.name if shop else DEFAULT_COMPANY_NAME)
    company_location = (company.location or "").strip()
    company_phone = (company.phone_number or "").strip()
    if not company_location and shop:
        company_location = (shop.location or "").strip()
    if not company_phone and shop:
        company_phone = (shop.phone_number or "").strip()
    shop_branch = ""
    if shop and shop.name and shop.name.strip().upper() != company_name.upper():
        shop_branch = shop.name.strip()
    logo_url = ""
    try:
        if company.logo:
            logo_url = company.logo.url
    except (ValueError, AttributeError):
        logo_url = ""
    return {
        "mark": (company_name or DEFAULT_COMPANY_NAME).upper(),
        "shop_name": company_name.upper(),
        "shop_location": company_location,
        "shop_phone": company_phone,
        "shop_branch": shop_branch,
        "logo_url": logo_url,
    }


def _supplier_receipt_print_meta(pos_settings: CompanyPosSettings | None = None) -> dict:
    row = pos_settings or get_company_pos_settings()
    paper = (
        row.receipt_paper_width
        if row.receipt_paper_width in RECEIPT_PAPER_WIDTHS
        else "80"
    )
    channels = list(pos_settings_as_dict(row).get("print_channels") or [])
    return {
        "receipt_font": receipt_font_style(row),
        "receipt_paper_width": paper,
        "print_via": channels[0] if channels else "",
        "print_channels": channels,
        "receipt_qr": {"payload": "", "label": "", "ready": False, "image_data_url": ""},
    }


def build_stock_in_supplier_receipt(movement, *, shop: Shop, authorised_by=None) -> dict:
    """Build a supplier copy receipt after buying / stocking in items."""
    from items.models import StockPaymentStatus

    pos = get_company_pos_settings()
    lines = list(movement.lines.select_related("item").all())
    first = lines[0] if lines else None
    supplier_name = (getattr(first, "supplier_name", None) or "").strip() or "—"
    supplier_phone = ""
    if first:
        dial = (first.supplier_phone_country_code or "").strip()
        phone = (first.supplier_phone_number or "").strip()
        supplier_phone = f"{dial} {phone}".strip()

    payment_status = (getattr(first, "payment_status", None) or "").strip()
    payment_label = ""
    if payment_status:
        payment_label = dict(StockPaymentStatus.choices).get(
            payment_status, payment_status.replace("_", " ").title()
        )

    cashier = ""
    profile = authorised_by or getattr(movement, "created_by", None)
    if profile is not None and getattr(profile, "user", None) is not None:
        cashier = (
            profile.user.get_full_name()
            or getattr(profile, "employee_id", "")
            or profile.user.username
            or ""
        )

    ticket_lines = []
    total = Decimal("0")
    for line in lines:
        unit = Decimal(line.buying_price or 0)
        qty = int(line.quantity or 0)
        line_total = (unit * qty).quantize(Decimal("0.01"))
        total += line_total
        serials = list(line.serial_numbers or [])
        ticket_lines.append(
            {
                "name": str(getattr(line.item, "name", None) or "Item"),
                "qty": qty,
                "price": _receipt_money(unit),
                "total": _receipt_money(line_total),
                "serials": [str(s) for s in serials[:12]],
                "serials_extra": max(0, len(serials) - 12),
            }
        )

    created = timezone.localtime(movement.created_at)
    ticket = {
        **_supplier_receipt_shop_header(shop),
        "receipt_number": format_simple_doc_number(
            DOC_NUMBER_PREFIX["stock_in"], movement.pk
        ),
        "kind": "Stock purchase",
        "date": created.strftime("%d %b %Y · %H:%M"),
        "party_label": "Supplier",
        "client": supplier_name,
        "cashier": cashier,
        "status": "",
        "lines": ticket_lines,
        "cancelled": False,
        "subtotal": _receipt_money(total),
        "tax_percent": "0",
        "tax_amount": _receipt_money(0),
        "show_tax": False,
        "total": _receipt_money(total),
        "payment": payment_label,
        "payment_details": {"label": "", "lines": []},
        "footer": "Supplier copy · Goods received",
    }
    if supplier_phone and supplier_phone != supplier_name:
        ticket["client"] = f"{supplier_name} · {supplier_phone}"

    paper = (
        pos.receipt_paper_width
        if pos.receipt_paper_width in RECEIPT_PAPER_WIDTHS
        else "80"
    )
    meta = _supplier_receipt_print_meta(pos)
    return {
        "receipt_number": ticket["receipt_number"],
        "receipt_text": _render_receipt_text(ticket, paper_width=paper),
        "receipt_ticket": ticket,
        **meta,
    }


def build_stock_request_delivery_note(
    movement, *, shop: Shop, authorised_by=None
) -> dict:
    """Build a delivery note after accepting an inter-shop stock request."""
    pos = get_company_pos_settings()
    lines = list(movement.lines.select_related("item").all())
    from_shop = movement.requested_from_shop or shop
    to_shop = movement.shop
    from_name = (getattr(from_shop, "name", None) or "").strip() or "—"
    to_name = (getattr(to_shop, "name", None) or "").strip() or "—"

    cashier = ""
    profile = authorised_by or getattr(movement, "responded_by", None)
    if profile is not None and getattr(profile, "user", None) is not None:
        cashier = (
            profile.user.get_full_name()
            or getattr(profile, "employee_id", "")
            or profile.user.username
            or ""
        )

    ticket_lines = []
    total_units = 0
    for line in lines:
        qty = int(line.quantity or 0)
        if qty <= 0:
            continue
        total_units += qty
        serials = list(line.serial_numbers or [])
        ticket_lines.append(
            {
                "name": str(getattr(line.item, "name", None) or "Item"),
                "qty": qty,
                "price": "0",
                "total": "0",
                "serials": [str(s) for s in serials[:12]],
                "serials_extra": max(0, len(serials) - 12),
            }
        )

    stamped = getattr(movement, "responded_at", None) or movement.created_at
    stamped = timezone.localtime(stamped)
    ticket = {
        **_supplier_receipt_shop_header(from_shop),
        "receipt_number": format_simple_doc_number(
            DOC_NUMBER_PREFIX["delivery"], movement.pk
        ),
        "kind": "Delivery note",
        "date": stamped.strftime("%d %b %Y · %H:%M"),
        "party_label": "",
        "client": "",
        "route_from": from_name,
        "route_to": to_name,
        "cashier": cashier,
        "status": "Transferred",
        "lines": ticket_lines,
        "cancelled": False,
        "qty_only": True,
        "total_units": total_units,
        "subtotal": "0",
        "tax_percent": "0",
        "tax_amount": "0",
        "show_tax": False,
        "total": "0",
        "payment": "",
        "payment_details": {"label": "", "lines": []},
        "footer": "Delivery note · Goods transferred",
    }
    paper = (
        pos.receipt_paper_width
        if pos.receipt_paper_width in RECEIPT_PAPER_WIDTHS
        else "80"
    )
    meta = _supplier_receipt_print_meta(pos)
    return {
        "receipt_number": ticket["receipt_number"],
        "receipt_text": _render_receipt_text(ticket, paper_width=paper),
        "receipt_ticket": ticket,
        **meta,
    }


def build_expense_supplier_receipt(expense, *, shop: Shop, authorised_by: str = "") -> dict:
    """Build a supplier copy receipt after registering an expense."""
    pos = get_company_pos_settings()
    supplier_name = (expense.supplier_name or "").strip() or "—"
    dial = (expense.supplier_phone_country_code or "").strip()
    phone = (expense.supplier_phone_number or "").strip()
    supplier_phone = f"{dial} {phone}".strip()
    client = supplier_name
    if supplier_phone:
        client = f"{supplier_name} · {supplier_phone}"

    category = ""
    if hasattr(expense, "get_category_display"):
        category = expense.get_category_display()
    payment_label = ""
    if hasattr(expense, "get_payment_status_display"):
        payment_label = expense.get_payment_status_display()

    created = timezone.localtime(expense.created_at)
    amount = expense.amount
    ticket = {
        **_supplier_receipt_shop_header(shop),
        "receipt_number": format_simple_doc_number(
            DOC_NUMBER_PREFIX["expense"], expense.pk
        ),
        "kind": "Expense",
        "date": created.strftime("%d %b %Y · %H:%M"),
        "party_label": "Supplier",
        "client": client,
        "cashier": authorised_by or "",
        "status": category,
        "lines": [
            {
                "name": str(expense.name or "Expense"),
                "qty": 1,
                "price": _receipt_money(amount),
                "total": _receipt_money(amount),
                "serials": [],
                "serials_extra": 0,
            }
        ],
        "cancelled": False,
        "subtotal": _receipt_money(amount),
        "tax_percent": "0",
        "tax_amount": _receipt_money(0),
        "show_tax": False,
        "total": _receipt_money(amount),
        "payment": payment_label,
        "payment_details": {"label": "", "lines": []},
        "footer": "Supplier copy · Expense recorded",
    }
    paper = (
        pos.receipt_paper_width
        if pos.receipt_paper_width in RECEIPT_PAPER_WIDTHS
        else "80"
    )
    meta = _supplier_receipt_print_meta(pos)
    return {
        "receipt_number": ticket["receipt_number"],
        "receipt_text": _render_receipt_text(ticket, paper_width=paper),
        "receipt_ticket": ticket,
        **meta,
    }


def complete_shop_checkout(*, shop: Shop, profile, payload: dict) -> dict:
    """
    Complete a MY-SHOP cart checkout.

    Validates the authorising staff 6-digit ID, creates a receipt, and for
    sale/credit deducts shop stock. Quotations never touch stock.
    """
    from employees.services import verify_active_employee_code
    from items.models import Item, ItemSerial, ShopItemPrice, ShopStock

    from .models import ShopReceipt, ShopReceiptLine

    kind = (payload.get("kind") or ShopReceiptKind.SALE).strip().lower()
    if kind not in ShopReceiptKind.values:
        raise ValidationError("Choose sale, credit, or quotation.")

    pos_settings = get_company_pos_settings()
    if not pos_settings.kind_enabled(kind):
        raise ValidationError("That transaction type is disabled in POS settings.")

    print_via = (payload.get("print_via") or "").strip().lower()
    if kind == ShopReceiptKind.SALE and pos_settings.compulsory_print_on_sale:
        if not print_via:
            raise ValidationError("Select a print method before completing the sale.")
        if not pos_settings.print_channel_enabled(print_via):
            raise ValidationError("That print method is disabled in POS settings.")
    elif print_via and not pos_settings.print_channel_enabled(print_via):
        raise ValidationError("That print method is disabled in POS settings.")

    login_code = (payload.get("login_code") or "").strip()
    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        raise ValidationError("Enter a valid active staff 6-digit ID.")

    from employees.module_permissions import ensure_employee_may

    ensure_employee_may(
        authorising,
        "my-shop",
        kind,
        message=f"You do not have permission to complete a {kind}.",
    )

    client_name = (payload.get("client_name") or "").strip().upper()
    client_phone_raw = (payload.get("client_phone") or "").strip()
    client_phone = format_kenya_phone(client_phone_raw) if client_phone_raw else ""

    share_whatsapp = bool(payload.get("share_whatsapp"))
    if kind != ShopReceiptKind.QUOTATION:
        share_whatsapp = False
    if share_whatsapp and not client_phone_raw:
        raise ValidationError("Client phone is required to share on WhatsApp.")

    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationError("Add at least one item to the cart.")

    from items.services import _normalize_serial_list

    parsed_lines = []
    errors = []
    item_ids = set()
    for raw in raw_lines:
        try:
            item_id = int(raw.get("id") or raw.get("item_id") or 0)
        except (TypeError, ValueError):
            item_id = 0
        try:
            qty = int(raw.get("qty") or raw.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        try:
            unit_price = _money(raw.get("price") or raw.get("unit_price") or 0)
        except ValidationError as exc:
            errors.extend(exc.messages)
            continue

        serials = _normalize_serial_list(
            raw.get("serials") or raw.get("serial_numbers") or []
        )
        if serials and qty <= 0:
            qty = len(serials)
        elif serials and qty != len(serials):
            errors.append(
                "Serial-tracked items need one serial number per unit sold."
            )
            continue

        if item_id <= 0 or qty <= 0:
            errors.append("Each cart line needs a valid item and quantity.")
            continue

        item_ids.add(item_id)
        parsed_lines.append(
            {
                "item_id": item_id,
                "qty": qty,
                "unit_price": unit_price,
                "serial_numbers": serials,
            }
        )

    with transaction.atomic():
        # Batch-load items, shop prices, and locked stock rows (avoids N+1 under lock).
        items_by_id = Item.objects.filter(
            pk__in=item_ids, is_suspended=False
        ).in_bulk()
        price_by_item = {
            item_id: price
            for item_id, price in ShopItemPrice.objects.filter(
                shop=shop, item_id__in=item_ids
            ).values_list("item_id", "price")
        }
        stock_by_item = {
            row.item_id: row
            for row in ShopStock.objects.select_for_update().filter(
                shop=shop, item_id__in=item_ids
            )
        }
        missing_stock_ids = [
            item_id for item_id in item_ids if item_id not in stock_by_item
        ]
        if missing_stock_ids:
            ShopStock.objects.bulk_create(
                [
                    ShopStock(shop=shop, item_id=item_id, quantity=0)
                    for item_id in missing_stock_ids
                ],
                ignore_conflicts=True,
            )
            for row in ShopStock.objects.select_for_update().filter(
                shop=shop, item_id__in=missing_stock_ids
            ):
                stock_by_item[row.item_id] = row

        from items.services import last_buying_prices_for_items, resolve_sale_unit_cost

        fallback_costs = {}
        if kind != ShopReceiptKind.QUOTATION:
            fallback_costs = last_buying_prices_for_items(
                item_ids, prefer_shop_id=shop.pk
            )

        prepared = []
        has_serial_sale_lines = False
        for row in parsed_lines:
            item_id = row["item_id"]
            qty = row["qty"]
            unit_price = row["unit_price"]
            serials = row["serial_numbers"]
            item = items_by_id.get(item_id)
            if item is None:
                errors.append(f"Item #{item_id} is unavailable.")
                continue

            override = price_by_item.get(item_id) if item.use_individual_shop_prices else None
            list_price = item.resolve_list_price(override)

            if not pos_settings.enable_discount:
                unit_price = list_price
            elif unit_price < item.minimum_selling_price:
                errors.append(
                    f"“{item.name}” is below the minimum selling price "
                    f"(KSh {item.minimum_selling_price})."
                )
                continue

            stock = stock_by_item.get(item_id)
            if stock is None:
                errors.append(f"Item #{item_id} is unavailable.")
                continue
            if kind != ShopReceiptKind.QUOTATION and stock.quantity < qty:
                errors.append(
                    f"Insufficient stock for “{item.name}” "
                    f"(available {stock.quantity}, requested {qty})."
                )
                continue

            serial_objects = {}
            if item.track_serial_number and kind != ShopReceiptKind.QUOTATION:
                has_serial_sale_lines = True
                if not serials:
                    errors.append(f"“{item.name}” requires serial numbers.")
                    continue
                if len(serials) != qty:
                    errors.append(
                        f"“{item.name}”: quantity must match serial count."
                    )
                    continue
                available = {
                    serial.serial_number: serial
                    for serial in ItemSerial.objects.select_for_update().filter(
                        item=item,
                        shop=shop,
                        serial_number__in=serials,
                        is_available=True,
                    )
                }
                missing = [s for s in serials if s not in available]
                if missing:
                    errors.append(
                        f"“{item.name}”: serial not in stock at {shop.name} "
                        f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})."
                    )
                    continue
                serial_objects = available
            elif serials and not item.track_serial_number:
                serials = []

            unit_cost = Decimal("0.00")
            line_cogs = Decimal("0.00")
            if kind != ShopReceiptKind.QUOTATION:
                last_buy = fallback_costs.get(item_id)
                unit_cost = resolve_sale_unit_cost(
                    stock,
                    fallback=last_buy,
                    sell_ceiling=list_price,
                )
                line_cogs = (unit_cost * qty).quantize(Decimal("0.01"))
                if unit_cost > 0 and unit_price < unit_cost:
                    extra = ""
                    try:
                        last_dec = _money(last_buy) if last_buy is not None else Decimal("0.00")
                    except ValidationError:
                        last_dec = Decimal("0.00")
                    if last_dec > 0 and last_dec != unit_cost:
                        extra = f", last buy KSh {last_dec}"
                    errors.append(
                        f"“{item.name}” is below unit cost "
                        f"(sell KSh {unit_price}, cost KSh {unit_cost}{extra})."
                    )
                    continue

            prepared.append(
                {
                    "item": item,
                    "stock": stock,
                    "qty": qty,
                    "unit_price": unit_price,
                    "unit_cost": unit_cost,
                    "line_total": (unit_price * qty).quantize(Decimal("0.01")),
                    "line_cogs": line_cogs,
                    "serial_numbers": serials,
                    "serial_objects": serial_objects,
                }
            )

        if errors:
            raise ValidationError(errors)
        if not prepared:
            raise ValidationError("Add at least one valid item to the cart.")

        requires_client = (
            kind in {ShopReceiptKind.CREDIT, ShopReceiptKind.QUOTATION}
            or has_serial_sale_lines
        )
        if requires_client:
            if not client_name:
                raise ValidationError("Client full name is required.")
            if not client_phone_raw:
                raise ValidationError("Client phone number is required.")
        elif bool(client_name) != bool(client_phone_raw):
            raise ValidationError("Provide both client name and phone, or leave both blank.")

        if client_phone_raw:
            normalized = _normalize_phone(client_phone_raw)
            if not normalized:
                raise ValidationError(
                    "Enter a valid Kenyan phone number (e.g. 07XX XXX XXX or +2547XXXXXXXX)."
                )
            client_phone = format_kenya_phone(client_phone_raw)
        if share_whatsapp and not client_phone:
            raise ValidationError("Client phone is required to share on WhatsApp.")

        subtotal = sum((row["line_total"] for row in prepared), Decimal("0.00"))
        tax = pos_settings.tax_breakdown(subtotal)
        total = tax["total"]

        payment_method = ShopPaymentMethod.NONE
        cash_amount = Decimal("0.00")
        mpesa_amount = Decimal("0.00")

        if kind == ShopReceiptKind.SALE:
            if not pos_settings.cash_sale_checkout_enabled():
                raise ValidationError("No payment methods are enabled in POS settings.")
            payment_method = (payload.get("payment_method") or "").strip().lower()
            if payment_method not in {
                ShopPaymentMethod.CASH,
                ShopPaymentMethod.MPESA,
                ShopPaymentMethod.BOTH,
            }:
                raise ValidationError("Choose cash, M-Pesa, or both.")
            if not pos_settings.payment_method_enabled(payment_method):
                raise ValidationError("That payment method is disabled in POS settings.")

            if payment_method == ShopPaymentMethod.CASH:
                cash_amount = total
            elif payment_method == ShopPaymentMethod.MPESA:
                mpesa_amount = total
            else:
                cash_amount = _money(payload.get("cash_amount") or 0)
                mpesa_amount = _money(payload.get("mpesa_amount") or 0)
                paid = (cash_amount + mpesa_amount).quantize(Decimal("0.01"))
                if paid != total:
                    # Tolerate 1-cent drift from client rounding.
                    if abs(paid - total) <= Decimal("0.01"):
                        mpesa_amount = (total - cash_amount).quantize(Decimal("0.01"))
                    else:
                        raise ValidationError(
                            "Cash and M-Pesa amounts must add up to the cart total."
                        )
                if cash_amount <= 0 or mpesa_amount <= 0:
                    raise ValidationError(
                        "For split payment, both cash and M-Pesa amounts must be greater than zero."
                    )

            mpesa_receipt_number = ""
            from .daraja_stk import require_successful_stk, stk_ready

            stk_id = (payload.get("stk_payment_id") or "").strip()
            if mpesa_amount > 0 and stk_id:
                # STK is optional — only validate when the cashier sent a prompt.
                if not stk_ready():
                    raise ValidationError(
                        "STK Push is not enabled. Clear the STK payment or enable Daraja."
                    )
                if not client_phone_raw:
                    raise ValidationError(
                        "Client phone is required when completing a sale with STK Push."
                    )
                stk_payment = require_successful_stk(
                    public_id=stk_id,
                    expected_amount=mpesa_amount,
                    expected_phone=client_phone_raw,
                    purpose="sale",
                )
                mpesa_receipt_number = stk_payment.mpesa_receipt_number or ""
            else:
                mpesa_receipt_number = (payload.get("mpesa_receipt_number") or "").strip()
        else:
            mpesa_receipt_number = ""

        credit_due_date = None
        if kind == ShopReceiptKind.CREDIT:
            raw_due = (payload.get("credit_due_date") or "").strip()
            if not raw_due:
                raise ValidationError("Payment due date is required for credit sales.")
            try:
                from datetime import date

                credit_due_date = date.fromisoformat(raw_due)
            except ValueError:
                raise ValidationError("Enter a valid payment due date.")
            if credit_due_date < timezone.localdate():
                raise ValidationError("Payment due date cannot be in the past.")

        client = None
        if client_phone and client_name:
            client = upsert_client(
                full_name=client_name,
                phone=client_phone,
                profile=authorising,
            )

        receipt = ShopReceipt.objects.create(
            shop=shop,
            receipt_number=_next_receipt_number(shop, kind=kind),
            kind=kind,
            payment_method=payment_method,
            client=client,
            client_name=client_name,
            client_phone=client_phone,
            subtotal=tax["subtotal"],
            tax_percent=tax["tax_percent"],
            tax_amount=tax["tax_amount"],
            total=total,
            cash_amount=cash_amount,
            mpesa_amount=mpesa_amount,
            mpesa_receipt_number=mpesa_receipt_number,
            share_whatsapp=share_whatsapp,
            credit_due_date=credit_due_date,
            created_by=authorising,
        )

        if kind == ShopReceiptKind.SALE:
            stk_id = (payload.get("stk_payment_id") or "").strip()
            if stk_id:
                from .daraja_stk import get_stk_payment
                from .models import MpesaStkStatus

                stk_row = get_stk_payment(stk_id)
                if stk_row and stk_row.status == MpesaStkStatus.SUCCESS:
                    stk_row.receipt = receipt
                    stk_row.applied = True
                    stk_row.save(update_fields=["receipt", "applied", "updated_at"])

        line_rows = ShopReceiptLine.objects.bulk_create(
            [
                ShopReceiptLine(
                    receipt=receipt,
                    item=row["item"],
                    item_name=row["item"].name,
                    quantity=row["qty"],
                    unit_price=row["unit_price"],
                    unit_cost=row.get("unit_cost") or Decimal("0.00"),
                    line_total=row["line_total"],
                    line_cogs=row.get("line_cogs") or Decimal("0.00"),
                    serial_numbers=row.get("serial_numbers") or [],
                )
                for row in prepared
            ]
        )

        if kind == ShopReceiptKind.CREDIT and receipt.client_id:
            from shops.credit_audit import log_credit_receipt_issued

            log_credit_receipt_issued(receipt=receipt)

        stock_updates = []
        if kind != ShopReceiptKind.QUOTATION:
            now = timezone.now()
            stocks_to_update = []
            items_to_update = []
            serials_to_update = []
            for row in prepared:
                stock = row["stock"]
                item = row["item"]
                stock.quantity -= row["qty"]
                stock.updated_at = now
                item.stock = max(0, item.stock - row["qty"])
                item.updated_at = now
                stocks_to_update.append(stock)
                items_to_update.append(item)
                stock_updates.append(
                    {
                        "id": item.pk,
                        "quantity": int(stock.quantity),
                    }
                )
                serial_objects = row.get("serial_objects") or {}
                for serial in row.get("serial_numbers") or []:
                    obj = serial_objects.get(serial)
                    if obj is None:
                        continue
                    obj.is_available = False
                    obj.updated_at = now
                    serials_to_update.append(obj)
            ShopStock.objects.bulk_update(stocks_to_update, ["quantity", "updated_at"])
            Item.objects.bulk_update(items_to_update, ["stock", "updated_at"])
            if serials_to_update:
                ItemSerial.objects.bulk_update(
                    serials_to_update, ["is_available", "updated_at"]
                )

    ticket = _build_receipt_ticket_data(receipt, line_rows)
    message = _render_receipt_text(ticket)
    qr = receipt_qr_for_receipt(receipt, pos_settings)
    whatsapp_url = ""
    if share_whatsapp:
        whatsapp_url = _whatsapp_url(phone=client_phone, text=message)

    return {
        "receipt": receipt,
        "receipt_number": receipt.receipt_number,
        "kind": receipt.kind,
        "kind_label": receipt.get_kind_display(),
        "total": str(receipt.total),
        "whatsapp_url": whatsapp_url,
        "message": message,
        "receipt_ticket": ticket,
        "print_via": print_via,
        "print_required": bool(
            kind == ShopReceiptKind.SALE and pos_settings.compulsory_print_on_sale
        ),
        "authorised_by": authorising.user.get_full_name()
        or authorising.user.username,
        "receipt_qr": qr,
        "receipt_font": receipt_font_style(pos_settings),
        "receipt_paper_width": (
            pos_settings.receipt_paper_width
            if pos_settings.receipt_paper_width in RECEIPT_PAPER_WIDTHS
            else "80"
        ),
        "stock_updates": stock_updates,
        "mpesa_receipt_number": receipt.mpesa_receipt_number or "",
    }


def get_open_shop_day(shop: Shop):
    """Return the currently open day session for a shop, if any."""
    from .models import ShopDaySession

    return (
        ShopDaySession.objects.filter(shop=shop, closed_at__isnull=True)
        .select_related("opened_by__user")
        .first()
    )


def get_last_closed_shop_day(shop: Shop):
    """Most recently closed day session for a shop (for opening balance hints)."""
    from .models import ShopDaySession

    return (
        ShopDaySession.objects.filter(shop=shop, closed_at__isnull=False)
        .select_related("closed_by__user", "opened_by__user")
        .order_by("-closed_at")
        .first()
    )


def _session_activity_totals(session, *, sales=None, expenses=None) -> dict:
    """Cash/M-Pesa sales and expenses that fall inside a day session window."""
    from decimal import Decimal

    opened = session.opened_at
    closed = session.closed_at or timezone.now()
    cash_sales = Decimal("0.00")
    mpesa_sales = Decimal("0.00")
    expense_total = Decimal("0.00")

    for sale in sales or []:
        when = sale["created_at"]
        if opened <= when < closed:
            cash_sales += Decimal(sale.get("cash_amount") or 0)
            mpesa_sales += Decimal(sale.get("mpesa_amount") or 0)

    for expense in expenses or []:
        when = expense["created_at"]
        if opened <= when < closed:
            expense_total += Decimal(expense.get("amount") or 0)

    opening_cash = Decimal(session.opening_cash or 0)
    opening_mpesa = Decimal(session.opening_mpesa or 0)
    opening_credit = Decimal(session.opening_credit or 0)
    expected_cash = opening_cash + cash_sales - expense_total
    expected_mpesa = opening_mpesa + mpesa_sales

    closing_cash = (
        Decimal(session.closing_cash)
        if session.closing_cash is not None
        else None
    )
    closing_mpesa = (
        Decimal(session.closing_mpesa)
        if session.closing_mpesa is not None
        else None
    )
    closing_credit = (
        Decimal(session.closing_credit)
        if session.closing_credit is not None
        else None
    )

    return {
        "opening_cash": opening_cash,
        "opening_mpesa": opening_mpesa,
        "opening_credit": opening_credit,
        "opening_total": opening_cash + opening_mpesa + opening_credit,
        "closing_cash": closing_cash,
        "closing_mpesa": closing_mpesa,
        "closing_credit": closing_credit,
        "closing_total": (
            None
            if closing_cash is None
            else (closing_cash + (closing_mpesa or 0) + (closing_credit or 0))
        ),
        "cash_sales": cash_sales,
        "mpesa_sales": mpesa_sales,
        "sales_total": cash_sales + mpesa_sales,
        "expenses": expense_total,
        "expected_cash": expected_cash,
        "expected_mpesa": expected_mpesa,
        "expected_credit": opening_credit,
        "expected_total": expected_cash + expected_mpesa + opening_credit,
        "cash_variance": (
            None if closing_cash is None else closing_cash - expected_cash
        ),
        "mpesa_variance": (
            None if closing_mpesa is None else closing_mpesa - expected_mpesa
        ),
        "credit_variance": (
            None if closing_credit is None else closing_credit - opening_credit
        ),
        "total_variance": (
            None
            if closing_cash is None
            else (closing_cash + (closing_mpesa or 0) + (closing_credit or 0))
            - (expected_cash + expected_mpesa + opening_credit)
        ),
    }


def list_shop_day_sessions(shop: Shop, *, limit: int = 30):
    """Recent open/closed day sessions for a shop (newest first), with balances."""
    from .models import Expense, ShopDaySession, ShopReceipt

    try:
        limit_n = max(1, min(int(limit or 30), 100))
    except (TypeError, ValueError):
        limit_n = 30

    sessions = list(
        ShopDaySession.objects.filter(shop=shop)
        .select_related("opened_by__user", "closed_by__user")
        .order_by("-opened_at")[:limit_n]
    )
    if not sessions:
        return []

    min_opened = min(session.opened_at for session in sessions)
    max_closed = max(
        (session.closed_at or timezone.now()) for session in sessions
    )
    sales = list(
        ShopReceipt.objects.filter(
            shop=shop,
            kind=ShopReceiptKind.SALE,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .values("created_at", "cash_amount", "mpesa_amount")
    )
    expenses = list(
        Expense.objects.filter(
            shop=shop,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
        ).values("created_at", "amount")
    )

    rows = []
    for session in sessions:
        activity = _session_activity_totals(
            session, sales=sales, expenses=expenses
        )
        rows.append(
            {
                "session": session,
                "is_open": session.is_open,
                "opened_at": session.opened_at,
                "closed_at": session.closed_at,
                "opened_by": session.opened_by,
                "closed_by": session.closed_by,
                **activity,
            }
        )
    return rows


def day_session_balance_summary(session) -> dict:
    """Live expected balances for one session (open or closed)."""
    from .models import Expense, ShopReceipt

    if session is None:
        return {}

    closed = session.closed_at or timezone.now()
    sales = list(
        ShopReceipt.objects.filter(
            shop_id=session.shop_id,
            kind=ShopReceiptKind.SALE,
            created_at__gte=session.opened_at,
            created_at__lt=closed,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .values("created_at", "cash_amount", "mpesa_amount")
    )
    expenses = list(
        Expense.objects.filter(
            shop_id=session.shop_id,
            created_at__gte=session.opened_at,
            created_at__lt=closed,
        ).values("created_at", "amount")
    )
    return _session_activity_totals(session, sales=sales, expenses=expenses)


def _parse_balance_fields(payload: dict) -> dict:
    return {
        "cash": _money(payload.get("cash_amount") or payload.get("cash") or 0),
        "mpesa": _money(payload.get("mpesa_amount") or payload.get("mpesa") or 0),
        "credit": _money(payload.get("credit_amount") or payload.get("credit") or 0),
    }


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


@transaction.atomic
def open_shop_day(*, shop: Shop, payload: dict):
    """Open a shop day with opening balances and staff authorisation."""
    from employees.services import verify_active_employee_code

    from .models import ShopDaySession

    locked_open = (
        ShopDaySession.objects.select_for_update()
        .filter(shop=shop, closed_at__isnull=True)
        .first()
    )
    if locked_open is not None:
        raise ValidationError("This shop is already open.")

    login_code = (payload.get("login_code") or "").strip()
    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        raise ValidationError("Enter a valid active staff 6-digit ID.")

    from employees.module_permissions import ensure_employee_may

    ensure_employee_may(
        authorising,
        "my-shop",
        "open_close",
        message="You do not have permission to open this shop.",
    )

    if not _truthy(payload.get("stock_confirmed")):
        raise ValidationError("Confirm that stock is up to date before opening.")

    balances = _parse_balance_fields(payload)
    session = ShopDaySession.objects.create(
        shop=shop,
        opening_cash=balances["cash"],
        opening_mpesa=balances["mpesa"],
        opening_credit=balances["credit"],
        stock_confirmed_open=True,
        opened_by=authorising,
    )
    return {
        "session": session,
        "action": "open",
        "authorised_by": authorising.user.get_full_name()
        or authorising.user.username,
        "message": f"{shop.name} opened successfully.",
    }


@transaction.atomic
def close_shop_day(*, shop: Shop, payload: dict):
    """Close the open shop day with closing balances and staff authorisation."""
    from employees.services import verify_active_employee_code

    from .models import ShopDaySession

    session = (
        ShopDaySession.objects.select_for_update()
        .filter(shop=shop, closed_at__isnull=True)
        .first()
    )
    if session is None:
        raise ValidationError("This shop is not open.")

    login_code = (payload.get("login_code") or "").strip()
    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        raise ValidationError("Enter a valid active staff 6-digit ID.")

    from employees.module_permissions import ensure_employee_may

    ensure_employee_may(
        authorising,
        "my-shop",
        "open_close",
        message="You do not have permission to close this shop.",
    )

    if not _truthy(payload.get("stock_confirmed")):
        raise ValidationError("Confirm that stock is up to date before closing.")

    balances = _parse_balance_fields(payload)
    session.closing_cash = balances["cash"]
    session.closing_mpesa = balances["mpesa"]
    session.closing_credit = balances["credit"]
    session.stock_confirmed_close = True
    session.closed_by = authorising
    session.closed_at = timezone.now()
    session.save(
        update_fields=[
            "closing_cash",
            "closing_mpesa",
            "closing_credit",
            "stock_confirmed_close",
            "closed_by",
            "closed_at",
        ]
    )
    return {
        "session": session,
        "action": "close",
        "authorised_by": authorising.user.get_full_name()
        or authorising.user.username,
        "message": f"{shop.name} closed successfully.",
    }


# ---------------------------------------------------------------------------
# Shop expenses (outside purchases / operating costs)
# ---------------------------------------------------------------------------

_EXPENSE_VALID_DIALS = {country["dial"] for country in COUNTRY_DIAL_CODES}
_EXPENSE_ISO_BY_DIAL = {country["dial"]: country["iso"] for country in COUNTRY_DIAL_CODES}


def _expense_phone_digits(phone: str) -> str:
    return re.sub(r"\D+", "", phone or "")


def _expense_normalize_national_phone(phone: str, dial: str = "") -> str:
    digits = _expense_phone_digits(phone)
    cc = _expense_phone_digits(dial)
    if cc and digits.startswith(cc) and len(digits) > len(cc):
        digits = digits[len(cc) :]
    digits = digits.lstrip("0")
    return digits[:9]


def search_expense_suppliers(
    *,
    query: str,
    by: str = "name",
    dial: str = "",
    limit: int = 8,
    match: str = "contains",
):
    query = (query or "").strip().upper()
    by = (by or "name").strip().lower()
    match_mode = (match or "contains").strip().lower()

    qs = ExpenseSupplier.objects.all()
    if by == "phone":
        digits = _expense_normalize_national_phone(query, dial)
        last4_mode = match_mode in ("last4", "endswith", "suffix")
        min_digits = 1 if last4_mode else 3
        if len(digits) < min_digits:
            return []
        if last4_mode:
            digits = digits[-4:]
        dial = (dial or "").strip()
        if dial:
            qs = qs.filter(phone_country_code=dial)
        matches = []
        for supplier in qs.order_by("name", "phone_number")[:120]:
            phone_digits = _expense_phone_digits(supplier.phone_number)
            if last4_mode:
                if phone_digits.endswith(digits):
                    matches.append(supplier)
            elif digits in phone_digits:
                matches.append(supplier)
            if len(matches) >= limit:
                break
        return matches

    if len(query) < 2:
        return []
    return list(
        qs.filter(name__icontains=query).order_by("name", "phone_number")[:limit]
    )


def upsert_expense_supplier(
    *,
    name: str,
    dial: str,
    phone: str,
    iso: str = "",
    supplier_id=None,
):
    name = (name or "").strip().upper()
    dial = (dial or "").strip()
    phone = _expense_normalize_national_phone(phone, dial)
    iso = ((iso or "").strip().upper() or _EXPENSE_ISO_BY_DIAL.get(dial, "KE"))[:2]
    if not name or not dial or not phone:
        return None

    try:
        supplier_pk = int(supplier_id) if supplier_id not in (None, "") else None
    except (TypeError, ValueError):
        supplier_pk = None

    if supplier_pk:
        supplier = ExpenseSupplier.objects.filter(pk=supplier_pk).first()
        if supplier is not None:
            conflict = (
                ExpenseSupplier.objects.filter(
                    phone_country_code=dial, phone_number=phone
                )
                .exclude(pk=supplier.pk)
                .first()
            )
            if conflict is not None:
                conflict.name = name
                conflict.phone_country_iso = iso or "KE"
                conflict.save(update_fields=["name", "phone_country_iso", "updated_at"])
                return conflict
            supplier.name = name
            supplier.phone_country_code = dial
            supplier.phone_number = phone
            supplier.phone_country_iso = iso or "KE"
            supplier.save(
                update_fields=[
                    "name",
                    "phone_country_code",
                    "phone_number",
                    "phone_country_iso",
                    "updated_at",
                ]
            )
            return supplier

    supplier, _created = ExpenseSupplier.objects.update_or_create(
        phone_country_code=dial,
        phone_number=phone,
        defaults={
            "name": name,
            "phone_country_iso": iso or "KE",
        },
    )
    return supplier


def register_shop_expense(*, shop: Shop, profile, payload: dict) -> dict:
    """Validate expense payload, upsert expense supplier, and save the expense."""
    from employees.services import verify_active_employee_code

    if shop.is_suspended:
        raise ValidationError(f"Shop “{shop.name}” is suspended.")

    login_code = (payload.get("login_code") or "").strip()
    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        raise ValidationError("Enter a valid active staff 6-digit ID.")

    from employees.module_permissions import ensure_employee_may

    ensure_employee_may(
        authorising,
        "my-shop",
        "register_expense",
        message="You do not have permission to register expenses.",
    )

    category = (payload.get("category") or "").strip().lower()
    if category not in {choice.value for choice in ExpenseCategory}:
        raise ValidationError("Choose an expense category.")

    name = (payload.get("name") or "").strip().upper()
    if not name:
        raise ValidationError("Expense name is required.")
    if len(name) > 200:
        raise ValidationError("Expense name is too long.")

    amount_raw = payload.get("amount")
    try:
        amount = _money(amount_raw)
    except ValidationError:
        raise ValidationError("Enter a valid expense amount.")
    if amount <= 0:
        raise ValidationError("Expense amount must be greater than zero.")

    payment_status = (payload.get("payment_status") or "").strip().lower()
    if payment_status not in {choice.value for choice in ExpensePaymentStatus}:
        raise ValidationError("Choose a payment status (unpaid, paid, or partial).")

    dial = (payload.get("supplier_phone_country_code") or "+254").strip()
    phone = _expense_normalize_national_phone(
        payload.get("supplier_phone_number") or "", dial
    )
    supplier_name = (payload.get("supplier_name") or "").strip().upper()
    supplier_iso = (payload.get("supplier_phone_country_iso") or "").strip().upper()
    supplier_id = payload.get("supplier_id") or ""

    if dial not in _EXPENSE_VALID_DIALS:
        raise ValidationError("Select a valid supplier country code.")
    if not supplier_name:
        raise ValidationError("Supplier name is required.")
    if not phone or len(phone) != 9 or not phone.isdigit():
        raise ValidationError("Enter a valid 9-digit supplier phone number.")

    with transaction.atomic():
        supplier = upsert_expense_supplier(
            name=supplier_name,
            dial=dial,
            phone=phone,
            iso=supplier_iso,
            supplier_id=supplier_id,
        )
        expense = Expense.objects.create(
            shop=shop,
            category=category,
            name=name,
            amount=amount,
            amount_paid=(
                amount
                if payment_status == ExpensePaymentStatus.PAID
                else _money(0)
            ),
            payment_status=payment_status,
            supplier=supplier,
            supplier_name=supplier_name,
            supplier_phone_country_code=dial,
            supplier_phone_number=phone,
            created_by=authorising,
        )

    return {
        "expense": expense,
        "authorised_by": authorising.user.get_full_name()
        or authorising.user.username,
        "message": f"Expense “{expense.name}” recorded for {shop.name}.",
    }


def _parse_iso_date(value, *, field_label: str):
    from datetime import datetime

    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError(f"Enter a valid {field_label} (YYYY-MM-DD).") from exc


def _shop_receipt_date_bounds(
    *,
    filter_mode: str,
    day: str = "",
    date_from: str = "",
    date_to: str = "",
    month: str = "",
    year: str = "",
):
    """Resolve inclusive local-date bounds for receipt filters."""
    from calendar import monthrange
    from datetime import date, datetime

    mode = (filter_mode or "day").strip().lower()
    today = timezone.localdate()

    if mode == "day":
        target = _parse_iso_date(day, field_label="date") or today
        return target, target

    if mode == "period":
        start = _parse_iso_date(date_from, field_label="from date")
        end = _parse_iso_date(date_to, field_label="to date")
        if start is None and end is None:
            return today, today
        if start is None:
            start = end
        if end is None:
            end = start
        if start > end:
            raise ValidationError("From date must be on or before to date.")
        return start, end

    if mode == "month":
        raw = (month or "").strip()
        if not raw:
            raw = today.strftime("%Y-%m")
        try:
            year_n, month_n = [int(part) for part in raw.split("-", 1)]
            if month_n < 1 or month_n > 12:
                raise ValueError
        except ValueError as exc:
            raise ValidationError("Enter a valid month (YYYY-MM).") from exc
        start = date(year_n, month_n, 1)
        end = date(year_n, month_n, monthrange(year_n, month_n)[1])
        return start, end

    if mode == "year":
        raw = (year or "").strip()
        if not raw:
            year_n = today.year
        else:
            try:
                year_n = int(raw)
            except ValueError as exc:
                raise ValidationError("Enter a valid year.") from exc
        if year_n < 2000 or year_n > today.year + 1:
            raise ValidationError("Enter a valid year.")
        return date(year_n, 1, 1), date(year_n, 12, 31)

    raise ValidationError("Choose day, period, month, or year.")


def _receipt_list_item(receipt) -> dict:
    created = timezone.localtime(receipt.created_at)
    cashier = ""
    if receipt.created_by_id and receipt.created_by:
        user = receipt.created_by.user
        cashier = (
            user.get_full_name()
            or getattr(receipt.created_by, "employee_id", "")
            or user.username
            or ""
        )
    return {
        "id": receipt.pk,
        "receipt_number": receipt.receipt_number,
        "kind": receipt.kind,
        "kind_label": receipt.get_kind_display(),
        "status": receipt.status,
        "status_label": receipt.get_status_display(),
        "payment_method": receipt.payment_method,
        "payment_label": receipt.get_payment_method_display(),
        "client_name": receipt.client_name or "",
        "client_phone": receipt.client_phone or "",
        "total": str(receipt.total),
        "created_at": created.isoformat(),
        "created_label": created.strftime("%d %b %Y · %H:%M"),
        "cashier": cashier,
        "can_return": (
            receipt.kind in {ShopReceiptKind.SALE, ShopReceiptKind.CREDIT}
            and receipt.status != ShopReceiptStatus.CANCELLED
        ),
    }


def _receipt_line_payload(line, *, sold_serials_by_item=None) -> dict:
    remaining = line.remaining_quantity
    remaining_serials = line.remaining_serial_numbers
    if sold_serials_by_item is not None and (line.serial_numbers or remaining_serials):
        allowed = sold_serials_by_item.get(line.item_id) or set()
        remaining_serials = [s for s in remaining_serials if s in allowed]
    return {
        "id": line.pk,
        "item_id": line.item_id,
        "item_name": line.item_name,
        "quantity": int(line.quantity),
        "returned_quantity": int(line.returned_quantity or 0),
        "remaining_quantity": remaining,
        "unit_price": str(line.unit_price),
        "line_total": str(line.line_total),
        "remaining_total": str(
            (Decimal(line.unit_price or 0) * remaining).quantize(Decimal("0.01"))
        ),
        "serial_numbers": list(line.serial_numbers or []),
        "returned_serial_numbers": list(line.returned_serial_numbers or []),
        "remaining_serial_numbers": remaining_serials,
        "track_serial": bool(remaining_serials or line.serial_numbers),
    }


def _sold_serials_by_item_for_lines(lines) -> dict:
    """item_id → serials that exist and are sold (eligible to return)."""
    from items.models import ItemSerial

    item_ids = set()
    serials = set()
    for line in lines:
        if not line.item_id:
            continue
        for serial in line.remaining_serial_numbers:
            item_ids.add(line.item_id)
            serials.add(serial)
    if not item_ids or not serials:
        return {}
    rows = ItemSerial.objects.filter(
        item_id__in=item_ids,
        serial_number__in=serials,
        is_available=False,
    ).values_list("item_id", "serial_number")
    out = {}
    for item_id, serial in rows:
        out.setdefault(item_id, set()).add(serial)
    return out


def list_shop_receipts(
    *,
    shop: Shop,
    query: str = "",
    filter_mode: str = "day",
    day: str = "",
    date_from: str = "",
    date_to: str = "",
    month: str = "",
    year: str = "",
    limit: int = 200,
) -> dict:
    """List shop receipts with live search and date filters."""
    from datetime import datetime, time

    from django.db.models import Q

    from .models import ShopReceipt

    start_date, end_date = _shop_receipt_date_bounds(
        filter_mode=filter_mode,
        day=day,
        date_from=date_from,
        date_to=date_to,
        month=month,
        year=year,
    )
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)

    try:
        limit_n = max(1, min(int(limit or 200), 500))
    except (TypeError, ValueError):
        limit_n = 200

    qs = (
        ShopReceipt.objects.filter(
            shop=shop,
            created_at__gte=start_dt,
            created_at__lte=end_dt,
        )
        .select_related("created_by__user")
        .order_by("-created_at", "-id")
    )

    q = (query or "").strip()
    if q:
        qs = qs.filter(
            Q(receipt_number__icontains=q)
            | Q(client_name__icontains=q)
            | Q(client_phone__icontains=q)
            | Q(kind__icontains=q)
        )

    total_count = qs.count()
    receipts = list(qs[:limit_n])
    return {
        "ok": True,
        "count": total_count,
        "returned": len(receipts),
        "filter_mode": (filter_mode or "day").strip().lower(),
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "receipts": [_receipt_list_item(row) for row in receipts],
    }


def get_shop_receipt_detail(*, shop: Shop, receipt_id: int) -> dict:
    """Full receipt payload for the receipts modal (details + reprint text)."""
    from .models import ShopReceipt

    try:
        receipt = (
            ShopReceipt.objects.select_related("created_by__user", "client", "shop")
            .prefetch_related("lines")
            .get(pk=receipt_id, shop=shop)
        )
    except ShopReceipt.DoesNotExist as exc:
        raise ValidationError("Receipt not found for this shop.") from exc

    lines = list(receipt.lines.all())
    sold_serials_by_item = _sold_serials_by_item_for_lines(lines)
    pos_settings = get_company_pos_settings()
    ticket = _build_receipt_ticket_data(receipt, lines)
    message = _render_receipt_text(ticket)
    item = _receipt_list_item(receipt)
    line_payloads = [
        _receipt_line_payload(line, sold_serials_by_item=sold_serials_by_item)
        for line in lines
    ]
    returnable_lines = []
    for line, payload in zip(lines, line_payloads):
        if line.remaining_quantity <= 0:
            continue
        if receipt.kind not in {ShopReceiptKind.SALE, ShopReceiptKind.CREDIT}:
            continue
        if receipt.status == ShopReceiptStatus.CANCELLED:
            continue
        # Serial sale lines are only returnable when a sold serial still exists.
        if line.serial_numbers and not payload["remaining_serial_numbers"]:
            continue
        returnable_lines.append(payload)
    return {
        "ok": True,
        "receipt": {
            **item,
            "subtotal": str(receipt.subtotal),
            "tax_percent": str(receipt.tax_percent),
            "tax_amount": str(receipt.tax_amount),
            "cash_amount": str(receipt.cash_amount),
            "mpesa_amount": str(receipt.mpesa_amount),
            "lines": line_payloads,
            "returnable_lines": returnable_lines,
        },
        "receipt_text": message,
        "receipt_ticket": ticket,
        "receipt_qr": receipt_qr_for_receipt(receipt, pos_settings),
        "receipt_font": receipt_font_style(pos_settings),
        "receipt_paper_width": (
            pos_settings.receipt_paper_width
            if pos_settings.receipt_paper_width in RECEIPT_PAPER_WIDTHS
            else "80"
        ),
    }


@transaction.atomic
def return_shop_receipt_items(*, shop: Shop, receipt_id: int, payload: dict) -> dict:
    """
    Return one or more lines from a sale/credit receipt.

    Restocks shop + global inventory, reactivates returned serials, and
    recalculates (or cancels) the receipt totals.
    """
    from employees.services import verify_active_employee_code
    from items.models import Item, ItemSerial, ShopStock
    from items.services import _normalize_serial_list, apply_stock_in_average_cost

    from .models import ShopReceipt, ShopReceiptLine

    login_code = (payload.get("login_code") or "").strip()
    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        raise ValidationError("Enter a valid active staff 6-digit ID.")

    from employees.module_permissions import ensure_employee_may

    ensure_employee_may(
        authorising,
        "my-shop",
        "return_receipt",
        message="You do not have permission to return receipts.",
    )

    try:
        receipt = (
            ShopReceipt.objects.select_for_update()
            .select_related("shop")
            .prefetch_related("lines")
            .get(pk=receipt_id, shop=shop)
        )
    except ShopReceipt.DoesNotExist as exc:
        raise ValidationError("Receipt not found for this shop.") from exc

    if receipt.kind == ShopReceiptKind.QUOTATION:
        raise ValidationError("Quotations cannot be returned.")
    if receipt.status == ShopReceiptStatus.CANCELLED:
        raise ValidationError("This receipt is already fully returned.")

    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationError("Select at least one item to return.")

    lines_by_id = {line.pk: line for line in receipt.lines.all()}
    prepared = []
    errors = []
    item_ids = set()

    for raw in raw_lines:
        try:
            line_id = int(raw.get("line_id") or raw.get("id") or 0)
        except (TypeError, ValueError):
            line_id = 0
        try:
            qty = int(raw.get("qty") or raw.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        serials = _normalize_serial_list(
            raw.get("serials") or raw.get("serial_numbers") or []
        )
        if serials and qty <= 0:
            qty = len(serials)
        elif serials and qty != len(serials):
            errors.append("Serial-tracked returns need one serial per unit.")
            continue
        if line_id <= 0 or qty <= 0:
            errors.append("Each return line needs a valid item and quantity.")
            continue
        line = lines_by_id.get(line_id)
        if line is None:
            errors.append(f"Receipt line #{line_id} was not found.")
            continue
        remaining = line.remaining_quantity
        if qty > remaining:
            errors.append(
                f"“{line.item_name}”: only {remaining} unit(s) left to return."
            )
            continue

        remaining_serials = line.remaining_serial_numbers
        if remaining_serials:
            if not serials:
                errors.append(f"“{line.item_name}” requires serial numbers to return.")
                continue
            if len(serials) != qty:
                errors.append(
                    f"“{line.item_name}”: return quantity must match serial count."
                )
                continue
            missing = [s for s in serials if s not in remaining_serials]
            if missing:
                errors.append(
                    f"“{line.item_name}”: serial not on this receipt "
                    f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})."
                )
                continue
        elif serials:
            serials = []

        serial_objects = {}
        if serials:
            if not line.item_id:
                errors.append(
                    f"“{line.item_name}”: cannot verify serial numbers for this line."
                )
                continue
            found = {
                row.serial_number: row
                for row in ItemSerial.objects.select_for_update().filter(
                    item_id=line.item_id,
                    serial_number__in=serials,
                )
            }
            not_in_system = [s for s in serials if s not in found]
            if not_in_system:
                errors.append(
                    f"“{line.item_name}”: serial not in the system "
                    f"({', '.join(not_in_system[:5])}"
                    f"{'…' if len(not_in_system) > 5 else ''})."
                )
                continue
            already_in_stock = [s for s in serials if found[s].is_available]
            if already_in_stock:
                errors.append(
                    f"“{line.item_name}”: serial already in stock (not sold) "
                    f"({', '.join(already_in_stock[:5])}"
                    f"{'…' if len(already_in_stock) > 5 else ''}). Cannot return."
                )
                continue
            serial_objects = found

        if line.item_id:
            item_ids.add(line.item_id)
        prepared.append(
            {
                "line": line,
                "qty": qty,
                "serial_numbers": serials,
                "serial_objects": serial_objects,
            }
        )

    if errors:
        raise ValidationError(errors)
    if not prepared:
        raise ValidationError("Select at least one valid item to return.")

    items_by_id = Item.objects.select_for_update().filter(pk__in=item_ids).in_bulk()
    stock_by_item = {
        row.item_id: row
        for row in ShopStock.objects.select_for_update().filter(
            shop=shop, item_id__in=item_ids
        )
    }
    missing_stock_ids = [
        item_id for item_id in item_ids if item_id not in stock_by_item
    ]
    if missing_stock_ids:
        ShopStock.objects.bulk_create(
            [
                ShopStock(shop=shop, item_id=item_id, quantity=0)
                for item_id in missing_stock_ids
            ],
            ignore_conflicts=True,
        )
        for row in ShopStock.objects.select_for_update().filter(
            shop=shop, item_id__in=missing_stock_ids
        ):
            stock_by_item[row.item_id] = row

    now = timezone.now()
    stocks_to_update = []
    items_to_update = []
    serials_to_update = []
    lines_to_update = []
    stock_updates = []

    for row in prepared:
        line = row["line"]
        qty = row["qty"]
        serials = row["serial_numbers"]
        line.returned_quantity = int(line.returned_quantity or 0) + qty
        if serials:
            existing_returned = [
                str(s).strip()
                for s in (line.returned_serial_numbers or [])
                if str(s).strip()
            ]
            line.returned_serial_numbers = existing_returned + serials
        remaining_after = max(0, int(line.quantity) - int(line.returned_quantity))
        line.line_total = (
            Decimal(line.unit_price or 0) * remaining_after
        ).quantize(Decimal("0.01"))
        lines_to_update.append(line)

        item = items_by_id.get(line.item_id) if line.item_id else None
        if item is None:
            continue
        stock = stock_by_item.get(item.pk)
        if stock is None:
            continue
        return_unit_cost = Decimal(line.unit_cost or 0)
        apply_stock_in_average_cost(
            stock,
            qty=qty,
            unit_cost=return_unit_cost,
        )
        stock.quantity += qty
        stock.updated_at = now
        item.stock = int(item.stock or 0) + qty
        item.updated_at = now
        stocks_to_update.append(stock)
        items_to_update.append(item)
        stock_updates.append({"id": item.pk, "quantity": int(stock.quantity)})

        if serials:
            found = row.get("serial_objects") or {}
            for serial_no in serials:
                obj = found.get(serial_no)
                if obj is None:
                    raise ValidationError(
                        f"“{line.item_name}”: serial not in the system ({serial_no})."
                    )
                if obj.is_available:
                    raise ValidationError(
                        f"“{line.item_name}”: serial already in stock ({serial_no}). "
                        "Cannot return."
                    )
                obj.is_available = True
                obj.shop = shop
                obj.updated_at = now
                serials_to_update.append(obj)

    ShopReceiptLine.objects.bulk_update(
        lines_to_update,
        ["returned_quantity", "returned_serial_numbers", "line_total"],
    )
    if stocks_to_update:
        ShopStock.objects.bulk_update(
            stocks_to_update, ["quantity", "average_cost", "updated_at"]
        )
    if items_to_update:
        Item.objects.bulk_update(items_to_update, ["stock", "updated_at"])
    if serials_to_update:
        ItemSerial.objects.bulk_update(
            serials_to_update, ["is_available", "shop", "updated_at"]
        )

    # Refresh remaining totals from all lines on this receipt.
    all_lines = list(ShopReceiptLine.objects.filter(receipt=receipt).order_by("id"))
    remaining_subtotal = sum(
        (
            (Decimal(line.unit_price or 0) * line.remaining_quantity).quantize(
                Decimal("0.01")
            )
            for line in all_lines
        ),
        Decimal("0.00"),
    )

    # Preserve the original tax rate stored on the receipt.
    tax_percent = Decimal(receipt.tax_percent or 0)
    if remaining_subtotal <= 0:
        tax_amount = Decimal("0.00")
        total = Decimal("0.00")
        cash_amount = Decimal("0.00")
        mpesa_amount = Decimal("0.00")
        status = ShopReceiptStatus.CANCELLED
    else:
        tax_amount = (remaining_subtotal * tax_percent / Decimal("100")).quantize(
            Decimal("0.01")
        )
        total = (remaining_subtotal + tax_amount).quantize(Decimal("0.01"))
        any_returned = any(int(line.returned_quantity or 0) > 0 for line in all_lines)
        status = (
            ShopReceiptStatus.PARTIAL_RETURN
            if any_returned
            else ShopReceiptStatus.ACTIVE
        )
        cash_amount = receipt.cash_amount
        mpesa_amount = receipt.mpesa_amount
        if receipt.kind == ShopReceiptKind.SALE:
            if receipt.payment_method == ShopPaymentMethod.CASH:
                cash_amount = total
                mpesa_amount = Decimal("0.00")
            elif receipt.payment_method == ShopPaymentMethod.MPESA:
                mpesa_amount = total
                cash_amount = Decimal("0.00")
            elif receipt.payment_method == ShopPaymentMethod.BOTH:
                old_total = Decimal(receipt.total or 0)
                if old_total > 0:
                    ratio = total / old_total
                    cash_amount = (Decimal(receipt.cash_amount or 0) * ratio).quantize(
                        Decimal("0.01")
                    )
                    mpesa_amount = (total - cash_amount).quantize(Decimal("0.01"))
                else:
                    cash_amount = Decimal("0.00")
                    mpesa_amount = total
            else:
                cash_amount = Decimal("0.00")
                mpesa_amount = Decimal("0.00")
        else:
            cash_amount = Decimal("0.00")
            mpesa_amount = Decimal("0.00")

    receipt.subtotal = remaining_subtotal
    receipt.tax_amount = tax_amount
    receipt.total = total
    receipt.cash_amount = cash_amount
    receipt.mpesa_amount = mpesa_amount
    receipt.status = status
    receipt.last_returned_at = now
    receipt.last_returned_by = authorising
    receipt.save(
        update_fields=[
            "subtotal",
            "tax_amount",
            "total",
            "cash_amount",
            "mpesa_amount",
            "status",
            "last_returned_at",
            "last_returned_by",
        ]
    )

    detail = get_shop_receipt_detail(shop=shop, receipt_id=receipt.pk)
    returned_units = sum(row["qty"] for row in prepared)
    if status == ShopReceiptStatus.CANCELLED:
        summary = (
            f"Receipt {receipt.receipt_number} fully returned and cancelled "
            f"({returned_units} unit(s) restocked)."
        )
    else:
        summary = (
            f"Returned {returned_units} unit(s) on {receipt.receipt_number}. "
            f"Remaining total KSh {total}."
        )

    if receipt.kind == ShopReceiptKind.CREDIT and receipt.client_id:
        from shops.credit_audit import log_credit_return

        log_credit_return(
            receipt=receipt,
            summary=summary,
            actor=authorising,
            occurred_at=now,
        )

    return {
        **detail,
        "ok": True,
        "message": summary,
        "authorised_by": authorising.user.get_full_name()
        or authorising.user.username,
        "stock_updates": stock_updates,
        "status": status,
        "status_label": receipt.get_status_display(),
        "total": str(total),
    }

