from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0043_whatsapp_supplier_automations"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="auto_item_catalogue",
            field=models.BooleanField(default=False),
        ),
    ]
