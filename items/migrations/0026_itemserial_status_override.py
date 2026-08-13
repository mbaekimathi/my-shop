from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0025_recalc_shopstock_average_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemserial",
            name="status_override",
            field=models.CharField(
                blank=True,
                choices=[
                    ("in_stock", "In stock"),
                    ("sold", "Sold"),
                    ("returned", "Returned"),
                    ("out", "Stocked out"),
                ],
                db_index=True,
                default="",
                help_text="When set, serial pages use this status instead of inferring it from sales and stock.",
                max_length=16,
            ),
        ),
    ]
