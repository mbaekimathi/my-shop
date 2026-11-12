from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0100_client_credit_follow_up_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="companypossettings",
            name="require_mpesa_reference_on_confirm",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When on, confirming an M-Pesa or Cash + M-Pesa sale requires a "
                    "reference number. When off, the reference is optional."
                ),
            ),
        ),
        migrations.AddField(
            model_name="shoppossettings",
            name="require_mpesa_reference_on_confirm",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When on, confirming an M-Pesa or Cash + M-Pesa sale requires a "
                    "reference number. When off, the reference is optional."
                ),
            ),
        ),
    ]
