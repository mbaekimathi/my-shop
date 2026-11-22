# Portion targets switch from % shares of a shared portions_per_unit to
# per-item portions: each leg says how many of that item one shelf unit
# gives. ShelfItem.portions_per_unit stays as the derived total. Existing
# links convert losslessly: portions = portions_per_unit × share / 100.

from decimal import Decimal
from django.db import migrations, models


def shares_to_portions(apps, schema_editor):
    ShelfItem = apps.get_model("items", "ShelfItem")
    Target = apps.get_model("items", "ShelfItemPortionTarget")
    ppu_by_shelf = {
        shelf_id: ppu
        for shelf_id, ppu in ShelfItem.objects.values_list(
            "id", "portions_per_unit"
        )
    }
    quant = Decimal("0.001")
    for target in Target.objects.all().iterator():
        ppu = ppu_by_shelf.get(target.shelf_item_id) or Decimal("0")
        target.portions = (ppu * (target.share or 0) / 100).quantize(quant)
        target.save(update_fields=["portions"])


def portions_to_shares(apps, schema_editor):
    ShelfItem = apps.get_model("items", "ShelfItem")
    Target = apps.get_model("items", "ShelfItemPortionTarget")
    ppu_by_shelf = {
        shelf_id: ppu
        for shelf_id, ppu in ShelfItem.objects.values_list(
            "id", "portions_per_unit"
        )
    }
    targets_by_shelf = {}
    for target in Target.objects.all().iterator():
        targets_by_shelf.setdefault(target.shelf_item_id, []).append(target)
    for shelf_id, targets in targets_by_shelf.items():
        ppu = ppu_by_shelf.get(shelf_id) or Decimal("0")
        total = sum(t.portions or 0 for t in targets)
        for target in targets:
            if total > 0:
                share = ((target.portions or 0) * 100 / total).quantize(
                    Decimal("0.01")
                )
            else:
                share = Decimal("100") / len(targets)
            target.share = share
            target.save(update_fields=["share"])


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0071_shelfitemportiontarget"),
    ]

    operations = [
        migrations.AddField(
            model_name="shelfitemportiontarget",
            name="portions",
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                help_text="Portions of this item each shelf unit gives.",
                max_digits=12,
            ),
        ),
        migrations.RunPython(
            shares_to_portions, reverse_code=portions_to_shares
        ),
        migrations.RemoveField(
            model_name="shelfitemportiontarget",
            name="share",
        ),
        migrations.AlterField(
            model_name="shelfitem",
            name="portions_per_unit",
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                help_text=(
                    "Total portions each shelf unit gives on a trade out — "
                    "the sum of the linked portion targets' portions. 0 or "
                    "no targets = trade out only moves the shelf."
                ),
                max_digits=12,
            ),
        ),
    ]
