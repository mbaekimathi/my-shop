from django.db import migrations, models


def create_default_stock_settings(apps, schema_editor):
    CompanyStockSettings = apps.get_model("shops", "CompanyStockSettings")
    CompanyStockSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0027_shopreceiptline_cogs_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyStockSettings",
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
                ("require_buying_price_on_in", models.BooleanField(default=True)),
                ("require_supplier_on_in", models.BooleanField(default=True)),
                ("require_payment_status_on_in", models.BooleanField(default=True)),
                ("require_reason_on_out", models.BooleanField(default=True)),
                ("require_refund_on_out", models.BooleanField(default=True)),
                ("require_note_on_request", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Company stock settings",
                "verbose_name_plural": "Company stock settings",
            },
        ),
        migrations.RunPython(create_default_stock_settings, migrations.RunPython.noop),
    ]
