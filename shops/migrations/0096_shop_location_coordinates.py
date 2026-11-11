from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0095_enable_delivery_notices"),
    ]

    operations = [
        migrations.AddField(
            model_name="shop",
            name="latitude",
            field=models.DecimalField(
                blank=True, decimal_places=7, max_digits=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="shop",
            name="longitude",
            field=models.DecimalField(
                blank=True, decimal_places=7, max_digits=10, null=True
            ),
        ),
        migrations.AddField(
            model_name="shop",
            name="maps_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
    ]
