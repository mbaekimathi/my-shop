from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0041_swap_legacy_transfer_shop_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemserial",
            name="status_reason",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional note when a serial status is set or corrected manually.",
                max_length=500,
            ),
        ),
    ]
