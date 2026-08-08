"""Active shop session helpers for MY-SHOP workspace entry."""

from functools import wraps

from items.services import actionable_shops_for_profile

SESSION_SHOP_KEY = "active_shop_id"
SESSION_SHOP_PORTAL_KEY = "shop_portal_auth"


def shops_for_profile(profile):
    """Active shops the employee may enter via MY-SHOP."""
    return actionable_shops_for_profile(profile)


def set_active_shop(request, shop):
    request.session[SESSION_SHOP_KEY] = str(shop.pk)


def clear_active_shop(request):
    if SESSION_SHOP_KEY in request.session:
        del request.session[SESSION_SHOP_KEY]


def get_active_shop_id(request):
    value = request.session.get(SESSION_SHOP_KEY)
    return str(value) if value else ""


def is_shop_portal_session(request) -> bool:
    return bool(request.session.get(SESSION_SHOP_PORTAL_KEY))


def set_shop_portal_session(request, shop):
    """Mark the browser as signed in to a shop via the public shop portal."""
    set_active_shop(request, shop)
    request.session[SESSION_SHOP_PORTAL_KEY] = True


def clear_shop_portal_session(request):
    clear_active_shop(request)
    if SESSION_SHOP_PORTAL_KEY in request.session:
        del request.session[SESSION_SHOP_PORTAL_KEY]


def resolve_portal_shop(request):
    """Return the portal-authenticated shop, or None."""
    if not is_shop_portal_session(request):
        return None

    shop_id = get_active_shop_id(request)
    if not shop_id:
        clear_shop_portal_session(request)
        return None

    from .models import Shop

    shop = (
        Shop.objects.filter(pk=shop_id, is_hidden=False, is_suspended=False)
        .only("id", "name", "location", "image", "is_suspended", "is_hidden")
        .first()
    )
    if shop is None:
        clear_shop_portal_session(request)
        return None
    return shop


def resolve_active_shop(request, profile):
    """Return the session shop if it is still allowed for this profile."""
    shop_id = get_active_shop_id(request)
    if not shop_id:
        return None

    allowed = {str(shop.pk): shop for shop in shops_for_profile(profile)}
    shop = allowed.get(shop_id)
    if shop is None:
        clear_active_shop(request)
        return None
    return shop


def get_shop_for_profile(profile, shop_id):
    """Load a specific allowed shop or None."""
    shop_id = str(shop_id or "").strip()
    if not shop_id:
        return None
    for shop in shops_for_profile(profile):
        if str(shop.pk) == shop_id:
            return shop
    return None


def shop_floor_required(view_func):
    """
    Allow shop floor routes when either:
    - a shop portal session is active, or
    - an active employee is signed in.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if resolve_portal_shop(request) is not None:
            return view_func(request, *args, **kwargs)

        from employees.access import active_employee_required

        return active_employee_required(view_func)(request, *args, **kwargs)

    return wrapper
