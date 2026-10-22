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
    switch=False,
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
        "switch": bool(switch),
    }


def render_portal_login(request, **kwargs):
    return render(request, PORTAL_LOGIN_TEMPLATE, portal_login_context(**kwargs))


def clear_employee_auth(request):
    """End the employee session while preserving any shop portal keys."""
    from shops.session import (
        migrate_shop_session_stamp,
        restore_shop_portal_session,
        snapshot_shop_portal_session,
    )

    from .access import SESSION_PROFILE_KEY, REQUEST_META_ATTR, REQUEST_PROFILE_ATTR
    from .models import EmployeeProfile

    old_key = request.session.session_key
    user_pk = getattr(request.user, "pk", None)
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

    # Release the single-session stamp only when this session still owns it —
    # a superseded session must not clear the newer device's stamp.
    if was_authenticated and user_pk and old_key:
        EmployeeProfile.objects.filter(
            user_id=user_pk, active_session_key=old_key
        ).update(active_session_key="")

    restore_shop_portal_session(request, shop_snap)
    # logout() flushed the session id — the shop stamp follows the new key.
    migrate_shop_session_stamp(request, old_key)


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
    from shops.session import (
        bind_shop_portal_session,
        persist_shop_portal_session,
        rotate_session_key,
        set_shop_portal_session,
    )

    old_key = request.session.session_key
    set_shop_portal_session(request, shop)
    rotate_session_key(request)
    persist_shop_portal_session(request)
    # The rotation changed the session id — re-point the employee stamp so a
    # coexisting employee session on this browser isn't superseded by itself.
    _migrate_employee_session_stamp(request, old_key)
    bind_shop_portal_session(request, shop)


def _migrate_employee_session_stamp(request, old_key):
    """Re-point the employee's session stamp after the session id rotated."""
    from .models import EmployeeProfile

    new_key = request.session.session_key
    user = getattr(request, "user", None)
    if (
        not old_key
        or not new_key
        or new_key == old_key
        or user is None
        or not user.is_authenticated
    ):
        return
    EmployeeProfile.objects.filter(
        user_id=user.pk, active_session_key=old_key
    ).update(active_session_key=new_key)


def begin_employee_session(request, user, profile):
    """Employee sign-in; leaves any shop portal session intact."""
    from django.contrib.auth import login

    from shops.session import (
        migrate_shop_session_stamp,
        restore_shop_portal_session,
        snapshot_shop_portal_session,
    )

    from .access import store_profile_session
    from .models import EmployeeProfile

    # login() flushes the session when it belonged to a different user;
    # keep the shop portal keys either way.
    old_key = request.session.session_key
    shop_snap = snapshot_shop_portal_session(request)
    login(request, user)
    restore_shop_portal_session(request, shop_snap)
    store_profile_session(request, profile)
    # login() rotated/flushed the session id — the shop stamp follows the new
    # key so a coexisting shop portal session isn't superseded by itself.
    migrate_shop_session_stamp(request, old_key)
    EmployeeProfile.objects.filter(pk=profile.pk).update(
        active_session_key=request.session.session_key or ""
    )
