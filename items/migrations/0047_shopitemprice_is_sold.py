from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0046_stockmovementline_item_name_nullable_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopitemprice",
            name="is_sold",
            field=models.BooleanField(
                default=True,
                help_text="When off, this item is not listed or sellable at this shop.",
            ),
        ),
    ]
