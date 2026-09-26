from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0059_trade_out"),
    ]

    operations = [
        migrations.AddField(
            model_name="companydarajasettings",
            name="nexus_api_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="companydarajasettings",
            name="nexus_collection_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="C2B BillRefNumber / collection id from Nexus (informational).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="companydarajasettings",
            name="nexus_key_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="companydarajasettings",
            name="nexus_key_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companydarajasettings",
            name="stk_provider",
            field=models.CharField(
                choices=[
                    ("daraja", "Safaricom Daraja (your credentials)"),
                    ("nexus_rushtech", "Nexus collections — Rushtech"),
                ],
                default="daraja",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="mpesastkpayment",
            name="stk_provider",
            field=models.CharField(
                blank=True,
                choices=[
                    ("daraja", "Safaricom Daraja (your credentials)"),
                    ("nexus_rushtech", "Nexus collections — Rushtech"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
