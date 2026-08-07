from django.conf import settings
from django.db import models

from .fields import MysqlEnumField


def employee_profile_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"employees/profiles/{instance.employee_id}.{ext}"


class EmployeeStatus(models.TextChoices):
    PENDING_APPROVAL = "pending_approval", "Pending Approval"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"


class EmployeeRole(models.TextChoices):
    EMPLOYEE = "employee", "Employee"
    SUPER_ADMIN = "super_admin", "Super Admin"
    COMPANY_MANAGER = "company_manager", "Company Manager"
    SHOP_MANAGER = "shop_manager", "Shop Manager"
    SHOP_CASHIER = "shop_cashier", "Shop Cashier"
    IT_SUPPORT = "it_support", "IT Support"


# Roles that work inside a delegated shop.
SHOP_ASSIGNABLE_ROLES = frozenset(
    {
        EmployeeRole.EMPLOYEE,
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.SHOP_CASHIER,
    }
)


class EmployeeProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
    )
    employee_id = models.CharField(max_length=6, unique=True, db_index=True)
    phone_country_code = models.CharField(max_length=8, default="+254")
    phone_number = models.CharField(max_length=20)
    profile_photo = models.ImageField(
        upload_to=employee_profile_path,
        blank=True,
        null=True,
    )
    status = MysqlEnumField(
        enum_values=EmployeeStatus.values,
        choices=EmployeeStatus.choices,
        default=EmployeeStatus.PENDING_APPROVAL,
        db_index=True,
    )
    role = MysqlEnumField(
        enum_values=EmployeeRole.values,
        choices=EmployeeRole.choices,
        default=EmployeeRole.EMPLOYEE,
        db_index=True,
    )
    assigned_shops = models.ManyToManyField(
        "shops.Shop",
        blank=True,
        related_name="assigned_employees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee_id"]

    def __str__(self):
        return f"{self.employee_id} — {self.user.get_full_name() or self.user.username}"

    @property
    def full_phone(self):
        return f"{self.phone_country_code} {self.phone_number}".strip()

    @property
    def is_active_employee(self):
        return self.status == EmployeeStatus.ACTIVE

    @property
    def role_label(self):
        return self.get_role_display()

    @property
    def status_label(self):
        return self.get_status_display()


class EmployeeModulePermission(models.Model):
    """Per-employee allow/deny for a module submodule capability."""

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name="module_permissions",
    )
    module_slug = models.CharField(max_length=64, db_index=True)
    submodule_slug = models.CharField(max_length=64, db_index=True)
    allowed = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "module_slug", "submodule_slug"],
                name="uniq_employee_module_submodule_perm",
            )
        ]
        indexes = [
            models.Index(
                fields=["module_slug", "submodule_slug"],
                name="emp_mod_submod_idx",
            ),
        ]
        ordering = ["module_slug", "submodule_slug", "employee_id"]

    def __str__(self):
        state = "allow" if self.allowed else "deny"
        return (
            f"{self.employee_id}: {self.module_slug}/{self.submodule_slug} → {state}"
        )
