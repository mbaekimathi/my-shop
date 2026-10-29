"""Public signed trade account page — a client's own trade-ins and trade-outs."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from employees.analytics_services import _money_ksh, _trade_due_amount, _zero
from shops.credit_note import credit_note_branding
from shops.models import Client, ShopReceipt, ShopReceiptKind, ShopReceiptStatus
from shops.services import _qty_label
from shops.trade_link import unsign_client_trade_token

_TRADE_RECEIPTS_Q = (
    Q(kind__in=(ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN))
    | Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
)


def _trade_row_status(due: Decimal, settled: Decimal) -> tuple[str, str]:
    if due <= 0:
        return "Settled", "good"
    if settled > 0:
        return "Part settled", "warn"
    return "Open", "bad"


def build_client_trade_account(*, client_id: int) -> dict:
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise Http404("Client not found.")
    receipts = list(
        ShopReceipt.objects.filter(client_id=client.pk)
        .filter(_TRADE_RECEIPTS_Q)
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .select_related("shop")
        .prefetch_related("lines")
        .order_by("-created_at")
    )
    balance = _zero()
    open_count = 0
    total_paid = _zero()
    shop_ids_seen: set[int] = set()
    trade_in_count = 0
    trade_out_count = 0
    rows = []
    for row in receipts:
        lines = []
        for line in row.lines.all():
            qty = Decimal(line.quantity or 0) - Decimal(
                line.returned_quantity or 0
            )
            if qty <= 0:
                continue
            unit = Decimal(line.unit_price or 0)
            lines.append(
                {
                    "name": line.item_name or "Item",
                    "qty": _qty_label(qty),
                    "unit": _money_ksh(unit),
                    "total": _money_ksh((unit * qty).quantize(Decimal("0.01"))),
                }
            )
        total = Decimal(row.total or 0)
        paid = Decimal(row.amount_paid or 0)
        exchange = Decimal(row.trade_exchange_value or 0)
        due = _trade_due_amount(total, paid, exchange)
        settled = (paid + exchange).quantize(Decimal("0.01"))
        status, status_tone = _trade_row_status(due, settled)
        balance += due
        total_paid += settled
        if row.shop_id:
            shop_ids_seen.add(row.shop_id)
        if due > 0:
            open_count += 1
        if row.kind == ShopReceiptKind.TRADE_IN:
            trade_in_count += 1
            type_label = "Trade in"
        else:
            trade_out_count += 1
            type_label = "Trade out"
        if row.settled_from_trade:
            type_label = f"{type_label} · settled"
        rows.append(
            {
                "id": f"trade-{row.pk}",
                "number": row.receipt_number,
                "type": type_label,
                "shop": row.shop.name if row.shop else "—",
                "status": status,
                "status_tone": status_tone,
                "total": _money_ksh(total),
                "paid": _money_ksh(settled),
                "due": _money_ksh(due),
                "when": row.created_at,
                "item_count": len(lines),
                "lines": lines,
            }
        )
    return {
        "client": client,
        "client_name": (client.full_name or "").strip() or "Customer",
        "client_phone": (client.phone_number or "").strip(),
        "balance": _money_ksh(balance),
        "can_pay": balance > 0,
        "summary_tiles": [
            {
                "label": "Open trades",
                "value": str(open_count),
                "hint": "Still being settled",
                "icon": "repeat",
                "tone": "warn" if open_count else "good",
            },
            {
                "label": "Trade ins",
                "value": str(trade_in_count),
                "hint": "Items brought in",
                "icon": "arrow-down-to-line",
                "tone": "shops",
            },
            {
                "label": "Trade outs",
                "value": str(trade_out_count),
                "hint": "Items taken out",
                "icon": "arrow-up-from-line",
                "tone": "shops",
            },
            {
                "label": "Settled so far",
                "value": _money_ksh(total_paid),
                "hint": "Cash, M-Pesa and exchanges",
                "icon": "wallet",
                "tone": "cash",
            },
        ],
        "rows": rows,
        "receipt_count": len(rows),
        "ledger_title": "Your trades",
        "empty_message": "You have no trade transactions on file.",
    }


@require_GET
def trade_note_page(request, token):
    client_id = unsign_client_trade_token(token)
    return render(
        request,
        "shops/trade_note.html",
        {
            **credit_note_branding(request=request),
            **build_client_trade_account(client_id=client_id),
        },
    )
