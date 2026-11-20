from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0122_alter_shopreceipt_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="require_clock_in",
            field=models.BooleanField(
                default=False,
                help_text="When on, employees must be clocked in before a sale can complete.",
            ),
        ),
    ]
