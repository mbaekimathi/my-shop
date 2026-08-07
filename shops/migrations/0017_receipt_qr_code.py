from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0016_receipt_font_style"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_receipt_qr",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_qr_content",
            field=models.CharField(
                choices=[
                    ("website", "Company website"),
                    ("receipt_details", "Receipt details"),
                ],
                default="website",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_qr_website",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
