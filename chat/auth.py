"""Shop session auth — same credentials as the MY-SHOP shop portal.

A shop signs in with its shared 6-digit `login_code` and password (the hash
lives in `shops_shop.password_hash`, written by MY-SHOP with Django's
hasher). Verified sessions store the shop id under a namespaced session key
independent of any MY-SHOP cookies.

Employees sign in with their 6-digit staff ID + password. Admins and IT
land on the all-shops view; store managers get the central-store workspace;
other roles get a role home (tools appear as they are added).
"""

import re
from functools import wraps

from django.contrib.auth.hashers import check_password, make_password
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .myshop_models import Shop

SESSION_SHOP_KEY = "chatbot_shop_id"
SESSION_EMPLOYEE_KEY = "chatbot_employee_id"
LOGIN_CODE_RE = re.compile(r"^\d{6}$")

# Every MY-SHOP employee role that may sign in via the Employee tab.
ALL_EMPLOYEE_ROLES = {
    "employee",
    "super_admin",
    "company_manager",
    "shop_manager",
    "store_manager",
    "shop_cashier",
    "it_support",
    "rider",
}

# Roles that may open the combined all-shops assistant.
ALL_SHOPS_ROLES = {
    "super_admin",
    "company_manager",
    "it_support",
}

# Back-compat alias — previously gated who could sign in at all.
EMPLOYEE_ROLES = ALL_EMPLOYEE_ROLES

ROLE_URL_SEGMENTS = {
    "employee": "employee",
    "super_admin": "super-admin",
    "company_manager": "company-manager",
    "shop_manager": "shop-manager",
    "store_manager": "store-manager",
    "shop_cashier": "shop-cashier",
    "it_support": "it-support",
    "rider": "rider",
}

ROLE_FROM_URL_SEGMENT = {slug: role for role, slug in ROLE_URL_SEGMENTS.items()}

ROLE_LABELS = {
    "employee": "Employee",
    "super_admin": "Super Admin",
    "company_manager": "Company Manager",
    "shop_manager": "Shop Manager",
    "store_manager": "Store Manager",
    "shop_cashier": "Shop Cashier",
    "it_support": "IT Support",
    "rider": "Rider",
}

ROLE_ICONS = {
    "employee": "badge-check",
    "super_admin": "shield-check",
    "company_manager": "building-2",
    "shop_manager": "store",
    "store_manager": "warehouse",
    "shop_cashier": "scan-barcode",
    "it_support": "monitor-cog",
    "rider": "bike",
}

_DUMMY_HASH = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = make_password("chatbot-dummy-not-a-real-secret")
    return _DUMMY_HASH


def can_view_all_shops(role: str) -> bool:
    return (role or "") in ALL_SHOPS_ROLES


def role_slug(role: str) -> str:
    return ROLE_URL_SEGMENTS.get(role or "", "employee")


def role_from_slug(slug: str) -> str | None:
    return ROLE_FROM_URL_SEGMENT.get(slug or "")


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role or "", (role or "Employee").replace("_", " ").title())


def role_icon(role: str) -> str:
    return ROLE_ICONS.get(role or "", "badge-check")


def role_home_url(role: str) -> str:
    """Path for the signed-in employee's landing page."""
    if can_view_all_shops(role):
        return reverse("chat:all_shops")
    return reverse("chat:role_home", kwargs={"slug": role_slug(role)})


def authenticate_shop(login_code: str, password: str):
    """Return the Shop on valid credentials, else None.

    Suspended/hidden shops cannot sign in. A dummy check runs when the code
    is unknown so response timing does not reveal which codes exist.
    """
    code = re.sub(r"\D+", "", login_code or "")
    password = password or ""
    if not LOGIN_CODE_RE.match(code) or not password:
        return None
    shop = Shop.objects.filter(login_code=code).first()
    if shop is None or shop.is_hidden or shop.is_suspended:
        check_password(password, shop.password_hash if shop else _dummy_hash())
        return None
    if not check_password(password, shop.password_hash or _dummy_hash()):
        return None
    return shop


def authenticate_employee(employee_id: str, password: str):
    """Return the active staff profile on valid credentials, else None.

    Any active MY-SHOP employee role may sign in. Admins / IT are routed to
    all-shops; store managers get the central-store workspace; other roles
    land on their role home.
    """
    code = re.sub(r"\D+", "", employee_id or "")
    password = password or ""
    if not LOGIN_CODE_RE.match(code) or not password:
        return None
    from django.contrib.auth.models import User
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, role FROM employees_employeeprofile "
            "WHERE employee_id=%s AND status='active' LIMIT 1",
            [code],
        )
        row = cur.fetchone()
    if row is None or row[2] not in ALL_EMPLOYEE_ROLES:
        check_password(password, _dummy_hash())
        return None
    user = User.objects.filter(pk=row[1]).first()
    if user is None or not check_password(password, user.password):
        return None
    return {
        "id": row[0],
        "role": row[2],
        "username": user.username,
    }


def login_employee(request, employee: dict) -> None:
    request.session[SESSION_EMPLOYEE_KEY] = employee["id"]
    request.session["chatbot_employee_role"] = employee["role"]


def current_employee(request):
    """Signed-in staff member or None."""
    emp_id = request.session.get(SESSION_EMPLOYEE_KEY)
    if not emp_id:
        return None
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT id, role FROM employees_employeeprofile "
            "WHERE id=%s AND status='active' LIMIT 1",
            [emp_id],
        )
        row = cur.fetchone()
    if row is None or row[1] not in ALL_EMPLOYEE_ROLES:
        request.session.pop(SESSION_EMPLOYEE_KEY, None)
        request.session.pop("chatbot_employee_role", None)
        return None
    return {"id": row[0], "role": row[1]}


def employee_allocated_to_shop(profile_id: int, shop_id: int) -> bool:
    """True when the employee profile is assigned to this shop.

    Mirrors MY-SHOP's `EmployeeProfile.assigned_shops` M2M — shop members are
    the staff allocated to work inside that branch.
    """
    if not profile_id or not shop_id:
        return False
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM employees_employeeprofile_assigned_shops "
            "WHERE employeeprofile_id=%s AND shop_id=%s LIMIT 1",
            [profile_id, shop_id],
        )
        return cur.fetchone() is not None


def employee_assigned_shop_ids(profile_id: int) -> list[int]:
    """Shop ids allocated to this employee profile (active shops only)."""
    if not profile_id:
        return []
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT shop_id FROM employees_employeeprofile_assigned_shops "
            "WHERE employeeprofile_id=%s",
            [profile_id],
        )
        ids = [int(r[0]) for r in cur.fetchall()]
    if not ids:
        return []
    return list(
        Shop.objects.filter(pk__in=ids, is_hidden=False, is_suspended=False)
        .order_by("name")
        .values_list("pk", flat=True)
    )


def login_shop(request, shop: Shop) -> None:
    request.session[SESSION_SHOP_KEY] = shop.pk


def logout_shop(request) -> None:
    request.session.pop(SESSION_SHOP_KEY, None)
    request.session.pop(SESSION_EMPLOYEE_KEY, None)
    request.session.pop("chatbot_employee_role", None)
    request.session.pop("chatbot_all_shops", None)


def current_shop(request):
    """Return the signed-in Shop or None. Refreshes suspension flags."""
    shop_id = request.session.get(SESSION_SHOP_KEY)
    if not shop_id:
        return None
    try:
        shop = Shop.objects.get(pk=shop_id)
    except Shop.DoesNotExist:
        request.session.pop(SESSION_SHOP_KEY, None)
        return None
    if shop.is_suspended or shop.is_hidden:
        request.session.pop(SESSION_SHOP_KEY, None)
        return None
    return shop


def shop_required(view):
    """Page guard — redirect to login when no shop session exists."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        shop = current_shop(request)
        if shop is None:
            return redirect(f"{_login_url()}?next={request.path}")
        request.chat_shop = shop
        return view(request, *args, **kwargs)

    return wrapper


def api_shop_required(view):
    """API guard — 401 JSON instead of a redirect."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        shop = current_shop(request)
        if shop is None:
            return JsonResponse(
                {"ok": False, "error": "authentication required"}, status=401
            )
        request.chat_shop = shop
        return view(request, *args, **kwargs)

    return wrapper


def api_store_manager_required(view):
    """API guard — Store Manager employee session required."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        employee = current_employee(request)
        if employee is None:
            return JsonResponse(
                {"ok": False, "error": "authentication required"}, status=401
            )
        if employee.get("role") != "store_manager":
            return JsonResponse(
                {"ok": False, "error": "store manager access required"}, status=403
            )
        request.chat_employee = employee
        return view(request, *args, **kwargs)

    return wrapper


def api_rider_required(view):
    """API guard — Rider employee session required."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        employee = current_employee(request)
        if employee is None:
            return JsonResponse(
                {"ok": False, "error": "authentication required"}, status=401
            )
        if employee.get("role") != "rider":
            return JsonResponse(
                {"ok": False, "error": "rider access required"}, status=403
            )
        request.chat_employee = employee
        return view(request, *args, **kwargs)

    return wrapper


def _login_url() -> str:
    return reverse("chat:login")
