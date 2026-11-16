"""
wipe_sales.py — permanently delete ALL sales data from the MY-SHOP database.

Run from the project root (where manage.py lives):

    python wipe_sales.py            # DRY RUN — prints counts, deletes nothing
    python wipe_sales.py --apply    # actually deletes

What it removes
---------------
- shops.ShopReceipt + ShopReceiptLine + ShopDelivery
  (all kinds: sale, credit, quotation, trade out/in)
- shops.MpesaStkPayment rows for sale / credit purposes
  (developer-subscription STKs are kept)
- shops.ClientCreditAccountEvent — the credit ledger only exists
  because of credit sales
- shops.PrintAgentJob — queued receipt print jobs
- pos.Sale / pos.SaleLine — legacy offline-POS sales
- items.StockMovement rows with sale-adjacent entry sources
  (customer_return, trade_out, trade_in, trade_exchange)
- shops.ShopDaySession — till open/close summaries (set
  WIPE_DAY_SESSIONS = False below to keep them)
- Client credit follow-up dates reset to NULL

What it keeps
-------------
Shops, clients, items, stock quantities, suppliers, employees,
expenses, shelf stock, settings — untouched.

Optional
--------
RESTORE_STOCK = True also returns the sold (non-returned) quantities
to items.ShopStock, reverses the stock effect of the deleted
sale-adjacent movements, and marks sold serials available again —
i.e. stock goes back to what it was before the sales happened.
"""

import os
import sys
from decimal import Decimal

# ---------------------------------------------------------------- settings
RESTORE_STOCK = False      # True = put sold quantities back into ShopStock
WIPE_DAY_SESSIONS = True   # False = keep shop day open/close records
# --------------------------------------------------------------------------

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402

from items.models import (  # noqa: E402
    ItemSerial,
    ShopStock,
    StockEntrySource,
    StockMovement,
    StockMovementType,
    StoreStock,
    StoreStockMovement,
)
from pos.models import Sale, SaleLine  # noqa: E402
from shops.models import (  # noqa: E402
    Client,
    ClientCreditAccountEvent,
    MpesaStkPayment,
    MpesaStkPurpose,
    PrintAgentJob,
    ShopDaySession,
    ShopDelivery,
    ShopReceipt,
    ShopReceiptLine,
)

SALE_MOVEMENT_SOURCES = [
    StockEntrySource.CUSTOMER_RETURN,
    StockEntrySource.TRADE_OUT,
    StockEntrySource.TRADE_IN,
    StockEntrySource.TRADE_EXCHANGE,
]


def _restock(shop_id, item_id, qty, unit_cost):
    """Add qty back to ShopStock, keeping the weighted average cost sane."""
    if item_id is None or qty <= 0:
        return
    stock, _ = ShopStock.objects.get_or_create(
        shop_id=shop_id, item_id=item_id, defaults={"quantity": 0, "average_cost": 0}
    )
    old_qty = Decimal(stock.quantity or 0)
    old_cost = Decimal(stock.average_cost or 0)
    add_cost = Decimal(unit_cost or 0)
    new_qty = old_qty + qty
    if new_qty > 0:
        stock.average_cost = ((old_qty * old_cost) + (qty * add_cost)) / new_qty
    stock.quantity = new_qty
    stock.save(update_fields=["quantity", "average_cost", "updated_at"])


def _destock(shop_id, item_id, qty):
    """Remove qty that a deleted stock-in movement had added. Clamps at 0."""
    if item_id is None or qty <= 0:
        return
    try:
        stock = ShopStock.objects.get(shop_id=shop_id, item_id=item_id)
    except ShopStock.DoesNotExist:
        return
    stock.quantity = max(Decimal("0"), Decimal(stock.quantity or 0) - qty)
    stock.save(update_fields=["quantity", "updated_at"])


def _store_restock(item_id, qty):
    """Add qty back to central StoreStock (mirrors _restock for ShopStock)."""
    if item_id is None or qty <= 0:
        return
    stock, _ = StoreStock.objects.get_or_create(
        item_id=item_id, defaults={"quantity": 0}
    )
    stock.quantity = Decimal(stock.quantity or 0) + qty
    stock.save(update_fields=["quantity", "updated_at"])


def _store_destock(item_id, qty):
    """Remove qty a deleted POS stock-in had added to StoreStock."""
    if item_id is None or qty <= 0:
        return
    try:
        stock = StoreStock.objects.get(item_id=item_id)
    except StoreStock.DoesNotExist:
        return
    stock.quantity = Decimal(stock.quantity or 0) - qty
    stock.save(update_fields=["quantity", "updated_at"])


def collect_counts():
    return {
        "ShopReceiptLine": ShopReceiptLine.objects.count(),
        "ShopDelivery": ShopDelivery.objects.count(),
        "ShopReceipt": ShopReceipt.objects.count(),
        "MpesaStkPayment (sale/credit)": MpesaStkPayment.objects.filter(
            purpose__in=[MpesaStkPurpose.SALE, MpesaStkPurpose.CREDIT]
        ).count(),
        "ClientCreditAccountEvent": ClientCreditAccountEvent.objects.count(),
        "PrintAgentJob": PrintAgentJob.objects.count(),
        "pos SaleLine": SaleLine.objects.count(),
        "pos Sale": Sale.objects.count(),
        "StockMovement (sale-adjacent)": StockMovement.objects.filter(
            entry_source__in=SALE_MOVEMENT_SOURCES
        ).count(),
        "StoreStockMovement (POS)": StoreStockMovement.objects.filter(
            source="pos"
        ).count(),
        "ShopDaySession": ShopDaySession.objects.count(),
    }


def wipe(apply: bool):
    counts = collect_counts()
    print("\nRows to delete:")
    for name, n in counts.items():
        print(f"  {name:32} {n}")
    if not WIPE_DAY_SESSIONS:
        print("  (ShopDaySession kept — WIPE_DAY_SESSIONS = False)")
    print(f"\nRESTORE_STOCK = {RESTORE_STOCK}")

    if not apply:
        print("\nDRY RUN — nothing was deleted. Re-run with:  python wipe_sales.py --apply")
        return

    with transaction.atomic():
        # --- optional: put sold stock back --------------------------------
        if RESTORE_STOCK:
            print("Restoring stock from receipt lines…")
            for line in ShopReceiptLine.objects.select_related("receipt").iterator():
                remaining = Decimal(line.quantity or 0) - Decimal(
                    line.returned_quantity or 0
                )
                _restock(
                    line.receipt.shop_id,
                    line.item_id,
                    remaining,
                    line.unit_cost,
                )
                # Mirror the central store ledger: sale-like kinds had
                # deducted StoreStock, trade-ins had added to it.
                if line.receipt.kind == "trade_in":
                    _store_destock(line.item_id, remaining)
                elif line.receipt.kind in ("sale", "credit", "trade_out"):
                    _store_restock(line.item_id, remaining)
                # serials that left with the sale become available again
                returned = {
                    str(s).strip()
                    for s in (line.returned_serial_numbers or [])
                    if str(s).strip()
                }
                sold_serials = [
                    str(s).strip()
                    for s in (line.serial_numbers or [])
                    if str(s).strip() and str(s).strip() not in returned
                ]
                if sold_serials:
                    ItemSerial.objects.filter(
                        item_id=line.item_id, serial_number__in=sold_serials
                    ).update(is_available=True)

            print("Reversing sale-adjacent stock movements…")
            movements = StockMovement.objects.filter(
                entry_source__in=SALE_MOVEMENT_SOURCES
            ).prefetch_related("lines")
            for mv in movements.iterator():
                for ml in mv.lines.all():
                    qty = Decimal(ml.quantity or 0)
                    if mv.movement_type == StockMovementType.IN:
                        if mv.entry_source == StockEntrySource.CUSTOMER_RETURN:
                            # already netted via remaining_quantity restore
                            continue
                        _destock(mv.shop_id, ml.item_id, qty)
                        if mv.entry_source == StockEntrySource.TRADE_EXCHANGE:
                            # Exchange stock-ins also added to StoreStock.
                            _store_destock(ml.item_id, qty)
                        serials = [s for s in (ml.serial_numbers or []) if str(s).strip()]
                        if serials:
                            ItemSerial.objects.filter(
                                item_id=ml.item_id, serial_number__in=serials
                            ).update(is_available=False)
                    elif mv.movement_type == StockMovementType.OUT:
                        _restock(mv.shop_id, ml.item_id, qty, ml.unit_cost)
                        serials = [s for s in (ml.serial_numbers or []) if str(s).strip()]
                        if serials:
                            ItemSerial.objects.filter(
                                item_id=ml.item_id, serial_number__in=serials
                            ).update(is_available=True)

        # --- delete sale-adjacent records --------------------------------
        print("Deleting credit-account events…")
        ClientCreditAccountEvent.objects.all().delete()

        print("Deleting M-Pesa STK payments (sale/credit)…")
        MpesaStkPayment.objects.filter(
            purpose__in=[MpesaStkPurpose.SALE, MpesaStkPurpose.CREDIT]
        ).delete()

        print("Deleting queued print jobs…")
        PrintAgentJob.objects.all().delete()

        print("Deleting deliveries…")
        ShopDelivery.objects.all().delete()

        print("Deleting receipt lines…")
        ShopReceiptLine.objects.all().delete()

        print("Deleting receipts…")
        ShopReceipt.objects.all().delete()

        print("Deleting legacy POS sales…")
        SaleLine.objects.all().delete()
        Sale.objects.all().delete()

        print("Deleting sale-adjacent stock movements…")
        StockMovement.objects.filter(entry_source__in=SALE_MOVEMENT_SOURCES).delete()

        print("Deleting POS store-stock movements…")
        StoreStockMovement.objects.filter(source="pos").delete()

        if WIPE_DAY_SESSIONS:
            print("Deleting shop day sessions…")
            ShopDaySession.objects.all().delete()

        print("Resetting client credit follow-ups…")
        Client.objects.filter(
            credit_follow_up_date__isnull=False
        ).update(credit_follow_up_date=None, credit_follow_up_reminder_sent_on=None)
        Client.objects.filter(
            credit_follow_up_reminder_sent_on__isnull=False
        ).update(credit_follow_up_reminder_sent_on=None)

    print("\nDone — all sales data deleted.")


if __name__ == "__main__":
    wipe(apply="--apply" in sys.argv)
