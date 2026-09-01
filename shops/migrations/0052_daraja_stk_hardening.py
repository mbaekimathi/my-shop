import secrets

from django.db import migrations, models


def populate_callback_secrets(apps, schema_editor):
    CompanyDarajaSettings = apps.get_model("shops", "CompanyDarajaSettings")
    for row in CompanyDarajaSettings.objects.all():
        if not (row.callback_secret or "").strip():
            row.callback_secret = secrets.token_urlsafe(32)
            row.save(update_fields=["callback_secret"])


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0051_shopreceipt_return_payment_events"),
    ]

    operations = [
        migrations.AddField(
            model_name="companydarajasettings",
            name="callback_secret",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Secret token embedded in the Safaricom STK callback URL.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="mpesastkpayment",
            name="stk_business_shortcode",
            field=models.CharField(
                blank=True,
                default="",
                help_text="BusinessShortCode used when this STK was initiated (for status queries).",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="mpesastkpayment",
            name="last_status_query_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(populate_callback_secrets, migrations.RunPython.noop),
    ]
