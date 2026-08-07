from django.db import migrations, models


def create_default_pos_settings(apps, schema_editor):
    CompanyPosSettings = apps.get_model("shops", "CompanyPosSettings")
    CompanyPosSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0004_client"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyPosSettings",
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
                ("enable_sale", models.BooleanField(default=True)),
                ("enable_credit", models.BooleanField(default=True)),
                ("enable_quotation", models.BooleanField(default=True)),
                ("enable_cash_sale_checkout", models.BooleanField(default=True)),
                ("enable_cash", models.BooleanField(default=True)),
                ("enable_mpesa", models.BooleanField(default=True)),
                ("enable_cash_mpesa", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Company POS settings",
                "verbose_name_plural": "Company POS settings",
            },
        ),
        migrations.RunPython(create_default_pos_settings, migrations.RunPython.noop),
    ]
