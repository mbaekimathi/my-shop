"""Communications workspace page + JSON APIs."""

from __future__ import annotations

import json

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from employees.access import (
    active_employee_required,
    get_profile_for_request,
    role_from_url_segment,
    role_url_segment,
)
from employees.module_permissions import (
    employee_may_any,
    permission_denied_response,
    require_module_permission,
)
from employees.workspace import get_dashboard_module, sidebar_for_communications

from .analytics import analytics_payload
from .bridge import bridge_deploy_hints, fetch_bridge_status, logout_bridge
from .campaigns import campaign_as_dict, create_campaign, recent_campaigns
from .constants import (
    AUDIENCE_TYPE_CHOICES,
    LAST_PURCHASE_WINDOWS,
    PLACEHOLDERS,
    SPEND_TIER_CHOICES,
    TRANSACTION_MIN_CHOICES,
)
from .models import BroadcastCampaign
from .replies import inbox_threads, unread_reply_count
from .services import audience_summary, list_product_categories, preview_message, recipients_payload


def _guard(request, role_segment, *, as_json=False):
    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        raise Http404("Role portal not found.")
    expected = role_url_segment(profile.role)
    if role_segment != expected:
        if as_json:
            return None, JsonResponse(
                {"ok": False, "error": "Wrong role portal.", "redirect": reverse(
                    "employees:workspace_module",
                    kwargs={"role_segment": expected, "module_slug": "whatsapp"},
                )},
                status=403,
            )
        return None, redirect(
            "employees:workspace_module",
            role_segment=expected,
            module_slug="whatsapp",
        )
    if not employee_may_any(profile, "whatsapp"):
        return None, permission_denied_response(
            request,
            profile,
            message="You do not have permission to access WhatsApp.",
            as_json=as_json,
        )
    denied = require_module_permission(
        request, profile, "whatsapp", "view", as_json=as_json
    )
    if denied:
        return None, denied
    return profile, None


def _api_urls(role_segment: str) -> dict:
    return {
        "status": reverse(
            "employees:whatsapp_api_status",
            kwargs={"role_segment": role_segment},
        ),
        "logout": reverse(
            "employees:whatsapp_api_logout",
            kwargs={"role_segment": role_segment},
        ),
        "recipients": reverse(
            "employees:whatsapp_api_recipients",
            kwargs={"role_segment": role_segment},
        ),
        "preview": reverse(
            "employees:whatsapp_api_preview",
            kwargs={"role_segment": role_segment},
        ),
        "send": reverse(
            "employees:whatsapp_api_send",
            kwargs={"role_segment": role_segment},
        ),
        "campaign": reverse(
            "employees:whatsapp_api_campaign",
            kwargs={"role_segment": role_segment, "campaign_id": 0},
        ).replace("/0/", "/{id}/"),
        "inbox": reverse(
            "employees:whatsapp_api_inbox",
            kwargs={"role_segment": role_segment},
        ),
        "analytics": reverse(
            "employees:whatsapp_api_analytics",
            kwargs={"role_segment": role_segment},
        ),
    }


def _filters_from_request(request) -> dict:
    if request.content_type and "application/json" in (request.content_type or ""):
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload.get("filters"), dict):
            return payload["filters"]
        return {
            "audience_type": payload.get("audience_type")
            or request.GET.get("audience_type")
            or "",
            "category": payload.get("category") or request.GET.get("category") or "",
            "categories": payload.get("categories") or payload.get("groups") or [],
            "item_ids": payload.get("item_ids") or payload.get("items") or [],
            "spend_tier": payload.get("spend_tier")
            or request.GET.get("spend_tier")
            or "",
            "min_transactions": payload.get("min_transactions")
            or request.GET.get("min_transactions")
            or "",
            "last_purchase_days": payload.get("last_purchase_days")
            or request.GET.get("last_purchase_days")
            or "",
            "shop_id": payload.get("shop_id") or request.GET.get("shop_id") or "",
            "search": payload.get("search") or request.GET.get("search") or "",
            "client_ids": payload.get("client_ids") or [],
            "destinations": payload.get("destinations") or [],
            "outstanding_only": payload.get("outstanding_only"),
        }

    categories_raw = request.GET.getlist("categories") or request.POST.getlist("categories")
    if not categories_raw:
        single = request.GET.get("category") or request.POST.get("category") or ""
        categories_raw = [single] if single else []
    item_ids_raw = (
        request.GET.getlist("item_ids")
        or request.POST.getlist("item_ids")
        or request.GET.get("item_ids")
        or request.POST.get("item_ids")
        or ""
    )
    client_ids_raw = (
        request.GET.getlist("client_ids")
        or request.POST.getlist("client_ids")
        or request.GET.get("client_ids")
        or request.POST.get("client_ids")
        or ""
    )
    return {
        "audience_type": request.GET.get("audience_type")
        or request.POST.get("audience_type")
        or "",
        "category": request.GET.get("category") or request.POST.get("category") or "",
        "categories": categories_raw,
        "item_ids": item_ids_raw,
        "spend_tier": request.GET.get("spend_tier")
        or request.POST.get("spend_tier")
        or "",
        "min_transactions": request.GET.get("min_transactions")
        or request.POST.get("min_transactions")
        or "",
        "last_purchase_days": request.GET.get("last_purchase_days")
        or request.POST.get("last_purchase_days")
        or "",
        "shop_id": request.GET.get("shop_id") or request.POST.get("shop_id") or "",
        "search": request.GET.get("search") or request.POST.get("search") or "",
        "client_ids": client_ids_raw,
        "destinations": request.GET.getlist("destinations")
        or request.POST.getlist("destinations")
        or request.GET.get("destinations")
        or request.POST.get("destinations")
        or "",
        "outstanding_only": request.GET.get("outstanding_only")
        or request.POST.get("outstanding_only"),
    }


def communications_dashboard(request, profile, meta, module, page_sidebar=None):
    """Main /…/communications/ page (called from workspace_module)."""
    segment = role_url_segment(profile.role)
    bridge = fetch_bridge_status()
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": page_sidebar
        or sidebar_for_communications(profile.role, active_view="home", profile=profile),
        "bridge": bridge,
        "bridge_hints": bridge_deploy_hints(),
        "categories": list_product_categories(),
        "audience_types": AUDIENCE_TYPE_CHOICES,
        "audience_summary": audience_summary(),
        "spend_tiers": SPEND_TIER_CHOICES,
        "transaction_mins": TRANSACTION_MIN_CHOICES,
        "last_purchase_windows": LAST_PURCHASE_WINDOWS,
        "placeholders": PLACEHOLDERS,
        "recent_campaigns": recent_campaigns(8),
        "reply_unread_count": unread_reply_count(),
        "comms_api": _api_urls(segment),
    }
    return render(request, "employees/communications.html", context)


@active_employee_required
@require_GET
def communications_api_status(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    bridge = fetch_bridge_status()
    return JsonResponse(
        {
            "ok": True,
            **bridge,
            "reply_unread_count": unread_reply_count(),
        }
    )


@active_employee_required
@require_http_methods(["GET", "POST"])
def communications_api_inbox(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    mark_read = request.method == "POST" or request.GET.get("mark_read") in {
        "1",
        "true",
        "yes",
    }
    return JsonResponse(inbox_threads(mark_read=mark_read))


@active_employee_required
@require_GET
def communications_api_analytics(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    return JsonResponse(analytics_payload())


@active_employee_required
@require_POST
def communications_api_logout(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    result = logout_bridge()
    return JsonResponse({"ok": result.get("ok", False), **fetch_bridge_status(), "error": result.get("error") or ""})


@active_employee_required
@require_GET
def communications_api_recipients(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    filters = _filters_from_request(request)
    payload = recipients_payload(filters, sample=500)
    return JsonResponse(payload)


@active_employee_required
@require_http_methods(["GET", "POST"])
def communications_api_preview(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    if request.method == "POST" and request.content_type and "application/json" in (
        request.content_type or ""
    ):
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        template = payload.get("body") or payload.get("template") or ""
        filters = payload.get("filters") or _filters_from_request(request)
        client_id = payload.get("client_id")
        destination_key = payload.get("destination_key")
    else:
        template = request.POST.get("body") or request.GET.get("body") or ""
        filters = _filters_from_request(request)
        client_id = request.GET.get("client_id") or request.POST.get("client_id")
        destination_key = request.GET.get("destination_key") or request.POST.get(
            "destination_key"
        )
    try:
        client_id = int(client_id) if client_id not in (None, "") else None
    except (TypeError, ValueError):
        client_id = None
    return JsonResponse(
        preview_message(
            template,
            filters,
            client_id=client_id,
            destination_key=destination_key or None,
        )
    )


@active_employee_required
@require_POST
def communications_api_send(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny

    if request.content_type and "multipart/form-data" in (request.content_type or ""):
        body = request.POST.get("body") or ""
        categories = request.POST.getlist("categories") or []
        if not categories and request.POST.get("category"):
            categories = [request.POST.get("category")]
        client_ids = request.POST.getlist("client_ids") or []
        if not client_ids and request.POST.get("client_ids"):
            client_ids = request.POST.get("client_ids")
        destinations = request.POST.getlist("destinations") or []
        if not destinations and request.POST.get("destinations"):
            destinations = request.POST.get("destinations")
        filters = {
            "audience_type": request.POST.get("audience_type") or "whatsapp",
            "category": request.POST.get("category") or "",
            "categories": categories,
            "item_ids": request.POST.getlist("item_ids") or [],
            "spend_tier": request.POST.get("spend_tier") or "",
            "min_transactions": request.POST.get("min_transactions") or "",
            "last_purchase_days": request.POST.get("last_purchase_days") or "",
            "shop_id": request.POST.get("shop_id") or "",
            "search": request.POST.get("search") or "",
            "client_ids": client_ids,
            "destinations": destinations,
            "outstanding_only": request.POST.get("outstanding_only"),
        }
        image = request.FILES.get("image")
    else:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
        body = payload.get("body") or ""
        filters = payload.get("filters") or {}
        image = None

    try:
        campaign = create_campaign(
            profile=profile,
            body_template=body,
            filters=filters,
            image=image,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except RuntimeError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)

    return JsonResponse({"ok": True, "campaign": campaign_as_dict(campaign)})


@active_employee_required
@require_GET
def communications_api_campaign(request, role_segment, campaign_id):
    profile, deny = _guard(request, role_segment, as_json=True)
    if deny:
        return deny
    campaign = BroadcastCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return JsonResponse({"ok": False, "error": "Campaign not found."}, status=404)
    return JsonResponse({"ok": True, "campaign": campaign_as_dict(campaign)})
