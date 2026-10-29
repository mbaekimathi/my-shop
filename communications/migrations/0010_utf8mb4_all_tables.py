"""Convert every table to utf8mb4 so text fields can store emoji.

Databases created via cPanel/phpMyAdmin default to utf8 (utf8mb3), which
rejects 4-byte characters with MySQL error 1366. MySQL-only; no-op elsewhere.
"""

from django.db import migrations


def convert_to_utf8mb4(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        return
    db_name = connection.settings_dict["NAME"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' "
            "AND (table_collation IS NULL OR table_collation NOT LIKE 'utf8mb4%%')",
            [db_name],
        )
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(
                f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci"
            )
        # Future tables inherit the database default charset.
        try:
            cursor.execute(
                f"ALTER DATABASE `{db_name}` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci"
            )
        except Exception:
            pass  # cPanel users may lack ALTER on the database itself


class Migration(migrations.Migration):
    dependencies = [
        ("communications", "0009_outboundmessage_channel"),
    ]

    operations = [
        migrations.RunPython(convert_to_utf8mb4, migrations.RunPython.noop),
    ]
