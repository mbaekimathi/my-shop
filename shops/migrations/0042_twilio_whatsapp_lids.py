from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0041_twilio_whatsapp_join_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="twilio_whatsapp_lids",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
