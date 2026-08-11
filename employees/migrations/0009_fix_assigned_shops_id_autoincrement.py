from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0008_rename_communications_to_whatsapp_perms"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE employees_employeeprofile_assigned_shops "
                "MODIFY id bigint NOT NULL AUTO_INCREMENT;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
