"""HTTP endpoints for Web Push subscribe / unsubscribe / VAPID key."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from .access import get_profile_for_request
from .models import EmployeeStatus, WebPushSubscription
from .throttle import rate_limit
from .web_push import vapid_public_key, web_push_enabled


def _actor(request):
    """Return (employee_profile|None, shop|None) for the current session."""
    profile = None
    if getattr(request.user, "is_authenticated", False):
        candidate = get_profile_for_request(request)
        if (
            candidate is not None
            and getattr(candidate, "status", None) == EmployeeStatus.ACTIVE
        ):
            profile = candidate

    shop = None
    try:
        from shops.session import resolve_portal_shop

        shop = resolve_portal_shop(request)
    except Exception:
        shop = None

    return profile, shop


def _parse_json(request) -> dict:
    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@require_GET
def web_push_vapid_public_key(request):
    key = vapid_public_key()
    return JsonResponse(
        {
            "ok": bool(key) and web_push_enabled(),
            "publicKey": key,
        }
    )


@rate_limit("web_push_subscribe")
@require_http_methods(["POST"])
def web_push_subscribe(request):
    profile, shop = _actor(request)
    if profile is None and shop is None:
        return JsonResponse(
            {"ok": False, "error": "auth_required", "message": "Sign in to enable alerts."},
            status=401,
        )

    if not web_push_enabled():
        return JsonResponse(
            {
                "ok": False,
                "error": "push_disabled",
                "message": "Push notifications are not configured on this server.",
            },
            status=503,
        )

    data = _parse_json(request)
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    p256dh = (keys.get("p256dh") or data.get("p256dh") or "").strip()
    auth = (keys.get("auth") or data.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return JsonResponse(
            {"ok": False, "error": "invalid", "message": "Incomplete push subscription."},
            status=400,
        )
    if len(endpoint) > 500:
        return JsonResponse(
            {"ok": False, "error": "invalid", "message": "Push endpoint is too long."},
            status=400,
        )

    ua = (request.META.get("HTTP_USER_AGENT") or "")[:255]
    endpoint_hash = WebPushSubscription.hash_endpoint(endpoint)
    row, _created = WebPushSubscription.objects.update_or_create(
        endpoint_hash=endpoint_hash,
        defaults={
            "endpoint": endpoint,
            "p256dh": p256dh[:255],
            "auth": auth[:255],
            "employee": profile,
            "shop": shop,
            "user_agent": ua,
        },
    )
    return JsonResponse(
        {
            "ok": True,
            "id": row.pk,
            "employee": bool(profile),
            "shop": bool(shop),
        }
    )


@rate_limit("web_push_subscribe")
@require_http_methods(["POST"])
def web_push_unsubscribe(request):
    profile, shop = _actor(request)
    data = _parse_json(request)
    endpoint = (data.get("endpoint") or "").strip()
    qs = WebPushSubscription.objects.all()
    if endpoint:
        qs = qs.filter(endpoint=endpoint)
    elif profile is not None:
        qs = qs.filter(employee=profile)
    elif shop is not None:
        qs = qs.filter(shop=shop)
    else:
        return JsonResponse(
            {"ok": False, "error": "auth_required"},
            status=401,
        )
    deleted, _ = qs.delete()
    return JsonResponse({"ok": True, "deleted": deleted})
