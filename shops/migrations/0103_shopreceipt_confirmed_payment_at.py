from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0102_sync_require_mpesa_reference_on_confirm"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="confirmed_payment_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Payment date/time entered when confirming the sale in analytics."
                ),
                null=True,
            ),
        ),
    ]
