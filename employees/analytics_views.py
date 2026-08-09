"""Analytics workspace module views."""

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .access import (
    active_employee_required,
    get_profile_for_request,
    role_from_url_segment,
    role_url_segment,
)
from .analytics_services import (
    apply_account_payment,
    build_analytics_page,
    build_analytics_receipts_list,
    build_client_credit_account,
    build_supplier_account,
    get_analytics_receipt_kind,
    get_analytics_section,
)
from .workspace import get_dashboard_module, sidebar_for_analytics
from shops.daraja_stk import stk_ready as daraja_stk_ready
from shops.daraja_stk import sync_callback_base_from_request
from shops.services import get_daraja_settings


def _analytics_meta(section):
    return {
        "title": f"{section['label']} · Analytics",
        "headline": section["label"],
        "summary": section["summary"],
        "icon": section["icon"],
    }


def _pay_url_for(profile):
    from django.urls import reverse

    return reverse(
        "employees:analytics_account_pay",
        kwargs={"role_segment": role_url_segment(profile.role)},
    )


def _stk_urls_for(profile):
    from django.urls import reverse

    segment = role_url_segment(profile.role)
    status_template = reverse(
        "employees:analytics_account_pay_stk_status",
        kwargs={
            "role_segment": segment,
            "payment_id": "00000000-0000-0000-0000-000000000000",
        },
    ).replace("00000000-0000-0000-0000-000000000000", "__ID__")
    return {
        "stk_initiate_url": reverse(
            "employees:analytics_account_pay_stk",
            kwargs={"role_segment": segment},
        ),
        "stk_status_url_template": status_template,
    }


def _render_analytics(request, profile, *, section_slug="overview"):
    from .module_permissions import require_module_permission

    denied = require_module_permission(
        request, profile, "analytics", section_slug
    )
    if denied is not None:
        return denied

    section = get_analytics_section(section_slug)
    module = get_dashboard_module("analytics", profile.role) or {
        "slug": "analytics",
        "label": "Analytics",
        "icon": "bar-chart-3",
        "summary": section["summary"],
    }
    context = build_analytics_page(
        profile=profile, request=request, section_slug=section["slug"]
    )
    return render(
        request,
        "employees/analytics.html",
        {
            "profile": profile,
            "meta": _analytics_meta(section),
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_analytics(
                profile.role, active_view=section["slug"], profile=profile
            ),
            **context,
        },
    )


def analytics_dashboard(request, profile, meta, module, page_sidebar=None):
    """Overview analytics page (/…/analytics/)."""
    return _render_analytics(request, profile, section_slug="overview")


@active_employee_required
@require_GET
def analytics_section(request, role_segment, section):
    """Dedicated analytics page for one domain (/…/analytics/<section>/)."""
    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect(
            "employees:analytics_section",
            role_segment=expected,
            section=section,
        )

    module = get_dashboard_module("analytics", profile.role)
    if module is None:
        from django.http import Http404

        raise Http404("Module not found.")

    return _render_analytics(request, profile, section_slug=section)


@active_employee_required
@require_GET
def analytics_receipts_list(request, role_segment, kind):
    """Receipt list for one document type (/…/analytics/receipts/<kind>/)."""
    from .workspace import analytics_section_url

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect(
            "employees:analytics_receipts_list",
            role_segment=expected,
            kind=kind,
        )

    module = get_dashboard_module("analytics", profile.role)
    if module is None:
        from django.http import Http404

        raise Http404("Module not found.")

    from .module_permissions import require_module_permission

    denied = require_module_permission(request, profile, "analytics", "receipts")
    if denied is not None:
        return denied

    kind_spec = get_analytics_receipt_kind(kind)
    context = build_analytics_receipts_list(
        profile=profile, request=request, kind=kind_spec["slug"]
    )
    back_href = analytics_section_url(profile.role, "receipts")
    query = request.GET.urlencode()
    if query:
        back_href = f"{back_href}?{query}"

    return render(
        request,
        "employees/analytics_receipts_list.html",
        {
            "profile": profile,
            "meta": {
                "title": f"{kind_spec['label']} · Analytics",
                "headline": kind_spec["label"],
                "summary": "Browse matching receipts across selected shops.",
                "icon": "receipt",
            },
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_analytics(
                profile.role, active_view="receipts", profile=profile
            ),
            "back_href": back_href,
            "back_label": "Back to receipts",
            **context,
        },
    )


@active_employee_required
@require_GET
def analytics_client_credit(request, role_segment, client_id):
    """Client account ledger (/…/analytics/clients/<id>/)."""
    from .workspace import analytics_section_url

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect(
            "employees:analytics_client_account",
            role_segment=expected,
            client_id=client_id,
        )

    module = get_dashboard_module("analytics", profile.role)
    if module is None:
        from django.http import Http404

        raise Http404("Module not found.")

    from_credits = "/analytics/credits/" in (request.path or "")
    back_section = "credits" if from_credits else "clients"

    from .module_permissions import require_module_permission

    denied = require_module_permission(request, profile, "analytics", back_section)
    if denied is not None:
        return denied

    account = build_client_credit_account(profile=profile, client_id=client_id)
    query = request.GET.urlencode()
    back_href = analytics_section_url(profile.role, back_section)
    if query:
        back_href = f"{back_href}?{query}"

    return render(
        request,
        "employees/analytics_client_credit.html",
        {
            "profile": profile,
            "meta": {
                "title": f"{account['client'].full_name} · Client account",
                "headline": "Client account",
                "summary": "Open credit receipts and outstanding balance for this client.",
                "icon": "contact",
            },
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_analytics(
                profile.role, active_view=back_section, profile=profile
            ),
            "back_href": back_href,
            "back_label": "Back to credits" if from_credits else "Back to clients",
            "pay_url": _pay_url_for(profile),
            "stk_ready": daraja_stk_ready(),
            "stk_off_label": (
                get_daraja_settings().stk_not_ready_reason() or "STK off"
            ),
            "client_phone": account["client"].phone_number,
            **_stk_urls_for(profile),
            **account,
        },
    )


@active_employee_required
@require_GET
def analytics_supplier_account(request, role_segment, kind, supplier_id):
    """Supplier account ledger (/…/analytics/suppliers/<kind>/<id>/)."""
    from .workspace import analytics_section_url

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect(
            "employees:analytics_supplier_account",
            role_segment=expected,
            kind=kind,
            supplier_id=supplier_id,
        )

    module = get_dashboard_module("analytics", profile.role)
    if module is None:
        from django.http import Http404

        raise Http404("Module not found.")

    from .module_permissions import require_module_permission

    denied = require_module_permission(request, profile, "analytics", "suppliers")
    if denied is not None:
        return denied

    account = build_supplier_account(
        profile=profile, kind=kind, supplier_id=supplier_id
    )
    query = request.GET.urlencode()
    from_expenses = "/analytics/expenses" in (request.META.get("HTTP_REFERER") or "")
    back_section = "expenses" if from_expenses else "suppliers"
    # Prefer explicit next= query, else infer from referer / kind usage.
    next_section = (request.GET.get("from") or "").strip().lower()
    if next_section in ("expenses", "suppliers"):
        back_section = next_section
    back_href = analytics_section_url(profile.role, back_section)
    if query:
        # Drop helper params from preserved filters when going back.
        from urllib.parse import parse_qs, urlencode

        params = parse_qs(query, keep_blank_values=True)
        params.pop("from", None)
        cleaned = urlencode({k: v[0] for k, v in params.items()})
        if cleaned:
            back_href = f"{back_href}?{cleaned}"

    return render(
        request,
        "employees/analytics_supplier_account.html",
        {
            "profile": profile,
            "meta": {
                "title": f"{account['supplier_name']} · Supplier account",
                "headline": "Supplier account",
                "summary": "Purchases or expenses and outstanding balance for this supplier.",
                "icon": "truck",
            },
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_analytics(
                profile.role, active_view=back_section, profile=profile
            ),
            "back_href": back_href,
            "back_label": (
                "Back to expenses" if back_section == "expenses" else "Back to suppliers"
            ),
            "pay_url": _pay_url_for(profile),
            **account,
        },
    )


@active_employee_required
@require_POST
def analytics_account_pay(request, role_segment):
    """Apply an account payment (FIFO across receipts, oldest first)."""
    from .module_permissions import require_module_permission

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return JsonResponse({"ok": False, "error": "Wrong portal."}, status=403)

    denied = require_module_permission(
        request, profile, "analytics", "account_pay", as_json=True
    )
    if denied is not None:
        return denied

    kind = (request.POST.get("kind") or "").strip().lower()
    try:
        account_id = int(request.POST.get("account_id") or 0)
    except (TypeError, ValueError):
        account_id = 0
    payment_method = (request.POST.get("payment_method") or "cash").strip().lower()
    stk_payment_id = (request.POST.get("stk_payment_id") or "").strip()

    try:
        result = apply_account_payment(
            profile=profile,
            kind=kind,
            account_id=account_id,
            amount=request.POST.get("amount"),
            payment_method=payment_method,
            stk_payment_id=stk_payment_id,
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)

    return JsonResponse(result)


@active_employee_required
@require_POST
def analytics_account_pay_stk(request, role_segment):
    """Start STK Push for a client credit account payment."""
    from shops.daraja_stk import (
        initiate_stk_push,
        stk_payment_payload,
        stk_ready,
        sync_callback_base_from_request,
    )
    from shops.models import Client

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")
    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return JsonResponse({"ok": False, "error": "Wrong portal."}, status=403)

    from .module_permissions import require_module_permission

    denied = require_module_permission(
        request, profile, "analytics", "account_pay", as_json=True
    )
    if denied is not None:
        return denied

    sync_callback_base_from_request(request, persist=True)
    if not stk_ready():
        return JsonResponse(
            {"ok": False, "error": "STK Push is not enabled in Daraja settings."},
            status=400,
        )

    kind = (request.POST.get("kind") or "").strip().lower()
    if kind != "credit":
        return JsonResponse(
            {"ok": False, "error": "M-Pesa STK is only available for client credit accounts."},
            status=400,
        )
    try:
        account_id = int(request.POST.get("account_id") or 0)
    except (TypeError, ValueError):
        account_id = 0
    client = Client.objects.filter(pk=account_id).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Client not found."}, status=404)
    phone = (request.POST.get("phone") or client.phone_number or "").strip()
    if not phone:
        return JsonResponse(
            {"ok": False, "error": "Client phone is required for M-Pesa STK Push."},
            status=400,
        )

    try:
        payment = initiate_stk_push(
            purpose="credit",
            amount=request.POST.get("amount"),
            phone=phone,
            account_reference=f"CR{client.pk}",
            description=f"Credit {client.full_name}"[:40],
            profile=profile,
            account_kind="credit",
            account_id=client.pk,
            request=request,
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "message": "STK Push sent. Ask the customer to enter their M-Pesa PIN.",
            **stk_payment_payload(payment),
        }
    )


@active_employee_required
@require_GET
def analytics_account_pay_stk_status(request, role_segment, payment_id):
    """Poll STK status for analytics account payments."""
    from shops.daraja_stk import get_stk_payment, stk_payment_payload

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")
    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return JsonResponse({"ok": False, "error": "Wrong portal."}, status=403)

    payment = get_stk_payment(payment_id)
    if payment is None:
        return JsonResponse({"ok": False, "error": "STK payment not found."}, status=404)
    return JsonResponse({"ok": True, **stk_payment_payload(payment)})


def _analytics_receipt_shop_access(profile, shop_id):
    """Resolve a shop the analytics viewer may open receipts for."""
    from items.services import actionable_shops_for_profile
    from shops.models import Shop

    shop_ids = {shop.pk for shop in actionable_shops_for_profile(profile)}
    if int(shop_id) not in shop_ids:
        return None
    return Shop.objects.filter(
        pk=shop_id, is_hidden=False, is_suspended=False
    ).first()


def _analytics_role_or_error(request, role_segment, *, as_json=False):
    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")
    expected = role_url_segment(profile.role)
    if role_segment != expected:
        if as_json:
            return profile, JsonResponse(
                {"ok": False, "error": "Wrong portal."}, status=403
            )
        return profile, redirect(
            request.path.replace(f"/{role_segment}/", f"/{expected}/", 1)
        )
    return profile, None


@active_employee_required
@require_GET
def analytics_receipt_detail(request, role_segment, shop_id, receipt_id):
    """JSON receipt detail for analytics return/reprint modal."""
    from .module_permissions import require_module_permission
    from shops.services import get_shop_receipt_detail

    profile, err = _analytics_role_or_error(request, role_segment, as_json=True)
    if err is not None:
        return err

    denied = require_module_permission(
        request, profile, "analytics", "receipts", as_json=True
    )
    if denied is not None:
        return denied

    shop = _analytics_receipt_shop_access(profile, shop_id)
    if shop is None:
        return JsonResponse({"ok": False, "error": "Shop not found."}, status=404)

    try:
        payload = get_shop_receipt_detail(shop=shop, receipt_id=receipt_id)
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=404)
    return JsonResponse(payload)


@active_employee_required
@require_POST
def analytics_receipt_return(request, role_segment, shop_id, receipt_id):
    """Return items from a receipt opened in analytics (staff ID required)."""
    import json

    from employees.module_permissions import require_module_permission
    from employees.services import verify_active_employee_code
    from shops.services import return_shop_receipt_items

    profile, err = _analytics_role_or_error(request, role_segment, as_json=True)
    if err is not None:
        return err

    denied = require_module_permission(
        request, profile, "analytics", "receipts", as_json=True
    )
    if denied is not None:
        return denied

    shop = _analytics_receipt_shop_access(profile, shop_id)
    if shop is None:
        return JsonResponse({"ok": False, "error": "Shop not found."}, status=404)

    try:
        if request.content_type and "application/json" in request.content_type:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        else:
            payload = {
                "login_code": request.POST.get("login_code"),
                "lines": json.loads(request.POST.get("lines") or "[]"),
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid return payload."}, status=400)

    actor = verify_active_employee_code(payload.get("login_code"))
    if actor is None:
        return JsonResponse(
            {"ok": False, "error": "Enter a valid active staff 6-digit ID."},
            status=403,
        )
    denied = require_module_permission(
        request, actor, "my-shop", "return_receipt", as_json=True
    )
    if denied is not None:
        return denied

    try:
        result = return_shop_receipt_items(
            shop=shop, receipt_id=receipt_id, payload=payload
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)
    return JsonResponse(result)


@active_employee_required
@require_POST
def analytics_receipt_verify_login(request, role_segment):
    """Validate staff 6-digit ID before confirming an analytics receipt return."""
    from employees.module_permissions import my_shop_capabilities
    from employees.services import verify_active_employee_code

    profile, err = _analytics_role_or_error(request, role_segment, as_json=True)
    if err is not None:
        return err

    code = (
        request.POST.get("login_code") or request.POST.get("employee_code") or ""
    ).strip()
    authorising = verify_active_employee_code(code)
    if authorising is None:
        return JsonResponse(
            {"ok": False, "error": "Not a valid active staff ID."},
            status=400,
        )
    name = authorising.user.get_full_name() or authorising.user.username
    return JsonResponse(
        {
            "ok": True,
            "employee_id": authorising.employee_id,
            "name": name,
            "capabilities": my_shop_capabilities(authorising),
        }
    )
