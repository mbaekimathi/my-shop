from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from items.models import Item
from items.services import backfill_item_sale_stock


class Command(BaseCommand):
    help = (
        "Reconcile stock for receipt lines that never moved stock — sale / "
        "credit / gift / trade-out lines sold while the item's "
        "deduct_stock_on_sale (or company stock tracking) was off, and "
        "untracked trade-ins. This is the same backfill the Sale stock "
        "tracking toggles run; safe to re-run since lines that already "
        "moved are skipped. Run once after deploying the backfill change."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--item-id",
            type=int,
            default=0,
            help="Reconcile a single item only.",
        )
        parser.add_argument(
            "--since",
            metavar="YYYY-MM-DD",
            default="",
            help=(
                "Only judge receipts created on/after this date. Defaults "
                "to the first POS-mirror movement — receipts older than "
                "that always deducted and are left alone."
            ),
        )

    def handle(self, *args, **options):
        from datetime import datetime

        from django.utils import timezone

        since = None
        raw_since = (options["since"] or "").strip()
        if raw_since:
            try:
                since = timezone.make_aware(
                    datetime.fromisoformat(raw_since)
                )
            except ValueError:
                raise CommandError(
                    "Enter --since as YYYY-MM-DD."
                )

        items = Item.objects.filter(is_suspended=False).order_by("pk")
        item_id = options["item_id"]
        if item_id:
            items = items.filter(pk=item_id)

        totals = {"items": 0, "lines": 0, "units_out": 0, "units_in": 0}
        # Movement rows fall back to each receipt's created_by; lines whose
        # receipts have no staff still move ShopStock/Item.stock but skip the
        # central-store mirror.
        with transaction.atomic():
            for item in items.iterator():
                summary = backfill_item_sale_stock(item, since=since)
                if not summary["lines"]:
                    continue
                totals["items"] += 1
                totals["lines"] += summary["lines"]
                totals["units_out"] += summary["units_out"]
                totals["units_in"] += summary["units_in"]
                self.stdout.write(
                    f"{item.name}: {summary['lines']} line(s) "
                    f"(-{summary['units_out']} / +{summary['units_in']})"
                )
            if options["dry_run"]:
                transaction.set_rollback(True)

        verb = "would reconcile" if options["dry_run"] else "reconciled"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {totals['lines']} line(s) across "
                f"{totals['items']} item(s) "
                f"(-{totals['units_out']} / +{totals['units_in']} units)"
            )
        )
