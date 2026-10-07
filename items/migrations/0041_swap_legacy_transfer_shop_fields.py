# Swap shop / requested_from_shop on pre send/confirm transfer rows.
#
# Before the Oct 2026 send/confirm change:
#   shop = requesting shop (receiver)
#   requested_from_shop = supplying shop (sender)
# After:
#   shop = sending shop
#   requested_from_shop = receiving shop
#
# Rows created with the new UI (from 2026-10-06 18:42 UTC onward) already use
# the new meaning and must not be swapped.

from datetime import datetime, timezone

from django.db import migrations

# First transfer created under send/confirm semantics (id=2100 locally).
NEW_TRANSFER_SEMANTICS_AT = datetime(2026, 10, 6, 18, 42, 0, tzinfo=timezone.utc)


def swap_legacy_transfer_shops(apps, schema_editor):
    StockMovement = apps.get_model("items", "StockMovement")
    qs = StockMovement.objects.filter(
        movement_type="request",
        created_at__lt=NEW_TRANSFER_SEMANTICS_AT,
    ).exclude(shop_id__isnull=True).exclude(requested_from_shop_id__isnull=True)

    for movement in qs.iterator(chunk_size=200):
        sender_id = movement.requested_from_shop_id
        receiver_id = movement.shop_id
        if sender_id == receiver_id:
            continue
        movement.shop_id = sender_id
        movement.requested_from_shop_id = receiver_id
        movement.save(update_fields=["shop_id", "requested_from_shop_id"])


def unswap_legacy_transfer_shops(apps, schema_editor):
    StockMovement = apps.get_model("items", "StockMovement")
    qs = StockMovement.objects.filter(
        movement_type="request",
        created_at__lt=NEW_TRANSFER_SEMANTICS_AT,
    ).exclude(shop_id__isnull=True).exclude(requested_from_shop_id__isnull=True)

    for movement in qs.iterator(chunk_size=200):
        sender_id = movement.shop_id
        receiver_id = movement.requested_from_shop_id
        if sender_id == receiver_id:
            continue
        movement.shop_id = receiver_id
        movement.requested_from_shop_id = sender_id
        movement.save(update_fields=["shop_id", "requested_from_shop_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0040_alter_stockmovement_transfer_labels"),
    ]

    operations = [
        migrations.RunPython(swap_legacy_transfer_shops, unswap_legacy_transfer_shops),
    ]
