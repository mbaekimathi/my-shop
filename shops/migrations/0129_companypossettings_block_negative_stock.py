from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0128_backfill_converted_from_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="block_negative_stock",
            field=models.BooleanField(
                default=True,
                help_text="When on, tracked item sales cannot take shop stock below zero. When off, tracked items can sell into negative stock.",
            ),
        ),
    ]
