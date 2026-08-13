from django.core.management.base import BaseCommand

from items.services import recalc_shop_stock_average_costs


class Command(BaseCommand):
    help = (
        "Rebuild shop stock average_cost from non-zero unit buying prices. "
        "Unpriced stock-ins are ignored; invoice totals above max sell are skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int, default=None)
        parser.add_argument(
            "--item-id",
            type=int,
            action="append",
            dest="item_ids",
            help="Limit to one or more item ids (repeatable).",
        )

    def handle(self, *args, **options):
        result = recalc_shop_stock_average_costs(
            shop_id=options.get("shop_id"),
            item_ids=options.get("item_ids"),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"average_cost recosted: updated={result['updated']} "
                f"unchanged={result['unchanged']}"
            )
        )
