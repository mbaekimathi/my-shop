from decimal import Decimal

from django.db import migrations, models


def backfill_average_cost(apps, schema_editor):
    """Seed shop stock average_cost from the latest stock-in buying price."""
    ShopStock = apps.get_model("items", "ShopStock")
    StockMovementLine = apps.get_model("items", "StockMovementLine")
    zero = Decimal("0.00")

    latest_by_item_shop = {}
    latest_by_item = {}
    lines = (
        StockMovementLine.objects.filter(
            buying_price__isnull=False,
            movement__movement_type="in",
        )
        .order_by("movement__created_at", "id")
        .values_list("item_id", "movement__shop_id", "buying_price")
    )
    for item_id, shop_id, buying_price in lines.iterator():
        price = Decimal(buying_price or 0)
        if price <= 0:
            continue
        latest_by_item[item_id] = price
        latest_by_item_shop[(shop_id, item_id)] = price

    to_update = []
    for row in ShopStock.objects.all().only("id", "shop_id", "item_id", "average_cost"):
        price = latest_by_item_shop.get((row.shop_id, row.item_id)) or latest_by_item.get(
            row.item_id
        )
        if price is None or price <= 0:
            continue
        row.average_cost = price.quantize(Decimal("0.01"))
        to_update.append(row)
        if len(to_update) >= 500:
            ShopStock.objects.bulk_update(to_update, ["average_cost"])
            to_update = []
    if to_update:
        ShopStock.objects.bulk_update(to_update, ["average_cost"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0019_amount_paid_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopstock",
            name="average_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Weighted average unit cost for this shop's on-hand stock.",
                max_digits=12,
            ),
        ),
        migrations.RunPython(backfill_average_cost, noop_reverse),
    ]
