"""Inspect recent M-Pesa STK payments (Daraja + Nexus)."""

from django.core.management.base import BaseCommand

from shops.daraja_stk import get_stk_payment, refresh_stk_payment_if_pending, stk_payment_trace_dict
from shops.models import MpesaStkPayment, MpesaStkStatus


class Command(BaseCommand):
    help = "List recent STK payments and optionally refresh pending status from the provider."

    def add_arguments(self, parser):
        parser.add_argument(
            "--last",
            type=int,
            default=10,
            help="How many recent payments to show (default 10).",
        )
        parser.add_argument(
            "--id",
            dest="payment_id",
            default="",
            help="Single payment public_id (UUID) to show.",
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Poll provider / callbacks fallback for pending payments shown.",
        )
        parser.add_argument(
            "--pending-only",
            action="store_true",
            help="Only list pending payments.",
        )

    def handle(self, *args, **options):
        payment_id = (options.get("payment_id") or "").strip()
        refresh = bool(options.get("refresh"))
        last = max(1, int(options.get("last") or 10))

        if payment_id:
            payment = get_stk_payment(payment_id)
            if payment is None:
                self.stderr.write(self.style.ERROR(f"No STK payment with id {payment_id}"))
                return
            if refresh and payment.status == MpesaStkStatus.PENDING:
                payment = refresh_stk_payment_if_pending(
                    payment, min_age_seconds=0, force_safaricom=True
                )
            self._print_row(stk_payment_trace_dict(payment))
            return

        qs = MpesaStkPayment.objects.order_by("-created_at")
        if options.get("pending_only"):
            qs = qs.filter(status=MpesaStkStatus.PENDING)
        rows = list(qs[:last])
        if refresh:
            for payment in rows:
                if payment.status == MpesaStkStatus.PENDING:
                    refresh_stk_payment_if_pending(
                        payment, min_age_seconds=0, force_safaricom=True
                    )
            rows = list(qs[:last])

        if not rows:
            self.stdout.write("No STK payments found.")
            return

        for payment in rows:
            self._print_row(stk_payment_trace_dict(payment))

    def _print_row(self, row: dict) -> None:
        self.stdout.write("-" * 60)
        self.stdout.write(f"id:       {row.get('id')}")
        self.stdout.write(f"status:   {row.get('status')} ({row.get('status_label')})")
        self.stdout.write(f"provider: {row.get('provider')}")
        self.stdout.write(f"amount:   {row.get('amount')}  phone: {row.get('phone')}")
        self.stdout.write(f"receipt:  {row.get('mpesa_receipt_number') or '-'}")
        self.stdout.write(f"desc:     {row.get('result_desc') or '-'}")
        self.stdout.write(f"checkout: {row.get('checkout_request_id') or '-'}")
        self.stdout.write(f"created:  {row.get('created_at')}")
