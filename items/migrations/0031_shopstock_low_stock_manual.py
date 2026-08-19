from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0030_shopstock_low_stock_threshold"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopstock",
            name="low_stock_manual",
            field=models.BooleanField(
                default=False,
                help_text="True when Alert at was typed for this shop. Otherwise the weekly average is used.",
            ),
        ),
    ]
