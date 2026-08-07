# Generated manually for load-speed indexes

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0017_sync_pending_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stockmovement",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AlterField(
            model_name="stockmovementline",
            name="buying_price",
            field=models.DecimalField(
                blank=True,
                db_index=True,
                decimal_places=2,
                max_digits=12,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(
                fields=["shop", "-created_at"],
                name="items_stockmv_shop_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(
                fields=["movement_type", "-created_at"],
                name="items_stockmv_type_created_idx",
            ),
        ),
    ]
