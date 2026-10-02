from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0062_daraja_deploy_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_stock_tracking",
            field=models.BooleanField(
                default=True,
                help_text="When on, checkout requires enough shop stock. When off, sales can proceed without stock.",
            ),
        ),
    ]
