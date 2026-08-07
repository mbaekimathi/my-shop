from django.db import migrations

import employees.fields


STATUS_ENUM = ("pending_approval", "active", "suspended")
ROLE_ENUM = (
    "employee",
    "super_admin",
    "company_manager",
    "shop_manager",
    "shop_cashier",
    "it_support",
)


def _enum_sql(values):
    return ", ".join(f"'{value}'" for value in values)


def convert_role_status_to_mysql_enum(apps, schema_editor):
    """Make role/status real MySQL ENUMs so DB UIs show dropdowns."""
    if schema_editor.connection.vendor != "mysql":
        return

    table = "employees_employeeprofile"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            MODIFY COLUMN `status` ENUM({_enum_sql(STATUS_ENUM)})
            NOT NULL DEFAULT 'pending_approval'
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            MODIFY COLUMN `role` ENUM({_enum_sql(ROLE_ENUM)})
            NOT NULL DEFAULT 'employee'
            """
        )


def convert_role_status_to_varchar(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return

    table = "employees_employeeprofile"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            MODIFY COLUMN `status` VARCHAR(32) NOT NULL DEFAULT 'pending_approval'
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            MODIFY COLUMN `role` VARCHAR(32) NOT NULL DEFAULT 'employee'
            """
        )


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0002_employeeprofile_role_employeeprofile_status"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="employeeprofile",
                    name="status",
                    field=employees.fields.MysqlEnumField(
                        choices=[
                            ("pending_approval", "Pending Approval"),
                            ("active", "Active"),
                            ("suspended", "Suspended"),
                        ],
                        default="pending_approval",
                        db_index=True,
                        enum_values=list(STATUS_ENUM),
                        max_length=32,
                    ),
                ),
                migrations.AlterField(
                    model_name="employeeprofile",
                    name="role",
                    field=employees.fields.MysqlEnumField(
                        choices=[
                            ("employee", "Employee"),
                            ("super_admin", "Super Admin"),
                            ("company_manager", "Company Manager"),
                            ("shop_manager", "Shop Manager"),
                            ("shop_cashier", "Shop Cashier"),
                            ("it_support", "IT Support"),
                        ],
                        default="employee",
                        db_index=True,
                        enum_values=list(ROLE_ENUM),
                        max_length=32,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    convert_role_status_to_mysql_enum,
                    convert_role_status_to_varchar,
                ),
            ],
        ),
    ]
