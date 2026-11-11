from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0015_delivery_complete_trigger"),
    ]

    operations = [
        migrations.AlterField(
            model_name="triggeredmessage",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("sale", "Sale"),
                    ("credit", "Credit sale"),
                    ("credit_follow_up", "Credit follow-up"),
                    ("credit_follow_up_today", "Credit follow-up today"),
                    ("quotation", "Quotation"),
                    ("trade_out", "Trade out"),
                    ("trade_in", "Trade in"),
                    ("supply", "Supply received"),
                    ("supplier_payment", "Supplier paid"),
                    ("delivery_on_way", "Delivery on the way"),
                    ("delivery_complete", "Delivery completed"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
