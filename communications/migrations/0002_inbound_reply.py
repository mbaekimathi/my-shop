from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0025_company_communications_settings"),
        ("communications", "0001_initial_whatsapp_broadcast"),
    ]

    operations = [
        migrations.CreateModel(
            name="InboundReply",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "wa_message_id",
                    models.CharField(db_index=True, max_length=200, unique=True),
                ),
                ("chat_id", models.CharField(blank=True, default="", max_length=120)),
                ("phone", models.CharField(db_index=True, max_length=40)),
                (
                    "sender_name",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                ("body", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(db_index=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "read_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "client",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inbound_replies",
                        to="shops.client",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["phone", "-created_at"],
                        name="communicati_phone_4f0c8a_idx",
                    ),
                    models.Index(
                        fields=["read_at", "-created_at"],
                        name="communicati_read_at_7d2b1e_idx",
                    ),
                ],
            },
        ),
    ]
