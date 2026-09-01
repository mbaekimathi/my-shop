class NoCacheHtmlMiddleware:
    """Prevent browsers from serving stale HTML when assets or templates change."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get("Content-Type", "")
        if "text/html" in content_type and response.status_code == 200:
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response["Pragma"] = "no-cache"
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
