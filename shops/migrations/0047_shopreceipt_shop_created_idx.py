from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0046_convert_paid_credits_to_sales"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="shopreceipt",
            index=models.Index(
                fields=["shop", "created_at"],
                name="shopreceipt_shop_created_idx",
            ),
        ),
    ]
