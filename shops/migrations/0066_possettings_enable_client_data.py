from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0065_possettings_enable_open_close"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_client_data",
            field=models.BooleanField(
                default=True,
                help_text="When on, cart can collect client name and phone. When off, client fields are hidden.",
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="enable_client_data",
            field=models.BooleanField(
                default=True,
                help_text="When on, cart can collect client name and phone. When off, client fields are hidden.",
            ),
        ),
    ]
