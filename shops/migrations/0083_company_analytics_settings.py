from django.db import migrations, models


def create_default_analytics_settings(apps, schema_editor):
    CompanyAnalyticsSettings = apps.get_model("shops", "CompanyAnalyticsSettings")
    CompanyAnalyticsSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0082_hide_stock_numbers"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyAnalyticsSettings",
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
                (
                    "receipt_data_source",
                    models.CharField(
                        choices=[
                            ("pending", "Pending receipts"),
                            ("confirmed", "Confirmed receipts"),
                        ],
                        default="pending",
                        help_text=(
                            "Default receipt set for analytics pages. "
                            "Users can still switch per view on the analytics page."
                        ),
                        max_length=16,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Company analytics settings",
                "verbose_name_plural": "Company analytics settings",
            },
        ),
        migrations.RunPython(
            create_default_analytics_settings, migrations.RunPython.noop
        ),
    ]
