from django.db import migrations

POS_TABLES = (
    "pos_product",
    "pos_sale",
    "pos_saleline",
)


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                f"ALTER TABLE {table} "
                "MODIFY id bigint NOT NULL AUTO_INCREMENT;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        )
        for table in POS_TABLES
    ]
