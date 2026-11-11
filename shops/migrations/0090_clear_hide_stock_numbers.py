from django.db import migrations


def clear_hide_stock_numbers(apps, schema_editor):
    """Stop permanent category/item stock hiding; stock take still masks selected lines."""
    CompanyStockSettings = apps.get_model("shops", "CompanyStockSettings")
    CompanyStockSettings.objects.filter(pk=1).update(
        hide_stock_mode="show",
        hide_stock_categories=[],
        hide_stock_item_ids=[],
    )


def noop_reverse(apps, schema_editor):
    # Previous category picks were environment-specific; do not restore them.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0089_alter_receipt_payment_methods_help_text"),
    ]

    operations = [
        migrations.RunPython(clear_hide_stock_numbers, noop_reverse),
    ]
