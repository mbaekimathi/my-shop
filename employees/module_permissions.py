"""Runtime checks for EmployeeModulePermission (HR permissions matrix).

Semantics match the permissions UI:
- Missing row ⇒ allowed (default allow)
- Explicit allowed=False ⇒ denied
- Super Admin always allowed (cannot be locked out of the system)
- HR staff always keep access to hr-management/permissions so they can recover
"""

from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse

from .access import HR_STAFF_ROLES, redirect_to_role_home
from .models import EmployeeModulePermission, EmployeeRole
from .permissions_catalog import PERMISSION_MODULE_BY_SLUG, is_valid_permission_key


REQUEST_PERM_CACHE_ATTR = "_employee_module_perm_cache"


def normalize_submodule(module_slug: str, submodule_slug: str) -> str:
    """Map runtime slugs onto catalog submodule keys."""
    slug = (submodule_slug or "").strip()
    if module_slug == "analytics" and slug in {"overview", ""}:
        return "view"
    if module_slug == "stock-management" and slug in {
        "serial-movements",
        "return-clients",
    }:
        return "serials"
    return slug


def _cache_for(profile) -> dict:
    cache = getattr(profile, REQUEST_PERM_CACHE_ATTR, None)
    if cache is not None:
        return cache
    rows = EmployeeModulePermission.objects.filter(employee_id=profile.pk).values_list(
        "module_slug", "submodule_slug", "allowed"
    )
    cache = {
        (module_slug, submodule_slug): allowed
        for module_slug, submodule_slug, allowed in rows
    }
    setattr(profile, REQUEST_PERM_CACHE_ATTR, cache)
    return cache


def employee_may(profile, module_slug: str, submodule_slug: str) -> bool:
    """Return whether profile may perform module/submodule."""
    if profile is None:
        return False

    module_slug = (module_slug or "").strip()
    submodule_slug = normalize_submodule(module_slug, submodule_slug)

    if not module_slug or not submodule_slug:
        return False

    if getattr(profile, "role", None) == EmployeeRole.SUPER_ADMIN:
        return True

    # Never lock HR staff out of the permissions matrix itself.
    if (
        getattr(profile, "role", None) in HR_STAFF_ROLES
        and module_slug == "hr-management"
        and submodule_slug == "permissions"
    ):
        return True

    if not is_valid_permission_key(module_slug, submodule_slug):
        # Unknown keys are not gated by this matrix.
        return True

    cache = _cache_for(profile)
    return cache.get((module_slug, submodule_slug), True)


def employee_may_any(profile, module_slug: str) -> bool:
    """True if the employee may use at least one submodule in the module."""
    module = PERMISSION_MODULE_BY_SLUG.get(module_slug)
    if module is None:
        return True
    if getattr(profile, "role", None) == EmployeeRole.SUPER_ADMIN:
        return True
    return any(
        employee_may(profile, module_slug, submodule["slug"])
        for submodule in module["submodules"]
    )


def module_capabilities(profile, module_slug: str) -> dict[str, bool]:
    """Map submodule slug → allowed for one module."""
    module = PERMISSION_MODULE_BY_SLUG.get(module_slug)
    if module is None:
        return {}
    return {
        submodule["slug"]: employee_may(profile, module_slug, submodule["slug"])
        for submodule in module["submodules"]
    }


def permission_denied_response(
    request,
    profile,
    *,
    message: str | None = None,
    as_json: bool = False,
):
    text = message or "You do not have permission to perform this action."
    if as_json:
        return JsonResponse({"ok": False, "error": text, "code": "unauthorized"}, status=403)
    messages.error(request, text, extra_tags="unauthorized")
    if profile is not None:
        return redirect_to_role_home(profile)
    from django.shortcuts import redirect

    return redirect("employees:login")


def require_module_permission(
    request,
    profile,
    module_slug: str,
    submodule_slug: str,
    *,
    as_json: bool = False,
    message: str | None = None,
):
    """Return a deny response when not allowed; otherwise None."""
    if employee_may(profile, module_slug, submodule_slug):
        return None
    label = f"{module_slug}/{normalize_submodule(module_slug, submodule_slug)}"
    return permission_denied_response(
        request,
        profile,
        message=message or f"You do not have permission for {label}.",
        as_json=as_json,
    )


def ensure_employee_may(profile, module_slug: str, submodule_slug: str, *, message=None):
    """Raise ValidationError when profile may not perform the capability."""
    from django.core.exceptions import ValidationError

    if employee_may(profile, module_slug, submodule_slug):
        return
    label = f"{module_slug}/{normalize_submodule(module_slug, submodule_slug)}"
    raise ValidationError(message or f"You do not have permission for {label}.")


def my_shop_capabilities(profile) -> dict[str, bool]:
    """Capability map for MY-SHOP UI (staff-code authorisation)."""
    return module_capabilities(profile, "my-shop")
