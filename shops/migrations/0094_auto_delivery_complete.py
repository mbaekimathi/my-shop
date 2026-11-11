from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0093_shop_delivery_complete_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="auto_delivery_complete",
            field=models.BooleanField(default=False),
        ),
    ]
