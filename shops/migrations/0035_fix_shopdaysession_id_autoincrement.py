from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0034_shop_working_hours_settings"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE shops_shopdaysession "
                "MODIFY id bigint NOT NULL AUTO_INCREMENT;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
