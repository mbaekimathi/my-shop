from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0097_shop_delivery_route_eta"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="credit_due_reminder_sent_on",
            field=models.DateField(
                blank=True,
                help_text="Local date the morning-of-due-date client reminder was sent.",
                null=True,
            ),
        ),
    ]
