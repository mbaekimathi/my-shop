from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0039_twilio_credentials"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="auto_shop_website",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="automation_audience_type",
            field=models.CharField(blank=True, default="sale", max_length=20),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="automation_last_purchase_days",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="automation_shop_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
