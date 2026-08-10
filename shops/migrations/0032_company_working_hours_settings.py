from django.db import migrations, models


def create_default_working_hours(apps, schema_editor):
    CompanyWorkingHoursSettings = apps.get_model("shops", "CompanyWorkingHoursSettings")
    CompanyWorkingHoursSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0031_alter_companystocksettings_enable_supplier_last4_search"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyWorkingHoursSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("work_monday", models.BooleanField(default=True)),
                ("work_tuesday", models.BooleanField(default=True)),
                ("work_wednesday", models.BooleanField(default=True)),
                ("work_thursday", models.BooleanField(default=True)),
                ("work_friday", models.BooleanField(default=True)),
                ("work_saturday", models.BooleanField(default=False)),
                ("work_sunday", models.BooleanField(default=False)),
                ("start_time", models.TimeField(default="08:00")),
                ("end_time", models.TimeField(default="17:00")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Company working hours",
                "verbose_name_plural": "Company working hours",
            },
        ),
        migrations.RunPython(create_default_working_hours, migrations.RunPython.noop),
    ]
