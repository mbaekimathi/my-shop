from django.contrib import admin

from .models import Conversation, Message, Participant


class ParticipantInline(admin.TabularInline):
    model = Participant
    extra = 0
    readonly_fields = ("joined_at",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id", "kind", "pair_key", "bot_shop_id", "name", "created_at"
    )
    list_filter = ("kind",)
    inlines = [ParticipantInline]
    readonly_fields = ("created_at",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conversation",
        "sender_shop_id",
        "is_bot",
        "kind",
        "created_at",
    )
    list_filter = ("kind", "is_bot")
    search_fields = ("body",)
    readonly_fields = ("created_at",)
