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

        host = (request.get_host() or "").strip()
        if host:
            scheme = "https" if request.is_secure() else "http"
            # Honor proxy TLS termination.
            forwarded = (request.META.get("HTTP_X_FORWARDED_PROTO") or "").split(",")[0].strip()
            if forwarded in {"http", "https"}:
                scheme = forwarded
            origin = f"{scheme}://{host}".rstrip("/")
            trusted = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
            if origin not in trusted:
                trusted.append(origin)
                settings.CSRF_TRUSTED_ORIGINS = trusted

            # Persist public HTTPS origin for Daraja when not manually set.
            if scheme == "https" and not (getattr(settings, "DARAJA_CALLBACK_BASE_URL", "") or "").strip():
                hostname = host.split(":")[0].lower()
                if hostname not in {"localhost", "127.0.0.1", "::1"} and not hostname.endswith(
                    ".local"
                ):
                    settings.DARAJA_CALLBACK_BASE_URL = origin

        return self.get_response(request)
