from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0050_shopreceiptline_return_batches"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="return_payment_events",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Till refund events for customer returns: "
                    "[{at, cash, mpesa, by_id}, ...]. "
                    "Sale-day till uses original tender; return-day till subtracts these."
                ),
            ),
        ),
    ]
