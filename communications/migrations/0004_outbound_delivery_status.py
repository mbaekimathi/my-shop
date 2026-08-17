from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0003_app_reply_link"),
    ]

    operations = [
        migrations.AlterField(
            model_name="outboundmessage",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("sent", "Sent"),
                    ("failed", "Failed"),
                    ("manual_review", "Manual review"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="wa_message_id",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=200
            ),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="provider_status",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="read_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
