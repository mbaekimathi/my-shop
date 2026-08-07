from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User

from .models import (
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)


class EmployeeProfileInline(admin.StackedInline):
    model = EmployeeProfile
    can_delete = False
    extra = 0
    fields = (
        "employee_id",
        "role",
        "status",
        "assigned_shops",
        "phone_country_code",
        "phone_number",
        "profile_photo",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("assigned_shops",)


class UserAdmin(DjangoUserAdmin):
    inlines = [EmployeeProfileInline]


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "user",
        "full_name",
        "role",
        "status",
        "assigned_shops_display",
        "full_phone",
        "created_at",
    )
    list_editable = ("role", "status")
    list_display_links = ("employee_id", "user")
    search_fields = (
        "employee_id",
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
        "phone_number",
        "assigned_shops__name",
    )
    list_filter = ("status", "role", "assigned_shops", "phone_country_code")
    ordering = ("status", "employee_id")
    list_per_page = 50
    autocomplete_fields = ("user",)
    filter_horizontal = ("assigned_shops",)
    actions = [
        "approve_active",
        "mark_suspended",
        "mark_pending",
        "set_role_employee",
        "set_role_shop_cashier",
        "set_role_shop_manager",
        "set_role_company_manager",
        "set_role_it_support",
        "set_role_super_admin",
    ]
    fieldsets = (
        (
            "Identity",
            {
                "fields": ("user", "employee_id", "profile_photo"),
            },
        ),
        (
            "Access (dropdowns)",
            {
                "description": (
                    "Choose role and status from the lists below. "
                    "Delegate one or more shops for Employee, Shop Manager, and Shop Cashier."
                ),
                "fields": ("role", "status", "assigned_shops"),
            },
        ),
        (
            "Contact",
            {
                "fields": ("phone_country_code", "phone_number"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Name", ordering="user__first_name")
    def full_name(self, obj):
        return obj.user.get_full_name() or "—"

    @admin.display(description="Phone")
    def full_phone(self, obj):
        return obj.full_phone

    @admin.display(description="Shops")
    def assigned_shops_display(self, obj):
        names = list(obj.assigned_shops.values_list("name", flat=True)[:4])
        if not names:
            return "—"
        label = ", ".join(names)
        extra = obj.assigned_shops.count() - len(names)
        if extra > 0:
            return f"{label} (+{extra})"
        return label

    @admin.action(description="Set status → Active")
    def approve_active(self, request, queryset):
        updated = queryset.update(status=EmployeeStatus.ACTIVE)
        self.message_user(request, f"{updated} employee(s) set to Active.", messages.SUCCESS)

    @admin.action(description="Set status → Suspended")
    def mark_suspended(self, request, queryset):
        updated = queryset.update(status=EmployeeStatus.SUSPENDED)
        self.message_user(request, f"{updated} employee(s) suspended.", messages.WARNING)

    @admin.action(description="Set status → Pending Approval")
    def mark_pending(self, request, queryset):
        updated = queryset.update(status=EmployeeStatus.PENDING_APPROVAL)
        self.message_user(request, f"{updated} employee(s) set to Pending Approval.")

    @admin.action(description="Set role → Employee")
    def set_role_employee(self, request, queryset):
        updated = queryset.update(role=EmployeeRole.EMPLOYEE)
        self.message_user(request, f"{updated} employee(s) set to Employee.")

    @admin.action(description="Set role → Shop Cashier")
    def set_role_shop_cashier(self, request, queryset):
        updated = queryset.update(role=EmployeeRole.SHOP_CASHIER)
        self.message_user(request, f"{updated} employee(s) set to Shop Cashier.")

    @admin.action(description="Set role → Shop Manager")
    def set_role_shop_manager(self, request, queryset):
        updated = queryset.update(role=EmployeeRole.SHOP_MANAGER)
        self.message_user(request, f"{updated} employee(s) set to Shop Manager.")

    @admin.action(description="Set role → Company Manager")
    def set_role_company_manager(self, request, queryset):
        updated = queryset.update(role=EmployeeRole.COMPANY_MANAGER)
        self.message_user(request, f"{updated} employee(s) set to Company Manager.")

    @admin.action(description="Set role → IT Support")
    def set_role_it_support(self, request, queryset):
        updated = queryset.update(role=EmployeeRole.IT_SUPPORT)
        self.message_user(request, f"{updated} employee(s) set to IT Support.")

    @admin.action(description="Set role → Super Admin")
    def set_role_super_admin(self, request, queryset):
        updated = queryset.update(role=EmployeeRole.SUPER_ADMIN)
        self.message_user(request, f"{updated} employee(s) set to Super Admin.")


@admin.register(EmployeeModulePermission)
class EmployeeModulePermissionAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "module_slug",
        "submodule_slug",
        "allowed",
        "updated_at",
    )
    list_filter = ("allowed", "module_slug", "submodule_slug")
    search_fields = (
        "employee__employee_id",
        "employee__user__username",
        "employee__user__first_name",
        "employee__user__last_name",
        "module_slug",
        "submodule_slug",
    )
    list_editable = ("allowed",)
    autocomplete_fields = ("employee",)
    ordering = ("module_slug", "submodule_slug", "employee__employee_id")

