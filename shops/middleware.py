"""Middleware for long-lived shop floor sessions."""


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
