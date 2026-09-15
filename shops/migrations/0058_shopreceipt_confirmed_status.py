from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0057_shop_receipt_phone_number"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shopreceipt",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Pending"),
                    ("confirmed", "Confirmed"),
                    ("partial_return", "Partially returned"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="active",
                max_length=20,
            ),
        ),
    ]
