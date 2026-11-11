from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0096_shop_location_coordinates"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopdelivery",
            name="distance_km",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Estimated road distance from the shop to the drop-off.",
                max_digits=8,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="shopdelivery",
            name="eta_minutes",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Estimated travel minutes from the shop to the drop-off.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="shopdelivery",
            name="expected_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Target complete time once a rider accepts (accepted_at + eta).",
                null=True,
            ),
        ),
    ]
