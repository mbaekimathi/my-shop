from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0007_companypossettings_enable_discount"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="compulsory_print_on_sale",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="enable_print_bluetooth",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="enable_print_usb",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="enable_print_wifi",
            field=models.BooleanField(default=True),
        ),
    ]
