from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0011_shop_item_prices"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovementline",
            name="supplier_name",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="stockmovementline",
            name="supplier_phone_country_code",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="stockmovementline",
            name="supplier_phone_number",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
