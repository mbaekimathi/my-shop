from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0011_companypossettings_tax"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_paper_width",
            field=models.CharField(
                choices=[("80", "80 mm"), ("58", "58 mm")],
                default="80",
                max_length=8,
            ),
        ),
    ]
