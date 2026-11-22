# Portion links move from a single ShelfItem.portion_item FK to a
# ShelfItemPortionTarget child model so one unit can split its portions
# across several catalog items (% shares totaling 100).

import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


def backfill_portion_targets(apps, schema_editor):
    ShelfItem = apps.get_model("items", "ShelfItem")
    Target = apps.get_model("items", "ShelfItemPortionTarget")
    rows = [
        Target(
            shelf_item_id=shelf_id,
            item_id=item_id,
            share=Decimal("100"),
            position=0,
        )
        for shelf_id, item_id in ShelfItem.objects.filter(
            portion_item__isnull=False
        ).values_list("id", "portion_item_id")
    ]
    Target.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0070_item_is_combo_itemcombocomponent_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShelfItemPortionTarget",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "share",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Percent of the unit's portions this item receives.",
                        max_digits=7,
                    ),
                ),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shelf_portion_targets",
                        to="items.item",
                    ),
                ),
                (
                    "shelf_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portion_targets",
                        to="items.shelfitem",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="shelfitemportiontarget",
            constraint=models.UniqueConstraint(
                fields=("shelf_item", "item"), name="uniq_shelf_portion_target"
            ),
        ),
        migrations.RunPython(
            backfill_portion_targets, migrations.RunPython.noop
        ),
        migrations.RemoveField(
            model_name="shelfitem",
            name="portion_item",
        ),
        migrations.AlterField(
            model_name="shelfitem",
            name="portions_per_unit",
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                help_text=(
                    "How many portions each shelf unit stocks into the "
                    "linked portion targets on a trade out. 0 or no "
                    "targets = trade out only moves the shelf."
                ),
                max_digits=12,
            ),
        ),
    ]
