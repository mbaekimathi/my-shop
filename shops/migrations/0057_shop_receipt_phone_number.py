from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0056_mpesa_send_money_pochi"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="receipt_phone_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Phone printed on this shop's receipts. Blank uses the company phone.",
                max_length=40,
            ),
        ),
    ]
