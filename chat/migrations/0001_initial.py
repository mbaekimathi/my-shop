import chat.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Conversation",
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
                    "kind",
                    models.CharField(
                        choices=[("direct", "Shop to shop"), ("bot", "Assistant")],
                        db_index=True,
                        max_length=12,
                    ),
                ),
                (
                    "pair_key",
                    models.CharField(
                        blank=True, max_length=32, null=True, unique=True
                    ),
                ),
                (
                    "bot_shop_id",
                    models.BigIntegerField(blank=True, null=True, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="Message",
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
                    "sender_shop_id",
                    models.BigIntegerField(blank=True, db_index=True, null=True),
                ),
                ("is_bot", models.BooleanField(db_index=True, default=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("text", "Text"),
                            ("image", "Image"),
                            ("system", "System"),
                        ],
                        default="text",
                        max_length=12,
                    ),
                ),
                ("body", models.TextField(blank=True, default="")),
                (
                    "attachment",
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to=chat.models.chat_attachment_path,
                    ),
                ),
                (
                    "client_tag",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="chat.conversation",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="Participant",
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
                ("shop_id", models.BigIntegerField(db_index=True)),
                ("last_read_id", models.BigIntegerField(default=0)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("typing_until", models.DateTimeField(blank=True, null=True)),
                ("joined_at", models.DateTimeField(auto_now_add=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="participants",
                        to="chat.conversation",
                    ),
                ),
            ],
            options={"ordering": ["conversation_id", "shop_id"]},
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "id"], name="chat_msg_conv_id_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="participant",
            constraint=models.UniqueConstraint(
                fields=("conversation", "shop_id"), name="uniq_chat_participant"
            ),
        ),
    ]
