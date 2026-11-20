"""Unify stock_count_enabled and deduct_stock_on_sale (off wins).

The item-stock page's "Stock count" switch and the Sale stock tracking
switch now drive the same per-item setting. For rows where the two flags
disagree, keep the item opted out — never switch tracking on for an item
that was switched off on either page.
"""

from django.db import migrations, models


def link_flags(apps, schema_editor):
    Item = apps.get_model("items", "Item")
    # Opted out on the item-stock page -> also off for sale deduction, and
    # marked custom so the company-default sync won't switch it back on.
    Item.objects.filter(
        stock_count_enabled=False, deduct_stock_on_sale=True
    ).update(deduct_stock_on_sale=False, sale_tracking_custom=True)
    # Opted out on the sale tracking page -> also off for store counting.
    Item.objects.filter(
        deduct_stock_on_sale=False, stock_count_enabled=True
    ).update(stock_count_enabled=False)


def unlink_flags(apps, schema_editor):
    # Not reversible — the original per-page picks can't be reconstructed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0067_alter_shelfstockmovement_reason_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="item",
            name="stock_count_enabled",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text=(
                    "Whether POS sales/returns move this item's central store count "
                    "(StoreStock). Kept in sync with deduct_stock_on_sale — the "
                    "item-stock page and Sale stock tracking page drive one switch."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="item",
            name="deduct_stock_on_sale",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text=(
                    "Whether selling/returning this item on the shop page moves its "
                    "stock counts. Kept in sync with stock_count_enabled — off on "
                    "either page means the item sells freely and its stock is manual only."
                ),
            ),
        ),
        migrations.RunPython(link_flags, unlink_flags),
    ]
