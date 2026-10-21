"""Middleware for long-lived shop floor sessions."""

import logging

from django.contrib.sessions.middleware import SessionMiddleware

from .session import SESSION_ROTATED_TO_KEY, write_rotation_pointer

logger = logging.getLogger(__name__)

# How many pointer hops a stale sessionid cookie may follow before giving up.
MAX_ROTATION_HOPS = 3


class RotatedSessionMiddleware(SessionMiddleware):
    """Bridge session-id rotations across concurrently open tabs.

    Shop portal and employee sign-ins cycle (or flush) the session id, which
    deletes the old session record. A second open tab can still send the
    previous sessionid cookie while the login response is in flight, and would
    otherwise see an empty session — looking signed out.

    On response we write a short-lived pointer under the retired key whenever
    the session id changed; on request we follow it so the lagging tab
    resolves the rotated session and refreshes its cookie.
    """

    def process_request(self, request):
        super().process_request(request)
        request._incoming_session_key = request.session.session_key
        request._followed_session_pointer = False

        seen = set()
        for _ in range(MAX_ROTATION_HOPS):
            target = request.session.get(SESSION_ROTATED_TO_KEY)
            if not target or target in seen:
                break
            seen.add(request.session.session_key)
            migrated = self.SessionStore(session_key=target)
            if not migrated.load():
                break
            request.session = migrated
            request._followed_session_pointer = True

        if request._followed_session_pointer:
            # Force a save so the response refreshes the browser's sessionid
            # cookie to the rotated key on the first stale-cookie request.
            request.session.modified = True

    def process_response(self, request, response):
        response = super().process_response(request, response)
        if getattr(request, "_followed_session_pointer", False):
            return response
        old_key = getattr(request, "_incoming_session_key", None)
        session = getattr(request, "session", None)
        new_key = getattr(session, "session_key", None)
        if not old_key or not new_key or old_key == new_key:
            return response
        if session.is_empty():
            return response
        write_rotation_pointer(self.SessionStore, old_key, new_key)
        return response


class ShopFloorSessionMiddleware:
    """
    Extend shop portal and employee-unlocked shop sessions on every request.

    Session expiry is only refreshed when code calls persist_shop_portal_session().
    Views that use shop_floor_required do that, but lightweight endpoints (ping,
    static assets with session cookies, etc.) may not — so the session can still
    expire at SESSION_COOKIE_AGE (14 days by default) even for shop portal users.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        if session is not None:
            from shops.session import (
                get_active_shop_id,
                is_shop_portal_session,
                persist_shop_portal_session,
            )

            if is_shop_portal_session(request) or (
                get_active_shop_id(request)
                and getattr(request.user, "is_authenticated", False)
            ):
                persist_shop_portal_session(request)
        return self.get_response(request)
