from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0038_clientcreditaccountevent"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="companycommunicationssettings",
            name="whatsapp_access_token",
        ),
        migrations.RemoveField(
            model_name="companycommunicationssettings",
            name="whatsapp_business_account_id",
        ),
        migrations.RemoveField(
            model_name="companycommunicationssettings",
            name="whatsapp_from_number",
        ),
        migrations.RemoveField(
            model_name="companycommunicationssettings",
            name="whatsapp_phone_number_id",
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="twilio_account_sid",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="twilio_auth_token",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="twilio_from_number",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="companycommunicationssettings",
            name="twilio_whatsapp_from",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
