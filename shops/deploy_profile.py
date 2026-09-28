"""Local vs hosted deployment profile for STK / Daraja settings."""

from __future__ import annotations

import copy
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shops.models import CompanyDarajaSettings

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
    """True when hosted and all Daraja secret fields come from .env."""
    if not is_hosted_deploy():
        return False
    overrides = hosted_env_overrides()
    required = ("consumer_key", "consumer_secret", "passkey", "shortcode")
    return all((overrides.get(name) or "").strip() for name in required)


def hosted_stk_enable_locked() -> bool:
    if not is_hosted_deploy():
        return False
    return "enable_stk_push" in hosted_env_overrides()


def hosted_nexus_credentials_locked() -> bool:
    if not is_hosted_deploy():
        return False
    return bool((hosted_env_overrides().get("nexus_api_key") or "").strip())


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


def clone_daraja_row(row: CompanyDarajaSettings) -> CompanyDarajaSettings:
    clone = copy.copy(row)
    clone.pk = row.pk
    clone._state.adding = False  # noqa: SLF001 — treat as persisted for reads
    return clone


def apply_hosted_env_overlays(row: CompanyDarajaSettings) -> CompanyDarajaSettings:
    """Overlay hosted .env credentials onto a DB row (in-memory only)."""
    if not is_hosted_deploy():
        return row
    overrides = hosted_env_overrides()
    if not overrides:
        return row
    effective = clone_daraja_row(row)
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


def deploy_profile_label() -> str:
    return "Hosted" if is_hosted_deploy() else "Local"
