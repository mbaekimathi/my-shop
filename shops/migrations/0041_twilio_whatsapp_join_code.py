from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0040_whatsapp_automation_audience"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="twilio_whatsapp_join_code",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
