"""
Django settings for MY-SHOP employee portal.

Most local vs hosted values auto-detect so .env only needs DB credentials
(and optional overrides).
"""

import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

from myshop.auto_env import (
    detect_debug,
    detect_is_hosted,
    resolve_allowed_hosts,
    resolve_bool,
    resolve_csrf_trusted_origins,
    resolve_secret_key,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

IS_HOSTED = detect_is_hosted(BASE_DIR)
DEBUG = detect_debug(BASE_DIR)
SECRET_KEY = resolve_secret_key(BASE_DIR)

ALLOWED_HOSTS = resolve_allowed_hosts(debug=DEBUG)

# Trust proxy headers (cPanel / nginx / Cloudflare / ngrok).
USE_X_FORWARDED_HOST = resolve_bool("DJANGO_USE_X_FORWARDED_HOST", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = resolve_csrf_trusted_origins()

# Optional override; otherwise auto from request / ngrok.
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
    "core.middleware.AutoHostMiddleware",
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

MYSQL_ENABLED = resolve_bool("MYSQL_ENABLED", default=True)

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
                os.getenv(
                    "MYSQL_CONN_MAX_AGE",
                    "60" if IS_HOSTED or not DEBUG else "300",
                )
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
# Cache & sessions (Redis when available, LocMem fallback for local / cPanel)
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
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(60 * 60 * 24 * 14)))
SESSION_SAVE_EVERY_REQUEST = False

# ---------------------------------------------------------------------------
# Rate limiting
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

EMPLOYEE_LIST_PAGE_SIZE = int(os.getenv("EMPLOYEE_LIST_PAGE_SIZE", "25"))

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL or "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Africa/Nairobi"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "300"))

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "employees:login"
LOGIN_REDIRECT_URL = "employees:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"

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

# Auto: serve media from Django on hosted installs unless explicitly disabled.
SERVE_MEDIA_IN_PRODUCTION = resolve_bool(
    "SERVE_MEDIA_IN_PRODUCTION",
    default=IS_HOSTED or not DEBUG,
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
# Production hardening (auto when hosted / DEBUG=False)
# ---------------------------------------------------------------------------

if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_SSL_REDIRECT = resolve_bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = resolve_bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = resolve_bool("CSRF_COOKIE_SECURE", default=True)
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = resolve_bool(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
    )
    SECURE_HSTS_PRELOAD = resolve_bool("SECURE_HSTS_PRELOAD", default=False)
