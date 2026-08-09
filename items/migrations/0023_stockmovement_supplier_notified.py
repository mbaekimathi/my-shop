from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0022_item_low_stock_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="supplier_notified",
            field=models.BooleanField(
                default=True,
                help_text="False after a new request until the supplying shop acknowledges it.",
            ),
        ),
    ]
