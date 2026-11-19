from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .models import (
    SHOP_ASSIGNABLE_ROLES,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
    split_extra_roles,
)

SESSION_PROFILE_KEY = "employee_profile_meta"
SESSION_ACTIVE_ROLE_KEY = "employee_active_role"
SESSION_ACTING_SHOP_KEY = "employee_acting_shop"
REQUEST_PROFILE_ATTR = "_employee_profile_cache"
REQUEST_META_ATTR = "_employee_meta_cache"

ROLE_HOME_URL_NAMES = {
    EmployeeRole.EMPLOYEE: "employees:role_employee",
    EmployeeRole.SUPER_ADMIN: "employees:role_super_admin",
    EmployeeRole.COMPANY_MANAGER: "employees:role_company_manager",
    EmployeeRole.SHOP_MANAGER: "employees:role_shop_manager",
    EmployeeRole.COMPANY_STORE_MANAGER: "employees:role_company_store_manager",
    EmployeeRole.SHOP_STORE_MANAGER: "employees:role_shop_store_manager",
    EmployeeRole.SHOP_CASHIER: "employees:role_shop_cashier",
    EmployeeRole.IT_SUPPORT: "employees:role_it_support",
    EmployeeRole.RIDER: "employees:role_rider",
}

ROLE_URL_SEGMENTS = {
    EmployeeRole.EMPLOYEE: "employee",
    EmployeeRole.SUPER_ADMIN: "super-admin",
    EmployeeRole.COMPANY_MANAGER: "company-manager",
    EmployeeRole.SHOP_MANAGER: "shop-manager",
    EmployeeRole.COMPANY_STORE_MANAGER: "company-store-manager",
    EmployeeRole.SHOP_STORE_MANAGER: "shop-store-manager",
    EmployeeRole.SHOP_CASHIER: "shop-cashier",
    EmployeeRole.IT_SUPPORT: "it-support",
    EmployeeRole.RIDER: "rider",
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
    if profile is not None:
        _apply_active_role(request, profile)
    setattr(request, REQUEST_PROFILE_ATTR, profile)
    return profile


def available_role_values(primary_role, extra_roles_raw) -> list:
    """All roles granted to an employee (primary + extras), in enum order."""
    granted = {primary_role, *split_extra_roles(extra_roles_raw)}
    return [value for value in EmployeeRole.values if value in granted]


def session_active_role(request, available_roles):
    """The session's picked role if still granted — else None (stale picks
    are dropped so a revoked role silently falls back to the primary role)."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    active = session.get(SESSION_ACTIVE_ROLE_KEY)
    if active in available_roles:
        return active
    if SESSION_ACTIVE_ROLE_KEY in session:
        del session[SESSION_ACTIVE_ROLE_KEY]
    return None


def session_acting_shop_id(request):
    """Shop picked on the role picker for an unallocated shop-scoped role."""
    session = getattr(request, "session", None)
    if session is None:
        return ""
    value = session.get(SESSION_ACTING_SHOP_KEY)
    return str(value or "").strip()


def _apply_active_role(request, profile) -> None:
    """Swap ``profile.role`` to the session's active role, in memory only.

    The whole codebase reads ``profile.role`` for dashboards, permissions,
    and role-scoped URLs — overriding it here makes the request behave as the
    picked role without touching ~300 call sites. The DB value is preserved
    on ``profile.db_role`` and is never written back: every save path for the
    signed-in profile uses ``update_fields`` or sets ``role`` explicitly.
    """
    available = profile.role_values()
    if len(available) >= 2:
        db_role = getattr(profile, "db_role", profile.role)
        profile.db_role = db_role
        active = session_active_role(request, available)
        if active:
            profile.role = active
    _apply_acting_shop(request, profile)


def _apply_acting_shop(request, profile) -> None:
    """Expose the picked view shop as ``profile.acting_shop_id``, in memory only.

    Multi-role staff who act in a shop-scoped role with no allocated shops pick
    a shop on the role picker; shop-scoped staff can also re-pick from the
    "Switch shop" user-menu item. The pick is session-only and is never written
    to the DB. Stale picks are dropped once the session's role is not
    shop-scoped.
    """
    session = getattr(request, "session", None)
    if session is None:
        return
    if profile.role not in SHOP_ASSIGNABLE_ROLES:
        session.pop(SESSION_ACTING_SHOP_KEY, None)
        return
    acting = session_acting_shop_id(request)
    if acting:
        profile.acting_shop_id = acting


def refresh_effective_role(request) -> None:
    """Drop cached profile/meta so the next read sees a newly picked role."""
    for attr in (REQUEST_META_ATTR, REQUEST_PROFILE_ATTR):
        if hasattr(request, attr):
            delattr(request, attr)
    user = getattr(request, "user", None)
    if user is not None:
        user.__dict__.pop("employee_profile", None)
    get_employee_meta_for_request(request)


def get_profile_meta(user):
    """Lean status/role row — avoids loading full model + file fields."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return (
        EmployeeProfile.objects.filter(user_id=user.pk)
        .values("employee_id", "role", "status", "extra_roles")
        .first()
    )


def get_employee_meta_for_request(request):
    """Return the employee's current meta, read fresh from the DB per request.

    The session copy (SESSION_PROFILE_KEY) is only a display mirror for paths
    that read it directly — never trusted for auth decisions. Trusting it
    would keep role/status changes made after login from taking effect until
    re-login, and could bleed meta across users sharing session state.
    """
    cached = getattr(request, REQUEST_META_ATTR, None)
    if cached is not None:
        return cached

    meta = get_profile_meta(request.user)
    if meta is None:
        return None

    primary_role = meta["role"]
    available = available_role_values(primary_role, meta["extra_roles"])
    role = session_active_role(request, available) or primary_role
    meta = {
        "user_id": request.user.pk,
        "employee_id": meta["employee_id"],
        "role": role,
        "primary_role": primary_role,
        "status": meta["status"],
        "available_roles": available,
    }
    session = getattr(request, "session", None)
    if session is not None and session.get(SESSION_PROFILE_KEY) != meta:
        session[SESSION_PROFILE_KEY] = meta
    setattr(request, REQUEST_META_ATTR, meta)
    return meta


def store_profile_session(request, profile):
    request.session[SESSION_PROFILE_KEY] = {
        "user_id": profile.user_id,
        "employee_id": profile.employee_id,
        "role": profile.role,
        "primary_role": profile.role,
        "status": profile.status,
        "available_roles": profile.role_values(),
    }
    setattr(request, REQUEST_META_ATTR, request.session[SESSION_PROFILE_KEY])
    setattr(request, REQUEST_PROFILE_ATTR, profile)


def employee_session_is_current(request) -> bool:
    """Employee sessions are concurrent across devices and tabs.

    ``EmployeeProfile.active_session_key`` is still stamped on login for
    diagnostics (most recent sign-in), but it is not enforced — a newer
    login must not sign out older sessions on the same computer or elsewhere.
    """
    return True


def clear_profile_session(request):
    if SESSION_PROFILE_KEY in request.session:
        del request.session[SESSION_PROFILE_KEY]
    request.session.pop(SESSION_ACTIVE_ROLE_KEY, None)
    request.session.pop(SESSION_ACTING_SHOP_KEY, None)
    if hasattr(request, REQUEST_META_ATTR):
        delattr(request, REQUEST_META_ATTR)
    if hasattr(request, REQUEST_PROFILE_ATTR):
        delattr(request, REQUEST_PROFILE_ATTR)



def role_home_url_name(role):
    return ROLE_HOME_URL_NAMES.get(role, "employees:role_employee")


def role_url_segment(role):
    return ROLE_URL_SEGMENTS.get(role, "employee")


def role_from_url_segment(segment):
    return ROLE_FROM_URL_SEGMENT.get(segment)


def redirect_to_role_home(profile):
    role = profile.role if hasattr(profile, "role") else profile.get("role")
    return redirect(role_home_url_name(role))


def _wants_json(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    requested_with = (request.headers.get("X-Requested-With") or "").lower()
    return (
        "application/json" in accept
        or requested_with == "xmlhttprequest"
        or (request.POST.get("ajax") or "") == "1"
    )


def _login_redirect(request, *, message=None, level="error"):
    """Send anonymous / blocked users to employee login, preserving next."""
    login_url = reverse("employees:login")
    next_path = request.get_full_path()
    redirect_to = login_url
    if next_path and next_path != login_url:
        redirect_to = f"{login_url}?{urlencode({'next': next_path})}"

    if _wants_json(request):
        from django.http import JsonResponse

        from .portal_auth import end_employee_session

        end_employee_session(request)
        return JsonResponse(
            {
                "ok": False,
                "error": "session_expired",
                "message": message or "Your session expired. Sign in again.",
                "login_url": redirect_to,
            },
            status=401,
        )

    if message:
        getattr(messages, level)(request, message)
    return redirect(redirect_to)


def active_employee_required(view_func):
    """Require an authenticated employee with Active status."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _login_redirect(
                request,
                message="Your session expired. Sign in again.",
                level="info",
            )

        meta = get_employee_meta_for_request(request)
        if meta is None:
            clear_profile_session(request)
            return _login_redirect(
                request,
                message="No employee profile is linked to this account.",
            )

        if meta["status"] == EmployeeStatus.PENDING_APPROVAL:
            return redirect("employees:pending")

        if meta["status"] == EmployeeStatus.SUSPENDED:
            return _login_redirect(
                request,
                message="Your employee account is suspended. Contact your administrator.",
            )

        if meta["status"] != EmployeeStatus.ACTIVE:
            from shops.services import get_company_display_name

            return _login_redirect(
                request,
                message=(
                    f"Your employee account cannot access {get_company_display_name()} yet."
                ),
            )

        return view_func(request, *args, **kwargs)

    return wrapper


def role_required(*allowed_roles):
    """Require Active status and one of the allowed roles."""

    def decorator(view_func):
        @wraps(view_func)
        @active_employee_required
        def wrapper(request, *args, **kwargs):
            meta = get_employee_meta_for_request(request)
            if meta is None or meta["role"] not in allowed_roles:
                messages.error(
                    request,
                    "You do not have permission to access that page.",
                    extra_tags="unauthorized",
                )
                if meta is not None:
                    return redirect(role_home_url_name(meta["role"]))
                return redirect("employees:login")
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
            messages.error(
                request,
                "You do not have permission to access HR Management.",
                extra_tags="unauthorized",
            )
            if profile is not None:
                return redirect_to_role_home(profile)
            return redirect("employees:login")
        return view_func(request, *args, **kwargs)

    return wrapper
