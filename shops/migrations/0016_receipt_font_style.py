from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0015_mpesa_payment_details"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_font_size",
            field=models.CharField(
                choices=[
                    ("small", "Small"),
                    ("medium", "Medium"),
                    ("large", "Large"),
                    ("xlarge", "Extra large"),
                ],
                default="medium",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_font_weight",
            field=models.CharField(
                choices=[
                    ("regular", "Regular"),
                    ("medium", "Medium"),
                    ("bold", "Bold"),
                    ("extrabold", "Extra bold"),
                ],
                default="regular",
                max_length=16,
            ),
        ),
    ]
