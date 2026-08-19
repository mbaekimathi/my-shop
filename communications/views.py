"""Communications workspace page + JSON APIs."""

from __future__ import annotations

import json
import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from employees.access import (
    active_employee_required,
    get_profile_for_request,
    role_from_url_segment,
    role_url_segment,
)
from employees.module_permissions import (
    employee_may_any,
    module_capabilities,
    permission_denied_response,
    require_module_permission,
)
from employees.workspace import (
    get_dashboard_module,
    sidebar_for_communications,
    sidebar_for_marketing_links,
)

from .analytics import analytics_payload
from .twilio import (
    apply_message_status,
    bridge_deploy_hints,
    fetch_bridge_status,
    logout_bridge,
    request_signature_ok,
    sandbox_join_info,
    sync_inbound_replies,
    sync_outbound_delivery_status,
)
from .automations import DEFAULT_CATALOGUE_TEMPLATE, WHATSAPP_TEXT_LIMIT
from .campaigns import (
    activities_payload,
    campaign_as_dict,
    cancel_campaign,
    create_campaign,
    create_catalogue_campaign,
    create_catalogue_campaign_series,
    recent_campaigns,
    retry_failed_campaign,
)
from .constants import (
    AUDIENCE_TYPE_CHOICES,
    CATALOGUE_MESSAGE_PLACEHOLDERS,
    LAST_PURCHASE_WINDOWS,
    PLACEHOLDERS,
    SHARE_PERIOD_CHOICES,
    SHARE_REPEAT_CHOICES,
    SPEND_TIER_CHOICES,
    TRANSACTION_MIN_CHOICES,
)
from .models import BroadcastCampaign
from .replies import (
    inbox_threads,
    mark_phone_read,
    record_inbound_reply,
    send_inbox_reply,
    unread_reply_count,
)
from .services import (
    audience_summary,
    add_whatsapp_contact,
    bought_item_ids_for_filters,
    companion_item_ids,
    constrain_filters_to_profile,
    create_whatsapp_group,
    join_whatsapp_group,
    list_product_categories,
    list_whatsapp_contacts,
    list_whatsapp_groups,
    preview_message,
    recipients_payload,
    WHATSAPP_WEB_URL,
)
from shops.services import (
    communications_settings_as_dict,
    get_communications_settings,
    set_communications_setting,
    update_automation_audience,
)
from items.services import actionable_shops_for_profile

logger = logging.getLogger(__name__)


def _guard(request, role_segment, *, as_json=False, submodule="view"):
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
        request, profile, "whatsapp", submodule, as_json=as_json
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
    """WhatsApp settings: what to automate and who to share with."""
    denied = require_module_permission(request, profile, "whatsapp", "view")
    if denied:
        return denied
    if request.method == "POST":
        return _automation_post(request, profile)

    segment = role_url_segment(profile.role)
    try:
        sync_outbound_delivery_status()
    except Exception:
        logger.exception("Twilio delivery sync failed")
    bridge = fetch_bridge_status()
    scoped = constrain_filters_to_profile({}, profile)
    comms = communications_settings_as_dict()
    shops = actionable_shops_for_profile(profile)
    audience_filters = constrain_filters_to_profile(
        {
            "audience_type": comms.get("automation_audience_type") or "sale",
            "last_purchase_days": comms.get("automation_last_purchase_days") or "",
            "shop_id": comms.get("automation_shop_id") or "",
        },
        profile,
    )
    people = _people_payload(profile, audience_filters)
    settings_url = reverse("employees:settings_section", kwargs={"section": "twilio"})
    catalogue_url = reverse(
        "employees:whatsapp_catalogue",
        kwargs={"role_segment": segment},
    )
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": page_sidebar
        or sidebar_for_communications(
            profile.role, active_view="whatsapp", profile=profile
        ),
        "bridge": bridge,
        "comms": comms,
        "shops": shops,
        "audience_types": AUDIENCE_TYPE_CHOICES,
        "audience_summary": audience_summary(
            shop_id=scoped.get("shop_id"),
            shop_ids=scoped.get("shop_ids"),
            shop_scoped=scoped.get("shop_scoped"),
        ),
        "last_purchase_windows": LAST_PURCHASE_WINDOWS,
        "recipient_count": people["recipient_count"],
        "recipients": people["recipients"],
        "recent_sends": recent_campaigns(8),
        "settings_url": settings_url,
        "catalogue_url": catalogue_url,
        "comms_api": _api_urls(segment),
        "module_permissions": module_capabilities(profile, "whatsapp"),
        "automation_toggles": _automation_toggles(comms),
        "sandbox_join": sandbox_join_info(),
    }
    return render(request, "employees/whatsapp_automations.html", context)


@active_employee_required
@require_http_methods(["GET", "POST"])
def whatsapp_catalogue(request, role_segment):
    """Manual item shares: pick items and send them to a saved WhatsApp audience."""
    profile, deny = _guard(request, role_segment)
    if deny:
        return deny
    if request.method == "POST":
        return _automation_post(request, profile)

    module = get_dashboard_module("whatsapp", profile.role) or {
        "label": "WhatsApp",
        "summary": "Share items on WhatsApp.",
        "icon": "messages-square",
    }
    meta = {
        "title": "Share items",
        "headline": "Share items",
        "summary": "Send item photos and prices on WhatsApp.",
        "icon": "images",
    }
    bridge = fetch_bridge_status()
    comms = communications_settings_as_dict()
    shops = actionable_shops_for_profile(profile)
    audience_filters = constrain_filters_to_profile(
        {
            "audience_type": comms.get("automation_audience_type") or "sale",
            "last_purchase_days": comms.get("automation_last_purchase_days") or "",
            "shop_id": comms.get("automation_shop_id") or "",
        },
        profile,
    )
    people = _people_payload(profile, audience_filters)
    scoped = constrain_filters_to_profile({}, profile)
    items, item_total = _share_item_rows(audience_filters)
    filter_items, _ = _catalogue_item_rows()
    settings_url = reverse("employees:settings_section", kwargs={"section": "twilio"})
    whatsapp_url = reverse(
        "employees:settings_section",
        kwargs={"section": "whatsapp"},
    )
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_marketing_links(
            profile.role, profile=profile, active_view="catalogue"
        ),
        "bridge": bridge,
        "comms": comms,
        "shops": shops,
        "audience_types": AUDIENCE_TYPE_CHOICES,
        "audience_summary": audience_summary(
            shop_id=scoped.get("shop_id"),
            shop_ids=scoped.get("shop_ids"),
            shop_scoped=scoped.get("shop_scoped"),
        ),
        "last_purchase_windows": LAST_PURCHASE_WINDOWS,
        "share_periods": SHARE_PERIOD_CHOICES,
        "share_repeats": SHARE_REPEAT_CHOICES,
        "filter_items": filter_items,
        "recipient_count": people["recipient_count"],
        "recipients": people["recipients"],
        "catalogue_items": items,
        "catalogue_item_total": item_total,
        "settings_url": settings_url,
        "whatsapp_url": whatsapp_url,
        "module_permissions": module_capabilities(profile, "whatsapp"),
        "sandbox_join": sandbox_join_info(),
        "catalogue_placeholders": CATALOGUE_MESSAGE_PLACEHOLDERS,
        "catalogue_message_default": DEFAULT_CATALOGUE_TEMPLATE,
    }
    return render(request, "employees/whatsapp_catalogue.html", context)


@active_employee_required
@require_http_methods(["GET", "POST"])
def whatsapp_contacts(request, role_segment):
    """All saved contacts, plus create/join WhatsApp groups."""
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.http import HttpResponseRedirect

    profile, deny = _guard(request, role_segment)
    if deny:
        return deny
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        open_whatsapp = (request.POST.get("open_whatsapp") or "").strip() in {
            "1",
            "true",
            "on",
            "yes",
        }
        try:
            if action == "add_contact":
                add_whatsapp_contact(
                    full_name=request.POST.get("full_name") or "",
                    phone=request.POST.get("phone") or "",
                    profile=profile,
                )
                messages.success(request, "Contact saved.")
            elif action == "create_group":
                group = create_whatsapp_group(
                    name=request.POST.get("name") or "",
                    invite_link=request.POST.get("invite_link") or "",
                    member_ids=request.POST.getlist("member_ids"),
                    profile=profile,
                )
                messages.success(request, "Group created.")
                if open_whatsapp:
                    return HttpResponseRedirect(group.invite_link or WHATSAPP_WEB_URL)
            elif action == "join_group":
                group = join_whatsapp_group(
                    name=request.POST.get("name") or "",
                    invite_link=request.POST.get("invite_link") or "",
                    member_ids=request.POST.getlist("member_ids"),
                    profile=profile,
                )
                messages.success(request, "Group saved. Open it in WhatsApp to join.")
                if open_whatsapp:
                    return HttpResponseRedirect(group.invite_link or WHATSAPP_WEB_URL)
            else:
                messages.error(request, "Unknown action.")
        except ValidationError as exc:
            messages.error(
                request,
                "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc),
            )
        return redirect(request.path)

    module = get_dashboard_module("whatsapp", profile.role) or {
        "label": "WhatsApp",
        "summary": "Share items on WhatsApp.",
        "icon": "messages-square",
    }
    meta = {
        "title": "Contacts",
        "headline": "Contacts",
        "summary": "Every saved contact, plus WhatsApp groups you create or join.",
        "icon": "contact",
    }
    contacts = list_whatsapp_contacts()
    groups = list_whatsapp_groups()
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_marketing_links(
            profile.role, profile=profile, active_view="contacts"
        ),
        "contacts": contacts,
        "contact_count": len(contacts),
        "groups": groups,
        "group_count": len(groups),
        "whatsapp_web_url": WHATSAPP_WEB_URL,
        "module_permissions": module_capabilities(profile, "whatsapp"),
    }
    return render(request, "employees/whatsapp_contacts.html", context)


@active_employee_required
@require_http_methods(["GET", "POST"])
def whatsapp_inbox(request, role_segment):
    """Customer WhatsApp chats on the Twilio number."""
    profile, deny = _guard(request, role_segment, submodule="inbox")
    if deny:
        return deny
    if request.method == "POST":
        return _automation_post(request, profile)

    try:
        sync_inbound_replies()
    except Exception:
        logger.exception("Twilio inbound sync failed")
    try:
        sync_outbound_delivery_status()
    except Exception:
        logger.exception("Twilio delivery sync failed")
    inbox = inbox_threads(mark_read=False)
    from .services import list_whatsapp_contacts

    module = get_dashboard_module("whatsapp", profile.role) or {
        "label": "WhatsApp",
        "summary": "Share items on WhatsApp.",
        "icon": "messages-square",
    }
    meta = {
        "title": "WhatsApp",
        "headline": "WhatsApp",
        "summary": "Chats on the Twilio WhatsApp number.",
        "icon": "messages-square",
    }
    settings_url = reverse("employees:settings_section", kwargs={"section": "twilio"})
    bridge = fetch_bridge_status()
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_marketing_links(
            profile.role, profile=profile, active_view="inbox"
        ),
        "inbox": inbox,
        "contacts": list_whatsapp_contacts()[:400],
        "bridge": bridge,
        "settings_url": settings_url,
        "comms_api": _api_urls(role_segment),
        "module_permissions": module_capabilities(profile, "whatsapp"),
        "selected_phone": (request.GET.get("phone") or "").strip(),
    }
    return render(request, "employees/whatsapp_inbox.html", context)


@active_employee_required
@require_http_methods(["GET", "POST"])
def marketing_activities(request, role_segment):
    """WhatsApp send history: queued, delivered, viewed, cancel, and retry."""
    profile, deny = _guard(request, role_segment)
    if deny:
        return deny
    if request.method == "POST":
        return _automation_post(request, profile)

    module = get_dashboard_module("whatsapp", profile.role) or {
        "label": "WhatsApp",
        "summary": "Share items on WhatsApp.",
        "icon": "messages-square",
    }
    meta = {
        "title": "Activities",
        "headline": "Activities",
        "summary": "Track WhatsApp item shares: queued, sent, viewed, cancelled, and failed.",
        "icon": "history",
    }
    try:
        sync_outbound_delivery_status()
    except Exception:
        logger.exception("Twilio delivery sync failed")
    settings_url = reverse("employees:settings_section", kwargs={"section": "twilio"})
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_marketing_links(
            profile.role, profile=profile, active_view="activities"
        ),
        "recent_sends": recent_campaigns(8),
        "activities": activities_payload(),
        "settings_url": settings_url,
        "module_permissions": module_capabilities(profile, "whatsapp"),
    }
    return render(request, "employees/marketing_activities.html", context)


@active_employee_required
@require_http_methods(["GET", "POST"])
def marketing_activity_detail(request, role_segment, campaign_id):
    """One WhatsApp send: the message, who it went to, and each person's status."""
    profile, deny = _guard(request, role_segment)
    if deny:
        return deny
    if request.method == "POST":
        return _automation_post(request, profile)

    campaign = BroadcastCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        raise Http404("Send not found.")
    try:
        sync_outbound_delivery_status()
    except Exception:
        logger.exception("Twilio delivery sync failed")
    activity = campaign_as_dict(campaign)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "campaign": activity})

    settings_url = reverse("employees:settings_section", kwargs={"section": "twilio"})
    activities_url = reverse(
        "employees:marketing_activities",
        kwargs={"role_segment": role_segment},
    )
    module = get_dashboard_module("whatsapp", profile.role) or {
        "label": "WhatsApp",
        "summary": "Share items on WhatsApp.",
        "icon": "messages-square",
    }
    kind = activity.get("kind_label") or "WhatsApp send"
    meta = {
        "title": kind,
        "headline": kind,
        "summary": "Message, recipients, and delivery status for this send.",
        "icon": "history",
    }
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_marketing_links(
            profile.role, profile=profile, active_view="activities"
        ),
        "activity": activity,
        "activities_url": activities_url,
        "settings_url": settings_url,
        "module_permissions": module_capabilities(profile, "whatsapp"),
    }
    return render(request, "employees/marketing_activity_detail.html", context)


def _automation_toggles(comms: dict) -> list[dict]:
    return [
        {
            "field": "auto_sale_receipt",
            "label": "Cash sale receipts",
            "summary": "Send the receipt after a cash sale.",
            "icon": "receipt",
            "enabled": bool(comms.get("auto_sale_receipt")),
        },
        {
            "field": "auto_quotation",
            "label": "Quotations",
            "summary": "Send the quotation when it is issued.",
            "icon": "file-text",
            "enabled": bool(comms.get("auto_quotation")),
        },
        {
            "field": "auto_payment_reminder",
            "label": "Credit sales",
            "summary": "Send a credit sale notice to the customer phone when credit is taken at the cart.",
            "icon": "wallet",
            "enabled": bool(comms.get("auto_payment_reminder")),
        },
        {
            "field": "auto_credit_due",
            "label": "Credit due dates",
            "summary": "Remind customers when credit is due.",
            "icon": "calendar-clock",
            "enabled": bool(comms.get("auto_credit_due")),
        },
        {
            "field": "auto_shop_website",
            "label": "Shop website",
            "summary": "Allow sending each shop’s public catalogue link.",
            "icon": "store",
            "enabled": bool(comms.get("auto_shop_website")),
        },
        {
            "field": "auto_stock_supplier",
            "label": "Buy stock",
            "summary": "Send a WhatsApp notice to the supplier after stock is bought from the shop popup.",
            "icon": "package-plus",
            "enabled": bool(comms.get("auto_stock_supplier")),
        },
        {
            "field": "auto_expense_supplier",
            "label": "Register expense",
            "summary": "Send a WhatsApp notice to the supplier after an expense is registered from the shop popup.",
            "icon": "banknote",
            "enabled": bool(comms.get("auto_expense_supplier")),
        },
    ]


def _people_payload(profile, filters: dict | None = None) -> dict:
    scoped = constrain_filters_to_profile(filters or {}, profile)
    payload = recipients_payload(scoped, sample=500)
    recipients = [
        {
            "client_id": row.get("client_id"),
            "full_name": (row.get("full_name") or "").strip() or "Customer",
            "phone": (row.get("phone") or "").strip(),
        }
        for row in payload.get("recipients") or []
    ]
    return {
        "recipient_count": int(payload.get("count") or 0),
        "recipients": recipients,
    }


def _selected_client_ids(request) -> list[int]:
    raw = request.POST.getlist("client_ids")
    if not raw and request.POST.get("client_ids"):
        raw = [request.POST.get("client_ids")]
    ids = []
    seen = set()
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def _audience_filters_from_post(request) -> dict:
    raw_item = (request.POST.get("filter_item_id") or "").strip()
    item_ids = []
    if raw_item.isdigit():
        item_ids = [int(raw_item)]
    return {
        "audience_type": request.POST.get("audience_type") or "",
        "last_purchase_days": request.POST.get("last_purchase_days") or "",
        "shop_id": request.POST.get("shop_id") or "",
        "item_ids": item_ids,
    }


def _json_wants(request) -> bool:
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )


def _automation_post(request, profile):
    from django.contrib import messages
    from django.core.exceptions import ValidationError

    action = (request.POST.get("action") or "").strip()
    wants_json = _json_wants(request)
    can_send = module_capabilities(profile, "whatsapp").get("send") is not False

    def fail(message, status=400):
        if wants_json:
            return JsonResponse({"ok": False, "error": message}, status=status)
        messages.error(request, message)
        return redirect(request.path)

    if action == "toggle_automation":
        field = (request.POST.get("field") or "").strip()
        enabled = (request.POST.get("enabled") or "").strip().lower() in {
            "1",
            "true",
            "on",
            "yes",
        }
        if field not in {
            "enable_automations",
            "auto_sale_receipt",
            "auto_quotation",
            "auto_payment_reminder",
            "auto_credit_due",
            "auto_shop_website",
            "auto_stock_supplier",
            "auto_expense_supplier",
            "auto_item_catalogue",
        }:
            return fail("Unknown automation.")
        try:
            row = set_communications_setting(field=field, enabled=enabled)
        except ValidationError as exc:
            return fail("; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        payload = communications_settings_as_dict(row)
        if wants_json:
            return JsonResponse({"ok": True, "message": "Saved.", **payload})
        messages.success(request, "Automation saved.")
        return redirect(request.path)

    if action == "save_audience":
        try:
            row = update_automation_audience(
                audience_type=request.POST.get("audience_type") or "",
                last_purchase_days=request.POST.get("last_purchase_days") or "",
                shop_id=request.POST.get("shop_id") or "",
            )
        except ValidationError as exc:
            return fail("; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        filters = constrain_filters_to_profile(
            {
                "audience_type": row.automation_audience_type,
                "last_purchase_days": row.automation_last_purchase_days,
                "shop_id": row.automation_shop_id or "",
            },
            profile,
        )
        people = _people_payload(profile, filters)
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": "Audience saved.",
                    **communications_settings_as_dict(row),
                    **people,
                }
            )
        messages.success(request, "Audience saved.")
        return redirect(request.path)

    if action == "preview_audience":
        filters = _audience_filters_from_post(request)
        people = _people_payload(profile, filters)
        share_items, share_total = _share_item_rows(filters)
        companion_rows = []
        include_companions = (request.POST.get("include_companions") or "").strip().lower() in {
            "1",
            "true",
            "on",
            "yes",
        }
        if include_companions:
            selected = _selected_item_ids(request, key="selected_item_ids")
            if not selected:
                selected = [row["id"] for row in share_items if row.get("checked")]
            companion_rows = _companion_item_rows(selected, filters)
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    **people,
                    "share_items": share_items,
                    "share_item_total": share_total,
                    "companion_items": companion_rows,
                }
            )
        return redirect(request.path)

    if action == "send_website":
        if not can_send:
            return fail("You do not have permission to send.", status=403)
        try:
            campaign = _send_shop_website_campaign(request, profile)
        except ValueError as exc:
            return fail(str(exc))
        except RuntimeError as exc:
            return fail(str(exc), status=503)
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": f"Queued {campaign.recipient_count} message(s).",
                    "campaign": campaign_as_dict(campaign),
                    "sends": recent_campaigns(8),
                }
            )
        messages.success(
            request, f"Queued {campaign.recipient_count} shop website message(s)."
        )
        return redirect(request.path)

    if action == "send_catalogue":
        if not can_send:
            return fail("You do not have permission to send.", status=403)
        try:
            campaigns = _send_catalogue_campaign(request, profile)
        except ValueError as exc:
            return fail(str(exc))
        except RuntimeError as exc:
            return fail(str(exc), status=503)
        campaign = campaigns[0]
        queued = campaign.messages.count()
        extra = len(campaigns) - 1
        if extra:
            notice = (
                f"Queued {queued} message(s) now. "
                f"{extra} more send(s) are scheduled in this period."
            )
        else:
            notice = f"Queued {queued} message(s)."
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": notice,
                    "campaign": campaign_as_dict(campaign),
                    "scheduled_sends": extra,
                    "sends": recent_campaigns(8),
                }
            )
        messages.success(request, notice)
        return redirect(request.path)

    if action == "refresh_sends":
        try:
            sync_outbound_delivery_status(force=True)
        except Exception:
            pass
        if wants_json:
            return JsonResponse({"ok": True, "sends": recent_campaigns(8), "activities": activities_payload()})
        return redirect(request.path)

    if action == "cancel_campaign":
        if not can_send:
            return fail("You do not have permission to cancel.", status=403)
        try:
            campaign_id = int(request.POST.get("campaign_id") or 0)
        except (TypeError, ValueError):
            campaign_id = 0
        try:
            campaign = cancel_campaign(campaign_id)
        except ValueError as exc:
            return fail(str(exc))
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": "Send cancelled. Messages already handed to Twilio cannot be recalled.",
                    "campaign": campaign_as_dict(campaign),
                    "sends": recent_campaigns(8),
                    "activities": activities_payload(),
                }
            )
        messages.success(request, "Send cancelled.")
        return redirect(request.path)

    if action == "retry_failed":
        if not can_send:
            return fail("You do not have permission to send.", status=403)
        try:
            campaign_id = int(request.POST.get("campaign_id") or 0)
        except (TypeError, ValueError):
            campaign_id = 0
        try:
            campaign = retry_failed_campaign(campaign_id)
        except ValueError as exc:
            return fail(str(exc))
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": "Retrying failed messages.",
                    "campaign": campaign_as_dict(campaign),
                    "sends": recent_campaigns(8),
                    "activities": activities_payload(),
                }
            )
        messages.success(request, "Retrying failed messages.")
        return redirect(request.path)

    return fail("Unknown action.")


def _send_shop_website_campaign(request, profile):
    row = get_communications_settings()
    if not row.has_twilio_credentials():
        raise ValueError("Save Twilio credentials in Settings first.")
    shops = [shop for shop in actionable_shops_for_profile(profile)]
    if row.automation_shop_id:
        shops = [shop for shop in shops if shop.pk == row.automation_shop_id]
    if not shops:
        raise ValueError("No public shops match this audience.")
    selected_ids = _selected_client_ids(request)
    if not selected_ids:
        raise ValueError("Select at least one person to send to.")
    lines = [
        "Hi {first_name},",
        "",
        "Browse items and prices on our shop website:",
        "",
    ]
    for shop in shops:
        url = request.build_absolute_uri(
            reverse("employees:shop_website", kwargs={"shop_id": shop.pk})
        )
        lines.append(f"{shop.name}: {url}")
    filters = constrain_filters_to_profile(
        {
            "audience_type": row.automation_audience_type or "sale",
            "last_purchase_days": row.automation_last_purchase_days or "",
            "shop_id": row.automation_shop_id or "",
            "client_ids": selected_ids,
        },
        profile,
    )
    return create_campaign(
        profile=profile,
        body_template="\n".join(lines),
        filters=filters,
    )


def _selected_item_ids(request, key: str = "item_ids") -> list[int]:
    raw = request.POST.getlist(key)
    if not raw and request.POST.get(key):
        raw = [request.POST.get(key)]
    ids = []
    seen = set()
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def _item_share_row(item, *, checked=False, source="bought") -> dict:
    from decimal import Decimal, InvalidOperation

    try:
        price = item.resolve_list_price()
    except (InvalidOperation, TypeError, ValueError):
        price = Decimal("0")
    try:
        amount = Decimal(price or 0).quantize(Decimal("1"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    image_url = item.public_image_url()
    return {
        "id": item.pk,
        "name": (item.name or "").strip() or "Item",
        "category": (item.category or "").strip(),
        "price_label": f"KSh {amount:,.0f}",
        "image_url": image_url,
        "checked": bool(checked),
        "source": source,
    }


def _catalogue_item_rows(*, limit: int = 200, item_ids: list[int] | None = None) -> tuple[list[dict], int]:
    from items.models import Item

    qs = Item.objects.filter(is_suspended=False)
    if item_ids is not None:
        wanted = [pk for pk in item_ids if pk]
        qs = qs.filter(pk__in=wanted)
        by_id = {item.pk: item for item in qs}
        rows = [
            _item_share_row(by_id[pk], checked=False, source="catalogue")
            for pk in wanted
            if pk in by_id
        ]
        return rows, len(rows)
    qs = qs.order_by("name", "id")
    total = qs.count()
    return [_item_share_row(item, source="catalogue") for item in qs[:limit]], total


def _share_item_rows(filters: dict | None, *, limit: int = 80) -> tuple[list[dict], int]:
    from items.models import Item

    scoped = parse_filters_safe(filters)
    wanted = bought_item_ids_for_filters(scoped, limit=limit)
    filter_ids = set(scoped.get("item_ids") or [])
    rows = []
    if wanted:
        by_id = {
            item.pk: item
            for item in Item.objects.filter(pk__in=wanted, is_suspended=False)
        }
        for pk in wanted:
            item = by_id.get(pk)
            if not item:
                continue
            rows.append(
                _item_share_row(
                    item,
                    checked=pk in filter_ids,
                    source="filter" if pk in filter_ids else "bought",
                )
            )
    if not rows:
        rows, _ = _catalogue_item_rows(limit=limit)
        for row in rows:
            row["checked"] = row["id"] in filter_ids
            row["source"] = "catalogue"
    return rows, len(rows)


def parse_filters_safe(filters: dict | None) -> dict:
    from .services import parse_filters

    return parse_filters(filters)


def _companion_item_rows(item_ids: list[int], filters: dict | None) -> list[dict]:
    from items.models import Item

    scoped = parse_filters_safe(filters)
    ids = companion_item_ids(
        item_ids,
        shop_id=scoped.get("shop_id"),
        shop_ids=scoped.get("shop_ids"),
        limit=8,
    )
    exclude = set(item_ids or [])
    ids = [pk for pk in ids if pk not in exclude]
    if not ids:
        return []
    by_id = {
        item.pk: item for item in Item.objects.filter(pk__in=ids, is_suspended=False)
    }
    return [
        _item_share_row(by_id[pk], checked=False, source="companion")
        for pk in ids
        if pk in by_id
    ]


def _send_catalogue_campaign(request, profile):
    from items.models import Item

    row = get_communications_settings()
    if not row.has_twilio_credentials():
        raise ValueError("Save Twilio credentials in Settings first.")
    item_ids = _selected_item_ids(request)
    if not item_ids:
        raise ValueError("Select at least one item to share.")
    selected_ids = _selected_client_ids(request)
    if not selected_ids:
        raise ValueError("Select at least one person to send to.")
    body_template = (request.POST.get("message_body") or "").strip()
    if len(body_template) > WHATSAPP_TEXT_LIMIT:
        raise ValueError("That message is too long for WhatsApp.")
    items = list(
        Item.objects.filter(pk__in=item_ids, is_suspended=False).order_by("name", "id")
    )
    by_id = {item.pk: item for item in items}
    ordered = [by_id[pk] for pk in item_ids if pk in by_id]
    audience = _audience_filters_from_post(request)
    try:
        period_days = int(request.POST.get("schedule_period") or 1)
    except (TypeError, ValueError):
        period_days = 1
    try:
        times = int(request.POST.get("schedule_times") or 1)
    except (TypeError, ValueError):
        times = 1
    filters = constrain_filters_to_profile(
        {
            "audience_type": audience.get("audience_type")
            or row.automation_audience_type
            or "sale",
            "last_purchase_days": audience.get("last_purchase_days")
            if audience.get("last_purchase_days") is not None
            else row.automation_last_purchase_days or "",
            "shop_id": audience.get("shop_id") or row.automation_shop_id or "",
            "client_ids": selected_ids,
        },
        profile,
    )
    campaigns = create_catalogue_campaign_series(
        profile=profile,
        items=ordered,
        filters=filters,
        request=request,
        period_days=period_days,
        times=times,
        body_template=body_template,
    )
    if not campaigns:
        raise ValueError("Could not queue this share.")
    return campaigns


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
    profile, deny = _guard(request, role_segment, as_json=True, submodule="inbox")
    if deny:
        return deny

    if request.method == "GET":
        try:
            sync_inbound_replies()
        except Exception:
            logger.exception("Twilio inbound sync failed")
        payload = inbox_threads(mark_read=False)
        phone = (request.GET.get("phone") or "").strip()
        if phone:
            mark_phone_read(phone)
            payload = inbox_threads(mark_read=False)
        return JsonResponse(payload)

    action = ""
    phone = ""
    body = ""
    image = None
    if request.content_type and "multipart/form-data" in (request.content_type or ""):
        action = (request.POST.get("action") or "send").strip()
        phone = request.POST.get("phone") or ""
        body = request.POST.get("body") or ""
        image = request.FILES.get("image")
    else:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        action = str(payload.get("action") or "mark_read").strip()
        phone = payload.get("phone") or ""
        body = payload.get("body") or ""

    if action == "send":
        denied = require_module_permission(
            request, profile, "whatsapp", "send", as_json=True
        )
        if denied:
            return denied
        try:
            send_inbox_reply(
                profile=profile,
                phone=phone,
                body=body,
                image=image,
                request=request,
            )
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        mark_phone_read(phone)
        payload = inbox_threads(mark_read=False)
        payload["ok"] = True
        return JsonResponse(payload)

    if action in {"mark_read", "read"}:
        mark_phone_read(phone)
        return JsonResponse(inbox_threads(mark_read=False))

    return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)


@active_employee_required
@require_GET
def communications_api_analytics(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True, submodule="analytics")
    if deny:
        return deny
    return JsonResponse(analytics_payload())


@active_employee_required
@require_POST
def communications_api_logout(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True, submodule="connect")
    if deny:
        return deny
    result = logout_bridge()
    return JsonResponse({"ok": result.get("ok", False), **fetch_bridge_status(), "error": result.get("error") or ""})


@active_employee_required
@require_GET
def communications_api_recipients(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True, submodule="send")
    if deny:
        return deny
    filters = constrain_filters_to_profile(_filters_from_request(request), profile)
    payload = recipients_payload(filters, sample=500)
    return JsonResponse(payload)


@active_employee_required
@require_http_methods(["GET", "POST"])
def communications_api_preview(request, role_segment):
    profile, deny = _guard(request, role_segment, as_json=True, submodule="send")
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
        filters = constrain_filters_to_profile(
            payload.get("filters") or _filters_from_request(request), profile
        )
        client_id = payload.get("client_id")
        destination_key = payload.get("destination_key")
    else:
        template = request.POST.get("body") or request.GET.get("body") or ""
        filters = constrain_filters_to_profile(_filters_from_request(request), profile)
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
    profile, deny = _guard(request, role_segment, as_json=True, submodule="send")
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
    profile, deny = _guard(request, role_segment, as_json=True, submodule="send")
    if deny:
        return deny
    campaign = BroadcastCampaign.objects.filter(pk=campaign_id).first()
    if campaign is None:
        return JsonResponse({"ok": False, "error": "Campaign not found."}, status=404)
    return JsonResponse({"ok": True, "campaign": campaign_as_dict(campaign)})


@csrf_exempt
@require_POST
def twilio_status_callback(request):
    """Public Twilio Message status webhook (sent / delivered / read / failed)."""
    from django.conf import settings

    token = (get_communications_settings().twilio_auth_token or "").strip()
    signature = request.headers.get("X-Twilio-Signature") or ""
    if token and signature:
        if not request_signature_ok(request, token, signature):
            return HttpResponse(status=403)
    elif token and not settings.DEBUG:
        return HttpResponse(status=403)

    sid = request.POST.get("MessageSid") or request.POST.get("SmsSid") or ""
    status = request.POST.get("MessageStatus") or request.POST.get("SmsStatus") or ""
    apply_message_status(
        message_sid=sid,
        status=status,
        error_code=request.POST.get("ErrorCode") or "",
        error_message=request.POST.get("ErrorMessage") or "",
    )
    return HttpResponse(status=204)


@csrf_exempt
@require_POST
def twilio_inbound_callback(request):
    """Public Twilio webhook for customer WhatsApp replies."""
    from django.conf import settings

    token = (get_communications_settings().twilio_auth_token or "").strip()
    signature = request.headers.get("X-Twilio-Signature") or ""
    if token and signature:
        if not request_signature_ok(request, token, signature):
            return HttpResponse(status=403)
    elif token and not settings.DEBUG:
        return HttpResponse(status=403)

    record_inbound_reply(
        message_sid=request.POST.get("MessageSid") or request.POST.get("SmsSid") or "",
        from_value=request.POST.get("From") or "",
        wa_id=request.POST.get("WaId") or "",
        body=request.POST.get("Body") or "",
        sender_name=request.POST.get("ProfileName") or "",
        num_media=request.POST.get("NumMedia") or 0,
    )
    return HttpResponse("<Response></Response>", content_type="text/xml")
