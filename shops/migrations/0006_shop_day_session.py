# Generated manually for ShopDaySession

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
        ("shops", "0005_company_pos_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShopDaySession",
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
                ("opened_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "closed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "opening_cash",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                (
                    "opening_mpesa",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                (
                    "opening_credit",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                (
                    "closing_cash",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                (
                    "closing_mpesa",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                (
                    "closing_credit",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("stock_confirmed_open", models.BooleanField(default=False)),
                ("stock_confirmed_close", models.BooleanField(default=False)),
                (
                    "closed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shop_days_closed",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "opened_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shop_days_opened",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="day_sessions",
                        to="shops.shop",
                    ),
                ),
            ],
            options={
                "ordering": ["-opened_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="shopdaysession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("closed_at__isnull", True)),
                fields=("shop",),
                name="uniq_open_shop_day_session",
            ),
        ),
    ]
