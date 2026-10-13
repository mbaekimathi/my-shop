# Swap created_by / responded_by on pre send/confirm transfer rows.
#
# Migration 0041 swapped shop ↔ requested_from_shop for legacy request rows so
# shop=sender and requested_from_shop=receiver. People were left as:
#   created_by = original requester (receiving shop staff)
#   responded_by = original supplier responder (sending shop staff)
# Under send/confirm display (From/Sent vs To/Received) those people are crossed
# relative to the shops. Swap them for the same legacy cutoff.

from datetime import datetime, timezone

from django.db import migrations

# Keep in sync with 0041_swap_legacy_transfer_shop_fields.
NEW_TRANSFER_SEMANTICS_AT = datetime(2026, 10, 6, 18, 42, 0, tzinfo=timezone.utc)


def swap_legacy_transfer_people(apps, schema_editor):
    StockMovement = apps.get_model("items", "StockMovement")
    qs = StockMovement.objects.filter(
        movement_type="request",
        created_at__lt=NEW_TRANSFER_SEMANTICS_AT,
    )

    for movement in qs.iterator(chunk_size=200):
        sender_person_id = movement.responded_by_id
        receiver_person_id = movement.created_by_id
        if sender_person_id == receiver_person_id:
            continue
        if sender_person_id is None and receiver_person_id is None:
            continue
        movement.created_by_id = sender_person_id
        movement.responded_by_id = receiver_person_id
        movement.save(update_fields=["created_by_id", "responded_by_id"])


def unswap_legacy_transfer_people(apps, schema_editor):
    StockMovement = apps.get_model("items", "StockMovement")
    qs = StockMovement.objects.filter(
        movement_type="request",
        created_at__lt=NEW_TRANSFER_SEMANTICS_AT,
    )

    for movement in qs.iterator(chunk_size=200):
        requester_id = movement.responded_by_id
        supplier_id = movement.created_by_id
        if requester_id == supplier_id:
            continue
        if requester_id is None and supplier_id is None:
            continue
        movement.created_by_id = requester_id
        movement.responded_by_id = supplier_id
        movement.save(update_fields=["created_by_id", "responded_by_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("items", "0042_itemserial_status_reason"),
    ]

    operations = [
        migrations.RunPython(
            swap_legacy_transfer_people, unswap_legacy_transfer_people
        ),
    ]
