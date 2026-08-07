# Generated manually for ShopReceipt models

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
        ("items", "0016_stock_request_response_fields"),
        ("shops", "0002_shop_password_and_login_code"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShopReceipt",
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
                    "receipt_number",
                    models.CharField(db_index=True, max_length=32, unique=True),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("sale", "Sale"),
                            ("credit", "Credit"),
                            ("quotation", "Quotation"),
                        ],
                        db_index=True,
                        default="sale",
                        max_length=16,
                    ),
                ),
                (
                    "payment_method",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("mpesa", "M-Pesa"),
                            ("both", "Cash & M-Pesa"),
                            ("none", "None"),
                        ],
                        default="cash",
                        max_length=16,
                    ),
                ),
                ("client_name", models.CharField(blank=True, default="", max_length=200)),
                ("client_phone", models.CharField(blank=True, default="", max_length=40)),
                ("total", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "cash_amount",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                (
                    "mpesa_amount",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                ("share_whatsapp", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shop_receipts",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="receipts",
                        to="shops.shop",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ShopReceiptLine",
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
                ("item_name", models.CharField(max_length=200)),
                ("quantity", models.PositiveIntegerField()),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("line_total", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shop_receipt_lines",
                        to="items.item",
                    ),
                ),
                (
                    "receipt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="shops.shopreceipt",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
    ]
