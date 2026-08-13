from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("items", "0026_itemserial_status_override"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stockmovementline",
            name="reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("waste", "Waste"),
                    ("transfer", "Transfer"),
                    ("display", "Display"),
                    ("return", "Supplier return"),
                ],
                default="",
                max_length=16,
            ),
        ),
    ]
