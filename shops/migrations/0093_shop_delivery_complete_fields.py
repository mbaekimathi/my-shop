from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0092_client_email_auto_delivery_on_way"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopdelivery",
            name="receiver_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Who received the goods at drop-off (sales).",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="shopdelivery",
            name="payment_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text="M-Pesa or other payment reference collected on credit deliveries.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="shopdelivery",
            name="payment_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the customer paid the rider (credit deliveries).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="shopdelivery",
            name="payment_amount",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True
            ),
        ),
        migrations.AlterField(
            model_name="shopdelivery",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Open"),
                    ("accepted", "Accepted"),
                    ("delivered", "Delivered"),
                    ("delivered_paid", "Delivered (paid)"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="open",
                max_length=16,
            ),
        ),
    ]
