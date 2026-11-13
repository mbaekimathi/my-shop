"""Conversation helpers + JSON serializers for the chat API."""

import mimetypes
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.db.models import Max
from django.utils import timezone

from .models import Conversation, ConversationKind, Message, MessageKind, Participant
from .myshop_models import CompanyProfile, Shop

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
PRESENCE_WRITE_INTERVAL = 10  # seconds between last_seen_at writes
ONLINE_WINDOW = 25  # seconds since last_seen_at counts as "online"
TYPING_TTL = 4  # seconds a typing flag stays alive

BOT_SUBTITLE = "Business data helper"


def company_name() -> str:
    """Company name from MY-SHOP's settings (`shops_companyprofile`)."""
    try:
        row = CompanyProfile.objects.first()
        if row and row.name.strip():
            return " ".join(row.name.split())
    except Exception:
        pass
    return "MY-SHOP"


def app_name() -> str:
    return f"{company_name()} Chat"


def bot_name() -> str:
    return f"{company_name()} Assistant"


def bot_welcome(shop_name: str) -> str:
    return (
        f"Hi {shop_name}! I'm your {company_name()} assistant.\n\n"
        "Ask me about your business data, for example:\n"
        "- *sales today* or *sales this week*\n"
        "- *stock sugar* - on-hand quantity and price\n"
        "- *low stock* - items running out\n"
        "- *top sellers* - best movers this month\n"
        "- *credit* - outstanding customer balances\n"
        "- *expenses today*\n"
        "- *receipt 00123*\n\n"
        "Type *help* any time to see the full menu."
    )

# ---------------------------------------------------------------------------
# Shop directory
# ---------------------------------------------------------------------------


def active_shops():
    return Shop.objects.filter(is_suspended=False, is_hidden=False).order_by("name")


def shop_by_id(shop_id) -> Shop | None:
    try:
        return Shop.objects.get(pk=shop_id, is_suspended=False, is_hidden=False)
    except (Shop.DoesNotExist, ValueError, TypeError):
        return None


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def avatar_tone(seed) -> int:
    try:
        return int(seed) % 6 + 1
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Conversation lifecycle
# ---------------------------------------------------------------------------


def pair_key_for(a: int, b: int) -> str:
    lo, hi = sorted((int(a), int(b)))
    return f"{lo}:{hi}"


def get_or_create_direct(me: Shop, other: Shop) -> Conversation:
    key = pair_key_for(me.pk, other.pk)
    convo = (
        Conversation.objects.filter(kind=ConversationKind.DIRECT, pair_key=key)
        .prefetch_related("participants")
        .first()
    )
    if convo is not None:
        return convo
    try:
        convo = Conversation.objects.create(
            kind=ConversationKind.DIRECT, pair_key=key
        )
    except IntegrityError:
        return Conversation.objects.get(
            kind=ConversationKind.DIRECT, pair_key=key
        )
    Participant.objects.bulk_create(
        [
            Participant(conversation=convo, shop_id=me.pk),
            Participant(conversation=convo, shop_id=other.pk),
        ]
    )
    return convo


def get_or_create_bot(shop: Shop) -> Conversation:
    convo = Conversation.objects.filter(
        kind=ConversationKind.BOT, bot_shop_id=shop.pk
    ).first()
    if convo is not None:
        return convo
    try:
        convo = Conversation.objects.create(
            kind=ConversationKind.BOT, bot_shop_id=shop.pk
        )
    except IntegrityError:
        return Conversation.objects.get(
            kind=ConversationKind.BOT, bot_shop_id=shop.pk
        )
    Participant.objects.create(conversation=convo, shop_id=shop.pk)
    Message.objects.create(
        conversation=convo,
        sender_shop_id=None,
        is_bot=True,
        kind=MessageKind.TEXT,
        body=bot_welcome(shop.name),
    )
    return convo


def create_group(me: Shop, name: str, members: list) -> Conversation:
    """A multi-shop thread — membership is a plain participant list."""
    convo = Conversation.objects.create(
        kind=ConversationKind.GROUP, name=name
    )
    seen = {me.pk}
    parts = [Participant(conversation=convo, shop_id=me.pk)]
    for shop in members:
        if shop.pk in seen:
            continue
        seen.add(shop.pk)
        parts.append(Participant(conversation=convo, shop_id=shop.pk))
    Participant.objects.bulk_create(parts)
    Message.objects.create(
        conversation=convo,
        sender_shop_id=None,
        kind=MessageKind.SYSTEM,
        body=f"{me.name} created group “{name}”",
    )
    return convo


def rename_group(convo: Conversation, me: Shop, name: str) -> Conversation:
    """Rename a group thread and post a system notice."""
    old = (convo.name or "Group").strip() or "Group"
    convo.name = name
    convo.save(update_fields=["name"])
    if old != name:
        Message.objects.create(
            conversation=convo,
            sender_shop_id=None,
            kind=MessageKind.SYSTEM,
            body=f"{me.name} renamed the group to “{name}”",
        )
    return convo


def delete_group(convo: Conversation) -> None:
    """Remove a group (and its messages/participants) for every shop."""
    convo.delete()


# Shared activity feeds — one auto-managed group per stock action. Every
# active shop is a participant so the whole business can track the day's
# stock-in, transfers, trades and expenses; messages name the shop and the
# staff member who ran the action.
ACTIVITY_GROUPS = {
    "stockin": "Incoming stock",
    "transfer": "Transfers",
    "trade": "Trades",
    "expense": "Expenses",
}
ACTIVITY_KEY_PREFIX = "activity:"


def is_activity_group(convo: Conversation) -> bool:
    """Shared stock-action feeds — still renameable/deletable by members."""
    key = convo.pair_key or ""
    return key.startswith(ACTIVITY_KEY_PREFIX)


def activity_conversation(activity: str) -> Conversation | None:
    """Get-or-create the shared feed group for one action type.

    Membership self-heals: shops added after the group was created join on
    the next activity post. The `activity:<name>` pair_key keeps the group
    unique and distinct from user-created groups (which have no pair_key).
    """
    name = ACTIVITY_GROUPS.get(activity)
    if not name:
        return None
    key = ACTIVITY_KEY_PREFIX + activity
    convo = (
        Conversation.objects.filter(
            kind=ConversationKind.GROUP, pair_key=key
        )
        .prefetch_related("participants")
        .first()
    )
    if convo is None:
        try:
            convo = Conversation.objects.create(
                kind=ConversationKind.GROUP, pair_key=key, name=name
            )
        except IntegrityError:
            convo = Conversation.objects.get(
                kind=ConversationKind.GROUP, pair_key=key
            )
    have = {p.shop_id for p in convo.participants.all()}
    missing = [
        Participant(conversation=convo, shop_id=s.pk)
        for s in active_shops()
        if s.pk not in have
    ]
    if missing:
        Participant.objects.bulk_create(missing, ignore_conflicts=True)
    return convo


def my_conversations(shop: Shop):
    return (
        Conversation.objects.filter(participants__shop_id=shop.pk)
        .annotate(last_id=Max("messages__id"))
        .order_by("-last_id")
        .prefetch_related("participants")
    )


def get_member_conversation(convo_id, shop: Shop) -> Conversation | None:
    try:
        return (
            Conversation.objects.prefetch_related("participants").get(
                pk=convo_id, participants__shop_id=shop.pk
            )
        )
    except (Conversation.DoesNotExist, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Presence / read state / typing
# ---------------------------------------------------------------------------


def touch_presence(participant: Participant) -> None:
    now = timezone.now()
    if (
        participant.last_seen_at is None
        or (now - participant.last_seen_at).total_seconds() > PRESENCE_WRITE_INTERVAL
    ):
        Participant.objects.filter(pk=participant.pk).update(last_seen_at=now)
        participant.last_seen_at = now


def set_typing(participant: Participant) -> None:
    Participant.objects.filter(pk=participant.pk).update(
        typing_until=timezone.now() + timedelta(seconds=TYPING_TTL)
    )


def mark_read(participant: Participant, up_to_id: int) -> None:
    if up_to_id and up_to_id > participant.last_read_id:
        Participant.objects.filter(pk=participant.pk).update(
            last_read_id=up_to_id
        )
        participant.last_read_id = up_to_id


def is_online(participant: Participant | None) -> bool:
    if participant is None or participant.last_seen_at is None:
        return False
    return (
        timezone.now() - participant.last_seen_at
    ).total_seconds() <= ONLINE_WINDOW


def is_typing(participant: Participant | None) -> bool:
    if participant is None or participant.typing_until is None:
        return False
    return participant.typing_until > timezone.now()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"[^A-Za-z0-9_-]")


def post_text(convo: Conversation, sender: Shop, body: str, client_tag: str = ""):
    body = (body or "").strip()
    if not body:
        return None, "Type a message first."
    max_len = getattr(settings, "CHAT_MESSAGE_MAX_LENGTH", 4000)
    if len(body) > max_len:
        return None, f"Messages are limited to {max_len} characters."
    tag = _TAG_RE.sub("", client_tag or "")[:64]
    if tag:
        existing = convo.messages.filter(
            sender_shop_id=sender.pk, client_tag=tag
        ).first()
        if existing is not None:
            return existing, None
    return (
        convo.messages.create(
            sender_shop_id=sender.pk,
            kind=MessageKind.TEXT,
            body=body,
            client_tag=tag,
        ),
        None,
    )


def post_image(convo: Conversation, sender: Shop, upload):
    if upload is None:
        return None, "Choose an image to send."
    content_type = (getattr(upload, "content_type", "") or "").lower()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        guessed = mimetypes.guess_type(getattr(upload, "name", "") or "")[0]
        if guessed not in ALLOWED_IMAGE_TYPES:
            return None, "Only JPG, PNG, WebP or GIF images can be sent."
    max_bytes = getattr(settings, "CHAT_ATTACHMENT_MAX_BYTES", 4 * 1024 * 1024)
    if upload.size > max_bytes:
        return None, "Images are limited to 4 MB."
    ext = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else "jpg"
    if ext not in ALLOWED_IMAGE_EXTS:
        guessed_ext = mimetypes.guess_extension(content_type or "")
        ext = (guessed_ext or ".jpg").lstrip(".")
    data = upload.read()
    msg = convo.messages.create(
        sender_shop_id=sender.pk,
        kind=MessageKind.IMAGE,
        body="",
    )
    msg.attachment.save(
        f"{convo.pk}-{msg.pk}-{uuid.uuid4().hex[:10]}.{ext}",
        ContentFile(data),
        save=True,
    )
    return msg, None


def post_bot_reply(convo: Conversation, body: str, meta: dict | None = None) -> Message:
    return convo.messages.create(
        sender_shop_id=None,
        is_bot=True,
        kind=MessageKind.TEXT,
        body=body,
        meta=meta or {},
    )


def post_card(convo: Conversation, sender: Shop, body: str, meta: dict) -> Message:
    """A rich message (e.g. stock transfer card) sent by a real shop."""
    return convo.messages.create(
        sender_shop_id=sender.pk,
        kind=MessageKind.TEXT,
        body=body,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _time_iso(dt) -> str:
    return dt.isoformat() if dt else ""


def serialize_message(
    msg: Message,
    me: Shop,
    peer: Participant | None = None,
    bot_thread: bool = False,
    peers: list | None = None,
    sender_names: dict | None = None,
) -> dict:
    mine = msg.sender_shop_id == me.pk
    if msg.is_bot or not mine or bot_thread:
        status = "read"
    elif peers is not None:
        status = _group_message_status(msg, peers)
    else:
        status = _message_status(msg, peer)
    meta = msg.meta or {}
    body = msg.body
    transfer = meta.get("transfer")
    if (
        transfer
        and transfer.get("status") != "fulfilled"
        and transfer.get("to_shop_id") == me.pk
    ):
        # Blind receiving: the receiver counts what actually arrived, so the
        # sent quantities and serial values stay hidden until they accept
        # and the stock is moved in.
        body = "Incoming stock transfer from " + (
            transfer.get("from_shop") or "a shop"
        )
        meta = dict(meta)
        transfer = dict(transfer)
        transfer["blind"] = True
        transfer["items"] = [
            {
                **item,
                "qty": "",
                "serials": [],
                "serial_count": 0,
                "expect_serials": bool(item.get("serial_count")),
            }
            for item in (transfer.get("items") or [])
        ]
        meta["transfer"] = transfer

    pending = meta.get("pending_action")
    if isinstance(pending, dict) and "payload" in pending:
        # The stored request payload is server-side — only the confirm
        # endpoint reads it; the client just needs the display fields.
        meta = dict(meta)
        pending = dict(pending)
        pending.pop("payload", None)
        meta["pending_action"] = pending

    data = {
        "id": msg.pk,
        "kind": msg.kind,
        "mine": mine,
        "bot": bool(msg.is_bot),
        "body": body,
        "meta": meta,
        "created_at": _time_iso(msg.created_at),
        "status": status,
    }
    if sender_names is not None and msg.sender_shop_id:
        # Group threads label every bubble with its sender's shop.
        data["sender_id"] = msg.sender_shop_id
        data["sender_name"] = sender_names.get(msg.sender_shop_id, "")
    if msg.kind == MessageKind.IMAGE and msg.attachment:
        from django.urls import reverse

        data["image_url"] = reverse(
            "chat:attachment", kwargs={"message_id": msg.pk}
        )
    return data


def _message_status(msg: Message, peer: Participant | None) -> str:
    if peer is not None and peer.last_read_id >= msg.pk:
        return "read"
    if (
        peer is not None
        and peer.last_seen_at is not None
        and peer.last_seen_at >= msg.created_at
    ):
        return "delivered"
    return "sent"


def _group_message_status(msg: Message, peers: list) -> str:
    """Group ticks mirror WhatsApp: read/delivered only once everyone is."""
    if not peers:
        return "sent"
    if all(p.last_read_id >= msg.pk for p in peers):
        return "read"
    if all(
        p.last_seen_at is not None and p.last_seen_at >= msg.created_at
        for p in peers
    ):
        return "delivered"
    return "sent"


# Meta keys that mark a message as a rich card — mirrored by cardHtml() in
# chatbot.js. The first key present becomes last_message.card so the client
# can pick the right notification sound without fetching the thread.
CARD_META_KEYS = (
    "transfer",
    "stock_in",
    "sale",
    "pending_action",
    "expense",
    "movement",
    "items_sold",
    "sales",
    "summary",
    "credits",
    "expenses",
)


def _card_flag(meta: dict | None) -> str:
    for key in CARD_META_KEYS:
        if key in (meta or {}):
            return key
    return ""


def _peer_of(convo: Conversation, me: Shop) -> Participant | None:
    for p in convo.participants.all():
        if p.shop_id != me.pk:
            return p
    return None


def _me_part(convo: Conversation, me: Shop) -> Participant | None:
    for p in convo.participants.all():
        if p.shop_id == me.pk:
            return p
    return None


def _group_others(convo: Conversation, me: Shop) -> list:
    return [p for p in convo.participants.all() if p.shop_id != me.pk]


def _group_subtitle(convo: Conversation, me: Shop, shops_map: dict) -> str:
    names = [
        shops_map[p.shop_id].name
        for p in _group_others(convo, me)
        if p.shop_id in shops_map
    ]
    if names:
        return ", ".join(names)[:80]
    total = convo.participants.count()
    return f"{total} member{'s' if total != 1 else ''}"


def serialize_conversation(convo: Conversation, me: Shop, shops_map: dict) -> dict:
    peer = _peer_of(convo, me)
    me_part = _me_part(convo, me)
    last = convo.messages.only(
        "id", "sender_shop_id", "is_bot", "kind", "body", "meta", "created_at"
    ).last()
    last_read = me_part.last_read_id if me_part else 0
    unread = (
        convo.messages.filter(id__gt=last_read)
        .exclude(sender_shop_id=me.pk)
        .count()
    )
    group = convo.kind == ConversationKind.GROUP
    others = _group_others(convo, me) if group else []

    if convo.kind == ConversationKind.BOT:
        title, subtitle, tone, bot = bot_name(), BOT_SUBTITLE, 2, True
        online = True
        last_seen = ""
        typing = False
    elif group:
        bot = False
        title = convo.name or "Group"
        subtitle = _group_subtitle(convo, me, shops_map)
        tone = avatar_tone(convo.pk)
        online = any(is_online(p) for p in others)
        last_seen = ""
        typing = any(is_typing(p) for p in others)
    else:
        bot = False
        peer_shop = shops_map.get(peer.shop_id) if peer else None
        title = peer_shop.name if peer_shop else "Shop"
        subtitle = peer_shop.location if peer_shop else ""
        tone = avatar_tone(peer.shop_id if peer else convo.pk)
        online = is_online(peer)
        last_seen = (
            _time_iso(timezone.localtime(peer.last_seen_at))
            if peer and peer.last_seen_at
            else ""
        )
        typing = is_typing(peer)

    last_data = None
    if last is not None:
        preview = last.body or ("Photo" if last.kind == MessageKind.IMAGE else "")
        lt = (last.meta or {}).get("transfer") or {}
        if (
            lt.get("to_shop_id") == me.pk
            and lt.get("status") != "fulfilled"
        ):
            # Keep the sent quantities out of the receiver's chat preview —
            # they count what arrived before the figures are revealed.
            preview = "Incoming stock transfer from " + (
                lt.get("from_shop") or "a shop"
            )
        if (
            group
            and last.sender_shop_id
            and last.sender_shop_id != me.pk
            and last.kind != MessageKind.SYSTEM
        ):
            # WhatsApp-style preview: prefix who spoke in the group.
            sender = shops_map.get(last.sender_shop_id)
            if sender is not None:
                preview = f"{sender.name}: {preview}"
        last_data = {
            "preview": preview[:90],
            "mine": last.sender_shop_id == me.pk,
            "bot": bool(last.is_bot),
            "kind": last.kind,
            "card": _card_flag(last.meta),
            "created_at": _time_iso(last.created_at),
            "status": (
                "read"
                if convo.kind == ConversationKind.BOT
                else _group_message_status(last, others)
                if group
                else _message_status(last, peer)
            )
            if last.sender_shop_id == me.pk
            else "",
        }

    return {
        "id": convo.pk,
        "kind": convo.kind,
        "title": title,
        "subtitle": subtitle,
        "initials": "*" if bot else initials(title),
        "tone": tone,
        "bot": bot,
        "peer_shop_id": peer.shop_id if peer and not group else None,
        "online": online,
        "last_seen": last_seen,
        "typing": typing,
        "unread": unread,
        "last_message": last_data,
        "last_id": last.pk if last else 0,
        # Members can rename/delete any group, including activity feeds.
        "can_manage": group,
        "activity": group and is_activity_group(convo),
    }


def serialize_group_peer(
    convo: Conversation, me: Shop, shops_map: dict
) -> dict:
    """The `peer` block of an open group thread — aggregates every member."""
    others = _group_others(convo, me)
    typer = next((p for p in others if is_typing(p)), None)
    typer_shop = shops_map.get(typer.shop_id) if typer else None
    return {
        "online": any(is_online(p) for p in others),
        "typing": typer is not None,
        "typing_name": typer_shop.name if typer_shop else "",
        "last_seen": "",
        "name": convo.name or "Group",
        "location": _group_subtitle(convo, me, shops_map),
        # My own ticks go blue only once every member has read the message.
        "read_up_to": min((p.last_read_id for p in others), default=0),
    }


def serialize_peer(peer: Participant | None, peer_shop: Shop | None) -> dict:
    return {
        "online": is_online(peer),
        "typing": is_typing(peer),
        "last_seen": (
            _time_iso(timezone.localtime(peer.last_seen_at))
            if peer and peer.last_seen_at
            else ""
        ),
        "name": peer_shop.name if peer_shop else "",
        "location": peer_shop.location if peer_shop else "",
        "read_up_to": peer.last_read_id if peer else 0,
    }


def shops_map_for(convos) -> dict:
    ids = set()
    for convo in convos:
        for p in convo.participants.all():
            ids.add(p.shop_id)
    if not ids:
        return {}
    return {s.pk: s for s in Shop.objects.filter(pk__in=ids)}