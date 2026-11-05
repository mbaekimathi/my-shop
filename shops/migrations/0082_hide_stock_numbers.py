from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0081_receipt_maintainer"),
    ]

    operations = [
        migrations.AddField(
            model_name="companystocksettings",
            name="hide_stock_mode",
            field=models.CharField(
                choices=[
                    ("show", "Show stock numbers"),
                    ("all", "Hide all"),
                    ("category", "Hide selected categories"),
                    ("items", "Hide selected items"),
                ],
                default="show",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="companystocksettings",
            name="hide_stock_categories",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="companystocksettings",
            name="hide_stock_item_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
