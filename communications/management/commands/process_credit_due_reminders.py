from django.core.management.base import BaseCommand

from communications.automations import send_credit_due_today_reminders


class Command(BaseCommand):
    help = (
        "Send morning-of-due-date credit payment reminders to clients. "
        "Schedule daily on shared hosting cron (e.g. 07:00)."
    )

    def handle(self, *args, **options):
        result = send_credit_due_today_reminders()
        self.stdout.write(
            self.style.SUCCESS(
                f"credit due reminders: sent={result.get('sent', 0)} "
                f"skipped={result.get('skipped', 0)} "
                f"day={result.get('day', '')} "
                f"reason={result.get('reason', '')}"
            )
        )
