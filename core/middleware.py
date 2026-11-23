import logging
import time

logger = logging.getLogger("myshop.slow_requests")


class RequestDurationLogMiddleware:
    """Log requests that hold a worker too long.

    Hosted LiteSpeed/Passenger caps the app at a handful of LSAPI children,
    so requests slower than SLOW_REQUEST_MS (default 2000ms) are what trigger
    the "max children process limit" warnings. Each line carries the resolved
    URL route so the pinning endpoint is identifiable; warnings land on stderr
    next to the LSAPI messages. X-Request-Ms exposes the same timing per
    response for spot checks in devtools.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        start = time.monotonic()
        response = self.get_response(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        response["X-Request-Ms"] = str(int(elapsed_ms))
        threshold_ms = getattr(settings, "SLOW_REQUEST_MS", 2000) or 0
        if elapsed_ms >= threshold_ms:
            match = getattr(request, "resolver_match", None)
            route = getattr(match, "route", "") or request.path
            logger.warning(
                "slow request %.0fms %s %s -> %s (%s)",
                elapsed_ms,
                request.method,
                request.path,
                response.status_code,
                route,
            )
        return response


class NoCacheHtmlMiddleware:
    """Prevent browsers from serving stale HTML when assets or templates change."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get("Content-Type", "")
        if "text/html" in content_type and response.status_code == 200:
            # no-cache revalidates HTML on every load (fresh after deploys)
            # while staying eligible for Chrome's back/forward cache —
            # no-store permanently blocks bfcache, forcing a full reload
            # plus ~1MB of JS re-parse on every back/forward navigation.
            response["Cache-Control"] = "no-cache"
        return response


class AutoHostMiddleware:
    """
    Learn the live public host on each request so CSRF / Daraja stay in sync
    without editing .env when the domain or ngrok URL changes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        from shops.daraja_stk import detect_request_base_url, is_safaricom_callback_base

        host = (request.get_host() or "").strip()
        if host:
            origin = detect_request_base_url(request) or ""
            if not origin:
                scheme = "https" if request.is_secure() else "http"
                forwarded = (request.META.get("HTTP_X_FORWARDED_PROTO") or "").split(",")[0].strip()
                if forwarded in {"http", "https"}:
                    scheme = forwarded
                origin = f"{scheme}://{host}".rstrip("/")

            trusted = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
            if origin and origin not in trusted:
                trusted.append(origin)
                settings.CSRF_TRUSTED_ORIGINS = trusted

            # Always track the current public HTTPS domain for Daraja callbacks.
            if origin and is_safaricom_callback_base(origin):
                current = (
                    getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or ""
                ).strip().rstrip("/")
                if current != origin.rstrip("/"):
                    settings.DARAJA_CALLBACK_BASE_URL = origin
                    try:
                        from shops.daraja_stk import persist_public_callback_base

                        persist_public_callback_base(origin)
                    except Exception:
                        pass

        return self.get_response(request)
