"""Local vs hosted deployment profile for STK / Daraja and messaging credentials."""

from __future__ import annotations

import copy
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shops.models import CompanyCommunicationsSettings, CompanyDarajaSettings

HOSTED_DARAJA_ENV = {
    "consumer_key": "DARAJA_CONSUMER_KEY",
    "consumer_secret": "DARAJA_CONSUMER_SECRET",
    "passkey": "DARAJA_PASSKEY",
    "shortcode": "DARAJA_SHORTCODE",
    "environment": "DARAJA_ENVIRONMENT",
    "stk_provider": "DARAJA_STK_PROVIDER",
    "nexus_api_key": "NEXUS_API_KEY",
    "nexus_collection_id": "NEXUS_COLLECTION_ID",
}

HOSTED_DARAJA_SECRET_FIELDS = (
    "consumer_key",
    "consumer_secret",
    "passkey",
    "shortcode",
    "nexus_api_key",
    "nexus_collection_id",
)

HOSTED_TWILIO_ENV = {
    "twilio_account_sid": "TWILIO_ACCOUNT_SID",
    "twilio_auth_token": "TWILIO_AUTH_TOKEN",
    "twilio_from_number": "TWILIO_FROM_NUMBER",
    "twilio_whatsapp_from": "TWILIO_WHATSAPP_FROM",
    "twilio_whatsapp_join_code": "TWILIO_WHATSAPP_JOIN_CODE",
}

HOSTED_SMS_ENV = {
    "sms_provider": "SMS_PROVIDER",
    "sms_api_key": "SMS_API_KEY",
    "sms_api_secret": "SMS_API_SECRET",
    "sms_sender_id": "SMS_SENDER_ID",
    "sms_api_base_url": "SMS_API_BASE_URL",
}

HOSTED_MESSAGE_ENV = {
    "message_from_name": "MESSAGE_FROM_NAME",
    "message_reply_to": "MESSAGE_REPLY_TO",
}

HOSTED_COMMS_CREDENTIAL_FIELDS = (
    *HOSTED_TWILIO_ENV.keys(),
    *HOSTED_SMS_ENV.keys(),
    *HOSTED_MESSAGE_ENV.keys(),
)


def current_daraja_deploy_profile() -> str:
    from django.conf import settings

    from shops.models import DarajaDeployProfile

    if getattr(settings, "IS_HOSTED", False):
        return DarajaDeployProfile.HOSTED
    return DarajaDeployProfile.LOCAL


def is_hosted_deploy() -> bool:
    from shops.models import DarajaDeployProfile

    return current_daraja_deploy_profile() == DarajaDeployProfile.HOSTED


def hosted_daraja_credentials_locked() -> bool:
    """On hosted, Daraja secrets are never saved via the UI — use server .env only."""
    return is_hosted_deploy()


def hosted_comms_secrets_locked() -> bool:
    """On hosted, Twilio / SMS / Message credentials come from server .env only."""
    return is_hosted_deploy()


def hosted_stk_enable_locked() -> bool:
    if not is_hosted_deploy():
        return False
    return "enable_stk_push" in hosted_env_overrides()


def hosted_nexus_credentials_locked() -> bool:
    return is_hosted_deploy()


def hosted_env_overrides() -> dict[str, str]:
    """Non-empty Daraja/Nexus values from the server .env (hosted only)."""
    if not is_hosted_deploy():
        return {}
    out: dict[str, str] = {}
    for field, env_name in HOSTED_DARAJA_ENV.items():
        value = (os.getenv(env_name) or "").strip()
        if value:
            out[field] = value
    enable = (os.getenv("DARAJA_ENABLE_STK_PUSH") or "").strip().lower()
    if enable in ("1", "true", "yes", "on"):
        out["enable_stk_push"] = "1"
    elif enable in ("0", "false", "no", "off"):
        out["enable_stk_push"] = "0"
    return out


def hosted_comms_env_overrides() -> dict[str, str]:
    """Non-empty Twilio / SMS / Message values from server .env (hosted only)."""
    if not is_hosted_deploy():
        return {}
    out: dict[str, str] = {}
    for mapping in (HOSTED_TWILIO_ENV, HOSTED_SMS_ENV, HOSTED_MESSAGE_ENV):
        for field, env_name in mapping.items():
            value = (os.getenv(env_name) or "").strip()
            if value:
                out[field] = value
    provider = (out.get("sms_provider") or "").strip().lower()
    if provider:
        from shops.models import SmsProvider

        allowed = {choice.value for choice in SmsProvider}
        if provider in allowed:
            out["sms_provider"] = provider
        elif provider == "twilio":
            out["sms_provider"] = SmsProvider.TWILIO
        elif provider in {"africas_talking", "africastalking", "at"}:
            out["sms_provider"] = SmsProvider.AFRICAS_TALKING
    return out


def comms_deploy_meta() -> dict:
    overrides = hosted_comms_env_overrides()
    locked = hosted_comms_secrets_locked()
    return {
        "deploy_profile_label": deploy_profile_label(),
        "is_hosted_deploy": is_hosted_deploy(),
        "credentials_from_env": locked,
        "twilio_credentials_from_env": locked,
        "sms_credentials_from_env": locked,
        "message_credentials_from_env": locked,
        "hosted_env_keys": sorted(overrides.keys()),
        "hosted_twilio_env_keys": sorted(
            k for k in overrides if k in HOSTED_TWILIO_ENV
        ),
        "hosted_sms_env_keys": sorted(k for k in overrides if k in HOSTED_SMS_ENV),
        "hosted_message_env_keys": sorted(
            k for k in overrides if k in HOSTED_MESSAGE_ENV
        ),
    }


def clone_daraja_row(row: CompanyDarajaSettings) -> CompanyDarajaSettings:
    clone = copy.copy(row)
    clone.pk = row.pk
    clone._state.adding = False  # noqa: SLF001 — treat as persisted for reads
    return clone


def clone_comms_row(row: CompanyCommunicationsSettings) -> CompanyCommunicationsSettings:
    clone = copy.copy(row)
    clone.pk = row.pk
    clone._state.adding = False  # noqa: SLF001
    return clone


def apply_hosted_env_overlays(row: CompanyDarajaSettings) -> CompanyDarajaSettings:
    """Hosted: Daraja/Nexus secrets from .env only (DB secrets are not used)."""
    if not is_hosted_deploy():
        return row
    effective = clone_daraja_row(row)
    for field in HOSTED_DARAJA_SECRET_FIELDS:
        setattr(effective, field, "")
    effective.credentials_valid = False
    effective.nexus_key_verified = False

    overrides = hosted_env_overrides()
    for field, value in overrides.items():
        if field == "enable_stk_push":
            effective.enable_stk_push = value == "1"
            continue
        if field == "environment":
            env = value.strip().lower()
            if env in ("production", "prod", "live"):
                effective.environment = "production"
            elif env in ("sandbox", "sand", "test"):
                effective.environment = "sandbox"
            continue
        if field == "stk_provider":
            p = value.strip().lower()
            if p in ("nexus", "nexus_rushtech"):
                effective.stk_provider = "nexus"
            elif p == "daraja":
                effective.stk_provider = "daraja"
            continue
        setattr(effective, field, value)

    if overrides.get("nexus_api_key"):
        effective.nexus_key_verified = (os.getenv("NEXUS_KEY_VERIFIED") or "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    daraja_fields = ("consumer_key", "consumer_secret", "passkey", "shortcode")
    if all((getattr(effective, name) or "").strip() for name in daraja_fields):
        if (os.getenv("DARAJA_CREDENTIALS_VALID") or "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            effective.credentials_valid = True
    return effective


def apply_hosted_comms_env_overlays(
    row: CompanyCommunicationsSettings,
) -> CompanyCommunicationsSettings:
    """Hosted: Twilio / SMS / Message credentials from .env only (not DB)."""
    if not is_hosted_deploy():
        return row
    effective = clone_comms_row(row)
    for field in HOSTED_COMMS_CREDENTIAL_FIELDS:
        setattr(effective, field, "")
    for field, value in hosted_comms_env_overrides().items():
        setattr(effective, field, value)
    return effective


def deploy_profile_label() -> str:
    return "Hosted" if is_hosted_deploy() else "Local"
