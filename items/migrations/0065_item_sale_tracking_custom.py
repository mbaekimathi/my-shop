from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('items', '0064_shelf_take_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='sale_tracking_custom',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True once this item's sale stock tracking was picked by "
                    "hand on the Sale stock tracking page. False = it "
                    "auto-follows the company default "
                    "(CompanyPosSettings.enable_stock_tracking)."
                ),
            ),
        ),
    ]
