from django.db import migrations


def recost_shop_stock(apps, schema_editor):
    """
    Originally called items.services.recalc_shop_stock_average_costs().

    That live helper now filters on StockMovement.entry_source, which is only
    added in migration 0029 — so running it here breaks fresh installs.

    Recalculation is optional data maintenance; run after migrate:
      python manage.py recalc_average_costs
    """
    pass


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0024_fix_items_id_autoincrement"),
    ]

    operations = [
        migrations.RunPython(recost_shop_stock, noop_reverse),
    ]
