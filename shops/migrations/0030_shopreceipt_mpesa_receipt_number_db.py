# Ensure shops_shopreceipt.mpesa_receipt_number exists in the database.
# Migration 0022 only updated Django state (database_operations=[]).

from django.db import migrations


def _add_mpesa_receipt_number_column(apps, schema_editor):
    table = "shops_shopreceipt"
    column = "mpesa_receipt_number"
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = {
            col.name.lower()
            for col in connection.introspection.get_table_description(cursor, table)
        }
    if column in existing:
        return
    qn = connection.ops.quote_name
    # Match ShopReceipt.mpesa_receipt_number: CharField(max_length=40, blank=True, default="")
    schema_editor.execute(
        f"ALTER TABLE {qn(table)} "
        f"ADD COLUMN {qn(column)} varchar(40) NOT NULL DEFAULT ''"
    )


def _noop_reverse(apps, schema_editor):
    # Keep the column; removing it would drop stored M-Pesa refs.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0029_company_stock_settings_supplier_last4"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunPython(
                    _add_mpesa_receipt_number_column,
                    _noop_reverse,
                ),
            ],
        ),
    ]
