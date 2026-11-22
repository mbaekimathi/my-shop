# Combo component legs switch from % shares (which had to total 100) to
# per-item quantities: each leg says how many units of that item one
# combo uses. The combo's price is still Σ component price × qty — the
# same formula it used with share/100. Existing legs convert losslessly:
# qty = share / 100.

from decimal import Decimal
from django.db import migrations, models


def shares_to_qtys(apps, schema_editor):
    Component = apps.get_model("items", "ItemComboComponent")
    for leg in Component.objects.all().iterator():
        leg.qty = (leg.qty or Decimal("0")) / Decimal("100")
        leg.save(update_fields=["qty"])


def qtys_to_shares(apps, schema_editor):
    Component = apps.get_model("items", "ItemComboComponent")
    for leg in Component.objects.all().iterator():
        leg.qty = (leg.qty or Decimal("0")) * Decimal("100")
        leg.save(update_fields=["qty"])


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0072_portion_targets_per_item"),
    ]

    operations = [
        migrations.RenameField(
            model_name="itemcombocomponent",
            old_name="share",
            new_name="qty",
        ),
        migrations.AlterField(
            model_name="itemcombocomponent",
            name="qty",
            field=models.DecimalField(
                decimal_places=4,
                help_text="Units of this component in one combo.",
                max_digits=12,
            ),
        ),
        migrations.RunPython(shares_to_qtys, reverse_code=qtys_to_shares),
        migrations.AlterField(
            model_name="item",
            name="is_combo",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text=(
                    "Combo items bundle other registered items. Their price "
                    "is the sum of each component's price × its portions, "
                    "and selling one tallies each component item by those "
                    "portions instead of the combo."
                ),
            ),
        ),
    ]
