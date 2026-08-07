from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0013_supplier"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovementline",
            name="refund",
            field=models.CharField(
                blank=True,
                default="",
                help_text="yes or no for stock-out refund",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="stockmovementline",
            name="refund_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
            ),
        ),
    ]
