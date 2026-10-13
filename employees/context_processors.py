from django.urls import reverse

from .access import (
    SESSION_PROFILE_KEY,
    get_profile_for_request,
    get_employee_meta_for_request,
    role_home_url_name,
)
from .models import EmployeeRole, EmployeeStatus
from .workspace import ROLE_PAGE_META, workspace_back_url


def _company_brand_context():
    try:
        from shops.services import get_company_display_name

        name = get_company_display_name()
    except Exception:
        name = "MY-SHOP"
    return {"company_name": name}


def _portal_workspace_context(portal_shop):
    home_url = reverse(
        "employees:my_shop_workspace", kwargs={"shop_id": portal_shop.pk}
    )
    return {
        "workspace": {
            "profile": None,
            "role": None,
            "role_label": "Shop portal",
            "status_label": "Signed in",
            "meta": {
                "title": portal_shop.name,
                "headline": portal_shop.name,
                "summary": portal_shop.location,
                "icon": "store",
            },
            "home_url": home_url,
            "dashboard_url": home_url,
            "profile_url": reverse("employees:shop_login"),
            "logout_url": reverse("employees:shop_logout"),
            "show_back": False,
            "back_url": None,
            "is_shop_portal": True,
            "shop": portal_shop,
        },
        "role_label": "Shop portal",
        "shop_portal": True,
        "portal_shop": portal_shop,
    }


def _employee_workspace_context(request, profile, role):
    role_meta = ROLE_PAGE_META.get(role, ROLE_PAGE_META[EmployeeRole.EMPLOYEE])
    home_url = reverse(role_home_url_name(role))
    back_url = workspace_back_url(request, role)
    return {
        "workspace": {
            "profile": profile,
            "role": role,
            "role_label": profile.get_role_display(),
            "status_label": profile.get_status_display(),
            "meta": role_meta,
            "home_url": home_url,
            "dashboard_url": home_url,
            "profile_url": reverse("employees:profile"),
            "logout_url": reverse("employees:logout"),
            "show_back": back_url is not None,
            "back_url": back_url,
            "is_shop_portal": False,
        }
    }


def _request_shop_id(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    kwargs = getattr(match, "kwargs", None) or {}
    value = kwargs.get("shop_id")
    return str(value) if value is not None else ""


def employee_workspace(request):
    """Expose workspace chrome data — one profile load per request max."""
    context = _company_brand_context()
    try:
        from shops.services import developer_payment_prompt_for_request

        prompt = developer_payment_prompt_for_request(request)
        if prompt:
            context["developer_payment_prompt"] = prompt
    except Exception:
        pass

    from shops.session import resolve_portal_shop

    portal_shop = resolve_portal_shop(request)
    request_shop_id = _request_shop_id(request)

    # On a shop-floor URL that matches the portal shop, prefer portal chrome
    # even when an employee session is also active in the same browser.
    if (
        portal_shop is not None
        and request_shop_id
        and str(portal_shop.pk) == request_shop_id
    ):
        context.update(_portal_workspace_context(portal_shop))
        return context

    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        meta = get_employee_meta_for_request(request) or request.session.get(
            SESSION_PROFILE_KEY
        ) or {}
        status = meta.get("status")
        role = meta.get("role")

        if status == EmployeeStatus.ACTIVE and role:
            profile = get_profile_for_request(request)
            if profile is not None and profile.is_active_employee:
                context.update(_employee_workspace_context(request, profile, role))
                return context

    if portal_shop is None:
        return context

    context.update(_portal_workspace_context(portal_shop))
    return context
