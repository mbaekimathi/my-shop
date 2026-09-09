from decimal import Decimal

from django.db import migrations


def materialize_shop_item_prices(apps, schema_editor):
    Item = apps.get_model("items", "Item")
    Shop = apps.get_model("shops", "Shop")
    ShopItemPrice = apps.get_model("items", "ShopItemPrice")

    shops = list(
        Shop.objects.filter(is_hidden=False, is_suspended=False).values_list("id", flat=True)
    )
    if not shops:
        Item.objects.filter(use_individual_shop_prices=False).update(
            use_individual_shop_prices=True
        )
        return

    existing = {
        (item_id, shop_id)
        for item_id, shop_id in ShopItemPrice.objects.values_list("item_id", "shop_id")
    }
    to_create = []

    for item in Item.objects.all().only(
        "id", "shop_price", "minimum_selling_price", "use_individual_shop_prices"
    ):
        seed = item.shop_price if item.shop_price and item.shop_price > 0 else item.minimum_selling_price
        if seed is None:
            seed = Decimal("0.00")
        seed = seed.quantize(Decimal("0.01"))
        for shop_id in shops:
            if (item.id, shop_id) in existing:
                continue
            to_create.append(
                ShopItemPrice(item_id=item.id, shop_id=shop_id, price=seed)
            )

    if to_create:
        ShopItemPrice.objects.bulk_create(to_create, batch_size=500)

    Item.objects.filter(use_individual_shop_prices=False).update(
        use_individual_shop_prices=True
    )


def noop_reverse(apps, schema_editor):
    # Keep per-shop rows; do not destroy pricing data on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0033_stockentry_customer_return"),
        ("shops", "0056_mpesa_send_money_pochi"),
    ]

    operations = [
        migrations.RunPython(materialize_shop_item_prices, noop_reverse),
    ]
