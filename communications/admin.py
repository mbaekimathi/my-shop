from django.contrib import admin

from .models import BroadcastCampaign, OutboundMessage, WhatsAppBridgeState, WhatsAppGroup


@admin.register(WhatsAppBridgeState)
class WhatsAppBridgeStateAdmin(admin.ModelAdmin):
    list_display = ("pk", "status", "wa_phone", "updated_at")
    readonly_fields = ("updated_at",)


class OutboundMessageInline(admin.TabularInline):
    model = OutboundMessage
    extra = 0
    readonly_fields = (
        "client",
        "client_name",
        "phone",
        "status",
        "provider_status",
        "attempt_count",
        "sent_at",
        "delivered_at",
        "read_at",
        "error",
    )
    can_delete = False


@admin.register(BroadcastCampaign)
class BroadcastCampaignAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "recipient_count",
        "sent_count",
        "failed_count",
        "created_by",
        "created_at",
    )
    list_filter = ("status",)
    inlines = [OutboundMessageInline]


@admin.register(WhatsAppGroup)
class WhatsAppGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "source", "invite_link", "created_at")
    list_filter = ("source",)
    search_fields = ("name", "invite_link")
    filter_horizontal = ("members",)


@admin.register(OutboundMessage)
class OutboundMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "campaign",
        "client_name",
        "phone",
        "status",
        "attempt_count",
        "sent_at",
    )
    list_filter = ("status",)
    search_fields = ("phone", "client_name")
