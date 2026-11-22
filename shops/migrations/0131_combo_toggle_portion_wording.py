# Combo component legs moved from % shares to per-item portion qtys
# (items.0073) — refresh the toggle's help text to match.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0130_companypossettings_enable_combo_items"),
    ]

    operations = [
        migrations.AlterField(
            model_name="companypossettings",
            name="enable_combo_items",
            field=models.BooleanField(
                default=True,
                help_text="When on, combo items can be registered in Item Management and sold (the sale tallies each component by its portion). When off, combos cannot be registered or sold.",
            ),
        ),
    ]
