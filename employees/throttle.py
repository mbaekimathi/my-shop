"""Cache-backed rate limiting for public and auth endpoints."""

from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse


def get_client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def is_rate_limited(scope: str, identifier: str) -> bool:
    """
    Return True when the identifier has exceeded the configured limit.
    Uses cache increment when supported; falls back to get/set.
    """
    limits = settings.RATE_LIMITS.get(scope, {})
    max_requests = limits.get("max", 60)
    window = limits.get("window", 60)
    key = f"rl:{scope}:{identifier}"

    try:
        added = cache_add(key, 1, window)
        if added:
            return False
        count = cache_incr(key)
        return count > max_requests
    except ValueError:
        cache_set(key, 1, window)
        return False


def cache_add(key, value, timeout):
    from django.core.cache import cache

    return cache.add(key, value, timeout)


def cache_incr(key):
    from django.core.cache import cache

    return cache.incr(key)


def cache_set(key, value, timeout):
    from django.core.cache import cache

    cache.set(key, value, timeout)


def rate_limit(scope: str):
    """Decorator that blocks requests when the client IP exceeds rate limits."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if is_rate_limited(scope, get_client_ip(request)):
                accept = request.headers.get("Accept", "")
                if scope == "check_employee_id" or "application/json" in accept:
                    return JsonResponse(
                        {
                            "available": None,
                            "message": "Too many requests. Please wait a moment.",
                            "error": "rate_limited",
                        },
                        status=429,
                    )
                return HttpResponse(
                    "Too many requests. Please wait a moment and try again.",
                    status=429,
                    content_type="text/plain",
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
