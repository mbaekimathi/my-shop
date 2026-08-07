"""Auto-detect local vs hosted settings so .env stays minimal."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def _env_flag(name: str) -> str | None:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw or raw in {"auto", "default"}:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return "true"
    if raw in {"0", "false", "no", "off"}:
        return "false"
    return raw


def detect_is_hosted(base_dir: Path) -> bool:
    """True when running under Passenger, Gunicorn, or an explicit production marker."""
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if app_env in {"local", "dev", "development"}:
        return False
    if app_env in {"prod", "production", "hosted", "cpanel", "vps"}:
        return True
    if (base_dir / ".production").exists():
        return True

    argv0 = (sys.argv[0] if sys.argv else "").lower().replace("\\", "/")
    if "runserver" in sys.argv or "test" in sys.argv:
        return False
    if "gunicorn" in argv0 or "uvicorn" in argv0:
        return True

    passenger_signals = (
        "PASSENGER_APP_ENV",
        "PASSENGER_BASE_URI",
        "PASSENGER_COMPILE_DIR",
        "IN_PASSENGER",
    )
    if any(os.getenv(key) for key in passenger_signals):
        return True

    server_software = (os.getenv("SERVER_SOFTWARE") or "").lower()
    if "passenger" in server_software or "apache" in server_software:
        return True

    return False


def detect_debug(base_dir: Path) -> bool:
    flagged = _env_flag("DJANGO_DEBUG")
    if flagged == "true":
        return True
    if flagged == "false":
        return False
    # Auto: local/dev True, hosted False.
    return not detect_is_hosted(base_dir)


def resolve_secret_key(base_dir: Path) -> str:
    """
    Prefer DJANGO_SECRET_KEY; otherwise reuse/create a persistent .secret_key file.
    Works the same on local and hosting without editing .env.
    """
    env_key = (os.getenv("DJANGO_SECRET_KEY") or "").strip()
    weak = {
        "",
        "change-me",
        "change-me-in-production",
        "django-insecure-j4yq22z^3zy!-3!ox^x(8d5$8g05b2vsbu*ydtd=zt*_p=ki1%",
    }
    if env_key and env_key not in weak and not env_key.startswith("django-insecure"):
        return env_key

    path = base_dir / ".secret_key"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    generated = secrets.token_urlsafe(48)
    try:
        path.write_text(generated + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        # Read-only filesystem — still boot with an ephemeral key.
        return generated
    return generated


def resolve_allowed_hosts(*, debug: bool) -> list[str]:
    """
    Auto hosts:
    - unset / auto / *  → ["*"] (works on any domain; fine behind cPanel/VPS)
    - otherwise comma-separated list
    Local also keeps localhost when a specific list is given.
    """
    raw = (os.getenv("DJANGO_ALLOWED_HOSTS") or "auto").strip()
    if not raw or raw.lower() in {"auto", "*", "any"}:
        hosts = ["*"]
    else:
        hosts = [h.strip() for h in raw.split(",") if h.strip()]

    if debug:
        for local in ("localhost", "127.0.0.1", "[::1]"):
            if local not in hosts and "*" not in hosts:
                hosts.append(local)
        for tunnel in (
            ".ngrok-free.app",
            ".ngrok-free.dev",
            ".ngrok.app",
            ".ngrok.io",
            ".loca.lt",
        ):
            if tunnel not in hosts and "*" not in hosts:
                hosts.append(tunnel)
    return hosts


def resolve_csrf_trusted_origins() -> list[str]:
    raw = (os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS") or "auto").strip()
    if not raw or raw.lower() in {"auto", "*"}:
        return []
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def resolve_bool(name: str, *, default: bool) -> bool:
    flagged = _env_flag(name)
    if flagged == "true":
        return True
    if flagged == "false":
        return False
    return default
