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
    ANALYTICS_DASHBOARD_SECTION_SLUGS,
    ANALYTICS_RECEIPT_KINDS,
    _date_filter_context,
    apply_account_payment,
    apply_credit_receipt_payment,
    apply_supplier_receipt_payment,
    build_analytics_page,
    build_analytics_receipts_list,
    build_client_credit_account,
    build_supplier_account,
    client_credit_account_url,
    get_analytics_receipt_kind,
    get_analytics_section,
    update_credit_receipt_due_date,
)
from shops.credit_audit import client_credit_audit_url
from .workspace import (
    get_dashboard_module,
    sidebar_for_analytics,
    sidebar_for_client_credit_account,
    sidebar_for_credits,
    sidebar_for_role_dashboard,
    sidebar_for_stock,
    sidebar_for_suppliers,
)
from shops.daraja_stk import stk_ready as daraja_stk_ready
from shops.daraja_stk import sync_callback_base_from_request
from shops.services import get_daraja_settings


def _client_credit_nav(profile, client_id):
    from django.urls import reverse

    from shops.credit_audit import client_credit_audit_url

    segment = role_url_segment(profile.role)
    kwargs = {"role_segment": segment, "client_id": client_id}
    return {
        "account_href": reverse("employees:analytics_client_account", kwargs=kwargs),
        "audit_href": client_credit_audit_url(profile.role, client_id),
    }


def _client_credit_sidebar(profile, *, client_name, client_id, active):
    nav = _client_credit_nav(profile, client_id)
    return sidebar_for_client_credit_account(
        profile.role,
        client_name=client_name,
        account_href=nav["account_href"],
        audit_href=nav["audit_href"],
        profile=profile,
        active=active,
    )


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


def _credit_receipt_update_url_template(profile):
    from django.urls import reverse

    segment = role_url_segment(profile.role)
    template = reverse(
        "employees:analytics_credit_receipt_update",
        kwargs={"role_segment": segment, "receipt_id": 0},
    )
    return template.replace("/0/", "/__ID__/")


def _render_analytics(request, profile, *, section_slug="overview"):
    from django.urls import reverse

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
        (
            "employees/analytics_supply.html"
            if section["slug"] == "supply"
            else "employees/analytics.html"
        ),
        {
            "profile": profile,
            "meta": _analytics_meta(section),
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "credit_audits_href": (
                reverse(
                    "employees:analytics_credit_audits",
                    kwargs={"role_segment": role_url_segment(profile.role)},
                )
                if section["slug"] == "credits"
                else ""
            ),
            "page_sidebar": (
                sidebar_for_suppliers(profile.role, profile=profile)
                if section["slug"] == "suppliers"
                else sidebar_for_credits(profile.role, profile=profile)
                if section["slug"] == "credits"
                else sidebar_for_stock(profile.role, profile=profile)
                if section["slug"] == "stock"
                else sidebar_for_role_dashboard(
                    profile.role,
                    profile=profile,
                    active_slug=section["slug"],
                    omit_dashboard_link=False,
                )
                if section["slug"] in ANALYTICS_DASHBOARD_SECTION_SLUGS
                else sidebar_for_analytics(
                    profile.role, active_view=section["slug"], profile=profile
                )
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
            "kind_options": list(ANALYTICS_RECEIPT_KINDS.values()),
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
    from shops.credit_note import credit_note_share_context
    from shops.credit_audit import build_client_credit_audit_trail

    share_context = credit_note_share_context(
        request=request,
        client_id=client_id,
        client_name=account["client"].full_name,
        balance=account["balance"],
    )
    audit_trail = build_client_credit_audit_trail(
        profile=profile, client_id=client_id
    )
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
            "page_sidebar": _client_credit_sidebar(
                profile,
                client_name=account["client"].full_name,
                client_id=client_id,
                active="account",
            ),
            "back_href": back_href,
            "back_label": "Back to credits" if from_credits else "Back to clients",
            "pay_url": _pay_url_for(profile),
            "stk_ready": daraja_stk_ready(),
            "stk_off_label": (
                get_daraja_settings().stk_not_ready_reason() or "STK off"
            ),
            "client_phone": account["client"].phone_number,
            **share_context,
            **_stk_urls_for(profile),
            "receipt_update_url_template": _credit_receipt_update_url_template(profile),
            "client_credit_payments_url": (
                client_credit_account_url(
                    profile.role,
                    client_id,
                    query=query,
                    from_credits=from_credits,
                )
                + "#transactions"
            ),
            "transaction_rows": audit_trail["rows"],
            "transaction_count": audit_trail["event_count"],
            "transaction_empty_message": audit_trail["empty_message"],
            **account,
        },
    )


@active_employee_required
@require_GET
def analytics_client_credit_audit(request, role_segment, client_id):
    """Client credit audit trail (/…/analytics/clients/<id>/payments/)."""
    from .workspace import analytics_section_url
    from shops.credit_audit import build_client_credit_audit_trail

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect(
            "employees:analytics_client_credit_audit",
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

    trail = build_client_credit_audit_trail(profile=profile, client_id=client_id)
    query = request.GET.urlencode()
    back_href = analytics_section_url(profile.role, back_section)
    if query:
        back_href = f"{back_href}?{query}"

    return render(
        request,
        "employees/analytics_client_credit_audit.html",
        {
            "profile": profile,
            "meta": {
                "title": f"{trail['client'].full_name} · Credit payments",
                "headline": "Credit payments",
                "summary": "Audit trail of payments and credit-note changes for this client.",
                "icon": "history",
            },
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": _client_credit_sidebar(
                profile,
                client_name=trail["client"].full_name,
                client_id=client_id,
                active="audit",
            ),
            "back_href": back_href,
            "back_label": "Back to credits" if from_credits else "Back to clients",
            **_client_credit_nav(profile, client_id),
            **trail,
        },
    )


@active_employee_required
@require_GET
def analytics_credit_audits(request, role_segment):
    """All non-sale changes made to accessible credit receipts."""
    from .module_permissions import require_module_permission
    from .workspace import analytics_section_url
    from shops.credit_audit import build_credit_audits

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect("employees:analytics_credit_audits", role_segment=expected)

    module = get_dashboard_module("analytics", profile.role)
    if module is None:
        from django.http import Http404

        raise Http404("Module not found.")

    denied = require_module_permission(request, profile, "analytics", "credits")
    if denied is not None:
        return denied

    trail = build_credit_audits(
        profile=profile,
        request=request,
        event_kind=request.GET.get("event") or "",
    )
    return render(
        request,
        "employees/analytics_credit_audits.html",
        {
            "profile": profile,
            "meta": {
                "title": "Credit audits · Analytics",
                "headline": "Credit audits",
                "summary": "Payments and changes made to credit receipts.",
                "icon": "history",
            },
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_credits(
                profile.role, profile=profile, active="audits"
            ),
            "back_href": analytics_section_url(profile.role, "credits"),
            **trail,
        },
    )


@active_employee_required
@require_GET
def analytics_supplier_account(request, role_segment, kind, supplier_id):
    """Supplier account ledger (/…/analytics/suppliers/<kind>/<id>/)."""
    from .workspace import _with_request_query, analytics_section_url

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
        profile=profile, kind=kind, supplier_id=supplier_id, request=request
    )
    from_section = (request.GET.get("from") or "").strip().lower()
    from_expenses = "/analytics/expenses" in (request.META.get("HTTP_REFERER") or "")
    if from_section in ("expenses", "suppliers"):
        back_section = from_section
    elif (kind or "").strip().lower() == "expense" or from_expenses:
        back_section = "expenses"
    else:
        back_section = "suppliers"

    back_href = _with_request_query(
        analytics_section_url(profile.role, back_section),
        request,
        drop=("from",),
    )

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
            "page_sidebar": (
                sidebar_for_suppliers(profile.role, profile=profile)
                if back_section == "suppliers"
                else sidebar_for_analytics(
                    profile.role, active_view=back_section, profile=profile
                )
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
    try:
        receipt_id = int(request.POST.get("receipt_id") or 0)
    except (TypeError, ValueError):
        receipt_id = 0
    payment_method = (request.POST.get("payment_method") or "cash").strip().lower()
    stk_payment_id = (request.POST.get("stk_payment_id") or "").strip()

    try:
        if receipt_id and kind == "credit":
            result = apply_credit_receipt_payment(
                profile=profile,
                receipt_id=receipt_id,
                amount=request.POST.get("amount"),
                payment_method=payment_method,
                stk_payment_id=stk_payment_id,
            )
        elif receipt_id and kind in ("expense", "stock"):
            result = apply_supplier_receipt_payment(
                profile=profile,
                kind=kind,
                account_id=account_id,
                receipt_id=receipt_id,
                amount=request.POST.get("amount"),
                shop_ids=request.POST.getlist("shop_id"),
            )
        else:
            date_filter = (
                _date_filter_context(request, allow_all_time=True)
                if kind in ("expense", "stock")
                else {}
            )
            result = apply_account_payment(
                profile=profile,
                kind=kind,
                account_id=account_id,
                amount=request.POST.get("amount"),
                payment_method=payment_method,
                stk_payment_id=stk_payment_id,
                shop_ids=request.POST.getlist("shop_id"),
                start=date_filter.get("start"),
                end=date_filter.get("end"),
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
    try:
        receipt_id = int(request.POST.get("receipt_id") or 0)
    except (TypeError, ValueError):
        receipt_id = 0
    client = Client.objects.filter(pk=account_id).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Client not found."}, status=404)
    phone = (request.POST.get("phone") or client.phone_number or "").strip()
    if not phone:
        return JsonResponse(
            {"ok": False, "error": "Client phone is required for M-Pesa STK Push."},
            status=400,
        )

    receipt = None
    if receipt_id:
        from shops.models import ShopReceipt, ShopReceiptKind, ShopReceiptStatus

        receipt = (
            ShopReceipt.objects.filter(
                pk=receipt_id,
                client_id=client.pk,
                kind=ShopReceiptKind.CREDIT,
            )
            .exclude(status=ShopReceiptStatus.CANCELLED)
            .first()
        )
        if receipt is None:
            return JsonResponse(
                {"ok": False, "error": "Credit receipt not found for this client."},
                status=404,
            )

    account_reference = f"CR{client.pk}"
    description = f"Credit {client.full_name}"[:40]
    if receipt is not None:
        account_reference = f"R{receipt.pk}"[:12]
        description = f"{receipt.receipt_number} {client.full_name}"[:40]

    try:
        payment = initiate_stk_push(
            purpose="credit",
            amount=request.POST.get("amount"),
            phone=phone,
            account_reference=account_reference,
            description=description,
            profile=profile,
            account_kind="credit",
            account_id=client.pk,
            receipt=receipt,
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


@active_employee_required
@require_POST
def analytics_credit_receipt_update(request, role_segment, receipt_id):
    """Update payment due date on one credit receipt."""
    from .module_permissions import require_module_permission

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        from django.http import Http404

        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return JsonResponse({"ok": False, "error": "Wrong portal."}, status=403)

    denied = require_module_permission(
        request, profile, "analytics", "clients", as_json=True
    )
    if denied is not None:
        from .module_permissions import employee_may

        if not employee_may(profile, "analytics", "credits"):
            return denied

    try:
        result = update_credit_receipt_due_date(
            profile=profile,
            receipt_id=receipt_id,
            credit_due_date=request.POST.get("credit_due_date"),
        )
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)

    return JsonResponse(result)


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
        payload = get_shop_receipt_detail(
            shop=shop,
            receipt_id=receipt_id,
            source=request.GET.get("source") or "pos",
        )
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
