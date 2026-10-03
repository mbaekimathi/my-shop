from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0036_stock_out_reason_custom"),
    ]

    operations = [
        migrations.AlterField(
            model_name="item",
            name="stock",
            field=models.DecimalField(
                decimal_places=3, default=Decimal("0"), max_digits=12
            ),
        ),
        migrations.AlterField(
            model_name="shopstock",
            name="quantity",
            field=models.DecimalField(
                decimal_places=3, default=Decimal("0"), max_digits=12
            ),
        ),
        migrations.AlterField(
            model_name="stockmovementline",
            name="quantity",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
    ]
