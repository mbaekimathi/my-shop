"""Seed an active Super Admin employee and a shop branch.

Creates (or resets, if they already exist):
  - Employee  ID 000000 / password 000000 — ACTIVE, Super Admin
  - Shop      login code 123456 / password 000000 — name "SHOP 1"

Run from the project root:

    python scripts/register_employee_and_shop.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
django.setup()

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from employees.services import (
    invalidate_profile_cache,
    mark_employee_id_taken,
    register_employee,
)
from shops.models import Shop
from shops.services import create_shop, update_shop

# ---------------------------------------------------------------------------
# Credentials + profile details. Edit the placeholder contact fields as needed.
# ---------------------------------------------------------------------------

EMPLOYEE_ID = "000000"
EMPLOYEE_PASSWORD = "000000"
EMPLOYEE_FIRST_NAME = "SYSTEM"
EMPLOYEE_LAST_NAME = "ADMIN"
EMPLOYEE_EMAIL = "admin@example.com"
EMPLOYEE_PHONE = "0700000000"
EMPLOYEE_PHONE_COUNTRY_CODE = "+254"

SHOP_NAME = "SHOP 1"
SHOP_LOGIN_CODE = "123456"
SHOP_PASSWORD = "000000"
SHOP_LOCATION = "NAIROBI"
SHOP_EMAIL = "shop1@example.com"
SHOP_PHONE = "0700000000"


def upsert_employee() -> EmployeeProfile:
    profile = EmployeeProfile.objects.filter(employee_id=EMPLOYEE_ID).first()
    if profile is None and not User.objects.filter(username=EMPLOYEE_ID).exists():
        employee_id = register_employee(
            {
                "first_name": EMPLOYEE_FIRST_NAME,
                "last_name": EMPLOYEE_LAST_NAME,
                "email": EMPLOYEE_EMAIL,
                "phone_country_code": EMPLOYEE_PHONE_COUNTRY_CODE,
                "phone_number": EMPLOYEE_PHONE,
                "employee_id": EMPLOYEE_ID,
            },
            EMPLOYEE_PASSWORD,
        )
        profile = EmployeeProfile.objects.get(employee_id=employee_id)
        created = True
    else:
        if profile is None:
            profile = EmployeeProfile.objects.get(user__username=EMPLOYEE_ID)
        created = False

    user = profile.user
    with transaction.atomic():
        if not created:
            user.set_password(EMPLOYEE_PASSWORD)
            user.first_name = EMPLOYEE_FIRST_NAME
            user.last_name = EMPLOYEE_LAST_NAME
            user.email = EMPLOYEE_EMAIL
            user.is_active = True
            user.save(
                update_fields=[
                    "password",
                    "first_name",
                    "last_name",
                    "email",
                    "is_active",
                ]
            )
        profile.status = EmployeeStatus.ACTIVE
        profile.role = EmployeeRole.SUPER_ADMIN
        profile.phone_country_code = EMPLOYEE_PHONE_COUNTRY_CODE
        profile.phone_number = EMPLOYEE_PHONE
        profile.save(
            update_fields=[
                "status",
                "role",
                "phone_country_code",
                "phone_number",
                "updated_at",
            ]
        )

    mark_employee_id_taken(EMPLOYEE_ID)
    invalidate_profile_cache(user.pk)
    action = "Registered" if created else "Updated"
    print(f"{action} employee {profile.employee_id} (Super Admin, active).")
    return profile


def upsert_shop(profile: EmployeeProfile) -> Shop:
    payload = {
        "name": SHOP_NAME,
        "location": SHOP_LOCATION,
        "email": SHOP_EMAIL,
        "phone_number": SHOP_PHONE,
        "login_code": SHOP_LOGIN_CODE,
        "password": SHOP_PASSWORD,
        "password_confirm": SHOP_PASSWORD,
    }
    shop = Shop.objects.filter(login_code=SHOP_LOGIN_CODE).first()
    if shop is None:
        shop = create_shop(profile, payload, {})
        print(f"Registered shop {shop.name} (login code {shop.login_code}).")
    else:
        update_shop(shop, payload, {})
        print(f"Updated shop {shop.name} (login code {shop.login_code}).")
    return shop


def main() -> None:
    try:
        profile = upsert_employee()
        upsert_shop(profile)
    except ValidationError as exc:
        messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
        for message in messages:
            print(f"Error: {message}")
        sys.exit(1)

    print("Done. Sign in with employee ID 000000 or shop code 123456 (password 000000).")


if __name__ == "__main__":
    main()
