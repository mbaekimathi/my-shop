import employees.fields
from django.db import migrations


# Final role set: store_manager is renamed company_store_manager and a new
# shop-scoped shop_store_manager is added.
ROLE_ENUM = (
    "employee",
    "super_admin",
    "company_manager",
    "shop_manager",
    "company_store_manager",
    "shop_store_manager",
    "shop_cashier",
    "it_support",
    "rider",
)

ROLE_CHOICES = [
    ("employee", "Employee"),
    ("super_admin", "Super Admin"),
    ("company_manager", "Company Manager"),
    ("shop_manager", "Shop Manager"),
    ("company_store_manager", "Company Store Manager"),
    ("shop_store_manager", "Shop Store Manager"),
    ("shop_cashier", "Shop Cashier"),
    ("it_support", "IT Support"),
    ("rider", "Rider"),
]

# Intermediate ENUM that still accepts the legacy value while rows migrate.
WIDENED_ENUM = (
    "employee",
    "super_admin",
    "company_manager",
    "shop_manager",
    "store_manager",
    "company_store_manager",
    "shop_store_manager",
    "shop_cashier",
    "it_support",
    "rider",
)

PREVIOUS_ENUM = (
    "employee",
    "super_admin",
    "company_manager",
    "shop_manager",
    "store_manager",
    "shop_cashier",
    "it_support",
    "rider",
)


def _enum_sql(values):
    return ", ".join(f"'{value}'" for value in values)


def _alter_role_enum(schema_editor, values):
    if schema_editor.connection.vendor != "mysql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            ALTER TABLE `employees_employeeprofile`
            MODIFY COLUMN `role` ENUM({_enum_sql(values)})
            NOT NULL DEFAULT 'employee'
            """
        )


def _rewrite_role_values(apps, mapping):
    """Rewrite role + extra_roles CSV values through *mapping* (exact members)."""
    EmployeeProfile = apps.get_model("employees", "EmployeeProfile")
    for old_value, new_value in mapping.items():
        EmployeeProfile.objects.filter(role=old_value).update(role=new_value)
    needles = [value for value in mapping]
    for profile in EmployeeProfile.objects.only("pk", "extra_roles"):
        parts = [p.strip() for p in (profile.extra_roles or "").split(",") if p.strip()]
        if not any(part in needles for part in parts):
            continue
        rewritten = [mapping.get(part, part) for part in parts]
        profile.extra_roles = ",".join(dict.fromkeys(rewritten))
        profile.save(update_fields=["extra_roles"])


def migrate_forward(apps, schema_editor):
    _alter_role_enum(schema_editor, WIDENED_ENUM)
    _rewrite_role_values(apps, {"store_manager": "company_store_manager"})
    _alter_role_enum(schema_editor, ROLE_ENUM)


def migrate_reverse(apps, schema_editor):
    _alter_role_enum(schema_editor, WIDENED_ENUM)
    _rewrite_role_values(
        apps,
        {
            "company_store_manager": "store_manager",
            "shop_store_manager": "shop_manager",
        },
    )
    _alter_role_enum(schema_editor, PREVIOUS_ENUM)


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0016_employeeprofile_sidebar_prefs"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(migrate_forward, migrate_reverse),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="employeeprofile",
                    name="role",
                    field=employees.fields.MysqlEnumField(
                        choices=ROLE_CHOICES,
                        db_index=True,
                        default="employee",
                        enum_values=list(ROLE_ENUM),
                        max_length=21,
                    ),
                ),
            ],
        ),
    ]
