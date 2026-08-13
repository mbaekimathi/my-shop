from django.db import migrations


def recost_shop_stock(apps, schema_editor):
    from items.services import recalc_shop_stock_average_costs

    recalc_shop_stock_average_costs()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0024_fix_items_id_autoincrement"),
    ]

    operations = [
        migrations.RunPython(recost_shop_stock, noop_reverse),
    ]
