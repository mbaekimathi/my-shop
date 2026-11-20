from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0019_employeeattendance"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeattendance",
            name="auto_clocked_out",
            field=models.BooleanField(
                default=False,
                help_text="True when the system closed the entry (idle), not the employee.",
            ),
        ),
        migrations.AddField(
            model_name="employeeattendance",
            name="last_activity_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeeattendance",
            name="check_prompted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeeattendance",
            name="confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
