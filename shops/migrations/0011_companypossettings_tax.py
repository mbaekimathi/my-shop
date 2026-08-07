from decimal import Decimal

from django.db import migrations, models


def backfill_receipt_subtotals(apps, schema_editor):
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    for receipt in ShopReceipt.objects.all().iterator():
        if receipt.subtotal == 0 and receipt.total:
            receipt.subtotal = receipt.total
            receipt.save(update_fields=["subtotal"])


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0010_expense_and_expense_supplier"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="enable_tax",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="tax_percent",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=6
            ),
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="subtotal",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="tax_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="shopreceipt",
            name="tax_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=6),
        ),
        migrations.RunPython(backfill_receipt_subtotals, migrations.RunPython.noop),
    ]
