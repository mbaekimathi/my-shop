"""Active shop session helpers for MY-SHOP workspace entry."""

from items.services import actionable_shops_for_profile

SESSION_SHOP_KEY = "active_shop_id"


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
