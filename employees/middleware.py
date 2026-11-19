"""Rolling idle timeout for employee sessions.

A session stays alive while the user is actually interacting: page loads,
form posts, mutations, and fetch calls stamped by session-guard.js with the
elapsed time since the last real interaction (X-MyShop-Activity header).

Background pollers (connectivity ping, stock-request status, inbox refresh)
carry no interaction evidence, so they can only *detect* an expired session
— they never postpone the timeout on an unattended terminal.
"""

import time

from django.conf import settings

IDLE_SESSION_KEY = "_emp_idle_ts"

# Automated queue replay must not count as user activity — it can fire on a
# connectivity change without anyone present.
_AUTO_SYNC_PATHS = frozenset(
    {"/employees/api/sync/", "/employees/api/sync/register/"}
)


def _reported_activity(request, now):
    """Interaction time evidenced by the client header, as a unix timestamp."""
    raw = request.headers.get("X-MyShop-Activity")
    try:
        elapsed_ms = int(raw)
    except (TypeError, ValueError):
        return 0
    if elapsed_ms < 0:
        return 0
    return max(now - elapsed_ms // 1000, 0)


def _request_is_activity(request):
    """True when the request itself is a user interaction."""
    if request.headers.get("Sec-Fetch-Mode") == "navigate":
        return True
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return request.path not in _AUTO_SYNC_PATHS
    # Fallback for browsers that do not send Sec-Fetch-* headers: a plain
    # browser navigation asks for HTML, fetch()/polls do not.
    if (
        not request.headers.get("Sec-Fetch-Mode")
        and not request.headers.get("Sec-Fetch-Dest")
        and not request.headers.get("X-Requested-With")
        and "text/html" in (request.headers.get("Accept") or "")
    ):
        return True
    return False


class EmployeeIdleTimeoutMiddleware:
    """Sign out employees idle longer than the configured session timeout.

    The limit is a company POS setting: ``enable_employee_session_timeout``
    + ``employee_session_timeout_minutes``. When the toggle is off (the
    default) there is no idle expiry — sessions end only on logout.
    EMPLOYEE_IDLE_TIMEOUT_SECONDS remains as the fallback when the settings
    row cannot be read.

    Runs after AuthenticationMiddleware. The expiry check compares against
    the last *prior* interaction (stored stamp or a reported header time) —
    the current request's own activity cannot revive a session that already
    timed out.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = int(
            getattr(settings, "EMPLOYEE_IDLE_TIMEOUT_SECONDS", 60 * 60 * 2)
        )

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        try:
            from shops.services import employee_idle_timeout_seconds

            timeout = employee_idle_timeout_seconds()
        except Exception:
            timeout = self.timeout
        if timeout is None:
            return self.get_response(request)

        session = request.session
        now = int(time.time())
        try:
            last = int(session.get(IDLE_SESSION_KEY) or 0)
        except (TypeError, ValueError):
            last = 0
        reported = _reported_activity(request, now)
        prior = max(last, reported)

        if prior and now - prior > timeout:
            from .portal_auth import end_employee_session

            end_employee_session(request)
            return self.get_response(request)

        if _request_is_activity(request):
            candidate = now
        else:
            candidate = reported
        if not prior:
            # First authenticated request after login/deploy seeds the clock.
            candidate = now
        if candidate > last:
            session[IDLE_SESSION_KEY] = candidate

        return self.get_response(request)


class SupportViewOnlyMiddleware:
    """IT support acting in another role gets a read-only pass.

    When the session's picked role differs from the profile's IT-support
    primary role (``profile.view_only_role`` — set by
    ``access._apply_active_role``), the picked workspace renders normally —
    cashier day sessions even count as "theirs" for display — but every
    unsafe method outside a small session-navigation allowlist is denied.
    The allowlist keeps the pass moving: picking a role again, re-picking
    the acting-shop view, and unlocking/leaving the floor are session
    writes, not business data.
    """

    # URL names that must keep accepting POSTs for a view-only session —
    # they only move session keys, never business records.
    _SAFE_POST_URL_NAMES = (
        "employees:select_role",
        "employees:my_shop_select",
        "employees:my_shop_switch",
        "employees:my_shop_leave",
        "employees:shop_login",
        "employees:logout",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._safe_paths = None

    def _safe_post_paths(self):
        if self._safe_paths is None:
            from django.urls import reverse

            paths = set()
            for name in self._SAFE_POST_URL_NAMES:
                try:
                    paths.add(reverse(name))
                except Exception:
                    continue
            self._safe_paths = paths
        return self._safe_paths

    def __call__(self, request):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return self.get_response(request)

        from .access import get_profile_for_request, role_home_url_name

        profile = get_profile_for_request(request)
        if (
            profile is None
            or not getattr(profile, "view_only_role", False)
            or request.path_info in self._safe_post_paths()
        ):
            return self.get_response(request)

        message = (
            f"View only — you're browsing as {profile.get_role_display()}. "
            "Switch back to your IT support role to make changes."
        )
        if (
            "application/json" in (request.headers.get("Accept") or "")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or (request.POST.get("ajax") or "") == "1"
        ):
            from django.http import JsonResponse

            return JsonResponse(
                {"ok": False, "error": message, "view_only": True},
                status=403,
            )

        from django.contrib import messages
        from django.shortcuts import redirect
        from django.utils.http import url_has_allowed_host_and_scheme

        messages.error(request, message, extra_tags="unauthorized")
        referer = request.headers.get("Referer") or ""
        if referer and url_has_allowed_host_and_scheme(
            referer, allowed_hosts={request.get_host()}
        ):
            return redirect(referer)
        return redirect(role_home_url_name(profile.role))


class CashierSessionBlockMiddleware:
    """Lock the cashier workspace while a colleague's day session is open.

    A cashier may only work on a shop day they opened themselves. When an
    allocated shop's open ``ShopDaySession`` belongs to another employee
    (e.g. the previous shift never closed), every page under the
    ``/shop-cashier/`` segment renders a blocking notice naming whoever must
    close it; the matching ``my-shop/*`` endpoints are denied separately in
    ``shops.views``.

    Runs last so the short-circuited response still passes through every
    outer middleware's response processing. Shop portal sessions and
    non-cashier roles are unaffected — a manager or the shop portal can
    always close the day as an escape hatch.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        from .access import get_profile_for_request, role_url_segment
        from .models import EmployeeRole

        segment = role_url_segment(EmployeeRole.SHOP_CASHIER)
        if not request.path_info.startswith(f"/{segment}/"):
            return self.get_response(request)

        profile = get_profile_for_request(request)
        if (
            profile is None
            or profile.role != EmployeeRole.SHOP_CASHIER
            or not profile.is_active_employee
        ):
            return self.get_response(request)

        from .workspace import (
            cashier_session_blocks,
            render_cashier_session_blocked,
        )

        blocks = cashier_session_blocks(profile)
        if not blocks:
            return self.get_response(request)

        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if wants_json or request.method != "GET":
            from django.http import JsonResponse

            first = blocks[0]
            opener = first["opener"]
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        f"{first['shop'].name} is still open under {opener}. "
                        f"Ask {opener} to close their session first."
                    ),
                },
                status=403,
            )
        return render_cashier_session_blocked(request, profile, blocks=blocks)
