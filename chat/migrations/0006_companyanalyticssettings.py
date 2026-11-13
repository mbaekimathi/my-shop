from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0005_companypossettings_expensesupplier_shoppossettings"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyAnalyticsSettings",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "receipt_data_source",
                    models.CharField(default="pending", max_length=16),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "shops_companyanalyticssettings",
                "managed": False,
            },
        ),
    ]
