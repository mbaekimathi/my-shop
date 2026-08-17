"""Shared business logic with caching for hot database paths."""

import re

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q

from .countries import COUNTRY_DIAL_CODES
from .models import SHOP_ALLOCATION_ROLES, SHOP_ASSIGNABLE_ROLES, EmployeeProfile, EmployeeRole, EmployeeStatus

EMPLOYEE_ID_RE = re.compile(r"^\d{6}$")
PHONE_RE = re.compile(r"^[\d\s\-()]{7,20}$")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_PROFILE_BYTES = 5 * 1024 * 1024  # 5 MB

EMPTY_EMPLOYEE_FORM = {
    "first_name": "",
    "last_name": "",
    "email": "",
    "phone_country_code": "+254",
    "phone_country_iso": "KE",
    "phone_number": "",
    "employee_id": "",
}

EMPLOYEE_ID_CACHE_PREFIX = "emp_id_taken:"
EMPLOYEE_ID_CACHE_TTL = 120  # seconds — short TTL; invalidated on registration


def verify_active_employee_code(code: str):
    """
    Return a profile when code matches any active row in the employees table.

    Role does not matter — shop manager, cashier, IT support, etc. all qualify
    as long as status is active.
    """
    code = (code or "").strip()
    if not EMPLOYEE_ID_RE.match(code):
        return None
    return (
        EmployeeProfile.objects.filter(
            employee_id=code,
            status=EmployeeStatus.ACTIVE,
            user__is_active=True,
        )
        .select_related("user")
        .first()
    )


def invalid_staff_code_message(code: str, *, shop=None) -> str:
    """Explain why a cart/staff 6-digit code was rejected."""
    code = (code or "").strip()
    if not code:
        return "Enter a valid active staff 6-digit ID."
    if not EMPLOYEE_ID_RE.match(code):
        return "Staff ID must be exactly 6 digits."

    shop_code = str(getattr(shop, "login_code", "") or "").strip()
    if shop_code and shop_code == code:
        return (
            "That's the shop branch code, not a staff ID. "
            "Enter the employee's personal 6-digit ID."
        )

    profile = (
        EmployeeProfile.objects.filter(employee_id=code)
        .select_related("user")
        .first()
    )
    if profile is None:
        return "Not a valid active staff ID."
    if profile.status == EmployeeStatus.PENDING_APPROVAL:
        return "That staff ID is still pending approval."
    if profile.status == EmployeeStatus.SUSPENDED:
        return "That staff ID is suspended."
    if not profile.user.is_active:
        return "That staff account is disabled."
    return "Not a valid active staff ID."


def _employee_id_cache_key(code: str) -> str:
    return f"{EMPLOYEE_ID_CACHE_PREFIX}{code}"


def employee_id_is_taken(code: str, *, exclude_employee_id: str | None = None) -> bool:
    """Check whether a 6-digit employee ID is already registered (cached)."""
    if exclude_employee_id and code == exclude_employee_id:
        return False

    key = _employee_id_cache_key(code)
    cached = cache.get(key)
    if cached is not None:
        if exclude_employee_id:
            taken = User.objects.filter(
                Q(username=code) | Q(employee_profile__employee_id=code)
            ).exclude(employee_profile__employee_id=exclude_employee_id).exists()
            return taken
        return cached

    qs = User.objects.filter(
        Q(username=code) | Q(employee_profile__employee_id=code)
    )
    if exclude_employee_id:
        qs = qs.exclude(employee_profile__employee_id=exclude_employee_id)
    taken = qs.exists()
    cache.set(key, taken, EMPLOYEE_ID_CACHE_TTL)
    return taken


def mark_employee_id_taken(code: str) -> None:
    """Record that an employee ID is now in use (after successful registration)."""
    cache.set(_employee_id_cache_key(code), True, EMPLOYEE_ID_CACHE_TTL)


def invalidate_employee_id_cache(code: str) -> None:
    cache.delete(_employee_id_cache_key(code))


def invalidate_profile_cache(user_id: int) -> None:
    cache.delete(f"profile_meta:{user_id}")


def employee_form_data_from_post(post) -> dict:
    """Extract employee registration fields from a POST body."""
    return {
        "first_name": post.get("first_name", "").strip().upper(),
        "last_name": post.get("last_name", "").strip().upper(),
        "email": post.get("email", "").strip().lower(),
        "phone_country_code": post.get("phone_country_code", "+254").strip(),
        "phone_country_iso": post.get("phone_country_iso", "KE").strip().upper(),
        "phone_number": post.get("phone_number", "").strip().upper(),
        "employee_id": post.get("employee_id", "").strip(),
    }


def validate_employee_registration(form_data, password, password_confirm, profile_photo):
    """Return a list of validation errors for employee registration."""
    errors = []
    valid_dials = {country["dial"] for country in COUNTRY_DIAL_CODES}
    valid_isos = {country["iso"] for country in COUNTRY_DIAL_CODES}

    if not form_data["first_name"]:
        errors.append("First name is required.")
    if not form_data["last_name"]:
        errors.append("Last name is required.")
    if not form_data["email"]:
        errors.append("Email is required.")
    elif "@" not in form_data["email"] or "." not in form_data["email"].split("@")[-1]:
        errors.append("Enter a valid email address.")
    if form_data["phone_country_code"] not in valid_dials:
        errors.append("Select a valid country code.")
    if form_data["phone_country_iso"] not in valid_isos:
        form_data["phone_country_iso"] = "KE"
    if not form_data["phone_number"]:
        errors.append("Phone number is required.")
    elif not PHONE_RE.match(form_data["phone_number"]):
        errors.append("Enter a valid phone number.")
    if not EMPLOYEE_ID_RE.match(form_data["employee_id"]):
        errors.append("Employee ID must be exactly 6 digits.")
    elif employee_id_is_taken(form_data["employee_id"]):
        errors.append("That employee ID is already registered.")
    if not password:
        errors.append("Password is required.")
    elif len(password) < 6:
        errors.append(
            "Password must be at least 6 characters. "
            "Letters, numbers, and symbols are allowed."
        )
    if password != password_confirm:
        errors.append("Password and confirm password do not match.")
    if profile_photo:
        if profile_photo.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Profile photo must be JPG, PNG, WEBP, or GIF.")
        elif profile_photo.size > MAX_PROFILE_BYTES:
            errors.append("Profile photo must be 5 MB or smaller.")
    return errors


def register_employee(form_data, password, profile_photo=None):
    """Create a pending employee account. Returns the new employee ID."""
    with transaction.atomic():
        user = User.objects.create_user(
            username=form_data["employee_id"],
            email=form_data["email"],
            password=password,
            first_name=form_data["first_name"],
            last_name=form_data["last_name"],
            is_active=True,
        )
        EmployeeProfile.objects.create(
            user=user,
            employee_id=form_data["employee_id"],
            phone_country_code=form_data["phone_country_code"],
            phone_number=form_data["phone_number"],
            profile_photo=profile_photo,
            status=EmployeeStatus.PENDING_APPROVAL,
            role=EmployeeRole.EMPLOYEE,
        )
    mark_employee_id_taken(form_data["employee_id"])
    return form_data["employee_id"]


def employee_edit_form_data_from_post(post) -> dict:
    """Extract employee edit fields from a POST body."""
    data = employee_form_data_from_post(post)
    data["role"] = post.get("role", "").strip()
    return data


def employee_edit_form_data_from_profile(profile: EmployeeProfile) -> dict:
    """Build edit form defaults from an employee profile."""
    dial_to_iso = {country["dial"]: country["iso"] for country in COUNTRY_DIAL_CODES}
    return {
        "first_name": profile.user.first_name,
        "last_name": profile.user.last_name,
        "email": profile.user.email,
        "phone_country_code": profile.phone_country_code,
        "phone_country_iso": dial_to_iso.get(profile.phone_country_code, "KE"),
        "phone_number": profile.phone_number,
        "employee_id": profile.employee_id,
        "role": profile.role,
    }


def validate_employee_update(
    form_data,
    password,
    password_confirm,
    profile_photo,
    *,
    current_employee_id,
):
    """Return validation errors for updating an existing employee."""
    errors = []
    valid_dials = {country["dial"] for country in COUNTRY_DIAL_CODES}
    valid_isos = {country["iso"] for country in COUNTRY_DIAL_CODES}
    valid_roles = {choice[0] for choice in EmployeeRole.choices}

    if not form_data["first_name"]:
        errors.append("First name is required.")
    if not form_data["last_name"]:
        errors.append("Last name is required.")
    if not form_data["email"]:
        errors.append("Email is required.")
    elif "@" not in form_data["email"] or "." not in form_data["email"].split("@")[-1]:
        errors.append("Enter a valid email address.")
    if form_data["phone_country_code"] not in valid_dials:
        errors.append("Select a valid country code.")
    if form_data["phone_country_iso"] not in valid_isos:
        form_data["phone_country_iso"] = "KE"
    if not form_data["phone_number"]:
        errors.append("Phone number is required.")
    elif not PHONE_RE.match(form_data["phone_number"]):
        errors.append("Enter a valid phone number.")
    if not EMPLOYEE_ID_RE.match(form_data["employee_id"]):
        errors.append("Employee ID must be exactly 6 digits.")
    elif employee_id_is_taken(
        form_data["employee_id"],
        exclude_employee_id=current_employee_id,
    ):
        errors.append("That employee ID is already registered.")
    if form_data["role"] not in valid_roles:
        errors.append("Select a valid role.")
    if password or password_confirm:
        if len(password) < 6:
            errors.append(
                "Password must be at least 6 characters. "
                "Letters, numbers, and symbols are allowed."
            )
        if password != password_confirm:
            errors.append("Password and confirm password do not match.")
    if profile_photo:
        if profile_photo.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Profile photo must be JPG, PNG, WEBP, or GIF.")
        elif profile_photo.size > MAX_PROFILE_BYTES:
            errors.append("Profile photo must be 5 MB or smaller.")
    return errors


def update_employee(
    profile: EmployeeProfile,
    form_data,
    *,
    password="",
    profile_photo=None,
    remove_photo=False,
):
    """Update an existing employee profile and linked user account."""
    user = profile.user
    previous_employee_id = profile.employee_id
    new_employee_id = form_data["employee_id"]

    with transaction.atomic():
        user.first_name = form_data["first_name"]
        user.last_name = form_data["last_name"]
        user.email = form_data["email"]
        if password:
            user.set_password(password)
        if new_employee_id != previous_employee_id:
            user.username = new_employee_id
            profile.employee_id = new_employee_id

        user_update_fields = ["first_name", "last_name", "email"]
        if password:
            user_update_fields.append("password")
        if new_employee_id != previous_employee_id:
            user_update_fields.append("username")
        user.save(update_fields=user_update_fields)

        profile.phone_country_code = form_data["phone_country_code"]
        profile.phone_number = form_data["phone_number"]
        profile.role = form_data["role"]

        if remove_photo and profile.profile_photo:
            profile.profile_photo.delete(save=False)
            profile.profile_photo = None
        elif profile_photo:
            if profile.profile_photo:
                profile.profile_photo.delete(save=False)
            profile.profile_photo = profile_photo

        profile.save(
            update_fields=[
                "employee_id",
                "phone_country_code",
                "phone_number",
                "role",
                "profile_photo",
                "updated_at",
            ]
        )
        if form_data["role"] not in SHOP_ALLOCATION_ROLES:
            profile.assigned_shops.clear()

    if new_employee_id != previous_employee_id:
        invalidate_employee_id_cache(previous_employee_id)
        mark_employee_id_taken(new_employee_id)
    invalidate_profile_cache(user.pk)
    return profile


def toggle_employee_suspended(profile: EmployeeProfile) -> EmployeeProfile:
    """Toggle an employee between active and suspended."""
    if profile.status == EmployeeStatus.ACTIVE:
        profile.status = EmployeeStatus.SUSPENDED
    elif profile.status == EmployeeStatus.SUSPENDED:
        profile.status = EmployeeStatus.ACTIVE
    else:
        raise ValueError("Only active or suspended employees can be toggled.")
    profile.save(update_fields=["status", "updated_at"])
    invalidate_profile_cache(profile.user_id)
    return profile


def delete_employee(profile: EmployeeProfile) -> None:
    """Permanently delete an employee account."""
    employee_id = profile.employee_id
    user_id = profile.user_id
    user = profile.user
    user.delete()
    invalidate_employee_id_cache(employee_id)
    invalidate_profile_cache(user_id)
