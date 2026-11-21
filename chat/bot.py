"""MY-SHOP Assistant — rule-based data chatbot.

A shop sends a message in its assistant thread; `reply_for` inspects the
text, queries the shared MY-SHOP tables (scoped to that shop), and returns a
WhatsApp-style reply using *bold* markers the client renders natively.

No external AI service — everything is deterministic and offline-friendly.
"""

import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.utils import timezone

from .myshop_models import (
    Client,
    ClientCreditAccountEvent,
    CompanyPosSettings,
    CustomerPrepayment,
    Expense,
    Item,
    ShelfItem,
    ShelfStockMovement,
    Shop,
    ShopDaySession,
    ShopItemPrice,
    ShopPosSettings,
    ShopReceipt,
    ShopReceiptLine,
    ShopStock,
    StockMovement,
    StockMovementLine,
)

def _tally(receipt) -> str:
    """Tally code — converted receipts show both kind letters (CS0005)."""
    from .stock import _receipt_tally_code

    return _receipt_tally_code(receipt)


COUNTED_STATUSES = ("active", "confirmed", "partial_return")
ANALYTICS_ALL = "all"
ANALYTICS_PENDING = "pending"
ANALYTICS_CONFIRMED = "confirmed"
SALE_KIND = "sale"
CREDIT_KIND = "credit"
GIFT_KIND = "gift"
# Credit account collections — ClientCreditAccountEvent kinds that put money
# in the till (the "Credit payments collected" day-report section).
CREDIT_PAID_KINDS = ("payment_cash", "payment_mpesa", "payment_card")

GREETING_RE = re.compile(
    r"^(hi+|hello|hey|habari|mambo|sasa|niaje|good\s*(morning|afternoon|evening|day)|"
    r"howdy|yo)\b[\s!.]*$",
    re.I,
)
HELP_RE = re.compile(r"(help|menu|commands|what can you do|options|guide)", re.I)
THANKS_RE = re.compile(r"^(thanks|thank you|asante|shukran|thx|ok thanks)\b", re.I)

STOP_WORDS = {
    "stock", "stok", "how", "many", "much", "do", "we", "have", "has", "is",
    "are", "the", "a", "an", "of", "in", "at", "on", "for", "check", "price",
    "bei", "ya", "ngapi", "left", "remaining", "available", "availability",
    "show", "me", "my", "our", "what", "whats", "what's", "please", "tell",
    "get", "any", "some", "item", "items", "product", "products", "quantity",
    "qty", "level", "levels", "current", "currently", "there", "cost",
    "shelf", "shelves",
}


def money(value) -> str:
    try:
        amount = Decimal(str(value or 0))
    except Exception:
        amount = Decimal("0")
    return f"KSh {amount:,.2f}"


def qty_fmt(value) -> str:
    try:
        q = Decimal(str(value or 0))
    except Exception:
        q = Decimal("0")
    if q == q.to_integral_value():
        return f"{int(q):,}"
    return f"{q.normalize():,}"


def _local_now():
    return timezone.localtime(timezone.now())


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def _explicit_dates(text: str, today) -> list:
    """Explicit calendar dates mentioned in text (sorted, deduped)."""
    t = text.lower()
    found = []

    def _safe(y, m, d):
        try:
            y = int(y)
            if y < 100:
                y += 2000
            found.append(datetime(y, m, d).date())
        except ValueError:
            pass

    for y, m, d in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        _safe(y, int(m), int(d))
    for d, m, y in re.findall(r"\b(\d{1,2})[/](\d{1,2})[/](\d{2,4})\b", t):
        _safe(y, int(m), int(d))
    for d, mon, y in re.findall(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s*" + _MON_RE + r"\s*,?\s*(\d{4})\b", t
    ):
        _safe(y, _MONTHS[mon], int(d))
    for mon, d, y in re.findall(
        r"\b" + _MON_RE + r"\s*(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b", t
    ):
        _safe(y, _MONTHS[mon], int(d))
    # Year-less "25 Oct" / "Oct 25" → nearest past occurrence.
    if not found:
        for d, mon in re.findall(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s*" + _MON_RE + r"\b(?!\s*\d{4})", t
        ):
            try:
                cand = datetime(today.year, _MONTHS[mon], int(d)).date()
                if cand > today:
                    cand = cand.replace(year=today.year - 1)
                found.append(cand)
            except ValueError:
                pass
        for mon, d in re.findall(
            r"\b" + _MON_RE + r"\s*(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*,?\s*\d{4})", t
        ):
            try:
                cand = datetime(today.year, _MONTHS[mon], int(d)).date()
                if cand > today:
                    cand = cand.replace(year=today.year - 1)
                found.append(cand)
            except ValueError:
                pass
    return sorted(set(found))


def _fmt_day(d) -> str:
    return d.strftime("%d %b %Y")


def _date_range(text: str):
    """Parse a date window from text → (start_date, end_date, label)."""
    now = _local_now()
    today = now.date()
    t = text.lower()

    explicit = _explicit_dates(t, today)
    if len(explicit) >= 2:
        start, end = explicit[0], explicit[-1]
        return (
            start,
            end,
            f"{_fmt_day(start)} – {_fmt_day(end)}",
        )
    if explicit:
        return explicit[0], explicit[0], _fmt_day(explicit[0])
    if "yesterday" in t or "jana" in t:
        d = today - timedelta(days=1)
        return d, d, "yesterday"
    if "last week" in t:
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=6)
        return start, end, "last week"
    if "this week" in t or "week" in t:
        start = today - timedelta(days=today.weekday())
        return start, today, "this week"
    if "last month" in t:
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, "last month"
    if "this month" in t or "month" in t:
        return today.replace(day=1), today, "this month"
    if "year" in t:
        return today.replace(month=1, day=1), today, "this year"
    return today, today, "today"


def _time_window(text: str):
    """(start, end, label) when the text names a period, else None.

    Used for point-in-time reports (credit): a bare "credits" means all-time
    outstanding, but once any window is given — explicit dates or a
    today/week/month/year word — the report scopes to rows created inside it.
    """
    t = text.lower()
    today = _local_now().date()
    if _explicit_dates(t, today) or re.search(
        r"\b(today|yesterday|jana|week|month|year)\b", t
    ):
        return _date_range(text)
    return None


def _window(start_date, end_date):
    """Inclusive [start_date, end_date] as aware datetimes.

    Range comparisons avoid `__date` lookups — XAMPP lacks MySQL tz tables,
    so `CONVERT_TZ` used by __date returns NULL and silently drops rows.
    """
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(
        datetime.combine(end_date + timedelta(days=1), time.min), tz
    )
    return start_dt, end_dt


def analytics_receipt_default() -> str:
    """Default receipt scope for sales & item analytics (`all` | `pending` | `confirmed`)."""
    return ANALYTICS_PENDING


def resolve_receipt_status(text: str | None = None) -> str:
    """All/pending/confirmed for sales & item analytics — message override or default."""
    low = (text or "").lower()
    if re.search(r"\bconfirmed\b", low):
        return ANALYTICS_CONFIRMED
    if re.search(r"\b(pending|unconfirmed)\b", low):
        return ANALYTICS_PENDING
    if re.search(r"\b(all|both)\b", low):
        return ANALYTICS_ALL
    return analytics_receipt_default()


def _analytics_statuses(status_key: str | None) -> tuple[str, ...]:
    """Map analytics scope → ShopReceipt.status values (same as MY-SHOP)."""
    key = (status_key or "").strip().lower()
    if key in (ANALYTICS_PENDING, "active"):
        return ("active",)
    if key == ANALYTICS_CONFIRMED:
        return ("confirmed", "partial_return")
    if key in (ANALYTICS_ALL, "any", "both"):
        return COUNTED_STATUSES
    return _analytics_statuses(analytics_receipt_default())


def _status_scope_label(status_key: str) -> str:
    if status_key == ANALYTICS_ALL:
        return "all receipts"
    return (
        "confirmed receipts"
        if status_key == ANALYTICS_CONFIRMED
        else "pending receipts"
    )


def _sales_label(date_label: str, status_key: str) -> str:
    return f"{date_label} · {_status_scope_label(status_key)}"


def _settled_window_q(start_dt, end_dt) -> Q:
    """Paid-day window for settled-from-credit sales (mirrors analytics).

    A credit converted to a sale is recognised on the day the money was
    confirmed (``confirmed_payment_at``); every other receipt keys on
    ``created_at``. Settled rows with no payment stamp fall back to
    ``created_at`` — same rule as ``employees.analytics_services``.
    """
    paid = Q(
        settled_from_credit=True,
        confirmed_payment_at__isnull=False,
        confirmed_payment_at__gte=start_dt,
        confirmed_payment_at__lt=end_dt,
    )
    normal = (
        Q(settled_from_credit=False) | Q(confirmed_payment_at__isnull=True)
    ) & Q(created_at__gte=start_dt, created_at__lt=end_dt)
    return paid | normal


_MONEY = DecimalField(max_digits=14, decimal_places=2)


def _tender_exprs() -> dict:
    """Cash/M-Pesa/card aggregates — settled-from-credit sales tender 0 here.

    Their money arrived through credit account collections, not the POS till,
    so analytics zeroes their channel columns (``_build_sales``); the bot's
    channel splits follow the same rule.
    """
    zero = Value(Decimal("0"), output_field=_MONEY)

    def _tender(field):
        return Sum(
            Case(
                When(settled_from_credit=False, then=F(field)),
                default=zero,
                output_field=_MONEY,
            )
        )

    return {
        "cash": _tender("cash_amount"),
        "mpesa": _tender("mpesa_amount"),
        "card": _tender("card_amount"),
    }


def _receipts(shop: Shop, start, end, kinds=(SALE_KIND,), receipt_status=None):
    start_dt, end_dt = _window(start, end)
    statuses = _analytics_statuses(receipt_status or analytics_receipt_default())
    return ShopReceipt.objects.filter(
        shop_id=shop.pk,
        kind__in=kinds,
        status__in=statuses,
    ).filter(_settled_window_q(start_dt, end_dt))


def _credit_rows(qs, shop_names=None) -> list:
    """Serialized outstanding-credit rows shared by both assistants.

    `rid` feeds the eye-icon items preview; `phone` comes from the receipt
    with a fallback to the linked client's number so the name is tappable.
    """
    rows = list(qs.order_by("-balance")[:80])
    missing = [
        r.client_id
        for r in rows
        if r.client_id and not (r.client_phone or "").strip()
    ]
    phones = (
        dict(
            Client.objects.filter(pk__in=missing).values_list(
                "pk", "phone_number"
            )
        )
        if missing
        else {}
    )
    out = []
    for r in rows:
        row = {
            "rid": r.pk,
            "customer": r.client_name or "Walk-in customer",
            "phone": (r.client_phone or "").strip()
            or phones.get(r.client_id, ""),
            "receipt": _tally(r),
            "balance": money(r.balance),
            "due": (
                r.credit_due_date.strftime("%d %b")
                if r.credit_due_date
                else ""
            ),
        }
        if shop_names is not None:
            row["shop"] = shop_names.get(r.shop_id, "")
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------


def _sales_reply(shop: Shop, text: str) -> str:
    start, end, label = _date_range(text)
    status = resolve_receipt_status(text)
    scope = _sales_label(label, status)
    qs = _receipts(shop, start, end, receipt_status=status)
    agg = qs.aggregate(
        total=Sum("total"),
        count=Count("id"),
        paid=Sum("amount_paid"),
        **_tender_exprs(),
    )
    count = agg["count"] or 0
    if not count:
        return f"No sales recorded for {shop.name} {scope}."
    lines = [
        f"*Sales — {shop.name} ({scope})*",
        f"Total: *{money(agg['total'])}* across {count} receipt{'s' if count != 1 else ''}",
        f"Cash {money(agg['cash'])}  |  M-Pesa {money(agg['mpesa'])}  |  Card {money(agg['card'])}",
    ]
    credit_qs = _receipts(
        shop, start, end, kinds=(CREDIT_KIND,), receipt_status=status
    )
    credit_total = credit_qs.aggregate(t=Sum("total"))["t"] or 0
    if credit_total:
        lines.append(f"Credit issued: {money(credit_total)}")
    lines.append(f"%%items|{start.isoformat()}|{end.isoformat()}|{status}%%")
    return "\n".join(lines)


def _expenses_reply(shop: Shop, text: str) -> str:
    start, end, label = _date_range(text)
    start_dt, end_dt = _window(start, end)
    qs = Expense.objects.filter(
        shop_id=shop.pk, created_at__gte=start_dt, created_at__lt=end_dt
    )
    agg = qs.aggregate(total=Sum("amount"), paid=Sum("amount_paid"), count=Count("id"))
    if not agg["count"]:
        return f"No expenses recorded for {shop.name} {label}."
    owed = (agg["total"] or 0) - (agg["paid"] or 0)
    lines = [
        f"*Expenses — {shop.name} ({label})*",
        f"Total: *{money(agg['total'])}* across {agg['count']} entr{'ies' if agg['count'] != 1 else 'y'}",
        f"Paid {money(agg['paid'])}" + (f"  |  Unpaid {money(owed)}" if owed else ""),
    ]
    by_cat = qs.values("category").annotate(t=Sum("amount")).order_by("-t")[:4]
    if by_cat:
        lines.append(
            "By category: "
            + ", ".join(f"{r['category']} {money(r['t'])}" for r in by_cat)
        )
    return "\n".join(lines)


def _prepay_code(row) -> str:
    """A0007-style code — same shape as ``format_simple_doc_number``."""
    n = max(1, int(row.pk or 1))
    width = 4 if n < 10000 else len(str(n))
    return f"A{n:0{width}d}"


def _prepayments_reply(shop: Shop, text: str) -> str:
    start, end, label = _date_range(text)
    start_dt, end_dt = _window(start, end)
    qs = CustomerPrepayment.objects.filter(
        shop_id=shop.pk, created_at__gte=start_dt, created_at__lt=end_dt
    )
    total = qs.aggregate(t=Sum("amount"))["t"] or 0
    count = qs.count()
    if not count:
        return f"No customer prepayments for {shop.name} {label}."
    lines = [
        f"*Customer prepayments — {shop.name} ({label})*",
        f"Total: *{money(total)}* across "
        f"{count} payment{'s' if count != 1 else ''}",
        "",
    ]
    for r in qs.order_by("-created_at")[:8]:
        channels = _dec(r.cash_amount) + _dec(r.mpesa_amount) + _dec(
            r.card_amount
        )
        purpose = (r.purpose or "").strip()
        lines.append(
            f"- {_prepay_code(r)}: {r.customer_name} "
            f"{money(channels or r.amount)} ({r.payment_method})"
            + (f" — {purpose}" if purpose else "")
        )
    if count > 8:
        lines.append(f"...plus {count - 8} more.")
    return "\n".join(lines)


def _credit_reply(shop: Shop, text: str) -> str:
    win = _time_window(text)
    qs = ShopReceipt.objects.filter(
        shop_id=shop.pk, kind=CREDIT_KIND, status__in=COUNTED_STATUSES
    )
    if win:
        start_dt, end_dt = _window(win[0], win[1])
        qs = qs.filter(created_at__gte=start_dt, created_at__lt=end_dt)
    qs = qs.annotate(balance=F("total") - F("amount_paid")).filter(balance__gt=0)
    total = qs.aggregate(t=Sum("balance"))["t"] or 0
    count = qs.count()
    if not count:
        if win:
            return f"No outstanding credit issued {win[2]} at {shop.name}."
        return f"{shop.name} has no outstanding customer credit. Clean books!"
    scope = f" issued {win[2]}" if win else ""
    lines = [
        f"*Outstanding credit{scope} — {shop.name}*",
        f"*{money(total)}* owed across {count} receipt{'s' if count != 1 else ''}",
        "",
        "Largest balances:",
    ]
    for r in qs.order_by("-balance")[:5]:
        who = r.client_name or "Walk-in customer"
        due = f" (due {r.credit_due_date:%d %b})" if r.credit_due_date else ""
        lines.append(f"- {who}: {money(r.balance)} on {_tally(r)}{due}")
    return "\n".join(lines)


def _stock_query(text: str) -> str:
    words = re.sub(r"[^\w\s-]", " ", text.lower()).split()
    kept = [w for w in words if w not in STOP_WORDS and len(w) > 1]
    return " ".join(kept).strip()


def _price_for(item: Item, shop: Shop) -> Decimal:
    override = ShopItemPrice.objects.filter(
        shop_id=shop.pk, item_id=item.pk, is_sold=True
    ).first()
    if override and override.price and override.price > 0:
        return override.price
    if item.shop_price and item.shop_price > 0:
        return item.shop_price
    return item.minimum_selling_price or Decimal("0")


def _stock_reply(shop: Shop, text: str) -> str:
    query = _stock_query(text)
    if not query:
        return "Which item? Try *stock sugar* or *price of cement*."
    modes = shop_stock_modes(shop)
    lines = []
    if modes["items"]:
        items = Item.objects.filter(is_suspended=False)
        for word in query.split():
            items = items.filter(name__icontains=word)
        for item in list(items[:4]):
            stock_row = ShopStock.objects.filter(
                shop_id=shop.pk, item_id=item.pk
            ).first()
            mine = stock_row.quantity if stock_row else Decimal("0")
            price = _price_for(item, shop)
            lines.append(
                f"*{item.name}* ({item.category})\n"
                f"In stock at {shop.name}: *{qty_fmt(mine)}*  |  "
                f"Price: {money(price)}  |  "
                f"All shops: {qty_fmt(item.stock)}"
            )
    if modes["shelf"]:
        shelf_qs = ShelfItem.objects.filter(
            shop_id=shop.pk, is_suspended=False, is_archived=False
        )
        for word in query.split():
            shelf_qs = shelf_qs.filter(name__icontains=word)
        for item in list(shelf_qs[:4]):
            unit = f" {item.unit}" if item.unit else ""
            lines.append(
                f"*{item.name}* (shelf)\n"
                f"On the shelf at {shop.name}: "
                f"*{qty_fmt(item.quantity)}*{unit}"
            )
    if not lines:
        return (
            f"I couldn't find an item matching \"{query}\" in the catalogue. "
            "Check the spelling or try a shorter name."
        )
    return "\n\n".join(lines[:6])


def _low_stock_reply(shop: Shop, text: str) -> str:
    modes = shop_stock_modes(shop)
    low = []
    if modes["items"]:
        rows = (
            ShopStock.objects.filter(shop_id=shop.pk)
            .select_related()
        )
        item_ids = [r.item_id for r in rows]
        items = {
            i.pk: i
            for i in Item.objects.filter(pk__in=item_ids, is_suspended=False)
        }
        for r in rows:
            item = items.get(r.item_id)
            if not item:
                continue
            threshold = r.low_stock_threshold or 0
            if threshold and r.quantity <= threshold:
                low.append((item.name, r.quantity, threshold))
            elif not threshold and r.quantity <= 5:
                low.append((item.name, r.quantity, 5))
    if modes["shelf"]:
        shelf_rows = ShelfItem.objects.filter(
            shop_id=shop.pk, is_suspended=False, is_archived=False
        )
        for item in shelf_rows:
            threshold = item.low_stock_threshold or 0
            name = f"{item.name} (shelf)"
            if threshold and item.quantity <= threshold:
                low.append((name, item.quantity, threshold))
            elif not threshold and item.quantity <= 5:
                low.append((name, item.quantity, 5))
    if not low:
        return f"Nothing is running low at {shop.name} right now. Stock looks healthy."
    low.sort(key=lambda x: x[1])
    lines = [f"*Low stock — {shop.name}* ({len(low)} items)", ""]
    for name, q, t in low[:10]:
        lines.append(f"- {name}: *{qty_fmt(q)}* left (alert at {qty_fmt(t)})")
    if len(low) > 10:
        lines.append(f"...plus {len(low) - 10} more.")
    return "\n".join(lines)


def _top_sellers_reply(shop: Shop, text: str) -> str:
    start, end, label = _date_range(text if re.search(r"week|month|year|today", text, re.I) else "this month")
    status = resolve_receipt_status(text)
    scope = _sales_label(label, status)
    receipt_ids = _receipts(shop, start, end, receipt_status=status).values("id")
    rows = (
        ShopReceiptLine.objects.filter(receipt_id__in=receipt_ids)
        .values("item_name")
        .annotate(q=Sum(F("quantity") - F("returned_quantity")), t=Sum("line_total"))
        .order_by("-q")[:8]
    )
    rows = [r for r in rows if (r["q"] or 0) > 0]
    if not rows:
        return f"No sale lines found for {shop.name} {scope}."
    lines = [f"*Top sellers — {shop.name} ({scope})*", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['item_name']} — x{qty_fmt(r['q'])} ({money(r['t'])})")
    return "\n".join(lines)


def _receipts_reply(shop: Shop, text: str) -> str:
    m = re.search(r"receipt\s*#?\s*([A-Za-z0-9-]+)", text, re.I)
    if m:
        number = m.group(1)
        r = (
            ShopReceipt.objects.filter(shop_id=shop.pk, receipt_number__iexact=number)
            .order_by("-created_at")
            .first()
        )
        if r is None:
            return f"I couldn't find receipt *{number}* for {shop.name}."
        who = r.client_name or "Walk-in customer"
        balance = (r.total or 0) - (r.amount_paid or 0)
        lines = [
            f"*Receipt {_tally(r)}* — {r.kind}",
            f"Date: {timezone.localtime(r.created_at):%d %b %Y, %H:%M}",
            f"Customer: {who}",
            f"Total: *{money(r.total)}* ({r.payment_method})",
            f"Status: {r.status}",
        ]
        if balance > 0:
            lines.append(f"Balance owed: {money(balance)}")
        return "\n".join(lines)
    recent = ShopReceipt.objects.filter(
        shop_id=shop.pk, status__in=COUNTED_STATUSES
    ).order_by("-created_at")[:5]
    recent = list(recent)
    if not recent:
        return f"{shop.name} has no receipts yet."
    lines = [f"*Recent receipts — {shop.name}*", ""]
    for r in recent:
        lines.append(
            f"- {_tally(r)} ({r.kind}) — {money(r.total)} — "
            f"{timezone.localtime(r.created_at):%d %b, %H:%M}"
        )
    lines.append("\nAsk *receipt 00123* for a specific one.")
    return "\n".join(lines)


def _day_status_reply(shop: Shop, text: str) -> str:
    session = ShopDaySession.objects.filter(shop_id=shop.pk).first()
    if session is None:
        return f"No trading day has been opened for {shop.name} yet."
    if session.is_open:
        return (
            f"*{shop.name}* day is *open* — started "
            f"{timezone.localtime(session.opened_at):%d %b %Y at %H:%M}.\n"
            f"Opening till: cash {money(session.opening_cash)}, "
            f"M-Pesa {money(session.opening_mpesa)}."
        )
    return (
        f"*{shop.name}* last trading day closed at "
        f"{timezone.localtime(session.closed_at):%d %b %Y, %H:%M}."
    )


def _shops_reply(shop: Shop, text: str) -> str:
    others = Shop.objects.filter(is_suspended=False, is_hidden=False).exclude(
        pk=shop.pk
    ).order_by("name")
    if not others:
        return "No other shops are registered yet."
    lines = ["*Registered shops*", ""]
    for s in others[:12]:
        lines.append(f"- {s.name} — {s.location}")
    lines.append("\nOpen a new chat to message any of them directly.")
    return "\n".join(lines)


def _daily_summary_reply(shop: Shop, text: str) -> str:
    start, end, label = _date_range(text)
    status = resolve_receipt_status(text)
    scope = _sales_label(label, status)
    start_dt, end_dt = _window(start, end)
    sales = _receipts(shop, start, end, receipt_status=status).aggregate(
        t=Sum("total"), c=Count("id")
    )
    expenses = Expense.objects.filter(
        shop_id=shop.pk, created_at__gte=start_dt, created_at__lt=end_dt
    ).aggregate(t=Sum("amount"))
    credit = (
        ShopReceipt.objects.filter(shop_id=shop.pk, kind=CREDIT_KIND, status__in=COUNTED_STATUSES)
        .annotate(balance=F("total") - F("amount_paid"))
        .filter(balance__gt=0)
        .aggregate(t=Sum("balance"))
    )
    prepay = CustomerPrepayment.objects.filter(
        shop_id=shop.pk, created_at__gte=start_dt, created_at__lt=end_dt
    ).aggregate(t=Sum("amount"))
    credit_paid = ClientCreditAccountEvent.objects.filter(
        shop_id=shop.pk,
        kind__in=CREDIT_PAID_KINDS,
        occurred_at__gte=start_dt,
        occurred_at__lt=end_dt,
    ).aggregate(t=Sum("amount"))
    lines = [
        f"*{scope.capitalize()} at {shop.name}*",
        f"Sales: *{money(sales['t'])}* ({sales['c'] or 0} receipts)",
        f"Expenses: {money(expenses['t'])}",
        f"Outstanding credit: {money(credit['t'])}",
    ]
    if prepay["t"]:
        lines.append(f"Customer prepayments: {money(prepay['t'])}")
    if credit_paid["t"]:
        lines.append(f"Credit payments: {money(credit_paid['t'])}")
    return "\n".join(lines)


def _help_reply(shop: Shop) -> str:
    default = analytics_receipt_default()
    return (
        f"*What I can do for {shop.name}*\n\n"
        "*Sales & money*\n"
        "- sales today / yesterday / this week / last week / this month\n"
        "- sales today pending / confirmed — override analytics default "
        f"(currently *{default}*)\n"
        "- expenses today / this month\n"
        "- prepayments — customer deposits received\n"
        "- summary — quick snapshot of today\n"
        "- credit — outstanding customer balances\n\n"
        "*Stock & products*\n"
        "- stock <item> — on-hand quantity (items & shelf), e.g. *stock sugar*\n"
        "- price of <item>\n"
        "- low stock — items below their alert level\n"
        "- stock movement / shelf stock movement — recent in/out & transfers\n"
        "- top sellers / items sold — best movers "
        f"(default *{default}* receipts)\n\n"
        "*Operations*\n"
        "- receipts — last 5, or *receipt <number>*\n"
        "- day status — is the shop open\n"
        "- shops — list all registered shops\n\n"
        "Tip: messages to other shops go through the normal chats — "
        "I'm only for your data questions."
    )


def _fallback_reply() -> str:
    return (
        "I didn't quite get that. I'm best at data questions like "
        "*sales today*, *stock <item>*, *low stock*, *credit*, *top sellers* "
        "or *receipt <number>*.\n\nType *help* for the full menu."
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _stock_movement_groups(shop: Shop, text: str):
    """(label, groups) — each movement with its own lines, for the card."""
    start, end, label = _date_range(text)
    start_dt, end_dt = _window(start, end)
    movements = list(
        StockMovement.objects.filter(
            Q(shop_id=shop.pk) | Q(requested_from_shop_id=shop.pk),
            created_at__range=(start_dt, end_dt),
        ).order_by("-created_at")[:30]
    )
    line_map = {}
    for l in StockMovementLine.objects.filter(
        movement_id__in=[m.pk for m in movements]
    ).order_by("pk"):
        line_map.setdefault(l.movement_id, []).append(l)

    shop_names = {
        s.pk: s.name
        for s in Shop.objects.filter(
            pk__in=[
                p
                for m in movements
                for p in (m.shop_id, m.requested_from_shop_id)
                if p and p != shop.pk
            ]
        )
    }
    groups = []
    for m in movements:
        if m.movement_type == "in":
            head, kind = "Stock in", "in"
        elif m.movement_type == "out":
            head, kind = "Stock out", "out"
        elif m.shop_id == shop.pk:
            head, kind = (
                f"To {shop_names.get(m.requested_from_shop_id, '?')}",
                "tout",
            )
        else:
            head, kind = (
                f"From {shop_names.get(m.shop_id, '?')}",
                "tin",
            )
        # Incoming transfers stay blind until this shop accepts them —
        # hide the sent quantities here too, same as the chat card.
        blind = (
            m.requested_from_shop_id == shop.pk
            and bool(m.request_status)
            and m.request_status != "fulfilled"
        )
        groups.append(
            {
                "kind": kind,
                "head": head,
                "status": m.request_status if m.movement_type == "request" else "",
                "when": timezone.localtime(m.created_at).strftime("%d %b, %H:%M"),
                "items": [
                    {
                        "item": l.item_name,
                        "qty": "?" if blind else qty_fmt(l.quantity),
                    }
                    for l in line_map.get(m.pk, [])
                ],
            }
        )
    return label, groups


def _shelf_movement_groups(shop: Shop, start_dt, end_dt) -> list:
    """Per-shop shelf ledger groups (in/register → tin, out → tout)."""
    item_ids = list(
        ShelfItem.objects.filter(shop_id=shop.pk).values_list(
            "pk", flat=True
        )
    )
    if not item_ids:
        return []
    moves = list(
        ShelfStockMovement.objects.filter(
            shelf_item_id__in=item_ids,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        ).order_by("-created_at", "-id")[:40]
    )
    items = {
        i.pk: i
        for i in ShelfItem.objects.filter(
            pk__in={m.shelf_item_id for m in moves}
        )
    }
    groups = []
    for mv in moves:
        item = items.get(mv.shelf_item_id)
        if mv.movement_type == "out":
            head, kind = "Shelf out", "tout"
        elif mv.movement_type == "register":
            head, kind = "Registered", "tin"
        else:
            head, kind = "Shelf in", "tin"
        status = " · ".join(
            bit
            for bit in (
                SHELF_REASON_LABELS.get(mv.reason, mv.reason),
                (mv.note or "").strip(),
            )
            if bit
        )
        groups.append(
            {
                "kind": kind,
                "head": head,
                "status": status,
                "when": timezone.localtime(mv.created_at).strftime(
                    "%d %b, %H:%M"
                ),
                "items": [
                    {
                        "item": item.name if item else "?",
                        "qty": qty_fmt(mv.quantity),
                    }
                ],
            }
        )
    return groups


def _shelf_groups_dated(shop: Shop, text: str):
    start, end, label = _date_range(text)
    start_dt, end_dt = _window(start, end)
    return label, _shelf_movement_groups(shop, start_dt, end_dt)


def _shop_movement(shop: Shop, text: str):
    """(label, groups, ledger, disabled_reply) — item or shelf ledger.

    Same routing as the all-shops assistant: a ``shelf``/``item`` keyword
    picks explicitly, a bare query defaults to the enabled ledger, and a
    ledger that's off answers honestly instead of pretending empty.
    """
    modes = shop_stock_modes(shop)
    ledger = _movement_ledger(text, modes)
    disabled = _movement_disabled_reply(ledger, modes)
    if disabled is not None:
        return None, [], ledger, disabled
    if ledger == "shelf":
        label, groups = _shelf_groups_dated(shop, text)
    else:
        label, groups = _stock_movement_groups(shop, text)
    return label, groups, ledger, None


def _stock_movement_reply(shop: Shop, text: str) -> str:
    label, groups, ledger, disabled = _shop_movement(shop, text)
    if disabled is not None:
        return disabled
    if not groups:
        what = (
            "shelf stock movements" if ledger == "shelf" else "stock movements"
        )
        return f"No {what} for {shop.name} {label}."
    head = "Shelf stock movement" if ledger == "shelf" else "Stock movement"
    lines = [f"*{head} — {shop.name} ({label})*", ""]
    for g in groups:
        sign = (
            "+IN"
            if g["kind"] == "in"
            else ("-OUT" if g["kind"] == "out" else g["head"])
        )
        for it in g["items"]:
            lines.append(
                f"{sign}: {it['item']} ×{it['qty']}"
                + (f" ({g['status']})" if g["status"] else "")
            )
    return "\n".join(lines)


def items_sold_rows(shop: Shop, text: str):
    """(label, rows) for the items-sold window — shared by text + card."""
    start, end, label = _date_range(text)
    status = resolve_receipt_status(text)
    scope = _sales_label(label, status)
    receipt_ids = _receipts(shop, start, end, receipt_status=status).values("id")
    rows = (
        ShopReceiptLine.objects.filter(receipt_id__in=receipt_ids)
        .values("item_name")
        .annotate(
            q=Sum(F("quantity") - F("returned_quantity")),
            t=Sum("line_total"),
        )
        .order_by("-q")
    )
    return scope, [r for r in rows if (r["q"] or 0) > 0]


ITEMS_INTENT_RE = re.compile(r"\bitems?\b", re.I)
# Naming a sales report (sale/sales/revenue/income) wins over the items
# word — the sales reply answers first and items ride behind its
# "View items sold" link instead of taking over the reply.
SALES_WORD_RE = re.compile(r"\b(sales?|revenue|income)\b", re.I)


def items_intent_matches(shop, text: str) -> bool:
    low = (text or "").lower()
    return bool(
        ITEMS_INTENT_RE.search(low)
        and not SALES_WORD_RE.search(low)
        and (
            re.search(r"\bsold\b|\blist\b|\bsales\b", low)
            or re.search(
                r"\btoday\b|\byesterday\b|\bjana\b|\bweek\b|\bmonth\b|\byear\b",
                low,
            )
            or _explicit_dates(low, _local_now().date())
            or low.strip() in ("items", "item", "items sold")
        )
    )


def card_meta(shop: Shop, text: str):
    """Structured payload attached to bot replies that render as cards."""
    t = (text or "").strip()
    low = t.lower()

    # Items-sold before the broad sales regex (which matches "sold").
    if items_intent_matches(shop, t):
        label, rows = items_sold_rows(shop, t)
        return {
            "items_sold": {
                "label": label,
                "items": [
                    {
                        "name": r["item_name"],
                        "qty": qty_fmt(r["q"]),
                        "total": money(r["t"]),
                    }
                    for r in rows
                ],
            }
        }

    if re.search(r"\b(sale|sales|sell|selling|sold|revenue|income|till|collection|collections)\b", low):
        start, end, label = _date_range(t)
        status = resolve_receipt_status(t)
        qs = _receipts(shop, start, end, receipt_status=status)
        agg = qs.aggregate(
            total=Sum("total"), count=Count("id"), **_tender_exprs()
        )
        if not (agg["count"] or 0):
            return None
        credit = (
            _receipts(
                shop, start, end, kinds=(CREDIT_KIND,), receipt_status=status
            ).aggregate(t=Sum("total"))["t"]
            or 0
        )
        card = {
            "sales": {
                "label": _sales_label(label, status),
                "total": money(agg["total"]),
                "receipts": agg["count"],
                "cash": money(agg["cash"]),
                "mpesa": money(agg["mpesa"]),
                "card": money(agg["card"]),
                "credit": money(credit),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "receipt_status": status,
            }
        }
        # Items stay behind the card's "View items sold" link — the
        # client builds it from start/end/receipt_status and fetches the
        # items list as a follow-up reply on click.
        return card

    if re.search(r"\b(expense|expenses|spent|spending|costs?)\b", low):
        start, end, label = _date_range(t)
        start_dt, end_dt = _window(start, end)
        qs = Expense.objects.filter(
            shop_id=shop.pk, created_at__gte=start_dt, created_at__lt=end_dt
        )
        agg = qs.aggregate(
            total=Sum("amount"), paid=Sum("amount_paid"), count=Count("id")
        )
        if not agg["count"]:
            return None
        return {
            "expenses": {
                "label": label,
                "total": money(agg["total"]),
                "count": agg["count"],
                "paid": money(agg["paid"]),
                "unpaid": money((agg["total"] or 0) - (agg["paid"] or 0)),
                "rows": [
                    {
                        "name": e.name,
                        "cat": e.category,
                        "amount": money(e.amount),
                        "status": e.payment_status,
                    }
                    for e in qs.order_by("-created_at")[:60]
                ],
            }
        }

    if re.search(r"\b(credits?|debts?|owe|owed|deni|denis|balances?)\b", low):
        win = _time_window(t)
        qs = ShopReceipt.objects.filter(
            shop_id=shop.pk, kind=CREDIT_KIND, status__in=COUNTED_STATUSES
        )
        if win:
            start_dt, end_dt = _window(win[0], win[1])
            qs = qs.filter(created_at__gte=start_dt, created_at__lt=end_dt)
        qs = qs.annotate(balance=F("total") - F("amount_paid")).filter(
            balance__gt=0
        )
        if not qs.exists():
            return None
        return {
            "credits": {
                "label": win[2] if win else "",
                "total": money(qs.aggregate(t=Sum("balance"))["t"]),
                "count": qs.count(),
                "rows": _credit_rows(qs),
            }
        }

    if re.search(r"\b(stock movements?|movements?|transfers?|moved)\b", low):
        label, groups, ledger, disabled = _shop_movement(shop, t)
        if disabled is not None or not groups:
            return None
        card = {"movement": {"label": label, "groups": groups}}
        if ledger == "shelf":
            card["movement"]["title"] = "Shelf stock movement"
        return card

    if re.search(r"\b(summary|report|overview|snapshot|today's|todays|performance)\b", low):
        start, end, label = _date_range(t)
        status = resolve_receipt_status(t)
        start_dt, end_dt = _window(start, end)
        sales = _receipts(shop, start, end, receipt_status=status).aggregate(
            t=Sum("total"), c=Count("id")
        )
        expenses = Expense.objects.filter(
            shop_id=shop.pk, created_at__gte=start_dt, created_at__lt=end_dt
        ).aggregate(t=Sum("amount"))
        credit = (
            ShopReceipt.objects.filter(
                shop_id=shop.pk, kind=CREDIT_KIND, status__in=COUNTED_STATUSES
            )
            .annotate(balance=F("total") - F("amount_paid"))
            .filter(balance__gt=0)
            .aggregate(t=Sum("balance"))
        )
        return {
            "summary": {
                "label": _sales_label(label, status),
                "sales": money(sales["t"]),
                "receipts": sales["c"] or 0,
                "expenses": money(expenses["t"]),
                "credit": money(credit["t"]),
            }
        }

    return None


def _items_sold_reply(shop: Shop, text: str) -> str:
    label, rows = items_sold_rows(shop, text)
    if not rows:
        return f"No items sold at {shop.name} {label}."
    lines = [f"*Items sold — {shop.name} ({label})* — {len(rows)} items", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['item_name']} — x{qty_fmt(r['q'])} ({money(r['t'])})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# All-shops assistant — company-wide aggregates for staff (admin/manager/IT)
# ---------------------------------------------------------------------------


def _all_receipts(start, end, kinds=(SALE_KIND,), receipt_status=None):
    start_dt, end_dt = _window(start, end)
    statuses = _analytics_statuses(receipt_status or analytics_receipt_default())
    return ShopReceipt.objects.filter(
        kind__in=kinds,
        status__in=statuses,
    ).filter(_settled_window_q(start_dt, end_dt))


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _employee_names(ids) -> dict:
    """{profile pk: display name} — joins the shared employees/auth tables."""
    ids = [pk for pk in set(ids) if pk]
    if not ids:
        return {}
    from django.db import connection

    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT ep.id, u.first_name, u.last_name, u.username "
                "FROM employees_employeeprofile ep "
                "JOIN auth_user u ON u.id = ep.user_id "
                f"WHERE ep.id IN ({','.join(['%s'] * len(ids))})",
                ids,
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    return {
        pk: f"{first or ''} {last or ''}".strip() or (username or "")
        for pk, first, last, username in rows
    }


def _refund_events(shop_ids, start_dt, end_dt) -> list:
    """(shop_id, at, amount) — till refunds paid inside the window."""
    from django.utils.dateparse import parse_datetime

    events = []
    for row in (
        ShopReceipt.objects.filter(shop_id__in=shop_ids)
        .filter(return_payment_events__len__gt=0)
        .values("shop_id", "return_payment_events")
    ):
        for event in row["return_payment_events"] or []:
            if not isinstance(event, dict):
                continue
            raw = event.get("at")
            when = (
                raw if isinstance(raw, datetime)
                else parse_datetime(str(raw or ""))
            )
            if when is None:
                continue
            if timezone.is_naive(when):
                when = timezone.make_aware(when)
            if not (start_dt <= when < end_dt):
                continue
            amount = (
                _dec(event.get("cash"))
                + _dec(event.get("mpesa"))
                + _dec(event.get("card"))
            )
            if amount > 0:
                events.append((row["shop_id"], when, amount))
    return events


REPORT_ROW_CAP = 40


def _all_shops_report(start, end, status, section_visible=None) -> dict:
    """Simplified cashiers report — one row per shop day session.

    Sessions overlapping the window carry their times, cashier and status;
    sales/paid-out land in the session whose activity window owns them
    (activity starts at the previous close — the till hand-over rule).
    Period totals span every active shop regardless of sessions so the
    report reconciles with the Sales/Expenses cards.

    Money in follows the real day report: POS channel tender (settled-from-
    credit sales tender 0 — their cash arrived via credit account
    collections), plus customer prepayments and credit payments collected,
    each under its own day-report section. Paid out covers expenses,
    owner drawings, item+shelf supplier payments and till refunds.

    ``section_visible`` is an optional ``(section_key) -> bool`` predicate —
    the viewer's day-report section picks (``day_report_section_enabled``).
    A switched-off part is dropped from ``paid_out`` / each row's ``in`` /
    ``out``, so hidden sections neither display nor count in the totals.
    """
    from bisect import bisect_right

    _sec = section_visible or (lambda _key: True)
    out_enabled = {
        "expenses": _sec("day_report_sec_expenses"),
        "stock": _sec("day_report_sec_item_stock"),
        "shelf": _sec("day_report_sec_shelf_stock"),
        "drawings": _sec("day_report_sec_drawings"),
        "refunds": _sec("day_report_sec_money_flow"),
    }
    in_enabled = {
        "sales": _sec("day_report_sec_money_flow"),
        "prepayments": _sec("day_report_sec_prepayments"),
        "credit_paid": _sec("day_report_sec_credit_payments"),
    }

    def _out_total(cell):
        return sum(
            (cell[kind] for kind, on in out_enabled.items() if on),
            Decimal("0"),
        )

    def _in_total(cell):
        return sum(
            (cell[kind] for kind, on in in_enabled.items() if on),
            Decimal("0"),
        )

    start_dt, end_dt = _window(start, end)
    shops = dict(
        Shop.objects.filter(is_suspended=False, is_hidden=False).values_list(
            "pk", "name"
        )
    )
    shop_ids = list(shops)

    sessions = list(
        ShopDaySession.objects.filter(
            shop_id__in=shop_ids, opened_at__lt=end_dt
        )
        .filter(Q(closed_at__isnull=True) | Q(closed_at__gte=start_dt))
        .order_by("-opened_at")
    )
    # Activity starts at the previous close (till hand-over convention).
    closes = {}
    for sid, closed_at in ShopDaySession.objects.filter(
        shop_id__in=shop_ids, closed_at__isnull=False
    ).values_list("shop_id", "closed_at"):
        closes.setdefault(sid, []).append(closed_at)
    for stamps in closes.values():
        stamps.sort()
    now = timezone.now()
    windows = []  # (session, activity_start, activity_end)
    for s in sessions:
        stamps = closes.get(s.shop_id) or []
        idx = bisect_right(stamps, s.opened_at)
        windows.append(
            (s, stamps[idx - 1] if idx else s.opened_at, s.closed_at or now)
        )

    sales_rows = list(
        _all_receipts(start, end, receipt_status=status)
        .filter(shop_id__in=shop_ids)
        .values(
            "shop_id", "created_at", "total", "settled_from_credit",
            "confirmed_payment_at",
            "cash_amount", "mpesa_amount", "card_amount",
        )
    )
    expense_rows = list(
        Expense.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        ).values("shop_id", "created_at", "category", "amount", "amount_paid")
    )
    stock_rows = list(
        StockMovement.objects.filter(
            shop_id__in=shop_ids,
            movement_type="in",
            created_at__gte=start_dt,
            created_at__lt=end_dt,
            amount_paid__gt=0,
        ).values("shop_id", "created_at", "amount_paid")
    )
    shelf_item_shop = dict(
        ShelfItem.objects.filter(shop_id__in=shop_ids).values_list(
            "pk", "shop_id"
        )
    )
    shelf_rows = (
        list(
            ShelfStockMovement.objects.filter(
                shelf_item_id__in=list(shelf_item_shop),
                movement_type__in=("in", "register"),
                created_at__gte=start_dt,
                created_at__lt=end_dt,
                amount_paid__gt=0,
            ).values("shelf_item_id", "created_at", "amount_paid")
        )
        if shelf_item_shop
        else []
    )
    prepay_rows = list(
        CustomerPrepayment.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        ).values(
            "shop_id", "created_at", "amount",
            "cash_amount", "mpesa_amount", "card_amount",
        )
    )
    credit_paid_rows = list(
        ClientCreditAccountEvent.objects.filter(
            shop_id__in=shop_ids,
            kind__in=CREDIT_PAID_KINDS,
            occurred_at__gte=start_dt,
            occurred_at__lt=end_dt,
        ).values("shop_id", "occurred_at", "amount")
    )
    refund_rows = _refund_events(shop_ids, start_dt, end_dt)
    credit = ShopReceipt.objects.filter(
        shop_id__in=shop_ids,
        kind=CREDIT_KIND,
        status__in=COUNTED_STATUSES,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    ).aggregate(t=Sum("total"), paid=Sum("amount_paid"))
    gift_total = _dec(
        _all_receipts(start, end, kinds=(GIFT_KIND,), receipt_status=status)
        .filter(shop_id__in=shop_ids)
        .aggregate(t=Sum("total"))["t"]
    )

    def _new_cell():
        return {
            "sales": Decimal("0"),
            "receipts": 0,
            "prepayments": Decimal("0"),
            "credit_paid": Decimal("0"),
            "expenses": Decimal("0"),
            "stock": Decimal("0"),
            "shelf": Decimal("0"),
            "drawings": Decimal("0"),
            "refunds": Decimal("0"),
        }

    buckets = [_new_cell() for _ in windows]
    outside = {}  # shop_id -> same shape, for activity with no session

    def _add(shop_id, when, amount, kind):
        cell = None
        for i, (s, wstart, wend) in enumerate(windows):
            if s.shop_id == shop_id and wstart <= when < wend:
                cell = buckets[i]
                break
        if cell is None:
            cell = outside.setdefault(shop_id, _new_cell())
        if kind == "sales":
            cell["sales"] += amount
            cell["receipts"] += 1
        else:
            cell[kind] += amount

    tot = {
        key: Decimal("0")
        for key in ("sales", "cash", "mpesa", "card", "expenses",
                    "drawings", "stock", "shelf", "refunds",
                    "prepayments", "credit_paid")
    }
    tot["receipts"] = 0
    for r in sales_rows:
        amount = _dec(r["total"])
        tot["sales"] += amount
        tot["receipts"] += 1
        if r["settled_from_credit"]:
            # Money landed via credit account collections — the channels
            # stay zero here and the paid-day owns the activity window.
            when = r["confirmed_payment_at"] or r["created_at"]
            tender = Decimal("0")
        else:
            when = r["created_at"]
            tender = (
                _dec(r["cash_amount"])
                + _dec(r["mpesa_amount"])
                + _dec(r["card_amount"])
            )
            tot["cash"] += _dec(r["cash_amount"])
            tot["mpesa"] += _dec(r["mpesa_amount"])
            tot["card"] += _dec(r["card_amount"])
        _add(r["shop_id"], when, tender, "sales")
    for r in expense_rows:
        paid = r["amount_paid"] if r["amount_paid"] is not None else r["amount"]
        paid = _dec(paid)
        is_drawings = str(r["category"] or "").strip().lower() == (
            "owner_drawings"
        )
        kind = "drawings" if is_drawings else "expenses"
        tot[kind] += paid
        _add(r["shop_id"], r["created_at"], paid, kind)
    for r in stock_rows:
        paid = _dec(r["amount_paid"])
        tot["stock"] += paid
        _add(r["shop_id"], r["created_at"], paid, "stock")
    for r in shelf_rows:
        paid = _dec(r["amount_paid"])
        tot["shelf"] += paid
        _add(
            shelf_item_shop.get(r["shelf_item_id"]),
            r["created_at"],
            paid,
            "shelf",
        )
    for r in prepay_rows:
        paid = (
            _dec(r["cash_amount"])
            + _dec(r["mpesa_amount"])
            + _dec(r["card_amount"])
        ) or _dec(r["amount"])
        tot["prepayments"] += paid
        _add(r["shop_id"], r["created_at"], paid, "prepayments")
    for r in credit_paid_rows:
        paid = _dec(r["amount"])
        tot["credit_paid"] += paid
        _add(r["shop_id"], r["occurred_at"], paid, "credit_paid")
    for sid, when, amount in refund_rows:
        tot["refunds"] += amount
        _add(sid, when, amount, "refunds")

    names = _employee_names(
        [s.opened_by_id for s, _, _ in windows]
        + [s.closed_by_id for s, _, _ in windows if s.closed_by_id]
    )
    rows = []
    for (s, _, _), bucket in zip(windows, buckets):
        opened_local = timezone.localtime(s.opened_at)
        if s.closed_at is None:
            when = f"{opened_local:%d %b} · since {opened_local:%H:%M}"
            state = "Open"
            out_time = ""
        else:
            when = (
                f"{opened_local:%d %b} · {opened_local:%H:%M}"
                f"–{timezone.localtime(s.closed_at):%H:%M}"
            )
            state = "Closed"
            closed_local = timezone.localtime(s.closed_at)
            out_time = f"{closed_local:%H:%M}"
            if closed_local.date() != opened_local.date():
                out_time += f" · {closed_local:%d %b}"
        rows.append(
            {
                "id": s.pk,
                "shop": shops.get(s.shop_id, "?"),
                "session": when,
                "day": f"{opened_local:%d %b}",
                "in_time": f"{opened_local:%H:%M}",
                "out_time": out_time,
                "status": state,
                "cashier": names.get(s.opened_by_id) or "—",
                "sales": bucket["sales"],
                "receipts": bucket["receipts"],
                "in": _in_total(bucket),
                "out": _out_total(bucket),
            }
        )
    for sid, bucket in outside.items():
        rows.append(
            {
                "id": None,
                "shop": shops.get(sid, "?"),
                "session": "Outside sessions",
                "day": "Outside sessions",
                "in_time": "",
                "out_time": "",
                "status": "—",
                "cashier": "—",
                "sales": bucket["sales"],
                "receipts": bucket["receipts"],
                "in": _in_total(bucket),
                "out": _out_total(bucket),
            }
        )

    credit_total = _dec(credit["t"])
    return {
        "sales": tot["sales"],
        "receipts": tot["receipts"],
        "cash": tot["cash"],
        "mpesa": tot["mpesa"],
        "card": tot["card"],
        "prepayments": tot["prepayments"],
        "credit_paid": tot["credit_paid"],
        "expenses": tot["expenses"],
        "stock": tot["stock"],
        "shelf": tot["shelf"],
        "drawings": tot["drawings"],
        "refunds": tot["refunds"],
        "paid_out": _out_total(tot),
        "credit": credit_total,
        "credit_due": max(Decimal("0"), credit_total - _dec(credit["paid"])),
        "gift": gift_total,
        "rows": rows[:REPORT_ROW_CAP],
        "sessions_total": len(rows),
    }


def _all_items_sold_card(scope: str, start, end, status, cap: int = 120) -> dict:
    """items_sold card payload aggregated across every shop."""
    receipt_ids = _all_receipts(start, end, receipt_status=status).values("id")
    rows = [
        r
        for r in (
            ShopReceiptLine.objects.filter(receipt_id__in=receipt_ids)
            .values("item_name")
            .annotate(
                q=Sum(F("quantity") - F("returned_quantity")),
                t=Sum("line_total"),
            )
            .order_by("-q")
        )
        if (r["q"] or 0) > 0
    ]
    return {
        "label": scope,
        "items": [
            {
                "name": r["item_name"],
                "qty": qty_fmt(r["q"]),
                "total": money(r["t"]),
            }
            for r in rows[:cap]
        ],
    }


def company_stock_modes() -> dict:
    """Company stock modes — which movement ledgers exist.

    ``items`` = item stock tracking, ``shelf`` = shelf mode (the Settings →
    POS → Stock one-of toggles on ``CompanyPosSettings``).
    """
    row = CompanyPosSettings.objects.filter(pk=1).first()
    return {
        "items": bool(getattr(row, "enable_stock_tracking", True)),
        "shelf": bool(getattr(row, "enable_shelf_mode", False)),
    }


def shop_stock_modes(shop) -> dict:
    """Stock modes for one shop — company flags AND the shop's own override.

    Same rule as ``shops.services.shop_stock_mode_flags``: a shop running
    custom POS settings (``override_pos``) narrows the company toggles;
    shops on company defaults follow the company mode.
    """
    modes = company_stock_modes()
    row = ShopPosSettings.objects.filter(
        shop_id=shop.pk, override_pos=True
    ).first()
    if row is not None:
        modes["items"] = modes["items"] and bool(row.enable_stock_tracking)
        modes["shelf"] = modes["shelf"] and bool(row.enable_shelf_mode)
    return modes


def _movement_ledger(text: str, modes: dict) -> str:
    """'items' | 'shelf' — which ledger a movement query reads.

    A ``shelf``/``item`` keyword picks explicitly; a bare query defaults to
    the enabled ledger (item stock first when both are on).
    """
    low = (text or "").lower()
    if re.search(r"\bshelf\b", low):
        return "shelf"
    if re.search(r"\bitems?\b", low):
        return "items"
    if modes["items"]:
        return "items"
    if modes["shelf"]:
        return "shelf"
    return "items"


SHELF_REASON_LABELS = {
    "waste": "Waste",
    "transfer": "Transfer",
    "display": "Display",
    "return": "Supplier return",
    "trade_out": "Trade out",
    "trade_in": "Trade in",
    "gift": "Gift",
    "custom": "Custom",
}


def _all_shelf_movement_groups(start_dt, end_dt) -> list:
    """Card groups for shelf movements across every shop, newest first."""
    moves = list(
        ShelfStockMovement.objects.filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        ).order_by("-created_at", "-id")[:40]
    )
    items = {
        i.pk: i
        for i in ShelfItem.objects.filter(
            pk__in={m.shelf_item_id for m in moves}
        )
    }
    names = dict(
        Shop.objects.filter(
            pk__in={i.shop_id for i in items.values()}
        ).values_list("pk", "name")
    )
    groups = []
    for mv in moves:
        item = items.get(mv.shelf_item_id)
        shop = names.get(item.shop_id if item else 0, "?")
        if mv.movement_type == "out":
            head, kind = f"{shop} — Shelf out", "tout"
        elif mv.movement_type == "register":
            head, kind = f"{shop} — Registered", "tin"
        else:
            head, kind = f"{shop} — Shelf in", "tin"
        status = " · ".join(
            bit
            for bit in (
                SHELF_REASON_LABELS.get(mv.reason, mv.reason),
                (mv.note or "").strip(),
            )
            if bit
        )
        groups.append(
            {
                "kind": kind,
                "head": head,
                "status": status,
                "when": timezone.localtime(mv.created_at).strftime(
                    "%d %b, %H:%M"
                ),
                "items": [
                    {
                        "item": item.name if item else "?",
                        "qty": qty_fmt(mv.quantity),
                    }
                ],
            }
        )
    return groups


def _movement_disabled_reply(ledger: str, modes: dict):
    """Honest answer when the asked-for ledger's stock mode is off."""
    if ledger == "shelf" and not modes["shelf"]:
        if modes["items"]:
            return (
                "Shelf stock is off for this company — *stock movement* "
                "covers item stock."
            )
        return "Stock tracking is off for this company."
    if ledger == "items" and not modes["items"]:
        if modes["shelf"]:
            return "Item stock tracking is off — try *shelf stock movement*."
        return "Stock tracking is off for this company."
    return None


def reply_for_all(text: str, section_visible=None) -> str:
    """Assistant answers aggregated across every active shop.

    ``section_visible`` is an optional ``(section_key) -> bool`` predicate —
    the viewer's day-report section picks (``day_report_section_enabled``).
    Only the generated "report" honours it; other replies keep their fixed
    shape.
    """
    t = (text or "").strip()
    low = t.lower()
    _sec = section_visible or (lambda _key: True)
    start, end, label = _date_range(t)
    status = resolve_receipt_status(t)
    scope = _sales_label(label, status)
    start_dt, end_dt = _window(start, end)
    shops = {s.pk: s.name for s in Shop.objects.filter(
        is_suspended=False, is_hidden=False
    )}

    if re.search(r"\b(credits?|debts?|owed|balances?)\b", low):
        win = _time_window(t)
        qs = ShopReceipt.objects.filter(
            kind=CREDIT_KIND, status__in=COUNTED_STATUSES
        )
        if win:
            qs = qs.filter(
                created_at__gte=start_dt, created_at__lt=end_dt
            )
        qs = qs.annotate(balance=F("total") - F("amount_paid")).filter(
            balance__gt=0
        )
        rows = qs.values("shop_id").annotate(
            t=Sum("balance"), c=Count("id")
        ).order_by("-t")
        total = qs.aggregate(t=Sum("balance"))["t"] or 0
        if not total:
            if win:
                return f"No outstanding credit issued {label} across all shops."
            return "No outstanding customer credit across all shops."
        scope = f" issued {label}" if win else ""
        lines = [
            f"*Outstanding credit{scope} — ALL SHOPS*",
            f"*{money(total)}* owed across {qs.count()} receipts",
            "",
        ]
        for r in rows:
            lines.append(
                f"- {shops.get(r['shop_id'], '?')}: {money(r['t'])}"
                f" ({r['c']} receipt{'s' if r['c'] != 1 else ''})"
            )
        return "\n".join(lines)

    if re.search(r"\b(expense|expenses|spent|spending|costs?)\b", low):
        rows = Expense.objects.filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        ).values("shop_id").annotate(t=Sum("amount")).order_by("-t")
        if not rows:
            return f"No expenses recorded across all shops {label}."
        total = sum(r["t"] or 0 for r in rows)
        lines = [
            f"*Expenses — ALL SHOPS ({label})*",
            f"Total: *{money(total)}*",
            "",
        ] + [f"- {shops.get(r['shop_id'], '?')}: {money(r['t'])}" for r in rows]
        return "\n".join(lines)

    if re.search(r"\b(prepayments?|deposits?|advancements?|advances?)\b", low):
        rows = (
            CustomerPrepayment.objects.filter(
                created_at__gte=start_dt, created_at__lt=end_dt
            )
            .values("shop_id")
            .annotate(t=Sum("amount"), c=Count("id"))
            .order_by("-t")
        )
        if not rows:
            return f"No customer prepayments across all shops {label}."
        total = sum(r["t"] or 0 for r in rows)
        lines = [
            f"*Customer prepayments — ALL SHOPS ({label})*",
            f"Total: *{money(total)}*",
            "",
        ] + [
            f"- {shops.get(r['shop_id'], '?')}: {money(r['t'])}"
            f" ({r['c']} payment{'s' if r['c'] != 1 else ''})"
            for r in rows
        ]
        return "\n".join(lines)

    if re.search(r"\b(stock movements?|movements?|transfers?|moved)\b", low):
        modes = company_stock_modes()
        ledger = _movement_ledger(t, modes)
        disabled = _movement_disabled_reply(ledger, modes)
        if disabled is not None:
            return disabled
        if ledger == "shelf":
            groups = _all_shelf_movement_groups(start_dt, end_dt)
            if not groups:
                return f"No shelf stock movements across all shops {label}."
            lines = [
                f"*Shelf stock movement — ALL SHOPS ({label})*",
                "",
            ]
            for g in groups:
                it = g["items"][0]
                lines.append(
                    f"{g['head']}: {it['item']} ×{it['qty']}"
                    + (f" ({g['status']})" if g["status"] else "")
                )
            return "\n".join(lines)
        moves = (
            StockMovement.objects.filter(
                created_at__range=(start_dt, end_dt)
            )
            .select_related()
            .order_by("-created_at")[:20]
        )
        if not moves:
            return f"No stock movements across all shops {label}."
        lines = [f"*Stock movement — ALL SHOPS ({label})*", ""]
        for m in moves:
            src = shops.get(m.shop_id, "?")
            dst = shops.get(m.requested_from_shop_id, "")
            who = f"{src} → {dst}" if dst else src
            lines.append(f"{who}: {m.movement_type} ({m.request_status or 'done'})")
        return "\n".join(lines)

    if re.search(r"\b(summary|report|overview|snapshot|performance)\b", low):
        rep = _all_shops_report(start, end, status, section_visible=_sec)
        money_flow_on = _sec("day_report_sec_money_flow")
        out_parts = []
        if _sec("day_report_sec_expenses"):
            out_parts.append(f"expenses {money(rep['expenses'])}")
        if _sec("day_report_sec_item_stock"):
            out_parts.append(f"stock {money(rep['stock'])}")
        if _sec("day_report_sec_shelf_stock") and rep["shelf"]:
            out_parts.append(f"shelf {money(rep['shelf'])}")
        if _sec("day_report_sec_drawings"):
            out_parts.append(f"drawings {money(rep['drawings'])}")
        if money_flow_on and rep["refunds"]:
            out_parts.append(f"refunds {money(rep['refunds'])}")
        lines = [f"*Report — ALL SHOPS ({scope})*"]
        if money_flow_on:
            lines += [
                f"Sales: *{money(rep['sales'])}* across "
                f"{rep['receipts']} receipts",
                f"Cash {money(rep['cash'])}  |  M-Pesa {money(rep['mpesa'])}"
                f"  |  Card {money(rep['card'])}",
            ]
        if _sec("day_report_sec_prepayments") and rep["prepayments"]:
            lines.append(
                f"Customer prepayments: {money(rep['prepayments'])}"
            )
        if _sec("day_report_sec_credit_payments") and rep["credit_paid"]:
            lines.append(
                f"Credit payments collected: {money(rep['credit_paid'])}"
            )
        if out_parts:
            lines.append(
                f"Paid out: {money(rep['paid_out'])} — {' · '.join(out_parts)}"
            )
        if _sec("day_report_sec_not_till") and rep["credit"]:
            credit_line = f"Credit issued: {money(rep['credit'])}"
            if rep["credit_due"]:
                credit_line += f" ({money(rep['credit_due'])} still due)"
            lines.append(credit_line)
        if _sec("day_report_sec_gift") and rep["gift"]:
            lines.append(f"Gifts issued: {money(rep['gift'])}")
        if rep["rows"]:
            in_on = (
                money_flow_on
                or _sec("day_report_sec_prepayments")
                or _sec("day_report_sec_credit_payments")
            )
            lines += ["", "*Sessions*"]
            for r in rep["rows"]:
                row_bits = []
                if in_on:
                    row_bits.append(f"in {money(r['in'])}")
                if out_parts:
                    row_bits.append(f"out {money(r['out'])}")
                tail = f" — {', '.join(row_bits)}" if row_bits else ""
                lines.append(
                    f"- {r['shop']} · {r['session']} · {r['cashier']} · "
                    f"{r['status']}{tail}"
                )
            hidden = rep["sessions_total"] - len(rep["rows"])
            if hidden > 0:
                lines.append(f"...plus {hidden} more sessions.")
        if _sec("day_report_sec_items_sold"):
            lines.append(
                f"%%items|{start.isoformat()}|{end.isoformat()}|{status}%%"
            )
        return "\n".join(lines)

    if items_intent_matches(None, t):
        receipt_ids = _all_receipts(start, end, receipt_status=status).values("id")
        rows = list(
            ShopReceiptLine.objects.filter(receipt_id__in=receipt_ids)
            .values("item_name")
            .annotate(
                q=Sum(F("quantity") - F("returned_quantity")),
                t=Sum("line_total"),
            )
            .order_by("-q")
        )
        rows = [r for r in rows if (r["q"] or 0) > 0]
        if not rows:
            return f"No items sold across all shops {scope}."
        lines = [
            f"*Items sold — ALL SHOPS ({scope})* — {len(rows)} items",
            "",
        ]
        for i, r in enumerate(rows[:80], 1):
            lines.append(
                f"{i}. {r['item_name']} — x{qty_fmt(r['q'])} ({money(r['t'])})"
            )
        if len(rows) > 80:
            lines.append(f"...plus {len(rows) - 80} more items.")
        return "\n".join(lines)

    if re.search(r"\b(sale|sales|sell|sold|revenue|income)\b", low):
        agg = _all_receipts(start, end, receipt_status=status).aggregate(
            total=Sum("total"), count=Count("id"), **_tender_exprs()
        )
        count = agg["count"] or 0
        if not count:
            return f"No sales recorded across all shops {scope}."
        per_shop = (
            _all_receipts(start, end, receipt_status=status)
            .values("shop_id")
            .annotate(t=Sum("total"), c=Count("id"))
            .order_by("-t")
        )
        lines = [
            f"*Sales — ALL SHOPS ({scope})*",
            f"Total: *{money(agg['total'])}* across {count} receipts",
            f"Cash {money(agg['cash'])}  |  M-Pesa {money(agg['mpesa'])}  |  Card {money(agg['card'])}",
            "",
        ]
        for r in per_shop:
            lines.append(f"- {shops.get(r['shop_id'], '?')}: {money(r['t'])}")
        lines.append(f"%%items|{start.isoformat()}|{end.isoformat()}|{status}%%")
        return "\n".join(lines)

    default = analytics_receipt_default()
    return (
        "*All-shops view*\n\n"
        "Ask me about every shop combined:\n"
        "- sales today / this week / this month\n"
        f"- sales today pending / confirmed (default *{default}*)\n"
        "- credits — outstanding balances per shop\n"
        "- expenses today / this month\n"
        "- prepayments — customer deposits per shop\n"
        "- stock movement — item stock; *shelf stock movement* for shelf\n"
        "- report — sessions & money per shop\n\n"
        "Pick a single shop from the picker for its chats and details."
    )


def card_for_all(text: str, section_visible=None):
    """Structured card payload for the all-shops assistant.

    Produces the same card shapes the shop assistant uses (see `card_meta`)
    so `/all/` replies render as the same wa-card widgets — plus a shop
    column where the company-wide list benefits from it. Mirrors the
    intent order of `reply_for_all` exactly.

    ``section_visible`` gates the "report" card on the viewer's day-report
    section picks — a switched-off section's keys are omitted entirely.
    """
    t = (text or "").strip()
    low = t.lower()
    _sec = section_visible or (lambda _key: True)
    start, end, label = _date_range(t)
    status = resolve_receipt_status(t)
    scope = _sales_label(label, status)
    start_dt, end_dt = _window(start, end)

    def _shop_names(qs=None, field="shop_id"):
        ids = (
            qs.values_list(field, flat=True)
            if qs is not None
            else Shop.objects.filter(
                is_suspended=False, is_hidden=False
            ).values_list("pk", flat=True)
        )
        return dict(
            Shop.objects.filter(pk__in=list(ids)).values_list("pk", "name")
        )

    if re.search(r"\b(credits?|debts?|owed|balances?)\b", low):
        win = _time_window(t)
        qs = ShopReceipt.objects.filter(
            kind=CREDIT_KIND, status__in=COUNTED_STATUSES
        )
        if win:
            qs = qs.filter(
                created_at__gte=start_dt, created_at__lt=end_dt
            )
        qs = qs.annotate(balance=F("total") - F("amount_paid")).filter(
            balance__gt=0
        )
        if not qs.exists():
            return None
        names = _shop_names(qs)
        return {
            "credits": {
                "label": label if win else "",
                "total": money(qs.aggregate(t=Sum("balance"))["t"]),
                "count": qs.count(),
                "rows": _credit_rows(qs, names),
            }
        }

    if re.search(r"\b(expense|expenses|spent|spending|costs?)\b", low):
        qs = Expense.objects.filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        )
        agg = qs.aggregate(
            total=Sum("amount"), paid=Sum("amount_paid"), count=Count("id")
        )
        if not agg["count"]:
            return None
        names = _shop_names(qs)
        return {
            "expenses": {
                "label": label,
                "total": money(agg["total"]),
                "count": agg["count"],
                "paid": money(agg["paid"]),
                "unpaid": money((agg["total"] or 0) - (agg["paid"] or 0)),
                "rows": [
                    {
                        "name": e.name,
                        "shop": names.get(e.shop_id, ""),
                        "cat": e.category,
                        "amount": money(e.amount),
                        "status": e.payment_status,
                    }
                    for e in qs.order_by("-created_at")[:60]
                ],
            }
        }

    if re.search(r"\b(stock movements?|movements?|transfers?|moved)\b", low):
        modes = company_stock_modes()
        ledger = _movement_ledger(t, modes)
        if _movement_disabled_reply(ledger, modes) is not None:
            return None
        if ledger == "shelf":
            groups = _all_shelf_movement_groups(start_dt, end_dt)
            if not groups:
                return None
            return {
                "movement": {
                    "title": "Shelf stock movement",
                    "label": label,
                    "groups": groups,
                }
            }
        moves = list(
            StockMovement.objects.filter(
                created_at__range=(start_dt, end_dt)
            ).order_by("-created_at")[:30]
        )
        if not moves:
            return None
        names = _shop_names()
        line_map = {}
        for l in StockMovementLine.objects.filter(
            movement_id__in=[m.pk for m in moves]
        ).order_by("pk"):
            line_map.setdefault(l.movement_id, []).append(l)
        groups = []
        for m in moves:
            src = names.get(m.shop_id, "?")
            if m.movement_type == "in":
                head, kind = f"{src} — Stock in", "in"
            elif m.movement_type == "out":
                head, kind = f"{src} — Stock out", "out"
            else:
                dst = names.get(m.requested_from_shop_id, "?")
                head, kind = f"{src} → {dst}", "tout"
            groups.append(
                {
                    "kind": kind,
                    "head": head,
                    "status": (
                        m.request_status
                        if m.movement_type == "request"
                        else ""
                    ),
                    "when": timezone.localtime(m.created_at).strftime(
                        "%d %b, %H:%M"
                    ),
                    "items": [
                        {"item": l.item_name, "qty": qty_fmt(l.quantity)}
                        for l in line_map.get(m.pk, [])
                    ],
                }
            )
        return {"movement": {"label": label, "groups": groups}}

    if re.search(r"\b(summary|report|overview|snapshot|performance)\b", low):
        from django.urls import reverse

        rep = _all_shops_report(start, end, status, section_visible=_sec)
        report = {
            "label": scope,
            "sessions_total": rep["sessions_total"],
        }
        money_flow_on = _sec("day_report_sec_money_flow")
        if money_flow_on:
            report.update(
                {
                    "sales": money(rep["sales"]),
                    "receipts": rep["receipts"],
                    "cash": money(rep["cash"]),
                    "mpesa": money(rep["mpesa"]),
                    "card": money(rep["card"]),
                    "refunds": money(rep["refunds"]),
                }
            )
        if _sec("day_report_sec_prepayments") and rep["prepayments"]:
            report["prepayments"] = money(rep["prepayments"])
        if _sec("day_report_sec_credit_payments") and rep["credit_paid"]:
            report["credit_paid"] = money(rep["credit_paid"])
        if _sec("day_report_sec_gift") and rep["gift"]:
            report["gift"] = money(rep["gift"])
        out_on = (
            money_flow_on
            or _sec("day_report_sec_expenses")
            or _sec("day_report_sec_item_stock")
            or _sec("day_report_sec_shelf_stock")
            or _sec("day_report_sec_drawings")
        )
        if out_on:
            report["paid_out"] = money(rep["paid_out"])
            if _sec("day_report_sec_expenses"):
                report["expenses"] = money(rep["expenses"])
            if _sec("day_report_sec_item_stock"):
                report["stock"] = money(rep["stock"])
            if _sec("day_report_sec_shelf_stock") and rep["shelf"]:
                report["shelf"] = money(rep["shelf"])
            if _sec("day_report_sec_drawings"):
                report["drawings"] = money(rep["drawings"])
        if _sec("day_report_sec_not_till"):
            report["credit"] = money(rep["credit"])
            report["credit_due"] = money(rep["credit_due"])
        report["rows"] = [
                    {
                        "shop": r["shop"],
                        "session": r["day"],
                        "status": r["status"],
                        "cashier": r["cashier"],
                        "in_time": r["in_time"] or "—",
                        "out_time": r["out_time"] or "—",
                        "href": (
                            reverse(
                                "chat:all_session_report",
                                kwargs={"session_id": r["id"]},
                            )
                            if r.get("id")
                            else ""
                        ),
                    }
                    for r in rep["rows"]
        ]
        return {"report": report}

    if items_intent_matches(None, t):
        return {
            "items_sold": _all_items_sold_card(scope, start, end, status)
        }

    if re.search(r"\b(sale|sales|sell|sold|revenue|income)\b", low):
        agg = _all_receipts(start, end, receipt_status=status).aggregate(
            total=Sum("total"), count=Count("id"), **_tender_exprs()
        )
        if not (agg["count"] or 0):
            return None
        credit = (
            _all_receipts(
                start, end, kinds=(CREDIT_KIND,), receipt_status=status
            ).aggregate(t=Sum("total"))["t"]
            or 0
        )
        card = {
            "sales": {
                "label": scope,
                "total": money(agg["total"]),
                "receipts": agg["count"],
                "cash": money(agg["cash"]),
                "mpesa": money(agg["mpesa"]),
                "card": money(agg["card"]),
                "credit": money(credit),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "receipt_status": status,
            }
        }
        # Items stay behind the card's "View items sold" link — see
        # `card_meta`.
        return card
    return None


def reply_for(shop: Shop, text: str) -> str:
    t = (text or "").strip()
    if not t:
        return _fallback_reply()
    low = t.lower()

    if GREETING_RE.match(t):
        return (
            f"Hello {shop.name}! What would you like to know?\n"
            "Try *sales today*, *low stock*, *top sellers* or *help*."
        )
    if THANKS_RE.match(t):
        return "You're welcome! Ask me anything else about the shop."
    if re.search(r"\b(stock|stok|price|bei|have|left|remaining)\b", low) and re.search(
        r"\b(low|running|finish|out|restock)\b", low
    ):
        return _low_stock_reply(shop, t)
    if re.search(r"\b(low stock|running out|running low|restock|finish|almost done)\b", low):
        return _low_stock_reply(shop, t)
    if re.search(r"\b(top|best|fast|most)\b.*\b(sell|selling|seller|moving|item|product)s?\b", low) or re.search(
        r"\bbest sellers?\b", low
    ):
        return _top_sellers_reply(shop, t)
    if re.search(r"\b(receipt|receipts|invoice)\b", low):
        return _receipts_reply(shop, t)
    # Items-sold before the broad sales regex (which matches "sold").
    if items_intent_matches(shop, t):
        return _items_sold_reply(shop, t)
    if re.search(r"\b(sale|sales|sell|selling|sold|revenue|income|till|collection|collections)\b", low):
        return _sales_reply(shop, t)
    if re.search(r"\b(expense|expenses|spent|spending|costs?)\b", low):
        return _expenses_reply(shop, t)
    if re.search(r"\b(prepayments?|deposits?|advancements?|advances?)\b", low):
        return _prepayments_reply(shop, t)
    if re.search(r"\b(credits?|debts?|owe|owed|deni|denis|balances?)\b", low):
        return _credit_reply(shop, t)
    if re.search(r"\b(stock movements?|movements?|transfers?|moved)\b", low):
        return _stock_movement_reply(shop, t)
    if re.search(r"\b(summary|report|overview|snapshot|today's|todays|performance)\b", low):
        return _daily_summary_reply(shop, t)
    if re.search(r"\b(open|opened|close|closed|day status|trading day|working)\b", low):
        return _day_status_reply(shop, t)
    if re.search(r"\b(shops|branches|stores|locations)\b", low):
        return _shops_reply(shop, t)
    if re.search(
        r"\b(stock|price|bei|have|many|much|left|remaining|available|availability)\b",
        low,
    ):
        return _stock_reply(shop, t)
    if HELP_RE.search(t):
        return _help_reply(shop)
    return _fallback_reply()


BOT_SUGGESTIONS = [
    "Sales (+ items)",
    "Credits",
    "Expenses",
    "Stock movement",
    "Summary",
]

# Topics whose period picker shows pending/confirmed receipt scope.
RECEIPT_STATUS_TOPICS = frozenset({"sales", "items", "summary", "report"})