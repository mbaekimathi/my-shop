# Trade-in POS toggles + receipt kind.
# Idempotent on the database: hosted installs may already have some of these
# columns from a partial deploy, which previously failed with:
#   Duplicate column name 'enable_trade_in'

from django.db import migrations, models
import django.db.models.deletion


def _existing_columns(schema_editor, table: str) -> set[str]:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        return {
            col.name.lower()
            for col in connection.introspection.get_table_description(cursor, table)
        }


def _ensure_field(schema_editor, model, field_name: str, field: models.Field) -> None:
    field.set_attributes_from_name(field_name)
    table = model._meta.db_table
    column = (field.column or field_name).lower()
    if column in _existing_columns(schema_editor, table):
        return
    schema_editor.add_field(model, field)


def _apply_trade_in_columns(apps, schema_editor):
    CompanyPosSettings = apps.get_model("shops", "CompanyPosSettings")
    ShopPosSettings = apps.get_model("shops", "ShopPosSettings")
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    Shop = apps.get_model("shops", "Shop")

    _ensure_field(
        schema_editor,
        CompanyPosSettings,
        "enable_trade_in",
        models.BooleanField(default=True),
    )
    _ensure_field(
        schema_editor,
        CompanyPosSettings,
        "receipt_format_trade_in",
        models.CharField(default="TI", max_length=8),
    )
    _ensure_field(
        schema_editor,
        ShopPosSettings,
        "enable_trade_in",
        models.BooleanField(default=True),
    )
    _ensure_field(
        schema_editor,
        ShopPosSettings,
        "receipt_format_trade_in",
        models.CharField(default="TI", max_length=8),
    )
    _ensure_field(
        schema_editor,
        ShopReceipt,
        "source_shop",
        models.ForeignKey(
            Shop,
            blank=True,
            help_text="Shop that sent the goods on a trade-in receipt.",
            null=True,
            on_delete=django.db.models.deletion.PROTECT,
            related_name="trade_ins_sent",
        ),
    )


def _noop_reverse(apps, schema_editor):
    # Keep columns; dropping them would wipe trade-in settings / links.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0070_companyprofile_main_website"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="companypossettings",
                    name="enable_trade_in",
                    field=models.BooleanField(default=True),
                ),
                migrations.AddField(
                    model_name="companypossettings",
                    name="receipt_format_trade_in",
                    field=models.CharField(default="TI", max_length=8),
                ),
                migrations.AddField(
                    model_name="shoppossettings",
                    name="enable_trade_in",
                    field=models.BooleanField(default=True),
                ),
                migrations.AddField(
                    model_name="shoppossettings",
                    name="receipt_format_trade_in",
                    field=models.CharField(default="TI", max_length=8),
                ),
                migrations.AddField(
                    model_name="shopreceipt",
                    name="source_shop",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Shop that sent the goods on a trade-in receipt.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="trade_ins_sent",
                        to="shops.shop",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(_apply_trade_in_columns, _noop_reverse),
            ],
        ),
        migrations.AlterField(
            model_name="shopreceipt",
            name="kind",
            field=models.CharField(
                choices=[
                    ("sale", "Sale"),
                    ("credit", "Credit"),
                    ("quotation", "Quotation"),
                    ("trade_out", "Trade out"),
                    ("trade_in", "Trade in"),
                ],
                db_index=True,
                default="sale",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="shopreceipt",
            name="trade_settlements",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Trade settlement events: [{type, amount|value, method?, "
                    "item_id?, item_name?, qty?, buying_price?, stock_movement_id?, "
                    "at, by_id}, ...]."
                ),
            ),
        ),
    ]
