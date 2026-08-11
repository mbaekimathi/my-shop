from django.db import migrations

# MySQL tables imported/restored without AUTO_INCREMENT on primary keys break
# Django inserts (StockMovement.create, etc.).
ITEMS_TABLES = (
    "items_item",
    "items_itemserial",
    "items_shopitemprice",
    "items_shopstock",
    "items_stockmovement",
    "items_stockmovementline",
    "items_supplier",
)


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0023_stockmovement_supplier_notified"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                f"ALTER TABLE {table} "
                "MODIFY id bigint NOT NULL AUTO_INCREMENT;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        )
        for table in ITEMS_TABLES
    ]
