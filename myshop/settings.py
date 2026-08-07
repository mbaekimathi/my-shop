"""
Django settings for MY-SHOP employee portal.
"""

import os
import sys
import warnings
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

_DEFAULT_INSECURE_KEY = (
    "django-insecure-j4yq22z^3zy!-3!ox^x(8d5$8g05b2vsbu*ydtd=zt*_p=ki1%"
)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _DEFAULT_INSECURE_KEY)

DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("1", "true", "yes")

if not DEBUG:
    if (
        not SECRET_KEY
        or SECRET_KEY == _DEFAULT_INSECURE_KEY
        or SECRET_KEY.startswith("django-insecure")
        or SECRET_KEY.strip().lower() in {"change-me", "change-me-in-production"}
    ):
        raise ImproperlyConfigured(
            "Set a strong DJANGO_SECRET_KEY before running with DJANGO_DEBUG=False."
        )

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

# Dev-only tunnel hosts (ngrok, etc.). Never auto-added in production.
if DEBUG:
    _TUNNEL_ALLOWED_HOSTS = (
        ".ngrok-free.app",
        ".ngrok-free.dev",
        ".ngrok.app",
        ".ngrok.io",
        ".loca.lt",
    )
    for _tunnel_host in _TUNNEL_ALLOWED_HOSTS:
        if _tunnel_host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_tunnel_host)

# Trust proxy headers (cPanel / nginx / Cloudflare / ngrok).
USE_X_FORWARDED_HOST = os.getenv("DJANGO_USE_X_FORWARDED_HOST", "True").lower() in (
    "1",
    "true",
    "yes",
)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# Optional override; otherwise Daraja auto-picks browser URL or local ngrok tunnel.
DARAJA_CALLBACK_BASE_URL = os.getenv("DARAJA_CALLBACK_BASE_URL", "").strip()
DARAJA_NGROK_API_URL = os.getenv(
    "DARAJA_NGROK_API_URL", "http://127.0.0.1:4040/api/tunnels"
).strip()

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "employees",
    "items",
    "shops",
    "pos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.NoCacheHtmlMiddleware",
]

ROOT_URLCONF = "myshop.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "employees.context_processors.employee_workspace",
            ],
        },
    },
]

WSGI_APPLICATION = "myshop.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

MYSQL_ENABLED = os.getenv("MYSQL_ENABLED", "False").lower() in ("1", "true", "yes")

if MYSQL_ENABLED:
    DATABASES = {
        "default": {
            # Custom backend: supports MariaDB 10.4+ (XAMPP) — Django default requires 10.11+
            "ENGINE": "myshop.db_backends.mysql",
            "NAME": os.getenv("MYSQL_DATABASE", "myshop"),
            "USER": os.getenv("MYSQL_USER", "root"),
            "PASSWORD": os.getenv("MYSQL_PASSWORD", ""),
            "HOST": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.getenv("MYSQL_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
                "connect_timeout": 5,
            },
            "CONN_MAX_AGE": int(
                os.getenv("MYSQL_CONN_MAX_AGE", "60" if not DEBUG else "300")
            ),
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
    if not DEBUG and "test" not in sys.argv:
        warnings.warn(
            "SQLite is not suitable for production multi-user workloads. "
            "Set MYSQL_ENABLED=True in your environment.",
            stacklevel=1,
        )

# ---------------------------------------------------------------------------
# Cache & sessions (Redis when available, LocMem fallback for local dev)
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_ENABLED = bool(REDIS_URL)

if REDIS_ENABLED:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": 5,
                "SOCKET_TIMEOUT": 5,
                "IGNORE_EXCEPTIONS": True,
            },
            "TIMEOUT": int(os.getenv("CACHE_TIMEOUT", "300")),
        }
    }
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "myshop-cache",
            "TIMEOUT": int(os.getenv("CACHE_TIMEOUT", "300")),
        }
    }
    SESSION_ENGINE = "django.contrib.sessions.backends.db"

SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(60 * 60 * 24 * 14)))  # 14 days
SESSION_SAVE_EVERY_REQUEST = False

# ---------------------------------------------------------------------------
# Rate limiting (per client IP, enforced via cache)
# ---------------------------------------------------------------------------

RATE_LIMITS = {
    "login": {
        "max": int(os.getenv("RATE_LIMIT_LOGIN_MAX", "10")),
        "window": int(os.getenv("RATE_LIMIT_LOGIN_WINDOW", "60")),
    },
    "check_employee_id": {
        "max": int(os.getenv("RATE_LIMIT_CHECK_ID_MAX", "30")),
        "window": int(os.getenv("RATE_LIMIT_CHECK_ID_WINDOW", "60")),
    },
    "register": {
        "max": int(os.getenv("RATE_LIMIT_REGISTER_MAX", "5")),
        "window": int(os.getenv("RATE_LIMIT_REGISTER_WINDOW", "300")),
    },
    "sync": {
        "max": int(os.getenv("RATE_LIMIT_SYNC_MAX", "30")),
        "window": int(os.getenv("RATE_LIMIT_SYNC_WINDOW", "60")),
    },
    "pos_sale": {
        "max": int(os.getenv("RATE_LIMIT_POS_SALE_MAX", "120")),
        "window": int(os.getenv("RATE_LIMIT_POS_SALE_WINDOW", "60")),
    },
}

# ---------------------------------------------------------------------------
# Pagination & list sizes
# ---------------------------------------------------------------------------

EMPLOYEE_LIST_PAGE_SIZE = int(os.getenv("EMPLOYEE_LIST_PAGE_SIZE", "25"))

# ---------------------------------------------------------------------------
# Celery (optional — requires Redis)
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL or "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Africa/Nairobi"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "300"))

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "employees:login"
LOGIN_REDIRECT_URL = "employees:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Serve uploaded files in production when no object storage is configured
# (typical for cPanel shared hosting — set True there).
SERVE_MEDIA_IN_PRODUCTION = os.getenv("SERVE_MEDIA_IN_PRODUCTION", "False").lower() in (
    "1",
    "true",
    "yes",
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO" if not DEBUG else "DEBUG")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# Production hardening
# ---------------------------------------------------------------------------

if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() in (
        "1",
        "true",
        "yes",
    )
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "True").lower() in (
        "1",
        "true",
        "yes",
    )
    CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "True").lower() in (
        "1",
        "true",
        "yes",
    )
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", "True"
    ).lower() in ("1", "true", "yes")
    SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "False").lower() in (
        "1",
        "true",
        "yes",
    )
    if not CSRF_TRUSTED_ORIGINS:
        warnings.warn(
            "DJANGO_CSRF_TRUSTED_ORIGINS is empty. Set https://your-domain "
            "for POST forms behind HTTPS.",
            stacklevel=1,
        )
    if not REDIS_URL:
        warnings.warn(
            "REDIS_URL is unset. LocMem cache is fine for a single cPanel app; "
            "use Redis on a multi-worker VPS.",
            stacklevel=1,
        )
