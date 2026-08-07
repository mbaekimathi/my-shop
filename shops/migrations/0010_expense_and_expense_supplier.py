# Generated manually for ExpenseSupplier + Expense

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
        ("shops", "0009_sync_pending_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpenseSupplier",
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
                ("name", models.CharField(db_index=True, max_length=160)),
                ("phone_country_code", models.CharField(db_index=True, max_length=8)),
                (
                    "phone_country_iso",
                    models.CharField(blank=True, default="KE", max_length=2),
                ),
                ("phone_number", models.CharField(db_index=True, max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name", "phone_number"],
            },
        ),
        migrations.CreateModel(
            name="Expense",
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
                    "category",
                    models.CharField(
                        choices=[
                            ("rent", "Rent"),
                            ("utilities", "Utilities"),
                            ("transport", "Transport"),
                            ("salaries", "Salaries"),
                            ("packaging", "Packaging"),
                            ("maintenance", "Maintenance"),
                            ("marketing", "Marketing"),
                            ("office", "Office supplies"),
                            ("security", "Security"),
                            ("food", "Food & refreshments"),
                            ("misc", "Miscellaneous"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "payment_status",
                    models.CharField(
                        choices=[
                            ("unpaid", "Unpaid"),
                            ("paid", "Paid"),
                            ("partial", "Partial"),
                        ],
                        db_index=True,
                        default="unpaid",
                        max_length=16,
                    ),
                ),
                (
                    "supplier_name",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "supplier_phone_country_code",
                    models.CharField(blank=True, default="", max_length=8),
                ),
                (
                    "supplier_phone_number",
                    models.CharField(blank=True, default="", max_length=20),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="expenses_created",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="expenses",
                        to="shops.shop",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="expenses",
                        to="shops.expensesupplier",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="expensesupplier",
            constraint=models.UniqueConstraint(
                fields=("phone_country_code", "phone_number"),
                name="uniq_expense_supplier_phone",
            ),
        ),
    ]
