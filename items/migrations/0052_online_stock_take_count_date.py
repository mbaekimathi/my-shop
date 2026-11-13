# Generated manually for daily online stock take.

from django.db import migrations, models
from django.utils import timezone


def backfill_count_date(apps, schema_editor):
    OnlineStockTakeEntry = apps.get_model("items", "OnlineStockTakeEntry")
    today = timezone.localdate()
    updates = []
    for entry in OnlineStockTakeEntry.objects.all().only("id", "updated_at", "count_date"):
        if entry.count_date:
            continue
        if entry.updated_at is not None:
            entry.count_date = timezone.localtime(entry.updated_at).date()
        else:
            entry.count_date = today
        updates.append(entry)
        if len(updates) >= 500:
            OnlineStockTakeEntry.objects.bulk_update(updates, ["count_date"])
            updates = []
    if updates:
        OnlineStockTakeEntry.objects.bulk_update(updates, ["count_date"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0051_store_stock"),
    ]

    operations = [
        migrations.AddField(
            model_name="onlinestocktakeentry",
            name="count_date",
            field=models.DateField(db_index=True, null=True),
        ),
        migrations.RunPython(backfill_count_date, noop_reverse),
        migrations.AlterField(
            model_name="onlinestocktakeentry",
            name="count_date",
            field=models.DateField(db_index=True),
        ),
        migrations.RemoveConstraint(
            model_name="onlinestocktakeentry",
            name="uniq_online_stock_take_shop_item",
        ),
        migrations.AddConstraint(
            model_name="onlinestocktakeentry",
            constraint=models.UniqueConstraint(
                fields=("shop", "item", "count_date"),
                name="uniq_online_stock_take_shop_item_date",
            ),
        ),
        migrations.AlterModelOptions(
            name="onlinestocktakeentry",
            options={
                "ordering": ["-count_date", "item__category", "item__name", "id"]
            },
        ),
    ]
