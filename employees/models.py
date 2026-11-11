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
    STORE_MANAGER = "store_manager", "Store Manager"
    SHOP_CASHIER = "shop_cashier", "Shop Cashier"
    IT_SUPPORT = "it_support", "IT Support"
    RIDER = "rider", "Rider"


# Roles that work inside a delegated shop.
SHOP_ASSIGNABLE_ROLES = frozenset(
    {
        EmployeeRole.EMPLOYEE,
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.SHOP_CASHIER,
    }
)

# Roles that may be limited to assigned shops for operational data (e.g. Credits).
SHOP_ALLOCATION_ROLES = frozenset(
    {
        *SHOP_ASSIGNABLE_ROLES,
        EmployeeRole.IT_SUPPORT,
    }
)

# Roles with full stock workflow (in / out / transfer / serials / report).
FULL_STOCK_WORKFLOW_ROLES = frozenset(
    {
        EmployeeRole.SUPER_ADMIN,
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.STORE_MANAGER,
        EmployeeRole.IT_SUPPORT,
    }
)

# Roles that see every active shop (not limited by assigned_shops).
COMPANY_WIDE_SHOP_ROLES = frozenset(
    {
        EmployeeRole.SUPER_ADMIN,
        EmployeeRole.COMPANY_MANAGER,
        EmployeeRole.STORE_MANAGER,
        EmployeeRole.IT_SUPPORT,
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
    active_session_key = models.CharField(
        max_length=40,
        blank=True,
        default="",
        editable=False,
        help_text="Most recent employee session key (diagnostic only; sessions are concurrent).",
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


class WebPushSubscription(models.Model):
    """Browser / installed-app push endpoint for device notifications."""

    endpoint = models.TextField()
    endpoint_hash = models.CharField(max_length=64, unique=True, db_index=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
        null=True,
        blank=True,
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
        null=True,
        blank=True,
    )
    user_agent = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["employee"], name="webpush_employee_idx"),
            models.Index(fields=["shop"], name="webpush_shop_idx"),
        ]

    def __str__(self):
        who = (
            f"employee:{self.employee_id}"
            if self.employee_id
            else f"shop:{self.shop_id}"
            if self.shop_id
            else "anon"
        )
        return f"WebPushSubscription({who})"

    @staticmethod
    def hash_endpoint(endpoint: str) -> str:
        import hashlib

        return hashlib.sha256((endpoint or "").encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        self.endpoint_hash = self.hash_endpoint(self.endpoint)
        return super().save(*args, **kwargs)


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
