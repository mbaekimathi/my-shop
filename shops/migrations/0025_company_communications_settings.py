from django.db import migrations, models


def create_singleton(apps, schema_editor):
    CompanyCommunicationsSettings = apps.get_model(
        "shops", "CompanyCommunicationsSettings"
    )
    CompanyCommunicationsSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0024_simple_receipt_numbers"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyCommunicationsSettings",
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
                ("enable_whatsapp", models.BooleanField(default=False)),
                ("enable_message", models.BooleanField(default=False)),
                ("enable_sms", models.BooleanField(default=False)),
                ("enable_automations", models.BooleanField(default=False)),
                ("enable_bulk_send", models.BooleanField(default=False)),
                ("auto_sale_receipt", models.BooleanField(default=False)),
                ("auto_quotation", models.BooleanField(default=False)),
                ("auto_payment_reminder", models.BooleanField(default=False)),
                ("auto_credit_due", models.BooleanField(default=False)),
                (
                    "whatsapp_phone_number_id",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "whatsapp_business_account_id",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "whatsapp_access_token",
                    models.CharField(blank=True, default="", max_length=512),
                ),
                (
                    "whatsapp_from_number",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "sms_provider",
                    models.CharField(
                        choices=[
                            ("africas_talking", "Africa's Talking"),
                            ("twilio", "Twilio"),
                            ("custom", "Custom HTTP"),
                        ],
                        default="africas_talking",
                        max_length=32,
                    ),
                ),
                ("sms_api_key", models.CharField(blank=True, default="", max_length=255)),
                (
                    "sms_api_secret",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "sms_sender_id",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "sms_api_base_url",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "message_from_name",
                    models.CharField(blank=True, default="", max_length=120),
                ),
                (
                    "message_reply_to",
                    models.EmailField(blank=True, default="", max_length=254),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Company communications settings",
                "verbose_name_plural": "Company communications settings",
            },
        ),
        migrations.RunPython(create_singleton, migrations.RunPython.noop),
    ]
