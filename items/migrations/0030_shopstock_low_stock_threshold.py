from django.db import migrations, models


def copy_item_thresholds(apps, schema_editor):
    Item = apps.get_model("items", "Item")
    ShopStock = apps.get_model("items", "ShopStock")
    for item in Item.objects.exclude(low_stock_threshold=0).iterator():
        ShopStock.objects.filter(item_id=item.pk).update(
            low_stock_threshold=item.low_stock_threshold
        )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0029_stockmovement_entry_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopstock",
            name="low_stock_threshold",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Alert when this shop's on-hand quantity is at or below this value.",
            ),
        ),
        migrations.RunPython(copy_item_thresholds, noop_reverse),
    ]
