from decimal import Decimal

from django.db import migrations, models


def backfill_stock_out_unit_cost(apps, schema_editor):
    StockMovementLine = apps.get_model("items", "StockMovementLine")
    ShopStock = apps.get_model("items", "ShopStock")

    avg_by_shop_item = {
        (row.shop_id, row.item_id): Decimal(row.average_cost or 0)
        for row in ShopStock.objects.all().only("shop_id", "item_id", "average_cost")
    }
    to_update = []
    qs = (
        StockMovementLine.objects.filter(
            movement__movement_type="out",
            unit_cost=0,
        )
        .select_related("movement")
        .only("id", "item_id", "unit_cost", "buying_price", "movement__shop_id")
    )
    for line in qs.iterator():
        shop_id = line.movement.shop_id
        unit = avg_by_shop_item.get((shop_id, line.item_id)) or Decimal(
            line.buying_price or 0
        )
        if unit <= 0:
            continue
        line.unit_cost = unit.quantize(Decimal("0.01"))
        to_update.append(line)
        if len(to_update) >= 500:
            StockMovementLine.objects.bulk_update(to_update, ["unit_cost"])
            to_update = []
    if to_update:
        StockMovementLine.objects.bulk_update(to_update, ["unit_cost"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0020_shopstock_average_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovementline",
            name="unit_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Cost per unit removed on stock-out (shop weighted average at the time).",
                max_digits=12,
            ),
        ),
        migrations.RunPython(backfill_stock_out_unit_cost, noop_reverse),
    ]
