from decimal import Decimal

from django.db import migrations, models


def backfill_receipt_line_costs(apps, schema_editor):
    """Stamp historical sale/credit lines with last-known buying price as unit_cost."""
    ShopReceiptLine = apps.get_model("shops", "ShopReceiptLine")
    StockMovementLine = apps.get_model("items", "StockMovementLine")

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
    qs = (
        ShopReceiptLine.objects.filter(unit_cost=0)
        .exclude(item_id__isnull=True)
        .select_related("receipt")
        .only(
            "id",
            "item_id",
            "quantity",
            "unit_cost",
            "line_cogs",
            "receipt__shop_id",
        )
    )
    for line in qs.iterator():
        shop_id = line.receipt.shop_id
        price = latest_by_item_shop.get((shop_id, line.item_id)) or latest_by_item.get(
            line.item_id
        )
        if price is None or price <= 0:
            continue
        unit = price.quantize(Decimal("0.01"))
        qty = int(line.quantity or 0)
        line.unit_cost = unit
        line.line_cogs = (unit * qty).quantize(Decimal("0.01"))
        to_update.append(line)
        if len(to_update) >= 500:
            ShopReceiptLine.objects.bulk_update(
                to_update, ["unit_cost", "line_cogs"]
            )
            to_update = []
    if to_update:
        ShopReceiptLine.objects.bulk_update(to_update, ["unit_cost", "line_cogs"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0026_expense_owner_drawings_category"),
        ("items", "0020_shopstock_average_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceiptline",
            name="unit_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Cost per unit at sale (weighted average / last buy snapshot).",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="shopreceiptline",
            name="line_cogs",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="unit_cost × original quantity at sale; remaining COGS uses unit_cost × remaining qty.",
                max_digits=12,
            ),
        ),
        migrations.RunPython(backfill_receipt_line_costs, noop_reverse),
    ]
