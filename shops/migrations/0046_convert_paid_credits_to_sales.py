from decimal import Decimal

from django.db import migrations


def convert_paid_credits_to_sales(apps, schema_editor):
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    zero = Decimal("0.00")
    for receipt in ShopReceipt.objects.filter(kind="credit").exclude(status="cancelled"):
        total = Decimal(receipt.total or 0)
        paid = Decimal(receipt.amount_paid or 0)
        if paid < total or (paid <= 0 and total <= 0):
            continue
        cash = Decimal(receipt.cash_amount or 0)
        mpesa = Decimal(receipt.mpesa_amount or 0)
        if cash > 0 and mpesa > 0:
            receipt.payment_method = "both"
        elif mpesa > 0:
            receipt.payment_method = "mpesa"
        elif cash > 0:
            receipt.payment_method = "cash"
        elif (receipt.mpesa_receipt_number or "").strip():
            receipt.payment_method = "mpesa"
            receipt.mpesa_amount = paid
        else:
            receipt.payment_method = "cash"
            receipt.cash_amount = paid or total or zero
        receipt.kind = "sale"
        receipt.credit_due_date = None
        receipt.save(
            update_fields=[
                "kind",
                "payment_method",
                "cash_amount",
                "mpesa_amount",
                "credit_due_date",
            ]
        )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0045_alter_companycommunicationssettings_options"),
    ]

    operations = [
        migrations.RunPython(convert_paid_credits_to_sales, noop_reverse),
    ]
