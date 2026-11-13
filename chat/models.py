"""Chat tables owned by MY-CHATBOT inside the shared `myshop` database.

Shop identity is stored as a plain bigint (`shop_id`) that references
`shops_shop.id` from the MY-SHOP project. We deliberately avoid a real FK so
these tables stay independent of the unmanaged MY-SHOP schema.
"""

from django.db import models


def chat_attachment_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"chat/{instance.conversation_id}/{instance.pk or 'new'}.{ext}"


class ConversationKind(models.TextChoices):
    DIRECT = "direct", "Shop to shop"
    BOT = "bot", "Assistant"
    GROUP = "group", "Group"


class Conversation(models.Model):
    """A direct thread between two shops, a group, or an assistant thread."""

    kind = models.CharField(
        max_length=12, choices=ConversationKind.choices, db_index=True
    )
    # Direct threads: "<low_shop_id>:<high_shop_id>" — unique per shop pair.
    # Shared activity feeds use "activity:<name>" — unique per action type.
    pair_key = models.CharField(
        max_length=32, unique=True, null=True, blank=True
    )
    # Assistant threads: one per shop.
    bot_shop_id = models.BigIntegerField(unique=True, null=True, blank=True)
    # Group threads: the title the creator gave it.
    name = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        if self.kind == ConversationKind.BOT:
            return f"Assistant thread (shop {self.bot_shop_id})"
        if self.kind == ConversationKind.GROUP:
            return f"Group {self.name!r}"
        return f"Direct thread {self.pair_key}"


class Participant(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="participants"
    )
    shop_id = models.BigIntegerField(db_index=True)
    last_read_id = models.BigIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    typing_until = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["conversation_id", "shop_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "shop_id"],
                name="uniq_chat_participant",
            )
        ]

    def __str__(self):
        return f"shop {self.shop_id} in {self.conversation_id}"


class MessageKind(models.TextChoices):
    TEXT = "text", "Text"
    IMAGE = "image", "Image"
    SYSTEM = "system", "System"


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    # NULL sender = assistant/system message.
    sender_shop_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    is_bot = models.BooleanField(default=False, db_index=True)
    kind = models.CharField(
        max_length=12, choices=MessageKind.choices, default=MessageKind.TEXT
    )
    body = models.TextField(blank=True, default="")
    attachment = models.FileField(
        upload_to=chat_attachment_path, blank=True, null=True
    )
    # Idempotency tag set by the sender's browser so retries cannot double-post.
    client_tag = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Structured payload for rich bubbles (e.g. stock transfer cards).
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(
                fields=["conversation", "id"], name="chat_msg_conv_id_idx"
            ),
        ]

    def __str__(self):
        preview = (self.body or self.kind)[:40]
        return f"#{self.pk} {preview}"
