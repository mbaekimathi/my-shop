from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0119_delete_companyanalyticssettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_gift",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="receipt_format_gift",
            field=models.CharField(default="GF", max_length=8),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="enable_gift",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="receipt_format_gift",
            field=models.CharField(default="GF", max_length=8),
        ),
    ]
