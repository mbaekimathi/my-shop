# Backfill confirmed_payment_at on sales converted from credits so their
# revenue is attributed to the day they were paid (settle day).

from django.db import migrations
from django.db.models import DateTimeField, F, OuterRef, Subquery
from django.db.models.functions import Coalesce


PAYMENT_KINDS = ("payment_cash", "payment_mpesa", "payment_card")


def backfill_confirmed_payment_at(apps, schema_editor):
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    ClientCreditAccountEvent = apps.get_model(
        "shops", "ClientCreditAccountEvent"
    )
    last_payment = Subquery(
        ClientCreditAccountEvent.objects.filter(
            receipt_id=OuterRef("pk"), kind__in=PAYMENT_KINDS
        )
        .order_by("-occurred_at", "-pk")
        .values("occurred_at")[:1],
        output_field=DateTimeField(),
    )
    ShopReceipt.objects.filter(
        settled_from_credit=True,
        confirmed_payment_at__isnull=True,
    ).update(
        confirmed_payment_at=Coalesce(last_payment, F("created_at"))
    )


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0124_companypossettings_cashier_default_prefs"),
    ]

    operations = [
        migrations.RunPython(
            backfill_confirmed_payment_at,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
