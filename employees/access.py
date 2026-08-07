from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .models import EmployeeProfile, EmployeeRole, EmployeeStatus

SESSION_PROFILE_KEY = "employee_profile_meta"
REQUEST_PROFILE_ATTR = "_employee_profile_cache"
REQUEST_META_ATTR = "_employee_meta_cache"

ROLE_HOME_URL_NAMES = {
    EmployeeRole.EMPLOYEE: "employees:role_employee",
    EmployeeRole.SUPER_ADMIN: "employees:role_super_admin",
    EmployeeRole.COMPANY_MANAGER: "employees:role_company_manager",
    EmployeeRole.SHOP_MANAGER: "employees:role_shop_manager",
    EmployeeRole.SHOP_CASHIER: "employees:role_shop_cashier",
    EmployeeRole.IT_SUPPORT: "employees:role_it_support",
}

ROLE_URL_SEGMENTS = {
    EmployeeRole.EMPLOYEE: "employee",
    EmployeeRole.SUPER_ADMIN: "super-admin",
    EmployeeRole.COMPANY_MANAGER: "company-manager",
    EmployeeRole.SHOP_MANAGER: "shop-manager",
    EmployeeRole.SHOP_CASHIER: "shop-cashier",
    EmployeeRole.IT_SUPPORT: "it-support",
}

ROLE_FROM_URL_SEGMENT = {segment: role for role, segment in ROLE_URL_SEGMENTS.items()}

HR_STAFF_ROLES = frozenset(
    {
        EmployeeRole.SUPER_ADMIN,
        EmployeeRole.IT_SUPPORT,
    }
)


def get_profile(user):
    """Load profile with user in one query when possible."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        cached = user.__dict__.get("employee_profile")
        if cached is not None:
            return cached
    except Exception:
        pass
    profile = (
        EmployeeProfile.objects.select_related("user")
        .filter(user_id=user.pk)
        .first()
    )
    if profile is not None:
        user.__dict__["employee_profile"] = profile
    return profile


def get_profile_for_request(request):
    """Load profile once per HTTP request (shared by views and context processor)."""
    if not request or not getattr(request, "user", None):
        return None
    cached = getattr(request, REQUEST_PROFILE_ATTR, None)
    if cached is not None:
        return cached
    profile = get_profile(request.user)
    setattr(request, REQUEST_PROFILE_ATTR, profile)
    return profile


def get_profile_meta(user):
    """Lean status/role row — avoids loading full model + file fields."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return (
        EmployeeProfile.objects.filter(user_id=user.pk)
        .values("employee_id", "role", "status")
        .first()
    )


def get_employee_meta_for_request(request):
    """Return session-backed employee meta, with one lean DB fallback per request."""
    cached = getattr(request, REQUEST_META_ATTR, None)
    if cached is not None:
        return cached

    session_meta = request.session.get(SESSION_PROFILE_KEY) or {}
    if session_meta.get("employee_id") and session_meta.get("role") and session_meta.get("status"):
        setattr(request, REQUEST_META_ATTR, session_meta)
        return session_meta

    meta = get_profile_meta(request.user)
    if meta is not None:
        setattr(request, REQUEST_META_ATTR, meta)
    return meta


def store_profile_session(request, profile):
    request.session[SESSION_PROFILE_KEY] = {
        "user_id": profile.user_id,
        "employee_id": profile.employee_id,
        "role": profile.role,
        "status": profile.status,
    }
    setattr(request, REQUEST_META_ATTR, request.session[SESSION_PROFILE_KEY])
    setattr(request, REQUEST_PROFILE_ATTR, profile)


def clear_profile_session(request):
    if SESSION_PROFILE_KEY in request.session:
        del request.session[SESSION_PROFILE_KEY]
    if hasattr(request, REQUEST_META_ATTR):
        delattr(request, REQUEST_META_ATTR)
    if hasattr(request, REQUEST_PROFILE_ATTR):
        delattr(request, REQUEST_PROFILE_ATTR)
    from shops.session import clear_active_shop

    clear_active_shop(request)



def role_home_url_name(role):
    return ROLE_HOME_URL_NAMES.get(role, "employees:role_employee")


def role_url_segment(role):
    return ROLE_URL_SEGMENTS.get(role, "employee")


def role_from_url_segment(segment):
    return ROLE_FROM_URL_SEGMENT.get(segment)


def redirect_to_role_home(profile):
    role = profile.role if hasattr(profile, "role") else profile.get("role")
    return redirect(role_home_url_name(role))


def active_employee_required(view_func):
    """Require an authenticated employee with Active status."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("employees:login")

        meta = get_employee_meta_for_request(request)
        if meta is None:
            clear_profile_session(request)
            messages.error(request, "No employee profile is linked to this account.")
            return redirect("employees:login")

        request.session[SESSION_PROFILE_KEY] = {
            "user_id": request.user.pk,
            "employee_id": meta["employee_id"],
            "role": meta["role"],
            "status": meta["status"],
        }

        if meta["status"] == EmployeeStatus.PENDING_APPROVAL:
            return redirect("employees:pending")

        if meta["status"] == EmployeeStatus.SUSPENDED:
            messages.error(
                request,
                "Your employee account is suspended. Contact your administrator.",
            )
            return redirect("employees:login")

        if meta["status"] != EmployeeStatus.ACTIVE:
            messages.error(request, "Your employee account cannot access MY-SHOP yet.")
            return redirect("employees:login")

        return view_func(request, *args, **kwargs)

    return wrapper


def role_required(*allowed_roles):
    """Require Active status and one of the allowed roles."""

    def decorator(view_func):
        @wraps(view_func)
        @active_employee_required
        def wrapper(request, *args, **kwargs):
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def hr_staff_required(view_func):
    """Require an active Super Admin or IT Support employee."""

    @wraps(view_func)
    @active_employee_required
    def wrapper(request, *args, **kwargs):
        profile = get_profile_for_request(request)
        if profile is None or profile.role not in HR_STAFF_ROLES:
            messages.error(request, "You do not have permission to access HR Management.")
            if profile is not None:
                return redirect_to_role_home(profile)
            return redirect("employees:login")
        return view_func(request, *args, **kwargs)

    return wrapper
