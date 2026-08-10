from django.db import migrations, models
import django.db.models.deletion


def seed_shop_working_hours(apps, schema_editor):
    Shop = apps.get_model("shops", "Shop")
    ShopWorkingHoursSettings = apps.get_model("shops", "ShopWorkingHoursSettings")
    CompanyWorkingHoursSettings = apps.get_model("shops", "CompanyWorkingHoursSettings")
    company, _ = CompanyWorkingHoursSettings.objects.get_or_create(pk=1)
    for shop in Shop.objects.filter(is_hidden=False, is_suspended=False):
        ShopWorkingHoursSettings.objects.get_or_create(
            shop=shop,
            defaults={
                "start_time": company.start_time,
                "end_time": company.end_time,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0033_companyworkinghourssettings_enabled"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShopWorkingHoursSettings",
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
                ("start_time", models.TimeField(default="08:00")),
                ("end_time", models.TimeField(default="17:00")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "shop",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="working_hours_settings",
                        to="shops.shop",
                    ),
                ),
            ],
            options={
                "verbose_name": "Shop working hours",
                "verbose_name_plural": "Shop working hours",
            },
        ),
        migrations.RunPython(seed_shop_working_hours, migrations.RunPython.noop),
    ]
