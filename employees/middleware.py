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
    """Sign out employees idle longer than EMPLOYEE_IDLE_TIMEOUT_SECONDS.

    Runs after AuthenticationMiddleware. The expiry check compares against
    the last *prior* interaction (stored stamp or a reported header time) —
    the current request's own activity cannot revive a session that already
    timed out.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = int(
            getattr(settings, "EMPLOYEE_IDLE_TIMEOUT_SECONDS", 15 * 60)
        )

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        session = request.session
        now = int(time.time())
        try:
            last = int(session.get(IDLE_SESSION_KEY) or 0)
        except (TypeError, ValueError):
            last = 0
        reported = _reported_activity(request, now)
        prior = max(last, reported)

        if prior and now - prior > self.timeout:
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
