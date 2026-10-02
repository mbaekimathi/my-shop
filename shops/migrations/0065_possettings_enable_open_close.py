from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0064_shoppossettings_enable_stock_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_open_close",
            field=models.BooleanField(
                default=True,
                help_text="When on, staff must open the shop day before selling and are redirected to open/close. When off, open/close is optional.",
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="enable_open_close",
            field=models.BooleanField(
                default=True,
                help_text="When on, staff must open the shop day before selling and are redirected to open/close. When off, open/close is optional.",
            ),
        ),
    ]
