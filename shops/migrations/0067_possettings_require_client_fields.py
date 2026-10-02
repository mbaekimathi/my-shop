from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0066_possettings_enable_client_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="require_client_phone",
            field=models.BooleanField(
                default=False,
                help_text="When on (and client data is collected), client phone is required before checkout.",
            ),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="require_client_name",
            field=models.BooleanField(
                default=False,
                help_text="When on (and client data is collected), client full name is required before checkout.",
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="require_client_phone",
            field=models.BooleanField(
                default=False,
                help_text="When on (and client data is collected), client phone is required before checkout.",
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="require_client_name",
            field=models.BooleanField(
                default=False,
                help_text="When on (and client data is collected), client full name is required before checkout.",
            ),
        ),
    ]
