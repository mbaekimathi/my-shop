import employees.fields
from django.db import migrations


ROLE_ENUM = (
    "employee",
    "super_admin",
    "company_manager",
    "shop_manager",
    "store_manager",
    "shop_cashier",
    "it_support",
)

ROLE_CHOICES = [
    ("employee", "Employee"),
    ("super_admin", "Super Admin"),
    ("company_manager", "Company Manager"),
    ("shop_manager", "Shop Manager"),
    ("store_manager", "Store Manager"),
    ("shop_cashier", "Shop Cashier"),
    ("it_support", "IT Support"),
]


def _enum_sql(values):
    return ", ".join(f"'{value}'" for value in values)


def expand_role_enum(apps, schema_editor):
    """Add store_manager to the MySQL ENUM so DB UIs show the new role."""
    if schema_editor.connection.vendor != "mysql":
        return

    table = "employees_employeeprofile"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            MODIFY COLUMN `role` ENUM({_enum_sql(ROLE_ENUM)})
            NOT NULL DEFAULT 'employee'
            """
        )


def shrink_role_enum(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return

    previous = (
        "employee",
        "super_admin",
        "company_manager",
        "shop_manager",
        "shop_cashier",
        "it_support",
    )
    table = "employees_employeeprofile"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE `{table}`
            SET `role` = 'shop_manager'
            WHERE `role` = 'store_manager'
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            MODIFY COLUMN `role` ENUM({_enum_sql(previous)})
            NOT NULL DEFAULT 'employee'
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0009_fix_assigned_shops_id_autoincrement"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(expand_role_enum, shrink_role_enum),
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
                        max_length=15,
                    ),
                ),
            ],
        ),
    ]
