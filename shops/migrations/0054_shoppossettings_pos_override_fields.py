from decimal import Decimal

from django.db import migrations, models


MISSING_FIELDS = (
    (
        "override_pos",
        models.BooleanField(
            default=False,
            help_text="When on, shop POS values replace company POS defaults.",
        ),
    ),
    (
        "override_receipt",
        models.BooleanField(
            default=False,
            help_text="When on, shop receipt values replace company receipt defaults.",
        ),
    ),
    ("enable_sale", models.BooleanField(default=True)),
    ("enable_credit", models.BooleanField(default=True)),
    ("enable_quotation", models.BooleanField(default=True)),
    ("enable_cash_sale_checkout", models.BooleanField(default=True)),
    ("enable_cash", models.BooleanField(default=True)),
    ("enable_mpesa", models.BooleanField(default=True)),
    ("enable_cash_mpesa", models.BooleanField(default=True)),
    ("enable_discount", models.BooleanField(default=True)),
    ("enable_tax", models.BooleanField(default=False)),
    (
        "tax_percent",
        models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=6),
    ),
)


def _existing_columns(schema_editor, table):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table)
    return {col.name for col in description}


def add_missing_pos_fields(apps, schema_editor):
    """
    Older environments may have a partial shops_shoppossettings table while
    0053 is already recorded. Add any missing POS override columns safely.
    Fresh installs already get the full table from 0053.
    """
    ShopPosSettings = apps.get_model("shops", "ShopPosSettings")
    table = ShopPosSettings._meta.db_table
    try:
        existing = _existing_columns(schema_editor, table)
    except Exception:
        return
    for name, field in MISSING_FIELDS:
        if name in existing:
            continue
        field.set_attributes_from_name(name)
        schema_editor.add_field(ShopPosSettings, field)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0053_shop_pos_settings"),
    ]

    operations = [
        migrations.RunPython(add_missing_pos_fields, noop_reverse),
    ]
