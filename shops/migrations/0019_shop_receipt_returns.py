# Generated manually for receipt returns / cancellation

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0007_employeemodulepermission"),
        ("shops", "0018_shopreceiptline_serial_numbers"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("partial_return", "Partially returned"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="last_returned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="last_returned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shop_receipts_returned",
                to="employees.employeeprofile",
            ),
        ),
        migrations.AddField(
            model_name="shopreceiptline",
            name="returned_quantity",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="shopreceiptline",
            name="returned_serial_numbers",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
