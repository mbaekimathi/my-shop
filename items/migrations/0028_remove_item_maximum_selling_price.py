from decimal import Decimal

from django.db import migrations


def backfill_prices_from_max(apps, schema_editor):
    Item = apps.get_model("items", "Item")
    ShopItemPrice = apps.get_model("items", "ShopItemPrice")
    Shop = apps.get_model("shops", "Shop")

    to_update = []
    for item in Item.objects.all().only(
        "id",
        "shop_price",
        "minimum_selling_price",
        "maximum_selling_price",
    ):
        shop_price = item.shop_price or Decimal("0")
        if shop_price > 0:
            continue
        max_price = item.maximum_selling_price or Decimal("0")
        min_price = item.minimum_selling_price or Decimal("0")
        item.shop_price = max_price if max_price > 0 else min_price
        to_update.append(item)
    if to_update:
        Item.objects.bulk_update(to_update, ["shop_price"])

    shop_ids = list(Shop.objects.values_list("pk", flat=True))
    if not shop_ids:
        return

    existing = {
        (item_id, shop_id)
        for item_id, shop_id in ShopItemPrice.objects.values_list("item_id", "shop_id")
    }
    new_rows = []
    for item in Item.objects.filter(use_individual_shop_prices=True).only(
        "id", "shop_price", "maximum_selling_price"
    ):
        max_price = item.maximum_selling_price or Decimal("0")
        shop_price = item.shop_price or Decimal("0")
        price = max_price if max_price > 0 else shop_price
        if price <= 0:
            continue
        for shop_id in shop_ids:
            if (item.pk, shop_id) in existing:
                continue
            new_rows.append(
                ShopItemPrice(item_id=item.pk, shop_id=shop_id, price=price)
            )
    if new_rows:
        ShopItemPrice.objects.bulk_create(new_rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0027_alter_stockmovementline_reason"),
        ("shops", "0038_clientcreditaccountevent"),
    ]

    operations = [
        migrations.RunPython(backfill_prices_from_max, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="item",
            name="maximum_selling_price",
        ),
    ]
