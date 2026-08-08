from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from shops.models import Shop

from .access import get_profile_for_request, hr_staff_required, role_url_segment
from .countries import COUNTRY_DIAL_CODES
from .models import (
    SHOP_ASSIGNABLE_ROLES,
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)
from .pagination import page_url, pagination_links, redirect_query_page
from .permissions_catalog import (
    PERMISSION_MODULES,
    is_valid_permission_key,
)
from .services import (
    EMPTY_EMPLOYEE_FORM,
    delete_employee,
    employee_edit_form_data_from_post,
    employee_edit_form_data_from_profile,
    mark_employee_id_taken,
    register_employee,
    toggle_employee_suspended,
    update_employee,
    validate_employee_registration,
    validate_employee_update,
)
from .workspace import hr_approvals_url, hr_section_url, sidebar_for_hr_permissions, sidebar_for_hr_management


HR_SECTIONS = {
    "payrolls": {
        "title": "Payrolls",
        "headline": "Payroll management",
        "summary": "Manage employee salaries, deductions, and payroll runs.",
        "icon": "wallet",
    },
    "leaves": {
        "title": "Leaves",
        "headline": "Leave management",
        "summary": "Track leave requests, balances, and approvals.",
        "icon": "calendar-off",
    },
    "authorizations": {
        "title": "Authorizations",
        "headline": "Shop authorizations",
        "summary": "Delegate one or more shops each Employee, Shop Manager, and Shop Cashier will work in.",
        "icon": "shield-check",
    },
    "permissions": {
        "title": "Permissions",
        "headline": "Module permissions",
        "summary": "Enable or disable what each employee can do in every module and submodule.",
        "icon": "key-round",
    },
    "performance": {
        "title": "Performance",
        "headline": "Performance reviews",
        "summary": "Monitor employee goals, reviews, and performance records.",
        "icon": "trending-up",
    },
    "audits": {
        "title": "Audits",
        "headline": "HR audits",
        "summary": "Review HR activity logs and compliance audit trails.",
        "icon": "clipboard-list",
    },
}


def _pending_employees_queryset():
    return (
        EmployeeProfile.objects.filter(status=EmployeeStatus.PENDING_APPROVAL)
        .select_related("user")
        .only(
            "employee_id",
            "role",
            "status",
            "profile_photo",
            "phone_country_code",
            "phone_number",
            "created_at",
            "user__username",
            "user__first_name",
            "user__last_name",
            "user__email",
        )
        .order_by("created_at", "employee_id")
    )


def _managed_employees_queryset():
    return (
        EmployeeProfile.objects.exclude(status=EmployeeStatus.PENDING_APPROVAL)
        .select_related("user")
        .order_by("status", "employee_id")
    )


def _shop_assignable_employees_queryset():
    return (
        EmployeeProfile.objects.filter(role__in=SHOP_ASSIGNABLE_ROLES)
        .exclude(status=EmployeeStatus.PENDING_APPROVAL)
        .select_related("user")
        .prefetch_related("assigned_shops")
        .order_by("role", "employee_id")
    )


def _available_shops_queryset():
    return Shop.objects.filter(is_hidden=False).order_by("name", "location")


def _authorizations_redirect(profile, page_number=1):
    segment = role_url_segment(profile.role)
    url = reverse(
        "employees:hr_section",
        kwargs={"role_segment": segment, "section": "authorizations"},
    )
    if page_number and int(page_number) > 1:
        url = f"{url}?page={int(page_number)}"
    return redirect(url)


def _parse_shop_ids(raw_values):
    shop_ids = []
    seen = set()
    for raw in raw_values or []:
        value = (raw or "").strip()
        if not value:
            continue
        try:
            shop_id = int(value)
        except (TypeError, ValueError):
            return None
        if shop_id in seen:
            continue
        seen.add(shop_id)
        shop_ids.append(shop_id)
    return shop_ids


def _assign_employee_shops(target, shop_ids_raw):
    """Replace delegated shops for a shop-scoped employee. Returns (ok, message)."""
    if target.role not in SHOP_ASSIGNABLE_ROLES:
        return False, (
            f"{target.employee_id} has role {target.get_role_display()}, "
            "which does not use delegated shops."
        )

    shop_ids = _parse_shop_ids(shop_ids_raw)
    if shop_ids is None:
        return False, "Select valid shops."

    if shop_ids:
        shops = list(_available_shops_queryset().filter(pk__in=shop_ids))
        if len(shops) != len(shop_ids):
            return False, "One or more selected shops are not available for assignment."
        # Preserve the submitted order where possible.
        shops_by_id = {shop.pk: shop for shop in shops}
        shops = [shops_by_id[shop_id] for shop_id in shop_ids]
    else:
        shops = []

    current_ids = set(target.assigned_shops.values_list("pk", flat=True))
    next_ids = {shop.pk for shop in shops}
    if current_ids == next_ids:
        return True, None

    target.assigned_shops.set(shops)
    target.save(update_fields=["updated_at"])

    if not shops:
        return True, f"Cleared shop assignments for {target.employee_id}."

    names = ", ".join(f"{shop.name} ({shop.location})" for shop in shops)
    return True, f"Assigned {target.employee_id} to {names}."


def _handle_authorizations_post(request, profile, page_number):
    action = (request.POST.get("action") or "").strip()

    if action == "register":
        form_data, form_errors, open_register_modal = _process_register_post(request)
        if not form_errors:
            return _authorizations_redirect(profile, page_number), None
        return None, {
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
        }

    if action == "assign_shop":
        target = get_object_or_404(
            _shop_assignable_employees_queryset(),
            employee_id=(request.POST.get("employee_id") or "").strip(),
        )
        ok, message = _assign_employee_shops(target, request.POST.getlist("shop_ids"))
        if not ok:
            messages.error(request, message)
        elif message:
            messages.success(request, message)
        else:
            messages.info(request, f"No changes for employee {target.employee_id}.")
        return _authorizations_redirect(profile, page_number), None

    messages.error(request, "Unknown action.")
    return _authorizations_redirect(profile, page_number), None


def _render_hr_authorizations(request, profile, section_meta, page_number=1):
    from .module_permissions import require_module_permission

    denied = require_module_permission(
        request, profile, "hr-management", "authorizations"
    )
    if denied is not None:
        return denied

    form_data = dict(EMPTY_EMPLOYEE_FORM)
    form_errors = []
    open_register_modal = False

    if request.method == "POST":
        redirect_response, modal_state = _handle_authorizations_post(
            request,
            profile,
            page_number,
        )
        if redirect_response is not None:
            return redirect_response
        if modal_state is not None:
            form_data = modal_state["form_data"]
            form_errors = modal_state["form_errors"]
            open_register_modal = modal_state["open_register_modal"]
    else:
        form_data, form_errors, open_register_modal = _register_context(request)

    paginator = Paginator(
        _shop_assignable_employees_queryset(),
        settings.EMPLOYEE_LIST_PAGE_SIZE,
    )
    employees_page = paginator.get_page(page_number)
    employees = list(employees_page.object_list)
    for employee in employees:
        employee.assigned_shop_ids = {
            shop.pk for shop in employee.assigned_shops.all()
        }
    segment = role_url_segment(profile.role)
    base_url = reverse(
        "employees:hr_section",
        kwargs={"role_segment": segment, "section": "authorizations"},
    )

    def _page_link(number):
        if number <= 1:
            return base_url
        return f"{base_url}?page={number}"

    pagination = {"previous_url": None, "next_url": None}
    if employees_page.has_previous():
        pagination["previous_url"] = _page_link(employees_page.previous_page_number)
    if employees_page.has_next():
        pagination["next_url"] = _page_link(employees_page.next_page_number)

    return render(
        request,
        "employees/hr_authorizations.html",
        {
            "profile": profile,
            "meta": section_meta,
            "section": "authorizations",
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_hr_management(
                profile.role, active_view="authorizations", profile=profile
            ),
            "employees_page": employees_page,
            "employees": employees,
            "shops": list(_available_shops_queryset()),
            "permissions_url": hr_section_url(profile.role, "permissions"),
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "countries": COUNTRY_DIAL_CODES,
            "pagination": pagination,
        },
    )


def _permission_employees_queryset():
    return (
        EmployeeProfile.objects.exclude(status=EmployeeStatus.PENDING_APPROVAL)
        .select_related("user")
        .order_by("role", "employee_id")
    )


def _permission_allowed_lookup(employee_pks):
    """Map (employee_pk, module, submodule) → allowed. Missing means allowed."""
    if not employee_pks:
        return {}
    rows = EmployeeModulePermission.objects.filter(
        employee_id__in=employee_pks
    ).values_list("employee_id", "module_slug", "submodule_slug", "allowed")
    return {
        (employee_id, module_slug, submodule_slug): allowed
        for employee_id, module_slug, submodule_slug, allowed in rows
    }


def _set_employee_permission(target, module_slug, submodule_slug, allowed):
    if not is_valid_permission_key(module_slug, submodule_slug):
        return False, "Unknown module or submodule."

    permission, created = EmployeeModulePermission.objects.update_or_create(
        employee=target,
        module_slug=module_slug,
        submodule_slug=submodule_slug,
        defaults={"allowed": allowed},
    )
    state = "enabled" if permission.allowed else "disabled"
    return True, {
        "employee_id": target.employee_id,
        "module_slug": module_slug,
        "submodule_slug": submodule_slug,
        "allowed": permission.allowed,
        "created": created,
        "message": (
            f"{target.employee_id}: {module_slug}/{submodule_slug} {state}."
        ),
    }


def _wants_json(request):
    accept = request.headers.get("Accept", "")
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
    )


def _handle_permissions_post(request, profile):
    action = (request.POST.get("action") or "").strip()

    if action == "register":
        form_data, form_errors, open_register_modal = _process_register_post(request)
        if not form_errors:
            return redirect(request.path), None, None
        return None, {
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
        }, None

    if action == "toggle_permission":
        target = (
            _permission_employees_queryset()
            .filter(employee_id=(request.POST.get("employee_id") or "").strip())
            .first()
        )
        if target is None:
            error = "Employee was not found."
            if _wants_json(request):
                return None, None, JsonResponse({"ok": False, "error": error}, status=404)
            messages.error(request, error)
            return redirect(request.path), None, None

        raw_allowed = (request.POST.get("allowed") or "").strip().lower()
        if raw_allowed in {"1", "true", "on", "yes"}:
            allowed = True
        elif raw_allowed in {"0", "false", "off", "no"}:
            allowed = False
        else:
            error = "Invalid permission value."
            if _wants_json(request):
                return None, None, JsonResponse({"ok": False, "error": error}, status=400)
            messages.error(request, error)
            return redirect(request.path), None, None

        ok, payload = _set_employee_permission(
            target,
            (request.POST.get("module_slug") or "").strip(),
            (request.POST.get("submodule_slug") or "").strip(),
            allowed,
        )
        if not ok:
            if _wants_json(request):
                return None, None, JsonResponse(
                    {"ok": False, "error": payload}, status=400
                )
            messages.error(request, payload)
            return redirect(request.path), None, None

        if _wants_json(request):
            return None, None, JsonResponse({"ok": True, **payload})

        messages.success(request, payload["message"])
        return redirect(request.path), None, None

    messages.error(request, "Unknown action.")
    return redirect(request.path), None, None


def _render_hr_permissions(request, profile, section_meta):
    from .module_permissions import require_module_permission

    denied = require_module_permission(
        request, profile, "hr-management", "permissions"
    )
    if denied is not None:
        return denied

    form_data = dict(EMPTY_EMPLOYEE_FORM)
    form_errors = []
    open_register_modal = False

    if request.method == "POST":
        redirect_response, modal_state, json_response = _handle_permissions_post(
            request,
            profile,
        )
        if json_response is not None:
            return json_response
        if redirect_response is not None:
            return redirect_response
        if modal_state is not None:
            form_data = modal_state["form_data"]
            form_errors = modal_state["form_errors"]
            open_register_modal = modal_state["open_register_modal"]
    else:
        form_data, form_errors, open_register_modal = _register_context(request)

    employees = list(_permission_employees_queryset())
    allowed_lookup = _permission_allowed_lookup([employee.pk for employee in employees])

    modules = []
    for module in PERMISSION_MODULES:
        rows = []
        for employee in employees:
            toggles = []
            for submodule in module["submodules"]:
                allowed = allowed_lookup.get(
                    (employee.pk, module["slug"], submodule["slug"]),
                    True,
                )
                toggles.append(
                    {
                        "slug": submodule["slug"],
                        "label": submodule["label"],
                        "allowed": allowed,
                    }
                )
            rows.append({"employee": employee, "toggles": toggles})
        modules.append(
            {
                "slug": module["slug"],
                "label": module["label"],
                "icon": module["icon"],
                "summary": module["summary"],
                "submodules": module["submodules"],
                "rows": rows,
            }
        )

    return render(
        request,
        "employees/hr_permissions.html",
        {
            "profile": profile,
            "meta": section_meta,
            "section": "permissions",
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_hr_permissions(profile.role, profile=profile),
            "permission_modules": modules,
            "authorizations_url": hr_section_url(profile.role, "authorizations"),
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "countries": COUNTRY_DIAL_CODES,
        },
    )


def _attach_phone_country_iso(employees):
    dial_to_iso = {country["dial"]: country["iso"] for country in COUNTRY_DIAL_CODES}
    for employee in employees:
        employee.phone_country_iso = dial_to_iso.get(
            employee.phone_country_code,
            "KE",
        )
    return employees


def _hr_management_redirect(profile, page_number=1):
    segment = role_url_segment(profile.role)
    return redirect(
        page_url(
            "hr_management",
            page_number,
            url_kwargs={"role_segment": segment},
        )
    )


def _process_register_post(request):
    from .services import employee_form_data_from_post

    form_data = employee_form_data_from_post(request.POST)
    password = request.POST.get("password", "")
    password_confirm = request.POST.get("password_confirm", "")
    profile_photo = request.FILES.get("profile_photo")
    form_errors = validate_employee_registration(
        form_data, password, password_confirm, profile_photo
    )

    if form_errors:
        return form_data, form_errors, True

    try:
        employee_id = register_employee(form_data, password, profile_photo)
    except IntegrityError:
        mark_employee_id_taken(form_data["employee_id"])
        return form_data, [
            "That employee ID or email could not be registered. Try again."
        ], True

    messages.success(
        request,
        f"Employee {employee_id} registered and is pending approval.",
    )
    return form_data, [], False


def _register_context(request):
    form_data = dict(EMPTY_EMPLOYEE_FORM)
    form_errors = []
    open_register_modal = False

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "register":
        form_data, form_errors, open_register_modal = _process_register_post(request)

    return form_data, form_errors, open_register_modal


def _handle_hr_management_post(request, profile, page_number):
    from .module_permissions import require_module_permission

    action = (request.POST.get("action") or "").strip()
    actor = get_profile_for_request(request)

    if action in {"register", "edit", "toggle_suspend", "delete"}:
        denied = require_module_permission(
            request, profile, "hr-management", action
        )
        if denied is not None:
            return denied, None

    if action == "register":
        form_data, form_errors, open_register_modal = _process_register_post(request)
        if not form_errors:
            return _hr_management_redirect(profile, page_number), None
        return None, {
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "open_edit_modal": False,
            "edit_form_data": dict(EMPTY_EMPLOYEE_FORM),
            "edit_form_errors": [],
            "edit_employee": None,
        }

    if action == "edit":
        original_employee_id = (request.POST.get("original_employee_id") or "").strip()
        target = get_object_or_404(
            _managed_employees_queryset(),
            employee_id=original_employee_id,
        )
        form_data = employee_edit_form_data_from_post(request.POST)
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        profile_photo = request.FILES.get("profile_photo")
        remove_photo = request.POST.get("remove_photo") == "1"
        form_errors = validate_employee_update(
            form_data,
            password,
            password_confirm,
            profile_photo,
            current_employee_id=original_employee_id,
        )

        if target.user_id == actor.user_id:
            if form_data["role"] != EmployeeRole.SUPER_ADMIN:
                form_errors.append("You cannot remove your own Super Admin role.")
            if form_data["employee_id"] != actor.employee_id:
                form_errors.append("You cannot change your own employee ID.")

        if form_errors:
            return None, {
                "form_data": dict(EMPTY_EMPLOYEE_FORM),
                "form_errors": [],
                "open_register_modal": False,
                "open_edit_modal": True,
                "edit_form_data": form_data,
                "edit_form_errors": form_errors,
                "edit_employee": target,
            }

        try:
            update_employee(
                target,
                form_data,
                password=password,
                profile_photo=profile_photo,
                remove_photo=remove_photo,
            )
        except IntegrityError:
            mark_employee_id_taken(form_data["employee_id"])
            return None, {
                "form_data": dict(EMPTY_EMPLOYEE_FORM),
                "form_errors": [],
                "open_register_modal": False,
                "open_edit_modal": True,
                "edit_form_data": form_data,
                "edit_form_errors": [
                    "That employee ID or email could not be saved. Try again."
                ],
                "edit_employee": target,
            }

        messages.success(
            request,
            f"Updated employee {form_data['employee_id']} "
            f"({form_data['first_name']} {form_data['last_name']}).",
        )
        return _hr_management_redirect(profile, page_number), None

    if action == "toggle_suspend":
        target = get_object_or_404(
            _managed_employees_queryset(),
            employee_id=(request.POST.get("employee_id") or "").strip(),
        )
        if target.user_id == actor.user_id:
            messages.error(request, "You cannot suspend your own account.")
            return _hr_management_redirect(profile, page_number), None

        toggle_employee_suspended(target)
        state = "suspended" if target.status == EmployeeStatus.SUSPENDED else "activated"
        messages.success(
            request,
            f"Employee {target.employee_id} has been {state}.",
        )
        return _hr_management_redirect(profile, page_number), None

    if action == "delete":
        target = get_object_or_404(
            _managed_employees_queryset(),
            employee_id=(request.POST.get("employee_id") or "").strip(),
        )
        if target.user_id == actor.user_id:
            messages.error(request, "You cannot delete your own account.")
            return _hr_management_redirect(profile, page_number), None

        employee_id = target.employee_id
        display_name = target.user.get_full_name() or target.user.username
        delete_employee(target)
        messages.success(request, f"Deleted employee {employee_id} ({display_name}).")
        return _hr_management_redirect(profile, page_number), None

    messages.error(request, "Unknown action.")
    return _hr_management_redirect(profile, page_number), None


def _render_hr_management(request, profile, meta, module, page_sidebar, page_number=1):
    from .module_permissions import module_capabilities, require_module_permission

    form_data = dict(EMPTY_EMPLOYEE_FORM)
    form_errors = []
    open_register_modal = False
    open_edit_modal = False
    edit_form_data = {**EMPTY_EMPLOYEE_FORM, "role": EmployeeRole.EMPLOYEE}
    edit_form_errors = []
    edit_employee = None
    caps = module_capabilities(profile, "hr-management")

    if request.method == "POST":
        redirect_response, modal_state = _handle_hr_management_post(
            request,
            profile,
            page_number,
        )
        if redirect_response is not None:
            return redirect_response
        if modal_state is not None:
            form_data = modal_state["form_data"]
            form_errors = modal_state["form_errors"]
            open_register_modal = modal_state["open_register_modal"]
            open_edit_modal = modal_state["open_edit_modal"]
            edit_form_data = modal_state["edit_form_data"]
            edit_form_errors = modal_state["edit_form_errors"]
            edit_employee = modal_state["edit_employee"]
    else:
        denied = require_module_permission(request, profile, "hr-management", "home")
        if denied is not None:
            return denied
        form_data, form_errors, open_register_modal = _register_context(request)

    pending_count = EmployeeProfile.objects.filter(
        status=EmployeeStatus.PENDING_APPROVAL
    ).count()
    paginator = Paginator(
        _managed_employees_queryset(),
        settings.EMPLOYEE_LIST_PAGE_SIZE,
    )
    employees_page = paginator.get_page(page_number)
    employees = _attach_phone_country_iso(list(employees_page.object_list))
    segment = role_url_segment(profile.role)

    return render(
        request,
        "employees/hr_management.html",
        {
            "profile": profile,
            "meta": meta,
            "module": module,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": page_sidebar,
            "pending_count": pending_count,
            "employees_page": employees_page,
            "employees": employees,
            "role_choices": EmployeeRole.choices,
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "open_edit_modal": open_edit_modal,
            "edit_form_data": edit_form_data,
            "edit_form_errors": edit_form_errors,
            "edit_employee": edit_employee,
            "countries": COUNTRY_DIAL_CODES,
            "approvals_url": hr_approvals_url(profile.role),
            "module_permissions": caps,
            "pagination": pagination_links(
                employees_page,
                "hr_management",
                url_kwargs={"role_segment": segment},
            ),
        },
    )


@hr_staff_required
@require_http_methods(["GET", "POST"])
def hr_section(request, role_segment, section):
    profile = get_profile_for_request(request)
    expected_segment = role_url_segment(profile.role)
    if role_segment != expected_segment:
        return redirect(
            "employees:hr_section",
            role_segment=expected_segment,
            section=section,
        )

    section_meta = HR_SECTIONS.get(section)
    if section_meta is None:
        raise Http404("HR section not found.")

    if section == "authorizations":
        try:
            page_number = max(1, int(request.GET.get("page") or 1))
        except (TypeError, ValueError):
            page_number = 1
        if request.method == "POST":
            try:
                page_number = max(1, int(request.POST.get("list_page") or page_number))
            except (TypeError, ValueError):
                pass
        return _render_hr_authorizations(
            request,
            profile,
            section_meta,
            page_number=page_number,
        )

    if section == "permissions":
        return _render_hr_permissions(request, profile, section_meta)

    form_data, form_errors, open_register_modal = _register_context(request)
    if request.method == "POST" and not form_errors and not open_register_modal:
        return redirect(request.path)

    return render(
        request,
        "employees/hr_section.html",
        {
            "profile": profile,
            "meta": section_meta,
            "section": section,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_hr_management(profile.role, active_view=section, profile=profile),
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "countries": COUNTRY_DIAL_CODES,
        },
    )


@hr_staff_required
@require_http_methods(["GET", "POST"])
def hr_management(request, profile, meta, module, page_sidebar):
    return _render_hr_management(request, profile, meta, module, page_sidebar, page_number=1)


@hr_staff_required
@require_http_methods(["GET", "POST"])
def hr_management_page(request, role_segment, page=None):
    profile = get_profile_for_request(request)
    expected_segment = role_url_segment(profile.role)
    if role_segment != expected_segment:
        return redirect(
            "employees:hr_management",
            role_segment=expected_segment,
        )

    redirect_response = redirect_query_page(
        request,
        "hr_management",
        page,
        url_kwargs={"role_segment": role_segment},
    )
    if redirect_response:
        return redirect_response

    module = {
        "slug": "hr-management",
        "label": "HR Management",
        "icon": "users",
        "summary": "Handle staff records, roles, and approvals.",
    }
    meta = {
        "title": module["label"],
        "headline": module["label"],
        "summary": module["summary"],
        "icon": module["icon"],
    }
    page_sidebar = sidebar_for_hr_management(profile.role, active_view="home", profile=profile)
    return _render_hr_management(
        request,
        profile,
        meta,
        module,
        page_sidebar,
        page_number=page or 1,
    )


@hr_staff_required
@require_http_methods(["GET", "POST"])
def hr_employee_approvals(request, role_segment, page=None):
    from .module_permissions import require_module_permission

    profile = get_profile_for_request(request)
    expected_segment = role_url_segment(profile.role)
    if role_segment != expected_segment:
        return redirect(
            "employees:hr_employee_approvals",
            role_segment=expected_segment,
        )

    denied = require_module_permission(
        request, profile, "hr-management", "approvals"
    )
    if denied is not None:
        return denied

    redirect_response = redirect_query_page(
        request,
        "hr_employee_approvals",
        page,
        url_kwargs={"role_segment": role_segment},
    )
    if redirect_response:
        return redirect_response

    form_data, form_errors, open_register_modal = _register_context(request)
    if request.method == "POST" and not form_errors and not open_register_modal:
        return redirect(request.path)

    paginator = Paginator(
        _pending_employees_queryset(),
        settings.EMPLOYEE_LIST_PAGE_SIZE,
    )
    employees_page = paginator.get_page(page or 1)
    meta = {
        "title": "Pending approvals",
        "headline": "Employee approvals",
        "summary": "Review pending registrations, assign roles, and activate accounts.",
        "icon": "clock-3",
    }

    return render(
        request,
        "employees/hr_employee_approvals.html",
        {
            "profile": profile,
            "meta": meta,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "page_sidebar": sidebar_for_hr_management(profile.role, active_view="approvals", profile=profile),
            "employees_page": employees_page,
            "employees": employees_page.object_list,
            "role_choices": EmployeeRole.choices,
            "status_choices": EmployeeStatus.choices,
            "return_to": "hr_approvals",
            "form_data": form_data,
            "form_errors": form_errors,
            "open_register_modal": open_register_modal,
            "countries": COUNTRY_DIAL_CODES,
            "pagination": pagination_links(
                employees_page,
                "hr_employee_approvals",
                url_kwargs={"role_segment": role_segment},
            ),
        },
    )
