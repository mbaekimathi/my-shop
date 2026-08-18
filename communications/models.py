import uuid

from django.db import models

from .constants import (
    BRIDGE_STATUS_CHOICES,
    BRIDGE_STATUS_DISCONNECTED,
    CAMPAIGN_STATUS_CHOICES,
    CAMPAIGN_DRAFT,
    MSG_STATUS_CHOICES,
    MSG_PENDING,
)


def campaign_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"communications/campaigns/{instance.pk or uuid.uuid4().hex}.{ext}"


class WhatsAppBridgeState(models.Model):
    """Singleton mirror of the Node whatsapp-web.js bridge connection."""

    status = models.CharField(
        max_length=20,
        choices=BRIDGE_STATUS_CHOICES,
        default=BRIDGE_STATUS_DISCONNECTED,
        db_index=True,
    )
    qr_data_url = models.TextField(blank=True, default="")
    wa_phone = models.CharField(max_length=40, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "WhatsApp bridge state"
        verbose_name_plural = "WhatsApp bridge state"

    def __str__(self):
        return f"WhatsApp bridge ({self.status})"

    @classmethod
    def get_solo(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class BroadcastCampaign(models.Model):
    """One manual segmented send job."""

    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="broadcast_campaigns",
    )
    body_template = models.TextField()
    image = models.FileField(upload_to=campaign_image_path, blank=True, null=True)
    filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=CAMPAIGN_STATUS_CHOICES,
        default=CAMPAIGN_DRAFT,
        db_index=True,
    )
    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Campaign #{self.pk} ({self.status})"


class OutboundMessage(models.Model):
    """Per-recipient send log for a campaign."""

    campaign = models.ForeignKey(
        BroadcastCampaign,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    client = models.ForeignKey(
        "shops.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_messages",
    )
    client_name = models.CharField(max_length=200, blank=True, default="")
    phone = models.CharField(max_length=40, db_index=True)
    body = models.TextField()
    image_path = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=MSG_STATUS_CHOICES,
        default=MSG_PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    wa_chat_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    wa_message_id = models.CharField(max_length=200, blank=True, default="", db_index=True)
    provider_status = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["campaign", "status"]),
        ]

    def __str__(self):
        return f"{self.phone} ({self.status})"


class InboundReply(models.Model):
    """A client reply captured from the personal WhatsApp bridge."""

    wa_message_id = models.CharField(max_length=200, unique=True, db_index=True)
    chat_id = models.CharField(max_length=120, blank=True, default="")
    phone = models.CharField(max_length=40, db_index=True)
    sender_name = models.CharField(max_length=200, blank=True, default="")
    body = models.TextField(blank=True, default="")
    client = models.ForeignKey(
        "shops.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inbound_replies",
    )
    outbound_message = models.ForeignKey(
        OutboundMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )
    created_at = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["phone", "-created_at"],
                name="communicati_phone_4f0c8a_idx",
            ),
            models.Index(
                fields=["read_at", "-created_at"],
                name="communicati_read_at_7d2b1e_idx",
            ),
        ]

    def __str__(self):
        return f"Reply from {self.phone}"


WHATSAPP_GROUP_CREATED = "created"
WHATSAPP_GROUP_JOINED = "joined"
WHATSAPP_GROUP_SOURCE_CHOICES = (
    (WHATSAPP_GROUP_CREATED, "Created"),
    (WHATSAPP_GROUP_JOINED, "Joined"),
)


class WhatsAppGroup(models.Model):
    """A WhatsApp group saved in MY-SHOP, with optional chat.whatsapp.com invite."""

    name = models.CharField(max_length=200)
    invite_link = models.CharField(max_length=500, blank=True, default="")
    source = models.CharField(
        max_length=20,
        choices=WHATSAPP_GROUP_SOURCE_CHOICES,
        default=WHATSAPP_GROUP_CREATED,
        db_index=True,
    )
    members = models.ManyToManyField(
        "shops.Client",
        blank=True,
        related_name="whatsapp_groups",
    )
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_groups_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name
