from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0098_shopreceipt_credit_due_reminder_sent_on"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopreceipt",
            name="credit_follow_up_date",
            field=models.DateField(
                blank=True,
                help_text="Employee follow-up date for outstanding credit (drives reminders).",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="shopreceipt",
            name="credit_due_reminder_sent_on",
            field=models.DateField(
                blank=True,
                help_text="Local date the morning-of-follow-up client reminder was sent.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="clientcreditaccountevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("credit_issued", "Credit issued"),
                    ("payment_cash", "Cash payment"),
                    ("payment_mpesa", "M-Pesa payment"),
                    ("due_date_set", "Payment due date set"),
                    ("due_date_changed", "Payment due date changed"),
                    ("follow_up_set", "Follow-up date set"),
                    ("follow_up_changed", "Follow-up date changed"),
                    ("items_returned", "Items returned"),
                    ("credit_cancelled", "Credit note cancelled"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
