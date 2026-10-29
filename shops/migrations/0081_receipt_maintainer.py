from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0080_shoppossettings_override_messages"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="show_maintainer_on_receipt",
            field=models.BooleanField(
                default=False,
                help_text="When on, receipts print a 'System maintained by' line with the maintainer phone.",
            ),
        ),
        migrations.AddField(
            model_name="companypossettings",
            name="maintainer_phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="show_maintainer_on_receipt",
            field=models.BooleanField(
                default=False,
                help_text="When on, receipts print a 'System maintained by' line with the maintainer phone.",
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="maintainer_phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
    ]
