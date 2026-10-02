from decimal import Decimal

from django.db import migrations, models


def backfill_expense_cash_amounts(apps, schema_editor):
    Expense = apps.get_model("shops", "Expense")
    zero = Decimal("0.00")
    for expense in Expense.objects.all().iterator():
        paid = expense.amount_paid or expense.amount or zero
        if (expense.cash_amount or zero) == zero and (expense.mpesa_amount or zero) == zero:
            expense.cash_amount = paid
            expense.mpesa_amount = zero
            expense.save(update_fields=["cash_amount", "mpesa_amount"])


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0067_possettings_require_client_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="cash_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Cash portion paid from the till (used for owner drawings).",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="expense",
            name="mpesa_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="M-Pesa portion paid from the till (used for owner drawings).",
                max_digits=12,
            ),
        ),
        migrations.RunPython(backfill_expense_cash_amounts, migrations.RunPython.noop),
    ]
