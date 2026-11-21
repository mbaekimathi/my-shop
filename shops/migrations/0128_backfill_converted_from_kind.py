"""Backfill ShopReceipt.converted_from_kind for receipts converted before
the field existed (e.g. S0417 — a sale changed to credit then settled back
to sale, or sale→gift rows that kept their S number).

Sources of truth, in order:
1. settled_from_credit / settled_from_trade flags (deterministic).
2. The stored number's letter prefix — when it cannot belong to the current
   kind's configured format, it was issued as the kind whose prefix it
   matches (only stamped on an unambiguous single-kind match).
"""

import re

from django.db import migrations

KINDS = ("sale", "credit", "quotation", "trade_out", "trade_in", "gift")
DEFAULTS = {
    "sale": "S",
    "credit": "C",
    "quotation": "Q",
    "trade_out": "T",
    "trade_in": "TI",
    "gift": "GF",
}
FIELDS = {k: f"receipt_format_{k}" for k in KINDS}
LETTER_RUN = re.compile(r"^([A-Za-z]+)")


def backfill(apps, schema_editor):
    ShopReceipt = apps.get_model("shops", "ShopReceipt")
    CompanyPosSettings = apps.get_model("shops", "CompanyPosSettings")
    ShopPosSettings = apps.get_model("shops", "ShopPosSettings")

    company = CompanyPosSettings.objects.order_by("pk").first()
    company_prefixes = {
        k: (
            getattr(company, FIELDS[k], "") or DEFAULTS[k]
        ).strip().upper()
        or DEFAULTS[k]
        for k in KINDS
    }
    shop_prefixes = {}
    for row in ShopPosSettings.objects.all().iterator():
        shop_prefixes[row.shop_id] = {
            k: (
                (getattr(row, FIELDS[k], "") or "").strip().upper()
                or company_prefixes[k]
            )
            for k in KINDS
        }

    def prefixes_for(shop_id):
        return shop_prefixes.get(shop_id, company_prefixes)

    updates = []
    qs = (
        ShopReceipt.objects.filter(converted_from_kind="")
        .exclude(receipt_number="")
        .only(
            "pk",
            "shop_id",
            "receipt_number",
            "kind",
            "converted_from_kind",
            "settled_from_credit",
            "settled_from_trade",
        )
        .iterator(chunk_size=500)
    )
    for receipt in qs:
        source = ""
        if receipt.settled_from_credit:
            source = "credit"
        elif receipt.settled_from_trade:
            source = "trade_out"
        else:
            number = (receipt.receipt_number or "").strip().upper()
            match = LETTER_RUN.match(number)
            if not match:
                continue
            prefix = match.group(1)
            kind = (receipt.kind or "").strip().lower()
            if kind not in KINDS:
                continue
            own = prefixes_for(receipt.shop_id)
            # Still matches the kind it claims to be — nothing to backfill.
            if prefix in (own.get(kind), DEFAULTS[kind]):
                continue
            candidates = {
                k
                for k in KINDS
                if k != kind
                and prefix in (own.get(k), DEFAULTS[k])
            }
            if len(candidates) == 1:
                source = candidates.pop()
        if source and source != receipt.kind:
            receipt.converted_from_kind = source
            updates.append(receipt)
        if len(updates) >= 500:
            ShopReceipt.objects.bulk_update(updates, ["converted_from_kind"])
            updates = []
    if updates:
        ShopReceipt.objects.bulk_update(updates, ["converted_from_kind"])


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0127_shopreceipt_converted_from_kind"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
