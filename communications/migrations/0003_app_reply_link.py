from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("communications", "0002_inbound_reply"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboundmessage",
            name="wa_chat_id",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=120
            ),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="wa_message_id",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="inboundreply",
            name="outbound_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="communications.outboundmessage",
            ),
        ),
    ]
