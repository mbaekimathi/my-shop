"""CSRF failure that stays JSON for shop-floor fetch calls."""

from django.http import JsonResponse
from django.views.csrf import csrf_failure as django_csrf_failure


def csrf_failure(request, reason=""):
    accept = request.headers.get("Accept") or ""
    requested_with = request.headers.get("X-Requested-With") or ""
    if "application/json" in accept or requested_with == "XMLHttpRequest":
        return JsonResponse(
            {"ok": False, "error": "Page expired. Refresh and try again."},
            status=403,
        )
    return django_csrf_failure(request, reason=reason)
