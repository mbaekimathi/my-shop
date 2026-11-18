from django.core.management.base import BaseCommand

from items.stock_take import close_overdue_stock_takes


class Command(BaseCommand):
    help = (
        "Close open stock takes whose deadline has passed. Pages and shop "
        "floor calls already sweep lazily; schedule this for exact-time "
        "closes even with no traffic."
    )

    def handle(self, *args, **options):
        closed = close_overdue_stock_takes()
        self.stdout.write(
            self.style.SUCCESS(f"stock takes closed: {closed}")
        )
