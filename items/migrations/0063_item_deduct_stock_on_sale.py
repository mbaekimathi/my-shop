from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('items', '0062_shelfstockmovement_amount_paid'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='deduct_stock_on_sale',
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text=(
                    "Whether selling/returning this item on the shop page moves "
                    "its stock counts. Off = the item sells freely; its stock is "
                    "manual only."
                ),
            ),
        ),
    ]
