from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0015_employeeprofile_extra_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeprofile",
            name="sidebar_prefs",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Per-employee dashboard sidebar link visibility "
                    "(link key → true/false; missing keys default to shown)."
                ),
            ),
        ),
    ]
