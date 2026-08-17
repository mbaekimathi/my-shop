from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0042_twilio_whatsapp_lids"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="auto_stock_supplier",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="auto_expense_supplier",
            field=models.BooleanField(default=False),
        ),
    ]
