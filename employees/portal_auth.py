"""Shared portal sign-in helpers for employee + shop login."""

from django.contrib.auth import logout
from django.contrib.auth.models import AnonymousUser
from django.shortcuts import render

PORTAL_LOGIN_TEMPLATE = "core/portal_login.html"


def portal_login_context(
    *,
    login_mode="employee",
    employee_error=None,
    shop_error=None,
    next_url="",
    employee_username="",
    shop_login_code="",
    rate_limited=False,
):
    if rate_limited and not employee_error and not shop_error:
        message = "Too many sign-in attempts. Wait a moment and try again."
        if login_mode == "shop":
            shop_error = message
        else:
            employee_error = message
    return {
        "login_mode": login_mode if login_mode in {"employee", "shop"} else "employee",
        "employee_error": employee_error,
        "shop_error": shop_error,
        "next_url": next_url or "",
        "employee_username": employee_username or "",
        "shop_login_code": shop_login_code or "",
    }


def render_portal_login(request, **kwargs):
    return render(request, PORTAL_LOGIN_TEMPLATE, portal_login_context(**kwargs))


def clear_employee_auth(request):
    """End the employee session while preserving any shop portal keys."""
    from shops.session import restore_shop_portal_session, snapshot_shop_portal_session

    from .access import SESSION_PROFILE_KEY, REQUEST_META_ATTR, REQUEST_PROFILE_ATTR

    shop_snap = snapshot_shop_portal_session(request)

    was_authenticated = bool(
        getattr(request, "user", None) is not None and request.user.is_authenticated
    )
    if was_authenticated:
        logout(request)
    else:
        request.user = AnonymousUser()

    if SESSION_PROFILE_KEY in request.session:
        del request.session[SESSION_PROFILE_KEY]
    if hasattr(request, REQUEST_META_ATTR):
        delattr(request, REQUEST_META_ATTR)
    if hasattr(request, REQUEST_PROFILE_ATTR):
        delattr(request, REQUEST_PROFILE_ATTR)

    restore_shop_portal_session(request, shop_snap)


def end_shop_portal_session(request):
    """End only the shop portal session."""
    from shops.session import clear_shop_portal_session

    clear_shop_portal_session(request)


def end_employee_session(request):
    """End only the employee session."""
    clear_employee_auth(request)


def end_all_portal_sessions(request):
    """Fully end shop portal and employee sessions (hard clear)."""
    from shops.session import clear_shop_portal_session

    clear_shop_portal_session(request)
    clear_employee_auth(request)
    request.session.flush()


def begin_shop_portal_session(request, shop):
    """Shop portal sign-in; leaves any employee session intact."""
    from shops.session import persist_shop_portal_session, set_shop_portal_session

    set_shop_portal_session(request, shop)
    request.session.cycle_key()
    persist_shop_portal_session(request)


def begin_employee_session(request, user, profile):
    """Employee sign-in; leaves any shop portal session intact."""
    from django.contrib.auth import login

    from .access import store_profile_session

    login(request, user)
    store_profile_session(request, profile)
