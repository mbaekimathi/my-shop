from django.db import migrations

SHOPS_TABLES = (
    "shops_client",
    "shops_companycommunicationssettings",
    "shops_companydarajasettings",
    "shops_companypossettings",
    "shops_companyprofile",
    "shops_companystocksettings",
    "shops_expense",
    "shops_expensesupplier",
    "shops_mpesastkpayment",
    "shops_shop",
    "shops_shopreceipt",
    "shops_shopreceiptline",
)


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0035_fix_shopdaysession_id_autoincrement"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                f"ALTER TABLE {table} "
                "MODIFY id bigint NOT NULL AUTO_INCREMENT;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        )
        for table in SHOPS_TABLES
    ]
