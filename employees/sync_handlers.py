"""Process offline sync operations from the client queue."""

import re

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from employees.services import mark_employee_id_taken

EMPLOYEE_ID_RE = re.compile(r"^\d{6}$")
PHONE_RE = re.compile(r"^[\d\s\-()]{7,20}$")


def process_sync_operations(employee: EmployeeProfile, operations: list) -> dict:
    results = []
    applied = 0
    failed = 0

    for op in operations:
        op_id = (op.get("id") or "").strip()
        op_type = (op.get("type") or "").strip()
        payload = op.get("payload") or {}

        try:
            result_payload = _dispatch(employee, op_type, payload)
            results.append(
                {
                    "id": op_id,
                    "type": op_type,
                    "ok": True,
                    "result": result_payload,
                }
            )
            applied += 1
        except SyncOperationError as exc:
            results.append(
                {
                    "id": op_id,
                    "type": op_type,
                    "ok": False,
                    "error": exc.code,
                    "message": exc.message,
                }
            )
            failed += 1

    return {
        "ok": failed == 0,
        "applied": applied,
        "failed": failed,
        "results": results,
        "server_time": timezone.now().isoformat(),
    }


class SyncOperationError(Exception):
    def __init__(self, message: str, code: str = "sync_error"):
        super().__init__(message)
        self.code = code
        self.message = message


def _dispatch(employee: EmployeeProfile, op_type: str, payload: dict) -> dict:
    if op_type == "create_sale":
        return _sync_create_sale(employee, payload)
    if op_type == "register_employee":
        return _sync_register_employee(payload)
    if op_type == "update_employee_access":
        return _sync_update_employee_access(employee, payload)
    if op_type == "cache_employee_id":
        return _sync_cache_employee_id(payload)
    raise SyncOperationError(f"Unknown operation type: {op_type}", "unknown_type")


def _sync_create_sale(employee: EmployeeProfile, payload: dict) -> dict:
    from pos.models import SaleSource
    from pos.services import SaleValidationError, create_sale_from_payload

    if employee.role not in (
        EmployeeRole.SHOP_CASHIER,
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.SUPER_ADMIN,
    ):
        raise SyncOperationError("Your role cannot create sales.", "forbidden")

    try:
        sale = create_sale_from_payload(employee, payload, source=SaleSource.OFFLINE)
    except SaleValidationError as exc:
        raise SyncOperationError(exc.message, exc.code)

    return {"client_id": sale.client_id, "total": str(sale.total), "source": sale.source}


def _sync_register_employee(payload: dict) -> dict:
    employee_id = (payload.get("employee_id") or "").strip()
    first_name = (payload.get("first_name") or "").strip().upper()
    last_name = (payload.get("last_name") or "").strip().upper()
    email = (payload.get("email") or "").strip().lower()
    phone_country_code = (payload.get("phone_country_code") or "+254").strip()
    phone_number = (payload.get("phone_number") or "").strip()
    password = payload.get("password") or ""

    if not EMPLOYEE_ID_RE.match(employee_id):
        raise SyncOperationError("Employee ID must be exactly 6 digits.", "invalid_employee_id")
    if not first_name or not last_name or not email:
        raise SyncOperationError("Name and email are required.", "invalid_profile")
    if not PHONE_RE.match(phone_number):
        raise SyncOperationError("Invalid phone number.", "invalid_phone")
    if len(password) < 6:
        raise SyncOperationError("Password must be at least 6 characters.", "invalid_password")

    if User.objects.filter(username=employee_id).exists():
        mark_employee_id_taken(employee_id)
        raise SyncOperationError("Employee ID already registered.", "employee_id_taken")

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=employee_id,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
            )
            EmployeeProfile.objects.create(
                user=user,
                employee_id=employee_id,
                phone_country_code=phone_country_code,
                phone_number=phone_number,
                status=EmployeeStatus.PENDING_APPROVAL,
                role=EmployeeRole.EMPLOYEE,
            )
        mark_employee_id_taken(employee_id)
    except IntegrityError:
        mark_employee_id_taken(employee_id)
        raise SyncOperationError("Registration could not be completed.", "integrity_error")

    return {"employee_id": employee_id, "status": EmployeeStatus.PENDING_APPROVAL}


def _sync_update_employee_access(employee: EmployeeProfile, payload: dict) -> dict:
    if employee.role != EmployeeRole.SUPER_ADMIN:
        raise SyncOperationError("Only Super Admin can update employee access.", "forbidden")

    target_id = (payload.get("employee_id") or "").strip()
    new_role = (payload.get("role") or "").strip()
    new_status = (payload.get("status") or "").strip()

    target = EmployeeProfile.objects.filter(employee_id=target_id).first()
    if target is None:
        raise SyncOperationError(f"Employee {target_id} not found.", "not_found")

    valid_roles = {c[0] for c in EmployeeRole.choices}
    valid_statuses = {c[0] for c in EmployeeStatus.choices}
    if new_role not in valid_roles or new_status not in valid_statuses:
        raise SyncOperationError("Invalid role or status.", "invalid_access")

    if target.user_id == employee.user_id:
        if new_role != EmployeeRole.SUPER_ADMIN or new_status != EmployeeStatus.ACTIVE:
            raise SyncOperationError("Cannot change your own Super Admin access.", "forbidden")

    target.role = new_role
    target.status = new_status
    target.save(update_fields=["role", "status", "updated_at"])
    if new_role not in {
        EmployeeRole.EMPLOYEE,
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.SHOP_CASHIER,
    }:
        target.assigned_shops.clear()

    return {
        "employee_id": target.employee_id,
        "role": target.role,
        "status": target.status,
        "assigned_shop_ids": list(
            target.assigned_shops.values_list("pk", flat=True)
        ),
    }


def _sync_cache_employee_id(payload: dict) -> dict:
    """Acknowledge cached employee-id checks from offline registration UX."""
    code = (payload.get("code") or "").strip()
    available = payload.get("available")
    return {"code": code, "cached": True, "available": available}
