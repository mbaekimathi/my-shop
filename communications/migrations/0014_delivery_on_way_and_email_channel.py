from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0013_alter_triggeredmessage_trigger"),
    ]

    operations = [
        migrations.AlterField(
            model_name="triggeredmessage",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("sale", "Sale"),
                    ("credit", "Credit sale"),
                    ("quotation", "Quotation"),
                    ("trade_out", "Trade out"),
                    ("trade_in", "Trade in"),
                    ("supply", "Supply received"),
                    ("supplier_payment", "Supplier paid"),
                    ("delivery_on_way", "Delivery on the way"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="triggeredmessage",
            name="channel",
            field=models.CharField(
                choices=[
                    ("whatsapp", "WhatsApp"),
                    ("sms", "SMS"),
                    ("message", "Email / Message"),
                    ("both", "WhatsApp + SMS"),
                    ("all", "WhatsApp + SMS + Email"),
                ],
                default="whatsapp",
                max_length=12,
            ),
        ),
    ]
