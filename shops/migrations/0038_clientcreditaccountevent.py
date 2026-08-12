import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0009_fix_assigned_shops_id_autoincrement"),
        ("shops", "0037_shopreceipt_credit_due_date"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientCreditAccountEvent",
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
                    "kind",
                    models.CharField(
                        choices=[
                            ("credit_issued", "Credit issued"),
                            ("payment_cash", "Cash payment"),
                            ("payment_mpesa", "M-Pesa payment"),
                            ("due_date_set", "Payment due date set"),
                            ("due_date_changed", "Payment due date changed"),
                            ("items_returned", "Items returned"),
                            ("credit_cancelled", "Credit note cancelled"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("detail", models.CharField(blank=True, default="", max_length=255)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="client_credit_account_events",
                        to="employees.employeeprofile",
                    ),
                ),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credit_account_events",
                        to="shops.client",
                    ),
                ),
                (
                    "receipt",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="credit_account_events",
                        to="shops.shopreceipt",
                    ),
                ),
                (
                    "shop",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="client_credit_account_events",
                        to="shops.shop",
                    ),
                ),
                (
                    "stk_payment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="credit_account_events",
                        to="shops.mpesastkpayment",
                    ),
                ),
            ],
            options={
                "ordering": ["-occurred_at", "-pk"],
                "indexes": [
                    models.Index(
                        fields=["client", "-occurred_at"],
                        name="shops_clien_client__8a0f0d_idx",
                    )
                ],
            },
        ),
    ]
