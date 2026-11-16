import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0014_web_push_subscription"),
        ("items", "0063_item_deduct_stock_on_sale"),
        ("shops", "0117_shelf_stock_requirements"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShelfStockTake",
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
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("closed", "Closed")],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                ("note", models.CharField(blank=True, default="", max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                (
                    "applied_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shelf_stock_takes_applied",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shelf_stock_takes_closed",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shelf_stock_takes_created",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shelf_stock_takes",
                        to="shops.shop",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="ShelfOnlineStockTakeEntry",
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
                ("count_date", models.DateField(db_index=True)),
                (
                    "physical_qty",
                    models.DecimalField(
                        blank=True, decimal_places=3, max_digits=12, null=True
                    ),
                ),
                (
                    "system_qty_at_count",
                    models.DecimalField(
                        blank=True, decimal_places=3, max_digits=12, null=True
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "updated_by_label",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "shelf_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="online_stock_take_entries",
                        to="items.shelfitem",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shelf_online_stock_take_entries",
                        to="shops.shop",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shelf_online_stock_take_entries",
                        to="employees.employeeprofile",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "-count_date",
                    "shelf_item__category",
                    "shelf_item__name",
                    "id",
                ],
            },
        ),
        migrations.CreateModel(
            name="ShelfStockTakeEntry",
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
                    "system_qty",
                    models.DecimalField(decimal_places=3, default=0, max_digits=12),
                ),
                (
                    "counted_qty",
                    models.DecimalField(
                        blank=True, decimal_places=3, max_digits=12, null=True
                    ),
                ),
                (
                    "shelf_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stock_take_entries",
                        to="items.shelfitem",
                    ),
                ),
                (
                    "take",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entries",
                        to="items.shelfstocktake",
                    ),
                ),
            ],
            options={
                "ordering": ["shelf_item__category", "shelf_item__name", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="shelfonlinestocktakeentry",
            constraint=models.UniqueConstraint(
                fields=("shop", "shelf_item", "count_date"),
                name="uniq_shelf_online_take_shop_item_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="shelfstocktakeentry",
            constraint=models.UniqueConstraint(
                fields=("take", "shelf_item"), name="uniq_shelf_take_item"
            ),
        ),
    ]
