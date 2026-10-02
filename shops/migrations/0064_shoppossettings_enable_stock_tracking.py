from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0063_companypossettings_enable_stock_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="shoppossettings",
            name="enable_stock_tracking",
            field=models.BooleanField(
                default=True,
                help_text="When on, checkout requires enough shop stock. When off, sales can proceed without stock.",
            ),
        ),
    ]
