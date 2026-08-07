from django.urls import reverse

from .access import (
    SESSION_PROFILE_KEY,
    get_profile_for_request,
    get_employee_meta_for_request,
    role_home_url_name,
)
from .models import EmployeeRole, EmployeeStatus
from .workspace import ROLE_PAGE_META, workspace_back_url


def employee_workspace(request):
    """Expose workspace chrome data — one profile load per request max."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    meta = get_employee_meta_for_request(request) or request.session.get(SESSION_PROFILE_KEY) or {}
    status = meta.get("status")
    role = meta.get("role")

    if status != EmployeeStatus.ACTIVE or not role:
        return {}

    profile = get_profile_for_request(request)
    if profile is None or not profile.is_active_employee:
        return {}

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
        }
    }
