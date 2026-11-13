"""MY-SHOP Assistant — rule-based data chatbot.

A shop sends a message in its assistant thread; `reply_for` inspects the
text, queries the shared MY-SHOP tables (scoped to that shop), and returns a
WhatsApp-style reply using *bold* markers the client renders natively.

No external AI service — everything is deterministic and offline-friendly.
"""

import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .myshop_models import (
    Client,
    CompanyAnalyticsSettings,
    Expense,
    Item,
    Shop,
    ShopDaySession,
    ShopItemPrice,
    ShopReceipt,
    ShopReceiptLine,
    ShopStock,
    StockMovement,
    StockMovementLine,
)

COUNTED_STATUSES = ("active", "confirmed", "partial_return")
ANALYTICS_PENDING = "pending"
ANALYTICS_CONFIRMED = "confirmed"
SALE_KIND = "sale"
CREDIT_KIND = "credit"

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
    """Company default from MY-SHOP analytics settings (`pending` | `confirmed`)."""
    try:
        row = CompanyAnalyticsSettings.objects.filter(pk=1).first()
        value = ((row.receipt_data_source if row else "") or "").strip().lower()
        if value in (ANALYTICS_PENDING, ANALYTICS_CONFIRMED):
            return value
    except Exception:
        pass
    return ANALYTICS_PENDING


def resolve_receipt_status(text: str | None = None) -> str:
    """Pending/confirmed for sales & item analytics — message override or company default."""
    low = (text or "").lower()
    if re.search(r"\bconfirmed\b", low):
        return ANALYTICS_CONFIRMED
    if re.search(r"\b(pending|unconfirmed)\b", low):
        return ANALYTICS_PENDING
    return analytics_receipt_default()


def _analytics_statuses(status_key: str | None) -> tuple[str, ...]:
    """Map analytics scope → ShopReceipt.status values (same as MY-SHOP)."""
    key = (status_key or "").strip().lower()
    if key in (ANALYTICS_PENDING, "active"):
        return ("active",)
    if key == ANALYTICS_CONFIRMED:
        return ("confirmed", "partial_return")
    return _analytics_statuses(analytics_receipt_default())


def _status_scope_label(status_key: str) -> str:
    return (
        "confirmed receipts"
        if status_key == ANALYTICS_CONFIRMED
        else "pending receipts"
    )


def _sales_label(date_label: str, status_key: str) -> str:
    return f"{date_label} · {_status_scope_label(status_key)}"


def _receipts(shop: Shop, start, end, kinds=(SALE_KIND,), receipt_status=None):
    start_dt, end_dt = _window(start, end)
    statuses = _analytics_statuses(receipt_status or analytics_receipt_default())
    return ShopReceipt.objects.filter(
        shop_id=shop.pk,
        kind__in=kinds,
        status__in=statuses,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    )


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
            "receipt": r.receipt_number,
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
        cash=Sum("cash_amount"),
        mpesa=Sum("mpesa_amount"),
        paid=Sum("amount_paid"),
    )
    count = agg["count"] or 0
    if not count:
        return f"No sales recorded for {shop.name} {scope}."
    lines = [
        f"*Sales — {shop.name} ({scope})*",
        f"Total: *{money(agg['total'])}* across {count} receipt{'s' if count != 1 else ''}",
        f"Cash {money(agg['cash'])}  |  M-Pesa {money(agg['mpesa'])}",
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
        lines.append(f"- {who}: {money(r.balance)} on {r.receipt_number}{due}")
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
    items = Item.objects.filter(is_suspended=False)
    for word in query.split():
        items = items.filter(name__icontains=word)
    items = list(items[:4])
    if not items:
        return (
            f"I couldn't find an item matching \"{query}\" in the catalogue. "
            "Check the spelling or try a shorter name."
        )
    lines = []
    for item in items:
        stock_row = ShopStock.objects.filter(shop_id=shop.pk, item_id=item.pk).first()
        mine = stock_row.quantity if stock_row else Decimal("0")
        price = _price_for(item, shop)
        lines.append(
            f"*{item.name}* ({item.category})\n"
            f"In stock at {shop.name}: *{qty_fmt(mine)}*  |  Price: {money(price)}  |  "
            f"All shops: {qty_fmt(item.stock)}"
        )
    return "\n\n".join(lines)


def _low_stock_reply(shop: Shop, text: str) -> str:
    rows = (
        ShopStock.objects.filter(shop_id=shop.pk)
        .select_related()
    )
    item_ids = [r.item_id for r in rows]
    items = {i.pk: i for i in Item.objects.filter(pk__in=item_ids, is_suspended=False)}
    low = []
    for r in rows:
        item = items.get(r.item_id)
        if not item:
            continue
        threshold = r.low_stock_threshold or 0
        if threshold and r.quantity <= threshold:
            low.append((item.name, r.quantity, threshold))
        elif not threshold and r.quantity <= 5:
            low.append((item.name, r.quantity, 5))
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
            f"*Receipt {r.receipt_number}* — {r.kind}",
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
            f"- {r.receipt_number} ({r.kind}) — {money(r.total)} — "
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
    lines = [
        f"*{scope.capitalize()} at {shop.name}*",
        f"Sales: *{money(sales['t'])}* ({sales['c'] or 0} receipts)",
        f"Expenses: {money(expenses['t'])}",
        f"Outstanding credit: {money(credit['t'])}",
    ]
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
        "- summary — quick snapshot of today\n"
        "- credit — outstanding customer balances\n\n"
        "*Stock & products*\n"
        "- stock <item> — on-hand quantity, e.g. *stock sugar*\n"
        "- price of <item>\n"
        "- low stock — items below their alert level\n"
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


def _stock_movement_rows(shop: Shop, text: str):
    """(label, rows) — flat movement line dicts for the text reply."""
    label, groups = _stock_movement_groups(shop, text)
    rows = []
    for g in groups:
        for it in g["items"]:
            rows.append(
                {
                    "kind": g["kind"],
                    "head": g["head"],
                    "item": it["item"],
                    "qty": it["qty"],
                    "status": g["status"],
                }
            )
    return label, rows


def _stock_movement_reply(shop: Shop, text: str) -> str:
    label, rows = _stock_movement_rows(shop, text)
    if not rows:
        return f"No stock movements for {shop.name} {label}."
    lines = [f"*Stock movement — {shop.name} ({label})*", ""]
    for r in rows:
        sign = "+IN" if r["kind"] == "in" else ("-OUT" if r["kind"] == "out" else r["head"])
        lines.append(
            f"{sign}: {r['item']} ×{r['qty']}"
            + (f" ({r['status']})" if r["status"] else "")
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


def items_intent_matches(shop, text: str) -> bool:
    low = (text or "").lower()
    return bool(
        ITEMS_INTENT_RE.search(low)
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
            total=Sum("total"), count=Count("id"),
            cash=Sum("cash_amount"), mpesa=Sum("mpesa_amount"),
        )
        if not (agg["count"] or 0):
            return None
        credit = (
            _receipts(
                shop, start, end, kinds=(CREDIT_KIND,), receipt_status=status
            ).aggregate(t=Sum("total"))["t"]
            or 0
        )
        return {
            "sales": {
                "label": _sales_label(label, status),
                "total": money(agg["total"]),
                "receipts": agg["count"],
                "cash": money(agg["cash"]),
                "mpesa": money(agg["mpesa"]),
                "credit": money(credit),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "receipt_status": status,
            }
        }

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
        label, groups = _stock_movement_groups(shop, t)
        if not groups:
            return None
        return {"movement": {"label": label, "groups": groups}}

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
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    )


def reply_for_all(text: str) -> str:
    """Assistant answers aggregated across every active shop."""
    t = (text or "").strip()
    low = t.lower()
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

    if re.search(r"\b(stock movements?|movements?|transfers?|moved)\b", low):
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
        sales = _all_receipts(start, end, receipt_status=status).aggregate(
            t=Sum("total"), c=Count("id")
        )
        expenses = Expense.objects.filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        ).aggregate(t=Sum("amount"))
        credit = (
            ShopReceipt.objects.filter(kind=CREDIT_KIND, status__in=COUNTED_STATUSES)
            .annotate(balance=F("total") - F("amount_paid"))
            .filter(balance__gt=0)
            .aggregate(t=Sum("balance"))
        )
        per_shop = (
            _all_receipts(start, end, receipt_status=status)
            .values("shop_id")
            .annotate(t=Sum("total"))
            .order_by("-t")
        )
        lines = [
            f"*{scope.capitalize()} at ALL SHOPS*",
            f"Sales: *{money(sales['t'])}* ({sales['c'] or 0} receipts)",
            f"Expenses: {money(expenses['t'])}",
            f"Outstanding credit: {money(credit['t'])}",
            "",
        ]
        for r in per_shop:
            lines.append(f"- {shops.get(r['shop_id'], '?')}: {money(r['t'])}")
        lines.append(f"%%items|{start.isoformat()}|{end.isoformat()}|{status}%%")
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
            total=Sum("total"), count=Count("id"),
            cash=Sum("cash_amount"), mpesa=Sum("mpesa_amount"),
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
            f"Cash {money(agg['cash'])}  |  M-Pesa {money(agg['mpesa'])}",
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
        "- stock movement\n"
        "- summary — company snapshot\n\n"
        "Pick a single shop from the picker for its chats and details."
    )


def card_for_all(text: str):
    """Structured card payload for the all-shops assistant.

    Produces the same card shapes the shop assistant uses (see `card_meta`)
    so `/all/` replies render as the same wa-card widgets — plus a shop
    column where the company-wide list benefits from it. Mirrors the
    intent order of `reply_for_all` exactly.
    """
    t = (text or "").strip()
    low = t.lower()
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
        sales = _all_receipts(start, end, receipt_status=status).aggregate(
            t=Sum("total"), c=Count("id")
        )
        expenses = Expense.objects.filter(
            created_at__gte=start_dt, created_at__lt=end_dt
        ).aggregate(t=Sum("amount"))
        credit = (
            ShopReceipt.objects.filter(
                kind=CREDIT_KIND, status__in=COUNTED_STATUSES
            )
            .annotate(balance=F("total") - F("amount_paid"))
            .filter(balance__gt=0)
            .aggregate(t=Sum("balance"))
        )
        return {
            "summary": {
                "label": scope,
                "sales": money(sales["t"]),
                "receipts": sales["c"] or 0,
                "expenses": money(expenses["t"]),
                "credit": money(credit["t"]),
            }
        }

    if items_intent_matches(None, t):
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
            "items_sold": {
                "label": scope,
                "items": [
                    {
                        "name": r["item_name"],
                        "qty": qty_fmt(r["q"]),
                        "total": money(r["t"]),
                    }
                    for r in rows[:120]
                ],
            }
        }

    if re.search(r"\b(sale|sales|sell|sold|revenue|income)\b", low):
        agg = _all_receipts(start, end, receipt_status=status).aggregate(
            total=Sum("total"), count=Count("id"),
            cash=Sum("cash_amount"), mpesa=Sum("mpesa_amount"),
        )
        if not (agg["count"] or 0):
            return None
        credit = (
            _all_receipts(
                start, end, kinds=(CREDIT_KIND,), receipt_status=status
            ).aggregate(t=Sum("total"))["t"]
            or 0
        )
        return {
            "sales": {
                "label": scope,
                "total": money(agg["total"]),
                "receipts": agg["count"],
                "cash": money(agg["cash"]),
                "mpesa": money(agg["mpesa"]),
                "credit": money(credit),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "receipt_status": status,
            }
        }
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
    "Sales",
    "Items",
    "Credits",
    "Expenses",
    "Stock movement",
    "Summary",
]

# Topics whose period picker shows pending/confirmed receipt scope.
RECEIPT_STATUS_TOPICS = frozenset({"sales", "items", "summary"})