# Generated manually for Client model + ShopReceipt.client

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
        ("shops", "0003_shop_receipt"),
    ]

    operations = [
        migrations.CreateModel(
            name="Client",
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
                ("full_name", models.CharField(max_length=200)),
                ("phone_number", models.CharField(max_length=40)),
                (
                    "phone_normalized",
                    models.CharField(db_index=True, max_length=40, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="clients_created",
                        to="employees.employeeprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["full_name", "id"],
            },
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="receipts",
                to="shops.client",
            ),
        ),
    ]
