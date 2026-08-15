from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods

import re

from items.views import (
    item_management,
    item_management_catalog,
    stock_management,
    stock_management_catalog,
    stock_management_print,
    stock_serial_detail,
    stock_serial_history,
    stock_serial_return_client,
)
from shops.services import (
    communications_settings_as_dict,
    daraja_settings_as_dict,
    get_communications_settings,
    get_company_pos_settings,
    get_company_display_name,
    get_company_profile,
    get_company_working_hours_settings,
    get_daraja_settings,
    pos_settings_as_dict,
    preview_receipt_number,
    receipt_font_style,
    receipt_qr_for_settings,
    set_company_pos_setting,
    set_company_tax_percent,
    set_daraja_stk_enabled,
    set_mpesa_payment_details,
    set_receipt_font_style,
    set_receipt_number_formats,
    set_receipt_paper_width,
    set_receipt_qr_settings,
    update_company_profile,
    save_working_hours_settings,
    update_daraja_settings,
    update_message_channel_settings,
    update_sms_settings,
    update_whatsapp_settings,
)
from shops.views import shop_management

from .access import (
    active_employee_required,
    clear_profile_session,
    get_profile,
    get_profile_for_request,
    redirect_to_role_home,
    role_required,
    role_from_url_segment,
    role_url_segment,
    store_profile_session,
)
from .analytics_views import analytics_dashboard
from communications.views import communications_dashboard
from .countries import COUNTRY_DIAL_CODES
from .hr_views import hr_management_page
from .models import EmployeeProfile, EmployeeRole, EmployeeStatus, SHOP_ASSIGNABLE_ROLES
from .pagination import page_url, pagination_links, redirect_query_page
from .services import (
    EMPLOYEE_ID_RE,
    EMPTY_EMPLOYEE_FORM,
    employee_form_data_from_post,
    employee_id_is_taken,
    mark_employee_id_taken,
    register_employee,
    validate_employee_registration,
)
from .throttle import rate_limit
from .workspace import (
    ROLE_PAGE_META,
    get_dashboard_module,
    get_settings_section,
    get_settings_sections,
    sidebar_for_module,
    sidebar_for_profile,
    sidebar_for_role_dashboard,
    sidebar_for_settings,
    sidebar_for_super_admin,
)


def _redirect_after_employee_access_update(request, list_page=None):
    return_to = request.POST.get("return_to", "").strip()
    page_number = int(list_page) if list_page else 1

    if return_to == "hr_approvals":
        profile = get_profile_for_request(request)
        segment = role_url_segment(profile.role)
        return redirect(
            page_url(
                "hr_employee_approvals",
                page_number,
                url_kwargs={"role_segment": segment},
            )
        )

    if return_to == "hr_authorizations":
        profile = get_profile_for_request(request)
        segment = role_url_segment(profile.role)
        url = reverse(
            "employees:hr_section",
            kwargs={"role_segment": segment, "section": "authorizations"},
        )
        if page_number > 1:
            url = f"{url}?page={page_number}"
        return redirect(url)

    return redirect(
        page_url("role_super_admin", page_number, fragment="employee-access")
    )


def _render_role_page(request, expected_role):
    profile = get_profile_for_request(request)
    meta = ROLE_PAGE_META[expected_role]
    return render(
        request,
        "employees/role_home.html",
        {
            "profile": profile,
            "meta": meta,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_role_dashboard(expected_role, profile=profile),
        },
    )


@active_employee_required
def legacy_communications_redirect(request, role_segment):
    """Old /…/communications/ bookmarks → /…/whatsapp/."""
    return redirect(
        "employees:workspace_module",
        role_segment=role_segment,
        module_slug="whatsapp",
    )


@active_employee_required
def legacy_settings_communications_redirect(request):
    """Old /settings/communications/ → /settings/whatsapp/."""
    return redirect("employees:settings_section", section="whatsapp")


@active_employee_required
def workspace_module(request, role_segment, module_slug):
    from .module_permissions import employee_may_any, permission_denied_response

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        raise Http404("Role portal not found.")

    expected_segment = role_url_segment(profile.role)
    if role_segment != expected_segment:
        return redirect(
            "employees:workspace_module",
            role_segment=expected_segment,
            module_slug=module_slug,
        )

    module = get_dashboard_module(module_slug, profile.role)
    if module is None:
        raise Http404("Module not found.")

    if not employee_may_any(profile, module_slug):
        return permission_denied_response(
            request,
            profile,
            message=f"You do not have permission to access {module['label']}.",
        )

    page_sidebar = sidebar_for_module(profile.role, module_slug, profile=profile)
    meta = {
        "title": module["label"],
        "headline": module["label"],
        "summary": module["summary"],
        "icon": module["icon"],
    }

    if module_slug == "item-management":
        return item_management(request, profile, meta, module, page_sidebar)

    if module_slug == "stock-management":
        return stock_management(request, profile, meta, module, page_sidebar)

    if module_slug == "hr-management":
        return hr_management_page(request, role_segment=role_segment)

    if module_slug == "shop-management":
        return shop_management(request, profile, meta, module, page_sidebar)

    if module_slug == "analytics":
        return analytics_dashboard(request, profile, meta, module, page_sidebar)

    if module_slug == "whatsapp":
        return communications_dashboard(request, profile, meta, module, page_sidebar)

    return render(
        request,
        "employees/module_placeholder.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
        },
    )


@active_employee_required
@require_GET
def stock_management_catalog_proxy(request, role_segment):
    """JSON catalog for stock-management action modes."""
    return stock_management_catalog(request, role_segment)


@active_employee_required
@require_GET
def stock_management_print_proxy(request, role_segment):
    """Printable stock list for stock-management."""
    return stock_management_print(request, role_segment)


@active_employee_required
@require_GET
def stock_serial_detail_page(request, role_segment, item_id):
    """Serial numbers for one stock item (in stock vs sold)."""
    from employees.access import (
        get_profile_for_request,
        redirect_to_role_home,
        role_from_url_segment,
        role_url_segment,
    )
    from employees.models import EmployeeRole
    from employees.module_permissions import require_module_permission
    from employees.workspace import get_dashboard_module

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return redirect(
            "employees:stock_serial_detail",
            role_segment=expected,
            item_id=item_id,
        )

    module = get_dashboard_module("stock-management", profile.role)
    if module is None:
        raise Http404("Module not found.")

    denied = require_module_permission(request, profile, "stock-management", "serials")
    if denied is not None:
        return denied

    if profile.role not in (EmployeeRole.SHOP_MANAGER, EmployeeRole.IT_SUPPORT):
        return redirect_to_role_home(profile)

    meta = {
        "title": module["label"],
        "headline": module["label"],
        "summary": module["summary"],
        "icon": module["icon"],
    }
    return stock_serial_detail(request, profile, meta, module, item_id)


@active_employee_required
@require_http_methods(["GET", "POST"])
def stock_serial_history_page(request, role_segment, item_id, serial_number):
    """Movement history for one serial number from registration to now."""
    denied, profile, meta, module_or_expected = _stock_serials_page_guard(
        request, role_segment
    )
    if denied is not None:
        return denied
    if profile is None:
        return redirect(
            "employees:stock_serial_history",
            role_segment=module_or_expected,
            item_id=item_id,
            serial_number=serial_number,
        )
    return stock_serial_history(
        request, profile, meta, module_or_expected, item_id, serial_number
    )


def _stock_serials_page_guard(request, role_segment):
    """Shared auth/permission gate for serial return client pages."""
    from employees.access import (
        get_profile_for_request,
        redirect_to_role_home,
        role_from_url_segment,
        role_url_segment,
    )
    from employees.models import EmployeeRole
    from employees.module_permissions import require_module_permission
    from employees.workspace import get_dashboard_module

    profile = get_profile_for_request(request)
    if role_from_url_segment(role_segment) is None:
        raise Http404("Role portal not found.")

    expected = role_url_segment(profile.role)
    if role_segment != expected:
        return None, None, None, expected

    module = get_dashboard_module("stock-management", profile.role)
    if module is None:
        raise Http404("Module not found.")

    denied = require_module_permission(request, profile, "stock-management", "serials")
    if denied is not None:
        return denied, None, None, None

    if profile.role not in (EmployeeRole.SHOP_MANAGER, EmployeeRole.IT_SUPPORT):
        return redirect_to_role_home(profile), None, None, None

    meta = {
        "title": module["label"],
        "headline": module["label"],
        "summary": module["summary"],
        "icon": module["icon"],
    }
    return None, profile, meta, module


@active_employee_required
@require_GET
def stock_serial_return_client_page(request, role_segment, client_id):
    """Returned serial items for one registered client."""
    denied, profile, meta, module_or_expected = _stock_serials_page_guard(
        request, role_segment
    )
    if denied is not None:
        return denied
    if profile is None:
        return redirect(
            "employees:stock_serial_return_client",
            role_segment=module_or_expected,
            client_id=client_id,
        )
    return stock_serial_return_client(
        request, profile, meta, module_or_expected, client_id=client_id
    )


@active_employee_required
@require_GET
def stock_serial_return_guest_page(request, role_segment):
    """Returned serial items for a walk-in client (phone/name)."""
    denied, profile, meta, module_or_expected = _stock_serials_page_guard(
        request, role_segment
    )
    if denied is not None:
        return denied
    if profile is None:
        return redirect(
            "employees:stock_serial_return_guest",
            role_segment=module_or_expected,
        )
    return stock_serial_return_client(
        request,
        profile,
        meta,
        module_or_expected,
        guest_phone=request.GET.get("phone") or "",
        guest_name=request.GET.get("name") or "",
    )


@active_employee_required
@require_GET
def item_management_catalog_proxy(request, role_segment):
    """JSON catalog for item-management list."""
    return item_management_catalog(request, role_segment)


def _safe_login_next(request, raw_next):
    """Return an internal next path when it is safe to redirect after login."""
    candidate = (raw_next or "").strip()
    if not candidate:
        return ""
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return ""


@rate_limit("login", methods=("POST",))
@require_http_methods(["GET", "POST"])
def employee_login(request):
    from .portal_auth import begin_employee_session, render_portal_login

    next_url = _safe_login_next(
        request,
        request.POST.get("next") if request.method == "POST" else request.GET.get("next"),
    )

    if request.user.is_authenticated:
        profile = get_profile(request.user)
        if profile and profile.is_active_employee:
            if next_url:
                return redirect(next_url)
            return redirect_to_role_home(profile)
        if profile and profile.status == EmployeeStatus.PENDING_APPROVAL:
            return redirect("employees:pending")
        logout(request)

    error = None
    username = ""
    if request.method == "POST":
        username = re.sub(r"\D+", "", request.POST.get("username", ""))[:6]
        password = request.POST.get("password", "")
        if len(username) != 6:
            error = "Enter your 6-digit employee ID."
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                error = "Invalid employee ID or password. Please try again."
            else:
                profile = get_profile(user)
                if profile is None:
                    error = "No employee profile found for this account."
                elif profile.status == EmployeeStatus.PENDING_APPROVAL:
                    error = (
                        "Your account is pending approval. "
                        "You can sign in once an administrator activates you."
                    )
                elif profile.status == EmployeeStatus.SUSPENDED:
                    error = "Your account is suspended. Contact your administrator."
                elif profile.status != EmployeeStatus.ACTIVE:
                    error = "Your account is not active. Contact your administrator."
                else:
                    begin_employee_session(request, user, profile)
                    if next_url:
                        return redirect(next_url)
                    return redirect_to_role_home(profile)

    return render_portal_login(
        request,
        login_mode="employee",
        employee_error=error,
        next_url=next_url,
        employee_username=username,
    )


@rate_limit("register")
@require_http_methods(["GET", "POST"])
def employee_register(request):
    if request.user.is_authenticated:
        profile = get_profile(request.user)
        if profile and profile.is_active_employee:
            return redirect_to_role_home(profile)
        if profile and profile.status == EmployeeStatus.PENDING_APPROVAL:
            return redirect("employees:pending")

    errors = []
    form_data = dict(EMPTY_EMPLOYEE_FORM)

    if request.method == "POST":
        form_data = employee_form_data_from_post(request.POST)
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        profile_photo = request.FILES.get("profile_photo")
        errors = validate_employee_registration(
            form_data, password, password_confirm, profile_photo
        )

        if not errors:
            try:
                employee_id = register_employee(form_data, password, profile_photo)
                return redirect(
                    "employees:registration_submitted",
                    employee_id=employee_id,
                )
            except IntegrityError:
                mark_employee_id_taken(form_data["employee_id"])
                errors.append("That employee ID or email could not be registered. Try again.")

    return render(
        request,
        "employees/register.html",
        {
            "errors": errors,
            "form_data": form_data,
            "countries": COUNTRY_DIAL_CODES,
        },
    )


def registration_submitted(request, employee_id):
    return render(
        request,
        "employees/registration_submitted.html",
        {"employee_id": employee_id},
    )


def _employee_id_json_response(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "private, max-age=120"
    return response


@rate_limit("check_employee_id")
@require_GET
def check_employee_id(request):
    """Live check whether a 6-digit employee code is available."""
    code = request.GET.get("code", "").strip()
    exclude = request.GET.get("exclude", "").strip()

    if not code:
        return _employee_id_json_response(
            {"available": None, "message": "Enter a 6-digit employee code."}
        )

    if not EMPLOYEE_ID_RE.match(code):
        return _employee_id_json_response(
            {
                "available": False,
                "message": "Employee code must be exactly 6 digits.",
            }
        )

    if exclude and EMPLOYEE_ID_RE.match(exclude) and code == exclude:
        return _employee_id_json_response(
            {
                "available": True,
                "message": f"Employee code {code} is available.",
            }
        )

    if employee_id_is_taken(code, exclude_employee_id=exclude or None):
        return _employee_id_json_response(
            {
                "available": False,
                "message": f"Employee code {code} is not available. Choose another.",
            }
        )

    return _employee_id_json_response(
        {
            "available": True,
            "message": f"Employee code {code} is available.",
        }
    )


def pending_approval(request):
    return render(request, "employees/pending.html")


@active_employee_required
def dashboard(request):
    """Entry point — send active employees to their role home."""
    return redirect_to_role_home(get_profile_for_request(request))


@role_required(EmployeeRole.EMPLOYEE)
def role_employee(request):
    return _render_role_page(request, EmployeeRole.EMPLOYEE)


@role_required(EmployeeRole.SUPER_ADMIN)
def role_super_admin(request, page=None):
    redirect_response = redirect_query_page(
        request, "role_super_admin", page, fragment="employee-access"
    )
    if redirect_response:
        return redirect_response

    profile = get_profile_for_request(request)
    meta = ROLE_PAGE_META[EmployeeRole.SUPER_ADMIN]
    queryset = (
        EmployeeProfile.objects.select_related("user")
        .only(
            "employee_id",
            "role",
            "status",
            "profile_photo",
            "phone_country_code",
            "phone_number",
            "user__username",
            "user__first_name",
            "user__last_name",
            "user__email",
        )
        .order_by("status", "employee_id")
    )
    paginator = Paginator(queryset, settings.EMPLOYEE_LIST_PAGE_SIZE)
    employees_page = paginator.get_page(page or 1)

    return render(
        request,
        "employees/super_admin.html",
        {
            "profile": profile,
            "meta": meta,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "employees_page": employees_page,
            "employees": employees_page.object_list,
            "role_choices": EmployeeRole.choices,
            "status_choices": EmployeeStatus.choices,
            "page_sidebar": sidebar_for_super_admin(),
            "pagination": pagination_links(
                employees_page, "role_super_admin", fragment="employee-access"
            ),
        },
    )


@role_required(EmployeeRole.SUPER_ADMIN)
@require_http_methods(["POST"])
def update_employee_access(request, employee_id):
    """Update an employee's role and/or status from Super Admin dropdowns."""
    list_page = request.POST.get("list_page", "").strip()

    target = (
        EmployeeProfile.objects.select_related("user")
        .filter(employee_id=employee_id)
        .first()
    )
    if target is None:
        messages.error(request, f"Employee {employee_id} was not found.")
        return _redirect_after_employee_access_update(request, list_page or None)

    new_role = request.POST.get("role", "").strip()
    new_status = request.POST.get("status", "").strip()
    valid_roles = {choice[0] for choice in EmployeeRole.choices}
    valid_statuses = {choice[0] for choice in EmployeeStatus.choices}

    if new_role not in valid_roles:
        messages.error(request, "Select a valid role.")
        return _redirect_after_employee_access_update(request, list_page or None)

    if new_status not in valid_statuses:
        messages.error(request, "Select a valid status.")
        return _redirect_after_employee_access_update(request, list_page or None)

    if target.user_id == request.user.pk:
        if new_role != EmployeeRole.SUPER_ADMIN:
            messages.error(request, "You cannot remove your own Super Admin role.")
            return _redirect_after_employee_access_update(request, list_page or None)
        if new_status != EmployeeStatus.ACTIVE:
            messages.error(
                request, "You cannot deactivate your own Super Admin account."
            )
            return _redirect_after_employee_access_update(request, list_page or None)

    changed = []
    if target.role != new_role:
        target.role = new_role
        changed.append(f"role → {target.get_role_display()}")
    if target.status != new_status:
        target.status = new_status
        changed.append(f"status → {target.get_status_display()}")

    update_fields = ["role", "status", "updated_at"]
    cleared_shops = False
    if new_role not in SHOP_ASSIGNABLE_ROLES and target.assigned_shops.exists():
        target.assigned_shops.clear()
        cleared_shops = True
        changed.append("shops cleared")

    if changed:
        target.save(update_fields=update_fields)
        messages.success(
            request,
            f"Updated {target.employee_id} "
            f"({target.user.get_full_name() or target.user.username}): "
            f"{', '.join(changed)}.",
        )
    elif cleared_shops:
        messages.success(
            request,
            f"Updated {target.employee_id}: shops cleared.",
        )
    else:
        messages.info(request, f"No changes for employee {target.employee_id}.")

    return _redirect_after_employee_access_update(request, list_page or None)


@role_required(EmployeeRole.COMPANY_MANAGER)
def role_company_manager(request):
    return _render_role_page(request, EmployeeRole.COMPANY_MANAGER)


@role_required(EmployeeRole.SHOP_MANAGER)
def role_shop_manager(request):
    return _render_role_page(request, EmployeeRole.SHOP_MANAGER)


@role_required(EmployeeRole.SHOP_CASHIER)
def role_shop_cashier(request):
    return _render_role_page(request, EmployeeRole.SHOP_CASHIER)


@role_required(EmployeeRole.IT_SUPPORT)
def role_it_support(request):
    return _render_role_page(request, EmployeeRole.IT_SUPPORT)


@active_employee_required
def employee_profile(request):
    profile = get_profile_for_request(request)
    meta = {
        "title": "My Profile",
        "headline": "My profile",
        "summary": "View your employee details and contact information.",
        "icon": "circle-user",
    }
    return render(
        request,
        "employees/profile.html",
        {
            "profile": profile,
            "meta": meta,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_profile(profile.role),
        },
    )


@active_employee_required
def employee_settings(request):
    from .module_permissions import employee_may, require_module_permission

    profile = get_profile_for_request(request)
    denied = require_module_permission(request, profile, "settings", "home")
    if denied is not None:
        return denied
    meta = {
        "title": "System Settings",
        "headline": "System settings",
        "summary": "Configure company-wide profile, theme, POS, and payment options.",
        "icon": "settings",
    }
    return render(
        request,
        "employees/settings.html",
        {
            "profile": profile,
            "meta": meta,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "settings_sections": [
                section
                for section in get_settings_sections()
                if employee_may(profile, "settings", section["slug"])
            ],
            "page_sidebar": sidebar_for_settings(
                profile.role, active_view="home", profile=profile
            ),
        },
    )


@active_employee_required
@require_http_methods(["GET", "POST"])
def employee_settings_section(request, section):
    from .module_permissions import require_module_permission

    profile = get_profile_for_request(request)
    settings_section = get_settings_section(section)
    if settings_section is None:
        raise Http404("Settings section not found.")

    denied = require_module_permission(
        request, profile, "settings", settings_section["slug"]
    )
    if denied is not None:
        return denied

    meta = {
        "title": settings_section["label"],
        "headline": settings_section["label"],
        "summary": settings_section["summary"],
        "icon": settings_section["icon"],
    }
    context = {
        "profile": profile,
        "meta": meta,
        "settings_section": settings_section,
        "role_label": profile.get_role_display(),
        "status_label": profile.get_status_display(),
        "page_sidebar": sidebar_for_settings(
            profile.role, active_view=settings_section["slug"], profile=profile
        ),
    }

    if settings_section["slug"] == "company-pos":
        return _company_pos_settings(request, context, setting_groups=POS_SETTING_GROUPS)

    if settings_section["slug"] == "company-receipt":
        return _company_pos_settings(
            request,
            context,
            setting_groups=RECEIPT_SETTING_GROUPS,
            template_name="employees/settings_receipt.html",
        )

    if settings_section["slug"] == "company-profile":
        return _company_profile_settings(request, context)

    if settings_section["slug"] in ("company-payments", "company-daraja"):
        return _company_daraja_settings(request, context)

    if settings_section["slug"] == "whatsapp":
        return _company_communications_settings(request, context)

    if settings_section["slug"] == "working-hours":
        return _company_working_hours_settings(request, context)

    return render(request, "employees/settings_section.html", context)


def _company_communications_settings(request, context):
    from shops.models import SmsProvider

    row = get_communications_settings()
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_whatsapp_settings":
            try:
                row = update_whatsapp_settings(
                    phone_number_id=request.POST.get("whatsapp_phone_number_id") or "",
                    business_account_id=request.POST.get("whatsapp_business_account_id")
                    or "",
                    access_token=request.POST.get("whatsapp_access_token") or "",
                    from_number=request.POST.get("whatsapp_from_number") or "",
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": "WhatsApp API saved.",
                        **communications_settings_as_dict(row),
                    }
                )
            messages.success(request, "WhatsApp API saved.")
            return redirect(request.path)

        if action == "save_sms_settings":
            try:
                row = update_sms_settings(
                    provider=request.POST.get("sms_provider") or "",
                    api_key=request.POST.get("sms_api_key") or "",
                    api_secret=request.POST.get("sms_api_secret") or "",
                    sender_id=request.POST.get("sms_sender_id") or "",
                    api_base_url=request.POST.get("sms_api_base_url") or "",
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": "Text API saved.",
                        **communications_settings_as_dict(row),
                    }
                )
            messages.success(request, "Text API saved.")
            return redirect(request.path)

        if action == "save_message_settings":
            try:
                row = update_message_channel_settings(
                    from_name=request.POST.get("message_from_name") or "",
                    reply_to=request.POST.get("message_reply_to") or "",
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": "Message API saved.",
                        **communications_settings_as_dict(row),
                    }
                )
            messages.success(request, "Message API saved.")
            return redirect(request.path)

        if wants_json:
            return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect(request.path)

    context.update(
        {
            "comms": communications_settings_as_dict(row),
            "sms_providers": SmsProvider.choices,
        }
    )
    return render(request, "employees/settings_communications.html", context)


def _company_daraja_settings(request, context):
    from shops.daraja_stk import sync_callback_base_from_request
    from shops.models import DarajaEnvironment

    # Avoid ngrok/network probes on every GET — only sync when mutating.
    row = get_daraja_settings()
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "toggle_stk_push":
            sync_callback_base_from_request(request, persist=True)
            enabled = (request.POST.get("enabled") or "").strip().lower() in (
                "1",
                "true",
                "on",
                "yes",
            )
            try:
                row = set_daraja_stk_enabled(enabled=enabled)
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": message,
                            **daraja_settings_as_dict(get_daraja_settings()),
                        },
                        status=400,
                    )
                messages.error(request, message)
                return redirect(request.path)
            payload = daraja_settings_as_dict(row)
            if wants_json:
                return JsonResponse({"ok": True, **payload})
            messages.success(
                request,
                "STK Push enabled." if row.enable_stk_push else "STK Push disabled.",
            )
            return redirect(request.path)

        if action == "save_daraja_credentials":
            enable_raw = (request.POST.get("enable_stk_push") or "").strip().lower()
            enable_stk = None
            if enable_raw in ("1", "true", "on", "yes"):
                enable_stk = True
            elif enable_raw in ("0", "false", "off", "no"):
                enable_stk = False
            try:
                row = update_daraja_settings(
                    environment=request.POST.get("environment") or "",
                    shortcode=request.POST.get("shortcode") or "",
                    consumer_key=request.POST.get("consumer_key") or "",
                    consumer_secret=request.POST.get("consumer_secret") or "",
                    passkey=request.POST.get("passkey") or "",
                    callback_base_url=request.POST.get("callback_base_url") or "",
                    enable_stk_push=enable_stk,
                    request=request,
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": message,
                            **daraja_settings_as_dict(get_daraja_settings()),
                        },
                        status=400,
                    )
                messages.error(request, message)
                context.update(
                    {
                        "daraja": daraja_settings_as_dict(get_daraja_settings()),
                        "daraja_environments": DarajaEnvironment.choices,
                        "form_data": {
                            "environment": (request.POST.get("environment") or "").strip(),
                            "shortcode": (request.POST.get("shortcode") or "").strip(),
                            "callback_base_url": daraja_settings_as_dict(
                                get_daraja_settings()
                            ).get("callback_base_url")
                            or "",
                            "enable_stk_push": enable_stk
                            if enable_stk is not None
                            else get_daraja_settings().enable_stk_push,
                        },
                    }
                )
                return render(request, "employees/settings_daraja.html", context)

            payload = daraja_settings_as_dict(row)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "message": "Daraja credentials verified and saved.",
                        **payload,
                    }
                )
            messages.success(request, "Daraja credentials verified and saved.")
            return redirect(request.path)

        if wants_json:
            return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect(request.path)

    context.update(
        {
            "daraja": daraja_settings_as_dict(row),
            "daraja_environments": DarajaEnvironment.choices,
            "form_data": {
                "environment": row.environment,
                "shortcode": row.shortcode,
                "callback_base_url": row.callback_base_url
                or (daraja_settings_as_dict(row).get("callback_base_url") or ""),
                "enable_stk_push": row.enable_stk_push,
            },
        }
    )
    return render(request, "employees/settings_daraja.html", context)


def _company_profile_settings(request, context):
    company = get_company_profile()

    if request.method == "POST":
        try:
            company = update_company_profile(request.POST, request.FILES)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)
            context.update(
                {
                    "company_profile": company,
                    "form_data": {
                        "name": (request.POST.get("name") or "").strip().upper(),
                        "phone_number": (request.POST.get("phone_number") or "").strip().upper(),
                        "email": (request.POST.get("email") or "").strip().lower(),
                        "location": (request.POST.get("location") or "").strip().upper(),
                    },
                }
            )
            return render(request, "employees/settings_company_profile.html", context)

        messages.success(request, "Company profile saved.")
        return redirect(request.path)

    context.update(
        {
            "company_profile": company,
            "form_data": {
                "name": company.name,
                "phone_number": company.phone_number,
                "email": company.email,
                "location": company.location,
            },
        }
    )
    return render(request, "employees/settings_company_profile.html", context)


def _working_hours_form_context(settings_row, post=None):
    from shops.models import WORKING_DAY_FIELDS
    from shops.services import active_shop_count, list_working_hours_shop_rows

    if post is None:
        day_rows = [
            {
                "field": field,
                "label": label,
                "short": short,
                "checked": bool(getattr(settings_row, field, False)),
            }
            for field, label, short in WORKING_DAY_FIELDS
        ]
        enabled = bool(settings_row.enabled)
    else:
        day_rows = [
            {
                "field": field,
                "label": label,
                "short": short,
                "checked": (post.get(field) or "").strip().lower()
                in ("1", "on", "true", "yes"),
            }
            for field, label, short in WORKING_DAY_FIELDS
        ]
        enabled = (post.get("enabled") or "").strip().lower() in (
            "1",
            "on",
            "true",
            "yes",
        )

    return {
        "working_hours": settings_row,
        "working_day_rows": day_rows,
        "working_day_count": sum(1 for day in day_rows if day["checked"]),
        "active_shop_count": active_shop_count(),
        "working_hours_shops": list_working_hours_shop_rows(post=post),
        "form_data": {
            "enabled": enabled,
        },
    }


def _company_working_hours_settings(request, context):
    settings_row = get_company_working_hours_settings()

    if request.method == "POST":
        try:
            settings_row = save_working_hours_settings(request.POST)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)
            context.update(_working_hours_form_context(settings_row, post=request.POST))
            return render(request, "employees/settings_working_hours.html", context)

        messages.success(request, "Working hours saved.")
        return redirect(request.path)

    context.update(_working_hours_form_context(settings_row))
    return render(request, "employees/settings_working_hours.html", context)


POS_SETTING_GROUPS = (
    {
        "title": "Transaction types",
        "summary": "Choose which document types appear on the shop checkout.",
        "toggles": (
            ("enable_sale", "Sale"),
            ("enable_credit", "Credit"),
            ("enable_quotation", "Quotation"),
        ),
    },
    {
        "title": "Payment methods",
        "summary": "Choose which payment options appear for cash sale checkout.",
        "toggles": (
            ("enable_cash", "Cash"),
            ("enable_mpesa", "M-Pesa"),
            ("enable_cash_mpesa", "Cash + M-Pesa"),
        ),
    },
    {
        "title": "Discounts",
        "summary": "Allow staff to lower sale prices on the shop page.",
        "toggles": (("enable_discount", "Activate discount"),),
    },
    {
        "title": "Tax",
        "summary": "Add a tax percentage on top of the items subtotal at checkout.",
        "toggles": (("enable_tax", "Activate tax"),),
        "show_tax_percent": True,
    },
)

RECEIPT_SETTING_GROUPS = (
    {
        "title": "Printing",
        "summary": "Require printing on sales and choose available print channels.",
        "toggles": (
            ("compulsory_print_on_sale", "Compulsory printing on sale"),
            ("enable_print_bluetooth", "Print via Bluetooth"),
            ("enable_print_usb", "Print via USB"),
            ("enable_print_wifi", "Print via Wi‑Fi"),
        ),
    },
)


def _company_pos_settings(
    request,
    context,
    *,
    setting_groups=POS_SETTING_GROUPS,
    template_name="employees/settings_pos.html",
):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if action == "toggle_pos_setting":
            field = (request.POST.get("field") or "").strip()
            enabled = (request.POST.get("enabled") or "").strip() in (
                "1",
                "true",
                "on",
                "yes",
            )
            try:
                row = set_company_pos_setting(field=field, enabled=enabled)
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "field": field,
                        "enabled": getattr(row, field),
                    }
                )
            messages.success(request, "Setting updated.")
            return redirect(request.path)
        if action == "set_tax_percent":
            try:
                row = set_company_tax_percent(percent=request.POST.get("tax_percent") or "")
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "tax_percent": str(row.tax_percent),
                        "enable_tax": row.enable_tax,
                    }
                )
            messages.success(request, "Tax percentage updated.")
            return redirect(request.path)
        if action == "set_receipt_paper_width":
            try:
                row = set_receipt_paper_width(
                    width=request.POST.get("receipt_paper_width") or ""
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "receipt_paper_width": row.receipt_paper_width,
                    }
                )
            messages.success(request, "Receipt paper size updated.")
            return redirect(request.path)
        if action == "set_receipt_number_formats":
            try:
                row = set_receipt_number_formats(
                    sale=request.POST.get("receipt_format_sale"),
                    credit=request.POST.get("receipt_format_credit"),
                    quotation=request.POST.get("receipt_format_quotation"),
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "receipt_format_sale": row.receipt_format_sale,
                        "receipt_format_credit": row.receipt_format_credit,
                        "receipt_format_quotation": row.receipt_format_quotation,
                        "preview_sale": preview_receipt_number(kind="sale", settings_row=row),
                        "preview_credit": preview_receipt_number(
                            kind="credit", settings_row=row
                        ),
                        "preview_quotation": preview_receipt_number(
                            kind="quotation", settings_row=row
                        ),
                    }
                )
            messages.success(request, "Receipt number formats updated.")
            return redirect(request.path)
        if action == "set_mpesa_payment_details":
            try:
                row = set_mpesa_payment_details(
                    collection_type=request.POST.get("mpesa_collection_type") or "",
                    business_number=request.POST.get("mpesa_business_number") or "",
                    account_number=request.POST.get("mpesa_account_number") or "",
                    till_number=request.POST.get("mpesa_till_number") or "",
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            details = row.mpesa_payment_details()
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "mpesa_collection_type": row.mpesa_collection_type,
                        "mpesa_business_number": row.mpesa_business_number,
                        "mpesa_account_number": row.mpesa_account_number,
                        "mpesa_till_number": row.mpesa_till_number,
                        "mpesa_payment_details": details,
                    }
                )
            messages.success(request, "Payment details updated.")
            return redirect(request.path)
        if action == "set_receipt_font_style":
            try:
                row = set_receipt_font_style(
                    size=request.POST.get("receipt_font_size") or "",
                    weight=request.POST.get("receipt_font_weight") or "",
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            font = receipt_font_style(row)
            if wants_json:
                return JsonResponse({"ok": True, **font})
            messages.success(request, "Receipt font style updated.")
            return redirect(request.path)
        if action == "set_receipt_qr_settings":
            enabled = (request.POST.get("enable_receipt_qr") or "").strip().lower() in (
                "1",
                "true",
                "on",
                "yes",
            )
            try:
                row = set_receipt_qr_settings(
                    enabled=enabled,
                    content=request.POST.get("receipt_qr_content") or "",
                    website=request.POST.get("receipt_qr_website") or "",
                )
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                if wants_json:
                    return JsonResponse({"ok": False, "error": message}, status=400)
                messages.error(request, message)
                return redirect(request.path)
            qr = receipt_qr_for_settings(
                row,
                preview={
                    "receipt_number": preview_receipt_number(kind="sale", settings_row=row),
                    "kind": "Sale",
                    "shop_name": (get_company_profile().name or "WESTLANDS BRANCH").upper(),
                    "client": "JANE WAMBUI",
                    "total": "3,700.00",
                    "date": "07 Aug 2026 · 09:15",
                    "payment": "Cash",
                },
            )
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "enable_receipt_qr": row.enable_receipt_qr,
                        "receipt_qr_content": row.receipt_qr_content,
                        "receipt_qr_website": row.receipt_qr_website,
                        "receipt_qr": qr,
                    }
                )
            messages.success(request, "Receipt QR settings updated.")
            return redirect(request.path)
        if wants_json:
            return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect(request.path)

    pos = get_company_pos_settings()
    groups = []
    enabled_count = 0
    toggle_count = 0
    for group in setting_groups:
        toggles = []
        for field, label in group["toggles"]:
            enabled = bool(getattr(pos, field))
            toggle_count += 1
            if enabled:
                enabled_count += 1
            toggles.append(
                {
                    "field": field,
                    "label": label,
                    "enabled": enabled,
                }
            )
        groups.append(
            {
                "title": group["title"],
                "summary": group["summary"],
                "show_tax_percent": bool(group.get("show_tax_percent")),
                "toggles": toggles,
            }
        )
    paper_width = pos.receipt_paper_width if pos.receipt_paper_width in ("80", "58") else "80"
    from decimal import Decimal

    company = get_company_profile()
    font = receipt_font_style(pos)
    preview_subtotal = Decimal("3700.00")
    preview_tax = Decimal("0.00")
    preview_tax_percent = Decimal(pos.tax_percent or 0).quantize(Decimal("0.01"))
    if pos.enable_tax and preview_tax_percent > 0:
        preview_tax = (preview_subtotal * preview_tax_percent / Decimal("100")).quantize(
            Decimal("0.01")
        )
    preview_total = (preview_subtotal + preview_tax).quantize(Decimal("0.01"))

    def _money(value: Decimal) -> str:
        return f"{value:,.0f}"

    context.update(
        {
            "pos_settings": pos,
            "pos_setting_groups": groups,
            "pos_settings_flags": pos_settings_as_dict(pos),
            "pos_enabled_count": enabled_count,
            "pos_toggle_count": toggle_count,
            "tax_percent_value": f"{pos.tax_percent:.0f}",
            "receipt_paper_width": paper_width,
            "receipt_font_size": font["size"],
            "receipt_font_weight": font["weight"],
            "receipt_font_size_px_80": font["size_px_80"],
            "receipt_font_size_px_58": font["size_px_58"],
            "receipt_font_weight_css": font["weight_css"],
            "receipt_font_size_choices": (
                ("small", "Small"),
                ("medium", "Medium"),
                ("large", "Large"),
                ("xlarge", "Extra large"),
            ),
            "receipt_font_weight_choices": (
                ("regular", "Regular"),
                ("medium", "Medium"),
                ("bold", "Bold"),
                ("extrabold", "Extra bold"),
            ),
            "receipt_format_sale": pos.receipt_format_sale or "SAL",
            "receipt_format_credit": pos.receipt_format_credit or "CRD",
            "receipt_format_quotation": pos.receipt_format_quotation or "QTN",
            "receipt_format_previews": {
                "sale": preview_receipt_number(kind="sale", settings_row=pos),
                "credit": preview_receipt_number(kind="credit", settings_row=pos),
                "quotation": preview_receipt_number(kind="quotation", settings_row=pos),
            },
            "mpesa_collection_type": pos.mpesa_collection_type or "",
            "mpesa_business_number": pos.mpesa_business_number or "",
            "mpesa_account_number": pos.mpesa_account_number or "",
            "mpesa_till_number": pos.mpesa_till_number or "",
            "mpesa_payment_details": pos.mpesa_payment_details(),
            "enable_receipt_qr": bool(pos.enable_receipt_qr),
            "receipt_qr_content": pos.receipt_qr_content or "website",
            "receipt_qr_website": pos.receipt_qr_website or "",
            "receipt_qr_content_choices": (
                ("website", "Company website"),
                ("receipt_details", "Receipt details"),
            ),
            "receipt_preview": {
                "logo_url": "",
                "shop_name": (company.name or "WESTLANDS BRANCH").upper(),
                "shop_location": company.location or "Waiyaki Way, Nairobi",
                "shop_phone": company.phone_number or "+254 712 000 111",
                "shop_branch": "",
                "receipt_number": preview_receipt_number(kind="sale", settings_row=pos),
                "kind": "Sale",
                "doc_type": "sale",
                "document_title": "Sales invoice / receipt",
                "doc_number_label": "Invoice No.",
                "party_label": "Customer",
                "authorised_label": "Cashier",
                "date": "07 Aug 2026 · 09:15",
                "client": "JANE WAMBUI",
                "party_phone": "+254 712 555 010",
                "cashier": "Staff 104822",
                "status": "",
                "lines": (
                    {
                        "name": "HDMI Cable 2M",
                        "detail": "",
                        "qty": 1,
                        "price": "850",
                        "total": "850",
                        "serials": (),
                    },
                    {
                        "name": "USB-C Hub",
                        "detail": "",
                        "qty": 2,
                        "price": "1,200",
                        "total": "2,400",
                        "serials": (),
                    },
                    {
                        "name": "Mouse Pad XL",
                        "detail": "",
                        "qty": 1,
                        "price": "450",
                        "total": "450",
                        "serials": (),
                    },
                ),
                "cancelled": False,
                "subtotal": _money(preview_subtotal),
                "tax_percent": f"{preview_tax_percent:.0f}",
                "tax_amount": _money(preview_tax),
                "show_tax": bool(pos.enable_tax and preview_tax_percent > 0),
                "total": _money(preview_total),
                "payment": "Cash",
                "payment_details": pos.mpesa_payment_details(),
                "footer": "Thank you for shopping with us",
            },
        }
    )
    context["receipt_qr"] = receipt_qr_for_settings(
        pos, preview=context["receipt_preview"]
    )
    context["receipt_preview"]["qr"] = context["receipt_qr"]
    logo_url = ""
    try:
        if company.logo:
            logo_url = company.logo.url
    except (ValueError, AttributeError):
        logo_url = ""
    context["receipt_preview"]["logo_url"] = logo_url
    # Convert nested tuples so json_script / sample print can serialize cleanly.
    preview_lines = []
    for line in context["receipt_preview"]["lines"]:
        preview_lines.append(
            {
                "name": line["name"],
                "detail": line.get("detail") or "",
                "qty": line["qty"],
                "price": line["price"],
                "total": line["total"],
                "serials": list(line.get("serials") or []),
                "serials_extra": int(line.get("serials_extra") or 0),
            }
        )
    context["receipt_sample"] = {
        "ticket": {
            "mark": get_company_display_name(),
            "shop_name": context["receipt_preview"]["shop_name"],
            "shop_location": context["receipt_preview"]["shop_location"],
            "shop_phone": context["receipt_preview"]["shop_phone"],
            "shop_branch": context["receipt_preview"].get("shop_branch") or "",
            "logo_url": logo_url,
            "receipt_number": context["receipt_preview"]["receipt_number"],
            "kind": context["receipt_preview"]["kind"],
            "doc_type": context["receipt_preview"].get("doc_type") or "sale",
            "document_title": context["receipt_preview"].get("document_title")
            or "Sales invoice / receipt",
            "doc_number_label": context["receipt_preview"].get("doc_number_label")
            or "Invoice No.",
            "party_label": context["receipt_preview"].get("party_label") or "Customer",
            "authorised_label": context["receipt_preview"].get("authorised_label")
            or "Cashier",
            "date": context["receipt_preview"]["date"],
            "client": context["receipt_preview"]["client"],
            "party_phone": context["receipt_preview"].get("party_phone") or "",
            "cashier": context["receipt_preview"]["cashier"],
            "status": context["receipt_preview"].get("status") or "",
            "lines": preview_lines,
            "cancelled": False,
            "subtotal": context["receipt_preview"]["subtotal"],
            "tax_percent": context["receipt_preview"]["tax_percent"],
            "tax_amount": context["receipt_preview"]["tax_amount"],
            "show_tax": context["receipt_preview"]["show_tax"],
            "total": context["receipt_preview"]["total"],
            "payment": context["receipt_preview"]["payment"],
            "payment_details": context["receipt_preview"]["payment_details"],
            "footer": context["receipt_preview"]["footer"],
        },
        "qr": context["receipt_qr"],
        "paper_width": paper_width,
        "font": {
            "size": font["size"],
            "weight": font["weight"],
            "size_px_80": font["size_px_80"],
            "size_px_58": font["size_px_58"],
            "weight_css": font["weight_css"],
        },
    }
    return render(request, template_name, context)


def employee_logout(request):
    clear_profile_session(request)
    logout(request)
    return redirect("core:landing")


@require_http_methods(["GET", "POST"])
def switch_to_employee_login(request):
    """End shop or employee session and open the employee login page."""
    clear_profile_session(request)
    logout(request)
    return redirect("employees:login")
