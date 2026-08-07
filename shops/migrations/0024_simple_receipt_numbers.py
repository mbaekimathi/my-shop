from django.db import migrations, models


def shorten_default_prefixes(apps, schema_editor):
    CompanyPosSettings = apps.get_model("shops", "CompanyPosSettings")
    mapping = {
        "receipt_format_sale": ("SAL", "S"),
        "receipt_format_credit": ("CRD", "C"),
        "receipt_format_quotation": ("QTN", "Q"),
    }
    for row in CompanyPosSettings.objects.all():
        changed = False
        for field, (old, new) in mapping.items():
            current = (getattr(row, field, "") or "").strip().upper()
            if current == old:
                setattr(row, field, new)
                changed = True
        if changed:
            row.save(
                update_fields=[
                    "receipt_format_sale",
                    "receipt_format_credit",
                    "receipt_format_quotation",
                ]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0023_daraja_callback_base_url"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shopreceipt",
            name="receipt_number",
            field=models.CharField(db_index=True, max_length=40),
        ),
        migrations.AddConstraint(
            model_name="shopreceipt",
            constraint=models.UniqueConstraint(
                fields=("shop", "receipt_number"),
                name="uniq_shop_receipt_number",
            ),
        ),
        migrations.AlterField(
            model_name="companypossettings",
            name="receipt_format_sale",
            field=models.CharField(default="S", max_length=8),
        ),
        migrations.AlterField(
            model_name="companypossettings",
            name="receipt_format_credit",
            field=models.CharField(default="C", max_length=8),
        ),
        migrations.AlterField(
            model_name="companypossettings",
            name="receipt_format_quotation",
            field=models.CharField(default="Q", max_length=8),
        ),
        migrations.RunPython(shorten_default_prefixes, migrations.RunPython.noop),
    ]
