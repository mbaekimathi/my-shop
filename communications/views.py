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
from employees.workspace import get_dashboard_module, sidebar_for_communications

from .analytics import analytics_payload
from .twilio import (
    apply_message_status,
    bridge_deploy_hints,
    fetch_bridge_status,
    logout_bridge,
    request_signature_ok,
    sandbox_join_info,
    sync_outbound_delivery_status,
)
from .campaigns import (
    campaign_as_dict,
    cancel_campaign,
    create_campaign,
    create_catalogue_campaign,
    recent_campaigns,
    retry_failed_campaign,
)
from .constants import (
    AUDIENCE_TYPE_CHOICES,
    LAST_PURCHASE_WINDOWS,
    PLACEHOLDERS,
    SPEND_TIER_CHOICES,
    TRANSACTION_MIN_CHOICES,
)
from .models import BroadcastCampaign
from .replies import inbox_threads, unread_reply_count
from .services import (
    audience_summary,
    constrain_filters_to_profile,
    list_product_categories,
    preview_message,
    recipients_payload,
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
    """Main /…/whatsapp/ page: what to automate and who to share with."""
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
    settings_url = reverse("employees:settings_section", kwargs={"section": "whatsapp"})
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
        or sidebar_for_communications(profile.role, active_view="home", profile=profile),
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
    """Manual item shares plus automatic send when a new item is registered."""
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
        "summary": "Send item photos and prices on WhatsApp, or auto-share new items.",
        "icon": "images",
    }
    try:
        sync_outbound_delivery_status()
    except Exception:
        logger.exception("Twilio delivery sync failed")
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
    items, item_total = _catalogue_item_rows()
    settings_url = reverse("employees:settings_section", kwargs={"section": "whatsapp"})
    whatsapp_url = reverse(
        "employees:workspace_module",
        kwargs={"role_segment": role_segment, "module_slug": "whatsapp"},
    )
    context = {
        "profile": profile,
        "meta": meta,
        "module": module,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_communications(
            profile.role, active_view="catalogue", profile=profile
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
        "catalogue_items": items,
        "catalogue_item_total": item_total,
        "recent_sends": recent_campaigns(8),
        "settings_url": settings_url,
        "whatsapp_url": whatsapp_url,
        "module_permissions": module_capabilities(profile, "whatsapp"),
        "sandbox_join": sandbox_join_info(),
    }
    return render(request, "employees/whatsapp_catalogue.html", context)


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
    return {
        "audience_type": request.POST.get("audience_type") or "",
        "last_purchase_days": request.POST.get("last_purchase_days") or "",
        "shop_id": request.POST.get("shop_id") or "",
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
        people = _people_payload(profile, _audience_filters_from_post(request))
        if wants_json:
            return JsonResponse({"ok": True, **people})
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
            campaign = _send_catalogue_campaign(request, profile)
        except ValueError as exc:
            return fail(str(exc))
        except RuntimeError as exc:
            return fail(str(exc), status=503)
        queued = campaign.messages.count()
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "message": f"Queued {queued} message(s).",
                    "campaign": campaign_as_dict(campaign),
                    "sends": recent_campaigns(8),
                }
            )
        messages.success(request, f"Queued {queued} item message(s).")
        return redirect(request.path)

    if action == "refresh_sends":
        try:
            sync_outbound_delivery_status()
        except Exception:
            pass
        if wants_json:
            return JsonResponse({"ok": True, "sends": recent_campaigns(8)})
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


def _selected_item_ids(request) -> list[int]:
    raw = request.POST.getlist("item_ids")
    if not raw and request.POST.get("item_ids"):
        raw = [request.POST.get("item_ids")]
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


def _catalogue_item_rows(*, limit: int = 200) -> tuple[list[dict], int]:
    from decimal import Decimal, InvalidOperation

    from items.models import Item

    qs = Item.objects.filter(is_suspended=False).order_by("name", "id")
    total = qs.count()
    rows = []
    for item in qs[:limit]:
        try:
            price = item.resolve_list_price()
        except (InvalidOperation, TypeError, ValueError):
            price = Decimal("0")
        try:
            amount = Decimal(price or 0).quantize(Decimal("1"))
        except (InvalidOperation, TypeError, ValueError):
            amount = Decimal("0")
        image_url = ""
        try:
            if item.image:
                image_url = item.image.url or ""
        except Exception:
            image_url = ""
        rows.append(
            {
                "id": item.pk,
                "name": (item.name or "").strip() or "Item",
                "category": (item.category or "").strip(),
                "price_label": f"KSh {amount:,.0f}",
                "image_url": image_url,
            }
        )
    return rows, total


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
    items = list(
        Item.objects.filter(pk__in=item_ids, is_suspended=False).order_by("name", "id")
    )
    by_id = {item.pk: item for item in items}
    ordered = [by_id[pk] for pk in item_ids if pk in by_id]
    filters = constrain_filters_to_profile(
        {
            "audience_type": row.automation_audience_type or "sale",
            "last_purchase_days": row.automation_last_purchase_days or "",
            "shop_id": row.automation_shop_id or "",
            "client_ids": selected_ids,
        },
        profile,
    )
    return create_catalogue_campaign(
        profile=profile,
        items=ordered,
        filters=filters,
        request=request,
    )


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
    mark_read = request.method == "POST" or request.GET.get("mark_read") in {
        "1",
        "true",
        "yes",
    }
    return JsonResponse(inbox_threads(mark_read=mark_read))


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
