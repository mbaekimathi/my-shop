from django.core.management.base import BaseCommand

from communications.tasks import process_pending_queue


class Command(BaseCommand):
    help = (
        "Send queued WhatsApp campaigns in small batches. "
        "Use on shared cPanel cron when Redis/Celery is not available."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max messages to attempt this run (default: COMMS_CRON_BATCH_SIZE)",
        )

    def handle(self, *args, **options):
        result = process_pending_queue(limit=options.get("limit"))
        self.stdout.write(
            self.style.SUCCESS(
                f"comms queue: attempted={result['attempted']} "
                f"finished_campaigns={result['finished_campaigns']}"
            )
        )
