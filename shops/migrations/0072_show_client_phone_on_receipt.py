from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0071_trade_in"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="show_client_phone_on_receipt",
            field=models.BooleanField(
                default=True,
                help_text="When on, the customer's phone number is printed on receipts. When off, it is left off the receipt.",
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="show_client_phone_on_receipt",
            field=models.BooleanField(
                default=True,
                help_text="When on, the customer's phone number is printed on receipts. When off, it is left off the receipt.",
            ),
        ),
    ]
