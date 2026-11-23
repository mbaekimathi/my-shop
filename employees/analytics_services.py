"""Decision-oriented analytics for the Analytics workspace module."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

logger = logging.getLogger(__name__)

from django.db.models import (
    Case,
    Count,
    DateTimeField,
    DecimalField,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest, TruncDay, TruncHour, TruncMonth
from django.http import Http404
from django.utils import timezone

from employees.models import EmployeeProfile, EmployeeStatus
from items.models import (
    Item,
    ShelfItem,
    ShelfStockMovement,
    ShelfStockMovementType,
    ShopStock,
    StockEntrySource,
    StockMovement,
    StockMovementLine,
    StockMovementType,
    StockOutReason,
    StockPaymentStatus,
    StockRequestStatus,
    Supplier,
)
from items.services import actionable_shops_for_profile
from shops.models import (
    Client,
    ClientCreditAccountEvent,
    ClientCreditAccountEventKind,
    CustomerPrepayment,
    Expense,
    ExpenseCategory,
    ExpensePaymentStatus,
    ExpenseSupplier,
    ShopDaySession,
    ShopDelivery,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)
from shops.services import (
    _qty_label,
    _qty_number,
    format_simple_doc_number,
    get_effective_pos_settings,
    prepayment_doc_code,
    receipt_tally_code,
)

ANALYTICS_SECTIONS = (
    {
        "slug": "overview",
        "label": "Overview",
        "icon": "layout-grid",
        "summary": "Company totals then per shop — sales, credits, stock, expenses, and profit.",
    },
    {
        "slug": "revenue",
        "label": "Revenue",
        "icon": "banknote",
        "summary": "Sales and credits by shop — stock, expenses, and profit.",
    },
    {
        "slug": "balances",
        "label": "Cashiers",
        "icon": "scale",
        "summary": "Cashier day report — openings, sales, paid out, and expected vs counted closing per cashier.",
    },
    {
        "slug": "sales",
        "label": "Sales",
        "icon": "shopping-bag",
        "summary": "Sales by shop — cash, M-Pesa, paid total, unpaid, general total, stock, expected and actual profit.",
    },
    {
        "slug": "credits",
        "label": "Credits",
        "icon": "credit-card",
        "summary": "Items on credit by shop — qty, credit amount, % paid back, and top credit day.",
    },
    {
        "slug": "items",
        "label": "Items",
        "icon": "tags",
        "summary": "Items sold by shop — units and value per shop, plus totals.",
    },
    {
        "slug": "stock",
        "label": "Stock",
        "icon": "package",
        "summary": "On-hand stock by shop — quantity per shop, plus totals.",
    },
    {
        "slug": "supply",
        "label": "Supply analytics",
        "icon": "truck",
        "summary": "Buy items suppliers — deliveries, spend, and price history per item.",
    },
    {
        "slug": "deliveries",
        "label": "Deliveries",
        "icon": "bike",
        "summary": "Customer deliveries on the map — rider activity and completed jobs.",
    },
    {
        "slug": "quotations",
        "label": "Quotations",
        "icon": "file-text",
        "summary": "Quotations by shop — count and value, plus totals.",
    },
    {
        "slug": "gifts",
        "label": "Gifts",
        "icon": "gift",
        "summary": "Gifts issued by shop — count and value, plus totals.",
    },
    {
        "slug": "tradings",
        "label": "Tradings",
        "icon": "repeat",
        "summary": "Client trade accounts — open a client to see their trade ins and trade outs.",
    },
    {
        "slug": "clients",
        "label": "Clients",
        "icon": "contact",
        "summary": "All clients — sales, credits, and quotations by shop, with filters.",
    },
    {
        "slug": "employees",
        "label": "Employees",
        "icon": "users",
        "summary": "Cashier sales by shop — receipts and value, plus totals.",
    },
    {
        "slug": "suppliers",
        "label": "Suppliers",
        "icon": "truck",
        "summary": "All suppliers by shop — entries and outstanding balance, plus totals.",
    },
    {
        "slug": "expenses",
        "label": "Expenses",
        "icon": "wallet",
        "summary": "Expense categories by shop — entries and amount, plus suppliers per expense.",
    },
    {
        "slug": "receipts",
        "label": "Receipts",
        "icon": "receipt",
        "summary": "Browse all receipts by type and period across shops.",
    },
)

ANALYTICS_SECTION_BY_SLUG = {row["slug"]: row for row in ANALYTICS_SECTIONS}

ANALYTICS_DASHBOARD_SECTION_SLUGS = frozenset({"suppliers", "clients"})

ANALYTICS_LIST_TABLE_SECTIONS = frozenset(
    {
        "revenue",
        "balances",
        "sales",
        "items",
        "stock",
        "supply",
        "credits",
        "gifts",
        "tradings",
        "clients",
        "employees",
        "suppliers",
        "expenses",
        "receipts",
    }
)


def _money(value) -> str:
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    return f"{amount:,.2f}"


def _money_ksh(value) -> str:
    return f"KSh {_money(value)}"


def _money_dense(value) -> str:
    """Compact amount for wide shop matrices (full value kept in cell title)."""
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    if amount == amount.to_integral_value():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def _shop_col(
    shop,
    *,
    max_len: int = 10,
    pair: bool = False,
    pair_qty: str = "Qty",
    pair_amt: str = "Amt",
) -> dict:
    """Compact shop column header; full name kept in title for hover."""
    full = (getattr(shop, "name", None) or "Shop").strip() or "Shop"
    if len(full) <= max_len:
        label = full
    else:
        parts = [part for part in full.split() if part]
        if len(parts) >= 2 and len(parts[0]) <= max_len:
            label = parts[0]
        elif len(parts) >= 2:
            label = "".join(part[0] for part in parts[:4]).upper()
        else:
            label = full[: max_len - 1].rstrip() + "…"
    col = {
        "label": label,
        "title": full,
        "compact": True,
    }
    if pair:
        col["pair"] = True
        col["pair_qty"] = pair_qty
        col["pair_amt"] = pair_amt
    return col


def _qty_amount_cell(qty, amount, *, title: str = "", tone: str = "neutral") -> dict:
    """Quantity and amount side by side, styled as distinct values."""
    qty_label = _qty_label(qty or 0)
    amount_label = _money_dense(amount)
    full = _money_ksh(amount)
    tone_map = {
        "success": "good",
        "ok": "good",
        "danger": "bad",
        "error": "bad",
        "warning": "warn",
        "warn": "warn",
        "good": "good",
        "bad": "bad",
        "neutral": "neutral",
    }
    return {
        "kind": "qty_amount",
        "qty": qty_label,
        "amount": amount_label,
        "title": title or f"{qty_label} · {full}",
        "tone": tone_map.get(tone, "neutral"),
    }


def _money_cell(amount, *, tone: str = "neutral", title: str = "") -> dict:
    """Single amount cell for P&L-style analytics tables."""
    tone_map = {
        "success": "good",
        "ok": "good",
        "danger": "bad",
        "error": "bad",
        "warning": "warn",
        "warn": "warn",
        "good": "good",
        "bad": "bad",
        "neutral": "neutral",
    }
    full = _money_ksh(amount)
    return {
        "kind": "money",
        "label": _money_dense(amount),
        "title": title or full,
        "tone": tone_map.get(tone, "neutral"),
    }


def _pct_cell(pct, *, tone: str = "neutral", title: str = "") -> dict:
    value = Decimal(pct or 0).quantize(Decimal("0.1"))
    tone_map = {
        "success": "good",
        "danger": "bad",
        "warning": "warn",
        "good": "good",
        "bad": "bad",
        "warn": "warn",
        "neutral": "neutral",
    }
    label = f"{value}%"
    return {
        "kind": "pct",
        "label": label,
        "title": title or f"Gross margin {label}",
        "tone": tone_map.get(tone, "neutral"),
    }


def _pair_total_col(*, pair_qty: str = "Qty", pair_amt: str = "Amt") -> dict:
    return {
        "label": "Total",
        "pair": True,
        "pair_qty": pair_qty,
        "pair_amt": pair_amt,
        "total": True,
    }


def _zero() -> Decimal:
    return Decimal("0.00")


def _due_amount(total, paid) -> Decimal:
    due = Decimal(total or 0) - Decimal(paid or 0)
    if due < 0:
        return _zero()
    return due.quantize(Decimal("0.01"))


def _trade_due_amount(total, paid, exchange_value=0) -> Decimal:
    due = (
        Decimal(total or 0)
        - Decimal(paid or 0)
        - Decimal(exchange_value or 0)
    )
    if due < 0:
        return _zero()
    return due.quantize(Decimal("0.01"))


def _credit_account_receipts_q() -> Q:
    """Open credits plus cleared credits that were converted to sales."""
    return Q(kind=ShopReceiptKind.CREDIT) | Q(
        kind=ShopReceiptKind.SALE, settled_from_credit=True
    )


def _pos_feature_flags(row=None) -> dict:
    """Company POS toggles that decide which analytics columns are in use.

    ``cash``/``mpesa`` describe receipt payment columns — a combined
    cash+M-Pesa tender still feeds both columns. ``pay_cash``/``pay_mpesa``
    describe the single-method account-payment choices (cash or M-Pesa).
    Pass ``row`` (e.g. a shop-effective merge from
    ``get_effective_pos_settings``) to scope the flags to a shop's
    overrides; defaults to the company row.
    """
    from shops.services import get_company_pos_settings

    row = row if row is not None else get_company_pos_settings()
    combo = bool(getattr(row, "enable_cash_mpesa", True))
    cash = bool(getattr(row, "enable_cash", True))
    mpesa = bool(getattr(row, "enable_mpesa", True))
    credit = bool(getattr(row, "enable_credit", True))
    trade_out = bool(getattr(row, "enable_trade_out", True))
    trade_in = bool(getattr(row, "enable_trade_in", True))
    card = any(
        getattr(row, toggle, False)
        for toggle in (
            "enable_card",
            "enable_cash_card",
            "enable_mpesa_card",
            "enable_cash_mpesa_card",
        )
    )
    return {
        "sale": bool(getattr(row, "enable_sale", True)),
        "credit": credit,
        "quotation": bool(getattr(row, "enable_quotation", True)),
        "gift": bool(getattr(row, "enable_gift", True)),
        "trade_out": trade_out,
        "trade_in": trade_in,
        "tradings": trade_out or trade_in,
        "cash": cash
        or combo
        or getattr(row, "enable_cash_card", False)
        or getattr(row, "enable_cash_mpesa_card", False),
        "mpesa": mpesa
        or combo
        or getattr(row, "enable_mpesa_card", False)
        or getattr(row, "enable_cash_mpesa_card", False),
        "card": card,
        "pay_cash": cash,
        "pay_mpesa": mpesa,
        "pay_card": bool(getattr(row, "enable_card", False)),
        "credit_collections": credit and (cash or mpesa or card),
        "delivery": bool(getattr(row, "enable_cart_delivery", True)),
    }


def _check_account_pay_method(method: str) -> None:
    """Reject account payments through a payment method the company disabled."""
    from django.core.exceptions import ValidationError

    flags = _pos_feature_flags()
    if method == "mpesa" and not flags.get("pay_mpesa", True):
        raise ValidationError("M-Pesa payments are disabled for this company.")
    if method == "cash" and not flags.get("pay_cash", True):
        raise ValidationError("Cash payments are disabled for this company.")
    if method == "card" and not flags.get("pay_card", False):
        raise ValidationError("Card payments are disabled for this company.")


# Column / metric / tile labels that only make sense while the matching
# company POS feature is enabled. A tuple of flags means "all must be on".
_POS_GATED_LABELS = {
    "Sales": ("sale",),
    "All sales": ("sale",),
    "Credits": ("credit",),
    "All credits": ("credit",),
    "Quotations": ("quotation",),
    "Gifts": ("gift",),
    "All gifts": ("gift",),
    "Trade out": ("trade_out",),
    "Trade in": ("trade_in",),
    "Cash": ("cash",),
    "Cash sales": ("cash",),
    "Live cash": ("cash",),
    "Open cash": ("cash",),
    "Exp. open cash": ("cash",),
    "Open cash var": ("cash",),
    "Exp. close cash": ("cash",),
    "Close cash": ("cash",),
    "Close cash var": ("cash",),
    "Expected opening cash": ("cash",),
    "Actual opening cash": ("cash",),
    "Opening cash variance": ("cash",),
    "Expected closing cash": ("cash",),
    "Actual closing cash": ("cash",),
    "Closing cash variance": ("cash",),
    "M-Pesa": ("mpesa",),
    "M-Pesa sales": ("mpesa",),
    "Live M-Pesa": ("mpesa",),
    "Open M-Pesa": ("mpesa",),
    "Exp. open M-Pesa": ("mpesa",),
    "Open M-Pesa var": ("mpesa",),
    "Exp. close M-Pesa": ("mpesa",),
    "Close M-Pesa": ("mpesa",),
    "Close M-Pesa var": ("mpesa",),
    "Expected opening M-Pesa": ("mpesa",),
    "Actual opening M-Pesa": ("mpesa",),
    "Opening M-Pesa variance": ("mpesa",),
    "Expected closing M-Pesa": ("mpesa",),
    "Actual closing M-Pesa": ("mpesa",),
    "Closing M-Pesa variance": ("mpesa",),
    "Card": ("card",),
    "Card sales": ("card",),
    "Live Card": ("card",),
    "Open Card": ("card",),
    "Exp. open Card": ("card",),
    "Open Card var": ("card",),
    "Exp. close Card": ("card",),
    "Close Card": ("card",),
    "Close Card var": ("card",),
    "Expected opening Card": ("card",),
    "Actual opening Card": ("card",),
    "Opening Card variance": ("card",),
    "Expected closing Card": ("card",),
    "Actual closing Card": ("card",),
    "Closing Card variance": ("card",),
    "Card till (period)": ("card",),
    "Paid cash": ("credit", "pay_cash"),
    "Paid M-Pesa": ("credit", "pay_mpesa"),
    "Paid card": ("credit", "pay_card"),
    "Paid total": ("credit_collections",),
    "Unpaid": ("credit",),
}


def _label_pos_hidden(label: str, flags: dict) -> bool:
    needs = _POS_GATED_LABELS.get((label or "").strip())
    return bool(needs) and not all(flags.get(key, True) for key in needs)


def _pos_col_label(col) -> str:
    if isinstance(col, dict):
        return (col.get("label") or col.get("title") or "").strip()
    return str(col or "").strip()


def _apply_pos_visibility(page: dict, flags: dict) -> dict:
    """Strip columns / metrics / rows tied to disabled company POS features.

    Column removal also drops the matching cell in every row so column/cell
    alignment is preserved. Rows are only dropped when the table's first
    column is a "Type"/"Metric" key column (e.g. per-type by-shop rollups),
    so a client or employee that happens to share a label is never removed.
    """
    if not isinstance(page, dict) or not flags or all(flags.values()):
        return page
    for table in page.get("tables") or []:
        columns = list(table.get("columns") or [])
        drop_idx = {
            i
            for i, col in enumerate(columns)
            if _label_pos_hidden(_pos_col_label(col), flags)
        }
        if drop_idx:
            table["columns"] = [
                col for i, col in enumerate(columns) if i not in drop_idx
            ]
            new_rows = []
            for row in table.get("rows") or []:
                cells = row.get("cells") if isinstance(row, dict) else row
                if cells is None:
                    new_rows.append(row)
                    continue
                kept = [
                    cell for i, cell in enumerate(cells) if i not in drop_idx
                ]
                new_rows.append({**row, "cells": kept} if isinstance(row, dict) else kept)
            table["rows"] = new_rows
        first_col = _pos_col_label(columns[0]) if columns else ""
        if first_col in ("Type", "Metric"):
            filtered = []
            for row in table.get("rows") or []:
                if isinstance(row, dict) and row.get("group_header"):
                    filtered.append(row)
                    continue
                cells = row.get("cells") if isinstance(row, dict) else row
                cells = cells or []
                first = cells[0] if cells else ""
                label = _pos_col_label(first)
                if _label_pos_hidden(label, flags):
                    continue
                filtered.append(row)
            table["rows"] = filtered
    if page.get("metrics"):
        page["metrics"] = [
            m
            for m in page["metrics"]
            if not _label_pos_hidden((m or {}).get("label"), flags)
        ]
    board = page.get("summary_board")
    if isinstance(board, dict) and board.get("tiles"):
        board["tiles"] = [
            t
            for t in board["tiles"]
            if not _label_pos_hidden((t or {}).get("label"), flags)
        ]
    if page.get("metric_groups"):
        groups = []
        for group in page["metric_groups"]:
            if _label_pos_hidden((group or {}).get("label"), flags):
                continue
            rows = []
            for row in group.get("rows") or []:
                metrics = [
                    m
                    for m in row.get("metrics") or []
                    if not _label_pos_hidden((m or {}).get("label"), flags)
                ]
                if metrics:
                    rows.append({**row, "metrics": metrics})
            groups.append({**group, "rows": rows})
        page["metric_groups"] = groups
    return page


def _parse_client_receipt_kind(raw: str) -> str:
    kind = (raw or "").strip().lower()
    if kind in ("sale", "sales"):
        return "sales"
    if kind in ("credit", "credits"):
        return "credits"
    if kind in ("quotation", "quotations", "quote"):
        return "quotations"
    if kind in ("trade_out", "trade-out"):
        return "trade_out"
    if kind in ("trade_in", "trade-in"):
        return "trade_in"
    if kind in ("tradings", "trades", "trade"):
        return "tradings"
    return "all"


def _client_activity_receipts_q(kind: str, flags: dict | None = None) -> Q:
    """Receipt kinds shown on the Clients analytics pages.

    Only kinds backed by an enabled company POS feature are included.
    """
    flags = flags or _pos_feature_flags()
    if kind == "credits":
        return _credit_account_receipts_q() if flags["credit"] else Q(pk__in=[])
    if kind == "sales":
        return Q(kind=ShopReceiptKind.SALE) if flags["sale"] else Q(pk__in=[])
    if kind == "quotations":
        return Q(kind=ShopReceiptKind.QUOTATION) if flags["quotation"] else Q(pk__in=[])
    if kind == "trade_out":
        return Q(kind=ShopReceiptKind.TRADE_OUT) if flags["trade_out"] else Q(pk__in=[])
    if kind == "trade_in":
        return Q(kind=ShopReceiptKind.TRADE_IN) if flags["trade_in"] else Q(pk__in=[])
    if kind == "tradings":
        q = Q(pk__in=[])
        if flags["trade_out"]:
            q |= Q(kind=ShopReceiptKind.TRADE_OUT)
        if flags["trade_in"]:
            q |= Q(kind=ShopReceiptKind.TRADE_IN)
        return q
    q = Q(pk__in=[])
    if flags["sale"]:
        q |= Q(kind=ShopReceiptKind.SALE)
    else:
        # Cleared credits/trades are stored as SALE receipts — keep them while
        # the source feature is still enabled even if plain sales are off.
        if flags["credit"]:
            q |= Q(kind=ShopReceiptKind.SALE, settled_from_credit=True)
        if flags["tradings"]:
            q |= Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
    if flags["credit"]:
        q |= Q(kind=ShopReceiptKind.CREDIT)
    if flags["quotation"]:
        q |= Q(kind=ShopReceiptKind.QUOTATION)
    if flags["trade_out"]:
        q |= Q(kind=ShopReceiptKind.TRADE_OUT)
    if flags["trade_in"]:
        q |= Q(kind=ShopReceiptKind.TRADE_IN)
    return q


def _enabled_client_receipt_kind(kind: str, flags: dict) -> str:
    """Fall back to "all" when the picked receipt kind is a disabled feature."""
    if kind in ("all", "tradings"):
        return kind if flags.get("tradings", True) or kind == "all" else "all"
    key = {
        "sales": "sale",
        "credits": "credit",
        "quotations": "quotation",
        "trade_out": "trade_out",
        "trade_in": "trade_in",
    }.get(kind)
    if key is not None and not flags.get(key, True):
        return "all"
    return kind


def _client_receipt_kind_options(flags: dict) -> list:
    """Filter options for the Clients transaction-type select."""
    options = [("all", "All types")]
    if flags["sale"]:
        options.append(("sales", "Sales"))
    if flags["credit"]:
        options.append(("credits", "Credits"))
    if flags["quotation"]:
        options.append(("quotations", "Quotations"))
    if flags["trade_out"]:
        options.append(("trade_out", "Trade out"))
    if flags["trade_in"]:
        options.append(("trade_in", "Trade in"))
    return options


def _payment_status_for_due(due: Decimal, paid: Decimal) -> str:
    if due <= 0:
        return "Paid"
    if paid > 0:
        return "Partial"
    return "Unpaid"


def _status_tone(status: str) -> str:
    label = (status or "").strip().lower()
    if label == "paid":
        return "good"
    if label == "partial":
        return "warn"
    if label in {"unpaid", "open", "overdue"}:
        return "bad"
    return "neutral"


def _parse_pay_amount(value) -> Decimal:
    from django.core.exceptions import ValidationError

    raw = str(value or "").strip().replace(",", "")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except Exception as exc:
        raise ValidationError("Enter a valid payment amount.") from exc
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")
    return amount


def _allocated_shop_filter(profile, request=None, *, shop_ids=None) -> dict:
    """Shop filter limited to shops allocated to the signed-in employee."""
    filter_shops = actionable_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    raw_values = []
    if shop_ids is not None:
        if isinstance(shop_ids, (str, int)):
            raw_values = [shop_ids]
        else:
            raw_values = list(shop_ids)
    elif request is not None:
        getter = getattr(request, "GET", None) or getattr(request, "POST", None)
        if getter is not None:
            raw_values = getter.getlist("shop_id") if hasattr(getter, "getlist") else []
    selected_shop_ids = _parse_shop_ids(raw_values, shops_by_id)
    active_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]
    selected_shops = [shops_by_id[pk] for pk in selected_shop_ids if pk in shops_by_id]
    if len(selected_shops) == 1:
        shop_filter_label = selected_shops[0].name
    elif selected_shops:
        shop_filter_label = f"{len(selected_shops)} shops"
    else:
        shop_filter_label = "All shops"
    return {
        "filter_shops": filter_shops,
        "selected_shop_ids": selected_shop_ids,
        "active_shop_ids": active_shop_ids,
        "selected_shops": selected_shops,
        "shop_filter_label": shop_filter_label,
    }


def _parse_receipt_id_set(receipt_ids) -> set[int]:
    """Normalize optional receipt id filters from forms / APIs."""
    if not receipt_ids:
        return set()
    values = [receipt_ids] if isinstance(receipt_ids, (str, int)) else list(receipt_ids)
    parsed: set[int] = set()
    for raw in values:
        try:
            pk = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if pk > 0:
            parsed.add(pk)
    return parsed


def _notify_supplier_after_payment(
    *,
    kind: str,
    account_id: int,
    amount,
    balance_after,
    receipt_ref: str = "",
    shop=None,
    supplier=None,
) -> None:
    """Post-commit: tell the supplier their payment arrived."""
    try:
        from communications.automations import maybe_send_supplier_payment_notice

        if supplier is None:
            model = ExpenseSupplier if kind == "expense" else Supplier
            supplier = model.objects.filter(pk=account_id).first()
        if supplier is None:
            return
        maybe_send_supplier_payment_notice(
            supplier,
            kind=kind,
            amount=amount,
            balance_after=balance_after,
            receipt_ref=receipt_ref,
            shop=shop,
        )
    except Exception:
        logger.exception(
            "Supplier payment notice failed for %s account %s", kind, account_id
        )


def _apply_supplier_payment_fifo(payables, remaining):
    """Apply `remaining` across payable stock movements oldest → newest.

    Each entry: ``{"kind": "stock"|"shelf", "movement", "total"}`` — updates
    ``amount_paid``/``payment_status`` in place; returns ``(remaining, cleared)``.
    """
    cleared = 0
    for entry in payables:
        if remaining <= 0:
            break
        movement = entry["movement"]
        if movement is None:
            continue
        due = _due_amount(entry["total"], movement.amount_paid)
        if due <= 0:
            continue
        apply = min(remaining, due)
        paid = (Decimal(movement.amount_paid or 0) + apply).quantize(
            Decimal("0.01")
        )
        due_after = _due_amount(entry["total"], paid)
        if due_after <= 0:
            status = StockPaymentStatus.PAID
        elif paid > 0:
            status = StockPaymentStatus.PARTIAL
        else:
            status = StockPaymentStatus.UNPAID
        movement.amount_paid = paid
        movement.payment_status = status
        movement.save(update_fields=["amount_paid", "payment_status"])
        if entry["kind"] == "stock":
            movement.lines.update(payment_status=status)
        remaining = (remaining - apply).quantize(Decimal("0.01"))
        cleared += 1
    return remaining, cleared


def apply_account_payment(
    *,
    profile,
    kind: str,
    account_id: int,
    amount,
    payment_method: str = "cash",
    stk_payment_id: str = "",
    mpesa_receipt_number: str = "",
    shop_ids=None,
    start=None,
    end=None,
    receipt_ids=None,
    supplier_name: str = "",
    supplier_phone: str = "",
) -> dict:
    """Apply a payment to an account, clearing receipts oldest → newest (FIFO).

    When ``receipt_ids`` is provided for credit accounts, only those receipts are
    eligible — still allocated oldest → newest within the selection.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction

    kind = (kind or "").strip().lower()
    if kind not in ("credit", "expense", "stock", "shelf"):
        raise ValidationError("Unknown payment type.")

    method = (payment_method or "cash").strip().lower()
    if kind == "credit" and method not in ("cash", "mpesa", "card"):
        raise ValidationError("Choose cash, M-Pesa, or card.")
    if kind != "credit":
        method = "cash"
    _check_account_pay_method(method)

    pay_amount = _parse_pay_amount(amount)
    remaining = pay_amount
    selected_receipt_ids = _parse_receipt_id_set(receipt_ids)
    allocated = {shop.pk for shop in actionable_shops_for_profile(profile)}
    if shop_ids:
        parsed = set()
        values = [shop_ids] if isinstance(shop_ids, (str, int)) else list(shop_ids)
        for raw in values:
            try:
                pk = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if pk in allocated:
                parsed.add(pk)
        shop_ids = parsed or allocated
    else:
        shop_ids = allocated
    cleared = 0
    mpesa_receipt_number = (mpesa_receipt_number or "").strip()
    stk_payment = None

    with transaction.atomic():
        if kind == "credit":
            client = Client.objects.filter(pk=account_id).first()
            if client is None:
                raise ValidationError("Client not found.")

            if method == "mpesa" and not (stk_payment_id or "").strip():
                if not mpesa_receipt_number:
                    raise ValidationError("Enter the M-Pesa receipt number.")
            elif method == "mpesa":
                from shops.daraja_stk import require_successful_stk, stk_ready

                if not stk_ready():
                    raise ValidationError(
                        "STK Push is not enabled or Daraja credentials are not verified."
                    )
                stk_payment = require_successful_stk(
                    public_id=stk_payment_id,
                    expected_amount=pay_amount,
                    expected_phone=client.phone_number or client.phone_normalized,
                    purpose="credit",
                )
                if stk_payment.applied:
                    raise ValidationError("This M-Pesa payment was already applied.")
                if stk_payment.account_kind and stk_payment.account_kind != "credit":
                    raise ValidationError("M-Pesa payment is not for a credit account.")
                if stk_payment.account_id and int(stk_payment.account_id) != int(client.pk):
                    raise ValidationError("M-Pesa payment belongs to a different client.")
                mpesa_receipt_number = stk_payment.mpesa_receipt_number or ""

            credit_qs = ShopReceipt.objects.select_for_update().filter(
                client_id=client.pk,
                kind=ShopReceiptKind.CREDIT,
                shop_id__in=shop_ids,
            )
            if selected_receipt_ids:
                credit_qs = credit_qs.filter(pk__in=selected_receipt_ids)
            else:
                credit_qs = _within_created_range(credit_qs, start, end)
            receipts = list(
                credit_qs.exclude(status=ShopReceiptStatus.CANCELLED).order_by(
                    "created_at", "pk"
                )
            )
            if selected_receipt_ids:
                found = {row.pk for row in receipts}
                missing = selected_receipt_ids - found
                if missing:
                    raise ValidationError(
                        "One or more selected credit receipts were not found "
                        "for this client."
                    )
            balance_before = sum(
                (_due_amount(row.total, row.amount_paid) for row in receipts),
                _zero(),
            )
            if balance_before <= 0:
                raise ValidationError(
                    "Selected receipts have no balance due."
                    if selected_receipt_ids
                    else "This account has no balance due."
                )
            if pay_amount > balance_before:
                raise ValidationError(
                    f"Amount exceeds balance due ({_money_ksh(balance_before)})."
                )
            converted = 0
            from shops.credit_settlement import record_credit_collection

            for receipt in receipts:
                if remaining <= 0:
                    break
                due = _due_amount(receipt.total, receipt.amount_paid)
                if due <= 0:
                    continue
                apply = min(remaining, due)
                if record_credit_collection(
                    receipt,
                    amount=apply,
                    payment_method=method,
                    mpesa_receipt_number=mpesa_receipt_number,
                ):
                    converted += 1
                remaining = (remaining - apply).quantize(Decimal("0.01"))
                cleared += 1
                from shops.credit_audit import log_credit_payment

                log_credit_payment(
                    client_id=client.pk,
                    receipt=receipt,
                    amount=apply,
                    payment_method=method,
                    actor=profile,
                    stk_payment=stk_payment if method == "mpesa" else None,
                    mpesa_receipt_number=mpesa_receipt_number,
                )

            if method == "mpesa" and stk_payment is not None:
                stk_payment.applied = True
                stk_payment.save(update_fields=["applied", "updated_at"])

            # Account balance after payment: all open credits for this client/shops,
            # not only the selected subset.
            all_open = list(
                ShopReceipt.objects.filter(
                    client_id=client.pk,
                    kind=ShopReceiptKind.CREDIT,
                    shop_id__in=shop_ids,
                )
                .exclude(status=ShopReceiptStatus.CANCELLED)
                .only("total", "amount_paid")
            )
            balance_after = sum(
                (_due_amount(row.total, row.amount_paid) for row in all_open),
                _zero(),
            )
            pay_label = {"mpesa": "M-Pesa", "card": "Card"}.get(method, "Cash")
            ref_bit = (
                f" · Ref {mpesa_receipt_number}" if mpesa_receipt_number else ""
            )
            converted_note = ""
            if converted:
                converted_note = (
                    f" {converted} fully paid credit"
                    f"{'' if converted == 1 else 's'} now recorded as "
                    f"{'a sale' if converted == 1 else 'sales'}."
                )
            scope_note = (
                " selected"
                if selected_receipt_ids
                else ""
            )
            return {
                "ok": True,
                "kind": kind,
                "account_id": client.pk,
                "cleared": cleared,
                "converted": converted,
                "payment_method": method,
                "mpesa_receipt_number": mpesa_receipt_number,
                "account_balance": _money_ksh(balance_after),
                "account_balance_raw": str(balance_after),
                "message": (
                    f"{pay_label} payment of {_money_ksh(pay_amount)} applied "
                    f"oldest → newest across {cleared}{scope_note} receipt"
                    f"{'' if cleared == 1 else 's'}{ref_bit}.{converted_note}"
                ),
            }

        if kind == "expense":
            supplier = ExpenseSupplier.objects.filter(pk=account_id).first()
            if supplier is None:
                raise ValidationError("Supplier not found.")
            expenses = list(
                _within_created_range(
                    Expense.objects.select_for_update().filter(
                        shop_id__in=shop_ids, supplier_id=supplier.pk
                    ),
                    start,
                    end,
                ).order_by("created_at", "pk")
            )
            balance_before = sum(
                (_due_amount(row.amount, row.amount_paid) for row in expenses),
                _zero(),
            )
            if balance_before <= 0:
                raise ValidationError("This account has no balance due.")
            if pay_amount > balance_before:
                raise ValidationError(
                    f"Amount exceeds balance due ({_money_ksh(balance_before)})."
                )
            for expense in expenses:
                if remaining <= 0:
                    break
                due = _due_amount(expense.amount, expense.amount_paid)
                if due <= 0:
                    continue
                apply = min(remaining, due)
                paid = (Decimal(expense.amount_paid or 0) + apply).quantize(
                    Decimal("0.01")
                )
                due_after = _due_amount(expense.amount, paid)
                if due_after <= 0:
                    status = ExpensePaymentStatus.PAID
                elif paid > 0:
                    status = ExpensePaymentStatus.PARTIAL
                else:
                    status = ExpensePaymentStatus.UNPAID
                expense.amount_paid = paid
                expense.payment_status = status
                expense.save(update_fields=["amount_paid", "payment_status"])
                remaining = (remaining - apply).quantize(Decimal("0.01"))
                cleared += 1
            balance_after = sum(
                (_due_amount(row.amount, row.amount_paid) for row in expenses),
                _zero(),
            )
            transaction.on_commit(
                lambda: _notify_supplier_after_payment(
                    kind="expense",
                    account_id=supplier.pk,
                    amount=pay_amount,
                    balance_after=balance_after,
                )
            )
            return {
                "ok": True,
                "kind": kind,
                "account_id": supplier.pk,
                "cleared": cleared,
                "account_balance": _money_ksh(balance_after),
                "account_balance_raw": str(balance_after),
                "message": (
                    f"Payment of {_money_ksh(pay_amount)} applied "
                    f"oldest → newest across {cleared} receipt"
                    f"{'' if cleared == 1 else 's'}."
                ),
            }

        if kind == "shelf":
            name = (supplier_name or "").strip()
            phone = (supplier_phone or "").strip()
            if not name:
                raise ValidationError("Supplier not found.")
            movements = list(
                _within_created_range(
                    _shelf_movements_for_supplier(name, phone, shop_ids)
                    .select_related("shelf_item", "shelf_item__shop"),
                    start,
                    end,
                ).order_by("created_at", "pk")
            )
            locked = {
                row.pk: row
                for row in ShelfStockMovement.objects.select_for_update().filter(
                    pk__in=[movement.pk for movement in movements]
                )
            }
            totals = {row.pk: _shelf_movement_total(row) for row in movements}
            payables = [
                {
                    "kind": "shelf",
                    "movement": locked.get(row.pk) or row,
                    "total": totals[row.pk],
                    "when": row.created_at,
                }
                for row in movements
            ]
            # Item stock-ins of the same supplier identity merge into the FIFO.
            item_map = _item_movements_for_identity(
                name, phone, list(shop_ids), start=start, end=end
            )
            locked_items = {
                row.pk: row
                for row in StockMovement.objects.select_for_update().filter(
                    pk__in=list(item_map.keys())
                )
            }
            for mid, info in item_map.items():
                movement = locked_items.get(mid) or info["movement"]
                payables.append(
                    {
                        "kind": "stock",
                        "movement": movement,
                        "total": info["total"],
                        "when": movement.created_at,
                    }
                )
            payables.sort(
                key=lambda entry: (entry["when"], entry["movement"].pk)
            )
            balance_before = sum(
                (
                    _due_amount(entry["total"], entry["movement"].amount_paid)
                    for entry in payables
                ),
                _zero(),
            )
            if balance_before <= 0:
                raise ValidationError("This account has no balance due.")
            if pay_amount > balance_before:
                raise ValidationError(
                    f"Amount exceeds balance due ({_money_ksh(balance_before)})."
                )
            remaining, cleared = _apply_supplier_payment_fifo(
                payables, remaining
            )
            balance_after = sum(
                (
                    _due_amount(entry["total"], entry["movement"].amount_paid)
                    for entry in payables
                ),
                _zero(),
            )
            first_shop = None
            for movement in movements:
                shelf_item = movement.shelf_item
                if shelf_item and shelf_item.shop:
                    first_shop = shelf_item.shop
                    break
            if first_shop is None:
                for info in item_map.values():
                    shop = getattr(info["movement"], "shop", None)
                    if shop is not None:
                        first_shop = shop
                        break
            transaction.on_commit(
                lambda: _notify_supplier_after_payment(
                    kind="shelf",
                    account_id=0,
                    amount=pay_amount,
                    balance_after=balance_after,
                    shop=first_shop,
                    supplier=_shelf_supplier_stub(name, phone),
                )
            )
            return {
                "ok": True,
                "kind": kind,
                "account_id": 0,
                "cleared": cleared,
                "account_balance": _money_ksh(balance_after),
                "account_balance_raw": str(balance_after),
                "message": (
                    f"Payment of {_money_ksh(pay_amount)} applied "
                    f"oldest → newest across {cleared} receipt"
                    f"{'' if cleared == 1 else 's'}."
                ),
            }

        supplier = Supplier.objects.filter(pk=account_id).first()
        if supplier is None:
            raise ValidationError("Supplier not found.")
        lines = list(
            _within_created_range(
                _stock_lines_for_supplier(supplier, list(shop_ids)),
                start,
                end,
                lookup="movement__created_at",
            ).select_related("movement")
        )
        by_movement = {}
        order = []
        for line in lines:
            movement = line.movement
            if movement is None:
                continue
            key = movement.pk
            if key not in by_movement:
                by_movement[key] = {
                    "movement": movement,
                    "total": _zero(),
                    "when": movement.created_at,
                }
                order.append(key)
            qty = Decimal(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            by_movement[key]["total"] += (unit * qty).quantize(Decimal("0.01"))
        order.sort(key=lambda mid: (by_movement[mid]["when"], mid))
        # Lock movements oldest first.
        movement_ids = order
        locked = {
            row.pk: row
            for row in StockMovement.objects.select_for_update().filter(
                pk__in=movement_ids
            )
        }
        payables = [
            {
                "kind": "stock",
                "movement": locked.get(mid) or by_movement[mid]["movement"],
                "total": by_movement[mid]["total"],
                "when": by_movement[mid]["when"],
            }
            for mid in order
        ]
        # Shelf stock-ins of the same supplier identity merge into the FIFO.
        shelf_rows = _shelf_movements_for_identity(
            supplier.name,
            f"{supplier.phone_country_code} {supplier.phone_number}",
            list(shop_ids),
            start=start,
            end=end,
        )
        locked_shelf = {
            row.pk: row
            for row in ShelfStockMovement.objects.select_for_update().filter(
                pk__in=[row.pk for row in shelf_rows]
            )
        }
        for row in shelf_rows:
            movement = locked_shelf.get(row.pk) or row
            payables.append(
                {
                    "kind": "shelf",
                    "movement": movement,
                    "total": _shelf_movement_total(movement),
                    "when": movement.created_at,
                }
            )
        payables.sort(key=lambda entry: (entry["when"], entry["movement"].pk))
        balance_before = _zero()
        for entry in payables:
            balance_before += _due_amount(
                entry["total"], entry["movement"].amount_paid
            )
        if balance_before <= 0:
            raise ValidationError("This account has no balance due.")
        if pay_amount > balance_before:
            raise ValidationError(
                f"Amount exceeds balance due ({_money_ksh(balance_before)})."
            )
        remaining, cleared = _apply_supplier_payment_fifo(payables, remaining)
        balance_after = _zero()
        for entry in payables:
            balance_after += _due_amount(
                entry["total"], entry["movement"].amount_paid
            )
        transaction.on_commit(
            lambda: _notify_supplier_after_payment(
                kind="stock",
                account_id=supplier.pk,
                amount=pay_amount,
                balance_after=balance_after,
            )
        )
        return {
            "ok": True,
            "kind": kind,
            "account_id": supplier.pk,
            "cleared": cleared,
            "account_balance": _money_ksh(balance_after),
            "account_balance_raw": str(balance_after),
            "message": (
                f"Payment of {_money_ksh(pay_amount)} applied "
                f"oldest → newest across {cleared} receipt"
                f"{'' if cleared == 1 else 's'}."
            ),
        }


def _credit_receipt_for_profile(profile, receipt_id: int):
    shop_ids = {shop.pk for shop in actionable_shops_for_profile(profile)}
    return (
        ShopReceipt.objects.select_for_update()
        .filter(
            pk=receipt_id,
            kind=ShopReceiptKind.CREDIT,
            shop_id__in=shop_ids,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .select_related("client", "shop")
        .first()
    )


def update_client_credit_follow_up_date(
    *, profile, client_id: int, credit_follow_up_date: str
) -> dict:
    """Set the follow-up date for a client's whole credit account."""
    from datetime import date

    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone

    raw = (credit_follow_up_date or "").strip()
    if not raw:
        raise ValidationError("Follow-up date is required.")
    try:
        follow_up = date.fromisoformat(raw)
    except ValueError:
        raise ValidationError("Enter a valid follow-up date.")

    shop_ids = {shop.pk for shop in actionable_shops_for_profile(profile)}
    if not shop_ids:
        raise ValidationError("No shops available.")

    with transaction.atomic():
        client = Client.objects.filter(pk=client_id).first()
        if client is None:
            raise ValidationError("Client not found.")
        open_credits = list(
            ShopReceipt.objects.filter(
                client_id=client.pk,
                kind=ShopReceiptKind.CREDIT,
                shop_id__in=shop_ids,
            )
            .exclude(status=ShopReceiptStatus.CANCELLED)
            .select_related("shop")
        )
        balance = _zero()
        first_shop = None
        for receipt in open_credits:
            due = _due_amount(receipt.total, receipt.amount_paid)
            if due <= 0:
                continue
            balance += due
            if first_shop is None:
                first_shop = receipt.shop
        if balance <= 0:
            raise ValidationError("This client has no open credit to follow up.")

        today = timezone.localdate()
        old_follow_up = client.credit_follow_up_date
        if old_follow_up == follow_up:
            label = follow_up.strftime("%d %b %Y")
            return {
                "ok": True,
                "client_id": client.pk,
                "follow_up": label,
                "follow_up_raw": follow_up.isoformat(),
                "follow_up_overdue": balance > 0 and follow_up < today,
                "balance": _money_ksh(balance),
                "notified": False,
                "notify_reason": "",
                "message": f"Follow-up date is already {label}.",
            }

        client.credit_follow_up_date = follow_up
        client.credit_follow_up_reminder_sent_on = None
        client.save(
            update_fields=[
                "credit_follow_up_date",
                "credit_follow_up_reminder_sent_on",
                "updated_at",
            ]
        )
        from shops.credit_audit import log_credit_follow_up_change

        log_credit_follow_up_change(
            client_id=client.pk,
            old_date=old_follow_up,
            new_date=follow_up,
            actor=profile,
            shop=first_shop,
        )

    # Notify after the account date is saved (outside the atomic block).
    from communications.automations import maybe_send_credit_follow_up_updated

    label = follow_up.strftime("%d %b %Y")
    notify = maybe_send_credit_follow_up_updated(
        client,
        old_date=old_follow_up,
        balance=balance,
        request=None,
    )
    notified = bool(notify.get("sent"))
    notify_reason = notify.get("reason") or ""
    if notified:
        message = f"Account follow-up set to {label}. Client notice queued."
    else:
        message = f"Account follow-up set to {label}. Notice not sent — {notify_reason}"
    return {
        "ok": True,
        "client_id": client.pk,
        "follow_up": label,
        "follow_up_raw": follow_up.isoformat(),
        "follow_up_overdue": balance > 0 and follow_up < today,
        "balance": _money_ksh(balance),
        "notified": notified,
        "notify_reason": notify_reason,
        "message": message,
    }


def apply_credit_receipt_payment(
    *,
    profile,
    receipt_id: int,
    amount,
    payment_method: str = "cash",
    stk_payment_id: str = "",
    mpesa_receipt_number: str = "",
) -> dict:
    """Apply a payment to one credit receipt (not FIFO across the account)."""
    from django.core.exceptions import ValidationError
    from django.db import transaction

    method = (payment_method or "cash").strip().lower()
    if method not in ("cash", "mpesa", "card"):
        raise ValidationError("Choose cash, M-Pesa, or card.")
    _check_account_pay_method(method)

    pay_amount = _parse_pay_amount(amount)
    mpesa_receipt_number = (mpesa_receipt_number or "").strip()
    stk_payment = None

    with transaction.atomic():
        receipt = _credit_receipt_for_profile(profile, receipt_id)
        if receipt is None:
            raise ValidationError("Credit receipt not found.")

        due = _due_amount(receipt.total, receipt.amount_paid)
        if due <= 0:
            raise ValidationError("This receipt has no balance due.")
        if pay_amount > due:
            raise ValidationError(f"Amount exceeds due on this receipt ({_money_ksh(due)}).")

        if method == "mpesa" and not (stk_payment_id or "").strip():
            if not mpesa_receipt_number:
                raise ValidationError("Enter the M-Pesa receipt number.")
        elif method == "mpesa":
            from shops.daraja_stk import require_successful_stk, stk_ready

            if not stk_ready():
                raise ValidationError(
                    "STK Push is not enabled or Daraja credentials are not verified."
                )
            client = receipt.client
            expected_phone = (
                receipt.client_phone
                or (client.phone_number if client else "")
                or (client.phone_normalized if client else "")
            )
            stk_payment = require_successful_stk(
                public_id=stk_payment_id,
                expected_amount=pay_amount,
                expected_phone=expected_phone,
                purpose="credit",
            )
            if stk_payment.applied:
                raise ValidationError("This M-Pesa payment was already applied.")
            if stk_payment.receipt_id and int(stk_payment.receipt_id) != int(receipt.pk):
                raise ValidationError("M-Pesa payment is for a different receipt.")
            if stk_payment.account_kind and stk_payment.account_kind != "credit":
                raise ValidationError("M-Pesa payment is not for a credit account.")
            if receipt.client_id and stk_payment.account_id:
                if int(stk_payment.account_id) != int(receipt.client_id):
                    raise ValidationError("M-Pesa payment belongs to a different client.")
            mpesa_receipt_number = stk_payment.mpesa_receipt_number or ""
            stk_payment.receipt = receipt
            stk_payment.applied = True
            stk_payment.save(update_fields=["receipt", "applied", "updated_at"])

        from shops.credit_settlement import record_credit_collection

        converted = record_credit_collection(
            receipt,
            amount=pay_amount,
            payment_method=method,
            mpesa_receipt_number=mpesa_receipt_number,
        )

        from shops.credit_audit import log_credit_payment

        log_credit_payment(
            client_id=receipt.client_id,
            receipt=receipt,
            amount=pay_amount,
            payment_method=method,
            actor=profile,
            stk_payment=stk_payment if method == "mpesa" else None,
            mpesa_receipt_number=mpesa_receipt_number,
        )

        due_after = _due_amount(receipt.total, receipt.amount_paid)
        balance_after = _zero()
        if receipt.client_id:
            balance_after = sum(
                (
                    _due_amount(row.total, row.amount_paid)
                    for row in ShopReceipt.objects.filter(
                        client_id=receipt.client_id,
                        kind=ShopReceiptKind.CREDIT,
                        shop_id__in={shop.pk for shop in actionable_shops_for_profile(profile)},
                    ).exclude(status=ShopReceiptStatus.CANCELLED)
                ),
                _zero(),
            )

        pay_label = {"mpesa": "M-Pesa", "card": "Card"}.get(method, "Cash")
        ref_bit = f" · Ref {mpesa_receipt_number}" if mpesa_receipt_number else ""
        status = _payment_status_for_due(due_after, receipt.amount_paid)
        converted_note = (
            " Receipt is now recorded as a sale." if converted else ""
        )
        return {
            "ok": True,
            "kind": "credit",
            "receipt_id": receipt.pk,
            "receipt_kind": receipt.kind,
            "converted": converted,
            "account_id": receipt.client_id,
            "payment_method": method,
            "mpesa_receipt_number": mpesa_receipt_number,
            "receipt_due": _money_ksh(due_after),
            "receipt_due_raw": str(due_after),
            "receipt_status": status,
            "receipt_status_tone": _status_tone(status),
            "account_balance": _money_ksh(balance_after),
            "account_balance_raw": str(balance_after),
            "message": (
                f"{pay_label} payment of {_money_ksh(pay_amount)} applied to "
                f"{receipt.receipt_number}{ref_bit}.{converted_note}"
            ),
        }


def apply_supplier_receipt_payment(
    *,
    profile,
    kind: str,
    account_id: int,
    receipt_id: int,
    amount,
    shop_ids=None,
    supplier_name: str = "",
    supplier_phone: str = "",
) -> dict:
    """Apply a cash payment to one stock-in, shelf stock-in or expense receipt."""
    from django.core.exceptions import ValidationError
    from django.db import transaction

    kind = (kind or "").strip().lower()
    if kind not in ("expense", "stock", "shelf"):
        raise ValidationError("Unknown payment type.")

    pay_amount = _parse_pay_amount(amount)
    shop_filter = _allocated_shop_filter(profile, shop_ids=shop_ids)
    active_shop_ids = shop_filter["active_shop_ids"]

    with transaction.atomic():
        if kind == "expense":
            supplier = None
            if account_id:
                supplier = ExpenseSupplier.objects.filter(pk=account_id).first()
                if supplier is None:
                    raise ValidationError("Supplier not found.")
            expense_qs = Expense.objects.select_for_update().filter(
                pk=receipt_id,
                shop_id__in=active_shop_ids,
            )
            if supplier is not None:
                expense_qs = expense_qs.filter(supplier_id=supplier.pk)
            expense = expense_qs.first()
            if expense is None:
                raise ValidationError("Expense receipt not found.")
            due = _due_amount(expense.amount, expense.amount_paid)
            if due <= 0:
                raise ValidationError("This receipt has no balance due.")
            if pay_amount > due:
                raise ValidationError(
                    f"Amount exceeds due on this receipt ({_money_ksh(due)})."
                )
            paid = (Decimal(expense.amount_paid or 0) + pay_amount).quantize(
                Decimal("0.01")
            )
            due_after = _due_amount(expense.amount, paid)
            if due_after <= 0:
                status = ExpensePaymentStatus.PAID
            elif paid > 0:
                status = ExpensePaymentStatus.PARTIAL
            else:
                status = ExpensePaymentStatus.UNPAID
            expense.amount_paid = paid
            expense.payment_status = status
            expense.save(update_fields=["amount_paid", "payment_status"])
            if expense.supplier_id is not None:
                balance_scope = Expense.objects.filter(
                    shop_id__in=active_shop_ids,
                    supplier_id=expense.supplier_id,
                )
            else:
                balance_scope = Expense.objects.filter(
                    shop_id__in=active_shop_ids,
                    supplier__isnull=True,
                    supplier_name__iexact=(
                        expense.supplier_name or ""
                    ).strip(),
                    supplier_phone_country_code=(
                        expense.supplier_phone_country_code or ""
                    ),
                    supplier_phone_number=(
                        expense.supplier_phone_number or ""
                    ),
                )
            balance_after = sum(
                (
                    _due_amount(row.amount, row.amount_paid)
                    for row in balance_scope.only("amount", "amount_paid")
                ),
                _zero(),
            )
            number = format_simple_doc_number("E", expense.pk)
            if supplier is not None:
                transaction.on_commit(
                    lambda: _notify_supplier_after_payment(
                        kind="expense",
                        account_id=supplier.pk,
                        amount=pay_amount,
                        balance_after=balance_after,
                        receipt_ref=number,
                        shop=expense.shop,
                    )
                )
            return {
                "ok": True,
                "kind": kind,
                "receipt_id": expense.pk,
                "account_id": supplier.pk if supplier else 0,
                "receipt_due": _money_ksh(due_after),
                "receipt_due_raw": str(due_after),
                "receipt_status": _payment_status_for_due(due_after, paid),
                "receipt_status_tone": _status_tone(
                    _payment_status_for_due(due_after, paid)
                ),
                "account_balance": _money_ksh(balance_after),
                "account_balance_raw": str(balance_after),
                "message": (
                    f"Payment of {_money_ksh(pay_amount)} applied to {number}."
                ),
            }

        if kind == "shelf":
            movement = (
                ShelfStockMovement.objects.select_for_update()
                .filter(
                    pk=receipt_id,
                    shelf_item__shop_id__in=active_shop_ids,
                    movement_type=ShelfStockMovementType.IN,
                )
                .select_related("shelf_item", "shelf_item__shop")
                .first()
            )
            if movement is None:
                raise ValidationError("Shelf stock-in receipt not found.")
            receipt_total = _shelf_movement_total(movement)
            due = _due_amount(receipt_total, movement.amount_paid)
            if due <= 0:
                raise ValidationError("This receipt has no balance due.")
            if pay_amount > due:
                raise ValidationError(
                    f"Amount exceeds due on this receipt ({_money_ksh(due)})."
                )
            paid = (Decimal(movement.amount_paid or 0) + pay_amount).quantize(
                Decimal("0.01")
            )
            due_after = _due_amount(receipt_total, paid)
            if due_after <= 0:
                status = StockPaymentStatus.PAID
            elif paid > 0:
                status = StockPaymentStatus.PARTIAL
            else:
                status = StockPaymentStatus.UNPAID
            movement.amount_paid = paid
            movement.payment_status = status
            movement.save(update_fields=["amount_paid", "payment_status"])
            supplier_name = (movement.supplier_name or "").strip()
            supplier_phone = (movement.supplier_phone or "").strip()
            balance_after = _zero()
            for row in _shelf_movements_for_supplier(
                supplier_name, supplier_phone, active_shop_ids
            ).only("quantity", "buying_price", "amount_paid"):
                balance_after += _due_amount(
                    _shelf_movement_total(row), row.amount_paid
                )
            for info in _item_movements_for_identity(
                supplier_name, supplier_phone, active_shop_ids
            ).values():
                balance_after += _due_amount(
                    info["total"], info["movement"].amount_paid
                )
            number = format_simple_doc_number("ST", movement.pk)
            shop = (
                movement.shelf_item.shop
                if movement.shelf_item and movement.shelf_item.shop
                else None
            )
            transaction.on_commit(
                lambda: _notify_supplier_after_payment(
                    kind="shelf",
                    account_id=0,
                    amount=pay_amount,
                    balance_after=balance_after,
                    receipt_ref=number,
                    shop=shop,
                    supplier=_shelf_supplier_stub(supplier_name, supplier_phone),
                )
            )
            return {
                "ok": True,
                "kind": kind,
                "receipt_id": movement.pk,
                "account_id": 0,
                "receipt_due": _money_ksh(due_after),
                "receipt_due_raw": str(due_after),
                "receipt_status": _payment_status_for_due(due_after, paid),
                "receipt_status_tone": _status_tone(
                    _payment_status_for_due(due_after, paid)
                ),
                "account_balance": _money_ksh(balance_after),
                "account_balance_raw": str(balance_after),
                "message": (
                    f"Payment of {_money_ksh(pay_amount)} applied to {number}."
                ),
            }

        supplier = Supplier.objects.filter(pk=account_id).first()
        movement = (
            StockMovement.objects.select_for_update()
            .filter(
                pk=receipt_id,
                shop_id__in=active_shop_ids,
                movement_type=StockMovementType.IN,
            )
            .first()
        )
        if movement is None:
            raise ValidationError("Stock-in receipt not found.")
        receipt_total = _zero()
        if supplier is not None:
            matching_lines = list(
                _stock_lines_for_supplier(supplier, active_shop_ids).filter(
                    movement_id=movement.pk
                )
            )
        else:
            name = (supplier_name or "").strip()
            phone = (supplier_phone or "").strip()
            if not name:
                raise ValidationError("Supplier not found.")
            matching_lines = (
                _item_movements_for_identity(
                    name, phone, active_shop_ids
                )
                .get(movement.pk, {})
                .get("lines", [])
            )
        if not matching_lines:
            raise ValidationError("Stock-in receipt not found.")
        for line in matching_lines:
            qty = Decimal(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            receipt_total += (unit * qty).quantize(Decimal("0.01"))
        due = _due_amount(receipt_total, movement.amount_paid)
        if due <= 0:
            raise ValidationError("This receipt has no balance due.")
        if pay_amount > due:
            raise ValidationError(
                f"Amount exceeds due on this receipt ({_money_ksh(due)})."
            )
        paid = (Decimal(movement.amount_paid or 0) + pay_amount).quantize(
            Decimal("0.01")
        )
        due_after = _due_amount(receipt_total, paid)
        if due_after <= 0:
            status = StockPaymentStatus.PAID
        elif paid > 0:
            status = StockPaymentStatus.PARTIAL
        else:
            status = StockPaymentStatus.UNPAID
        movement.amount_paid = paid
        movement.payment_status = status
        movement.save(update_fields=["amount_paid", "payment_status"])
        movement.lines.update(payment_status=status)

        balance_after = _zero()
        if supplier is not None:
            by_movement = {}
            for line in _stock_lines_for_supplier(
                supplier, active_shop_ids
            ).select_related("movement"):
                linked = line.movement
                if linked is None:
                    continue
                info = by_movement.setdefault(
                    linked.pk,
                    {
                        "total": _zero(),
                        "paid": Decimal(linked.amount_paid or 0),
                    },
                )
                qty = Decimal(line.quantity or 0)
                unit = Decimal(line.buying_price or 0)
                info["total"] += (unit * qty).quantize(Decimal("0.01"))
            for info in by_movement.values():
                balance_after += _due_amount(info["total"], info["paid"])
        else:
            for info in _item_movements_for_identity(
                name, phone, active_shop_ids
            ).values():
                linked = info["movement"]
                balance_after += _due_amount(
                    info["total"], linked.amount_paid
                )
        if supplier is not None:
            shelf_name, shelf_phone = supplier.name, (
                f"{supplier.phone_country_code} {supplier.phone_number}"
            )
        else:
            shelf_name, shelf_phone = name, phone
        for row in _shelf_movements_for_identity(
            shelf_name, shelf_phone, active_shop_ids
        ):
            balance_after += _due_amount(
                _shelf_movement_total(row), row.amount_paid
            )
        number = format_simple_doc_number("I", movement.pk)
        transaction.on_commit(
            lambda: _notify_supplier_after_payment(
                kind="stock",
                account_id=supplier.pk if supplier else 0,
                amount=pay_amount,
                balance_after=balance_after,
                receipt_ref=number,
                shop=movement.shop,
                supplier=(
                    supplier
                    if supplier is not None
                    else _shelf_supplier_stub(name, phone)
                ),
            )
        )
        return {
            "ok": True,
            "kind": kind,
            "receipt_id": movement.pk,
            "account_id": supplier.pk if supplier else 0,
            "receipt_due": _money_ksh(due_after),
            "receipt_due_raw": str(due_after),
            "receipt_status": _payment_status_for_due(due_after, paid),
            "receipt_status_tone": _status_tone(
                _payment_status_for_due(due_after, paid)
            ),
            "account_balance": _money_ksh(balance_after),
            "account_balance_raw": str(balance_after),
            "message": (
                f"Payment of {_money_ksh(pay_amount)} applied to {number}."
            ),
        }


def _pct(part, whole) -> str:
    whole = Decimal(whole or 0)
    if whole <= 0:
        return "—"
    return f"{(Decimal(part or 0) / whole * 100).quantize(Decimal('0.1'))}%"


def _trend_hint(current, previous, *, invert: bool = False) -> tuple[str, str]:
    """Return (hint, tone) comparing current vs previous period."""
    curr = Decimal(current or 0)
    prev = Decimal(previous or 0)
    diff = curr - prev
    if prev == 0 and curr == 0:
        return "flat vs prior", "neutral"
    if prev == 0:
        tone = "bad" if invert else "good"
        if diff < 0:
            tone = "good" if invert else "bad"
        return "new vs prior", tone
    pct = (abs(diff) / abs(prev) * 100).quantize(Decimal("0.1"))
    if diff == 0:
        return "→ 0% vs prior", "neutral"
    arrow = "↑" if diff > 0 else "↓"
    rising_good = not invert
    if diff > 0:
        tone = "good" if rising_good else "bad"
    else:
        tone = "bad" if rising_good else "good"
    return f"{arrow} {pct}% vs prior", tone


def _section_href(role, slug: str, query: str = "") -> str:
    from employees.workspace import analytics_section_url

    href = analytics_section_url(role, slug)
    if query:
        return f"{href}?{query}"
    return href


def _overview_section(
    *,
    slug: str,
    title: str,
    icon: str,
    href: str,
    value: str,
    hint: str = "",
    tone: str = "neutral",
    body: str = "",
    stats: list | None = None,
    bars: list | None = None,
) -> dict:
    return {
        "slug": slug,
        "title": title,
        "icon": icon,
        "href": href,
        "value": value,
        "hint": hint,
        "tone": tone,
        "body": body,
        "stats": stats or [],
        "bars": bars or [],
    }


def _chart_scale(*values) -> Decimal:
    peak = max((abs(Decimal(v or 0)) for v in values), default=_zero())
    return peak if peak > 0 else Decimal("1")


def _bar_pct(value, scale: Decimal) -> float:
    return float(min(Decimal("100"), (abs(Decimal(value or 0)) / scale) * 100))


def _chart_bars(rows: list[tuple[str, Decimal | int | float]], *, money: bool = True) -> list:
    scale = _chart_scale(*(value for _label, value in rows))
    bars = []
    for label, value in rows:
        amount = Decimal(value or 0)
        bars.append(
            {
                "label": label,
                "display": _money_ksh(amount) if money else f"{int(amount):,}",
                "pct": _bar_pct(amount, scale),
                "negative": amount < 0,
            }
        )
    return bars


def _compare_rows(
    rows: list[tuple[str, Decimal | int | float, Decimal | int | float]],
    *,
    money: bool = True,
) -> list:
    scale = _chart_scale(*(v for row in rows for v in row[1:]))
    out = []
    for label, current, prior in rows:
        curr = Decimal(current or 0)
        prev = Decimal(prior or 0)
        out.append(
            {
                "label": label,
                "current_display": _money_ksh(curr) if money else f"{int(curr):,}",
                "prior_display": _money_ksh(prev) if money else f"{int(prev):,}",
                "current_pct": _bar_pct(curr, scale),
                "prior_pct": _bar_pct(prev, scale),
                "negative": curr < 0,
            }
        )
    return out


def _donut_slices(rows: list[tuple[str, Decimal | int | float, str]]) -> dict:
    total = sum((Decimal(value or 0) for _label, value, _tone in rows), _zero())
    slices = []
    gradient_parts = []
    cursor = Decimal("0")
    for label, value, tone in rows:
        amount = Decimal(value or 0)
        if amount <= 0 and total > 0:
            continue
        share = (
            (amount / total * 100).quantize(Decimal("0.1"))
            if total > 0
            else _zero()
        )
        start = cursor
        end = cursor + share
        cursor = end
        slices.append(
            {
                "label": label,
                "display": _money_ksh(amount),
                "pct": f"{share}%",
                "tone": tone,
            }
        )
        gradient_parts.append(f"var(--ax-donut-{tone}) {start}% {end}%")
    if not gradient_parts:
        gradient = "var(--line) 0% 100%"
    else:
        # Fill remainder if rounding left a gap.
        if cursor < 100:
            gradient_parts.append(f"var(--line) {cursor}% 100%")
        gradient = ", ".join(gradient_parts)
    return {"total": _money_ksh(total), "slices": slices, "gradient": gradient}


def _mini_compare_bars(current, previous) -> list:
    scale = _chart_scale(current, previous)
    return [
        {
            "label": "Now",
            "pct": _bar_pct(current, scale),
            "tone": "now",
        },
        {
            "label": "Prior",
            "pct": _bar_pct(previous, scale),
            "tone": "prior",
        },
    ]


def _sparkline(values, *, width: int = 120, height: int = 36) -> dict:
    nums = [float(Decimal(v or 0)) for v in values]
    if not nums:
        return {
            "points": "",
            "area": "",
            "width": width,
            "height": height,
            "empty": True,
        }
    lo = min(nums)
    hi = max(nums)
    span = (hi - lo) or 1.0
    n = len(nums)
    coords = []
    for index, value in enumerate(nums):
        x = 0.0 if n == 1 else (index / (n - 1)) * width
        y = height - 2 - ((value - lo) / span) * (height - 4)
        coords.append((x, y))
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = (
        f"M0,{height} "
        + " ".join(f"L{x:.1f},{y:.1f}" for x, y in coords)
        + f" L{width},{height} Z"
    )
    return {
        "points": points,
        "area": area,
        "width": width,
        "height": height,
        "empty": False,
        "up": nums[-1] >= nums[0],
    }


def _trend_line_chart(
    rows: list[tuple[str, Decimal | int | float, Decimal | int | float]],
    *,
    width: int = 640,
    height: int = 200,
) -> dict:
    if not rows:
        return {
            "width": width,
            "height": height,
            "sales_points": "",
            "expense_points": "",
            "sales_area": "",
            "labels": [],
            "empty": True,
        }

    sales_vals = [float(Decimal(row[1] or 0)) for row in rows]
    expense_vals = [float(Decimal(row[2] or 0)) for row in rows]
    lo = min(sales_vals + expense_vals)
    hi = max(sales_vals + expense_vals)
    span = (hi - lo) or 1.0
    pad_top, pad_bottom, pad_x = 12, 28, 8
    plot_h = height - pad_top - pad_bottom
    plot_w = width - pad_x * 2
    n = len(rows)

    def _coords(values):
        points = []
        for index, value in enumerate(values):
            x = pad_x + (0 if n == 1 else (index / (n - 1)) * plot_w)
            y = pad_top + plot_h - ((value - lo) / span) * plot_h
            points.append((x, y))
        return points

    sales_coords = _coords(sales_vals)
    expense_coords = _coords(expense_vals)
    sales_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in sales_coords)
    expense_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in expense_coords)
    sales_area = (
        f"M{sales_coords[0][0]:.1f},{height - pad_bottom} "
        + " ".join(f"L{x:.1f},{y:.1f}" for x, y in sales_coords)
        + f" L{sales_coords[-1][0]:.1f},{height - pad_bottom} Z"
    )

    # Show a few x labels only.
    label_indexes = {0, n - 1}
    if n >= 3:
        label_indexes.add(n // 2)
    if n >= 5:
        label_indexes.add(n // 4)
        label_indexes.add((3 * n) // 4)
    labels = []
    for index in sorted(label_indexes):
        x = pad_x + (0 if n == 1 else (index / (n - 1)) * plot_w)
        labels.append({"x": f"{x:.1f}", "text": rows[index][0]})

    return {
        "width": width,
        "height": height,
        "sales_points": sales_points,
        "expense_points": expense_points,
        "sales_area": sales_area,
        "labels": labels,
        "label_y": height - 8,
        "empty": all(v == 0 for v in sales_vals + expense_vals),
        "max_label": _money_ksh(hi),
        "min_label": _money_ksh(lo),
    }


def _column_chart(
    rows: list[tuple[str, Decimal | int | float]],
    *,
    width: int = 640,
    height: int = 160,
) -> dict:
    """Compact vertical column chart for count-style series."""
    if not rows:
        return {
            "width": width,
            "height": height,
            "bars": [],
            "labels": [],
            "empty": True,
        }
    vals = [float(Decimal(row[1] or 0)) for row in rows]
    hi = max(vals) or 1.0
    pad_top, pad_bottom, pad_x = 10, 24, 8
    plot_h = height - pad_top - pad_bottom
    plot_w = width - pad_x * 2
    n = len(rows)
    slot = plot_w / n
    bar_w = max(2.0, min(24.0, slot * 0.62))
    bars = []
    for i, value in enumerate(vals):
        h = max(0.0, (value / hi) * plot_h)
        x = pad_x + i * slot + (slot - bar_w) / 2
        y = pad_top + plot_h - h
        bars.append(
            {
                "x": f"{x:.1f}",
                "y": f"{y:.1f}",
                "w": f"{bar_w:.1f}",
                "h": f"{h:.1f}",
                "label": rows[i][0],
                "display": f"{int(round(value)):,}",
            }
        )
    label_indexes = {0, n - 1}
    if n >= 3:
        label_indexes.add(n // 2)
    if n >= 5:
        label_indexes.add(n // 4)
        label_indexes.add((3 * n) // 4)
    labels = [
        {"x": f"{pad_x + i * slot + slot / 2:.1f}", "text": rows[i][0]}
        for i in sorted(label_indexes)
    ]
    return {
        "width": width,
        "height": height,
        "bars": bars,
        "labels": labels,
        "label_y": height - 6,
        "empty": all(v == 0 for v in vals),
        "max_display": f"{int(round(hi)):,}",
    }


def _shop_movement_chart(
    rows: list[dict],
    *,
    width: int = 640,
    height: int = 220,
) -> dict:
    """Grouped column chart: opening / sales / closing / prior per shop."""
    if not rows:
        return {
            "width": width,
            "height": height,
            "empty": True,
            "bars": [],
            "labels": [],
            "grid": [],
            "max_label": _money_ksh(0),
        }

    series_keys = ("opening", "sales", "closing", "prior")
    values = []
    for row in rows:
        for key in series_keys:
            values.append(float(Decimal(row.get(key) or 0)))
    hi = max(values) if values else 0.0
    if hi <= 0:
        hi = 1.0

    pad_top, pad_bottom, pad_left, pad_right = 18, 36, 44, 10
    plot_h = height - pad_top - pad_bottom
    plot_w = width - pad_left - pad_right
    n = len(rows)
    group_w = plot_w / max(n, 1)
    bar_gap = 2.0
    series_n = len(series_keys)
    bar_w = max(4.0, min(18.0, (group_w - 16) / series_n - bar_gap))
    cluster_w = series_n * bar_w + (series_n - 1) * bar_gap

    def _y(value: float) -> float:
        return pad_top + plot_h - (value / hi) * plot_h

    grid = []
    for step in (0.0, 0.5, 1.0):
        y = _y(hi * step)
        amount = Decimal(str(hi * step)).quantize(Decimal("0.01"))
        if amount >= 1000:
            label = f"{(amount / 1000):.1f}k".replace(".0k", "k")
        else:
            label = f"{amount:,.0f}"
        grid.append(
            {
                "y": f"{y:.1f}",
                "label": label,
            }
        )

    bars = []
    labels = []
    for index, row in enumerate(rows):
        group_x = pad_left + index * group_w + (group_w - cluster_w) / 2
        full_label = (row.get("label") or "Shop").strip() or "Shop"
        short = full_label
        if len(short) > 12:
            parts = [part for part in short.split() if part]
            short = parts[0] if parts else short[:11]
            if len(short) > 12:
                short = short[:11] + "…"
        labels.append(
            {
                "x": f"{pad_left + index * group_w + group_w / 2:.1f}",
                "text": short,
                "title": full_label,
            }
        )
        for series_index, key in enumerate(series_keys):
            value = float(Decimal(row.get(key) or 0))
            x = group_x + series_index * (bar_w + bar_gap)
            y = _y(value)
            h = max(0.0, pad_top + plot_h - y)
            bars.append(
                {
                    "x": f"{x:.1f}",
                    "y": f"{y:.1f}",
                    "width": f"{bar_w:.1f}",
                    "height": f"{h:.1f}",
                    "series": key,
                    "title": f"{full_label} · {key}: {_money_ksh(value)}",
                }
            )

    return {
        "width": width,
        "height": height,
        "empty": all(v == 0 for v in values),
        "bars": bars,
        "labels": labels,
        "label_y": height - 10,
        "grid": grid,
        "baseline_y": f"{pad_top + plot_h:.1f}",
        "max_label": _money_ksh(hi),
        "plot_left": pad_left,
        "axis_x": pad_left - 6,
    }


def _parse_shop_ids(raw_values, shops_by_id):
    ids = []
    for raw in raw_values:
        try:
            pk = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if pk in shops_by_id:
            ids.append(pk)
    return ids


ANALYTICS_RECEIPT_STATUS_ALL = "all"
ANALYTICS_RECEIPT_STATUS_PENDING = "pending"
ANALYTICS_RECEIPT_STATUS_CONFIRMED = "confirmed"
ANALYTICS_RECEIPT_STATUS_OPTIONS = (
    (ANALYTICS_RECEIPT_STATUS_ALL, "All receipts"),
    (ANALYTICS_RECEIPT_STATUS_PENDING, "Pending receipts"),
    (ANALYTICS_RECEIPT_STATUS_CONFIRMED, "Confirmed receipts"),
)
ANALYTICS_RECEIPT_STATUS_SESSION_KEY = "analytics_receipt_status"


def _normalize_analytics_receipt_status(raw: str | None) -> str | None:
    key = (raw or "").strip().lower()
    if key in ("pending", "active"):
        return ANALYTICS_RECEIPT_STATUS_PENDING
    if key == "confirmed":
        return ANALYTICS_RECEIPT_STATUS_CONFIRMED
    if key in ("all", "any", "both"):
        return ANALYTICS_RECEIPT_STATUS_ALL
    return None


def _analytics_receipt_statuses(status_key: str | None) -> tuple[str, ...] | None:
    key = (status_key or "").strip().lower()
    if key in ("pending", "active"):
        return (ShopReceiptStatus.ACTIVE,)
    if key == "confirmed":
        return (ShopReceiptStatus.CONFIRMED, ShopReceiptStatus.PARTIAL_RETURN)
    if key in ("all", "any", "both"):
        return (
            ShopReceiptStatus.ACTIVE,
            ShopReceiptStatus.CONFIRMED,
            ShopReceiptStatus.PARTIAL_RETURN,
        )
    return None


def resolve_analytics_receipt_status(
    raw: str | None = None, request=None, default: str | None = None
) -> str:
    """Resolve all/pending/confirmed for analytics pages.

    An explicit GET value wins and is remembered on the session, so the same
    receipt set follows the user across every analytics page; otherwise the
    remembered choice applies, then ``default``, then pending. ``default`` only
    changes the final fallback when nothing was chosen or remembered.
    """
    resolved = _normalize_analytics_receipt_status(raw)
    session = getattr(request, "session", None)
    if resolved is not None:
        if session is not None and session.get(
            ANALYTICS_RECEIPT_STATUS_SESSION_KEY
        ) != resolved:
            session[ANALYTICS_RECEIPT_STATUS_SESSION_KEY] = resolved
        return resolved
    if session is not None:
        remembered = _normalize_analytics_receipt_status(
            session.get(ANALYTICS_RECEIPT_STATUS_SESSION_KEY)
        )
        if remembered is not None:
            return remembered
    fallback = _normalize_analytics_receipt_status(default)
    if fallback is not None:
        return fallback
    return ANALYTICS_RECEIPT_STATUS_PENDING


def _apply_analytics_receipt_status(qs, status_key: str | None, *, field: str = "status"):
    statuses = _analytics_receipt_statuses(status_key)
    if statuses is None:
        return qs
    return qs.filter(**{f"{field}__in": statuses})


def _settled_sale_range_q(*, start, end, prefix: str = "") -> Q:
    """Period filter honouring the paid day on settled-from-credit sales.

    Normal receipts fall into a period by ``created_at`` (print day). A credit
    that was fully paid and converted to a sale is recognised on the day the
    money was confirmed — stamped on ``confirmed_payment_at`` — so its revenue
    lands on the paid day, not the day the credit was issued. Settled rows
    with no payment stamp (legacy data) fall back to ``created_at``.
    """
    p = f"{prefix}__" if prefix else ""
    settled = f"{p}settled_from_credit"
    paid_field = f"{p}confirmed_payment_at"
    created_field = f"{p}created_at"
    paid_range = Q(**{settled: True, f"{paid_field}__isnull": False})
    normal = Q()
    if start is not None:
        paid_range &= Q(**{f"{paid_field}__gte": start})
        normal &= Q(**{f"{created_field}__gte": start})
    if end is not None:
        paid_range &= Q(**{f"{paid_field}__lt": end})
        normal &= Q(**{f"{created_field}__lt": end})
    return paid_range | (
        (Q(**{settled: False}) | Q(**{f"{paid_field}__isnull": True})) & normal
    )


def _receipts_qs(
    *,
    shop_ids,
    start,
    end,
    kinds=None,
    exclude_cancelled=True,
    receipt_status=None,
):
    qs = ShopReceipt.objects.filter(shop_id__in=shop_ids)
    if start is not None or end is not None:
        qs = qs.filter(_settled_sale_range_q(start=start, end=end))
    if kinds:
        qs = qs.filter(kind__in=kinds)
    if receipt_status:
        qs = _apply_analytics_receipt_status(qs, receipt_status)
    elif exclude_cancelled:
        qs = qs.exclude(status=ShopReceiptStatus.CANCELLED)
    return qs


def _sum_total(qs):
    return qs.aggregate(total=Coalesce(Sum("total"), _zero()))["total"] or _zero()


def _alert(level, title, detail, action="", *, href="", href_label=""):
    return {
        "level": level,
        "title": title,
        "detail": detail,
        "action": action,
        "href": href,
        "href_label": href_label,
    }


def _query_with_all_time(query: str = "") -> str:
    """Rewrite a filter query string to all-time (drop day/period/month/year fields)."""
    from urllib.parse import parse_qs, urlencode

    params = parse_qs(query, keep_blank_values=True) if query else {}
    flat = {key: values[0] for key, values in params.items()}
    for key in ("date", "date_from", "date_to", "month", "year"):
        flat.pop(key, None)
    flat["range"] = "all"
    return urlencode(flat)


def _metric(label, value, hint="", tone="neutral"):
    tone_map = {
        "success": "good",
        "ok": "good",
        "danger": "bad",
        "error": "bad",
        "warning": "warn",
        "warn": "warn",
        "good": "good",
        "bad": "bad",
        "info": "info",
        "violet": "violet",
        "cash": "cash",
        "cost": "cost",
        "neutral": "neutral",
    }
    return {
        "label": label,
        "value": value,
        "hint": hint,
        "tone": tone_map.get(tone, "neutral"),
    }


def _till_metric_row(row_label: str, expected: dict, actual: dict, variance: dict) -> dict:
    """Opening/closing row for till summary cards."""
    return {
        "label": row_label,
        "metrics": [
            {**expected, "label": "Expected"},
            {**actual, "label": "Actual"},
            {**variance, "label": "Variance", "kind": "variance"},
        ],
    }


def _till_metric_group(*, label: str, icon: str, tone: str, metrics: list[dict]) -> dict:
    return {
        "label": label,
        "icon": icon,
        "tone": tone,
        "rows": [
            _till_metric_row("Opening", metrics[0], metrics[1], metrics[2]),
            _till_metric_row("Closing", metrics[3], metrics[4], metrics[5]),
        ],
    }


def _sales_summary_board(
    *,
    total_amount,
    total_docs: int,
    cash_amount,
    mpesa_amount,
    card_amount=_zero(),
    paid_cash_amount=_zero(),
    paid_mpesa_amount=_zero(),
    paid_card_amount=_zero(),
    unpaid_credit_amount=_zero(),
    stock_amount=_zero(),
    expected_profit_amount=_zero(),
    actual_profit_amount=_zero(),
    active_shops: int = 0,
    shop_count: int = 0,
) -> dict:
    total = Decimal(total_amount or 0)
    cash = Decimal(cash_amount or 0)
    mpesa = Decimal(mpesa_amount or 0)
    card = Decimal(card_amount or 0)
    paid_cash = Decimal(paid_cash_amount or 0)
    paid_mpesa = Decimal(paid_mpesa_amount or 0)
    paid_card = Decimal(paid_card_amount or 0)
    paid_credit = paid_cash + paid_mpesa + paid_card
    unpaid_credit = Decimal(unpaid_credit_amount or 0)
    stock = Decimal(stock_amount or 0)
    expected_profit = Decimal(expected_profit_amount or 0)
    actual_profit = Decimal(actual_profit_amount or 0)
    cash_share = (
        f"{((cash / total) * Decimal('100')).quantize(Decimal('0.1'))}% of general"
        if total > 0
        else "No sales yet"
    )
    mpesa_share = (
        f"{((mpesa / total) * Decimal('100')).quantize(Decimal('0.1'))}% of general"
        if total > 0
        else "No sales yet"
    )
    card_share = (
        f"{((card / total) * Decimal('100')).quantize(Decimal('0.1'))}% of general"
        if total > 0
        else "No sales yet"
    )
    paid_share = (
        f"{((paid_credit / total) * Decimal('100')).quantize(Decimal('0.1'))}% of general"
        if total > 0
        else "No credit collections yet"
    )
    expected_margin = (
        f"{((expected_profit / total) * Decimal('100')).quantize(Decimal('0.1'))}%"
        if total > 0
        else "—"
    )
    collected = cash + mpesa + card + paid_credit
    actual_margin = (
        f"{((actual_profit / collected) * Decimal('100')).quantize(Decimal('0.1'))}%"
        if collected > 0
        else "—"
    )
    shops = active_shops or shop_count
    return {
        "hero": {
            "label": "General total",
            "value": _money_ksh(total),
            "hint": f"{int(total_docs or 0)} receipts · {shops} shop{'s' if shops != 1 else ''}",
        },
        "tiles": [
            {
                "label": "Cash",
                "value": _money_ksh(cash),
                "hint": cash_share,
                "icon": "banknote",
                "tone": "cash",
            },
            {
                "label": "M-Pesa",
                "value": _money_ksh(mpesa),
                "hint": mpesa_share,
                "icon": "smartphone",
                "tone": "mpesa",
            },
            {
                "label": "Card",
                "value": _money_ksh(card),
                "hint": card_share,
                "icon": "credit-card",
                "tone": "card",
            },
            {
                "label": "Paid cash",
                "value": _money_ksh(paid_cash),
                "hint": "Credit collections in cash",
                "icon": "banknote",
                "tone": "credits",
            },
            {
                "label": "Paid M-Pesa",
                "value": _money_ksh(paid_mpesa),
                "hint": "Credit collections via M-Pesa",
                "icon": "smartphone",
                "tone": "credits",
            },
            {
                "label": "Paid card",
                "value": _money_ksh(paid_card),
                "hint": "Credit collections via card",
                "icon": "credit-card",
                "tone": "credits",
            },
            {
                "label": "Paid total",
                "value": _money_ksh(paid_credit),
                "hint": paid_share,
                "icon": "credit-card",
                "tone": "credits",
            },
            {
                "label": "Unpaid",
                "value": _money_ksh(unpaid_credit),
                "hint": "Outstanding on open credits",
                "icon": "clock",
                "tone": "warn" if unpaid_credit > 0 else "good",
            },
            {
                "label": "Stock",
                "value": _money_ksh(stock),
                "hint": "Buying cost of items sold",
                "icon": "package",
                "tone": "cost",
            },
            {
                "label": "Expected profit",
                "value": _money_ksh(expected_profit),
                "hint": f"Selling − stock · {expected_margin}",
                "icon": "trending-up",
                "tone": "good" if expected_profit >= 0 else "warn",
            },
            {
                "label": "Actual profit",
                "value": _money_ksh(actual_profit),
                "hint": f"Collected − stock · {actual_margin}",
                "icon": "wallet",
                "tone": "good" if actual_profit >= 0 else "warn",
            },
        ],
    }


def _items_summary_board(
    *,
    total_sales,
    total_cogs,
    total_gross,
    overall_margin,
    loss_makers: int,
    item_count: int,
) -> dict:
    gross = Decimal(total_gross or 0)
    sales = Decimal(total_sales or 0)
    cogs = Decimal(total_cogs or 0)
    margin = Decimal(overall_margin or 0)
    return {
        "hero": {
            "label": "Gross profit",
            "value": _money_ksh(gross),
            "hint": (
                f"Margin {margin}% · {item_count} item{'s' if item_count != 1 else ''} sold"
            ),
            "tone": "good" if gross >= 0 else "bad",
        },
        "tiles": [
            {
                "label": "Sales value",
                "value": _money_ksh(sales),
                "hint": "Ex-tax line totals",
                "icon": "tags",
                "tone": "sales",
            },
            {
                "label": "COGS",
                "value": _money_ksh(cogs),
                "hint": "Stamped cost at sale",
                "icon": "package",
                "tone": "cost",
            },
            {
                "label": "Loss-makers",
                "value": str(loss_makers),
                "hint": "Items sold below cost",
                "icon": "alert-triangle",
                "tone": "warn" if loss_makers else "good",
            },
        ],
    }


def _credits_summary_board(
    *,
    total_amount,
    total_docs: int,
    paid_amount,
    due_amount,
    stock_amount,
    profit_amount,
    active_shops: int,
    shop_count: int,
) -> dict:
    total = Decimal(total_amount or 0)
    paid = Decimal(paid_amount or 0)
    due = Decimal(due_amount or 0)
    stock = Decimal(stock_amount or 0)
    profit = Decimal(profit_amount or 0)
    paid_share = (
        f"{((paid / total) * Decimal('100')).quantize(Decimal('0.1'))}% of total"
        if total > 0
        else "No credits yet"
    )
    due_share = (
        f"{((due / total) * Decimal('100')).quantize(Decimal('0.1'))}% of total"
        if total > 0
        else "No credits yet"
    )
    margin = (
        f"{((profit / total) * Decimal('100')).quantize(Decimal('0.1'))}%"
        if total > 0
        else "—"
    )
    shops = active_shops or shop_count
    return {
        "hero": {
            "label": "Total credits",
            "value": _money_ksh(total),
            "hint": f"{int(total_docs or 0)} receipts · {shops} shop{'s' if shops != 1 else ''}",
        },
        "tiles": [
            {
                "label": "Paid",
                "value": _money_ksh(paid),
                "hint": paid_share,
                "icon": "wallet",
                "tone": "cash",
            },
            {
                "label": "Due",
                "value": _money_ksh(due),
                "hint": due_share,
                "icon": "credit-card",
                "tone": "warn" if due > 0 else "good",
            },
            {
                "label": "Stock",
                "value": _money_ksh(stock),
                "hint": "Buying cost of items sold",
                "icon": "package",
                "tone": "cost",
            },
            {
                "label": "Profit",
                "value": _money_ksh(profit),
                "hint": f"Margin {margin}",
                "icon": "trending-up",
                "tone": "good" if profit >= 0 else "warn",
            },
        ],
    }


def _client_account_summary_board(
    *,
    balance,
    open_count: int,
    receipt_count: int,
    total_paid,
    shop_count: int,
    period_label: str = "",
) -> dict:
    due = Decimal(balance or 0) if not isinstance(balance, Decimal) else balance
    paid_total = Decimal(total_paid or 0) if not isinstance(total_paid, Decimal) else total_paid
    open_n = int(open_count or 0)
    receipts = int(receipt_count or 0)
    shops = int(shop_count or 0)
    period = (period_label or "").strip()
    scoped = bool(period) and period.lower() != "all time"
    return {
        "hero": {
            "label": "Outstanding balance",
            "value": _money_ksh(due) if isinstance(balance, Decimal) else str(balance),
            "hint": (
                f"{open_n} open credit{'s' if open_n != 1 else ''} · "
                f"{receipts} receipt{'s' if receipts != 1 else ''}"
                + (f" · {period}" if scoped else "")
            ),
            "tone": "warn" if due > 0 else "good",
        },
        "tiles": [
            {
                "label": "Open credits",
                "value": str(open_n),
                "hint": "Still owing" + (f" · {period}" if scoped else ""),
                "icon": "credit-card",
                "tone": "warn" if open_n else "good",
            },
            {
                "label": "Paid so far",
                "value": _money_ksh(paid_total),
                "hint": period if scoped else "Across all receipts",
                "icon": "wallet",
                "tone": "cash",
            },
            {
                "label": "Shops",
                "value": str(shops),
                "hint": "With credit activity",
                "icon": "store",
                "tone": "shops",
            },
        ],
    }


def _stock_summary_board(
    *,
    total_value,
    total_units: int,
    item_count: int,
    purchases,
    cogs_total,
    shrinkage_total,
    net_inventory_move,
) -> dict:
    closing = Decimal(total_value or 0)
    shrinkage = Decimal(shrinkage_total or 0)
    net_move = Decimal(net_inventory_move or 0)
    return {
        "hero": {
            "label": "Inventory value",
            "value": _money_ksh(closing),
            "hint": (
                f"{_qty_label(total_units)} units · {item_count} item"
                f"{'s' if item_count != 1 else ''} · net move {_money_dense(net_move)}"
            ),
            "tone": "good",
        },
        "tiles": [
            {
                "label": "Purchases",
                "value": _money_ksh(purchases),
                "hint": "Stock-in this period",
                "icon": "package-plus",
                "tone": "sales",
            },
            {
                "label": "COGS",
                "value": _money_ksh(cogs_total),
                "hint": "Sold this period",
                "icon": "shopping-bag",
                "tone": "cost",
            },
            {
                "label": "Shrinkage",
                "value": _money_ksh(shrinkage),
                "hint": "Waste and display at cost",
                "icon": "alert-triangle",
                "tone": "warn" if shrinkage > 0 else "good",
            },
        ],
    }


def _overview_metric_cards(
    *,
    total_sale_docs: int,
    total_sales,
    total_credit_docs: int,
    total_credits,
    total_docs: int,
    total_revenue,
    total_stock,
    total_gross,
    total_gross_pct,
    total_expense_docs: int,
    total_expenses,
    total_profit,
    total_margin,
) -> list:
    profit = Decimal(total_profit or 0)
    gross = Decimal(total_gross or 0)
    expenses = Decimal(total_expenses or 0)
    profit_tone = "good" if profit >= 0 else "bad"
    return [
        _metric(
            "All sales",
            _money_ksh(total_sales),
            hint=f"{int(total_sale_docs or 0)} receipts",
            tone="info",
        ),
        _metric(
            "All credits",
            _money_ksh(total_credits),
            hint=f"{int(total_credit_docs or 0)} receipts",
            tone="violet",
        ),
        _metric(
            "Total revenue",
            _money_ksh(total_revenue),
            hint=f"{int(total_docs or 0)} receipts · sales + credits",
            tone="cash",
        ),
        _metric(
            "Stock value sold",
            _money_ksh(total_stock),
            hint="Buying cost of items sold",
            tone="cost",
        ),
        _metric(
            "Gross margin",
            _money_ksh(gross),
            hint=f"{total_gross_pct}% of revenue",
            tone="good" if gross >= 0 else "bad",
        ),
        _metric(
            "Expenses cost used",
            _money_ksh(expenses),
            hint=(
                f"{int(total_expense_docs or 0)} expenses · "
                "excludes owner drawings"
            ),
            tone="warn" if expenses > 0 else "neutral",
        ),
        _metric(
            "Profit",
            _money_ksh(profit),
            hint="Gross margin − expenses",
            tone=profit_tone,
        ),
        _metric(
            "Profit margin",
            f"{total_margin}%",
            hint="Of total revenue",
            tone=profit_tone,
        ),
    ]


def _row_search_haystack(row) -> str:
    """Text the client-side table filter matches on for this row — mirrors
    the ``data-search-text`` attribute (explicit search_text, else the first
    cell's label/sub or plain value)."""
    if isinstance(row, dict):
        text = (row.get("search_text") or "").strip()
        if text:
            return text
        cells = row.get("cells") or []
    else:
        cells = row or []
    first = cells[0] if cells else ""
    if isinstance(first, dict):
        return f"{first.get('label') or ''} {first.get('sub') or ''}".strip()
    return str(first or "")


ANALYTICS_TABLE_PAGE_SIZE = 200


def _page_table_rows(
    rows,
    filters,
    *,
    searchable: bool,
    tail_rows: int = 0,
    page_size: int = ANALYTICS_TABLE_PAGE_SIZE,
):
    """Server-side ``?q=`` filter + ``?page=N`` slice for the big matrix
    tables (clients / suppliers / stock).

    Pinned ``tail_rows`` trailing rows (grand totals) are excluded from
    filtering and slicing and ride along on every page. ``?download`` keeps
    the full set so PDF exports are unaffected. Returns
    ``(page_rows, pager_or_None)``.
    """
    from urllib.parse import parse_qsl, urlencode

    rows = list(rows or [])
    tail = rows[-tail_rows:] if tail_rows else []
    body = rows[:-tail_rows] if tail_rows else rows
    if searchable:
        tokens = [
            token
            for token in (filters.get("search_query") or "").strip().lower().split()
            if token
        ]
        if tokens:
            body = [
                row
                for row in body
                if all(
                    token in _row_search_haystack(row).lower() for token in tokens
                )
            ]
    params = dict(parse_qsl(filters.get("query") or "", keep_blank_values=True))
    try:
        page_no = max(1, int(params.pop("page", "") or 1))
    except (TypeError, ValueError):
        page_no = 1
    total = len(body)
    if total <= page_size or params.get("download"):
        return [*body, *tail], None
    pages = (total + page_size - 1) // page_size
    page_no = min(page_no, pages)
    pager = {
        "page": page_no,
        "pages": pages,
        "total": total,
        "prev_url": (
            f"?{urlencode({**params, 'page': page_no - 1})}"
            if page_no > 1
            else ""
        ),
        "next_url": (
            f"?{urlencode({**params, 'page': page_no + 1})}"
            if page_no < pages
            else ""
        ),
    }
    start = (page_no - 1) * page_size
    return [*body[start : start + page_size], *tail], pager


def _table(
    title,
    columns,
    rows,
    empty="No data for this period.",
    footnote="",
    shop_grid=None,
    *,
    searchable=False,
    table_class="",
    card_class="",
    section="",
    pager=None,
    wide=False,
):
    if shop_grid is None:
        shop_grid = any(
            isinstance(col, dict) and (col.get("compact") or col.get("pair"))
            for col in columns
        )
    return {
        "title": title,
        "columns": columns,
        "rows": rows,
        "empty": empty,
        "footnote": footnote,
        "shop_grid": bool(shop_grid),
        "searchable": bool(searchable),
        "table_class": (table_class or "").strip(),
        "card_class": (card_class or "").strip(),
        "section": section,
        "pager": pager,
        "wide": bool(wide),
    }


def hydrate_table_zrows(page: dict | None) -> dict | None:
    """Pre-pair every table row's cells with their column dicts for the
    template.

    Done at render time (not in _table) because page post-processors still
    rewrite ``table["rows"]`` / ``table["columns"]`` afterwards — zipping
    early would leave the pairs stale. Without this the template nests a
    per-column loop inside the per-cell loop: rows × cells × columns node
    renders on the big matrix tables (clients, suppliers, stock).
    """
    for table in (page or {}).get("tables") or []:
        columns = table.get("columns") or []
        zrows = []
        for row in table.get("rows") or []:
            if isinstance(row, dict) and row.get("group_header"):
                zrows.append(
                    {"group_header": True, "label": row.get("label")}
                )
                continue
            if isinstance(row, dict):
                cells = row.get("cells") or row
            else:
                cells = row
            zrows.append(
                {
                    "row": row,
                    "cells": cells,
                    "zipped": list(zip(cells, columns)),
                }
            )
        table["zrows"] = zrows
    return page


def _insight(title, body):
    return {"title": title, "body": body}


SHRINKAGE_REASONS = frozenset(
    {
        StockOutReason.WASTE,
        StockOutReason.DISPLAY,
    }
)


def _trading_line_qs(*, shop_ids, start, end, kinds, receipt_status=None):
    qs = ShopReceiptLine.objects.filter(
        receipt__shop_id__in=shop_ids,
        receipt__kind__in=kinds,
    )
    if receipt_status:
        qs = _apply_analytics_receipt_status(
            qs, receipt_status, field="receipt__status"
        )
    else:
        qs = qs.exclude(receipt__status=ShopReceiptStatus.CANCELLED)
    if start is not None or end is not None:
        qs = qs.filter(
            _settled_sale_range_q(start=start, end=end, prefix="receipt")
        )
    return qs


def _trading_by_shop_for_period(
    *, shop_ids, start, end, kinds=None, receipt_status=None
) -> dict[int, dict]:
    """Net selling value and COGS by shop for receipts created in [start, end)."""
    kinds = list(kinds or [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT])
    trading_line_qs = _trading_line_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=kinds,
        receipt_status=receipt_status,
    )
    empty = {
        "lines": 0,
        "value": _zero(),
        "cogs": _zero(),
        "sale_value": _zero(),
        "credit_value": _zero(),
        "sale_cogs": _zero(),
        "credit_cogs": _zero(),
    }
    by_shop: dict[int, dict] = {}

    def _entry(shop_id: int) -> dict:
        row = by_shop.get(shop_id)
        if row is None:
            row = dict(empty)
            by_shop[shop_id] = row
        return row

    remaining_value = ExpressionWrapper(
        F("unit_price") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    remaining_cogs = ExpressionWrapper(
        F("unit_cost") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    for row in trading_line_qs.values("receipt__shop_id", "receipt__kind").annotate(
        lines=Count("id"),
        value=Coalesce(Sum(remaining_value), _zero()),
        cogs=Coalesce(Sum(remaining_cogs), _zero()),
    ):
        shop_id = row["receipt__shop_id"]
        kind = row["receipt__kind"]
        value = Decimal(row["value"] or 0).quantize(Decimal("0.01"))
        cogs = Decimal(row["cogs"] or 0).quantize(Decimal("0.01"))
        lines = int(row["lines"] or 0)
        entry = _entry(shop_id)
        entry["lines"] += lines
        entry["value"] += value
        entry["cogs"] += cogs
        if kind == ShopReceiptKind.SALE:
            entry["sale_value"] += value
            entry["sale_cogs"] += cogs
        elif kind == ShopReceiptKind.CREDIT:
            entry["credit_value"] += value
            entry["credit_cogs"] += cogs

    missing_cost_lines = list(
        trading_line_qs.filter(unit_cost=0)
        .exclude(item_id__isnull=True)
        .values(
            "receipt__shop_id",
            "receipt__kind",
            "item_id",
            "quantity",
            "returned_quantity",
        )
    )
    if missing_cost_lines:
        from items.services import last_buying_prices_for_items

        item_ids = {row["item_id"] for row in missing_cost_lines if row["item_id"]}
        fallback_prices = last_buying_prices_for_items(item_ids)
        for row in missing_cost_lines:
            item_id = row["item_id"]
            unit = Decimal(fallback_prices.get(item_id) or 0)
            if unit <= 0:
                continue
            remaining = max(
                Decimal("0"),
                Decimal(row["quantity"] or 0)
                - Decimal(row["returned_quantity"] or 0),
            )
            if remaining <= 0:
                continue
            extra = (unit * remaining).quantize(Decimal("0.01"))
            entry = _entry(row["receipt__shop_id"])
            entry["cogs"] += extra
            if row["receipt__kind"] == ShopReceiptKind.SALE:
                entry["sale_cogs"] += extra
            elif row["receipt__kind"] == ShopReceiptKind.CREDIT:
                entry["credit_cogs"] += extra
    return by_shop


def _employee_performance_for_period(
    *, shop_ids, start, end, receipt_status=None
) -> dict:
    """Per-seller trading rollup for receipts created in [start, end).

    Doc counts come from the sale/credit receipts; value/cost come from
    receipt lines with the same remaining-qty netting (and last-buy cost
    fallback) as the per-shop rollup in `_trading_by_shop_for_period`.
    Keyed by employee — labels resolve to "name · employee_id" like the
    Employees section.
    """
    kinds = [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT]
    receipts = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=kinds,
        receipt_status=receipt_status,
    )
    line_qs = _trading_line_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=kinds,
        receipt_status=receipt_status,
    )

    by_emp: dict = {}
    labels: dict = {}

    def _key(created_by_id):
        return created_by_id if created_by_id is not None else "anon"

    def _entry(key):
        row = by_emp.get(key)
        if row is None:
            row = {
                "label": labels.get(key) or "Staff",
                "sale_docs": 0,
                "credit_docs": 0,
                "sale_value": _zero(),
                "credit_value": _zero(),
                "cogs": _zero(),
            }
            by_emp[key] = row
        return row

    for row in receipts.values(
        "created_by_id",
        "created_by__employee_id",
        "created_by__user__first_name",
        "created_by__user__last_name",
        "created_by__user__username",
        "kind",
    ).annotate(docs=Count("id")):
        first = (row["created_by__user__first_name"] or "").strip()
        last = (row["created_by__user__last_name"] or "").strip()
        name = (
            f"{first} {last}".strip()
            or row["created_by__user__username"]
            or "Staff"
        )
        emp_id = (row["created_by__employee_id"] or "").strip()
        key = _key(row["created_by_id"])
        labels[key] = f"{name} · {emp_id}" if emp_id else name
        entry = _entry(key)
        entry["label"] = labels[key]
        docs = int(row["docs"] or 0)
        if row["kind"] == ShopReceiptKind.SALE:
            entry["sale_docs"] += docs
        elif row["kind"] == ShopReceiptKind.CREDIT:
            entry["credit_docs"] += docs

    remaining_value = ExpressionWrapper(
        F("unit_price") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    remaining_cogs = ExpressionWrapper(
        F("unit_cost") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    for row in line_qs.values(
        "receipt__created_by_id", "receipt__kind"
    ).annotate(
        value=Coalesce(Sum(remaining_value), _zero()),
        cogs=Coalesce(Sum(remaining_cogs), _zero()),
    ):
        entry = _entry(_key(row["receipt__created_by_id"]))
        value = Decimal(row["value"] or 0).quantize(Decimal("0.01"))
        cogs = Decimal(row["cogs"] or 0).quantize(Decimal("0.01"))
        entry["cogs"] += cogs
        if row["receipt__kind"] == ShopReceiptKind.SALE:
            entry["sale_value"] += value
        elif row["receipt__kind"] == ShopReceiptKind.CREDIT:
            entry["credit_value"] += value

    missing_cost_lines = list(
        line_qs.filter(unit_cost=0)
        .exclude(item_id__isnull=True)
        .values(
            "receipt__created_by_id",
            "item_id",
            "quantity",
            "returned_quantity",
        )
    )
    if missing_cost_lines:
        from items.services import last_buying_prices_for_items

        item_ids = {row["item_id"] for row in missing_cost_lines if row["item_id"]}
        fallback_prices = last_buying_prices_for_items(item_ids)
        for row in missing_cost_lines:
            unit = Decimal(fallback_prices.get(row["item_id"]) or 0)
            if unit <= 0:
                continue
            remaining = max(
                Decimal("0"),
                Decimal(row["quantity"] or 0)
                - Decimal(row["returned_quantity"] or 0),
            )
            if remaining <= 0:
                continue
            entry = _entry(_key(row["receipt__created_by_id"]))
            entry["cogs"] += (unit * remaining).quantize(Decimal("0.01"))
    return by_emp


def _cogs_by_shop_for_period(
    *, shop_ids, start, end, kinds=None, receipt_status=None
) -> dict[int, tuple[int, Decimal]]:
    """COGS from stamped sale/credit line costs (with last-buy fallback)."""
    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=kinds,
        receipt_status=receipt_status,
    )
    return {
        shop_id: (int(row["lines"] or 0), Decimal(row["cogs"] or 0))
        for shop_id, row in trading.items()
    }


def _shrinkage_by_shop_for_period(
    *, shop_ids, start, end
) -> dict[int, tuple[int, Decimal]]:
    """Waste/display stock-out cost by shop (inventory losses)."""
    shrinkage_by_shop: dict[int, tuple[int, Decimal]] = {}
    out_lines = StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__movement_type=StockMovementType.OUT,
        movement__created_at__gte=start,
        movement__created_at__lt=end,
        reason__in=SHRINKAGE_REASONS,
    )
    for row in out_lines.values("movement__shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(
            Sum(
                ExpressionWrapper(
                    F("unit_cost") * F("quantity"),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ),
            _zero(),
        ),
    ):
        shrinkage_by_shop[row["movement__shop_id"]] = (
            int(row["docs"] or 0),
            Decimal(row["amount"] or 0).quantize(Decimal("0.01")),
        )

    # Fallback for older outs stamped without unit_cost.
    missing = list(
        out_lines.filter(unit_cost=0).values(
            "movement__shop_id", "item_id", "quantity"
        )
    )
    if missing:
        from items.services import last_buying_prices_for_items

        item_ids = {row["item_id"] for row in missing if row["item_id"]}
        fallback_prices = last_buying_prices_for_items(item_ids)
        for row in missing:
            unit = Decimal(fallback_prices.get(row["item_id"]) or 0)
            if unit <= 0:
                continue
            qty = Decimal(row["quantity"] or 0)
            if qty <= 0:
                continue
            shop_id = row["movement__shop_id"]
            extra = (unit * qty).quantize(Decimal("0.01"))
            docs, amt = shrinkage_by_shop.get(shop_id, (0, _zero()))
            shrinkage_by_shop[shop_id] = (docs, amt + extra)
    return shrinkage_by_shop


def _opex_and_drawings_by_shop(expenses_qs) -> tuple[dict, dict]:
    opex_by_shop: dict[int, tuple[int, Decimal]] = {}
    drawings_by_shop: dict[int, tuple[int, Decimal]] = {}
    for row in expenses_qs.values("shop_id", "category").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("amount"), _zero()),
    ):
        shop_id = row["shop_id"]
        amount = Decimal(row["amount"] or 0)
        docs = int(row["docs"] or 0)
        if row["category"] == ExpenseCategory.OWNER_DRAWINGS:
            d_docs, d_amt = drawings_by_shop.get(shop_id, (0, _zero()))
            drawings_by_shop[shop_id] = (d_docs + docs, d_amt + amount)
        else:
            o_docs, o_amt = opex_by_shop.get(shop_id, (0, _zero()))
            opex_by_shop[shop_id] = (o_docs + docs, o_amt + amount)
    return opex_by_shop, drawings_by_shop


def _analytics_time_filter_enabled() -> bool:
    from shops.services import get_company_pos_settings

    return bool(
        getattr(
            get_company_pos_settings(), "enable_analytics_time_filter", False
        )
    )


def _parse_filter_time(raw):
    """Parse an ``HH:MM`` / ``HH:MM:SS`` time input value; None when blank/invalid."""
    from datetime import time as _time

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return _time.fromisoformat(raw)
    except ValueError:
        return None


def _apply_time_filter_bounds(start, end, *, time_from=None, time_to=None):
    """Narrow [start, end) to the picked clock times on the boundary dates.

    ``time_from`` moves the start to that time on the first day; ``time_to``
    moves the exclusive end to the end of that minute on the last day, so a
    "to 17:00" pick still counts sales made during 17:00.
    """
    from datetime import datetime, timedelta

    from django.utils import timezone

    tz = timezone.get_current_timezone()
    if time_from is not None:
        start = timezone.make_aware(
            datetime.combine(start.date(), time_from), tz
        )
    if time_to is not None:
        last_day = (end - timedelta(days=1)).date()
        end = timezone.make_aware(
            datetime.combine(last_day, time_to), tz
        ) + timedelta(minutes=1)
    return start, end


def _date_filter_context(
    request=None, *, data=None, allow_all_time=False, default_all_time=None
) -> dict:
    """Parse day / period / month / year filters. Optionally support all-time."""
    from django.utils import timezone
    from items.views import _report_range_bounds

    getter = data
    if getter is None and request is not None:
        getter = (
            request.POST
            if str(getattr(request, "method", "GET")).upper() == "POST"
            else request.GET
        )
    range_raw = ""
    time_from_raw = ""
    time_to_raw = ""
    if getter is not None and hasattr(getter, "get"):
        range_raw = (getter.get("range") or "").strip().lower()
        time_from_raw = (getter.get("time_from") or "").strip()
        time_to_raw = (getter.get("time_to") or "").strip()
    time_filter = _analytics_time_filter_enabled()
    today = timezone.localdate()
    if default_all_time is None:
        default_all_time = allow_all_time
    use_all_time = allow_all_time and (
        range_raw == "all"
        or (default_all_time and range_raw not in ("day", "period", "month", "year"))
    )
    if use_all_time:
        return {
            "report_range": "all",
            "report_date_value": today.isoformat(),
            "report_date_from": today.isoformat(),
            "report_date_to": today.isoformat(),
            "report_month_value": today.strftime("%Y-%m"),
            "report_year_value": f"{today.year}-01",
            "report_period_label": "All time",
            "range_type": "all",
            "start": None,
            "end": None,
            "prev_start": None,
            "prev_end": None,
            "time_filter_enabled": time_filter,
            "report_time_from": "",
            "report_time_to": "",
        }

    class _QueryRequest:
        GET = getter or {}

    range_type, start, end, filter_context = _report_range_bounds(
        request
        if request is not None and not allow_all_time and data is None
        else _QueryRequest()
    )
    prev_shift = end - start
    if time_filter and range_type in ("day", "period"):
        start, end = _apply_time_filter_bounds(
            start,
            end,
            time_from=_parse_filter_time(time_from_raw),
            time_to=_parse_filter_time(time_to_raw),
        )
    return {
        **filter_context,
        "range_type": range_type,
        "start": start,
        "end": end,
        "prev_start": start - prev_shift,
        "prev_end": end - prev_shift,
        "time_filter_enabled": time_filter,
        "report_time_from": time_from_raw if time_filter else "",
        "report_time_to": time_to_raw if time_filter else "",
    }


def _within_created_range(qs, start, end, *, lookup: str = "created_at"):
    if start and end:
        return qs.filter(**{f"{lookup}__gte": start, f"{lookup}__lt": end})
    return qs


def _filters_context(
    profile,
    request,
    *,
    allow_all_time=False,
    default_all_time=None,
    shop_scope: str = "default",
    default_receipt_status: str | None = None,
):
    date_filter = _date_filter_context(
        request,
        allow_all_time=allow_all_time,
        default_all_time=default_all_time,
    )
    filter_shops = actionable_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    selected_shop_ids = _parse_shop_ids(request.GET.getlist("shop_id"), shops_by_id)
    active_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]
    range_type = date_filter.get("range_type") or date_filter.get("report_range") or "day"
    receipt_status = resolve_analytics_receipt_status(
        request.GET.get("receipt_status"),
        request=request,
        default=default_receipt_status,
    )
    return {
        **date_filter,
        "filter_shops": filter_shops,
        "selected_shop_ids": selected_shop_ids,
        "active_shop_ids": active_shop_ids,
        "pos_flags": _pos_feature_flags(),
        "credits_shop_scoped": shop_scope == "allocated",
        "credits_require_shop_pick": False,
        "receipt_status": receipt_status,
        "receipt_status_options": ANALYTICS_RECEIPT_STATUS_OPTIONS,
        "receipt_status_label": dict(ANALYTICS_RECEIPT_STATUS_OPTIONS).get(
            receipt_status, "Pending receipts"
        ),
        "report_range_label": {
            "all": "All time",
            "day": "Day",
            "period": "Period",
            "month": "Month",
            "year": "Year",
        }.get(range_type, "Day"),
    }


def get_analytics_section(slug: str) -> dict:
    section = ANALYTICS_SECTION_BY_SLUG.get((slug or "").strip().lower())
    if section is None:
        raise Http404("Analytics section not found.")
    return section


def build_analytics_page(*, profile, request, section_slug: str = "overview") -> dict:
    section = get_analytics_section(section_slug)
    allow_all_time = section["slug"] in (
        "suppliers",
        "expenses",
        "supply",
        "deliveries",
        "credits",
        "tradings",
        "clients",
    )
    # Credits/tradings keep the usual day default; suppliers/expenses/supply/clients default to all-time.
    default_all_time = section["slug"] not in {"credits", "tradings"}
    filters = _filters_context(
        profile,
        request,
        allow_all_time=allow_all_time,
        default_all_time=default_all_time if allow_all_time else False,
        default_receipt_status=(
            ANALYTICS_RECEIPT_STATUS_ALL
            if section["slug"] == "receipts"
            else None
        ),
    )
    filters["role"] = profile.role
    from .module_permissions import employee_may

    filters["can_pay_accounts"] = employee_may(
        profile, "analytics", "account_pay"
    )
    filters["can_edit_company_pos"] = employee_may(
        profile, "settings", "company-pos"
    )
    filters["balances_mode"] = (
        (request.GET.get("mode") or "").strip().lower()
        if section["slug"] == "balances"
        else ""
    )
    filters["query"] = request.GET.urlencode()
    filters["search_query"] = (request.GET.get("q") or "").strip()
    filters["receipt_kind"] = (request.GET.get("receipt_kind") or "").strip().lower()
    try:
        filters["selected_employee_id"] = int(
            request.GET.get("employee_id") or 0
        )
    except (TypeError, ValueError):
        filters["selected_employee_id"] = 0
    filters["expense_category"] = (request.GET.get("category") or "").strip().lower()
    filters["supply_item_id"] = (request.GET.get("item_id") or "").strip()
    filters["supplier_status"] = _parse_supplier_status(request.GET.get("status"))
    filters["supplier_view"] = (request.GET.get("view") or "").strip().lower()
    filters["supplier_mode"] = (
        (request.GET.get("mode") or "").strip().lower()
        if section["slug"] == "suppliers"
        else ""
    )
    filters["client_scope"] = (request.GET.get("client") or "").strip().lower()
    builders = {
        "overview": _build_overview,
        "revenue": _build_revenue,
        "balances": _build_balances,
        "sales": _build_sales,
        "items": _build_items,
        "stock": _build_stock,
        "supply": _build_supply,
        "deliveries": _build_deliveries,
        "quotations": _build_quotations,
        "gifts": _build_gifts,
        "tradings": _build_tradings,
        "credits": _build_credits,
        "clients": _build_clients,
        "employees": _build_employees,
        "suppliers": _build_suppliers,
        "expenses": _build_expenses,
        "receipts": _build_receipts,
    }
    page = _apply_pos_visibility(builders[section["slug"]](filters), filters["pos_flags"])
    if section["slug"] in ANALYTICS_LIST_TABLE_SECTIONS:
        page["list_table_layout"] = True
    return {
        **filters,
        "section": section,
        "section_slug": section["slug"],
        "analytics_sections": ANALYTICS_SECTIONS,
        "include_all_time": allow_all_time,
        "page": page,
    }


def build_item_report_page(*, profile, request) -> dict:
    """Item report context — per item per shop avg qty, best day, margin."""
    filters = _filters_context(
        profile,
        request,
        allow_all_time=True,
        default_all_time=False,
    )
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    filters["search_query"] = (request.GET.get("q") or "").strip()
    page = _apply_pos_visibility(_build_item_report(filters), filters["pos_flags"])
    page["list_table_layout"] = True
    page["search_query"] = filters["search_query"]
    page["download_href"] = _pdf_download_href(filters["query"])
    return {
        **filters,
        "section_slug": "item-report",
        "include_all_time": True,
        "page": page,
    }


def build_credit_report_page(*, profile, request) -> dict:
    """Credit report context — shops rollup plus client credit accounts."""
    filters = _filters_context(
        profile,
        request,
        allow_all_time=True,
        default_all_time=False,
    )
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    filters["search_query"] = (request.GET.get("q") or "").strip()
    filters["receipt_kind"] = (request.GET.get("receipt_kind") or "").strip().lower()
    page = _build_credit_report(filters)
    page["list_table_layout"] = True
    page["search_query"] = filters["search_query"]
    return {
        **filters,
        "section_slug": "credits",
        "include_all_time": True,
        "page": page,
    }


def build_credit_clients_page(*, profile, request) -> dict:
    """Credit clients context — client credit accounts by shop."""
    filters = _filters_context(
        profile,
        request,
        allow_all_time=True,
        default_all_time=False,
    )
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    filters["search_query"] = (request.GET.get("q") or "").strip()
    filters["receipt_kind"] = "credits"
    filters["from_credits"] = True
    filters["clients_from"] = "credit-clients"
    from django.urls import reverse

    from employees.access import role_url_segment

    filters["credits_home_href"] = reverse(
        "employees:analytics_credit_clients",
        kwargs={"role_segment": role_url_segment(profile.role)},
    )
    page = _build_clients(filters)
    page["list_table_layout"] = True
    page["search_query"] = filters["search_query"]
    page["headline"] = "Credit clients"
    page["lead"] = (
        "Clients with credit receipts in the selected shops — open a client "
        "to view their credit account."
    )
    return {
        **filters,
        "section_slug": "credits",
        "include_all_time": True,
        "page": page,
    }


def analytics_receipts_list_url(role, kind: str, *, query: str = "") -> str:
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        "employees:analytics_receipts_list",
        kwargs={
            "role_segment": role_url_segment(role),
            "kind": (kind or "sales").strip().lower(),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


ANALYTICS_RECEIPT_KINDS = {
    "sales": {
        "slug": "sales",
        "label": "Sales receipts",
        "short_label": "Sales",
    },
    "credits": {
        "slug": "credits",
        "label": "Credit receipts",
        "short_label": "Credits",
    },
    "quotations": {
        "slug": "quotations",
        "label": "Quotations",
        "short_label": "Quotations",
    },
    "gifts": {
        "slug": "gifts",
        "label": "Gift receipts",
        "short_label": "Gifts",
    },
    "tradings": {
        "slug": "tradings",
        "label": "Trade outs",
        "short_label": "Tradings",
    },
    "stock": {
        "slug": "stock",
        "label": "Stock supplier receipts",
        "short_label": "Stock suppliers",
    },
    "expense": {
        "slug": "expense",
        "label": "Expense supplier receipts",
        "short_label": "Expense suppliers",
    },
    "advancements": {
        "slug": "advancements",
        "label": "Customer advancements",
        "short_label": "Advancements",
    },
    "cancelled": {
        "slug": "cancelled",
        "label": "Cancelled receipts",
        "short_label": "Cancelled",
    },
    "partial-returns": {
        "slug": "partial-returns",
        "label": "Partial returns",
        "short_label": "Partial returns",
    },
}

def _analytics_receipt_kind_filter(kind: str):
    # Converted receipts keep both identities: a credit→sale counts under
    # sales AND credits, a sale→gift under sales AND gifts.
    def _kind_both(k):
        q = Q(kind=k) | Q(converted_from_kind=k)
        if k == ShopReceiptKind.CREDIT:
            q |= Q(settled_from_credit=True)
        elif k == ShopReceiptKind.TRADE_OUT:
            q |= Q(settled_from_trade=True)
        return q

    key = (kind or "").strip().lower()
    if key == "sales":
        return _kind_both(ShopReceiptKind.SALE) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key == "credits":
        return _kind_both(ShopReceiptKind.CREDIT) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key == "quotations":
        return _kind_both(ShopReceiptKind.QUOTATION) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key in {"gift", "gifts"}:
        return _kind_both(ShopReceiptKind.GIFT) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key == "trade_in":
        return _kind_both(ShopReceiptKind.TRADE_IN) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key == "trade_out":
        return _kind_both(ShopReceiptKind.TRADE_OUT) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key in {"advancement", "advancements", "advance", "advances", "prepayment", "prepayments", "deposit", "deposits"}:
        # Advancements are CustomerPrepayment rows, not POS receipts.
        return Q(pk__in=[])
    if key in {"tradings", "trades", "trade"}:
        flags = _pos_feature_flags()
        q = Q(pk__in=[])
        if flags["trade_out"]:
            q |= Q(kind=ShopReceiptKind.TRADE_OUT)
        if flags["trade_in"]:
            q |= Q(kind=ShopReceiptKind.TRADE_IN)
        if flags["tradings"]:
            q |= Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
        return q & ~Q(status=ShopReceiptStatus.CANCELLED)
    if key == "cancelled":
        return Q(status=ShopReceiptStatus.CANCELLED)
    if key == "partial-returns":
        return Q(status=ShopReceiptStatus.PARTIAL_RETURN)
    raise Http404("Receipt type not found.")


_RECEIPT_KIND_FLAG = {
    "sales": "sale",
    "credits": "credit",
    "quotations": "quotation",
    "gifts": "gift",
    "tradings": "tradings",
}


def get_analytics_receipt_kind(kind: str) -> dict:
    spec = ANALYTICS_RECEIPT_KINDS.get((kind or "").strip().lower())
    if spec is None:
        raise Http404("Receipt type not found.")
    flag = _RECEIPT_KIND_FLAG.get(spec["slug"])
    if flag is not None and not _pos_feature_flags().get(flag, True):
        raise Http404("Receipt type not found.")
    return spec


def analytics_receipt_kind_options() -> list:
    """Receipt-type options limited to enabled company POS features."""
    return [
        spec
        for spec in ANALYTICS_RECEIPT_KINDS.values()
        if _RECEIPT_KIND_FLAG.get(spec["slug"]) is None
        or _pos_feature_flags().get(_RECEIPT_KIND_FLAG[spec["slug"]], True)
    ]


def _cashier_label(profile) -> str:
    from shops.services import employee_display_name

    return employee_display_name(profile)


def _stock_in_line_amount_expr():
    return ExpressionWrapper(
        F("buying_price") * F("quantity"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _stock_in_receipt_totals_by_shop(*, shop_ids, start, end) -> dict[int, tuple[int, Decimal]]:
    """Docs and purchase value per shop for stock-in supplier receipts."""
    by_shop: dict[int, tuple[int, Decimal]] = {}
    if not shop_ids:
        return by_shop
    for row in (
        StockMovementLine.objects.filter(
            movement__shop_id__in=shop_ids,
            movement__movement_type=StockMovementType.IN,
            movement__created_at__gte=start,
            movement__created_at__lt=end,
        )
        .values("movement_id", "movement__shop_id")
        .annotate(amount=Coalesce(Sum(_stock_in_line_amount_expr()), _zero()))
    ):
        shop_id = row["movement__shop_id"]
        docs, amount = by_shop.get(shop_id, (0, _zero()))
        by_shop[shop_id] = (
            docs + 1,
            amount + Decimal(row["amount"] or 0),
        )
    return by_shop


def _expense_receipt_totals_by_shop(*, shop_ids, start, end) -> dict[int, tuple[int, Decimal]]:
    """Docs and amount per shop for expense supplier receipts."""
    by_shop: dict[int, tuple[int, Decimal]] = {}
    if not shop_ids:
        return by_shop
    for row in (
        Expense.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .values("shop_id")
        .annotate(
            docs=Count("id"),
            amount=Coalesce(Sum("amount"), _zero()),
        )
    ):
        by_shop[row["shop_id"]] = (
            int(row["docs"] or 0),
            Decimal(row["amount"] or 0),
        )
    return by_shop


def _receipt_line_items_detail(lines) -> list:
    """Per-line item detail for the items popup on analytics receipt tables."""
    detail = []
    for line in lines:
        qty = Decimal(getattr(line, "quantity", 0) or 0)
        if qty <= 0:
            continue
        name = (getattr(line, "item_name", None) or "").strip()
        if not name and getattr(line, "item", None) is not None:
            name = (getattr(line.item, "name", None) or "").strip()
        unit = getattr(line, "unit_price", None)
        if unit is None:
            unit = getattr(line, "buying_price", None)
        total = getattr(line, "line_total", None)
        if total is None and unit is not None:
            total = (Decimal(unit) * qty).quantize(Decimal("0.01"))
        detail.append(
            {
                "name": name or "Item",
                "qty": _qty_label(qty),
                "unit": _money_ksh(unit) if unit is not None else "",
                "total": _money_ksh(total) if total is not None else "",
            }
        )
    return detail


def _items_label_from_detail(detail) -> str:
    return ", ".join(f"{item['name']} ×{item['qty']}" for item in detail) or "—"


def _receipt_line_items_label(lines) -> str:
    """Compact sold/received item list for analytics receipt tables."""
    return _items_label_from_detail(_receipt_line_items_detail(lines))


def _list_stock_supplier_receipt_rows(*, shop_ids, start, end, search="", limit=None):
    """Stock-in GRN rows for analytics receipt lists."""
    qs = (
        StockMovement.objects.filter(
            shop_id__in=shop_ids,
            movement_type=StockMovementType.IN,
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("shop", "created_by", "created_by__user")
        .prefetch_related("lines")
        .order_by("-created_at", "-id")
    )
    rows = []
    search_l = (search or "").strip().lower()
    for movement in qs:
        lines = list(movement.lines.all())
        first = lines[0] if lines else None
        supplier = (getattr(first, "supplier_name", None) or "").strip() or "—"
        dial = (getattr(first, "supplier_phone_country_code", None) or "").strip()
        phone = (getattr(first, "supplier_phone_number", None) or "").strip()
        supplier_phone = f"{dial} {phone}".strip()
        client = supplier
        if supplier_phone:
            client = f"{supplier} · {supplier_phone}"
        number = format_simple_doc_number("I", movement.pk)
        shop_name = movement.shop.name if movement.shop else "—"
        items_detail = _receipt_line_items_detail(lines)
        items = _items_label_from_detail(items_detail)
        total = _zero()
        for line in lines:
            qty = Decimal(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            total += (unit * qty).quantize(Decimal("0.01"))
        cashier = _cashier_label(getattr(movement, "created_by", None))
        pay_status = (
            getattr(first, "payment_status", None) or movement.payment_status or ""
        ).strip()
        status_label = dict(StockPaymentStatus.choices).get(
            pay_status,
            pay_status.replace("_", " ").title() if pay_status else "Recorded",
        )
        haystack = " ".join(
            [
                number,
                shop_name,
                supplier,
                supplier_phone,
                items,
                cashier,
                status_label,
                "stock",
                "supplier",
            ]
        ).lower()
        if search_l and search_l not in haystack:
            continue
        rows.append(
            {
                "number": number,
                "shop": shop_name,
                "client": client,
                "client_name": supplier,
                "phone": supplier_phone or "—",
                "items": items,
                "items_detail": items_detail,
                "total": _money_ksh(total),
                "total_raw": total,
                "status": status_label,
                "kind": "Stock purchase",
                "when": timezone.localtime(movement.created_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "when_dt": movement.created_at,
                "cashier": cashier or "—",
                "sort_id": movement.pk,
            }
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def _list_expense_supplier_receipt_rows(*, shop_ids, start, end, search="", limit=None):
    """Expense voucher rows for analytics receipt lists."""
    qs = (
        Expense.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("shop", "created_by", "created_by__user")
        .order_by("-created_at", "-id")
    )
    rows = []
    search_l = (search or "").strip().lower()
    for expense in qs:
        supplier = (expense.supplier_name or "").strip() or "—"
        dial = (expense.supplier_phone_country_code or "").strip()
        phone = (expense.supplier_phone_number or "").strip()
        supplier_phone = f"{dial} {phone}".strip()
        client = supplier
        if supplier_phone:
            client = f"{supplier} · {supplier_phone}"
        number = format_simple_doc_number("E", expense.pk)
        shop_name = expense.shop.name if expense.shop else "—"
        items = (expense.name or "").strip() or "—"
        items_detail = [
            {
                "name": items,
                "qty": "",
                "unit": "",
                "total": _money_ksh(expense.amount),
            }
        ]
        cashier = _cashier_label(getattr(expense, "created_by", None))
        haystack = " ".join(
            [
                number,
                shop_name,
                supplier,
                supplier_phone,
                items,
                cashier,
                "expense",
                "supplier",
            ]
        ).lower()
        if search_l and search_l not in haystack:
            continue
        rows.append(
            {
                "number": number,
                "shop": shop_name,
                "client": client,
                "client_name": supplier,
                "phone": supplier_phone or "—",
                "items": items,
                "items_detail": items_detail,
                "total": _money_ksh(expense.amount),
                "total_raw": Decimal(expense.amount or 0),
                "status": expense.get_payment_status_display(),
                "kind": "Expense",
                "when": timezone.localtime(expense.created_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "when_dt": expense.created_at,
                "cashier": cashier or "—",
                "sort_id": expense.pk,
            }
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def _list_prepayment_receipt_rows(*, shop_ids, start, end, search="", limit=None):
    """Customer prepayment/advancement rows for analytics receipt lists."""
    qs = (
        CustomerPrepayment.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("shop", "created_by", "created_by__user")
        .order_by("-created_at", "-id")
    )
    rows = []
    search_l = (search or "").strip().lower()
    for prepay in qs:
        number = prepayment_doc_code(prepay)
        shop_name = prepay.shop.name if prepay.shop else "—"
        customer = (prepay.customer_name or "").strip() or "—"
        phone = (prepay.customer_phone or "").strip() or "—"
        purpose = (prepay.purpose or "").strip() or "Advance payment"
        items_detail = [
            {
                "name": purpose,
                "qty": "",
                "unit": "",
                "total": _money_ksh(prepay.amount),
            }
        ]
        cashier = _cashier_label(getattr(prepay, "created_by", None))
        status_label = (
            prepay.get_payment_method_display()
            if prepay.payment_method
            else "Recorded"
        )
        haystack = " ".join(
            [
                number,
                shop_name,
                customer,
                phone,
                purpose,
                cashier,
                status_label,
                "advancement",
                "prepayment",
                "deposit",
            ]
        ).lower()
        if search_l and search_l not in haystack:
            continue
        rows.append(
            {
                "number": number,
                "shop": shop_name,
                "client": customer,
                "client_name": customer,
                "phone": phone,
                "items": purpose,
                "items_detail": items_detail,
                "total": _money_ksh(prepay.amount),
                "total_raw": Decimal(prepay.amount or 0),
                "status": status_label,
                "kind": "Advancement",
                "when": timezone.localtime(prepay.created_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "when_dt": prepay.created_at,
                "cashier": cashier or "—",
                "sort_id": prepay.pk,
            }
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def build_analytics_receipts_list(*, profile, request, kind: str) -> dict:
    """Filterable receipt list for one analytics document type across shops."""
    spec = get_analytics_receipt_kind(kind)
    filters = _filters_context(
        profile, request, default_receipt_status=ANALYTICS_RECEIPT_STATUS_ALL
    )
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    search = (request.GET.get("q") or "").strip()
    slug = spec["slug"]

    def _list_payload(rows):
        return {
            **filters,
            "kind": spec,
            "search": search,
            "rows": [
                {
                    "number": row["number"],
                    "shop": row["shop"],
                    "client": row["client"],
                    "items": row.get("items") or "—",
                    "total": row["total"],
                    "status": row["status"],
                    "kind": row["kind"],
                    "when": row["when"],
                    "cashier": row["cashier"],
                }
                for row in rows
            ],
            "total_count": len(rows),
            "returned_count": len(rows),
            "truncated": False,
            "page": {
                "headline": spec["label"],
                "lead": "",
            },
        }

    if slug == "stock":
        return _list_payload(
            _list_stock_supplier_receipt_rows(
                shop_ids=shop_ids, start=start, end=end, search=search
            )
        )

    if slug == "expense":
        return _list_payload(
            _list_expense_supplier_receipt_rows(
                shop_ids=shop_ids, start=start, end=end, search=search
            )
        )

    if slug == "advancements":
        return _list_payload(
            _list_prepayment_receipt_rows(
                shop_ids=shop_ids, start=start, end=end, search=search
            )
        )

    qs = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .filter(_analytics_receipt_kind_filter(slug))
        .select_related("shop", "created_by", "created_by__user")
        .prefetch_related("lines")
        .order_by("-created_at", "-id")
    )
    if slug not in {"cancelled", "partial-returns"}:
        qs = _apply_analytics_receipt_status(qs, filters.get("receipt_status"))
    if search:
        qs = qs.filter(
            Q(receipt_number__icontains=search)
            | Q(client_name__icontains=search)
            | Q(client_phone__icontains=search)
            | Q(shop__name__icontains=search)
            | Q(lines__item_name__icontains=search)
            | Q(converted_from_kind__icontains=search)
        ).distinct()

    rows = []
    for row in qs:
        cashier = _cashier_label(row.created_by)
        client = row.client_name or "Walk-in"
        if row.client_phone:
            client = f"{client} · {row.client_phone}"
        rows.append(
            {
                "number": receipt_tally_code(row),
                "shop": row.shop.name if row.shop else "—",
                "client": client,
                "items": _receipt_line_items_label(row.lines.all()),
                "total": _money_ksh(row.total),
                "status": row.get_status_display(),
                "kind": row.get_kind_display(),
                "when": timezone.localtime(row.created_at).strftime("%d %b %Y · %H:%M"),
                "cashier": cashier or "—",
            }
        )

    return {
        **filters,
        "kind": spec,
        "search": search,
        "rows": rows,
        "total_count": len(rows),
        "returned_count": len(rows),
        "truncated": False,
        "page": {
            "headline": spec["label"],
            "lead": "",
        },
    }


CONFIRM_RECEIPT_STATUS_FILTERS = (
    ("", "All statuses"),
    ("pending", "Pending"),
    ("partial_return", "Partial return"),
    ("cancelled", "Cancelled"),
    ("confirmed", "Confirmed"),
)

CONFIRM_RECEIPT_KIND_FILTERS = (
    ("sales_credits", "Sales & credits"),
    ("quotation", "Quotations"),
    ("gift", "Gifts"),
    ("trade_out", "Trade out"),
    ("trade_in", "Trade in"),
    ("delivery", "Delivery notes"),
    ("goods_received", "Goods received"),
    ("all", "All types"),
)

# Optional per-band cap when callers pass an explicit limit (e.g. tests).
# Page / rows / print default to None = all receipts in the filtered period.
CONFIRM_RECEIPT_DEFAULT_LIMIT = None
CONFIRM_RECEIPT_PRINT_LIMIT = None
# Always show every status: pending → cancelled → confirmed.
CONFIRM_RECEIPT_STATUS_RANK = {
    ShopReceiptStatus.ACTIVE: 0,
    "pending": 0,
    ShopReceiptStatus.PARTIAL_RETURN: 0,
    "partial_return": 0,
    ShopReceiptStatus.CANCELLED: 1,
    "cancelled": 1,
    ShopReceiptStatus.CONFIRMED: 2,
    "confirmed": 2,
}


def _confirm_receipt_status_key(raw: str | None) -> str:
    """Normalize the status query param to a CONFIRM_RECEIPT_STATUS_FILTERS key."""
    key = (raw or "").strip().lower()
    aliases = {
        "active": "pending",
        "cancel": "cancelled",
        "partial": "partial_return",
        "partial-returns": "partial_return",
        "partially_paid": "partial_return",
    }
    key = aliases.get(key, key)
    if key in dict(CONFIRM_RECEIPT_STATUS_FILTERS):
        return key
    return ""


def _confirm_receipt_apply_limit(queryset, limit):
    """Slice a queryset when limit is set; otherwise return the full ordered set."""
    ordered = queryset
    if limit is None:
        return ordered
    return ordered[: int(limit)]


def _confirm_receipt_sale_rows(
    *, shop_ids, start, end, status_key, search, limit=None, kinds=None
):
    status_map = {
        "pending": ShopReceiptStatus.ACTIVE,
        "active": ShopReceiptStatus.ACTIVE,
        "confirmed": ShopReceiptStatus.CONFIRMED,
        "partial_return": ShopReceiptStatus.PARTIAL_RETURN,
        "partial-returns": ShopReceiptStatus.PARTIAL_RETURN,
        "partial": ShopReceiptStatus.PARTIAL_RETURN,
        "cancelled": ShopReceiptStatus.CANCELLED,
        "cancel": ShopReceiptStatus.CANCELLED,
    }
    if not shop_ids:
        return []
    kind_list = list(kinds or [ShopReceiptKind.SALE])
    if not kind_list:
        return []

    # Converted receipts keep both identities — a sale→credit row answers to
    # both the sales and the credits filter.
    kind_match = Q(kind__in=kind_list) | Q(converted_from_kind__in=kind_list)
    if ShopReceiptKind.CREDIT in kind_list:
        kind_match |= Q(settled_from_credit=True)
    if ShopReceiptKind.TRADE_OUT in kind_list:
        kind_match |= Q(settled_from_trade=True)
    qs = (
        ShopReceipt.objects.filter(shop_id__in=shop_ids)
        .filter(kind_match)
        .select_related("shop", "created_by", "created_by__user")
        .annotate(
            has_customer_delivery=Exists(
                ShopDelivery.objects.filter(receipt_id=OuterRef("pk"))
            )
        )
        .only(
            "id",
            "shop_id",
            "receipt_number",
            "converted_from_kind",
            "settled_from_credit",
            "settled_from_trade",
            "client_name",
            "client_phone",
            "mpesa_receipt_number",
            "confirmed_payment_at",
            "payment_method",
            "total",
            "amount_paid",
            "kind",
            "status",
            "created_at",
            "shop__id",
            "shop__name",
            "created_by__employee_id",
            "created_by__user__first_name",
            "created_by__user__last_name",
            "created_by__user__username",
        )
    )
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    if search:
        qs = qs.filter(
            Q(receipt_number__icontains=search)
            | Q(client_name__icontains=search)
            | Q(client_phone__icontains=search)
            | Q(shop__name__icontains=search)
            | Q(converted_from_kind__icontains=search)
        )

    def _serialize(receipts):
        from shops.services import (
            credit_confirm_state,
            get_effective_pos_settings,
        )

        rows = []
        require_by_shop: dict[int, bool] = {}
        for row in receipts:
            is_credit = row.kind == ShopReceiptKind.CREDIT
            tally_code = receipt_tally_code(row)
            credit_state = credit_confirm_state(row) if is_credit else None
            is_pending = row.status == ShopReceiptStatus.ACTIVE
            is_confirmed = row.status == ShopReceiptStatus.CONFIRMED
            is_cancelled = row.status == ShopReceiptStatus.CANCELLED
            is_partial = row.status == ShopReceiptStatus.PARTIAL_RETURN
            is_partial_confirmation = bool(
                credit_state and credit_state["partially_paid"]
            )
            status_label = (
                credit_state["status_label"]
                if credit_state
                else row.get_status_display()
            )
            status_value = (
                "partial_confirmation"
                if is_partial_confirmation
                else row.status
            )
            shop_name = row.shop.name if row.shop else "—"
            client = row.client_name or "Walk-in"
            client_phone = row.client_phone or ""
            sold_by = _cashier_label(getattr(row, "created_by", None)) or "—"
            reference = (row.mpesa_receipt_number or "").strip()
            amount = Decimal(row.total or 0)
            amount_label = _money_ksh(amount)
            printed_at = getattr(row, "created_at", None)
            # "Printed" is created_at; "Paid" is the payment time entered on confirm.
            when_label = (
                timezone.localtime(printed_at).strftime("%d %b %Y · %H:%M")
                if printed_at
                else "—"
            )
            paid_at = getattr(row, "confirmed_payment_at", None)
            shows_paid = is_confirmed or is_partial or is_partial_confirmation
            paid_when_label = (
                timezone.localtime(paid_at).strftime("%d %b %Y · %H:%M")
                if paid_at and shows_paid
                else "—"
            )
            paid_via = ""
            if shows_paid:
                method = (row.payment_method or "").strip()
                if method and method != ShopPaymentMethod.NONE:
                    paid_via = row.get_payment_method_display()
            shop_pk = int(row.shop_id or 0)
            if shop_pk not in require_by_shop:
                require_by_shop[shop_pk] = bool(
                    getattr(
                        get_effective_pos_settings(row.shop),
                        "require_mpesa_reference_on_confirm",
                        False,
                    )
                )
            kind_label = row.get_kind_display()
            if bool(getattr(row, "has_customer_delivery", False)):
                sold_as = "delivery"
                sold_as_label = "Delivery"
            else:
                sold_as = "shop"
                sold_as_label = "Shop"
            if is_credit:
                # Pending/partially returned credits confirm with an amount
                # (full clear or partial).
                can_confirm = is_pending or is_cancelled or is_partial
                can_cancel = False
                can_set_pending = False
                can_edit_status = can_confirm
                status_rank = (
                    0
                    if is_partial_confirmation or is_pending
                    else CONFIRM_RECEIPT_STATUS_RANK.get(row.status, 9)
                )
                credit_clear_on_confirm = True
                credit_clear_mode = (credit_state or {}).get("clear_mode") or ""
                credit_confirm_notice = (credit_state or {}).get("confirm_notice") or ""
                credit_due = str((credit_state or {}).get("due") or "0.00")
                credit_paid = str((credit_state or {}).get("paid") or "0.00")
            elif row.kind == ShopReceiptKind.SALE:
                can_confirm = is_pending or is_cancelled or is_partial
                can_cancel = is_pending or is_confirmed or is_partial
                can_set_pending = is_confirmed or is_cancelled
                can_edit_status = True
                status_rank = CONFIRM_RECEIPT_STATUS_RANK.get(row.status, 9)
                credit_clear_on_confirm = False
                credit_clear_mode = ""
                credit_confirm_notice = ""
                credit_due = "0.00"
                credit_paid = "0.00"
            else:
                # Quotations / gifts / trades are review-only rows here —
                # they confirm through their own flows (kind change, trade
                # settlement), not the confirm-receipts actions.
                can_confirm = False
                can_cancel = False
                can_set_pending = False
                can_edit_status = False
                status_rank = CONFIRM_RECEIPT_STATUS_RANK.get(row.status, 9)
                credit_clear_on_confirm = False
                credit_clear_mode = ""
                credit_confirm_notice = ""
                credit_due = "0.00"
                credit_paid = "0.00"
            rows.append(
                {
                    "receipt_id": row.pk,
                    "shop_id": row.shop_id,
                    "source": "pos",
                    "number": tally_code,
                    "shop": shop_name,
                    "client": client,
                    "client_phone": client_phone,
                    "sold_by": sold_by,
                    "reference": reference,
                    "amount": amount,
                    "amount_label": amount_label,
                    "status": status_value,
                    "status_label": status_label,
                    "kind": row.kind,
                    "kind_label": kind_label,
                    "sold_as": sold_as,
                    "sold_as_label": sold_as_label,
                    "when": when_label,
                    "when_dt": printed_at,
                    "paid_when": paid_when_label,
                    "paid_via": paid_via,
                    "can_confirm": can_confirm,
                    "can_cancel": can_cancel,
                    "can_set_pending": can_set_pending,
                    "can_edit_status": can_edit_status,
                    "require_mpesa_reference_on_confirm": require_by_shop[shop_pk],
                    "is_partial_confirmation": is_partial_confirmation,
                    "credit_clear_on_confirm": credit_clear_on_confirm,
                    "credit_clear_mode": credit_clear_mode,
                    "credit_confirm_notice": credit_confirm_notice,
                    "credit_due": credit_due,
                    "amount_paid": credit_paid,
                    "status_rank": status_rank,
                    "search_text": " ".join(
                        [
                            row.receipt_number or "",
                            tally_code,
                            client,
                            client_phone,
                            sold_by,
                            reference,
                            amount_label,
                            shop_name,
                            status_label,
                            when_label,
                            paid_when_label if paid_when_label != "—" else "",
                            paid_via,
                            kind_label,
                            sold_as_label,
                        ]
                    ),
                }
            )
        return rows

    # Single-status lists are a straight newest-first slice.
    if status_key in status_map:
        return _serialize(
            _confirm_receipt_apply_limit(
                qs.filter(status=status_map[status_key]).order_by(
                    "-created_at", "-id"
                ),
                limit,
            )
        )

    # All statuses: fetch each band separately so a large pending set cannot
    # crowd cancelled/confirmed out of the page (pending → cancelled → confirmed).
    bands = (
        [ShopReceiptStatus.ACTIVE, ShopReceiptStatus.PARTIAL_RETURN],
        [ShopReceiptStatus.CANCELLED],
        [ShopReceiptStatus.CONFIRMED],
    )
    rows = []
    for statuses in bands:
        rows.extend(
            _serialize(
                _confirm_receipt_apply_limit(
                    qs.filter(status__in=statuses).order_by("-created_at", "-id"),
                    limit,
                )
            )
        )
    return rows


def _confirm_receipt_transfer_rows(
    *, shop_ids, start, end, status_key, search, kind_key, limit=None
):
    """Inter-shop transfer delivery notes and goods-received slips."""
    from shops.services import DOC_NUMBER_PREFIX, format_simple_doc_number

    if not shop_ids:
        return []

    qs = (
        StockMovement.objects.filter(movement_type=StockMovementType.REQUEST)
        .filter(Q(shop_id__in=shop_ids) | Q(requested_from_shop_id__in=shop_ids))
        .select_related("shop", "requested_from_shop", "created_by", "created_by__user")
        .only(
            "id",
            "shop_id",
            "requested_from_shop_id",
            "request_status",
            "created_at",
            "responded_at",
            "notes",
            "shop__id",
            "shop__name",
            "requested_from_shop__id",
            "requested_from_shop__name",
            "created_by__employee_id",
            "created_by__user__first_name",
            "created_by__user__last_name",
            "created_by__user__username",
        )
    )
    if kind_key == "delivery":
        qs = qs.filter(request_status=StockRequestStatus.PENDING)
    elif kind_key == "goods_received":
        qs = qs.filter(request_status=StockRequestStatus.FULFILLED)

    status_map = {
        "pending": StockRequestStatus.PENDING,
        "active": StockRequestStatus.PENDING,
        "confirmed": StockRequestStatus.FULFILLED,
        "cancelled": StockRequestStatus.DECLINED,
        "cancel": StockRequestStatus.DECLINED,
    }
    if status_key in status_map:
        qs = qs.filter(request_status=status_map[status_key])
    elif status_key in ("partial_return", "partial-returns", "partial"):
        return []

    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)

    ordered = qs.order_by("-created_at", "-id")
    if limit is not None:
        # Over-fetch a little so kind/status post-filters still fill the band.
        ordered = ordered[: max(int(limit) * 2, int(limit))]
    search_l = (search or "").strip().lower()
    rows = []
    for movement in ordered:
        is_fulfilled = movement.request_status == StockRequestStatus.FULFILLED
        is_declined = movement.request_status == StockRequestStatus.DECLINED
        from_shop = movement.shop
        to_shop = movement.requested_from_shop
        from_name = from_shop.name if from_shop else "—"
        to_name = to_shop.name if to_shop else "—"
        route = f"{from_name} → {to_name}"
        sold_by = _cashier_label(getattr(movement, "created_by", None)) or "—"
        if is_fulfilled:
            kind = "goods_received"
            kind_label = "Goods received"
            number = format_simple_doc_number(
                DOC_NUMBER_PREFIX["goods_received"], movement.pk
            )
            shop_id = to_shop.pk if to_shop else (from_shop.pk if from_shop else 0)
            shop_name = to_name
            when_dt = movement.responded_at or movement.created_at
            status = "confirmed"
            status_label = "Received"
            status_rank = 2
            source = "goods_received"
        else:
            kind = "delivery"
            kind_label = "Delivery note"
            number = format_simple_doc_number(
                DOC_NUMBER_PREFIX["delivery"], movement.pk
            )
            shop_id = from_shop.pk if from_shop else (to_shop.pk if to_shop else 0)
            shop_name = from_name
            when_dt = movement.created_at
            if is_declined:
                status = "cancelled"
                status_label = "Declined"
                status_rank = 1
            else:
                status = "pending"
                status_label = "In transit"
                status_rank = 0
            source = "delivery"

        if kind_key in ("delivery", "goods_received") and kind != kind_key:
            continue

        haystack = " ".join(
            [
                number,
                from_name,
                to_name,
                route,
                shop_name,
                sold_by,
                kind_label,
                status_label,
                (movement.notes or ""),
            ]
        ).lower()
        if search_l and search_l not in haystack:
            continue

        rows.append(
            {
                "receipt_id": movement.pk,
                "shop_id": shop_id,
                "source": source,
                "number": number,
                "shop": shop_name,
                "client": route,
                "client_phone": "",
                "sold_by": sold_by,
                "reference": "",
                "amount": None,
                "amount_label": "—",
                "status": status,
                "status_label": status_label,
                "kind": kind,
                "kind_label": kind_label,
                "sold_as": "",
                "sold_as_label": "—",
                "when": timezone.localtime(when_dt).strftime("%d %b %Y · %H:%M"),
                "when_dt": when_dt,
                "can_confirm": False,
                "can_cancel": False,
                "status_rank": status_rank,
                "search_text": haystack,
            }
        )
        if limit is not None and len(rows) >= int(limit):
            break
    return rows


def _confirm_receipts_count_label(kind_key: str) -> str:
    if kind_key in ("sale", "credit", "sales_credits"):
        return "receipt"
    if kind_key == "quotation":
        return "quotation"
    if kind_key == "gift":
        return "gift receipt"
    if kind_key == "trade_out":
        return "trade out note"
    if kind_key == "trade_in":
        return "trade in note"
    if kind_key == "delivery":
        return "delivery note"
    if kind_key == "goods_received":
        return "goods received slip"
    return "receipt"


def _confirm_receipts_map_receipt_kind(raw: str) -> str:
    """Map receipts-browse receipt_kind query values onto confirm kind filters."""
    key = (raw or "").strip().lower()
    if key in ("all", "any"):
        return "all"
    if key in (
        "sale",
        "sales",
        "credit",
        "credits",
        "sales_credits",
        "sale_credit",
    ):
        return "sales_credits"
    if key in ("quotation", "quotations", "quote"):
        return "quotation"
    if key in ("gift", "gifts"):
        return "gift"
    if key in ("trade_out", "trade-out", "tradeouts"):
        return "trade_out"
    if key in ("trade_in", "trade-in", "tradeins"):
        return "trade_in"
    if key == "delivery":
        return "delivery"
    if key in ("goods_received", "goods-received"):
        return "goods_received"
    return ""


def _confirm_receipts_effective_params(request) -> dict:
    """Normalize confirm-receipts filters; bare page defaults to today (day)."""
    params = request.GET.copy()
    today = timezone.localdate()
    range_raw = (params.get("range") or "").strip().lower()
    if not range_raw:
        params["range"] = "day"
        params["date"] = today.isoformat()
    elif range_raw == "day" and not (params.get("date") or "").strip():
        params["date"] = today.isoformat()
    elif range_raw == "month" and not (params.get("month") or "").strip():
        params["month"] = today.strftime("%Y-%m")
    if not (params.get("kind") or "").strip():
        mapped = _confirm_receipts_map_receipt_kind(params.get("receipt_kind"))
        params["kind"] = mapped or "all"
    # Legacy links: separate sale/credit filters merged into Sales & credits.
    if (params.get("kind") or "").strip().lower() in (
        "sale",
        "sales",
        "credit",
        "credits",
        "sale_credit",
    ):
        params["kind"] = "sales_credits"
    return params


def _confirm_receipts_query_context(*, profile, request) -> dict:
    params = _confirm_receipts_effective_params(request)

    class _ParamsRequest:
        GET = params
        method = "GET"
        user = getattr(request, "user", None)
        session = getattr(request, "session", None)

    filters = _filters_context(
        profile, _ParamsRequest(), allow_all_time=True, default_all_time=False
    )
    # Receipt lookup is client-side live search only — do not server-filter by q.
    status_key = _confirm_receipt_status_key(params.get("status"))
    flags = filters.get("pos_flags") or _pos_feature_flags()
    kind_options = tuple(
        option
        for option in CONFIRM_RECEIPT_KIND_FILTERS
        if _confirm_kind_enabled(option[0], flags)
    )
    kind_key = (params.get("kind") or "all").strip().lower()
    if kind_key not in {value for value, _label in kind_options}:
        kind_key = kind_options[0][0] if kind_options else "sales_credits"
    return {
        **filters,
        "search": "",
        "status_filter": status_key,
        "status_options": CONFIRM_RECEIPT_STATUS_FILTERS,
        "kind_filter": kind_key,
        "kind_options": kind_options,
        "count_label": _confirm_receipts_count_label(kind_key),
    }


def _confirm_kind_enabled(kind_key: str, flags: dict) -> bool:
    if kind_key == "sales_credits":
        return bool(flags.get("sale", True)) or bool(flags.get("credit", True))
    if kind_key == "sale":
        return bool(flags.get("sale", True))
    if kind_key == "credit":
        return bool(flags.get("credit", True))
    if kind_key == "quotation":
        return bool(flags.get("quotation", True))
    if kind_key == "gift":
        return bool(flags.get("gift", True))
    if kind_key == "trade_out":
        return bool(flags.get("trade_out", True))
    if kind_key == "trade_in":
        return bool(flags.get("trade_in", True))
    # Transfers ("delivery", "goods_received") and "all" are always listed.
    return True


def _confirm_receipts_collect_rows(
    *, shop_ids, start, end, status_key, search, kind_key, limit=None, flags=None
):
    """Collect confirm-receipt rows for the filtered period.

    limit=None returns every matching receipt (default for page / print).
    An explicit int still caps each status band independently.
    """
    rows = []
    band_limit = None if limit is None else int(limit)
    flags = flags or _pos_feature_flags()
    pos_kinds_map = {
        "sales_credits": [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT],
        "sale": [ShopReceiptKind.SALE],
        "credit": [ShopReceiptKind.CREDIT],
        "quotation": [ShopReceiptKind.QUOTATION],
        "gift": [ShopReceiptKind.GIFT],
        "trade_out": [ShopReceiptKind.TRADE_OUT],
        "trade_in": [ShopReceiptKind.TRADE_IN],
    }
    kind_flag_keys = {
        ShopReceiptKind.SALE: "sale",
        ShopReceiptKind.CREDIT: "credit",
        ShopReceiptKind.QUOTATION: "quotation",
        ShopReceiptKind.GIFT: "gift",
        ShopReceiptKind.TRADE_OUT: "trade_out",
        ShopReceiptKind.TRADE_IN: "trade_in",
    }
    if kind_key == "all":
        wanted_kinds = list(kind_flag_keys)
    else:
        wanted_kinds = pos_kinds_map.get(kind_key, [])
    wanted_kinds = [
        kind for kind in wanted_kinds if flags.get(kind_flag_keys[kind], True)
    ]
    if wanted_kinds:
        rows.extend(
            _confirm_receipt_sale_rows(
                shop_ids=shop_ids,
                start=start,
                end=end,
                status_key=status_key,
                search=search,
                limit=band_limit,
                kinds=wanted_kinds,
            )
        )
    if kind_key in ("delivery", "goods_received", "all"):
        rows.extend(
            _confirm_receipt_transfer_rows(
                shop_ids=shop_ids,
                start=start,
                end=end,
                status_key=status_key,
                search=search,
                kind_key=kind_key if kind_key != "all" else "all",
                limit=band_limit,
            )
        )

    # "all" runs the sale + credit queries — a converted receipt matches both
    # (kind and converted_from_kind), so drop the duplicate.
    seen_rows = set()
    deduped = []
    for row in rows:
        key = (row.get("source"), row.get("receipt_id"))
        if key in seen_rows:
            continue
        seen_rows.add(key)
        deduped.append(row)
    rows = deduped

    rows.sort(
        key=lambda row: (
            row.get("status_rank", 9),
            -(row.get("when_dt").timestamp() if row.get("when_dt") else 0),
            -int(row.get("receipt_id") or 0),
        )
    )
    pending = [row for row in rows if int(row.get("status_rank", 9)) == 0]
    cancelled = [row for row in rows if int(row.get("status_rank", 9)) == 1]
    confirmed = [row for row in rows if int(row.get("status_rank", 9)) >= 2]
    if band_limit is None:
        return pending + cancelled + confirmed
    # Cap each band independently so pending volume never drops cancelled/confirmed.
    return pending[:band_limit] + cancelled[:band_limit] + confirmed[:band_limit]


def _confirm_rows_apply_permissions(rows, profile):
    """Hide cancel/undo affordances when the profile may not return receipts."""
    from .module_permissions import employee_may

    if employee_may(profile, "analytics", "return-receipts"):
        return rows
    for row in rows:
        row["can_cancel"] = False
        # Confirmed receipts may only be set back to pending under the same
        # return/undo toggle.
        if row.get("status") == "confirmed":
            row["can_set_pending"] = False
    return rows


def serialize_confirm_receipt_rows(rows) -> list[dict]:
    """JSON-safe row payload for async list rendering."""
    payload = []
    for row in rows:
        amount = row.get("amount")
        amount_label = row.get("amount_label")
        if amount_label in (None, ""):
            amount_label = _money_ksh(amount) if amount is not None else "—"
        reference = (row.get("reference") or "").strip()
        payload.append(
            {
                "receipt_id": int(row.get("receipt_id") or 0),
                "shop_id": int(row.get("shop_id") or 0),
                "source": row.get("source") or "pos",
                "number": row.get("number") or "",
                "client": row.get("client") or "",
                "client_phone": row.get("client_phone") or "",
                "sold_by": row.get("sold_by") or "—",
                "reference": reference,
                "amount": (
                    float(amount)
                    if isinstance(amount, (int, float, Decimal))
                    else None
                ),
                "amount_label": amount_label,
                "status": row.get("status") or "",
                "status_label": row.get("status_label") or "",
                "when": row.get("when") or "",
                "paid_when": row.get("paid_when") or "—",
                "paid_via": row.get("paid_via") or "",
                "can_confirm": bool(row.get("can_confirm")),
                "can_cancel": bool(row.get("can_cancel")),
                "can_set_pending": bool(row.get("can_set_pending")),
                "can_edit_status": bool(row.get("can_edit_status")),
                "require_mpesa_reference_on_confirm": bool(
                    row.get("require_mpesa_reference_on_confirm")
                ),
                "kind": row.get("kind") or "sale",
                "kind_label": row.get("kind_label") or "",
                "sold_as": row.get("sold_as") or "",
                "sold_as_label": row.get("sold_as_label") or "—",
                "is_partial_confirmation": bool(row.get("is_partial_confirmation")),
                "credit_clear_on_confirm": bool(row.get("credit_clear_on_confirm")),
                "credit_clear_mode": row.get("credit_clear_mode") or "",
                "credit_confirm_notice": row.get("credit_confirm_notice") or "",
                "credit_due": str(row.get("credit_due") or "0.00"),
                "amount_paid": str(row.get("amount_paid") or "0.00"),
                "search_text": row.get("search_text")
                or " ".join(
                    [
                        str(row.get("number") or ""),
                        str(row.get("client") or ""),
                        str(row.get("client_phone") or ""),
                        str(row.get("sold_by") or ""),
                        reference,
                        str(amount_label or ""),
                        str(row.get("shop") or ""),
                        str(row.get("status_label") or ""),
                        str(row.get("kind_label") or ""),
                        str(row.get("sold_as_label") or ""),
                    ]
                ),
            }
        )
    return payload


def build_confirm_receipts_rows(*, profile, request) -> dict:
    """Lean row payload for the confirm-receipts async list."""
    context = _confirm_receipts_query_context(profile=profile, request=request)
    rows = _confirm_rows_apply_permissions(
        _confirm_receipts_collect_rows(
            shop_ids=context["active_shop_ids"],
            start=context["start"],
            end=context["end"],
            status_key=context["status_filter"],
            search=context["search"],
            kind_key=context["kind_filter"],
        ),
        profile,
    )
    return {
        "ok": True,
        "rows": serialize_confirm_receipt_rows(rows),
        "total_count": len(rows),
        "count_label": context["count_label"],
        "status_filter": context["status_filter"],
        "kind_filter": context["kind_filter"],
        "search": context["search"],
        "report_period_label": context.get("report_period_label") or "All time",
    }


def build_confirm_receipts_page(*, profile, request, include_rows: bool = True) -> dict:
    """Confirm-receipts page shell (optionally with rows for tests / fallback)."""
    from django.urls import reverse

    from employees.access import role_url_segment

    context = _confirm_receipts_query_context(profile=profile, request=request)
    rows = []
    if include_rows:
        rows = _confirm_rows_apply_permissions(
            _confirm_receipts_collect_rows(
                shop_ids=context["active_shop_ids"],
                start=context["start"],
                end=context["end"],
                status_key=context["status_filter"],
                search=context["search"],
                kind_key=context["kind_filter"],
            ),
            profile,
        )

    segment = role_url_segment(profile.role)
    detail_template = reverse(
        "employees:analytics_receipt_detail",
        kwargs={
            "role_segment": segment,
            "shop_id": 0,
            "receipt_id": 0,
        },
    )
    confirm_template = reverse(
        "employees:analytics_receipt_confirm",
        kwargs={
            "role_segment": segment,
            "shop_id": 0,
            "receipt_id": 0,
        },
    )
    cancel_template = reverse(
        "employees:analytics_receipt_cancel",
        kwargs={
            "role_segment": segment,
            "shop_id": 0,
            "receipt_id": 0,
        },
    )
    set_status_template = reverse(
        "employees:analytics_receipt_set_status",
        kwargs={
            "role_segment": segment,
            "shop_id": 0,
            "receipt_id": 0,
        },
    )
    rows_url = reverse(
        "employees:analytics_confirm_receipts_rows",
        kwargs={"role_segment": segment},
    )
    print_url = reverse(
        "employees:analytics_confirm_receipts_print",
        kwargs={"role_segment": segment},
    )
    verify_url = reverse(
        "employees:analytics_receipt_verify_login",
        kwargs={"role_segment": segment},
    )

    return {
        **context,
        "rows": rows,
        "total_count": len(rows),
        "async_rows": not include_rows,
        "page": {
            "headline": "Confirm receipts",
            "lead": (
                "Tick receipts in place or live-search a number. "
                "Set selected sales to pending, confirmed, or cancelled. "
                "Credits confirm with an amount — full balance clears to a confirmed sale; "
                "a smaller amount records a partial confirmation. "
                "Delivery notes and goods received slips are listed for review only."
            ),
            "detail_url_template": detail_template,
            "confirm_url_template": confirm_template,
            "cancel_url_template": cancel_template,
            "set_status_url_template": set_status_template,
            "rows_url": rows_url,
            "print_url": print_url,
            "verify_login_url": verify_url,
        },
    }


def build_confirm_receipts_print_document(*, profile, request) -> dict:
    """Printable confirm-receipts list for the current page filters."""
    context = _confirm_receipts_query_context(profile=profile, request=request)
    rows = _confirm_receipts_collect_rows(
        shop_ids=context["active_shop_ids"],
        start=context["start"],
        end=context["end"],
        status_key=context["status_filter"],
        search=context["search"],
        kind_key=context["kind_filter"],
        limit=CONFIRM_RECEIPT_PRINT_LIMIT,
    )
    kind_label = dict(CONFIRM_RECEIPT_KIND_FILTERS).get(
        context["kind_filter"], "Sales"
    )
    period_label = context.get("report_period_label") or "All time"
    shop_label = context.get("shop_filter_label") or "All shops"
    grand = _zero()
    print_rows = []
    for row in rows:
        amount = row.get("amount")
        if isinstance(amount, (int, float, Decimal)):
            grand += Decimal(amount)
        print_rows.append(
            {
                "number": row.get("number") or "—",
                "status": row.get("status_label") or row.get("status") or "—",
                "kind": row.get("kind_label") or row.get("kind") or "—",
                "client_name": row.get("client") or "—",
                "phone": row.get("client_phone") or "—",
                "shop": row.get("shop") or "—",
                "sold_by": row.get("sold_by") or "—",
                "sold_as": row.get("sold_as_label") or "—",
                "total": row.get("amount_label")
                or (_money_ksh(amount) if amount is not None else "—"),
                "reference": (row.get("reference") or "").strip() or "—",
                "when": row.get("when") or "—",
                "paid_when": row.get("paid_when") or "—",
                "paid_via": row.get("paid_via") or "",
            }
        )
    receipt_total = len(print_rows)
    a4_page_estimate = max(1, (receipt_total + 27) // 28)
    return {
        "title": "Confirm receipts",
        "kind_label": kind_label,
        "period_label": period_label,
        "shop_label": shop_label,
        "receipt_total": receipt_total,
        "grand_amount": _money_ksh(grand),
        "a4_page_estimate": a4_page_estimate,
        "rows": print_rows,
        "count_label": context.get("count_label") or "receipt",
    }


def build_confirm_receipts_report_pdf(
    *, document: dict, company_name: str = ""
) -> bytes:
    """Flat landscape A4 ledger PDF of the filtered confirm-receipts list."""
    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    kind_label = document.get("kind_label") or "Receipts"
    period_label = document.get("period_label") or "All time"
    shop_label = document.get("shop_label") or "All shops"

    headers = [
        "Receipt",
        "Status",
        "Client",
        "Shop",
        "Sold",
        "Amount",
        "Ref · Paid",
        "Printed",
    ]
    aligns = ["left", "left", "left", "left", "left", "right", "left", "left"]
    rows = []
    for row in document.get("rows") or []:
        client = str(row.get("client_name") or "—")
        phone = str(row.get("phone") or "").strip()
        if phone and phone != "—":
            client = f"{client} · {phone}"
        sold = str(row.get("sold_by") or "—")
        sold_as = str(row.get("sold_as") or "").strip()
        if sold_as and sold_as != "—":
            sold = f"{sold} · {sold_as}"
        ref_paid = str(row.get("reference") or "—").strip() or "—"
        paid = " · ".join(
            bit
            for bit in (
                str(row.get("paid_when") or "").strip(),
                str(row.get("paid_via") or "").strip(),
            )
            if bit and bit != "—"
        )
        if paid:
            ref_paid = paid if ref_paid == "—" else f"{ref_paid} · {paid}"
        rows.append(
            [
                row.get("number") or "—",
                row.get("status") or "—",
                client,
                row.get("shop") or "—",
                sold,
                row.get("total") or "—",
                ref_paid,
                row.get("when") or "—",
            ]
        )
    rows.append(
        {
            "kind": "total",
            "cells": [
                f"Total · {document.get('receipt_total') or 0} "
                f"{document.get('count_label') or 'receipt'}"
                f"{'' if document.get('receipt_total') == 1 else 's'}",
                "",
                "",
                "",
                "",
                document.get("grand_amount") or "—",
                "",
                "",
            ],
        }
    )

    return _analytics_report_pdf(
        title="Confirm receipts",
        subtitle=(
            "Filtered confirm-receipts list — pending first, "
            "then cancelled, then confirmed."
        ),
        brand=brand,
        meta_lines=[shop_label, period_label, kind_label],
        kpi_cards=[],
        sections=[
            {
                "kicker": "CONFIRM RECEIPTS",
                "title": f"{kind_label} · {period_label}",
                "headers": headers,
                "aligns": aligns,
                "rows": rows,
                "empty": "No receipts for the selected shops and period.",
                "footnote": "",
            }
        ],
        footer_note=(
            f"{brand} · Confirm receipts · {shop_label} · "
            f"{period_label} · {kind_label}"
        ),
    )


def confirm_receipts_report_pdf_response(*, document: dict, inline: bool = False):
    """PDF download/print for the filtered confirm-receipts list."""
    from django.http import HttpResponse
    from django.utils.text import slugify

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_confirm_receipts_report_pdf(
        document=document, company_name=company_name
    )
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    kind = slugify(document.get("kind_label") or "receipts") or "receipts"
    filename = f"confirm-receipts-{kind}-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def client_credit_account_url(
    role, client_id, *, query: str = "", from_credits: bool = False
) -> str:
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        (
            "employees:analytics_client_credit"
            if from_credits
            else "employees:analytics_client_account"
        ),
        kwargs={
            "role_segment": role_url_segment(role),
            "client_id": int(client_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


def build_client_credit_account(*, profile, client_id: int, request=None) -> dict:
    """Credit ledger and outstanding balance for one client (optionally period-scoped)."""
    shop_filter = _allocated_shop_filter(profile, request)
    shop_ids = shop_filter["active_shop_ids"]
    date_filter = _date_filter_context(request, allow_all_time=True)
    start, end = date_filter["start"], date_filter["end"]
    period_label = date_filter.get("report_period_label") or "All time"
    scope_hint = (
        shop_filter["selected_shops"][0].name
        if len(shop_filter["selected_shops"]) == 1
        else shop_filter["shop_filter_label"]
    )

    client = Client.objects.filter(pk=client_id).first()
    if client is None or not shop_ids:
        raise Http404("Client not found.")
    if not (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exists()
    ):
        raise Http404("Client not found.")

    all_credit_qs = (
        ShopReceipt.objects.filter(
            client_id=client.pk,
            shop_id__in=shop_ids,
        )
        .filter(_credit_account_receipts_q())
        .exclude(status=ShopReceiptStatus.CANCELLED)
    )

    all_count = all_credit_qs.count()
    receipts = list(
        _within_created_range(all_credit_qs, start, end)
        .select_related("shop", "created_by", "created_by__user")
        .prefetch_related("lines")
        .order_by("-created_at")
    )
    from django.utils import timezone

    today = timezone.localdate()
    balance = _zero()
    open_count = 0
    total_paid = _zero()
    shop_ids_seen: set[int] = set()
    rows = []
    for row in receipts:
        cashier = ""
        if row.created_by and row.created_by.user:
            cashier = (
                row.created_by.user.get_full_name()
                or row.created_by.employee_id
                or row.created_by.user.username
            )
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
                    "qty": _qty_number(qty),
                    "unit": _money_ksh(unit),
                    "total": _money_ksh((unit * qty).quantize(Decimal("0.01"))),
                }
            )
        total = Decimal(row.total or 0)
        paid = Decimal(row.amount_paid or 0)
        due = _due_amount(total, paid)
        balance += due
        total_paid += paid
        if row.shop_id:
            shop_ids_seen.add(row.shop_id)
        if due > 0:
            open_count += 1
        status = _payment_status_for_due(due, paid)
        pay_by = ""
        pay_by_raw = ""
        pay_by_overdue = False
        if row.credit_due_date:
            pay_by = row.credit_due_date.strftime("%d %b %Y")
            pay_by_raw = row.credit_due_date.isoformat()
            pay_by_overdue = due > 0 and row.credit_due_date < today
        rows.append(
            {
                "id": f"credit-{row.pk}",
                "pay_kind": "credit",
                "pay_id": row.pk,
                "number": row.receipt_number,
                "shop": row.shop.name if row.shop else "—",
                "status": status,
                "status_tone": _status_tone(status),
                "total": _money_ksh(total),
                "paid": _money_ksh(paid),
                "due": _money_ksh(due),
                "due_raw": str(due),
                "can_pay": due > 0,
                "can_manage": True,
                "when": row.created_at,
                "pay_by": pay_by,
                "pay_by_raw": pay_by_raw,
                "pay_by_overdue": pay_by_overdue,
                "cashier": cashier or "—",
                "item_count": len(lines),
                "lines": lines,
            }
        )

    # Open balances first (newest → oldest), then paid/zero-due at the bottom.
    rows.sort(
        key=lambda item: (
            Decimal(item.get("due_raw") or 0) <= 0,
            -(item["when"].timestamp() if item.get("when") else 0),
        )
    )

    other_count = max(0, all_count - len(rows))
    other_credits_alert = None
    all_credits_href = ""
    if start is not None and end is not None and other_count > 0:
        query = ""
        from_credits = False
        if request is not None:
            getter = getattr(request, "GET", None)
            query = getter.urlencode() if getter is not None else ""
            from_credits = "/analytics/credits/" in (getattr(request, "path", "") or "")
        all_query = _query_with_all_time(query)
        all_credits_href = client_credit_account_url(
            profile.role,
            client.pk,
            query=all_query,
            from_credits=from_credits,
        )
        other_credits_alert = _alert(
            "info",
            "Other credits outside this period",
            (
                f"{other_count} more credit receipt"
                f"{'' if other_count == 1 else 's'} for this client "
                f"outside {period_label}."
            ),
            href=all_credits_href,
            href_label="View other credits",
        )

    empty_scope = (
        f"at {scope_hint}" if shop_filter["selected_shop_ids"] else "in your shops"
    )
    follow_up = ""
    follow_up_raw = ""
    follow_up_overdue = False
    if client.credit_follow_up_date:
        follow_up = client.credit_follow_up_date.strftime("%d %b %Y")
        follow_up_raw = client.credit_follow_up_date.isoformat()
        follow_up_overdue = balance > 0 and client.credit_follow_up_date < today
    return {
        "client": client,
        "balance": _money_ksh(balance),
        "balance_raw": str(balance),
        "credit_count": open_count,
        "receipt_count": len(rows),
        "follow_up": follow_up,
        "follow_up_raw": follow_up_raw,
        "follow_up_overdue": follow_up_overdue,
        "summary_board": _client_account_summary_board(
            balance=balance,
            open_count=open_count,
            receipt_count=len(rows),
            total_paid=total_paid,
            shop_count=len(shop_ids_seen),
            period_label=period_label,
        ),
        "rows": rows,
        "ledger_title": "Credit receipts",
        "empty_message": (
            f"No credit receipts for this client {empty_scope} ({period_label})."
        ),
        "account_kind": "credit",
        "account_id": client.pk,
        "can_pay": balance > 0,
        "ledger_show_receipt_pay": True,
        "ledger_show_select": True,
        "scope_hint": scope_hint,
        "other_credits_count": other_count,
        "other_credits_alert": other_credits_alert,
        "all_credits_href": all_credits_href,
        **shop_filter,
        **date_filter,
        **_pay_method_context(),
    }


def _pay_method_context() -> dict:
    """Payment-method radios for the credit account-pay modals."""
    flags = _pos_feature_flags()
    return {
        "pos_pay_cash": flags["pay_cash"],
        "pos_pay_mpesa": flags["pay_mpesa"],
        "pos_pay_card": flags["pay_card"],
    }


def _client_transaction_kind_meta(row) -> tuple[str, str, str, str, str, Decimal, bool]:
    """Return kind_label, status, tone, paid_label, due_label, due_raw, is_credit.

    Status rules for the clients transactions page:
    - Sales → Paid
    - Credits → Paid when cleared, otherwise Unpaid
    """
    total = Decimal(row.total or 0)
    paid = Decimal(row.amount_paid or 0)
    is_credit = bool(
        row.kind == ShopReceiptKind.CREDIT
        or (row.kind == ShopReceiptKind.SALE and row.settled_from_credit)
    )
    if row.kind == ShopReceiptKind.QUOTATION:
        return "Quotation", "Quotation", "neutral", "—", "—", _zero(), False
    if row.kind in {ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN}:
        from shops.trade_settlement import trade_balance_due

        due = trade_balance_due(row)
        paid_cash = Decimal(row.amount_paid or 0)
        status = "Open" if due > 0 else "Cleared"
        label = "Trade in" if row.kind == ShopReceiptKind.TRADE_IN else "Trade out"
        return (
            label,
            status,
            "warn" if due > 0 else "good",
            _money_ksh(paid_cash),
            _money_ksh(due),
            due,
            False,
        )
    if is_credit:
        due = _due_amount(total, paid)
        status = "Paid" if due <= 0 else "Unpaid"
        return (
            "Credit",
            status,
            _status_tone(status),
            _money_ksh(paid),
            _money_ksh(due),
            due,
            True,
        )
    return (
        "Sale",
        "Paid",
        _status_tone("Paid"),
        _money_ksh(paid),
        _money_ksh(_zero()),
        _zero(),
        False,
    )


def build_client_transactions(*, profile, client_id: int, request=None) -> dict:
    """All client transactions (sales/credits/quotations) with filters for Clients analytics."""
    shop_filter = _allocated_shop_filter(profile, request)
    shop_ids = shop_filter["active_shop_ids"]
    date_filter = _date_filter_context(request, allow_all_time=True, default_all_time=True)
    start, end = date_filter["start"], date_filter["end"]
    period_label = date_filter.get("report_period_label") or "All time"
    scope_hint = (
        shop_filter["selected_shops"][0].name
        if len(shop_filter["selected_shops"]) == 1
        else shop_filter["shop_filter_label"]
    )

    raw_kind = ""
    query = ""
    if request is not None:
        getter = getattr(request, "GET", None)
        if getter is not None:
            query = getter.urlencode()
            raw_kind = (getter.get("receipt_kind") or "").strip()
    flags = _pos_feature_flags()
    receipt_kind = _enabled_client_receipt_kind(
        _parse_client_receipt_kind(raw_kind), flags
    )

    client = Client.objects.filter(pk=client_id).first()
    if client is None or not shop_ids:
        raise Http404("Client not found.")
    if not (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .filter(_client_activity_receipts_q("all", flags))
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exists()
    ):
        raise Http404("Client not found.")

    base_qs = (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .filter(_client_activity_receipts_q(receipt_kind, flags))
        .exclude(status=ShopReceiptStatus.CANCELLED)
    )
    all_count = base_qs.count()
    receipts = list(
        _within_created_range(base_qs, start, end)
        .select_related("shop", "created_by", "created_by__user")
        .prefetch_related("lines")
        .order_by("-created_at")
    )

    sales_total = _zero()
    credits_total = _zero()
    quotations_total = _zero()
    trade_out_total = _zero()
    trade_in_total = _zero()
    sales_count = 0
    credits_count = 0
    quotations_count = 0
    trade_out_count = 0
    trade_in_count = 0
    shop_ids_seen: set[int] = set()
    rows = []
    item_rows = []
    for row in receipts:
        cashier = ""
        if row.created_by and row.created_by.user:
            cashier = (
                row.created_by.user.get_full_name()
                or row.created_by.employee_id
                or row.created_by.user.username
            )
        lines = []
        for line in row.lines.all():
            qty = Decimal(line.quantity or 0) - Decimal(
                line.returned_quantity or 0
            )
            if qty <= 0:
                continue
            unit = Decimal(line.unit_price or 0)
            line_total = (unit * qty).quantize(Decimal("0.01"))
            lines.append(
                {
                    "name": line.item_name or "Item",
                    "qty": _qty_number(qty),
                    "unit": _money_ksh(unit),
                    "total": _money_ksh(line_total),
                }
            )
        total = Decimal(row.total or 0)
        (
            kind_label,
            status,
            status_tone,
            paid_label,
            due_label,
            due,
            is_credit,
        ) = _client_transaction_kind_meta(row)
        if row.kind == ShopReceiptKind.QUOTATION:
            quotations_total += total
            quotations_count += 1
        elif row.kind == ShopReceiptKind.TRADE_OUT:
            trade_out_total += total
            trade_out_count += 1
        elif row.kind == ShopReceiptKind.TRADE_IN:
            trade_in_total += total
            trade_in_count += 1
        elif is_credit:
            credits_total += total
            credits_count += 1
        else:
            sales_total += total
            sales_count += 1
        if row.shop_id:
            shop_ids_seen.add(row.shop_id)
        when_label = ""
        date_label = ""
        if row.created_at:
            local_when = timezone.localtime(row.created_at)
            when_label = local_when.strftime("%d %b %Y · %H:%M")
            date_label = local_when.strftime("%d %b %Y")
        shop_name = row.shop.name if row.shop else "—"
        for line in lines:
            item_rows.append(
                {
                    "date": date_label or "—",
                    "when_label": when_label or "—",
                    "type": kind_label,
                    "receipt": row.receipt_number,
                    "shop": shop_name,
                    "item": line["name"],
                    "qty": line["qty"],
                    "unit": line["unit"],
                    "total": line["total"],
                    "status": status,
                    "search_text": (
                        f"{date_label} {when_label} {kind_label} "
                        f"{row.receipt_number} {shop_name} {line['name']}"
                    ).strip(),
                }
            )
        rows.append(
            {
                "id": f"{row.kind}-{row.pk}",
                "pay_id": row.pk,
                "number": row.receipt_number,
                "shop": shop_name,
                "kind": row.kind,
                "kind_label": kind_label,
                "status": status,
                "status_tone": status_tone,
                "total": _money_ksh(total),
                "total_raw": str(total.quantize(Decimal("0.01"))),
                "paid": paid_label,
                "due": due_label,
                "due_raw": str(due),
                "when": row.created_at,
                "when_label": when_label,
                "date_label": date_label,
                "cashier": cashier or "—",
                "item_count": len(lines),
                "lines": lines,
                "can_pay": False,
                "can_manage": False,
            }
        )

    period_scoped = start is not None and end is not None
    other_alert = None
    all_time_href = ""
    other_count = max(0, all_count - len(rows)) if period_scoped else 0
    if period_scoped and other_count > 0:
        all_query = _query_with_all_time(query)
        all_time_href = client_credit_account_url(
            profile.role, client.pk, query=all_query, from_credits=False
        )
        other_alert = _alert(
            "info",
            "Other transactions outside this period",
            (
                f"{other_count} more transaction"
                f"{'' if other_count == 1 else 's'} for this client "
                f"outside {period_label}."
            ),
            href=all_time_href,
            href_label="View all time",
        )

    empty_scope = (
        f"at {scope_hint}" if shop_filter["selected_shop_ids"] else "in your shops"
    )
    kind_titles = {
        "all": "All transactions",
        "sales": "Sales",
        "credits": "Credits",
        "quotations": "Quotations",
        "trade_out": "Trade outs",
        "trade_in": "Trade ins",
        "tradings": "Trades",
    }
    kind_empty = {
        "all": "transactions",
        "sales": "sales",
        "credits": "credits",
        "quotations": "quotations",
        "trade_out": "trade outs",
        "trade_in": "trade ins",
        "tradings": "trades",
    }
    grand_total = (
        sales_total + credits_total + quotations_total + trade_out_total + trade_in_total
    )
    from urllib.parse import parse_qs, urlencode

    params = parse_qs(query, keep_blank_values=True) if query else {}
    flat = {key: values[0] for key, values in params.items()}
    flat.pop("download", None)
    flat["download"] = "pdf"
    download_href = f"?{urlencode(flat)}"

    return _apply_pos_visibility(
        {
        "client": client,
        "receipt_count": len(rows),
        "item_count": len(item_rows),
        "receipt_kind": receipt_kind,
        "receipt_kind_options": _client_receipt_kind_options(flags),
        "pos_flags": flags,
        "rows": rows,
        "item_rows": item_rows,
        "ledger_title": kind_titles.get(receipt_kind, "Transactions"),
        "items_title": "Items by transaction",
        "empty_message": (
            f"No {kind_empty.get(receipt_kind, 'transactions')} for this client "
            f"{empty_scope} ({period_label})."
        ),
        "items_empty_message": (
            f"No items for this client {empty_scope} ({period_label})."
        ),
        "scope_hint": scope_hint,
        "other_count": other_count,
        "other_alert": other_alert,
        "all_time_href": all_time_href,
        "download_href": download_href,
        "export_totals": {
            "sales_total": sales_total,
            "credits_total": credits_total,
            "quotations_total": quotations_total,
            "grand_total": grand_total,
            "sales_count": sales_count,
            "credits_count": credits_count,
            "quotations_count": quotations_count,
        },
        "summary_board": {
            "hero": {
                "label": "Transaction value",
                "value": _money_ksh(grand_total),
                "hint": (
                    f"{len(rows)} transaction{'s' if len(rows) != 1 else ''}"
                    f" · {period_label}"
                ),
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Sales",
                    "value": _money_ksh(sales_total),
                    "hint": f"{sales_count} receipt{'s' if sales_count != 1 else ''}",
                    "icon": "shopping-bag",
                    "tone": "cash",
                },
                {
                    "label": "Credits",
                    "value": _money_ksh(credits_total),
                    "hint": f"{credits_count} receipt{'s' if credits_count != 1 else ''}",
                    "icon": "credit-card",
                    "tone": "warn" if credits_count else "neutral",
                },
                {
                    "label": "Quotations",
                    "value": _money_ksh(quotations_total),
                    "hint": (
                        f"{quotations_count} quote"
                        f"{'' if quotations_count == 1 else 's'}"
                    ),
                    "icon": "file-text",
                    "tone": "shops",
                },
                {
                    "label": "Trade out",
                    "value": _money_ksh(trade_out_total),
                    "hint": f"{trade_out_count} receipt{'s' if trade_out_count != 1 else ''}",
                    "icon": "arrow-up-right",
                    "tone": "warn" if trade_out_count else "neutral",
                },
                {
                    "label": "Trade in",
                    "value": _money_ksh(trade_in_total),
                    "hint": f"{trade_in_count} receipt{'s' if trade_in_count != 1 else ''}",
                    "icon": "arrow-down-left",
                    "tone": "cash" if trade_in_count else "neutral",
                },
            ],
        },
        "export_rows": [
            {
                "when": row["when_label"],
                "type": row["kind_label"],
                "receipt": row["number"],
                "shop": row["shop"],
                "status": row["status"],
                "items": row["item_count"],
                "total": row["total"],
                "total_raw": row["total_raw"],
                "paid": row["paid"] if row["paid"] != "—" else "—",
                "due": row["due"] if row["due"] != "—" else "—",
                "cashier": row["cashier"],
            }
            for row in rows
        ],
        **shop_filter,
        **date_filter,
        },
        flags,
    )


def build_client_transactions_pdf(*, account: dict, company_name: str = "") -> bytes:
    """Modern branded A4 landscape PDF for filtered client transactions."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from core.pdf_fonts import MANROPE_PDF, MANROPE_PDF_BOLD, register_manrope_pdf_fonts

    register_manrope_pdf_fonts()

    client = account["client"]
    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    period_label = account.get("report_period_label") or "All time"
    shop_label = account.get("shop_filter_label") or account.get("scope_hint") or "All shops"
    kind = account.get("receipt_kind") or "all"
    kind_label = {
        "all": "All types",
        "sales": "Sales",
        "credits": "Credits",
        "quotations": "Quotations",
        "trade_out": "Trade out",
        "trade_in": "Trade in",
        "tradings": "Trades",
    }.get(kind, "All types")
    rows = list(account.get("export_rows") or [])
    item_rows = list(account.get("item_rows") or [])
    totals = account.get("export_totals") or {}
    printed_at = timezone.localtime()

    ink = colors.HexColor("#0b1220")
    muted = colors.HexColor("#5b6b7c")
    line = colors.HexColor("#e2e8f0")
    line_strong = colors.HexColor("#cbd5e1")
    surface = colors.HexColor("#f8fafc")
    head_fill = colors.HexColor("#0f172a")
    accent = colors.HexColor("#0f766e")
    accent_soft = colors.HexColor("#ccfbf1")
    white = colors.white
    paid_tone = colors.HexColor("#047857")
    unpaid_tone = colors.HexColor("#b45309")
    quote_tone = colors.HexColor("#475569")

    def _esc(text) -> str:
        return (
            str(text if text is not None else "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    buffer = BytesIO()
    page_size = landscape(A4)
    left_m = 10 * mm
    right_m = 10 * mm
    top_m = 12 * mm
    bottom_m = 12 * mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=top_m,
        bottomMargin=bottom_m,
        title=f"{brand} — Client transactions · {client.full_name}",
        author=brand,
    )

    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        "ClientTxBrand",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=8,
        textColor=accent,
        leading=10,
        spaceAfter=1,
    )
    title_style = ParagraphStyle(
        "ClientTxTitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=16,
        textColor=ink,
        leading=19,
        spaceAfter=1,
    )
    subtitle_style = ParagraphStyle(
        "ClientTxSubtitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=9,
        textColor=muted,
        leading=12,
    )
    meta_right = ParagraphStyle(
        "ClientTxMetaRight",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=8,
        textColor=muted,
        leading=10,
        alignment=TA_RIGHT,
    )
    meta_right_strong = ParagraphStyle(
        "ClientTxMetaRightStrong",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=9,
        textColor=ink,
        leading=11,
        alignment=TA_RIGHT,
    )
    kpi_label = ParagraphStyle(
        "ClientTxKpiLabel",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
        alignment=TA_CENTER,
    )
    kpi_value = ParagraphStyle(
        "ClientTxKpiValue",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=11,
        textColor=ink,
        leading=13,
        alignment=TA_CENTER,
    )
    kpi_hint = ParagraphStyle(
        "ClientTxKpiHint",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
        alignment=TA_CENTER,
    )
    head_cell = ParagraphStyle(
        "ClientTxHead",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=white,
        leading=9,
    )
    cell = ParagraphStyle(
        "ClientTxCell",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7.5,
        textColor=ink,
        leading=9.5,
    )
    cell_strong = ParagraphStyle(
        "ClientTxCellStrong",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7.5,
        textColor=ink,
        leading=9.5,
    )
    cell_right = ParagraphStyle(
        "ClientTxCellRight",
        parent=cell,
        alignment=TA_RIGHT,
    )
    cell_center = ParagraphStyle(
        "ClientTxCellCenter",
        parent=cell,
        alignment=TA_CENTER,
    )
    empty_style = ParagraphStyle(
        "ClientTxEmpty",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=9,
        textColor=muted,
        leading=12,
        alignment=TA_CENTER,
    )
    foot_style = ParagraphStyle(
        "ClientTxFoot",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
    )
    section_kicker = ParagraphStyle(
        "ClientTxSectionKicker",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=accent,
        leading=9,
        spaceAfter=1,
    )
    section_title = ParagraphStyle(
        "ClientTxSectionTitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=11,
        textColor=ink,
        leading=13,
        spaceAfter=4,
    )

    story = []
    phone = (client.phone_number or "").strip() or "—"
    header = Table(
        [
            [
                [
                    Paragraph(_esc(brand).upper(), brand_style),
                    Paragraph("Client transactions", title_style),
                    Paragraph(
                        f"{_esc(client.full_name)} · {_esc(phone)}",
                        subtitle_style,
                    ),
                ],
                [
                    Paragraph(_esc(shop_label), meta_right_strong),
                    Paragraph(
                        f"{_esc(period_label)} · {_esc(kind_label)}",
                        meta_right,
                    ),
                    Paragraph(
                        printed_at.strftime("Generated %d %b %Y · %H:%M"),
                        meta_right,
                    ),
                ],
            ]
        ],
        colWidths=[doc.width * 0.62, doc.width * 0.38],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, accent),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 8 * mm))

    sales_n = int(totals.get("sales_count") or 0)
    credits_n = int(totals.get("credits_count") or 0)
    quotes_n = int(totals.get("quotations_count") or 0)
    kpi = Table(
        [
            [
                [
                    Paragraph("SALES", kpi_label),
                    Paragraph(_esc(_money_ksh(totals.get("sales_total"))), kpi_value),
                    Paragraph(
                        f"{sales_n} receipt{'s' if sales_n != 1 else ''}",
                        kpi_hint,
                    ),
                ],
                [
                    Paragraph("CREDITS", kpi_label),
                    Paragraph(_esc(_money_ksh(totals.get("credits_total"))), kpi_value),
                    Paragraph(
                        f"{credits_n} receipt{'s' if credits_n != 1 else ''}",
                        kpi_hint,
                    ),
                ],
                [
                    Paragraph("QUOTATIONS", kpi_label),
                    Paragraph(
                        _esc(_money_ksh(totals.get("quotations_total"))), kpi_value
                    ),
                    Paragraph(
                        f"{quotes_n} quote{'' if quotes_n == 1 else 's'}",
                        kpi_hint,
                    ),
                ],
                [
                    Paragraph("TOTAL VALUE", kpi_label),
                    Paragraph(_esc(_money_ksh(totals.get("grand_total"))), kpi_value),
                    Paragraph(
                        f"{len(rows)} transaction{'s' if len(rows) != 1 else ''}",
                        kpi_hint,
                    ),
                ],
            ]
        ],
        colWidths=[doc.width / 4.0] * 4,
    )
    kpi.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), surface),
                ("BOX", (0, 0), (-1, -1), 0.8, line_strong),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, line),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(kpi)
    story.append(Spacer(1, 6 * mm))

    def _style_data_table(table_data):
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), head_fill),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 1), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, 0), 0, head_fill),
            ("BOX", (0, 0), (-1, -1), 0.7, line_strong),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, line),
            ("BACKGROUND", (0, 1), (-1, -1), white),
        ]
        for row_idx in range(1, len(table_data)):
            if row_idx % 2 == 0:
                style_cmds.append(
                    ("BACKGROUND", (0, row_idx), (-1, row_idx), surface)
                )
        return TableStyle(style_cmds)

    usable = doc.width
    story.append(Paragraph("TRANSACTIONS", section_kicker))
    story.append(
        Paragraph(
            account.get("ledger_title") or "All transactions",
            section_title,
        )
    )
    col_widths = [
        usable * 0.12,  # When
        usable * 0.08,  # Type
        usable * 0.12,  # Receipt
        usable * 0.14,  # Shop
        usable * 0.08,  # Status
        usable * 0.05,  # Items
        usable * 0.11,  # Total
        usable * 0.10,  # Paid
        usable * 0.10,  # Due
        usable * 0.10,  # Cashier
    ]
    table_data = [
        [
            Paragraph("When", head_cell),
            Paragraph("Type", head_cell),
            Paragraph("Receipt", head_cell),
            Paragraph("Shop", head_cell),
            Paragraph("Status", head_cell),
            Paragraph("Items", head_cell),
            Paragraph("Total", head_cell),
            Paragraph("Paid", head_cell),
            Paragraph("Due", head_cell),
            Paragraph("Cashier", head_cell),
        ]
    ]

    for idx, row in enumerate(rows):
        status = (row.get("status") or "").strip()
        status_style = ParagraphStyle(
            f"ClientTxStatus{idx}",
            parent=cell_center,
            fontName=MANROPE_PDF_BOLD,
            textColor=(
                paid_tone
                if status.lower() == "paid"
                else unpaid_tone
                if status.lower() == "unpaid"
                else quote_tone
            ),
        )
        table_data.append(
            [
                Paragraph(_esc(row.get("when") or "—"), cell),
                Paragraph(_esc(row.get("type") or "—"), cell_strong),
                Paragraph(_esc(row.get("receipt") or "—"), cell_strong),
                Paragraph(_esc(row.get("shop") or "—"), cell),
                Paragraph(_esc(status or "—"), status_style),
                Paragraph(_esc(row.get("items") or 0), cell_center),
                Paragraph(_esc(row.get("total") or "—"), cell_right),
                Paragraph(_esc(row.get("paid") or "—"), cell_right),
                Paragraph(_esc(row.get("due") or "—"), cell_right),
                Paragraph(_esc(row.get("cashier") or "—"), cell),
            ]
        )

    if len(table_data) == 1:
        story.append(
            Paragraph(
                _esc(account.get("empty_message") or "No transactions found."),
                empty_style,
            )
        )
    else:
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(_style_data_table(table_data))
        story.append(table)

    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("ITEM BREAKDOWN", section_kicker))
    story.append(
        Paragraph(
            account.get("items_title") or "Items by transaction",
            section_title,
        )
    )

    item_col_widths = [
        usable * 0.11,  # Date
        usable * 0.08,  # Type
        usable * 0.26,  # Item
        usable * 0.12,  # Receipt
        usable * 0.14,  # Shop
        usable * 0.06,  # Qty
        usable * 0.11,  # Unit
        usable * 0.12,  # Total
    ]
    item_table_data = [
        [
            Paragraph("Date", head_cell),
            Paragraph("Type", head_cell),
            Paragraph("Item", head_cell),
            Paragraph("Receipt", head_cell),
            Paragraph("Shop", head_cell),
            Paragraph("Qty", head_cell),
            Paragraph("Unit", head_cell),
            Paragraph("Total", head_cell),
        ]
    ]
    for row in item_rows:
        item_table_data.append(
            [
                Paragraph(_esc(row.get("date") or "—"), cell),
                Paragraph(_esc(row.get("type") or "—"), cell_strong),
                Paragraph(_esc(row.get("item") or "—"), cell_strong),
                Paragraph(_esc(row.get("receipt") or "—"), cell),
                Paragraph(_esc(row.get("shop") or "—"), cell),
                Paragraph(_esc(row.get("qty") or 0), cell_center),
                Paragraph(_esc(row.get("unit") or "—"), cell_right),
                Paragraph(_esc(row.get("total") or "—"), cell_right),
            ]
        )

    if len(item_table_data) == 1:
        story.append(
            Paragraph(
                _esc(account.get("items_empty_message") or "No items found."),
                empty_style,
            )
        )
    else:
        item_table = Table(item_table_data, colWidths=item_col_widths, repeatRows=1)
        item_table.setStyle(_style_data_table(item_table_data))
        story.append(item_table)

    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            (
                f"{brand} · Confidential client transaction report · "
                f"{len(rows)} transaction{'s' if len(rows) != 1 else ''} · "
                f"{len(item_rows)} item line{'s' if len(item_rows) != 1 else ''} · "
                f"{period_label} · {kind_label}"
            ),
            foot_style,
        )
    )

    class _NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdf_canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            page_count = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_page_number(page_count)
                pdf_canvas.Canvas.showPage(self)
            pdf_canvas.Canvas.save(self)

        def _draw_page_number(self, page_count):
            self.setFont(MANROPE_PDF, 7)
            self.setFillColor(muted)
            label = f"Page {self._pageNumber} of {page_count}"
            self.drawRightString(page_size[0] - right_m, 6 * mm, label)
            self.setStrokeColor(accent_soft)
            self.setLineWidth(2)
            self.line(left_m, 8.5 * mm, page_size[0] - right_m - 28 * mm, 8.5 * mm)

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()


def client_transactions_pdf_response(*, account: dict):
    """PDF download for filtered client transactions."""
    from django.http import HttpResponse
    from django.utils.text import slugify

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_client_transactions_pdf(
        account=account, company_name=company_name
    )
    client = account["client"]
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    name = slugify(client.full_name or "client") or "client"
    kind = account.get("receipt_kind") or "all"
    filename = f"client-{name}-transactions-{kind}-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def _pdf_export_href(query: str, *, mode: str = "pdf") -> str:
    """Current filter query with a PDF export flag (download or print)."""
    from urllib.parse import parse_qs, urlencode

    action = (mode or "pdf").strip().lower()
    if action not in {"pdf", "print", "download", "1"}:
        action = "pdf"
    if action in {"download", "1"}:
        action = "pdf"
    params = parse_qs(query or "", keep_blank_values=True)
    flat = {key: values[0] for key, values in params.items()}
    flat.pop("download", None)
    flat["download"] = action
    return f"?{urlencode(flat)}"


def _pdf_download_href(query: str) -> str:
    """Current filter query with the download flag swapped on."""
    return _pdf_export_href(query, mode="pdf")


def _pdf_print_href(query: str) -> str:
    """Current filter query with the print (inline PDF) flag swapped on."""
    return _pdf_export_href(query, mode="print")


def _flatten_analytics_table(table: dict) -> tuple[list, list, list]:
    """Flatten an analytics table dict into PDF-ready headers/aligns/rows."""
    columns = list(table.get("columns") or [])
    headers: list[str] = []
    aligns: list[str] = []
    for col in columns:
        if isinstance(col, dict):
            if col.get("pair"):
                base = str(col.get("label") or "").strip()
                qty = str(col.get("pair_qty") or "Qty")
                amt = str(col.get("pair_amt") or "Amt")
                headers += [f"{base} {qty}".strip(), f"{base} {amt}".strip()]
                aligns += ["right", "right"]
            else:
                headers.append(str(col.get("label") or ""))
                aligns.append("left" if col.get("phone") else "right")
        else:
            headers.append(str(col))
            aligns.append("left")

    rows: list[list[str]] = []
    for row in table.get("rows") or []:
        cells = row.get("cells") if isinstance(row, dict) else row
        flat: list[str] = []
        for col, cell in zip(columns, list(cells or [])):
            if isinstance(col, dict) and col.get("pair"):
                if isinstance(cell, dict) and cell.get("kind") == "qty_amount":
                    flat += [
                        str(cell.get("qty") or "0"),
                        str(cell.get("amount") or "0"),
                    ]
                else:
                    flat += ["—", "—"]
            elif isinstance(cell, dict):
                flat.append(str(cell.get("label") or "—"))
            else:
                flat.append(str(cell))
        rows.append(flat)
    return headers, aligns, rows


def _analytics_report_pdf(
    *,
    title: str,
    subtitle: str,
    brand: str,
    meta_lines: list,
    kpi_cards: list,
    sections: list,
    footer_note: str,
) -> bytes:
    """Shared branded landscape A4 layout for analytics PDF downloads."""
    from io import BytesIO

    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from core.pdf_fonts import MANROPE_PDF, MANROPE_PDF_BOLD, register_manrope_pdf_fonts

    register_manrope_pdf_fonts()

    brand = (brand or "MY-SHOP").strip() or "MY-SHOP"
    printed_at = timezone.localtime()

    ink = colors.HexColor("#0b1220")
    ink_band = colors.HexColor("#0f172a")
    ink_band_soft = colors.HexColor("#1e293b")
    muted = colors.HexColor("#5b6b7c")
    line = colors.HexColor("#e2e8f0")
    line_strong = colors.HexColor("#cbd5e1")
    surface = colors.HexColor("#f8fafc")
    accent = colors.HexColor("#0f766e")
    accent_soft = colors.HexColor("#ccfbf1")
    accent_softest = colors.HexColor("#f0fdfa")
    accent_ice = colors.HexColor("#5eead4")
    slate_300 = colors.HexColor("#cbd5e1")
    slate_400 = colors.HexColor("#94a3b8")
    slate_700 = colors.HexColor("#334155")
    white = colors.white

    def _esc(text) -> str:
        return (
            str(text if text is not None else "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    buffer = BytesIO()
    page_size = landscape(A4)
    left_m = 10 * mm
    right_m = 10 * mm
    top_m = 12 * mm
    bottom_m = 12 * mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=left_m,
        rightMargin=right_m,
        topMargin=top_m,
        bottomMargin=bottom_m,
        title=f"{brand} — {title}",
        author=brand,
    )

    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        "PdfBrand",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=8,
        textColor=accent_ice,
        leading=10,
        spaceAfter=2,
    )
    title_style = ParagraphStyle(
        "PdfTitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=17,
        textColor=white,
        leading=20,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "PdfSubtitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=9,
        textColor=slate_300,
        leading=12,
    )
    meta_right = ParagraphStyle(
        "PdfMetaRight",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=8,
        textColor=slate_400,
        leading=10,
        alignment=TA_RIGHT,
    )
    meta_right_strong = ParagraphStyle(
        "PdfMetaRightStrong",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=9,
        textColor=colors.HexColor("#f1f5f9"),
        leading=11,
        alignment=TA_RIGHT,
    )
    kpi_label = ParagraphStyle(
        "PdfKpiLabel",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
    )
    kpi_value = ParagraphStyle(
        "PdfKpiValue",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=12,
        textColor=ink,
        leading=14,
    )
    kpi_hint = ParagraphStyle(
        "PdfKpiHint",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
    )
    head_cell = ParagraphStyle(
        "PdfHead",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=slate_300,
        leading=8.8,
    )
    head_cell_right = ParagraphStyle(
        "PdfHeadRight",
        parent=head_cell,
        alignment=TA_RIGHT,
    )
    head_cell_center = ParagraphStyle(
        "PdfHeadCenter",
        parent=head_cell,
        alignment=TA_CENTER,
    )
    group_head = ParagraphStyle(
        "PdfGroupHead",
        parent=head_cell,
        fontSize=8,
        textColor=white,
        alignment=TA_CENTER,
    )
    cell = ParagraphStyle(
        "PdfCell",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7.6,
        textColor=ink,
        leading=9.4,
    )
    cell_strong = ParagraphStyle(
        "PdfCellStrong",
        parent=cell,
        fontName=MANROPE_PDF_BOLD,
    )
    cell_right = ParagraphStyle(
        "PdfCellRight",
        parent=cell,
        alignment=TA_RIGHT,
    )
    cell_right_strong = ParagraphStyle(
        "PdfCellRightStrong",
        parent=cell_right,
        fontName=MANROPE_PDF_BOLD,
    )
    cell_center = ParagraphStyle(
        "PdfCellCenter",
        parent=cell,
        alignment=TA_CENTER,
    )
    cell_center_strong = ParagraphStyle(
        "PdfCellCenterStrong",
        parent=cell_center,
        fontName=MANROPE_PDF_BOLD,
    )
    group_cell = ParagraphStyle(
        "PdfGroupCell",
        parent=cell,
        fontName=MANROPE_PDF_BOLD,
        fontSize=7.6,
        textColor=accent,
    )
    empty_style = ParagraphStyle(
        "PdfEmpty",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=9,
        textColor=muted,
        leading=12,
        alignment=TA_CENTER,
    )
    foot_style = ParagraphStyle(
        "PdfFoot",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
    )
    section_kicker = ParagraphStyle(
        "PdfSectionKicker",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=accent,
        leading=9,
        spaceAfter=1,
    )
    section_title = ParagraphStyle(
        "PdfSectionTitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=11,
        textColor=ink,
        leading=13,
        spaceAfter=4,
    )

    story = []
    right_block = []
    for idx, text in enumerate(meta_lines or []):
        right_block.append(
            Paragraph(
                _esc(text),
                meta_right_strong if idx == 0 else meta_right,
            )
        )
    right_block.append(
        Paragraph(
            printed_at.strftime("Generated %d %b %Y · %H:%M"),
            meta_right,
        )
    )
    header = Table(
        [
            [
                [
                    Paragraph(_esc(brand).upper(), brand_style),
                    Paragraph(_esc(title), title_style),
                    Paragraph(_esc(subtitle), subtitle_style),
                ],
                right_block,
            ]
        ],
        colWidths=[doc.width * 0.62, doc.width * 0.38],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), ink_band),
                ("LEFTPADDING", (0, 0), (0, -1), 14),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 14),
                ("LEFTPADDING", (-1, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 11),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, -1), 2.2, accent),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 7 * mm))

    cards = [card for card in (kpi_cards or [])[:6] if card]
    if cards:
        gap = 2.6 * mm
        card_w = (doc.width - gap * (len(cards) - 1)) / max(len(cards), 1)
        kpi_row = []
        kpi_widths = []
        for cidx, (label, value, hint) in enumerate(cards):
            if cidx:
                kpi_row.append("")
                kpi_widths.append(gap)
            card_rows = [
                [Paragraph(_esc(str(label or "")).upper(), kpi_label)],
                [Paragraph(_esc(value if value is not None else "—"), kpi_value)],
            ]
            if hint:
                card_rows.append([Paragraph(_esc(hint), kpi_hint)])
            card = Table(card_rows, colWidths=[card_w])
            card.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), surface),
                        ("BOX", (0, 0), (-1, -1), 0.6, line_strong),
                        ("LINEBEFORE", (0, 0), (0, -1), 2.4, accent),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, 0), 7),
                        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            kpi_row.append(card)
            kpi_widths.append(card_w)
        kpi = Table([kpi_row], colWidths=kpi_widths)
        kpi.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(kpi)
        story.append(Spacer(1, 7 * mm))

    def _share_bar(pct):
        """Mini teal share bar for '% of sales' cells."""
        try:
            fill = max(0.0, min(100.0, float(pct or 0)))
        except (TypeError, ValueError):
            return None
        width, height = 8.5 * mm, 1.5 * mm
        drawing = Drawing(width, height)
        drawing.hAlign = "RIGHT"
        drawing.add(
            Rect(
                0,
                0,
                width,
                height,
                rx=height / 2,
                ry=height / 2,
                fillColor=line,
                strokeColor=None,
            )
        )
        if fill > 0:
            drawing.add(
                Rect(
                    0,
                    0,
                    max(width * fill / 100.0, height),
                    height,
                    rx=height / 2,
                    ry=height / 2,
                    fillColor=accent,
                    strokeColor=None,
                )
            )
        return drawing

    def _style_data_table(n_rows: int, has_total: bool, head_rows: int = 1) -> TableStyle:
        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), ink_band_soft),
            ("BACKGROUND", (0, head_rows - 1), (-1, head_rows - 1), ink_band),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, head_rows - 1), 5.5),
            ("BOTTOMPADDING", (0, 0), (-1, head_rows - 1), 5.5),
            ("TOPPADDING", (0, head_rows), (-1, -1), 5),
            ("BOTTOMPADDING", (0, head_rows), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("LINEBELOW", (0, head_rows - 1), (-1, head_rows - 1), 1.1, accent),
            ("LINEBELOW", (0, head_rows), (-1, -1), 0.4, line),
            ("BACKGROUND", (0, head_rows), (-1, -1), white),
        ]
        if head_rows > 1:
            cmds.append(("LINEBELOW", (0, 0), (-1, 0), 0.5, slate_700))
        if has_total:
            cmds.append(("LINEABOVE", (0, -1), (-1, -1), 1.0, accent))
            cmds.append(("BACKGROUND", (0, -1), (-1, -1), accent_soft))
        return TableStyle(cmds)

    usable = doc.width
    for section in sections or []:
        story.append(
            Paragraph(_esc(section.get("kicker") or "TABLE"), section_kicker)
        )
        story.append(
            Paragraph(_esc(section.get("title") or ""), section_title)
        )
        story.append(
            HRFlowable(
                width=16 * mm,
                thickness=1.2,
                color=accent,
                hAlign="LEFT",
                spaceBefore=0,
                spaceAfter=5,
            )
        )
        headers = list(section.get("headers") or [])
        aligns = list(section.get("aligns") or []) or ["left"] * len(headers)
        header_groups = list(section.get("header_groups") or [])
        rows = list(section.get("rows") or [])
        if not headers or not rows:
            story.append(
                Paragraph(
                    _esc(section.get("empty") or "No data for this period."),
                    empty_style,
                )
            )
        else:
            n_cols = len(headers)

            def _cell_style(align, strong=False):
                if strong:
                    if align == "right":
                        return cell_right_strong
                    if align == "center":
                        return cell_center_strong
                    return cell_strong
                if align == "right":
                    return cell_right
                if align == "center":
                    return cell_center
                return cell

            head_row = [
                Paragraph(
                    _esc(str(text).upper()),
                    (
                        head_cell_right
                        if aligns[idx] == "right"
                        else head_cell_center
                        if aligns[idx] == "center"
                        else head_cell
                    ),
                )
                for idx, text in enumerate(headers)
            ]
            header_rows = [head_row]
            span_cmds = []
            if header_groups:
                top_row = [""] * n_cols
                col = 0
                for gidx, group in enumerate(header_groups):
                    label, span = group[0], group[1]
                    rowspan = len(group) > 2 and bool(group[2])
                    span = int(span or 1)
                    top_row[col] = Paragraph(
                        _esc(str(label or "").upper()), group_head
                    )
                    if gidx:
                        span_cmds.append(
                            (
                                "LINEAFTER",
                                (col - 1, 0),
                                (col - 1, -1),
                                0.8,
                                line_strong,
                            )
                        )
                    if span > 1:
                        span_cmds.append(
                            ("SPAN", (col, 0), (col + span - 1, 0))
                        )
                    elif rowspan or gidx == 0:
                        span_cmds.append(("SPAN", (col, 0), (col, 1)))
                    col += span
                header_rows = [top_row, head_row]

            # Backwards-compat: plain rows whose first cell reads "total".
            legacy_total = (
                rows
                and not isinstance(rows[-1], dict)
                and str((rows[-1] or [""])[0] or "").strip().lower() == "total"
            )
            body = []
            row_kinds = []
            for row in rows:
                kind = "data"
                cells = row
                if isinstance(row, dict):
                    kind = row.get("kind") or "data"
                    cells = row.get("cells") or []
                if kind == "group" or (
                    isinstance(row, dict) and row.get("group_header")
                ):
                    label = row.get("label") if isinstance(row, dict) else ""
                    body.append(
                        [Paragraph(_esc(label or ""), group_cell)]
                        + [""] * (n_cols - 1)
                    )
                    row_kinds.append("group")
                    continue
                is_total = kind == "total" or (
                    legacy_total and row is rows[-1]
                )
                row_kinds.append("total" if is_total else "data")
                row_cells = []
                for idx, value in enumerate(cells):
                    if isinstance(value, dict) and "bar" in value:
                        block = [
                            Paragraph(
                                _esc(value.get("label") or "—"),
                                _cell_style(aligns[idx], is_total),
                            )
                        ]
                        bar = _share_bar(value.get("bar"))
                        if bar is not None:
                            block.append(Spacer(1, 0.5 * mm))
                            block.append(bar)
                        row_cells.append(block)
                        continue
                    row_cells.append(
                        Paragraph(
                            _esc(value),
                            _cell_style(aligns[idx], is_total),
                        )
                    )
                body.append(row_cells)
            fractions = section.get("widths")
            if fractions and len(fractions) == n_cols:
                col_widths = [usable * frac for frac in fractions]
            else:
                weights = [
                    1.7 if aligns[idx] == "left" and idx == 0 else (
                        1.15 if aligns[idx] == "left" else 1.0
                    )
                    for idx in range(n_cols)
                ]
                weight_total = sum(weights) or 1.0
                col_widths = [usable * w / weight_total for w in weights]
            grid = Table(
                header_rows + body,
                colWidths=col_widths,
                repeatRows=len(header_rows),
            )
            style = _style_data_table(
                len(header_rows) + len(body),
                "total" in row_kinds and row_kinds[-1] == "total",
                head_rows=len(header_rows),
            )
            for cmd in span_cmds:
                style.add(*cmd)
            zebra = 0
            for offset, kind in enumerate(row_kinds):
                r = offset + len(header_rows)
                if kind == "group":
                    zebra = 0
                    style.add("SPAN", (0, r), (-1, r))
                    style.add("BACKGROUND", (0, r), (-1, r), accent_softest)
                    style.add("LINEBEFORE", (0, r), (0, r), 2.2, accent)
                    style.add("LINEABOVE", (0, r), (-1, r), 0.6, accent)
                elif kind == "total":
                    style.add("LINEABOVE", (0, r), (-1, r), 1.0, accent)
                    style.add("BACKGROUND", (0, r), (-1, r), accent_soft)
                elif zebra % 2 == 1:
                    style.add("BACKGROUND", (0, r), (-1, r), surface)
                    zebra += 1
                else:
                    zebra += 1
            grid.setStyle(style)
            story.append(grid)
            footnote = (section.get("footnote") or "").strip()
            if footnote:
                story.append(Spacer(1, 1.5 * mm))
                story.append(Paragraph(_esc(footnote), foot_style))
        story.append(Spacer(1, 6 * mm))

    story.append(
        Paragraph(
            _esc(
                footer_note
                or f"{brand} · {title} · "
                f"{printed_at.strftime('%d %b %Y · %H:%M')}"
            ),
            foot_style,
        )
    )

    class _NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdf_canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            page_count = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_page_number(page_count)
                pdf_canvas.Canvas.showPage(self)
            pdf_canvas.Canvas.save(self)

        def _draw_page_number(self, page_count):
            # Brand ribbon across the top of every page.
            self.setFillColor(ink_band)
            self.rect(
                0,
                page_size[1] - 2.4 * mm,
                page_size[0],
                2.4 * mm,
                fill=1,
                stroke=0,
            )
            self.setFillColor(accent)
            self.rect(
                0, page_size[1] - 2.4 * mm, 34 * mm, 2.4 * mm, fill=1, stroke=0
            )
            # Footer: hairline, brand left, page numbers right.
            self.setStrokeColor(line)
            self.setLineWidth(0.8)
            self.line(left_m, 8.5 * mm, page_size[0] - right_m, 8.5 * mm)
            self.setFont(MANROPE_PDF, 7)
            self.setFillColor(muted)
            self.drawString(left_m, 5.6 * mm, f"{brand} · {title}")
            self.drawRightString(
                page_size[0] - right_m,
                5.6 * mm,
                f"Page {self._pageNumber} of {page_count}",
            )

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()


def _pdf_cell_label(cell) -> str:
    if isinstance(cell, dict):
        if cell.get("kind") == "qty_amount":
            return str(cell.get("amount") or "0")
        return str(cell.get("label") or "—")
    return str(cell)


def _credits_shop_pdf_section(table: dict) -> dict:
    """Simplified shop rollup: total, due, paid, % paid, profit/loss, stock."""
    rows = []
    for row in table.get("rows") or []:
        cells = row.get("cells") if isinstance(row, dict) else row
        cells = list(cells or [])
        total_cell = cells[3] if len(cells) > 3 else {}
        amount = (
            str(total_cell.get("amount") or "0")
            if isinstance(total_cell, dict)
            else "0"
        )
        rows.append(
            [
                _pdf_cell_label(cells[0]) if cells else "—",
                amount,
                _pdf_cell_label(cells[2]) if len(cells) > 2 else "—",
                _pdf_cell_label(cells[1]) if len(cells) > 1 else "—",
                _pdf_cell_label(cells[4]) if len(cells) > 4 else "—",
                _pdf_cell_label(cells[6]) if len(cells) > 6 else "—",
                _pdf_cell_label(cells[5]) if len(cells) > 5 else "—",
            ]
        )
    return {
        "kicker": "CREDITS REPORT",
        "title": "Credits by shop",
        "headers": [
            "Shop",
            "Total amount",
            "Total due",
            "Total paid",
            "% paid",
            "Profit/Loss",
            "Stock value",
        ],
        "aligns": ["left", "right", "right", "right", "right", "right", "right"],
        "widths": [0.22, 0.15, 0.13, 0.13, 0.10, 0.14, 0.13],
        "rows": rows,
        "empty": table.get("empty") or "No data.",
        "footnote": table.get("footnote") or "",
    }


def _credits_items_pdf_section(table: dict) -> dict:
    """Credit items matrix — quad cells per shop (qty · credit · paid · %)."""
    section = _item_report_pdf_section(table)
    section["kicker"] = "CREDITS REPORT"
    section["title"] = table.get("title") or "Items picked on credit"
    return section


def _summary_board_kpi_cards(summary: dict) -> list:
    cards = []
    hero = (summary or {}).get("hero") or {}
    if hero:
        cards.append(
            (
                hero.get("label") or "Total",
                hero.get("value") or "—",
                hero.get("hint") or "",
            )
        )
    for tile in (summary or {}).get("tiles") or []:
        cards.append(
            (
                tile.get("label") or "",
                tile.get("value") or "—",
                tile.get("hint") or "",
            )
        )
    return cards


def build_credits_report_pdf(*, page: dict, filters: dict, company_name: str = "") -> bytes:
    """Landscape A4 PDF of the analytics credits report (shops + clients)."""
    period_label = filters.get("report_period_label") or "All time"
    selected = set(filters.get("selected_shop_ids") or [])
    filter_shops = list(filters.get("filter_shops") or [])
    if selected:
        names = [str(shop.name) for shop in filter_shops if shop.pk in selected]
        shop_label = ", ".join(names) if names else "Selected shops"
    else:
        shop_label = "All shops"

    sections = []
    for table in page.get("tables") or []:
        if table.get("title") == "Credits by shop":
            sections.append(_credits_shop_pdf_section(table))
            continue
        if table.get("title") == "Items picked on credit":
            sections.append(_credits_items_pdf_section(table))
            continue
        headers, aligns, rows = _flatten_analytics_table(table)
        sections.append(
            {
                "kicker": "CREDITS REPORT",
                "title": table.get("title") or "Table",
                "headers": headers,
                "aligns": aligns,
                "rows": rows,
                "empty": table.get("empty") or "No data.",
                "footnote": table.get("footnote") or "",
            }
        )

    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    return _analytics_report_pdf(
        title="Credits report",
        subtitle=(
            page.get("lead") or "Items picked on credit by shop."
        ),
        brand=brand,
        meta_lines=[shop_label, period_label],
        kpi_cards=_summary_board_kpi_cards(page.get("summary_board") or {}),
        sections=sections,
        footer_note=(
            f"{brand} · Credits report · {shop_label} · {period_label}"
        ),
    )


def credits_report_pdf_response(*, page: dict, filters: dict):
    """PDF download for the analytics credits report."""
    from django.http import HttpResponse

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_credits_report_pdf(
        page=page, filters=filters, company_name=company_name
    )
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    filename = f"credits-report-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def build_credit_audit_pdf(*, trail: dict, company_name: str = "") -> bytes:
    """Landscape A4 PDF of the credit activity audit trail."""
    period_label = trail.get("period_label") or "All time"
    selected_shop = int(trail.get("selected_shop_id") or 0)
    shop_label = "All shops"
    for shop in trail.get("filter_shops") or []:
        if shop.pk == selected_shop:
            shop_label = str(shop.name)
            break
    selected_emp = int(trail.get("selected_employee_id") or 0)
    employee_label = "All employees"
    for emp in trail.get("filter_employees") or []:
        if emp.pk == selected_emp:
            employee_label = (
                emp.user.get_full_name() or emp.employee_id or "Employee"
            )
            break
    event_label = trail.get("event_filter_label") or "All events"

    headers = [
        "When",
        "Client",
        "Event",
        "Receipt",
        "Shop",
        "Amount",
        "Detail",
        "By",
    ]
    aligns = ["left", "left", "left", "left", "left", "right", "left", "left"]
    widths = [0.11, 0.13, 0.12, 0.10, 0.11, 0.09, 0.24, 0.10]
    rows = [
        [
            row.get("when_label") or "—",
            row.get("client") or "—",
            row.get("kind_label") or "—",
            row.get("receipt_number") or "—",
            row.get("shop") or "—",
            row.get("amount") or "—",
            row.get("detail") or "—",
            row.get("actor") or "—",
        ]
        for row in trail.get("rows") or []
    ]

    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    return _analytics_report_pdf(
        title="Credit activity audit trail",
        subtitle=(
            trail.get("lead")
            or "Payments and changes on credit receipts."
        ),
        brand=brand,
        meta_lines=[
            shop_label,
            f"{period_label} · {event_label} · {employee_label}",
        ],
        kpi_cards=_summary_board_kpi_cards(trail.get("summary_board") or {}),
        sections=[
            {
                "kicker": "AUDIT TRAIL",
                "title": "Credit activity",
                "headers": headers,
                "aligns": aligns,
                "rows": rows,
                "widths": widths,
                "empty": (
                    trail.get("empty_message")
                    or "No credit account changes recorded yet."
                ),
            }
        ],
        footer_note=(
            f"{brand} · Credit activity audit · {shop_label} · "
            f"{period_label} · {event_label}"
        ),
    )


def credit_audit_pdf_response(*, trail: dict):
    """PDF download for the credit activity audit trail."""
    from django.http import HttpResponse

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_credit_audit_pdf(trail=trail, company_name=company_name)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    filename = f"credit-activity-audit-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def _tradings_accounts_pdf_section(table: dict) -> dict:
    """Simplified client trade rollup: trade in, trade out, balance."""
    rows = []
    for row in table.get("rows") or []:
        cells = row.get("cells") if isinstance(row, dict) else row
        cells = list(cells or [])
        flat = [
            _pdf_cell_label(cells[0]) if cells else "—",
            _pdf_cell_label(cells[1]) if len(cells) > 1 else "—",
        ]
        for idx in (2, 3, 4):
            flat.append(
                _pdf_cell_label(cells[idx]) if idx < len(cells) else "—"
            )
        rows.append(flat)
    return {
        "kicker": "TRADINGS REPORT",
        "title": table.get("title") or "Client accounts",
        "headers": ["Client", "Phone", "Trade in", "Trade out", "Balance"],
        "aligns": ["left", "left", "right", "right", "right"],
        "widths": [0.28, 0.16, 0.19, 0.19, 0.18],
        "rows": rows,
        "empty": table.get("empty") or "No data.",
    }


def _client_trades_pdf_section(table: dict) -> dict:
    """Simplified per-client trade list: receipt, type, items, money columns."""
    rows = []
    for row in table.get("rows") or []:
        cells = row.get("cells") if isinstance(row, dict) else row
        cells = list(cells or [])

        def cell_label(idx):
            return _pdf_cell_label(cells[idx]) if idx < len(cells) else "—"

        items = ""
        if len(cells) > 4:
            items_cell = cells[4]
            if isinstance(items_cell, dict):
                items = (items_cell.get("title") or "").strip()
            if not items or items == "—":
                items = _pdf_cell_label(items_cell)
        rows.append(
            [
                cell_label(0),
                cell_label(1),
                cell_label(3),
                items or "—",
                cell_label(5),
                cell_label(6),
                cell_label(7),
                cell_label(8),
            ]
        )
    return {
        "kicker": "TRADINGS REPORT",
        "title": table.get("title") or "Trades",
        "headers": [
            "Trade",
            "Type",
            "Shop",
            "Items",
            "Value",
            "Cleared",
            "Balance",
            "Status",
        ],
        "aligns": [
            "left",
            "left",
            "left",
            "left",
            "right",
            "right",
            "right",
            "left",
        ],
        "widths": [0.13, 0.09, 0.13, 0.27, 0.11, 0.11, 0.09, 0.07],
        "rows": rows,
        "empty": table.get("empty") or "No data.",
    }


def build_tradings_report_pdf(*, page: dict, filters: dict, company_name: str = "") -> bytes:
    """Landscape A4 PDF of the analytics tradings report."""
    period_label = filters.get("report_period_label") or "All time"
    selected = set(filters.get("selected_shop_ids") or [])
    filter_shops = list(filters.get("filter_shops") or [])
    if selected:
        names = [str(shop.name) for shop in filter_shops if shop.pk in selected]
        shop_label = ", ".join(names) if names else "Selected shops"
    else:
        shop_label = "All shops"

    headline = (page.get("headline") or "").strip()
    client_report = bool(headline) and headline != "Tradings"

    sections = []
    for table in page.get("tables") or []:
        title = table.get("title")
        if title == "Client accounts":
            sections.append(_tradings_accounts_pdf_section(table))
        elif title == "Trades":
            sections.append(_client_trades_pdf_section(table))
        else:
            headers, aligns, rows = _flatten_analytics_table(table)
            sections.append(
                {
                    "kicker": "TRADINGS REPORT",
                    "title": title or "Table",
                    "headers": headers,
                    "aligns": aligns,
                    "rows": rows,
                    "empty": table.get("empty") or "No data.",
                    "footnote": table.get("footnote") or "",
                }
            )

    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    return _analytics_report_pdf(
        title=(
            f"{headline} — tradings" if client_report else "Tradings report"
        ),
        subtitle=(
            page.get("lead")
            or "Client trade accounts — trade ins and trade outs."
        ),
        brand=brand,
        meta_lines=[shop_label, period_label],
        kpi_cards=[
            (
                metric.get("label") or "",
                metric.get("value") or "—",
                metric.get("hint") or "",
            )
            for metric in page.get("metrics") or []
        ],
        sections=sections,
        footer_note=(
            f"{brand} · Tradings report · {shop_label} · {period_label}"
        ),
    )


def tradings_report_pdf_response(*, page: dict, filters: dict):
    """PDF download for the analytics tradings report."""
    from django.http import HttpResponse
    from django.utils.text import slugify

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_tradings_report_pdf(
        page=page, filters=filters, company_name=company_name
    )
    headline = (page.get("headline") or "").strip()
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    if headline and headline != "Tradings":
        filename = f"tradings-{slugify(headline) or 'report'}-{stamp}.pdf"
    else:
        filename = f"tradings-report-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def _short_pdf_header(label, default: str) -> str:
    """Compact PDF sub-header — quad columns are too narrow for long labels."""
    return (
        (label or default)
        .replace("/day", "")
        .replace("Top day", "Day")
    )


def _item_report_pdf_section(table: dict) -> dict:
    """Item report section mirroring the on-page shop-column matrix."""
    columns = list(table.get("columns") or [])

    header_groups = []
    headers = []
    aligns = []
    specs = []  # one entry per on-page column: "text" | "share" | "quad"
    for idx, col in enumerate(columns):
        if isinstance(col, dict) and col.get("quad"):
            header_groups.append((col.get("label") or "Shop", 4))
            headers += [
                _short_pdf_header(col.get("sub_qty"), "Qty"),
                _short_pdf_header(col.get("sub_amt"), "Amt"),
                _short_pdf_header(col.get("sub_pct"), "%"),
                _short_pdf_header(col.get("sub_day"), "Day"),
            ]
            aligns += ["right", "right", "right", "center"]
            specs.append("quad")
            continue
        label = (
            (col.get("label") or "")
            if isinstance(col, dict)
            else str(col or "")
        )
        is_share = isinstance(col, dict) and col.get("share")
        # Single columns span both header rows; the rowspan flag (3rd tuple
        # item) tells the renderer to merge vertically.
        header_groups.append((label, 1, True))
        headers.append("")
        aligns.append("right" if is_share else "left")
        specs.append("share" if is_share else "text")

    rows = []
    for row in table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if row.get("group_header"):
            rows.append({"kind": "group", "label": row.get("label") or ""})
            continue
        cells = list(row.get("cells") or [])
        flat = []
        for idx, spec in enumerate(specs):
            cell = cells[idx] if idx < len(cells) else None
            if spec == "quad":
                if isinstance(cell, dict) and cell.get("kind") == "quad":
                    flat += [
                        str(cell.get("qty") or "—"),
                        str(cell.get("amount") or "—"),
                        str(cell.get("pct") or "—"),
                        str(cell.get("day") or "—"),
                    ]
                else:
                    flat += ["—", "—", "—", "—"]
            elif spec == "share":
                if isinstance(cell, dict) and cell.get("kind") == "share":
                    flat.append(
                        {
                            "bar": cell.get("pct"),
                            "label": str(cell.get("label") or "—"),
                        }
                    )
                else:
                    flat.append("—")
            else:
                flat.append(_pdf_cell_label(cell) if cell is not None else "—")
        rows.append(
            {
                "kind": "total" if row.get("_total") else "data",
                "cells": flat,
            }
        )

    fractions = []
    for spec in specs:
        if spec == "quad":
            fractions += [0.046, 0.056, 0.054, 0.031]
        elif spec == "share":
            fractions.append(0.048)
        else:
            fractions.append(0.12)
    frac_total = sum(fractions) or 1.0
    widths = [frac / frac_total for frac in fractions]
    return {
        "kicker": "ITEM REPORT",
        "title": table.get("title") or "Item report by shop",
        "header_groups": header_groups,
        "headers": headers,
        "aligns": aligns,
        "widths": widths,
        "rows": rows,
        "empty": table.get("empty") or "No data.",
        "footnote": table.get("footnote") or "",
    }


def build_item_report_pdf(*, page: dict, filters: dict, company_name: str = "") -> bytes:
    """Landscape A4 PDF of the item report (per item x shop averages)."""
    period_label = filters.get("report_period_label") or "All time"
    selected = set(filters.get("selected_shop_ids") or [])
    filter_shops = list(filters.get("filter_shops") or [])
    if selected:
        names = [str(shop.name) for shop in filter_shops if shop.pk in selected]
        shop_label = ", ".join(names) if names else "Selected shops"
    else:
        shop_label = "All shops"

    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    return _analytics_report_pdf(
        title="Item report",
        subtitle=(
            page.get("lead")
            or "Avg qty, avg profit, and % profit per selling day, per shop."
        ),
        brand=brand,
        meta_lines=[shop_label, period_label],
        kpi_cards=_summary_board_kpi_cards(page.get("summary_board") or {}),
        sections=[
            _item_report_pdf_section(table)
            for table in page.get("tables") or []
        ],
        footer_note=f"{brand} · Item report · {shop_label} · {period_label}",
    )


def item_report_pdf_response(*, page: dict, filters: dict):
    """PDF download for the item report."""
    from django.http import HttpResponse

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_item_report_pdf(
        page=page, filters=filters, company_name=company_name
    )
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    filename = f"item-report-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def build_credit_items_pdf(*, page: dict, filters: dict, company_name: str = "") -> bytes:
    """Landscape A4 PDF of the credits items matrix (per item x shop)."""
    period_label = filters.get("report_period_label") or "All time"
    selected = set(filters.get("selected_shop_ids") or [])
    filter_shops = list(filters.get("filter_shops") or [])
    if selected:
        names = [str(shop.name) for shop in filter_shops if shop.pk in selected]
        shop_label = ", ".join(names) if names else "Selected shops"
    else:
        shop_label = "All shops"

    sections = []
    for table in page.get("tables") or []:
        if any(
            isinstance(col, dict) and col.get("quad")
            for col in table.get("columns") or []
        ):
            section = _item_report_pdf_section(table)
            section["kicker"] = "CREDIT ITEMS"
        else:
            headers, aligns, rows = _flatten_analytics_table(table)
            section = {
                "kicker": "CREDITS",
                "title": table.get("title") or "Table",
                "headers": headers,
                "aligns": aligns,
                "rows": rows,
                "empty": table.get("empty") or "No data.",
                "footnote": table.get("footnote") or "",
            }
        sections.append(section)

    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    return _analytics_report_pdf(
        title="Credit items report",
        subtitle=(
            page.get("lead")
            or "Qty on credit, credit amount, and % paid back per shop."
        ),
        brand=brand,
        meta_lines=[shop_label, period_label],
        kpi_cards=_summary_board_kpi_cards(page.get("summary_board") or {}),
        sections=sections,
        footer_note=f"{brand} · Credit items report · {shop_label} · {period_label}",
    )


def credit_items_pdf_response(*, page: dict, filters: dict):
    """PDF download for the credits items matrix."""
    from django.http import HttpResponse

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_credit_items_pdf(
        page=page, filters=filters, company_name=company_name
    )
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    filename = f"credit-items-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def build_receipts_report_pdf(*, page: dict, filters: dict, company_name: str = "") -> bytes:
    """Landscape A4 PDF of the filtered analytics receipts browse."""
    period_label = filters.get("report_period_label") or "All time"
    selected = set(filters.get("selected_shop_ids") or [])
    filter_shops = list(filters.get("filter_shops") or [])
    if selected:
        names = [str(shop.name) for shop in filter_shops if shop.pk in selected]
        shop_label = ", ".join(names) if names else "Selected shops"
    else:
        shop_label = "All shops"

    kind = (page.get("receipt_kind") or filters.get("receipt_kind") or "all").strip().lower()
    kind_label = {
        "all": "All types",
        "sales": "Sales",
        "credits": "Credits",
        "quotations": "Quotations",
        "trade_out": "Trade out",
        "trade_in": "Trade in",
        "stock": "Stock suppliers",
        "expense": "Expense suppliers",
        "cancelled": "Cancelled",
        "partial-returns": "Partial returns",
    }.get(kind, "All types")
    status_label = filters.get("receipt_status_label") or "Receipts"
    meta_lines = [shop_label, period_label, kind_label, status_label]

    sections = []
    for table in page.get("tables") or []:
        headers, aligns, rows = _flatten_analytics_table(table)
        sections.append(
            {
                "kicker": "RECEIPTS",
                "title": table.get("title") or "Table",
                "headers": headers,
                "aligns": aligns,
                "rows": rows,
                "empty": table.get("empty") or "No receipts.",
                "footnote": table.get("footnote") or "",
            }
        )

    brand = (company_name or "MY-SHOP").strip() or "MY-SHOP"
    return _analytics_report_pdf(
        title="Receipts report",
        subtitle=(
            page.get("lead")
            or "Filtered receipts across selected shops and period."
        ),
        brand=brand,
        meta_lines=meta_lines,
        kpi_cards=[
            (
                metric.get("label") or "",
                metric.get("value") or "—",
                metric.get("hint") or "",
            )
            for metric in page.get("metrics") or []
        ],
        sections=sections,
        footer_note=(
            f"{brand} · Receipts report · {shop_label} · {period_label} · {kind_label}"
        ),
    )


def receipts_report_pdf_response(*, page: dict, filters: dict, inline: bool = False):
    """PDF download/print for the filtered analytics receipts page."""
    from django.http import HttpResponse

    from shops.services import get_company_profile

    company = get_company_profile()
    company_name = (getattr(company, "name", None) or "").strip() or "MY-SHOP"
    pdf_bytes = build_receipts_report_pdf(
        page=page, filters=filters, company_name=company_name
    )
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    kind = (page.get("receipt_kind") or "all").strip().lower() or "all"
    filename = f"receipts-{kind}-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


def build_receipts_print_document(*, filters) -> dict:
    """Printable receipts list for the stock-style print popup."""
    browse = _collect_analytics_receipts_browse(
        filters=filters, include_receipt_actions=False
    )
    rows = [
        {
            "number": row.get("number") or "—",
            "kind": row.get("kind") or "—",
            "client_name": row.get("client_name") or "—",
            "phone": row.get("phone") or "—",
            "items": row.get("items") or "—",
            "total": row.get("total") or "—",
            "when": row.get("when") or "—",
            "reference": "",
        }
        for row in browse["receipt_rows"]
    ]
    # Rough A4 estimate: header + ~28 receipt rows per page.
    receipt_total = len(rows)
    a4_page_estimate = max(1, (receipt_total + 27) // 28)
    return {
        "title": "Receipts",
        "kind_label": browse["kind_label"],
        "period_label": browse["period_label"],
        "receipt_kind": browse["receipt_kind"] or "all",
        "receipt_total": receipt_total,
        "grand_amount": _money_ksh(browse["grand_amount"]),
        "shop_count": len(browse["shops"]),
        "a4_page_estimate": a4_page_estimate,
        "rows": rows,
    }


def _account_pay_url(role) -> str:
    from django.urls import reverse

    from employees.access import role_url_segment

    return reverse(
        "employees:analytics_account_pay",
        kwargs={"role_segment": role_url_segment(role)},
    )


def analytics_employee_url(role, employee_id, *, query: str = "") -> str:
    """Analytics → Employees drill-down (/…/analytics/employees/<id>/)."""
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        "employees:analytics_employee",
        kwargs={
            "role_segment": role_url_segment(role),
            "employee_id": int(employee_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


def supplier_account_url(role, kind: str, supplier_id, *, query: str = "") -> str:
    from django.urls import reverse

    from employees.access import role_url_segment

    kind = (kind or "").strip().lower()
    if kind not in ("expense", "stock"):
        kind = "expense"
    href = reverse(
        "employees:analytics_supplier_account",
        kwargs={
            "role_segment": role_url_segment(role),
            "kind": kind,
            "supplier_id": int(supplier_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


def item_suppliers_url(role, item_id, *, query: str = "") -> str:
    """Item-suppliers page URL — the suppliers who supplied one catalog item."""
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        "employees:analytics_item_suppliers",
        kwargs={
            "role_segment": role_url_segment(role),
            "item_id": int(item_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


def shelf_item_suppliers_url(role, shelf_item_id, *, query: str = "") -> str:
    """Shelf-item-suppliers page URL — suppliers who supplied one shelf item."""
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        "employees:analytics_shelf_item_suppliers",
        kwargs={
            "role_segment": role_url_segment(role),
            "item_id": int(shelf_item_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


def _stock_lines_for_supplier(supplier: Supplier, shop_ids):
    return StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__movement_type=StockMovementType.IN,
        supplier_name__iexact=supplier.name,
        supplier_phone_country_code=supplier.phone_country_code,
        supplier_phone_number=supplier.phone_number,
    ).select_related("movement", "movement__shop", "item")


def _shelf_movements_for_supplier(name: str, phone: str, shop_ids):
    """Shelf stock-in movements keyed by the free-text supplier pair."""
    return ShelfStockMovement.objects.filter(
        shelf_item__shop_id__in=shop_ids,
        movement_type=ShelfStockMovementType.IN,
        supplier_name__iexact=(name or "").strip(),
        supplier_phone__iexact=(phone or "").strip(),
    )


def _shelf_movement_total(movement) -> Decimal:
    qty = Decimal(movement.quantity or 0)
    unit = Decimal(movement.buying_price or 0)
    return (unit * qty).quantize(Decimal("0.01"))


def _shelf_supplier_stub(name: str, phone: str):
    """Supplier-shaped object so shelf suppliers get payment notices too."""
    from types import SimpleNamespace

    return SimpleNamespace(
        pk=None,
        name=(name or "").strip(),
        phone_country_code="",
        phone_number=(phone or "").strip(),
    )


def _shelf_movements_for_identity(
    name: str, phone_raw: str, shop_ids, start=None, end=None
):
    """Shelf IN movements matching a supplier by name + national phone digits.

    Unlike `_shelf_movements_for_supplier` (exact pair match), this compares
    normalized phone digits so "+254 722…" and "0722…" merge into one supplier.
    """
    target = _supplier_phone_digits(phone_raw)
    rows = ShelfStockMovement.objects.filter(
        shelf_item__shop_id__in=shop_ids,
        movement_type=ShelfStockMovementType.IN,
        supplier_name__iexact=(name or "").strip(),
    ).select_related(
        "shelf_item",
        "shelf_item__shop",
        "created_by",
        "created_by__user",
    )
    rows = _within_created_range(rows, start, end)
    return [
        row for row in rows if _supplier_phone_digits(row.supplier_phone) == target
    ]


def _item_movements_for_identity(
    name: str, phone_raw: str, shop_ids, start=None, end=None
):
    """Item stock movements carrying this supplier's lines → {pk: bundle}.

    Lines store supplier identity as free text; match on name + normalized
    national phone digits so shelf and item activity for one supplier merge.
    """
    target = _supplier_phone_digits(phone_raw)
    lines = (
        StockMovementLine.objects.filter(
            movement__shop_id__in=shop_ids,
            movement__movement_type=StockMovementType.IN,
            supplier_name__iexact=(name or "").strip(),
        )
        .select_related(
            "movement",
            "movement__shop",
            "movement__created_by",
            "movement__created_by__user",
            "item",
        )
    )
    lines = _within_created_range(
        lines, start, end, lookup="movement__created_at"
    )
    by_movement: dict[int, dict] = {}
    for line in lines:
        if (
            _supplier_phone_digits(
                line.supplier_phone_country_code, line.supplier_phone_number
            )
            != target
        ):
            continue
        movement = line.movement
        if movement is None:
            continue
        info = by_movement.get(movement.pk)
        if info is None:
            info = by_movement[movement.pk] = {
                "movement": movement,
                "total": _zero(),
                "lines": [],
            }
        qty = Decimal(line.quantity or 0)
        unit = Decimal(line.buying_price or 0)
        info["total"] += (unit * qty).quantize(Decimal("0.01"))
        info["lines"].append(line)
    return by_movement


def build_supplier_account(
    *, profile, kind: str, supplier_id=None, request=None, shop_ids=None
) -> dict:
    """Ledger for a stock, expense or shelf supplier."""
    kind = (kind or "").strip().lower()
    if kind not in ("expense", "stock", "shelf"):
        raise Http404("Supplier type not found.")

    shop_filter = _allocated_shop_filter(profile, request, shop_ids=shop_ids)
    shop_ids = shop_filter["active_shop_ids"]
    date_filter = _date_filter_context(request, allow_all_time=True)
    start, end = date_filter["start"], date_filter["end"]
    period_label = date_filter.get("report_period_label") or "All time"
    scope_hint = (
        shop_filter["selected_shops"][0].name
        if len(shop_filter["selected_shops"]) == 1
        else shop_filter["shop_filter_label"]
    )

    if kind == "expense":
        supplier = ExpenseSupplier.objects.filter(pk=supplier_id).first()
        if supplier is None:
            raise Http404("Supplier not found.")
        if not shop_ids or not Expense.objects.filter(
            shop_id__in=shop_ids, supplier_id=supplier.pk
        ).exists():
            raise Http404("Supplier not found.")
        expenses = list(
            _within_created_range(
                Expense.objects.filter(shop_id__in=shop_ids, supplier_id=supplier.pk),
                start,
                end,
            )
            .select_related("shop", "created_by", "created_by__user")
            .order_by("-created_at")
        )
        balance = _zero()
        total = _zero()
        rows = []
        for row in expenses:
            cashier = ""
            if row.created_by and row.created_by.user:
                cashier = (
                    row.created_by.user.get_full_name()
                    or row.created_by.employee_id
                    or row.created_by.user.username
                )
            amount = Decimal(row.amount or 0)
            paid = Decimal(row.amount_paid or 0)
            due = _due_amount(amount, paid)
            total += amount
            balance += due
            lines = [
                {
                    "name": row.name or "Expense",
                    "qty": 1,
                    "unit": _money_ksh(amount),
                    "total": _money_ksh(amount),
                    "meta": row.get_category_display(),
                }
            ]
            rows.append(
                {
                    "id": f"expense-{row.pk}",
                    "pay_kind": "expense",
                    "pay_id": row.pk,
                    "number": format_simple_doc_number("E", row.pk),
                    "shop": row.shop.name if row.shop else "—",
                    "status": _payment_status_for_due(due, paid),
                    "status_tone": _status_tone(_payment_status_for_due(due, paid)),
                    "total": _money_ksh(amount),
                    "paid": _money_ksh(paid),
                    "due": _money_ksh(due),
                    "due_raw": str(due),
                    "can_pay": due > 0,
                    "when": row.created_at,
                    "cashier": cashier or "—",
                    "item_count": 1,
                    "lines": lines,
                }
            )
        phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
        return {
            "kind": "expense",
            "kind_label": "Expense supplier",
            "supplier": supplier,
            "supplier_name": supplier.name,
            "supplier_phone": phone,
            "entries": len(expenses),
            "balance": _money_ksh(balance),
            "balance_raw": str(balance),
            "total": _money_ksh(total),
            "rows": rows,
            "ledger_title": "Expense receipts",
            "empty_message": (
                f"No expense receipts for this supplier at {scope_hint} ({period_label})."
                if shop_filter["selected_shop_ids"]
                else f"No expense receipts for this supplier in your shops ({period_label})."
            ),
            "account_kind": "expense",
            "account_id": supplier.pk,
            "can_pay": True,
            "ledger_show_receipt_pay": True,
            "scope_hint": scope_hint,
            **shop_filter,
            **date_filter,
        }

    if kind == "shelf":
        # Shelf suppliers are free-text on ShelfStockMovement (name + phone);
        # the account is keyed by that pair from the query string.
        name = ""
        phone = ""
        if request is not None:
            name = (request.GET.get("name") or "").strip()
            phone = (request.GET.get("phone") or "").strip()
        if not name:
            raise Http404("Supplier not found.")
        # Identity matching (name + national phone digits) so format variants
        # like "0722…" vs "+254 722…" land on the same account, and item
        # stock-ins from the same supplier merge in below.
        movements = _shelf_movements_for_identity(
            name, phone, shop_ids, start=start, end=end
        )
        item_map = _item_movements_for_identity(
            name, phone, shop_ids, start=start, end=end
        )
        if not shop_ids or (not movements and not item_map):
            raise Http404("Supplier not found.")
        balance = _zero()
        total = _zero()
        rows = []
        for movement in movements:
            qty = Decimal(movement.quantity or 0)
            unit = Decimal(movement.buying_price or 0)
            line_total = (unit * qty).quantize(Decimal("0.01"))
            paid = Decimal(movement.amount_paid or 0)
            due = _due_amount(line_total, paid)
            total += line_total
            balance += due
            status_label = _payment_status_for_due(due, paid)
            cashier = ""
            profile_row = getattr(movement, "created_by", None)
            if profile_row and getattr(profile_row, "user", None):
                cashier = (
                    profile_row.user.get_full_name()
                    or profile_row.employee_id
                    or profile_row.user.username
                )
            shelf_item = movement.shelf_item
            rows.append(
                {
                    "id": f"shelf-{movement.pk}",
                    "pay_kind": "shelf",
                    "pay_id": movement.pk,
                    "kind_label": "Shelf stock",
                    "number": format_simple_doc_number("ST", movement.pk),
                    "shop": (
                        shelf_item.shop.name
                        if shelf_item and shelf_item.shop
                        else "—"
                    ),
                    "status": status_label,
                    "status_tone": _status_tone(status_label),
                    "total": _money_ksh(line_total),
                    "paid": _money_ksh(paid),
                    "due": _money_ksh(due),
                    "due_raw": str(due),
                    "can_pay": due > 0,
                    "when": movement.created_at,
                    "cashier": cashier or "—",
                    "item_count": 1,
                    "lines": [
                        {
                            "name": (
                                shelf_item.name if shelf_item else "Shelf item"
                            ),
                            "qty": _qty_number(qty),
                            "unit": _money_ksh(unit),
                            "total": _money_ksh(line_total),
                            "meta": status_label,
                        }
                    ],
                }
            )
        # Item stock-ins from the same supplier identity merge into one ledger.
        for mid in sorted(
            item_map, key=lambda pk: (item_map[pk]["movement"].created_at, pk)
        ):
            info = item_map[mid]
            movement = info["movement"]
            receipt_total = info["total"]
            paid = Decimal(movement.amount_paid or 0)
            due = _due_amount(receipt_total, paid)
            total += receipt_total
            balance += due
            status_label = _payment_status_for_due(due, paid)
            cashier = ""
            profile_row = getattr(movement, "created_by", None)
            if profile_row and getattr(profile_row, "user", None):
                cashier = (
                    profile_row.user.get_full_name()
                    or profile_row.employee_id
                    or profile_row.user.username
                )
            receipt_lines = []
            for line in info["lines"]:
                qty = Decimal(line.quantity or 0)
                unit = Decimal(line.buying_price or 0)
                line_total = (unit * qty).quantize(Decimal("0.01"))
                receipt_lines.append(
                    {
                        "name": (
                            line.item.name
                            if getattr(line, "item", None)
                            else (line.item_name or "Item")
                        ),
                        "qty": _qty_number(qty),
                        "unit": _money_ksh(unit),
                        "total": _money_ksh(line_total),
                        "meta": dict(StockPaymentStatus.choices).get(
                            (line.payment_status or "").strip(),
                            (line.payment_status or "").strip(),
                        ),
                    }
                )
            rows.append(
                {
                    "id": f"stock-{movement.pk}",
                    "pay_kind": "stock",
                    "pay_id": movement.pk,
                    "kind_label": "Item stock",
                    "number": format_simple_doc_number("I", movement.pk),
                    "shop": movement.shop.name if movement.shop else "—",
                    "status": status_label,
                    "status_tone": _status_tone(status_label),
                    "total": _money_ksh(receipt_total),
                    "paid": _money_ksh(paid),
                    "due": _money_ksh(due),
                    "due_raw": str(due),
                    "can_pay": due > 0,
                    "when": movement.created_at,
                    "cashier": cashier or "—",
                    "item_count": len(receipt_lines),
                    "lines": receipt_lines,
                }
            )
        rows.sort(key=lambda row: (row["when"], -row["pay_id"]), reverse=True)
        return {
            "kind": "shelf",
            "kind_label": "Shelf supplier",
            "supplier": None,
            "supplier_name": name,
            "supplier_phone": phone,
            "entries": len(rows),
            "balance": _money_ksh(balance),
            "balance_raw": str(balance),
            "total": _money_ksh(total),
            "rows": rows,
            "ledger_title": "Stock-in receipts",
            "empty_message": (
                f"No stock-in receipts for this supplier at {scope_hint} ({period_label})."
                if shop_filter["selected_shop_ids"]
                else f"No stock-in receipts for this supplier in your shops ({period_label})."
            ),
            "account_kind": "shelf",
            "account_id": 0,
            "can_pay": True,
            "ledger_show_receipt_pay": True,
            "ledger_show_type": True,
            "scope_hint": scope_hint,
            **shop_filter,
            **date_filter,
        }

    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if supplier is None:
        raise Http404("Supplier not found.")
    phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
    has_shelf = bool(
        _shelf_movements_for_identity(supplier.name, phone, shop_ids)
    )
    if not shop_ids or (
        not _stock_lines_for_supplier(supplier, shop_ids).exists()
        and not has_shelf
    ):
        raise Http404("Supplier not found.")

    lines = list(
        _within_created_range(
            _stock_lines_for_supplier(supplier, shop_ids),
            start,
            end,
            lookup="movement__created_at",
        )
        .select_related(
            "movement",
            "movement__shop",
            "movement__created_by",
            "movement__created_by__user",
            "item",
        )
        .order_by("-movement__created_at", "pk")
    )
    by_movement = {}
    order = []
    for line in lines:
        movement = line.movement
        if movement is None:
            continue
        key = movement.pk
        if key not in by_movement:
            by_movement[key] = {"movement": movement, "lines": []}
            order.append(key)
        by_movement[key]["lines"].append(line)

    balance = _zero()
    total = _zero()
    rows = []
    for key in order:
        bundle = by_movement[key]
        movement = bundle["movement"]
        receipt_lines = []
        receipt_total = _zero()
        for line in bundle["lines"]:
            qty = Decimal(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            line_total = (unit * qty).quantize(Decimal("0.01"))
            receipt_total += line_total
            receipt_lines.append(
                {
                    "name": (
                        line.item.name
                        if line.item
                        else (line.item_name or "Item")
                    ),
                    "qty": _qty_number(qty),
                    "unit": _money_ksh(unit),
                    "total": _money_ksh(line_total),
                    "meta": dict(StockPaymentStatus.choices).get(
                        (line.payment_status or "").strip(),
                        (line.payment_status or "").strip(),
                    ),
                }
            )
        paid = Decimal(movement.amount_paid or 0)
        due = _due_amount(receipt_total, paid)
        total += receipt_total
        balance += due
        cashier = ""
        profile_row = getattr(movement, "created_by", None)
        if profile_row and getattr(profile_row, "user", None):
            cashier = (
                profile_row.user.get_full_name()
                or profile_row.employee_id
                or profile_row.user.username
            )
        rows.append(
            {
                "id": f"stock-{movement.pk}",
                "pay_kind": "stock",
                "pay_id": movement.pk,
                "kind_label": "Item stock",
                "number": format_simple_doc_number("I", movement.pk),
                "shop": movement.shop.name if movement.shop else "—",
                "status": _payment_status_for_due(due, paid),
                "status_tone": _status_tone(_payment_status_for_due(due, paid)),
                "total": _money_ksh(receipt_total),
                "paid": _money_ksh(paid),
                "due": _money_ksh(due),
                "due_raw": str(due),
                "can_pay": due > 0,
                "when": movement.created_at,
                "cashier": cashier or "—",
                "item_count": len(receipt_lines),
                "lines": receipt_lines,
            }
        )

    # Shelf stock-ins from the same supplier identity merge into one ledger.
    shelf_movements = _shelf_movements_for_identity(
        supplier.name,
        f"{supplier.phone_country_code} {supplier.phone_number}",
        shop_ids,
        start=start,
        end=end,
    )
    for movement in shelf_movements:
        qty = Decimal(movement.quantity or 0)
        unit = Decimal(movement.buying_price or 0)
        line_total = (unit * qty).quantize(Decimal("0.01"))
        paid = Decimal(movement.amount_paid or 0)
        due = _due_amount(line_total, paid)
        total += line_total
        balance += due
        status_label = _payment_status_for_due(due, paid)
        cashier = ""
        profile_row = getattr(movement, "created_by", None)
        if profile_row and getattr(profile_row, "user", None):
            cashier = (
                profile_row.user.get_full_name()
                or profile_row.employee_id
                or profile_row.user.username
            )
        shelf_item = movement.shelf_item
        rows.append(
            {
                "id": f"shelf-{movement.pk}",
                "pay_kind": "shelf",
                "pay_id": movement.pk,
                "kind_label": "Shelf stock",
                "number": format_simple_doc_number("ST", movement.pk),
                "shop": (
                    shelf_item.shop.name
                    if shelf_item and shelf_item.shop
                    else "—"
                ),
                "status": status_label,
                "status_tone": _status_tone(status_label),
                "total": _money_ksh(line_total),
                "paid": _money_ksh(paid),
                "due": _money_ksh(due),
                "due_raw": str(due),
                "can_pay": due > 0,
                "when": movement.created_at,
                "cashier": cashier or "—",
                "item_count": 1,
                "lines": [
                    {
                        "name": (
                            shelf_item.name if shelf_item else "Shelf item"
                        ),
                        "qty": _qty_number(qty),
                        "unit": _money_ksh(unit),
                        "total": _money_ksh(line_total),
                        "meta": status_label,
                    }
                ],
            }
        )
    rows.sort(key=lambda row: (row["when"], -row["pay_id"]), reverse=True)

    return {
        "kind": "stock",
        "kind_label": "Stock supplier",
        "supplier": supplier,
        "supplier_name": supplier.name,
        "supplier_phone": phone,
        "entries": len(rows),
        "balance": _money_ksh(balance),
        "balance_raw": str(balance),
        "total": _money_ksh(total),
        "rows": rows,
        "ledger_title": "Stock-in receipts",
        "ledger_show_type": True,
        "empty_message": (
            f"No stock-in receipts for this supplier at {scope_hint} ({period_label})."
            if shop_filter["selected_shop_ids"]
            else f"No stock-in receipts for this supplier in your shops ({period_label})."
        ),
        "account_kind": "stock",
        "account_id": supplier.pk,
        "can_pay": True,
        "ledger_show_receipt_pay": True,
        "scope_hint": scope_hint,
        **shop_filter,
        **date_filter,
    }


def _registered_supplier_balance(supplier, shop_ids, start, end) -> Decimal:
    """Outstanding due across a registered supplier's stock + shelf receipts."""
    due_total = _zero()
    lines = _within_created_range(
        _stock_lines_for_supplier(supplier, shop_ids),
        start,
        end,
        lookup="movement__created_at",
    ).select_related("movement")
    by_movement: dict[int, dict] = {}
    for line in lines:
        movement = line.movement
        if movement is None:
            continue
        info = by_movement.get(movement.pk)
        if info is None:
            info = by_movement[movement.pk] = {
                "total": _zero(),
                "paid": Decimal(movement.amount_paid or 0),
            }
        qty = Decimal(line.quantity or 0)
        unit = Decimal(line.buying_price or 0)
        info["total"] += (unit * qty).quantize(Decimal("0.01"))
    for info in by_movement.values():
        due_total += _due_amount(info["total"], info["paid"])
    phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
    for movement in _shelf_movements_for_identity(
        supplier.name, phone, shop_ids, start=start, end=end
    ):
        due_total += _due_amount(
            _shelf_movement_total(movement), movement.amount_paid
        )
    return due_total


def _identity_supplier_balance(name, phone, shop_ids, start, end) -> Decimal:
    """Outstanding due for a free-text supplier identity (shelf + item lines)."""
    due_total = _zero()
    for movement in _shelf_movements_for_identity(
        name, phone, shop_ids, start=start, end=end
    ):
        due_total += _due_amount(
            _shelf_movement_total(movement), movement.amount_paid
        )
    for info in _item_movements_for_identity(
        name, phone, shop_ids, start=start, end=end
    ).values():
        due_total += _due_amount(
            info["total"], Decimal(info["movement"].amount_paid or 0)
        )
    return due_total


def build_item_suppliers_page(*, profile, request, item_id) -> dict:
    """Suppliers who supplied one catalog item — balances and pay actions."""
    from django.urls import reverse

    from employees.access import role_url_segment

    from .module_permissions import employee_may

    item = Item.objects.filter(pk=item_id).first()
    if item is None:
        raise Http404("Item not found.")

    shop_filter = _allocated_shop_filter(profile, request)
    shop_ids = shop_filter["active_shop_ids"]
    date_filter = _date_filter_context(request, allow_all_time=True)
    start, end = date_filter["start"], date_filter["end"]
    period_label = date_filter.get("report_period_label") or "All time"
    scope_hint = (
        shop_filter["selected_shops"][0].name
        if len(shop_filter["selected_shops"]) == 1
        else shop_filter["shop_filter_label"]
    )
    query = request.GET.urlencode()
    role = profile.role
    can_pay = employee_may(profile, "analytics", "account_pay")

    # A stock-in line's supplier text resolves to the Supplier registry via
    # the normalized identity (name + national phone digits); anything else
    # stays a free-text supplier that opens the name/phone ledger.
    identity_to_supplier = {
        _supplier_identity_key(
            supplier.name, supplier.phone_country_code, supplier.phone_number
        ): supplier
        for supplier in Supplier.objects.all()
    }

    lines = list(
        _within_created_range(
            StockMovementLine.objects.filter(
                movement__shop_id__in=shop_ids,
                movement__movement_type=StockMovementType.IN,
                item_id=item.pk,
            ).exclude(supplier_name=""),
            start,
            end,
            lookup="movement__created_at",
        ).select_related("movement", "movement__shop")
    )

    suppliers_data: dict[tuple, dict] = {}
    for line in lines:
        movement = line.movement
        if movement is None:
            continue
        name = (line.supplier_name or "").strip()
        if not name:
            continue
        key = _supplier_identity_key(
            name,
            line.supplier_phone_country_code,
            line.supplier_phone_number,
        )
        info = suppliers_data.get(key)
        if info is None:
            info = suppliers_data[key] = {
                "name": name,
                "phone_cc": (line.supplier_phone_country_code or "").strip(),
                "phone_num": (line.supplier_phone_number or "").strip(),
                "receipt_ids": set(),
                "qty": _zero(),
                "value": _zero(),
            }
        info["receipt_ids"].add(movement.pk)
        qty = Decimal(line.quantity or 0)
        unit = Decimal(line.buying_price or 0)
        info["qty"] += qty
        info["value"] += (unit * qty).quantize(Decimal("0.01"))

    ranked = []
    total_qty = _zero()
    total_value = _zero()
    total_due = _zero()
    for key, info in suppliers_data.items():
        supplier = identity_to_supplier.get(key)
        name = info["name"]
        if supplier is not None:
            phone = (
                f"{supplier.phone_country_code} {supplier.phone_number}".strip()
            )
            cell = _supplier_entity_cell(role, "stock", supplier, query)
            due = _registered_supplier_balance(supplier, shop_ids, start, end)
            pay_kind = "stock"
            account_id = supplier.pk
        else:
            phone = f"{info['phone_cc']} {info['phone_num']}".strip()
            stub = SimpleNamespace(
                pk=key,
                name=name,
                phone_country_code=info["phone_cc"],
                phone_number=info["phone_num"],
            )
            cell = _shelf_supplier_cell(stub, role=role, query=query)
            due = _identity_supplier_balance(
                name, phone, shop_ids, start, end
            )
            pay_kind = "shelf"
            account_id = 0
        total_qty += info["qty"]
        total_value += info["value"]
        total_due += due
        ranked.append((due, name.lower(), cell, info, phone, pay_kind, account_id))
    ranked.sort(key=lambda row: (-row[0], row[1]))

    columns = [
        "Supplier",
        "Receipts",
        "Qty",
        {"label": "Supplied", "money": True},
        {"label": "Balance", "money": True},
    ]
    if can_pay:
        columns.append("Pay")
    rows = []
    for due, _sort, cell, info, phone, pay_kind, account_id in ranked:
        cells = [
            cell,
            str(len(info["receipt_ids"])),
            _qty_label(info["qty"]),
            _money_cell(
                info["value"],
                title=(
                    f"{_qty_label(info['qty'])} supplied · "
                    f"{_money_ksh(info['value'])}"
                ),
            ),
            _money_cell(
                due,
                tone="warn" if due > 0 else "neutral",
                title="Outstanding across all their receipts in this period",
            ),
        ]
        if can_pay:
            cells.append(
                {
                    "kind": "pay",
                    "label": "Pay",
                    "pay_kind": pay_kind,
                    "account_id": account_id,
                    "supplier_name": info["name"],
                    "supplier_phone": phone,
                    "pay_id": "",
                    "number": info["name"],
                    "due": _money_ksh(due),
                    "due_raw": str(due),
                    "shop": "",
                    "can_pay": due > 0,
                }
            )
        rows.append(cells)

    back_href = _suppliers_view_url(
        request, role, view="items", mode="supplied"
    )
    receipt_links = sum(len(info["receipt_ids"]) for info in suppliers_data.values())
    return {
        "headline": item.name or "Item",
        "period_label": period_label,
        "lead": (
            f"Suppliers who supplied this item at {scope_hint} in "
            f"{period_label}. Open a supplier for their receipts, or pay "
            f"their balance here."
        ),
        "view": "items",
        "list_mode": "supplied",
        "back_href": back_href,
        "back_label": "Back to items supplied",
        "show_search": True,
        "search_placeholder": "Search suppliers…",
        "search_empty": "No suppliers match that search.",
        "list_table_layout": True,
        "alerts": [],
        "insights": [],
        "metrics": [
            {
                "label": "Suppliers",
                "value": str(len(suppliers_data)),
                "hint": "linked to this item",
            },
            {
                "label": "Qty supplied",
                "value": _qty_label(total_qty),
                "hint": f"in {period_label}",
            },
            {
                "label": "Supplied value",
                "value": _money_ksh(total_value),
                "hint": (
                    f"{receipt_links} receipt"
                    f"{'s' if receipt_links != 1 else ''}"
                ),
            },
            {
                "label": "Balance",
                "value": _money_ksh(total_due),
                "hint": "owed to these suppliers",
                "tone": "warn" if total_due > 0 else "good",
            },
        ],
        "tables": [
            _table(
                "Suppliers to pay",
                columns,
                rows,
                empty=(
                    f"No supplier-linked stock-ins for this item at "
                    f"{scope_hint} in {period_label}."
                ),
                footnote=(
                    "Balance covers all the supplier's receipts in this "
                    "period. Pay clears their oldest receipts first."
                ),
                shop_grid=False,
                searchable=True,
            )
        ],
        "supplier_pay": bool(can_pay),
        "pay_url": reverse(
            "employees:analytics_account_pay",
            kwargs={"role_segment": role_url_segment(role)},
        ),
        "pay_balance": _money_ksh(total_due),
        "pay_balance_raw": str(total_due),
    }


def _suppliers_view_url(request, role, *, view: str, mode: str = "") -> str:
    """Suppliers workspace URL carrying the current filters + tab/mode."""
    from urllib.parse import urlencode

    from .workspace import analytics_section_url

    params = request.GET.copy() if request is not None else {}
    for key in ("view", "mode", "page", "download", "name", "phone"):
        params.pop(key, None)
    params["view"] = view
    if mode:
        params["mode"] = mode
    href = analytics_section_url(role, "suppliers")
    encoded = (
        params.urlencode() if hasattr(params, "urlencode") else urlencode(params)
    )
    return f"{href}?{encoded}" if encoded else href


def _supplier_mode_href(query: str, view: str, mode: str) -> str:
    """Same-page href with the suppliers tab + in-page mode swapped in."""
    from urllib.parse import parse_qs, urlencode

    params = parse_qs(query or "", keep_blank_values=True)
    flat = {key: values[0] for key, values in params.items()}
    flat.pop("download", None)
    flat.pop("page", None)
    flat["view"] = view
    if mode:
        flat["mode"] = mode
    else:
        flat.pop("mode", None)
    return f"?{urlencode(flat)}"


def build_shelf_item_suppliers_page(
    *, profile, request, shelf_item_id
) -> dict:
    """Suppliers who supplied one shelf item — balances and pay actions."""
    from django.urls import reverse

    from employees.access import role_url_segment

    from .module_permissions import employee_may

    shelf_item = ShelfItem.objects.filter(pk=shelf_item_id).first()
    if shelf_item is None:
        raise Http404("Shelf item not found.")

    shop_filter = _allocated_shop_filter(profile, request)
    shop_ids = shop_filter["active_shop_ids"]
    date_filter = _date_filter_context(request, allow_all_time=True)
    start, end = date_filter["start"], date_filter["end"]
    period_label = date_filter.get("report_period_label") or "All time"
    scope_hint = (
        shop_filter["selected_shops"][0].name
        if len(shop_filter["selected_shops"]) == 1
        else shop_filter["shop_filter_label"]
    )
    query = request.GET.urlencode()
    role = profile.role
    can_pay = employee_may(profile, "analytics", "account_pay")

    identity_to_supplier = {
        _supplier_identity_key(
            supplier.name, supplier.phone_country_code, supplier.phone_number
        ): supplier
        for supplier in Supplier.objects.all()
    }

    movements = list(
        _within_created_range(
            ShelfStockMovement.objects.filter(
                shelf_item_id=shelf_item.pk,
                shelf_item__shop_id__in=shop_ids,
                movement_type=ShelfStockMovementType.IN,
            ).exclude(supplier_name=""),
            start,
            end,
        )
    )

    suppliers_data: dict[tuple, dict] = {}
    for movement in movements:
        name = (movement.supplier_name or "").strip()
        if not name:
            continue
        phone = (movement.supplier_phone or "").strip()
        key = _supplier_identity_key(name, phone)
        info = suppliers_data.get(key)
        if info is None:
            info = suppliers_data[key] = {
                "name": name,
                "phone": phone,
                "receipt_ids": set(),
                "qty": _zero(),
                "value": _zero(),
            }
        info["receipt_ids"].add(movement.pk)
        qty = Decimal(movement.quantity or 0)
        unit = Decimal(movement.buying_price or 0)
        info["qty"] += qty
        info["value"] += (unit * qty).quantize(Decimal("0.01"))

    ranked = []
    total_qty = _zero()
    total_value = _zero()
    total_due = _zero()
    for key, info in suppliers_data.items():
        supplier = identity_to_supplier.get(key)
        name = info["name"]
        phone = info["phone"]
        if supplier is not None:
            phone = (
                f"{supplier.phone_country_code} {supplier.phone_number}".strip()
            )
            cell = _supplier_entity_cell(role, "stock", supplier, query)
            due = _registered_supplier_balance(supplier, shop_ids, start, end)
            pay_kind = "stock"
            account_id = supplier.pk
        else:
            stub = SimpleNamespace(
                pk=key,
                name=name,
                phone_country_code="",
                phone_number=phone,
            )
            cell = _shelf_supplier_cell(stub, role=role, query=query)
            due = _identity_supplier_balance(
                name, phone, shop_ids, start, end
            )
            pay_kind = "shelf"
            account_id = 0
        total_qty += info["qty"]
        total_value += info["value"]
        total_due += due
        ranked.append((due, name.lower(), cell, info, phone, pay_kind, account_id))
    ranked.sort(key=lambda row: (-row[0], row[1]))

    columns = [
        "Supplier",
        "Receipts",
        "Qty",
        {"label": "Supplied", "money": True},
        {"label": "Balance", "money": True},
    ]
    if can_pay:
        columns.append("Pay")
    rows = []
    for due, _sort, cell, info, phone, pay_kind, account_id in ranked:
        cells = [
            cell,
            str(len(info["receipt_ids"])),
            _qty_label(info["qty"]),
            _money_cell(
                info["value"],
                title=(
                    f"{_qty_label(info['qty'])} supplied · "
                    f"{_money_ksh(info['value'])}"
                ),
            ),
            _money_cell(
                due,
                tone="warn" if due > 0 else "neutral",
                title="Outstanding across all their receipts in this period",
            ),
        ]
        if can_pay:
            cells.append(
                {
                    "kind": "pay",
                    "label": "Pay",
                    "pay_kind": pay_kind,
                    "account_id": account_id,
                    "supplier_name": info["name"],
                    "supplier_phone": phone,
                    "pay_id": "",
                    "number": info["name"],
                    "due": _money_ksh(due),
                    "due_raw": str(due),
                    "shop": "",
                    "can_pay": due > 0,
                }
            )
        rows.append(cells)

    back_href = _suppliers_view_url(
        request, role, view="shelf", mode="supplied"
    )
    receipt_links = sum(len(info["receipt_ids"]) for info in suppliers_data.values())
    return {
        "headline": shelf_item.name or "Shelf item",
        "period_label": period_label,
        "lead": (
            f"Suppliers who supplied this shelf item at {scope_hint} in "
            f"{period_label}. Open a supplier for their receipts, or pay "
            f"their balance here."
        ),
        "view": "shelf",
        "list_mode": "supplied",
        "back_href": back_href,
        "back_label": "Back to items supplied",
        "show_search": True,
        "search_placeholder": "Search suppliers…",
        "search_empty": "No suppliers match that search.",
        "list_table_layout": True,
        "alerts": [],
        "insights": [],
        "metrics": [
            {
                "label": "Suppliers",
                "value": str(len(suppliers_data)),
                "hint": "linked to this shelf item",
            },
            {
                "label": "Qty supplied",
                "value": _qty_label(total_qty),
                "hint": f"in {period_label}",
            },
            {
                "label": "Supplied value",
                "value": _money_ksh(total_value),
                "hint": (
                    f"{receipt_links} receipt"
                    f"{'s' if receipt_links != 1 else ''}"
                ),
            },
            {
                "label": "Balance",
                "value": _money_ksh(total_due),
                "hint": "owed to these suppliers",
                "tone": "warn" if total_due > 0 else "good",
            },
        ],
        "tables": [
            _table(
                "Suppliers to pay",
                columns,
                rows,
                empty=(
                    f"No supplier-linked shelf stock-ins for this item at "
                    f"{scope_hint} in {period_label}."
                ),
                footnote=(
                    "Balance covers all the supplier's receipts in this "
                    "period. Pay clears their oldest receipts first."
                ),
                shop_grid=False,
                searchable=True,
            )
        ],
        "supplier_pay": bool(can_pay),
        "pay_url": reverse(
            "employees:analytics_account_pay",
            kwargs={"role_segment": role_url_segment(role)},
        ),
        "pay_balance": _money_ksh(total_due),
        "pay_balance_raw": str(total_due),
    }


def _common_receipt_sets(filters):
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    prev_start, prev_end = filters["prev_start"], filters["prev_end"]
    receipt_status = filters.get("receipt_status")
    sales = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.SALE],
        receipt_status=receipt_status,
    )
    prev_sales = _receipts_qs(
        shop_ids=shop_ids,
        start=prev_start,
        end=prev_end,
        kinds=[ShopReceiptKind.SALE],
        receipt_status=receipt_status,
    )
    credits = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.CREDIT],
        receipt_status=receipt_status,
    )
    quotes = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.QUOTATION],
        receipt_status=receipt_status,
    )
    expenses = _within_created_range(
        Expense.objects.filter(shop_id__in=shop_ids),
        start,
        end,
    )
    return sales, prev_sales, credits, quotes, expenses


def _day_balance_data(filters) -> dict:
    """Opening/closing day balances and till variance for the active period.

    Expected cash = opening + cash sales − expenses paid − suppliers paid.
    Expected M-Pesa = opening + M-Pesa sales.
    Includes currently open sessions so live cashbox can be shown.
    """
    from collections import defaultdict

    from django.utils import timezone

    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    now = timezone.now()
    empty_shop = {
        "open_sessions": 0,
        "close_sessions": 0,
        "opening_cash": _zero(),
        "opening_mpesa": _zero(),
        "opening_card": _zero(),
        "opening_credit": _zero(),
        "expected_opening_cash": _zero(),
        "expected_opening_mpesa": _zero(),
        "expected_opening_card": _zero(),
        "expected_opening_credit": _zero(),
        "opening_cash_variance": _zero(),
        "opening_mpesa_variance": _zero(),
        "opening_card_variance": _zero(),
        "opening_credit_variance": _zero(),
        "closing_cash": _zero(),
        "closing_mpesa": _zero(),
        "closing_card": _zero(),
        "closing_credit": _zero(),
        "session_open_cash": _zero(),
        "session_open_mpesa": _zero(),
        "session_open_card": _zero(),
        "cash_sales": _zero(),
        "mpesa_sales": _zero(),
        "card_sales": _zero(),
        "cash_refunds": _zero(),
        "mpesa_refunds": _zero(),
        "card_refunds": _zero(),
        "expenses": _zero(),
        "expenses_paid": _zero(),
        "drawings_paid": _zero(),
        "suppliers_paid": _zero(),
        "expected_cash": _zero(),
        "expected_mpesa": _zero(),
        "expected_card": _zero(),
        "cash_variance": _zero(),
        "mpesa_variance": _zero(),
        "card_variance": _zero(),
        "live_cash": _zero(),
        "live_mpesa": _zero(),
        "live_card": _zero(),
        "live_total": _zero(),
        "is_open": False,
    }

    by_shop: dict[int, dict] = defaultdict(lambda: dict(empty_shop))

    prior_closed = list(
        ShopDaySession.objects.filter(
            shop_id__in=shop_ids,
            closed_at__isnull=False,
            closed_at__lt=end,
        ).only(
            "shop_id",
            "closed_at",
            "closing_cash",
            "closing_mpesa",
            "closing_card",
            "closing_credit",
        )
    )
    prior_by_shop: dict[int, list] = defaultdict(list)
    for session in prior_closed:
        prior_by_shop[session.shop_id].append(session)
    for shop_id in prior_by_shop:
        prior_by_shop[shop_id].sort(key=lambda row: row.closed_at, reverse=True)

    def _prior_closed_for(session):
        for prior in prior_by_shop.get(session.shop_id, []):
            if prior.closed_at <= session.opened_at:
                return prior
        return None

    def _opening_expectations(session):
        prior = _prior_closed_for(session)
        if prior is None:
            expected_cash = _zero()
            expected_mpesa = _zero()
            expected_card = _zero()
            expected_credit = _zero()
        else:
            expected_cash = Decimal(prior.closing_cash or 0)
            expected_mpesa = Decimal(prior.closing_mpesa or 0)
            expected_card = Decimal(getattr(prior, "closing_card", None) or 0)
            expected_credit = Decimal(prior.closing_credit or 0)
        actual_cash = Decimal(session.opening_cash or 0)
        actual_mpesa = Decimal(session.opening_mpesa or 0)
        actual_card = Decimal(getattr(session, "opening_card", None) or 0)
        actual_credit = Decimal(session.opening_credit or 0)
        return {
            "expected_opening_cash": expected_cash,
            "expected_opening_mpesa": expected_mpesa,
            "expected_opening_card": expected_card,
            "expected_opening_credit": expected_credit,
            "opening_cash_variance": actual_cash - expected_cash,
            "opening_mpesa_variance": actual_mpesa - expected_mpesa,
            "opening_card_variance": actual_card - expected_card,
            "opening_credit_variance": actual_credit - expected_credit,
        }

    opened_sessions = list(
        ShopDaySession.objects.filter(
            shop_id__in=shop_ids,
            opened_at__gte=start,
            opened_at__lt=end,
        ).only(
            "id",
            "shop_id",
            "opened_at",
            "opening_cash",
            "opening_mpesa",
            "opening_card",
            "opening_credit",
        )
    )
    opening_by_session_id: dict[int, dict] = {}
    for session in opened_sessions:
        opening = _opening_expectations(session)
        opening_by_session_id[session.pk] = opening
        entry = by_shop[session.shop_id]
        entry["expected_opening_cash"] += opening["expected_opening_cash"]
        entry["expected_opening_mpesa"] += opening["expected_opening_mpesa"]
        entry["expected_opening_card"] += opening["expected_opening_card"]
        entry["expected_opening_credit"] += opening["expected_opening_credit"]
        entry["opening_cash_variance"] += opening["opening_cash_variance"]
        entry["opening_mpesa_variance"] += opening["opening_mpesa_variance"]
        entry["opening_card_variance"] += opening["opening_card_variance"]
        entry["opening_credit_variance"] += opening["opening_credit_variance"]

    for row in (
        ShopDaySession.objects.filter(
            shop_id__in=shop_ids,
            opened_at__gte=start,
            opened_at__lt=end,
        )
        .values("shop_id")
        .annotate(
            sessions=Count("id"),
            cash=Coalesce(Sum("opening_cash"), _zero()),
            mpesa=Coalesce(Sum("opening_mpesa"), _zero()),
            card=Coalesce(Sum("opening_card"), _zero()),
            credit=Coalesce(Sum("opening_credit"), _zero()),
        )
    ):
        entry = by_shop[row["shop_id"]]
        entry["open_sessions"] = int(row["sessions"] or 0)
        entry["opening_cash"] = Decimal(row["cash"] or 0)
        entry["opening_mpesa"] = Decimal(row["mpesa"] or 0)
        entry["opening_card"] = Decimal(row["card"] or 0)
        entry["opening_credit"] = Decimal(row["credit"] or 0)

    closed_sessions = list(
        ShopDaySession.objects.filter(
            shop_id__in=shop_ids,
            closed_at__isnull=False,
            closed_at__gte=start,
            closed_at__lt=end,
        ).only(
            "id",
            "shop_id",
            "opened_by_id",
            "closed_by_id",
            "opened_at",
            "closed_at",
            "opening_cash",
            "opening_mpesa",
            "opening_card",
            "opening_credit",
            "closing_cash",
            "closing_mpesa",
            "closing_card",
            "closing_credit",
        )
    )
    open_now_sessions = list(
        ShopDaySession.objects.filter(
            shop_id__in=shop_ids,
            closed_at__isnull=True,
        ).only(
            "id",
            "shop_id",
            "opened_by_id",
            "closed_by_id",
            "opened_at",
            "closed_at",
            "opening_cash",
            "opening_mpesa",
            "opening_card",
            "opening_credit",
            "closing_cash",
            "closing_mpesa",
            "closing_card",
            "closing_credit",
        )
    )
    # Ensure opening expectations exist for currently open sessions opened
    # outside the selected period (still needed for live cashbox).
    for session in open_now_sessions:
        if session.pk not in opening_by_session_id:
            opening_by_session_id[session.pk] = _opening_expectations(session)

    activity_sessions = list(closed_sessions) + list(open_now_sessions)
    sales_by_shop: dict[int, list] = defaultdict(list)
    refunds_by_shop: dict[int, list] = defaultdict(list)
    expenses_by_shop: dict[int, list] = defaultdict(list)
    suppliers_by_shop: dict[int, list] = defaultdict(list)
    credit_by_shop: dict[int, list] = defaultdict(list)
    trade_by_shop: dict[int, list] = defaultdict(list)
    if activity_sessions:
        from shops.services import (
            _collect_return_payment_events_for_shops,
            session_activity_start,
            stamp_session_activity_starts,
        )

        # Till hand-over: gap activity after the previous close belongs to
        # the next session — each window opens at that prior closed_at.
        stamp_session_activity_starts(activity_sessions)
        min_opened = min(
            session_activity_start(session) for session in activity_sessions
        )
        max_closed = max(
            (session.closed_at or now) for session in activity_sessions
        )
        # Settled credits/trades land via collection/settlement events on the
        # paid day, not the receipt's print day.
        day_sales_qs = ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            kind=ShopReceiptKind.SALE,
            settled_from_credit=False,
            settled_from_trade=False,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
        )
        day_sales_qs = _apply_analytics_receipt_status(
            day_sales_qs, filters.get("receipt_status")
        )
        for row in day_sales_qs.values(
            "shop_id",
            "created_at",
            "confirmed_payment_at",
            "cash_amount",
            "mpesa_amount",
            "card_amount",
            "return_payment_events",
        ):
            sales_by_shop[row["shop_id"]].append(row)
        from shops.services import (
            _session_credit_collections,
            _session_trade_payments,
        )

        for shop_id in shop_ids:
            credit_by_shop[shop_id].extend(
                _session_credit_collections(
                    shop_id=shop_id, start=min_opened, end=max_closed
                )
            )
            trade_by_shop[shop_id].extend(
                _session_trade_payments(
                    shop_id=shop_id, start=min_opened, end=max_closed
                )
            )
        refunds_by_shop.update(
            _collect_return_payment_events_for_shops(
                shop_ids=shop_ids, start=min_opened, end=max_closed
            )
        )
        for row in Expense.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
        ).values(
            "shop_id",
            "created_at",
            "amount_paid",
            "cash_amount",
            "mpesa_amount",
            "card_amount",
            "category",
        ):
            expenses_by_shop[row["shop_id"]].append(row)
        for row in StockMovement.objects.filter(
            shop_id__in=shop_ids,
            movement_type=StockMovementType.IN,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
            amount_paid__gt=0,
        ).values("shop_id", "created_at", "amount_paid"):
            suppliers_by_shop[row["shop_id"]].append(row)

    def _activity_for(session, *, window_end):
        from shops.services import _receipt_original_tender, session_activity_start

        window_start = session_activity_start(session)
        cash_sales = _zero()
        mpesa_sales = _zero()
        card_sales = _zero()
        for sale in sales_by_shop.get(session.shop_id, []):
            when = sale["created_at"]
            if window_start <= when < window_end:
                cash, mpesa, card = _receipt_original_tender(
                    sale.get("cash_amount"),
                    sale.get("mpesa_amount"),
                    sale.get("return_payment_events"),
                    card_amount=sale.get("card_amount"),
                )
                cash_sales += cash
                mpesa_sales += mpesa
                card_sales += card

        cash_refunds = _zero()
        mpesa_refunds = _zero()
        card_refunds = _zero()
        for event in refunds_by_shop.get(session.shop_id, []):
            when = event.get("at")
            if when is not None and window_start <= when < window_end:
                cash_refunds += Decimal(event.get("cash") or 0)
                mpesa_refunds += Decimal(event.get("mpesa") or 0)
                card_refunds += Decimal(event.get("card") or 0)

        expenses_paid = _zero()
        drawings_paid = _zero()
        drawings_cash = _zero()
        drawings_mpesa = _zero()
        drawings_card = _zero()
        for expense in expenses_by_shop.get(session.shop_id, []):
            when = expense["created_at"]
            if window_start <= when < window_end:
                paid = Decimal(expense["amount_paid"] or 0)
                if (
                    str(expense.get("category") or "").strip().lower()
                    == ExpenseCategory.OWNER_DRAWINGS
                ):
                    cash_part = Decimal(expense.get("cash_amount") or 0)
                    mpesa_part = Decimal(expense.get("mpesa_amount") or 0)
                    card_part = Decimal(expense.get("card_amount") or 0)
                    if cash_part == 0 and mpesa_part == 0 and card_part == 0:
                        cash_part = paid
                    drawings_cash += cash_part
                    drawings_mpesa += mpesa_part
                    drawings_card += card_part
                    drawings_paid += cash_part + mpesa_part + card_part
                else:
                    expenses_paid += paid

        suppliers_paid = _zero()
        for payment in suppliers_by_shop.get(session.shop_id, []):
            when = payment["created_at"]
            if window_start <= when < window_end:
                suppliers_paid += Decimal(payment["amount_paid"] or 0)

        credit_cash = _zero()
        credit_mpesa = _zero()
        credit_card = _zero()
        for row in credit_by_shop.get(session.shop_id, []):
            when = row.get("at")
            if when is None or not (window_start <= when < window_end):
                continue
            if row.get("channel"):
                amount = Decimal(row.get("amount") or 0)
                if row["channel"] == "mpesa":
                    credit_mpesa += amount
                elif row["channel"] == "card":
                    credit_card += amount
                else:
                    credit_cash += amount
            else:
                credit_cash += Decimal(row.get("cash") or 0)
                credit_mpesa += Decimal(row.get("mpesa") or 0)
                credit_card += Decimal(row.get("card") or 0)

        trade_in_cash = _zero()
        trade_in_mpesa = _zero()
        trade_in_card = _zero()
        trade_out_cash = _zero()
        trade_out_mpesa = _zero()
        trade_out_card = _zero()
        for row in trade_by_shop.get(session.shop_id, []):
            when = row.get("at")
            if when is None or not (window_start <= when < window_end):
                continue
            amount = Decimal(row.get("amount") or 0)
            channel = row.get("channel") or "cash"
            if row.get("direction") == "out":
                if channel == "mpesa":
                    trade_out_mpesa += amount
                elif channel == "card":
                    trade_out_card += amount
                else:
                    trade_out_cash += amount
            else:
                if channel == "mpesa":
                    trade_in_mpesa += amount
                elif channel == "card":
                    trade_in_card += amount
                else:
                    trade_in_cash += amount

        opening_cash = Decimal(session.opening_cash or 0)
        opening_mpesa = Decimal(session.opening_mpesa or 0)
        opening_card = Decimal(getattr(session, "opening_card", None) or 0)
        # Cleared-pending money stays outside Expected — it belongs to an
        # earlier session's sale, so it surfaces as excess on the session
        # that collected it (and a matching deficit on the printer's side).
        expected_cash = (
            opening_cash
            + cash_sales
            + credit_cash
            + trade_in_cash
            - cash_refunds
            - expenses_paid
            - drawings_cash
            - suppliers_paid
            - trade_out_cash
        )
        expected_mpesa = (
            opening_mpesa
            + mpesa_sales
            + credit_mpesa
            + trade_in_mpesa
            - mpesa_refunds
            - drawings_mpesa
            - trade_out_mpesa
        )
        expected_card = (
            opening_card
            + card_sales
            + credit_card
            + trade_in_card
            - card_refunds
            - drawings_card
            - trade_out_card
        )
        return {
            "cash_sales": cash_sales,
            "mpesa_sales": mpesa_sales,
            "card_sales": card_sales,
            "cash_refunds": cash_refunds,
            "mpesa_refunds": mpesa_refunds,
            "card_refunds": card_refunds,
            "expenses_paid": expenses_paid,
            "drawings_paid": drawings_paid,
            "drawings_cash": drawings_cash,
            "drawings_mpesa": drawings_mpesa,
            "drawings_card": drawings_card,
            "suppliers_paid": suppliers_paid,
            "expected_cash": expected_cash,
            "expected_mpesa": expected_mpesa,
            "expected_card": expected_card,
        }

    session_rows = []
    for session in closed_sessions:
        entry = by_shop[session.shop_id]
        entry["close_sessions"] += 1
        entry["closing_cash"] += Decimal(session.closing_cash or 0)
        entry["closing_mpesa"] += Decimal(session.closing_mpesa or 0)
        entry["closing_card"] += Decimal(getattr(session, "closing_card", None) or 0)
        entry["closing_credit"] += Decimal(session.closing_credit or 0)
        entry["session_open_cash"] += Decimal(session.opening_cash or 0)
        entry["session_open_mpesa"] += Decimal(session.opening_mpesa or 0)
        entry["session_open_card"] += Decimal(
            getattr(session, "opening_card", None) or 0
        )

        activity = _activity_for(session, window_end=session.closed_at)
        cash_sales = activity["cash_sales"]
        mpesa_sales = activity["mpesa_sales"]
        card_sales = activity["card_sales"]
        expenses_paid = activity["expenses_paid"]
        drawings_paid = activity["drawings_paid"]
        suppliers_paid = activity["suppliers_paid"]
        expected_cash = activity["expected_cash"]
        expected_mpesa = activity["expected_mpesa"]
        expected_card = activity["expected_card"]
        closing_cash = Decimal(session.closing_cash or 0)
        closing_mpesa = Decimal(session.closing_mpesa or 0)
        closing_card = Decimal(getattr(session, "closing_card", None) or 0)
        cash_variance = closing_cash - expected_cash
        mpesa_variance = closing_mpesa - expected_mpesa
        card_variance = closing_card - expected_card

        entry["cash_sales"] += cash_sales
        entry["mpesa_sales"] += mpesa_sales
        entry["card_sales"] += card_sales
        entry["cash_refunds"] += activity["cash_refunds"]
        entry["mpesa_refunds"] += activity["mpesa_refunds"]
        entry["card_refunds"] += activity["card_refunds"]
        entry["expenses"] += expenses_paid
        entry["expenses_paid"] += expenses_paid
        entry["drawings_paid"] += drawings_paid
        entry["suppliers_paid"] += suppliers_paid
        entry["expected_cash"] += expected_cash
        entry["expected_mpesa"] += expected_mpesa
        entry["expected_card"] += expected_card
        entry["cash_variance"] += cash_variance
        entry["mpesa_variance"] += mpesa_variance
        entry["card_variance"] += card_variance

        opening = opening_by_session_id.get(session.pk, {})
        session_rows.append(
            {
                "id": session.pk,
                "shop_id": session.shop_id,
                "is_open": False,
                "opened_by_id": session.opened_by_id,
                "closed_by_id": session.closed_by_id,
                "opened_at": session.opened_at,
                "closed_at": session.closed_at,
                "opening_cash": Decimal(session.opening_cash or 0),
                "opening_mpesa": Decimal(session.opening_mpesa or 0),
                "opening_card": Decimal(getattr(session, "opening_card", None) or 0),
                "opening_credit": Decimal(session.opening_credit or 0),
                "expected_opening_cash": opening.get(
                    "expected_opening_cash", _zero()
                ),
                "expected_opening_mpesa": opening.get(
                    "expected_opening_mpesa", _zero()
                ),
                "expected_opening_card": opening.get(
                    "expected_opening_card", _zero()
                ),
                "expected_opening_credit": opening.get(
                    "expected_opening_credit", _zero()
                ),
                "opening_cash_variance": opening.get(
                    "opening_cash_variance", _zero()
                ),
                "opening_mpesa_variance": opening.get(
                    "opening_mpesa_variance", _zero()
                ),
                "opening_card_variance": opening.get(
                    "opening_card_variance", _zero()
                ),
                "opening_credit_variance": opening.get(
                    "opening_credit_variance", _zero()
                ),
                "closing_cash": closing_cash,
                "closing_mpesa": closing_mpesa,
                "closing_card": closing_card,
                "closing_credit": Decimal(session.closing_credit or 0),
                "cash_sales": cash_sales,
                "mpesa_sales": mpesa_sales,
                "card_sales": card_sales,
                "cash_refunds": activity["cash_refunds"],
                "mpesa_refunds": activity["mpesa_refunds"],
                "card_refunds": activity["card_refunds"],
                "expenses": expenses_paid,
                "expenses_paid": expenses_paid,
                "drawings_paid": drawings_paid,
                "suppliers_paid": suppliers_paid,
                "expected_cash": expected_cash,
                "expected_mpesa": expected_mpesa,
                "expected_card": expected_card,
                "cash_variance": cash_variance,
                "mpesa_variance": mpesa_variance,
                "card_variance": card_variance,
            }
        )

    live_by_shop: dict[int, dict] = {}
    for session in open_now_sessions:
        entry = by_shop[session.shop_id]
        activity = _activity_for(session, window_end=now)
        cash_sales = activity["cash_sales"]
        mpesa_sales = activity["mpesa_sales"]
        card_sales = activity["card_sales"]
        expenses_paid = activity["expenses_paid"]
        drawings_paid = activity["drawings_paid"]
        suppliers_paid = activity["suppliers_paid"]
        expected_cash = activity["expected_cash"]
        expected_mpesa = activity["expected_mpesa"]
        expected_card = activity["expected_card"]
        opening_cash = Decimal(session.opening_cash or 0)
        opening_mpesa = Decimal(session.opening_mpesa or 0)
        opening_card = Decimal(getattr(session, "opening_card", None) or 0)
        opening_credit = Decimal(session.opening_credit or 0)
        live_total = expected_cash + expected_mpesa + expected_card

        entry["is_open"] = True
        entry["live_cash"] = expected_cash
        entry["live_mpesa"] = expected_mpesa
        entry["live_card"] = expected_card
        entry["live_total"] = live_total

        live_by_shop[session.shop_id] = {
            "is_open": True,
            "opened_at": session.opened_at,
            "opening_cash": opening_cash,
            "opening_mpesa": opening_mpesa,
            "opening_card": opening_card,
            "opening_credit": opening_credit,
            "cash_sales": cash_sales,
            "mpesa_sales": mpesa_sales,
            "card_sales": card_sales,
            "cash_refunds": activity["cash_refunds"],
            "mpesa_refunds": activity["mpesa_refunds"],
            "card_refunds": activity["card_refunds"],
            "expenses_paid": expenses_paid,
            "drawings_paid": drawings_paid,
            "suppliers_paid": suppliers_paid,
            "live_cash": expected_cash,
            "live_mpesa": expected_mpesa,
            "live_card": expected_card,
            "live_total": live_total,
            "closing_cash": None,
            "closing_mpesa": None,
            "closing_card": None,
        }

        opening = opening_by_session_id.get(session.pk, {})
        session_rows.append(
            {
                "id": session.pk,
                "shop_id": session.shop_id,
                "is_open": True,
                "opened_by_id": session.opened_by_id,
                "closed_by_id": session.closed_by_id,
                "opened_at": session.opened_at,
                "closed_at": None,
                "opening_cash": opening_cash,
                "opening_mpesa": opening_mpesa,
                "opening_card": opening_card,
                "opening_credit": opening_credit,
                "expected_opening_cash": opening.get(
                    "expected_opening_cash", _zero()
                ),
                "expected_opening_mpesa": opening.get(
                    "expected_opening_mpesa", _zero()
                ),
                "expected_opening_card": opening.get(
                    "expected_opening_card", _zero()
                ),
                "expected_opening_credit": opening.get(
                    "expected_opening_credit", _zero()
                ),
                "opening_cash_variance": opening.get(
                    "opening_cash_variance", _zero()
                ),
                "opening_mpesa_variance": opening.get(
                    "opening_mpesa_variance", _zero()
                ),
                "opening_card_variance": opening.get(
                    "opening_card_variance", _zero()
                ),
                "opening_credit_variance": opening.get(
                    "opening_credit_variance", _zero()
                ),
                "closing_cash": _zero(),
                "closing_mpesa": _zero(),
                "closing_card": _zero(),
                "closing_credit": _zero(),
                "cash_sales": cash_sales,
                "mpesa_sales": mpesa_sales,
                "card_sales": card_sales,
                "cash_refunds": activity["cash_refunds"],
                "mpesa_refunds": activity["mpesa_refunds"],
                "card_refunds": activity["card_refunds"],
                "expenses": expenses_paid,
                "expenses_paid": expenses_paid,
                "drawings_paid": drawings_paid,
                "suppliers_paid": suppliers_paid,
                "expected_cash": expected_cash,
                "expected_mpesa": expected_mpesa,
                "expected_card": expected_card,
                "cash_variance": _zero(),
                "mpesa_variance": _zero(),
                "card_variance": _zero(),
            }
        )

    # Closed shops: live cashbox = last recorded closing balances.
    last_closed_by_shop: dict[int, object] = {}
    for prior in prior_closed:
        current = last_closed_by_shop.get(prior.shop_id)
        if current is None or prior.closed_at > current.closed_at:
            last_closed_by_shop[prior.shop_id] = prior
    for shop_id, prior in last_closed_by_shop.items():
        if shop_id in live_by_shop:
            continue
        closing_cash = Decimal(prior.closing_cash or 0)
        closing_mpesa = Decimal(prior.closing_mpesa or 0)
        closing_card = Decimal(getattr(prior, "closing_card", None) or 0)
        last_total = closing_cash + closing_mpesa + closing_card
        entry = by_shop[shop_id]
        entry["live_cash"] = closing_cash
        entry["live_mpesa"] = closing_mpesa
        entry["live_card"] = closing_card
        entry["live_total"] = last_total
        live_by_shop[shop_id] = {
            "is_open": False,
            "opened_at": None,
            "opening_cash": _zero(),
            "opening_mpesa": _zero(),
            "opening_card": _zero(),
            "opening_credit": _zero(),
            "cash_sales": _zero(),
            "mpesa_sales": _zero(),
            "card_sales": _zero(),
            "expenses_paid": _zero(),
            "drawings_paid": _zero(),
            "suppliers_paid": _zero(),
            "live_cash": closing_cash,
            "live_mpesa": closing_mpesa,
            "live_card": closing_card,
            "live_total": last_total,
            "closing_cash": closing_cash,
            "closing_mpesa": closing_mpesa,
            "closing_card": closing_card,
            "closed_at": prior.closed_at,
        }

    totals = dict(empty_shop)
    for entry in by_shop.values():
        for key, value in entry.items():
            if key == "is_open":
                totals["is_open"] = totals["is_open"] or bool(value)
                continue
            totals[key] += value

    return {
        "by_shop": dict(by_shop),
        "totals": totals,
        "session_rows": session_rows,
        "live_by_shop": live_by_shop,
    }


def _amount_map_from_balances(
    by_shop: dict[int, dict], key: str
) -> dict[int, tuple[int, Decimal]]:
    """Map shop → (session count, amount) for day-balance fields."""
    result: dict[int, tuple[int, Decimal]] = {}
    for shop_id, entry in by_shop.items():
        amount = Decimal(entry.get(key) or 0)
        if key.startswith("closing") or key.startswith("opening_") or key.startswith("expected_opening") or key in {
            "cash_variance",
            "mpesa_variance",
            "card_variance",
            "expected_cash",
            "expected_mpesa",
            "expected_card",
            "session_open_cash",
            "session_open_mpesa",
            "session_open_card",
            "cash_sales",
            "mpesa_sales",
            "card_sales",
            "expenses",
            "expenses_paid",
            "drawings_paid",
            "suppliers_paid",
            "live_cash",
            "live_mpesa",
            "live_card",
            "live_total",
        }:
            sessions = int(entry.get("close_sessions") or 0)
        else:
            sessions = int(entry.get("open_sessions") or 0)
        if sessions or amount:
            result[shop_id] = (sessions, amount)
    return result


def _variance_tone(value) -> str:
    amount = Decimal(value or 0)
    if amount == 0:
        return "good"
    return "bad"


def _build_overview(filters):
    """Raw company totals for the period, then a per-shop breakdown."""
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    flags = filters.get("pos_flags") or _pos_feature_flags()
    sales, _prev_sales, credits, _quotes, expenses = _common_receipt_sets(filters)

    sale_docs_by_shop: dict[int, int] = {
        row["shop_id"]: int(row["docs"] or 0)
        for row in sales.values("shop_id").annotate(docs=Count("id"))
    }
    credit_docs_by_shop: dict[int, int] = (
        {
            row["shop_id"]: int(row["docs"] or 0)
            for row in credits.values("shop_id").annotate(docs=Count("id"))
        }
        if flags["credit"]
        else {}
    )
    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        receipt_status=filters.get("receipt_status"),
    )
    opex_by_shop, drawings_by_shop = _opex_and_drawings_by_shop(expenses)
    shrinkage_by_shop = _shrinkage_by_shop_for_period(
        shop_ids=shop_ids, start=start, end=end
    )

    def _pct(part, whole) -> Decimal:
        return (
            ((part / whole) * Decimal("100")).quantize(Decimal("0.1"))
            if whole > 0
            else _zero()
        )

    rows_by_shop: dict[int, dict] = {}
    total_sale_docs = total_credit_docs = total_expense_docs = 0
    total_sales = total_credits = _zero()
    total_stock = total_expenses = _zero()
    for shop in shops:
        row = trading.get(shop.pk) or {}
        sale_amt = Decimal(row.get("sale_value") or 0)
        credit_amt = (
            Decimal(row.get("credit_value") or 0) if flags["credit"] else _zero()
        )
        stock = Decimal(row.get("cogs") or 0)
        opex_docs, opex_amt = opex_by_shop.get(shop.pk, (0, _zero()))
        opex_docs = int(opex_docs or 0)
        opex_amt = Decimal(opex_amt or 0)
        revenue = sale_amt + credit_amt
        gross = revenue - stock
        profit = gross - opex_amt
        sale_docs = sale_docs_by_shop.get(shop.pk, 0)
        credit_docs = credit_docs_by_shop.get(shop.pk, 0)
        rows_by_shop[shop.pk] = {
            "sale_docs": sale_docs,
            "sale_amt": sale_amt,
            "credit_docs": credit_docs,
            "credit_amt": credit_amt,
            "docs": sale_docs + credit_docs,
            "revenue": revenue,
            "stock": stock,
            "gross": gross,
            "gross_pct": _pct(gross, revenue),
            "expense_docs": opex_docs,
            "expenses": opex_amt,
            "profit": profit,
            "margin": _pct(profit, revenue),
        }
        total_sale_docs += sale_docs
        total_credit_docs += credit_docs
        total_expense_docs += opex_docs
        total_sales += sale_amt
        total_credits += credit_amt
        total_stock += stock
        total_expenses += opex_amt

    total_docs = total_sale_docs + total_credit_docs
    total_revenue = total_sales + total_credits
    total_gross = total_revenue - total_stock
    total_profit = total_gross - total_expenses
    total_gross_pct = _pct(total_gross, total_revenue)
    total_margin = _pct(total_profit, total_revenue)
    total_shrinkage = sum(
        (amt for _docs, amt in shrinkage_by_shop.values()), _zero()
    )
    total_drawings = sum(
        (amt for _docs, amt in drawings_by_shop.values()), _zero()
    )

    shop_columns = ["Shop"]
    for label, is_total, band, band_start in (
        ("Sales", False, "flow", True),
        ("Credits", False, "flow", False),
        ("Total revenue", True, "total", True),
    ):
        shop_columns.append(
            {
                "label": label,
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "total": is_total,
                "band": band,
                "band_start": band_start,
            }
        )
    shop_columns.extend(
        [
            {
                "label": "Stock sold",
                "title": "Buying cost of items sold",
                "band": "result",
                "band_start": True,
            },
            {
                "label": "Gross margin",
                "title": "Total revenue − stock value sold",
                "band": "result",
            },
            {
                "label": "Expenses",
                "title": "Operating expenses recorded this period (excludes owner drawings)",
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "band": "result",
            },
            {
                "label": "Profit",
                "title": "Gross margin − expenses for this period",
                "band": "result",
            },
            {
                "label": "Margin",
                "title": "Profit as a share of total revenue",
                "band": "result",
            },
        ]
    )

    def _shop_cells(name, data):
        return [
            name,
            _qty_amount_cell(
                data["sale_docs"],
                data["sale_amt"],
                title=f"{data['sale_docs']} · {_money_ksh(data['sale_amt'])}",
            ),
            _qty_amount_cell(
                data["credit_docs"],
                data["credit_amt"],
                title=f"{data['credit_docs']} · {_money_ksh(data['credit_amt'])}",
            ),
            _qty_amount_cell(
                data["docs"],
                data["revenue"],
                title=f"{data['docs']} · {_money_ksh(data['revenue'])}",
            ),
            _money_cell(
                data["stock"],
                title=f"Stock value sold {_money_ksh(data['stock'])}",
            ),
            _money_cell(
                data["gross"],
                tone="good" if data["gross"] >= 0 else "bad",
                title=f"Gross margin {_money_ksh(data['gross'])} · {data['gross_pct']}% of revenue",
            ),
            (
                _qty_amount_cell(
                    data["expense_docs"],
                    data["expenses"],
                    title=f"{data['expense_docs']} expenses · {_money_ksh(data['expenses'])}",
                    tone="warn" if data["expenses"] > 0 else "neutral",
                )
                if data["expense_docs"] or data["expenses"]
                else "—"
            ),
            _money_cell(
                data["profit"],
                tone="good" if data["profit"] >= 0 else "bad",
                title=f"Profit {_money_ksh(data['profit'])} · margin {data['margin']}%",
            ),
            _pct_cell(
                data["margin"],
                tone="good" if data["profit"] >= 0 else "bad",
                title=f"Profit margin {data['margin']}%",
            ),
        ]

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -rows_by_shop.get(shop.pk, {}).get("revenue", _zero()),
            shop.name.lower(),
        ),
    )
    shop_rows = [
        _shop_cells(shop.name, rows_by_shop[shop.pk])
        for shop in shops_sorted
        if shop.pk in rows_by_shop
    ]
    if shop_rows:
        shop_rows.append(
            _shop_cells(
                "All shops",
                {
                    "sale_docs": total_sale_docs,
                    "sale_amt": total_sales,
                    "credit_docs": total_credit_docs,
                    "credit_amt": total_credits,
                    "docs": total_docs,
                    "revenue": total_revenue,
                    "stock": total_stock,
                    "gross": total_gross,
                    "gross_pct": total_gross_pct,
                    "expense_docs": total_expense_docs,
                    "expenses": total_expenses,
                    "profit": total_profit,
                    "margin": total_margin,
                },
            )
        )

    employee_perf = _employee_performance_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        receipt_status=filters.get("receipt_status"),
    )
    emp_columns = ["Employee"]
    for label, is_total, band, band_start in (
        ("Sales", False, "flow", True),
        ("Credits", False, "flow", False),
        ("Total revenue", True, "total", True),
    ):
        emp_columns.append(
            {
                "label": label,
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "total": is_total,
                "band": band,
                "band_start": band_start,
            }
        )
    emp_columns.extend(
        [
            {
                "label": "Stock sold",
                "title": "Buying cost of items sold",
                "band": "result",
                "band_start": True,
            },
            {
                "label": "Gross margin",
                "title": "Total revenue − stock value sold",
                "band": "result",
            },
            {
                "label": "Margin",
                "title": "Gross margin as a share of total revenue",
                "band": "result",
            },
        ]
    )

    def _emp_cells(name, data):
        return [
            name,
            _qty_amount_cell(
                data["sale_docs"],
                data["sale_amt"],
                title=f"{data['sale_docs']} · {_money_ksh(data['sale_amt'])}",
            ),
            _qty_amount_cell(
                data["credit_docs"],
                data["credit_amt"],
                title=f"{data['credit_docs']} · {_money_ksh(data['credit_amt'])}",
            ),
            _qty_amount_cell(
                data["docs"],
                data["revenue"],
                title=f"{data['docs']} · {_money_ksh(data['revenue'])}",
            ),
            _money_cell(
                data["stock"],
                title=f"Stock value sold {_money_ksh(data['stock'])}",
            ),
            _money_cell(
                data["gross"],
                tone="good" if data["gross"] >= 0 else "bad",
                title=f"Gross margin {_money_ksh(data['gross'])} · {data['margin']}% of revenue",
            ),
            _pct_cell(
                data["margin"],
                tone="good" if data["gross"] >= 0 else "bad",
                title=f"Gross margin {data['margin']}%",
            ),
        ]

    def _emp_data(entry):
        sale_amt = Decimal(entry["sale_value"] or 0)
        credit_amt = (
            Decimal(entry["credit_value"] or 0) if flags["credit"] else _zero()
        )
        sale_docs = int(entry["sale_docs"] or 0)
        credit_docs = (
            int(entry["credit_docs"] or 0) if flags["credit"] else 0
        )
        revenue = sale_amt + credit_amt
        stock = Decimal(entry["cogs"] or 0)
        gross = revenue - stock
        return {
            "sale_docs": sale_docs,
            "sale_amt": sale_amt,
            "credit_docs": credit_docs,
            "credit_amt": credit_amt,
            "docs": sale_docs + credit_docs,
            "revenue": revenue,
            "stock": stock,
            "gross": gross,
            "margin": _pct(gross, revenue),
        }

    emp_sorted = sorted(
        employee_perf.values(),
        key=lambda row: (
            -(
                Decimal(row["sale_value"] or 0)
                + Decimal(row["credit_value"] or 0)
            ),
            row["label"].lower(),
        ),
    )
    emp_rows = [
        _emp_cells(entry["label"], _emp_data(entry)) for entry in emp_sorted
    ]
    if emp_rows:
        emp_rows.append(
            _emp_cells(
                "All employees",
                _emp_data(
                    {
                        "sale_value": total_sales,
                        "credit_value": total_credits,
                        "cogs": total_stock,
                        "sale_docs": total_sale_docs,
                        "credit_docs": total_credit_docs,
                    }
                ),
            )
        )

    alerts = []
    if total_shrinkage > 0:
        alerts.append(
            _alert(
                "warning",
                "Shrinkage in period",
                f"Waste/display stock-outs cost {_money_ksh(total_shrinkage)} — "
                "not deducted from the profit figures above.",
                "Review stock-out reasons on the Stock page.",
            )
        )
    if total_drawings > 0:
        alerts.append(
            _alert(
                "info",
                "Owner drawings",
                f"{_money_ksh(total_drawings)} recorded — equity, not operating expense.",
            )
        )

    page = {
        "headline": "Company overview",
        "lead": (
            "Raw figures for the selected period. Sales and credits are ex-tax "
            "item value, net of returns. Stock value sold is the buying cost of "
            "those items. Gross margin = revenue − stock. Profit = gross margin "
            "− expenses (owner drawings excluded)."
        ),
        "alerts": alerts,
        "metrics": _overview_metric_cards(
            total_sale_docs=total_sale_docs,
            total_sales=total_sales,
            total_credit_docs=total_credit_docs,
            total_credits=total_credits,
            total_docs=total_docs,
            total_revenue=total_revenue,
            total_stock=total_stock,
            total_gross=total_gross,
            total_gross_pct=total_gross_pct,
            total_expense_docs=total_expense_docs,
            total_expenses=total_expenses,
            total_profit=total_profit,
            total_margin=total_margin,
        ),
        "trends": [],
        "charts": [],
        "sections": [],
        "insights": [],
        "tables": [
            _table(
                "Individual shops",
                shop_columns,
                shop_rows,
                empty="No shop activity for the selected period.",
                shop_grid=True,
                footnote=(
                    "All figures are for the selected period. "
                    "Sales and credits are ex-tax item value, net of returns. "
                    "Stock sold is the buying cost of those items. "
                    "Expenses exclude owner drawings. "
                    "Profit = total revenue − stock − expenses."
                ),
            ),
            _table(
                "Employee performance",
                emp_columns,
                emp_rows,
                empty="No employee sales for the selected period.",
                shop_grid=True,
                footnote=(
                    "Same period and netting as the shop rollup — sales and "
                    "credits are ex-tax item value, stock sold is the buying "
                    "cost of those items. Expenses are not attributed to "
                    "employees."
                ),
            ),
        ],
    }
    breakdown = _revenue_breakdown_table(
        filters=filters,
        shop_ids=shop_ids,
        start=start,
        end=end,
        sales=sales,
        credits=credits,
    )
    if breakdown is not None:
        page["tables"].append(breakdown)
    return page


def _metric_shop_columns(shops, *, pair_qty: str = "Docs", pair_amt: str = "Amt") -> list:
    columns = ["Metric"]
    for shop in shops:
        columns.append(
            _shop_col(shop, pair=True, pair_qty=pair_qty, pair_amt=pair_amt)
        )
    columns.append(_pair_total_col(pair_qty=pair_qty, pair_amt=pair_amt))
    return columns


def _metric_shop_rows(
    shops,
    specs: list[tuple[str, dict[int, tuple[int, Decimal]]]],
) -> list:
    """Build rows of (label, per-shop qty/amount map) into pair cells + totals."""
    rows = []
    for label, by_shop in specs:
        total_qty = 0
        total_amount = _zero()
        cells = [label]
        for shop in shops:
            qty, amount = by_shop.get(shop.pk, (0, _zero()))
            total_qty += Decimal(qty or 0)
            total_amount += Decimal(amount or 0)
            cells.append(
                _qty_amount_cell(
                    qty,
                    amount,
                    title=f"{_qty_label(qty)} · {_money_ksh(amount)}",
                )
            )
        cells.append(
            _qty_amount_cell(
                total_qty,
                total_amount,
                title=f"{_qty_label(total_qty)} · {_money_ksh(total_amount)}",
            )
        )
        rows.append(cells)
    return rows


def _period_breakdown_config(filters) -> dict | None:
    """Pick hour / day / month buckets from the active analytics range filter."""
    from datetime import timedelta

    range_type = (
        filters.get("range_type") or filters.get("report_range") or "day"
    ).strip().lower()
    if range_type == "day":
        return {
            "bucket_kind": "hour",
            "column_label": "Hour",
            "title": "Hourly transactions",
            "empty": "No hourly transactions for this day.",
        }
    if range_type == "month":
        return {
            "bucket_kind": "day",
            "column_label": "Day",
            "title": "Daily transactions",
            "empty": "No daily transactions for this month.",
        }
    if range_type == "year":
        return {
            "bucket_kind": "month",
            "column_label": "Month",
            "title": "Monthly transactions",
            "empty": "No monthly transactions for this year.",
        }

    start, end = filters.get("start"), filters.get("end")
    if not start or not end:
        return None
    duration = end - start
    if duration <= timedelta(hours=36):
        return {
            "bucket_kind": "hour",
            "column_label": "Hour",
            "title": "Hourly transactions",
            "empty": "No hourly transactions for this period.",
        }
    if duration <= timedelta(days=62):
        return {
            "bucket_kind": "day",
            "column_label": "Day",
            "title": "Daily transactions",
            "empty": "No daily transactions for this period.",
        }
    return {
        "bucket_kind": "month",
        "column_label": "Month",
        "title": "Monthly transactions",
        "empty": "No monthly transactions for this period.",
    }


def _local_utc_offset() -> timedelta:
    """Current TIME_ZONE offset from UTC (Africa/Nairobi is fixed +03, no DST)."""
    tz = timezone.get_current_timezone()
    return tz.utcoffset(timezone.now()) or timedelta(0)


def _created_at_trunc(field: str, bucket_kind: str):
    """
    Truncate timestamps in local wall time without MySQL CONVERT_TZ.

    cPanel MySQL often has empty timezone tables, so CONVERT_TZ returns NULL
    for both named zones (Africa/Nairobi) and even CONVERT_TZ(..., 'UTC', ...)
    because 'UTC' is also a named zone. Shift with INTERVAL, then Trunc with
    tzinfo=UTC so Django skips CONVERT_TZ entirely.
    """
    shifted = ExpressionWrapper(
        F(field) + _local_utc_offset(),
        output_field=DateTimeField(),
    )
    # Match connection.timezone_name (UTC) → no CONVERT_TZ in SQL.
    utc = dt_timezone.utc
    if bucket_kind == "hour":
        return TruncHour(shifted, tzinfo=utc)
    if bucket_kind == "day":
        return TruncDay(shifted, tzinfo=utc)
    return TruncMonth(shifted, tzinfo=utc)


def _receipt_revenue_moment(prefix: str = ""):
    """Moment a receipt's revenue belongs to.

    ``confirmed_payment_at`` on settled-from-credit sales (the day the credit
    cleared), ``created_at`` everywhere else. ``prefix`` is the relation path
    when reached through a join (e.g. ``receipt`` on line querysets).
    """
    p = f"{prefix}__" if prefix else ""
    return Case(
        When(
            **{
                f"{p}settled_from_credit": True,
                f"{p}confirmed_payment_at__isnull": False,
            },
            then=F(f"{p}confirmed_payment_at"),
        ),
        default=F(f"{p}created_at"),
        output_field=DateTimeField(),
    )


def _revenue_moment_trunc(bucket_kind: str, *, prefix: str = ""):
    """Bucket receipts by revenue day — paid day for settled credit sales."""
    shifted = ExpressionWrapper(
        _receipt_revenue_moment(prefix) + _local_utc_offset(),
        output_field=DateTimeField(),
    )
    utc = dt_timezone.utc
    if bucket_kind == "hour":
        return TruncHour(shifted, tzinfo=utc)
    if bucket_kind == "day":
        return TruncDay(shifted, tzinfo=utc)
    return TruncMonth(shifted, tzinfo=utc)


def _bucket_label(moment, bucket_kind: str) -> str:
    if moment is None:
        return ""
    # SQL buckets are local wall time encoded as UTC (see _created_at_trunc).
    # Python-built labels from _iter_bucket_labels are real local datetimes.
    if timezone.is_aware(moment) and moment.utcoffset() == timedelta(0):
        wall = moment.replace(tzinfo=None)
    else:
        wall = timezone.localtime(moment)
        if timezone.is_aware(wall):
            wall = wall.replace(tzinfo=None)
    if bucket_kind == "hour":
        return wall.replace(minute=0, second=0, microsecond=0).strftime("%H:00")
    if bucket_kind == "day":
        return wall.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%d %b")
    return wall.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime(
        "%b %Y"
    )


def _iter_bucket_labels(start, end, bucket_kind: str) -> list[str]:
    from datetime import timedelta

    if not start or not end:
        return []
    cursor = timezone.localtime(start)
    end_local = timezone.localtime(end)
    labels: list[str] = []
    if bucket_kind == "hour":
        cursor = cursor.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)
        while cursor < end_local:
            labels.append(_bucket_label(cursor, bucket_kind))
            cursor = cursor + step
    elif bucket_kind == "day":
        cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)
        while cursor < end_local:
            labels.append(_bucket_label(cursor, bucket_kind))
            cursor = cursor + step
    else:
        cursor = cursor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cursor < end_local:
            labels.append(_bucket_label(cursor, bucket_kind))
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = cursor.replace(year=year, month=month)
    return labels


def _trading_by_bucket_for_period(
    *, shop_ids, start, end, bucket_kind: str, kinds=None, receipt_status=None
) -> dict[str, dict]:
    """Net selling value and COGS keyed by time-bucket label."""
    kinds = list(kinds or [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT])
    trading_line_qs = _trading_line_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=kinds,
        receipt_status=receipt_status,
    )
    empty = {
        "lines": 0,
        "value": _zero(),
        "cogs": _zero(),
        "sale_value": _zero(),
        "credit_value": _zero(),
        "sale_cogs": _zero(),
        "credit_cogs": _zero(),
    }
    by_bucket: dict[str, dict] = {}

    def _entry(label: str) -> dict:
        row = by_bucket.get(label)
        if row is None:
            row = dict(empty)
            by_bucket[label] = row
        return row

    remaining_value = ExpressionWrapper(
        F("unit_price") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    remaining_cogs = ExpressionWrapper(
        F("unit_cost") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    trunc = _revenue_moment_trunc(bucket_kind, prefix="receipt")
    for row in (
        trading_line_qs.annotate(bucket=trunc)
        .values("bucket", "receipt__kind")
        .annotate(
            lines=Count("id"),
            value=Coalesce(Sum(remaining_value), _zero()),
            cogs=Coalesce(Sum(remaining_cogs), _zero()),
        )
    ):
        label = _bucket_label(row["bucket"], bucket_kind)
        if not label:
            continue
        kind = row["receipt__kind"]
        value = Decimal(row["value"] or 0).quantize(Decimal("0.01"))
        cogs = Decimal(row["cogs"] or 0).quantize(Decimal("0.01"))
        lines = int(row["lines"] or 0)
        entry = _entry(label)
        entry["lines"] += lines
        entry["value"] += value
        entry["cogs"] += cogs
        if kind == ShopReceiptKind.SALE:
            entry["sale_value"] += value
            entry["sale_cogs"] += cogs
        elif kind == ShopReceiptKind.CREDIT:
            entry["credit_value"] += value
            entry["credit_cogs"] += cogs

    missing_cost_lines = list(
        trading_line_qs.filter(unit_cost=0)
        .exclude(item_id__isnull=True)
        .annotate(bucket=trunc)
        .values(
            "bucket",
            "receipt__kind",
            "item_id",
            "quantity",
            "returned_quantity",
        )
    )
    if missing_cost_lines:
        from items.services import last_buying_prices_for_items

        item_ids = {row["item_id"] for row in missing_cost_lines if row["item_id"]}
        fallback_prices = last_buying_prices_for_items(item_ids)
        for row in missing_cost_lines:
            item_id = row["item_id"]
            unit = Decimal(fallback_prices.get(item_id) or 0)
            if unit <= 0:
                continue
            remaining = max(
                Decimal("0"),
                Decimal(row["quantity"] or 0)
                - Decimal(row["returned_quantity"] or 0),
            )
            if remaining <= 0:
                continue
            label = _bucket_label(row["bucket"], bucket_kind)
            if not label:
                continue
            extra = (unit * remaining).quantize(Decimal("0.01"))
            entry = _entry(label)
            entry["cogs"] += extra
            if row["receipt__kind"] == ShopReceiptKind.SALE:
                entry["sale_cogs"] += extra
            elif row["receipt__kind"] == ShopReceiptKind.CREDIT:
                entry["credit_cogs"] += extra
    return by_bucket


def _opex_and_drawings_by_bucket(
    expenses_qs, *, bucket_kind: str
) -> tuple[dict[str, tuple[int, Decimal]], dict[str, tuple[int, Decimal]]]:
    opex_by_bucket: dict[str, tuple[int, Decimal]] = {}
    drawings_by_bucket: dict[str, tuple[int, Decimal]] = {}
    trunc = _created_at_trunc("created_at", bucket_kind)
    for row in expenses_qs.annotate(bucket=trunc).values("bucket", "category").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("amount"), _zero()),
    ):
        label = _bucket_label(row["bucket"], bucket_kind)
        if not label:
            continue
        amount = Decimal(row["amount"] or 0)
        docs = int(row["docs"] or 0)
        if row["category"] == ExpenseCategory.OWNER_DRAWINGS:
            d_docs, d_amt = drawings_by_bucket.get(label, (0, _zero()))
            drawings_by_bucket[label] = (d_docs + docs, d_amt + amount)
        else:
            o_docs, o_amt = opex_by_bucket.get(label, (0, _zero()))
            opex_by_bucket[label] = (o_docs + docs, o_amt + amount)
    return opex_by_bucket, drawings_by_bucket


def _docs_by_bucket(qs, *, bucket_kind: str) -> dict[str, int]:
    trunc = _revenue_moment_trunc(bucket_kind)
    return {
        _bucket_label(row["bucket"], bucket_kind): int(row["docs"] or 0)
        for row in qs.annotate(bucket=trunc)
        .values("bucket")
        .annotate(docs=Count("id"))
        if row["bucket"] is not None
    }


def _revenue_breakdown_table(
    *,
    filters,
    shop_ids,
    start,
    end,
    sales,
    credits,
) -> dict | None:
    config = _period_breakdown_config(filters)
    if not config or not start or not end:
        return None

    bucket_kind = config["bucket_kind"]
    labels = _iter_bucket_labels(start, end, bucket_kind)
    if not labels:
        return None

    trading = _trading_by_bucket_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        bucket_kind=bucket_kind,
        receipt_status=filters.get("receipt_status"),
    )
    period_expenses = _within_created_range(
        Expense.objects.filter(shop_id__in=shop_ids),
        start,
        end,
    )
    opex_by_bucket, _drawings = _opex_and_drawings_by_bucket(
        period_expenses, bucket_kind=bucket_kind
    )
    sale_docs_by_bucket = _docs_by_bucket(sales, bucket_kind=bucket_kind)
    flags = filters.get("pos_flags") or _pos_feature_flags()
    credit_docs_by_bucket = (
        _docs_by_bucket(credits, bucket_kind=bucket_kind) if flags["credit"] else {}
    )

    metric_maps = [
        ("Sales", "sale"),
        ("Credits", "credit"),
        ("Total", "total"),
    ]
    columns = [config["column_label"]]
    for label, key in metric_maps:
        columns.append(
            {
                "label": label,
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "total": key == "total",
                "band": "total" if key == "total" else "flow",
                "band_start": key in {"sale", "total"},
            }
        )
    columns.extend(
        [
            {
                "label": "Stock",
                "title": "Buying cost of items sold",
                "band": "result",
                "band_start": True,
            },
            {
                "label": "Expenses",
                "title": "Operating expenses recorded in this bucket (excludes owner drawings)",
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "band": "result",
            },
            {
                "label": "Profit",
                "title": "Total − stock − expenses for this bucket",
                "band": "result",
            },
        ]
    )

    table_rows = []
    for label in labels:
        row = trading.get(label) or {}
        sale_docs = sale_docs_by_bucket.get(label, 0)
        credit_docs = credit_docs_by_bucket.get(label, 0)
        sale_amt = Decimal(row.get("sale_value") or 0)
        credit_amt = (
            Decimal(row.get("credit_value") or 0) if flags["credit"] else _zero()
        )
        stock = Decimal(row.get("cogs") or 0)
        opex_docs, opex_amt = opex_by_bucket.get(label, (0, _zero()))
        opex_amt = Decimal(opex_amt or 0)
        revenue_amt = sale_amt + credit_amt
        total_docs = sale_docs + credit_docs
        if not total_docs and not opex_docs and revenue_amt == 0 and stock == 0:
            continue
        profit = revenue_amt - stock - opex_amt
        margin = (
            ((profit / revenue_amt) * Decimal("100")).quantize(Decimal("0.1"))
            if revenue_amt > 0
            else _zero()
        )
        cells = [
            label,
            _qty_amount_cell(
                sale_docs,
                sale_amt,
                title=f"{sale_docs} · {_money_ksh(sale_amt)}",
            ),
            _qty_amount_cell(
                credit_docs,
                credit_amt,
                title=f"{credit_docs} · {_money_ksh(credit_amt)}",
            ),
            _qty_amount_cell(
                total_docs,
                revenue_amt,
                title=f"{total_docs} · {_money_ksh(revenue_amt)}",
            ),
            _money_cell(stock, title=f"Stock value {_money_ksh(stock)}"),
            (
                _qty_amount_cell(
                    opex_docs,
                    opex_amt,
                    title=f"{int(opex_docs or 0)} expenses · {_money_ksh(opex_amt)}",
                    tone="warn" if opex_amt > 0 else "neutral",
                )
                if opex_docs or opex_amt
                else "—"
            ),
            _money_cell(
                profit,
                tone="good" if profit >= 0 else "bad",
                title=f"Profit {_money_ksh(profit)} · margin {margin}%",
            ),
        ]
        table_rows.append(cells)

    return _table(
        config["title"],
        columns,
        table_rows,
        empty=config["empty"],
        shop_grid=True,
        footnote=(
            f"{config['title']} for the selected range. "
            "Same rules as revenue by shop: sales and credits are ex-tax item value, "
            "stock is buying cost, expenses exclude owner drawings, "
            "profit = total − stock − expenses."
        ),
    )


def _build_revenue(filters):
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(filters["active_shop_ids"])]
    shop_ids = [shop.pk for shop in shops]
    start, end = filters["start"], filters["end"]
    flags = filters.get("pos_flags") or _pos_feature_flags()
    sales, _prev_sales, credits, _quotes, _expenses = _common_receipt_sets(filters)

    sale_docs_by_shop: dict[int, int] = {
        row["shop_id"]: int(row["docs"] or 0)
        for row in sales.values("shop_id").annotate(docs=Count("id"))
    }
    credit_docs_by_shop: dict[int, int] = (
        {
            row["shop_id"]: int(row["docs"] or 0)
            for row in credits.values("shop_id").annotate(docs=Count("id"))
        }
        if flags["credit"]
        else {}
    )
    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        receipt_status=filters.get("receipt_status"),
    )

    # Operating expenses for this period (owner drawings stay out of P&L).
    period_expenses = _within_created_range(
        Expense.objects.filter(shop_id__in=shop_ids),
        start,
        end,
    )
    opex_by_shop, drawings_by_shop = _opex_and_drawings_by_shop(period_expenses)

    sales_by_shop: dict[int, tuple[int, Decimal]] = {}
    credits_by_shop: dict[int, tuple[int, Decimal]] = {}
    total_by_shop: dict[int, tuple[int, Decimal]] = {}
    stock_by_shop: dict[int, Decimal] = {}
    expenses_by_shop: dict[int, tuple[int, Decimal]] = {}
    profit_by_shop: dict[int, Decimal] = {}
    for shop in shops:
        row = trading.get(shop.pk) or {}
        sale_docs = sale_docs_by_shop.get(shop.pk, 0)
        credit_docs = credit_docs_by_shop.get(shop.pk, 0)
        sale_amt = Decimal(row.get("sale_value") or 0)
        credit_amt = (
            Decimal(row.get("credit_value") or 0) if flags["credit"] else _zero()
        )
        stock = Decimal(row.get("cogs") or 0)
        opex_docs, opex_amt = opex_by_shop.get(shop.pk, (0, _zero()))
        opex_amt = Decimal(opex_amt or 0)
        revenue_amt = sale_amt + credit_amt
        sales_by_shop[shop.pk] = (sale_docs, sale_amt)
        credits_by_shop[shop.pk] = (credit_docs, credit_amt)
        total_by_shop[shop.pk] = (sale_docs + credit_docs, revenue_amt)
        stock_by_shop[shop.pk] = stock
        expenses_by_shop[shop.pk] = (int(opex_docs or 0), opex_amt)
        profit_by_shop[shop.pk] = revenue_amt - stock - opex_amt

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -total_by_shop.get(shop.pk, (0, _zero()))[1],
            shop.name.lower(),
        ),
    )

    metric_maps = [
        ("Sales", sales_by_shop, "flow"),
        ("Credits", credits_by_shop, "flow"),
        ("Total", total_by_shop, "total"),
    ]

    columns = ["Shop"]
    for label, _by_shop, band in metric_maps:
        columns.append(
            {
                "label": label,
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "total": label == "Total",
                "band": band,
                "band_start": label in {"Sales", "Total"},
            }
        )
    columns.extend(
        [
            {
                "label": "Stock",
                "title": "Buying cost of items sold",
                "band": "result",
                "band_start": True,
            },
            {
                "label": "Expenses",
                "title": "Operating expenses recorded this period (excludes owner drawings)",
                "pair": True,
                "pair_qty": "Docs",
                "pair_amt": "Amt",
                "band": "result",
            },
            {
                "label": "Profit",
                "title": "Total − stock − expenses for this period",
                "band": "result",
            },
        ]
    )

    table_rows = []
    for shop in shops_sorted:
        cells = [shop.name]
        for _label, by_shop, _band in metric_maps:
            qty, amount = by_shop.get(shop.pk, (0, _zero()))
            cells.append(
                _qty_amount_cell(
                    qty,
                    amount,
                    title=f"{_qty_label(qty)} · {_money_ksh(amount)}",
                )
            )
        stock = stock_by_shop.get(shop.pk, _zero())
        opex_docs, opex = expenses_by_shop.get(shop.pk, (0, _zero()))
        profit = profit_by_shop.get(shop.pk, _zero())
        revenue = total_by_shop.get(shop.pk, (0, _zero()))[1]
        margin = (
            ((profit / revenue) * Decimal("100")).quantize(Decimal("0.1"))
            if revenue > 0
            else _zero()
        )
        cells.append(_money_cell(stock, title=f"Stock value {_money_ksh(stock)}"))
        cells.append(
            _qty_amount_cell(
                opex_docs,
                opex,
                title=f"{int(opex_docs or 0)} expenses · {_money_ksh(opex)}",
                tone="warn" if Decimal(opex or 0) > 0 else "neutral",
            )
            if opex_docs or opex
            else "—"
        )
        cells.append(
            _money_cell(
                profit,
                tone="good" if profit >= 0 else "bad",
                title=f"Profit {_money_ksh(profit)} · margin {margin}%",
            )
        )
        table_rows.append(cells)

    total_sale_docs, total_sale_amt = 0, _zero()
    total_credit_docs, total_credit_amt = 0, _zero()
    total_expense_docs = 0
    for shop in shops_sorted:
        sale_docs, sale_amt = sales_by_shop.get(shop.pk, (0, _zero()))
        credit_docs, credit_amt = credits_by_shop.get(shop.pk, (0, _zero()))
        exp_docs, _exp_amt = expenses_by_shop.get(shop.pk, (0, _zero()))
        total_sale_docs += int(sale_docs or 0)
        total_sale_amt += Decimal(sale_amt or 0)
        total_credit_docs += int(credit_docs or 0)
        total_credit_amt += Decimal(credit_amt or 0)
        total_expense_docs += int(exp_docs or 0)
    total_docs = total_sale_docs + total_credit_docs
    total_revenue = total_sale_amt + total_credit_amt
    total_stock = sum((stock_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero())
    total_expenses = sum(
        (expenses_by_shop.get(shop.pk, (0, _zero()))[1] for shop in shops_sorted),
        _zero(),
    )
    total_drawings = sum(
        (drawings_by_shop.get(shop.pk, (0, _zero()))[1] for shop in shops_sorted),
        _zero(),
    )
    total_profit = total_revenue - total_stock - total_expenses
    margin_pct = (
        ((total_profit / total_revenue) * Decimal("100")).quantize(Decimal("0.1"))
        if total_revenue > 0
        else _zero()
    )

    if shops_sorted:
        table_rows.append(
            [
                "Total",
                _qty_amount_cell(
                    total_sale_docs,
                    total_sale_amt,
                    title=f"{total_sale_docs} · {_money_ksh(total_sale_amt)}",
                ),
                _qty_amount_cell(
                    total_credit_docs,
                    total_credit_amt,
                    title=f"{total_credit_docs} · {_money_ksh(total_credit_amt)}",
                ),
                _qty_amount_cell(
                    total_docs,
                    total_revenue,
                    title=f"{total_docs} · {_money_ksh(total_revenue)}",
                ),
                _money_cell(total_stock, title=f"Stock value {_money_ksh(total_stock)}"),
                _qty_amount_cell(
                    total_expense_docs,
                    total_expenses,
                    title=f"{total_expense_docs} expenses · {_money_ksh(total_expenses)}",
                    tone="warn" if total_expenses > 0 else "neutral",
                ),
                _money_cell(
                    total_profit,
                    tone="good" if total_profit >= 0 else "bad",
                    title=f"Profit {_money_ksh(total_profit)} · margin {margin_pct}%",
                ),
            ]
        )

    active_shops = sum(
        1 for shop in shops_sorted if total_by_shop.get(shop.pk, (0, _zero()))[0] > 0
    )
    expense_hint = f"{total_expense_docs} entries · excludes drawings"
    if total_drawings > 0:
        expense_hint = (
            f"{total_expense_docs} entries · drawings {_money_dense(total_drawings)} excluded"
        )

    page = {
        "headline": "Revenue",
        "lead": (
            "Sales and credits by shop, with stock, expenses, and profit. "
            "Day shows hourly, month shows daily, year shows monthly."
        ),
        "alerts": [],
        "metrics": [
            _metric(
                "Sales",
                _money_ksh(total_sale_amt),
                hint=f"{total_sale_docs} receipts",
            ),
            _metric(
                "Credits",
                _money_ksh(total_credit_amt),
                hint=f"{total_credit_docs} receipts",
            ),
            _metric(
                "Total",
                _money_ksh(total_revenue),
                hint=f"{total_docs} receipts · {active_shops} shops",
            ),
            _metric(
                "Stock",
                _money_ksh(total_stock),
                hint="Buying cost of items sold",
            ),
            _metric(
                "Expenses",
                _money_ksh(total_expenses),
                hint=expense_hint,
                tone="warn" if total_expenses > 0 else "neutral",
            ),
            _metric(
                "Profit",
                _money_ksh(total_profit),
                hint=f"Margin {margin_pct}% · after expenses",
                tone="good" if total_profit >= 0 else "bad",
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                "Revenue by shop",
                columns,
                table_rows,
                empty="No revenue data for selected shops and period.",
                shop_grid=True,
                footnote=(
                    "All figures are for the selected period. "
                    "Sales and credits are ex-tax item value, net of returns. "
                    "Stock is the buying cost of those items. "
                    "Expenses are operating costs recorded in this period "
                    "(owner drawings are excluded from profit). "
                    "Profit = total − stock − expenses."
                ),
            )
        ],
    }
    breakdown = _revenue_breakdown_table(
        filters=filters,
        shop_ids=shop_ids,
        start=start,
        end=end,
        sales=sales,
        credits=credits,
    )
    if breakdown is not None:
        page["tables"].append(breakdown)
    return page


# ?mode=settings on the Cashiers page — the company-wide POS flags that
# govern cashier sessions (open & close, shop switching, idle sign-out).
BALANCES_CASHIER_SETTING_GROUPS = (
    {
        "title": "Open & close",
        "summary": "",
        "toggles": (
            ("enable_open_close", "Compulsory open & close"),
            ("require_clock_in", "Compulsory clock in"),
        ),
        "show_open_close_schedule": True,
    },
    {
        "title": "Shop switching",
        "summary": "",
        "toggles": (
            (
                "enable_passwordless_shop_switch",
                "Switch shops without password",
            ),
        ),
    },
    {
        "title": "Session timeout",
        "summary": (
            "Off = a session ends only when the user logs out. On = the "
            "user is signed out after the set minutes of inactivity."
        ),
        "toggles": (
            ("enable_employee_session_timeout", "Employee session"),
            ("enable_shop_session_timeout", "Shop session"),
        ),
        "show_session_timeouts": True,
    },
)

BALANCES_CASHIER_SETTING_FIELDS = frozenset(
    field
    for group in BALANCES_CASHIER_SETTING_GROUPS
    for field, _label in group["toggles"]
)


def _build_balances_settings(filters):
    """?mode=settings — company cashier session options on the Cashiers page."""
    from django.urls import reverse

    from shops.services import (
        get_company_pos_settings,
        get_company_working_hours_settings,
        list_working_hours_shop_rows,
        pos_toggle_enabled,
    )

    from .access import role_url_segment
    from .workspace import (
        CASHIER_SIDEBAR_LINKS,
        DAY_REPORT_SECTIONS_OFF_BY_DEFAULT,
        DAY_REPORT_SECTION_TOGGLES,
        DAY_REPORT_STATUS_MODES,
        SELL_POPUP_TOGGLES,
        cashier_default_day_report_status,
        cashier_default_pref,
        cashier_sidebar_pref_field,
        day_report_status_pref_field,
        settings_section_url,
    )

    role = filters.get("role")
    role_segment = role_url_segment(role) if role is not None else ""
    back_href = ""
    if role_segment:
        back_href = reverse(
            "employees:analytics_section",
            kwargs={"role_segment": role_segment, "section": "balances"},
        )

    def _default_on(key, *, off_by_default=False):
        value = cashier_default_pref(key)
        if value is None:
            return not off_by_default
        return bool(value)

    status_mode = cashier_default_day_report_status()
    # The per-cashier picks from /shop-cashier/settings/ shown as company
    # defaults — a cashier's own pick still wins where they have set one.
    groups = [
        {
            "title": "Dashboard sidebar",
            "summary": (
                "Company default menu links on the cashier dashboard. A "
                "cashier's own pick on Cashier settings still wins."
            ),
            "toggles": [
                {
                    "field": cashier_sidebar_pref_field(key),
                    "label": label,
                    "enabled": _default_on(key),
                }
                for key, label in CASHIER_SIDEBAR_LINKS
            ],
        },
        {
            "title": "Sell popup",
            "summary": (
                "Company defaults for the dashboard Sell popup — document "
                "types, payment methods, and price editing."
            ),
            "toggles": [
                {
                    "field": key,
                    "label": label,
                    "enabled": _default_on(key),
                }
                for key, label in SELL_POPUP_TOGGLES
            ],
        },
        {
            "title": "Day report",
            "summary": (
                "Which receipt statuses count as sales in the day "
                "accounting report by default."
            ),
            "stock_mode": True,
            "toggles": [
                {
                    "field": day_report_status_pref_field(mode),
                    "label": label,
                    "enabled": status_mode == mode,
                }
                for mode, label in DAY_REPORT_STATUS_MODES
            ],
        },
        {
            "title": "Day report sections",
            "summary": (
                "Company defaults for the tables shown in the day "
                "accounting report."
            ),
            "toggles": [
                {
                    "field": key,
                    "label": label,
                    "enabled": _default_on(
                        key,
                        off_by_default=key in DAY_REPORT_SECTIONS_OFF_BY_DEFAULT,
                    ),
                }
                for key, label in DAY_REPORT_SECTION_TOGGLES
            ],
        },
    ]
    pos = get_company_pos_settings()
    for group in BALANCES_CASHIER_SETTING_GROUPS:
        groups.append(
            {
                "title": group["title"],
                "summary": group["summary"],
                "show_open_close_schedule": bool(
                    group.get("show_open_close_schedule")
                ),
                "show_session_timeouts": bool(
                    group.get("show_session_timeouts")
                ),
                "toggles": [
                    {
                        "field": field,
                        "label": label,
                        "enabled": pos_toggle_enabled(pos, field),
                    }
                    for field, label in group["toggles"]
                ],
            }
        )
    return {
        "headline": "Cashier settings",
        "lead": (
            "Company-wide options for cashier sessions — cashier "
            "dashboard and day report defaults, compulsory open & close "
            "and clock in, shop switching, and idle sign-out."
        ),
        "alerts": [],
        "insights": [],
        "tables": [],
        "back_href": back_href,
        "back_label": "Sessions",
        "hide_date_filters": True,
        "cashier_settings_mode": True,
        "pos_settings": pos,
        "settings_groups": groups,
        "working_hours_enabled": bool(
            get_company_working_hours_settings().enabled
        ),
        "open_close_times_shops": list_working_hours_shop_rows(),
        "working_hours_settings_url": settings_section_url(
            role, "working-hours"
        ),
        "employee_timeout_minutes": int(
            getattr(pos, "employee_session_timeout_minutes", 0) or 0
        ),
        "shop_timeout_minutes": int(
            getattr(pos, "shop_session_timeout_minutes", 0) or 0
        ),
    }


def _build_balances(filters):
    """Cashiers report — shop day sessions the viewer can open and review."""
    from urllib.parse import urlencode

    from django.urls import reverse
    from django.utils import timezone

    from .access import role_url_segment

    role = filters.get("role")
    role_segment = role_url_segment(role) if role is not None else ""
    if (filters.get("balances_mode") or "") == "settings":
        return _build_balances_settings(filters)

    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    shops_by_id = {shop.pk: shop for shop in shops}

    balances_url = ""
    back_query = ""
    cashier_settings_href = ""
    if role_segment:
        balances_url = reverse(
            "employees:analytics_section",
            kwargs={"role_segment": role_segment, "section": "balances"},
        )
        if filters.get("can_edit_company_pos"):
            cashier_settings_href = f"{balances_url}?mode=settings"
        query = (filters.get("query") or "").strip()
        if query:
            balances_url = f"{balances_url}?{query}"
        back_query = urlencode({"back": balances_url})

    def _session_report_href(session_id):
        if not session_id or not role_segment:
            return ""
        url = reverse(
            "employees:analytics_day_session",
            kwargs={
                "role_segment": role_segment,
                "session_id": session_id,
            },
        )
        return f"{url}?{back_query}" if back_query else url

    day_balances = _day_balance_data(filters)
    session_rows = [dict(row) for row in day_balances["session_rows"]]

    # Cashier filter — a day belongs to whoever opened it.
    selected_employee_id = int(filters.get("selected_employee_id") or 0)
    filter_employees = list(
        EmployeeProfile.objects.filter(status=EmployeeStatus.ACTIVE)
        .select_related("user")
        .only(
            "id",
            "employee_id",
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        )
        .order_by("employee_id")[:300]
    )
    profiles = {profile.pk: profile for profile in filter_employees}
    if selected_employee_id and selected_employee_id not in profiles:
        selected_employee_id = 0

    actor_ids = {
        row.get("opened_by_id")
        for row in session_rows
        if row.get("opened_by_id")
    } | {
        row.get("closed_by_id")
        for row in session_rows
        if row.get("closed_by_id")
    }
    missing = actor_ids - set(profiles)
    if missing:
        profiles.update(
            {
                profile.pk: profile
                for profile in EmployeeProfile.objects.filter(pk__in=missing)
                .select_related("user")
            }
        )

    def _cashier_name(profile_id):
        profile = profiles.get(profile_id)
        if profile is None:
            return "—"
        return profile.user.get_full_name() or profile.user.username or "—"

    def _cashier_cell(profile_id):
        profile = profiles.get(profile_id)
        if profile is None:
            return "—"
        code = getattr(profile, "employee_id", "") or ""
        return {
            "label": _cashier_name(profile_id),
            "sub": f"ID {code}" if code else "",
        }

    if selected_employee_id:
        session_rows = [
            row
            for row in session_rows
            if row.get("opened_by_id") == selected_employee_id
        ]

    session_list_rows = []
    for row in sorted(
        session_rows,
        key=lambda item: item["closed_at"] or item["opened_at"] or timezone.now(),
        reverse=True,
    ):
        shop = shops_by_id.get(row["shop_id"])
        shop_name = shop.name if shop else f"Shop {row['shop_id']}"
        opened_local = timezone.localtime(row["opened_at"])
        is_open_row = bool(row.get("is_open")) or row["closed_at"] is None
        if is_open_row:
            session_sub = f"Started {opened_local:%H:%M}"
            status_cell = {
                "kind": "status",
                "label": "Open",
                "tone": "warn",
            }
        else:
            closed_local = timezone.localtime(row["closed_at"])
            session_sub = f"{opened_local:%H:%M} – {closed_local:%H:%M}"
            status_cell = {
                "kind": "status",
                "label": "Closed",
                "tone": "good",
            }
        href = _session_report_href(row.get("id"))
        session_list_rows.append(
            [
                {
                    "label": opened_local.strftime("%d %b %Y"),
                    "sub": session_sub,
                    "href": href,
                },
                _cashier_cell(row.get("opened_by_id")),
                shop_name,
                status_cell,
                {
                    "label": "Open",
                    "href": href,
                },
            ]
        )

    headline = "Cashiers report"
    if selected_employee_id:
        headline = f"Cashiers report — {_cashier_name(selected_employee_id)}"
    return {
        "headline": headline,
        "lead": (
            "Shop day sessions in the selected period — open a session to "
            "view every report for that day: money flow, items sold, "
            "expenses, drawings, and stock."
        ),
        "alerts": [],
        "insights": [],
        "tables": [
            _table(
                "Sessions",
                ["Session", "Cashier", "Shop", "Status", "Report"],
                session_list_rows,
                empty="No shop day sessions in this period.",
                footnote=(
                    "A day belongs to the cashier who opened it. Open a "
                    "session to view every report for that day."
                ),
            ),
        ],
        "filter_employees": filter_employees,
        "selected_employee_id": selected_employee_id,
        "cashier_settings_href": cashier_settings_href,
        "sessions_layout": True,
    }


def build_day_session_report_page(
    *, profile, request, session, allow_edit: bool = False
) -> dict:
    """Every report for one shop day session — opened from the Cashiers list.

    Mirrors the day accounting PDF (money flow + items sold + flagged sales +
    drawings + expenses + stock in/out) so the whole day can be reviewed on
    screen; ``?download=1`` on the view streams the same PDF.

    ``allow_edit`` is set by the analytics day-session view (the only place
    the POST endpoint exists) — when the profile may ``analytics/edit-balances``
    the page gains an "Edit session" action that corrects the recorded
    opening float and counted closing only.
    """
    from urllib.parse import urlencode

    from django.utils.http import url_has_allowed_host_and_scheme

    from shops.services import (
        day_report_channel_columns,
        day_report_not_till_lines,
        day_session_balance_summary,
        day_session_report_details,
        day_session_sales_profit,
        get_effective_pos_settings,
        shop_day_balance_channels,
        shop_stock_mode_flags,
    )

    from .workspace import (
        analytics_section_url,
        day_report_counted_statuses,
        day_report_section_enabled,
        day_report_status_mode,
    )

    shop = session.shop
    report_mode = day_report_status_mode(profile)
    counted_statuses = day_report_counted_statuses(profile)
    summary = day_session_balance_summary(
        session,
        counted_statuses=counted_statuses,
        item_stock_from_till=day_report_section_enabled(
            profile, "day_report_sec_item_stock"
        ),
        shelf_stock_from_till=day_report_section_enabled(
            profile, "day_report_sec_shelf_stock"
        ),
    )
    details = day_session_report_details(
        session, counted_statuses=counted_statuses
    )
    pos = get_effective_pos_settings(shop)
    channels = shop_day_balance_channels(shop=shop, pos=pos)
    print_paper_default = (
        getattr(pos, "receipt_paper_width", "") or ""
    ).strip()
    if print_paper_default not in ("80", "58"):
        print_paper_default = "a4"
    is_open = session.closed_at is None

    requested_back = (request.GET.get("back") or "").strip()
    back_href = ""
    if requested_back and url_has_allowed_host_and_scheme(
        requested_back, allowed_hosts={request.get_host()}
    ):
        back_href = requested_back
    if not back_href:
        role = getattr(profile, "role", None)
        back_href = (
            analytics_section_url(role, "balances")
            if role is not None
            else ""
        )

    def _person(row):
        if row is None or getattr(row, "user", None) is None:
            return "—"
        return row.user.get_full_name() or row.user.username or "—"

    opened_local = timezone.localtime(session.opened_at)
    closed_local = (
        timezone.localtime(session.closed_at) if session.closed_at else None
    )
    opened_by = _person(session.opened_by)
    closed_by = _person(session.closed_by)

    sales_scope_label = (
        "Pending sales only"
        if report_mode == "pending"
        else "Confirmed sales only"
        if report_mode == "confirmed"
        else ""
    )
    period_label = (
        f"{opened_local:%d %b %Y} · Open — live preview"
        if is_open
        else f"{opened_local:%d %b %Y} · Closed {closed_local:%H:%M}"
    )
    lead = f"Opened {opened_local:%d %b %Y %H:%M} by {opened_by}"
    lead += (
        f" — closed {closed_local:%d %b %Y %H:%M} by {closed_by}."
        if not is_open
        else " — not yet closed."
    )
    if sales_scope_label:
        lead += f" {sales_scope_label}."

    refunds_total = (
        Decimal(summary.get("cash_refunds") or 0)
        + Decimal(summary.get("mpesa_refunds") or 0)
        + Decimal(summary.get("card_refunds") or 0)
    )
    sales_count = int(details.get("sales_count") or 0)
    total_variance = summary.get("total_variance")

    item_stock_sec = day_report_section_enabled(
        profile, "day_report_sec_item_stock"
    )
    shelf_stock_sec = day_report_section_enabled(
        profile, "day_report_sec_shelf_stock"
    )
    sales_profit_sec = day_report_section_enabled(
        profile, "day_report_sec_sales_profit"
    )
    sales_profit = day_session_sales_profit(
        summary=summary,
        details=details,
        item_stock=item_stock_sec,
        shelf_stock=shelf_stock_sec,
    )

    metrics = [
        _metric("Cashier", opened_by),
        _metric("Session start", f"{opened_local:%d %b %Y · %H:%M}"),
        _metric(
            "Session end",
            (
                f"{closed_local:%d %b %Y · %H:%M}"
                if closed_local
                else "Still open"
            ),
            hint=(
                "Live preview — still counting"
                if is_open
                else (f"Closed by {closed_by}" if closed_by != "—" else "")
            ),
            tone="warn" if is_open else "good",
        ),
    ]
    if day_report_section_enabled(profile, "day_report_sec_money_flow"):
        sales_hint = f"{sales_count} receipt{'s' if sales_count != 1 else ''}"
        if sales_scope_label:
            sales_hint += f" · {sales_scope_label.lower()}"
        metrics.append(
            _metric(
                "Sales generated",
                _money_ksh(summary.get("sales_total")),
                hint=sales_hint,
            )
        )
    if sales_profit_sec:
        metrics.append(
            _metric(
                "Expected sales",
                _money_ksh(sales_profit["expected_sales"]),
                hint="counted + pending + credit + gifts",
            )
        )
    if day_report_section_enabled(profile, "day_report_sec_prepayments"):
        prepay_count = int(summary.get("prepayments_count") or 0)
        metrics.append(
            _metric(
                "Customer prepayments",
                _money_ksh(summary.get("prepayments_total")),
                hint=(
                    f"{prepay_count} payment"
                    f"{'s' if prepay_count != 1 else ''} received"
                ),
            )
        )
    if day_report_section_enabled(profile, "day_report_sec_expenses"):
        unpaid_expenses = sales_profit["expenses_unpaid"]
        metrics.append(
            _metric(
                "Expenses paid",
                _money_ksh(summary.get("expenses_paid")),
            )
        )
    stock_head_groups = (
        (details.get("stock_purchases") or [] if item_stock_sec else [])
        + (details.get("shelf_purchases") or [] if shelf_stock_sec else [])
    )
    if stock_head_groups:
        stock_unpaid = sales_profit["stock_unpaid"]
        metrics.append(
            _metric(
                "Stock purchased — paid",
                _money_ksh(summary.get("suppliers_paid")),
            )
        )
    if day_report_section_enabled(
        profile, "day_report_sec_not_till"
    ) and getattr(pos, "enable_credit", False):
        metrics.append(
            _metric(
                "Unpaid credit sales",
                _money_ksh(
                    (details.get("credit_sales") or {}).get("due")
                ),
            )
        )
    if day_report_section_enabled(
        profile, "day_report_sec_gift"
    ) and getattr(pos, "enable_gift", True):
        metrics.append(
            _metric(
                "Gift items issued",
                _money_ksh((details.get("gifts") or {}).get("total")),
            )
        )
    if day_report_section_enabled(profile, "day_report_sec_expenses"):
        metrics.append(
            _metric("Unpaid expenses", _money_ksh(unpaid_expenses))
        )
    if stock_head_groups:
        metrics.append(
            _metric("Unpaid stock purchased", _money_ksh(stock_unpaid))
        )
    if sales_profit_sec:
        metrics += [
            _metric(
                "Expected profit",
                _money_ksh(sales_profit["expected_profit"]),
                hint="expected sales − all costs + prepayments",
                tone=(
                    "good"
                    if sales_profit["expected_profit"] >= _zero()
                    else "bad"
                ),
            ),
            _metric(
                "Actual profit",
                _money_ksh(sales_profit["actual_profit"]),
                hint="counted sales − paid costs + prepayments",
                tone=(
                    "good"
                    if sales_profit["actual_profit"] >= _zero()
                    else "bad"
                ),
            ),
        ]

    channel_cols = day_report_channel_columns(
        summary=summary, channels=channels
    )

    def _money_cell(value, *, minus=False, tone="", title=""):
        if value is None:
            return {"kind": "money", "label": "—"}
        amount = Decimal(value)
        label = _money_ksh(amount)
        if minus and amount != 0:
            label = f"−{label}"
        cell = {"kind": "money", "label": label}
        if tone:
            cell["tone"] = tone
        if title:
            cell["title"] = title
        return cell

    def _channel_values(prefix):
        return {
            "cash": summary.get(f"{prefix}_cash"),
            "mpesa": summary.get(f"{prefix}_mpesa"),
            "card": summary.get(f"{prefix}_card"),
        }

    def _flow_row(label, by_channel, total, *, minus=False, row_class=""):
        cells = [label]
        for key, _label in channel_cols:
            cells.append(_money_cell(by_channel.get(key), minus=minus))
        cells.append(_money_cell(total, minus=minus))
        classes = ["ax-flow-row"]
        if minus:
            classes.append("ax-flow-row--out")
        if row_class:
            classes.append(row_class)
        return {"cells": cells, "row_class": " ".join(classes)}

    flow_columns = [{"label": "Flow"}]
    flow_columns += [
        {"label": label, "money": True} for _key, label in channel_cols
    ]
    flow_columns.append({"label": "Total", "money": True, "total": True})
    flow_rows = [
        _flow_row(
            "Opening float",
            _channel_values("opening"),
            summary.get("opening_total"),
            row_class="ax-flow-row--opening",
        ),
        _flow_row(
            f"Sales collected ({sales_count} receipts)",
            {
                "cash": summary.get("cash_sales"),
                "mpesa": summary.get("mpesa_sales"),
                "card": summary.get("card_sales"),
            },
            summary.get("sales_total"),
            row_class="ax-flow-row--in",
        ),
        _flow_row(
            f"Credit payments collected ({summary.get('credit_collected_count') or 0})",
            {
                "cash": summary.get("credit_collected_cash"),
                "mpesa": summary.get("credit_collected_mpesa"),
                "card": summary.get("credit_collected_card"),
            },
            summary.get("credit_collected_total"),
            row_class="ax-flow-row--in",
        ),
        _flow_row(
            f"Trade payments collected ({summary.get('trade_collected_count') or 0})",
            {
                "cash": summary.get("trade_collected_cash"),
                "mpesa": summary.get("trade_collected_mpesa"),
                "card": summary.get("trade_collected_card"),
            },
            summary.get("trade_collected_total"),
            row_class="ax-flow-row--in",
        ),
        _flow_row(
            f"Customer prepayments ({summary.get('prepayments_count') or 0})",
            _channel_values("prepayments"),
            summary.get("prepayments_total"),
            row_class="ax-flow-row--in",
        ),
        _flow_row(
            "Returns paid",
            {
                "cash": summary.get("cash_refunds"),
                "mpesa": summary.get("mpesa_refunds"),
                "card": summary.get("card_refunds"),
            },
            refunds_total,
            minus=True,
        ),
        _flow_row(
            "Expenses paid",
            {"cash": summary.get("expenses_paid")},
            summary.get("expenses_paid"),
            minus=True,
        ),
        _flow_row(
            "Stock bought",
            {"cash": summary.get("suppliers_paid")},
            summary.get("suppliers_paid"),
            minus=True,
        ),
        _flow_row(
            "Owner drawings",
            {
                "cash": summary.get("drawings_cash"),
                "mpesa": summary.get("drawings_mpesa"),
                "card": summary.get("drawings_card"),
            },
            summary.get("drawings_paid"),
            minus=True,
        ),
        _flow_row(
            f"Trade-in payouts ({summary.get('trade_paid_count') or 0})",
            {
                "cash": summary.get("trade_paid_cash"),
                "mpesa": summary.get("trade_paid_mpesa"),
                "card": summary.get("trade_paid_card"),
            },
            summary.get("trade_paid_total"),
            minus=True,
        ),
        _flow_row(
            "Expected in till",
            _channel_values("expected"),
            summary.get("expected_total"),
            row_class="ax-flow-row--expected",
        ),
    ]
    if not is_open:
        flow_rows.append(
            _flow_row(
                "Counted closing",
                _channel_values("closing"),
                summary.get("closing_total"),
                row_class="ax-flow-row--closing",
            )
        )
        variance_cells = ["Variance"]
        for key, _label in channel_cols:
            channel_variance = summary.get(f"{key}_variance")
            variance_cells.append(
                _money_cell(
                    channel_variance,
                    tone=_variance_tone(channel_variance),
                )
            )
        variance_cells.append(
            _money_cell(total_variance, tone=_variance_tone(total_variance))
        )
        variance_class = (
            "is-good" if _variance_tone(total_variance) == "good" else "is-bad"
        )
        flow_rows.append(
            {
                "cells": variance_cells,
                "row_class": (
                    f"ax-flow-row ax-flow-row--variance {variance_class}"
                ),
            }
        )
    # Cross-session pending clears, per cashier: receipts this session
    # printed but a later session collected stay in this Expected — the
    # deficit — while money collected here on another session's behalf sits
    # above Expected — the excess. Neither row is part of the build-up.
    # Each names the cashier on the other side of the hand-off.
    def _party_names(rows, key):
        names = []
        for row in rows:
            name = (row.get(key) or "").strip()
            if name and name != "—" and name not in names:
                names.append(name)
        return ", ".join(names)

    elsewhere_names = _party_names(
        details.get("cleared_elsewhere") or [], "cleared_by"
    )
    collected_for_names = _party_names(
        details.get("cleared_pending") or [], "seller"
    )
    # Only the side that applies shows: the deficit on the printing
    # session's page, the excess on the collecting session's page.
    if summary.get("cleared_elsewhere_count"):
        deficit_label = (
            f"Deficit — pending cleared in later sessions "
            f"({summary.get('cleared_elsewhere_count') or 0})"
        )
        if elsewhere_names:
            deficit_label += f" — collected by {elsewhere_names}"
        flow_rows.append(
            _flow_row(
                deficit_label,
                {
                    "cash": summary.get("cleared_elsewhere_cash"),
                    "mpesa": summary.get("cleared_elsewhere_mpesa"),
                    "card": summary.get("cleared_elsewhere_card"),
                },
                summary.get("cleared_elsewhere_total"),
                row_class="ax-flow-row--deficit",
            )
        )
    if summary.get("cleared_pending_count"):
        excess_label = (
            f"Excess — cleared pending receipts "
            f"({summary.get('cleared_pending_count') or 0})"
        )
        if collected_for_names:
            excess_label += f" — collected for {collected_for_names}"
        flow_rows.append(
            _flow_row(
                excess_label,
                {
                    "cash": summary.get("cleared_pending_cash"),
                    "mpesa": summary.get("cleared_pending_mpesa"),
                    "card": summary.get("cleared_pending_card"),
                },
                summary.get("cleared_pending_total"),
                row_class="ax-flow-row--excess",
            )
        )
    credit_payments = details.get("credit_payments") or {}
    credit_pay_rows = credit_payments.get("rows") or []
    credit_payments_sec = getattr(
        pos, "enable_credit", False
    ) and day_report_section_enabled(
        profile, "day_report_sec_credit_payments"
    )

    # "Dates" day-report pick (off by default): row times gain the date so
    # gap-window activity from before the session opened is identifiable.
    show_dates = day_report_section_enabled(profile, "day_report_sec_dates")
    time_header = "Date · time" if show_dates else "Time"

    def _time(at):
        local = timezone.localtime(at)
        if show_dates:
            return local.strftime("%d %b · %H:%M")
        return local.strftime("%H:%M")

    def _supplier_cell(name, phone):
        name = (name or "").strip()
        phone = (phone or "").strip()
        if not name and not phone:
            return "—"
        return {"label": name or "—", "sub": phone}

    money_footnote = (
        "Expected in till = opening + sales + credit and trade collections "
        "+ prepayments − returns − expenses − stock bought − drawings − "
        "trade-in payouts. A pending receipt cleared in another session "
        "stays in this Expected — the deficit; money cleared here for an "
        "earlier session sits outside Expected — the excess. "
        "Variance = counted − expected."
    )

    # Sales & profit as an Expected-vs-Actual matrix: each metric on a row,
    # the expectation and the reality side by side, the difference column
    # carrying what's still to collect / unpaid / the profit gap.
    sales_sub = (
        f"{sales_profit['sales_count']} receipts · "
        f"counted {_money_ksh(sales_profit['counted_sales'])}"
    )
    if sales_profit["pending_count"]:
        sales_sub += f" + pending {_money_ksh(sales_profit['pending_total'])}"
    if getattr(pos, "enable_credit", False):
        sales_sub += f" + credit {_money_ksh(sales_profit['credit_total'])}"
    if getattr(pos, "enable_gift", True):
        sales_sub += f" + gifts {_money_ksh(sales_profit['gift_total'])}"
    expected_actual_rows = [
        [
            {"label": "Sales", "sub": sales_sub},
            _money_cell(sales_profit["expected_sales"]),
            _money_cell(sales_profit["actual_sales"]),
            _money_cell(
                sales_profit["outstanding"],
                tone=(
                    "warn" if sales_profit["outstanding"] > _zero() else "good"
                ),
                title="Still to collect",
            ),
        ],
        [
            {
                "label": "Expenses",
                "sub": (
                    f"{sales_profit['expense_count']} docs · "
                    "difference is unpaid"
                ),
            },
            _money_cell(sales_profit["expenses_total"]),
            _money_cell(sales_profit["expenses_paid"]),
            _money_cell(
                sales_profit["expenses_unpaid"],
                tone=(
                    "warn"
                    if sales_profit["expenses_unpaid"] > _zero()
                    else "good"
                ),
                title="Unpaid expenses",
            ),
        ],
        [
            {
                "label": "Stock bought",
                "sub": (
                    f"{sales_profit['stock_doc_count']} docs · "
                    "difference is unpaid"
                ),
            },
            _money_cell(sales_profit["stock_total"]),
            _money_cell(sales_profit["stock_paid"]),
            _money_cell(
                sales_profit["stock_unpaid"],
                tone=(
                    "warn"
                    if sales_profit["stock_unpaid"] > _zero()
                    else "good"
                ),
                title="Unpaid stock",
            ),
        ],
        [
            {
                "label": "Customer prepayments",
                "sub": (
                    f"{sales_profit['prepayments_count']} received · "
                    "money ahead of orders"
                ),
            },
            _money_cell(sales_profit["prepayments_total"]),
            _money_cell(sales_profit["prepayments_total"]),
            _money_cell(_zero(), title="Received in full"),
        ],
        {
            "total_row": True,
            "cells": [
                "Profit",
                _money_cell(
                    sales_profit["expected_profit"],
                    tone=(
                        "good"
                        if sales_profit["expected_profit"] >= _zero()
                        else "bad"
                    ),
                ),
                _money_cell(
                    sales_profit["actual_profit"],
                    tone=(
                        "good"
                        if sales_profit["actual_profit"] >= _zero()
                        else "bad"
                    ),
                ),
                _money_cell(
                    sales_profit["profit_gap"],
                    title="Expected − actual gap",
                ),
            ],
        },
    ]

    tables = [
        _table(
            "Expected vs actual",
            [
                "Entry",
                {"label": "Expected", "money": True},
                {"label": "Actual", "money": True},
                {"label": "Difference", "money": True, "total": True},
            ],
            expected_actual_rows,
            empty="",
            footnote=(
                "Expected sales = counted sales + uncounted pending "
                "receipts + credit sales + gift issues; actual sales = "
                "money that reached the till (the difference is still to "
                "collect). Expected profit = expected sales − all "
                "expenses and stock bought + prepayments; actual profit "
                "charges paid costs only — cost differences are the "
                "unpaid amounts. Credit and gift value never enters "
                "Expected in till."
            ),
            section="day_report_sec_sales_profit",
            card_class="ax-card--expected",
            wide=True,
        ),
        _table(
            "Money flow",
            flow_columns,
            flow_rows,
            empty="No money movement recorded this day.",
            footnote=money_footnote,
            section="day_report_sec_money_flow",
            table_class="ax-table--moneyflow",
            wide=True,
        ),
    ]

    def _printed_label(at):
        if at is None:
            return "—"
        return timezone.localtime(at).strftime("%d %b %Y · %H:%M")

    cleared_pending = details.get("cleared_pending") or []
    cleared_total = sum(
        (Decimal(row.get("total") or 0) for row in cleared_pending), _zero()
    )
    tables.append(
        _table(
            "Cleared pending receipts",
            [
                "Cleared",
                "Receipt",
                "Sold by",
                "Items",
                {"label": "Amount", "money": True},
            ],
            [
                [
                    _time(row["at"]),
                    {
                        "label": row["number"],
                        "sub": f"Printed {_printed_label(row['printed_at'])}",
                    },
                    row["seller"],
                    row["items_label"],
                    _money_ksh(row["total"]),
                ]
                for row in cleared_pending
            ],
            empty="No earlier pending receipts cleared this session.",
            footnote=(
                f"{len(cleared_pending)} receipt"
                f"{'s' if len(cleared_pending) != 1 else ''} · "
                f"{_money_ksh(cleared_total)} — printed in an earlier "
                "session, confirmed and collected into this till; held "
                "above Expected as excess."
            )
            if cleared_pending
            else "",
            section="day_report_sec_cleared",
        )
    )

    cleared_elsewhere = details.get("cleared_elsewhere") or []
    elsewhere_total = sum(
        (Decimal(row.get("total") or 0) for row in cleared_elsewhere),
        _zero(),
    )
    tables.append(
        _table(
            "Pending cleared in later sessions",
            [
                "Cleared",
                "Receipt",
                "Cleared by",
                "Items",
                {"label": "Amount", "money": True},
            ],
            [
                [
                    _time(row["at"]),
                    {
                        "label": row["number"],
                        "sub": f"Printed {_printed_label(row['printed_at'])}",
                    },
                    row["cleared_by"],
                    row["items_label"],
                    _money_ksh(row["total"]),
                ]
                for row in cleared_elsewhere
            ],
            empty="No pending receipts cleared in later sessions.",
            footnote=(
                f"{len(cleared_elsewhere)} receipt"
                f"{'s' if len(cleared_elsewhere) != 1 else ''} · "
                f"{_money_ksh(elsewhere_total)} — printed in this session, "
                "confirmed and collected into a later till; this cashier's "
                "deficit."
            )
            if cleared_elsewhere
            else "",
            section="day_report_sec_cleared",
        )
    )

    prepay_rows = details.get("prepayments") or []
    prepay_total = sum(
        (Decimal(row.get("amount") or 0) for row in prepay_rows), _zero()
    )
    tables.append(
        _table(
            "Customer prepayments",
            [
                time_header,
                "Doc",
                "Customer",
                "Purpose",
                "Channel",
                "Recorded by",
                {"label": "Amount", "money": True},
            ],
            [
                [
                    _time(row["at"]),
                    row.get("number") or "—",
                    {
                        "label": row["customer_name"],
                        "sub": row.get("customer_phone") or "",
                    },
                    row["purpose"],
                    row.get("method_label") or "—",
                    row["by"],
                    _money_ksh(row["amount"]),
                ]
                for row in prepay_rows
            ],
            empty="No customer prepayments recorded this day.",
            footnote=(
                f"{len(prepay_rows)} prepayment"
                f"{'s' if len(prepay_rows) != 1 else ''}"
                f" · {_money_ksh(prepay_total)} received."
                " Money in ahead of orders — part of Expected in till."
            ),
            section="day_report_sec_prepayments",
        )
    )

    if getattr(pos, "enable_credit", False):
        credit_pay_table_rows = []
        for pay in credit_pay_rows:
            if pay["converted"]:
                outcome = {
                    "kind": "status",
                    "label": "Converted to sale",
                    "tone": "good",
                }
            elif pay["cleared"]:
                outcome = {
                    "kind": "status",
                    "label": "Paid in full",
                    "tone": "good",
                }
            else:
                outcome = {
                    "kind": "status",
                    "label": (
                        f"Part payment — {_money_ksh(pay['due_after'])} due"
                        if pay["due_after"] is not None
                        else "Part payment"
                    ),
                    "tone": "warn",
                }
            receipt_sub = pay["client"]
            if pay["ref"]:
                receipt_sub += f" · Ref {pay['ref']}"
            credit_pay_table_rows.append(
                [
                    _time(pay["at"]),
                    {"label": pay["number"], "sub": receipt_sub},
                    _printed_label(pay.get("printed_at")),
                    pay["channel_label"],
                    outcome,
                    pay["by"],
                    _money_ksh(pay["amount"]),
                ]
            )
        converted_count = credit_payments.get("receipts_converted") or 0
        credit_pay_footnote = (
            f"{credit_payments.get('count') or 0} payment"
            f"{'s' if (credit_payments.get('count') or 0) != 1 else ''}"
            f" · {_money_ksh(credit_payments.get('total'))} collected"
        )
        if converted_count:
            credit_pay_footnote += (
                f" · {converted_count} credit"
                f"{'s' if converted_count != 1 else ''} converted to sales"
            )
        credit_pay_footnote += (
            ". Collected into this session's till — part of Expected in till."
        )
        tables.append(
            _table(
                "Credit payments collected",
                [
                    time_header,
                    "Receipt",
                    "Printed",
                    "Channel",
                    "Outcome",
                    "Collected by",
                    {"label": "Amount", "money": True},
                ],
                credit_pay_table_rows,
                empty="No credit payments collected this day.",
                footnote=credit_pay_footnote,
                section="day_report_sec_credit_payments",
            )
        )

    trade_pay_rows = details.get("trade_payments") or []
    trade_table_rows = []
    for pay in trade_pay_rows:
        flow = (
            {"kind": "status", "label": "Collected", "tone": "good"}
            if pay["direction"] == "in"
            else {"kind": "status", "label": "Paid out", "tone": "warn"}
        )
        receipt_sub = pay["client"]
        if pay["ref"]:
            receipt_sub += f" · Ref {pay['ref']}"
        trade_table_rows.append(
            [
                _time(pay["at"]),
                {
                    "label": pay["number"],
                    "sub": (
                        f"{receipt_sub} · Printed "
                        f"{_printed_label(pay['printed_at'])}"
                    ),
                },
                pay["channel_label"],
                flow,
                pay["by"],
                _money_ksh(pay["amount"]),
            ]
        )
    trade_collected_total = sum(
        (
            Decimal(pay["amount"] or 0)
            for pay in trade_pay_rows
            if pay["direction"] == "in"
        ),
        _zero(),
    )
    trade_paid_total = sum(
        (
            Decimal(pay["amount"] or 0)
            for pay in trade_pay_rows
            if pay["direction"] == "out"
        ),
        _zero(),
    )
    trade_footnote = (
        f"{len(trade_pay_rows)} payment"
        f"{'s' if len(trade_pay_rows) != 1 else ''}"
        f" · {_money_ksh(trade_collected_total)} collected"
        f" · {_money_ksh(trade_paid_total)} paid out"
        ". Settlements move this session's till — collections are part "
        "of Expected in till and payouts subtract from it."
    )
    tables.append(
        _table(
            "Trade settlements",
            [
                time_header,
                "Receipt",
                "Channel",
                "Flow",
                "Settled by",
                {"label": "Amount", "money": True},
            ],
            trade_table_rows,
            empty="No trade settlements this day.",
            footnote=trade_footnote,
            section="day_report_sec_cleared",
        )
    )

    not_till = day_report_not_till_lines(
        summary=summary, details=details, pos=pos
    )
    not_till_by_key = {
        line["key"]: line for line in not_till.get("lines") or []
    }
    credit_gift_tone = {
        ShopReceiptStatus.ACTIVE: "warn",
        ShopReceiptStatus.CANCELLED: "bad",
    }

    def _receipt_rows(kind):
        rows = []
        for row in details.get("credit_gift_receipts") or []:
            if row["kind"] != kind:
                continue
            rows.append(
                [
                    {"label": row["number"], "sub": _time(row["at"])},
                    row["seller"],
                    {
                        "kind": "status",
                        "label": row["status_label"],
                        "tone": credit_gift_tone.get(row["status"], "good"),
                    },
                    _money_ksh(row["total"]),
                ]
            )
        return rows

    def _not_till_row(line, *, with_due):
        cells = [
            line["label"],
            str(line.get("count") or 0),
            _money_ksh(line.get("value")),
        ]
        if with_due:
            cells += [
                _money_ksh(line.get("collected"))
                if line.get("collected") is not None
                else "—",
                _money_ksh(line.get("due"))
                if line.get("due") is not None
                else "—",
            ]
        return cells

    credit_line = not_till_by_key.get("credit")
    credit_receipt_rows = _receipt_rows(ShopReceiptKind.CREDIT)
    if credit_line is not None or credit_receipt_rows:
        if credit_line is not None:
            credit_footnote = (
                "Credit is collected later — it does not add to "
                "Expected in till."
            )
            slips = not_till.get("slips")
            if slips:
                credit_footnote += (
                    f" Credit slips counted: opened "
                    f"{_money_ksh(slips.get('opened'))}"
                )
                if not is_open:
                    credit_footnote += (
                        f" · counted {_money_ksh(slips.get('counted'))}"
                    )
                    if slips.get("variance") is not None:
                        credit_footnote += (
                            f" · diff {_money_ksh(slips['variance'])}"
                        )
                credit_footnote += "."
            tables.append(
                _table(
                    "Credit — not in the till",
                    [
                        "Document",
                        {"label": "Docs", "money": True},
                        {"label": "Value", "money": True},
                        {"label": "Collected", "money": True},
                        {"label": "Still due", "money": True},
                    ],
                    [_not_till_row(credit_line, with_due=True)],
                    empty="",
                    footnote=credit_footnote,
                    section="day_report_sec_not_till",
                )
            )
        if credit_receipt_rows:
            tables.append(
                _table(
                    "Credit receipts",
                    [
                        "Receipt",
                        "Sold by",
                        "Status",
                        {"label": "Amount", "money": True},
                    ],
                    credit_receipt_rows,
                    empty="No credit receipts this day.",
                    section="day_report_sec_not_till",
                )
            )

    gift_line = not_till_by_key.get("gift")
    gift_receipt_rows = _receipt_rows(ShopReceiptKind.GIFT)
    if gift_line is not None or gift_receipt_rows:
        if gift_line is not None:
            tables.append(
                _table(
                    "Gifts — not in the till",
                    [
                        "Document",
                        {"label": "Docs", "money": True},
                        {"label": "Value", "money": True},
                    ],
                    [_not_till_row(gift_line, with_due=False)],
                    empty="",
                    footnote=(
                        "Gifts are free issues — they do not add to "
                        "Expected in till."
                    ),
                    section="day_report_sec_gift",
                )
            )
        if gift_receipt_rows:
            tables.append(
                _table(
                    "Gift receipts",
                    [
                        "Receipt",
                        "Sold by",
                        "Status",
                        {"label": "Amount", "money": True},
                    ],
                    gift_receipt_rows,
                    empty="No gift receipts this day.",
                    section="day_report_sec_gift",
                )
            )

    items_sold = details.get("items_sold") or []
    items_qty_total = sum(
        (Decimal(row.get("qty") or 0) for row in items_sold), _zero()
    )
    items_amount_total = sum(
        (Decimal(row.get("amount") or 0) for row in items_sold), _zero()
    )
    tables.append(
        _table(
            "Items sold",
            [
                "Item",
                {"label": "Qty", "money": True},
                {"label": "Amount", "money": True},
            ],
            [
                [
                    row["item"],
                    _qty_label(row["qty"]),
                    _money_ksh(row["amount"]),
                ]
                for row in items_sold
            ],
            empty="No items sold this day.",
            footnote=(
                f"{len(items_sold)} item"
                f"{'s' if len(items_sold) != 1 else ''} · "
                f"{_qty_label(items_qty_total)} units · "
                f"{_money_ksh(items_amount_total)}"
            )
            if items_sold
            else "",
            section="day_report_sec_items_sold",
        )
    )

    sellers = details.get("sellers") or []
    seller_receipt_total = sum(
        (int(row.get("receipts") or 0) for row in sellers), 0
    )
    seller_qty_total = sum(
        (Decimal(row.get("qty") or 0) for row in sellers), _zero()
    )
    seller_amount_total = sum(
        (Decimal(row.get("amount") or 0) for row in sellers), _zero()
    )
    tables.append(
        _table(
            "Sales by employee",
            [
                "Employee",
                {"label": "Sales", "money": True},
                {"label": "Qty", "money": True},
                {"label": "Amount", "money": True},
            ],
            [
                [
                    row["seller"],
                    str(row["receipts"]),
                    _qty_label(row["qty"]),
                    _money_ksh(row["amount"]),
                ]
                for row in sellers
            ],
            empty="No sales recorded this day.",
            footnote=(
                f"{len(sellers)} employee"
                f"{'s' if len(sellers) != 1 else ''} · "
                f"{seller_receipt_total} sale"
                f"{'s' if seller_receipt_total != 1 else ''} · "
                f"{_qty_label(seller_qty_total)} units · "
                f"{_money_ksh(seller_amount_total)}"
            )
            if sellers
            else "",
            section="day_report_sec_sellers",
        )
    )

    flagged = details.get("flagged_receipts") or []
    flagged_total = sum(
        (Decimal(row.get("total") or 0) for row in flagged), _zero()
    )
    flagged_rows = []
    for row in flagged:
        flagged_rows.append(
            [
                {"label": row["number"], "sub": _time(row["at"])},
                row["seller"],
                {
                    "kind": "status",
                    "label": row["status_label"],
                    "tone": (
                        "warn"
                        if row["status"] == ShopReceiptStatus.ACTIVE
                        else "bad"
                    ),
                },
                _money_ksh(row["total"]),
            ]
        )
    flagged_table = _table(
        "Pending & cancelled sales",
        [
            "Receipt",
            "Sold by",
            "Status",
            {"label": "Amount", "money": True},
        ],
        flagged_rows,
        empty="No pending or cancelled sales this day.",
        footnote=(
            f"{len(flagged)} receipt{'s' if len(flagged) != 1 else ''} · "
            f"{_money_ksh(flagged_total)}"
        )
        if flagged
        else "",
        section="day_report_sec_flagged",
    )
    tables.append(flagged_table)

    drawing_rows = details.get("drawings") or []
    drawing_columns = [{"label": f"{time_header} · detail"}]
    drawing_columns += [
        {"label": label, "money": True} for _key, label in channel_cols
    ]
    drawing_columns.append({"label": "Total", "money": True})
    drawing_table_rows = []
    for row in drawing_rows:
        cells = [f"{_time(row['at'])} · {row['name']}"]
        for key, _label in channel_cols:
            cells.append(_money_ksh(row.get(key)))
        cells.append(_money_ksh(row.get("paid")))
        drawing_table_rows.append(cells)
    tables.append(
        _table(
            "Drawings done",
            drawing_columns,
            drawing_table_rows,
            empty="No drawings recorded this day.",
            footnote=(
                f"Total {_money_ksh(summary.get('drawings_paid'))}"
                if drawing_rows
                else ""
            ),
            section="day_report_sec_drawings",
        )
    )

    expense_rows = details.get("expenses") or []
    expenses_paid_total = sum(
        (Decimal(row.get("paid") or 0) for row in expense_rows), _zero()
    )
    expense_status_tones = {
        ExpensePaymentStatus.PAID: "good",
        ExpensePaymentStatus.PARTIAL: "warn",
        ExpensePaymentStatus.UNPAID: "bad",
    }
    expense_table_rows = []
    for row in expense_rows:
        expense_table_rows.append(
            [
                _time(row["at"]),
                row["name"],
                row["category_label"],
                _qty_label(row["qty"]),
                _supplier_cell(row["supplier_name"], row["supplier_phone"]),
                {
                    "kind": "status",
                    "label": row.get("status_label") or "—",
                    "tone": expense_status_tones.get(
                        row.get("status"), "neutral"
                    ),
                },
                _money_ksh(row.get("paid")),
            ]
        )
    tables.append(
        _table(
            "Expenses incurred",
            [
                time_header,
                "Expense",
                "Category",
                {"label": "Qty", "money": True},
                "Supplier",
                "Status",
                {"label": "Paid", "money": True},
            ],
            expense_table_rows,
            empty="No expenses recorded this day.",
            section="day_report_sec_expenses",
            footnote=(
                f"Paid {_money_ksh(expenses_paid_total)}"
                if expense_rows
                else ""
            ),
        )
    )

    stock_status_tones = {
        StockPaymentStatus.PAID: "good",
        StockPaymentStatus.PARTIAL: "warn",
        StockPaymentStatus.UNPAID: "bad",
    }

    def _stocked_in_table(title, groups, empty, section=""):
        rows = []
        qty_total = _zero()
        amount_total = _zero()
        has_total = False
        for group in groups:
            source = (group.get("source_label") or "").strip() or "Stock in"
            paid_note = source
            if group.get("paid"):
                paid_note += f" · Paid {_money_ksh(group['paid'])}"
            elif group.get("payment_label"):
                paid_note += f" · {group['payment_label']}"
            lines = group.get("lines") or [
                {
                    "item": "Stock purchase",
                    "qty": None,
                    "unit_cost": None,
                    "total": None,
                    "supplier_name": "",
                    "supplier_phone": "",
                }
            ]
            for index, line in enumerate(lines):
                if line.get("qty") is not None:
                    qty_total += Decimal(line["qty"])
                if line.get("total") is not None:
                    amount_total += Decimal(line["total"])
                    has_total = True
                rows.append(
                    [
                        _time(group["at"]),
                        {
                            "label": line["item"],
                            "sub": paid_note if index == 0 else source,
                        },
                        (
                            _qty_label(line["qty"])
                            if line.get("qty") is not None
                            else "—"
                        ),
                        (
                            _money_ksh(line["unit_cost"])
                            if line.get("unit_cost") is not None
                            else "—"
                        ),
                        (
                            _money_ksh(line["total"])
                            if line.get("total") is not None
                            else "—"
                        ),
                        {
                            "kind": "status",
                            "label": line.get("payment_label") or "—",
                            "tone": stock_status_tones.get(
                                line.get("payment_status"), "neutral"
                            ),
                        },
                        _supplier_cell(
                            line["supplier_name"], line["supplier_phone"]
                        ),
                    ]
                )
        paid_total = sum(
            (Decimal(group.get("paid") or 0) for group in groups), _zero()
        )
        if groups:
            rows.append(
                {
                    "total_row": True,
                    "cells": [
                        "Total",
                        "",
                        _qty_label(qty_total),
                        "",
                        (
                            _money_ksh(amount_total)
                            if has_total
                            else "—"
                        ),
                        (
                            {
                                "kind": "status",
                                "label": f"Paid {_money_ksh(paid_total)}",
                                "tone": "good",
                            }
                            if paid_total > 0
                            else "—"
                        ),
                        "",
                    ],
                }
            )
        return _table(
            title,
            [
                time_header,
                "Item",
                {"label": "Qty", "money": True},
                {"label": "Amount", "money": True},
                {"label": "Total", "money": True},
                "Status",
                "Supplier",
            ],
            rows,
            empty=empty,
            footnote=(
                f"Paid to suppliers {_money_ksh(paid_total)}"
                if groups
                else ""
            ),
            section=section,
        )

    def _stocked_out_table(title, out_rows, est_total, empty, section=""):
        qty_total = _zero()
        refund_total = _zero()
        has_value = False
        value_total = _zero()
        rows = []
        for row in out_rows:
            extras = []
            if row.get("note"):
                extras.append(row["note"])
            if row.get("refund_amount"):
                refund_total += Decimal(row["refund_amount"])
                extras.append(
                    f"Refund {_money_ksh(row['refund_amount'])}"
                )
            qty_total += Decimal(row.get("qty") or 0)
            if row.get("est_value") is not None:
                value_total += Decimal(row["est_value"])
                has_value = True
            rows.append(
                [
                    _time(row["at"]),
                    row["item"],
                    _qty_label(row["qty"]),
                    {
                        "label": row.get("reason_label") or "—",
                        "sub": " · ".join(extras),
                    },
                    (
                        _money_ksh(row["unit_cost"])
                        if row.get("unit_cost") is not None
                        else "—"
                    ),
                    (
                        _money_ksh(row["est_value"])
                        if row.get("est_value") is not None
                        else "—"
                    ),
                ]
            )
        if est_total is not None:
            value_total = Decimal(est_total)
            has_value = has_value or value_total != 0
        if out_rows:
            rows.append(
                {
                    "total_row": True,
                    "cells": [
                        "Total",
                        "",
                        _qty_label(qty_total),
                        "",
                        "",
                        _money_ksh(value_total) if has_value else "—",
                    ],
                }
            )
        footnote_parts = []
        if has_value:
            footnote_parts.append(
                f"Estimated value {_money_ksh(value_total)}"
            )
        if refund_total > 0:
            footnote_parts.append(
                f"Refunded {_money_ksh(refund_total)}"
            )
        return _table(
            title,
            [
                time_header,
                "Item",
                {"label": "Qty", "money": True},
                "Reason",
                {"label": "Amount", "money": True},
                {"label": "Total", "money": True},
            ],
            rows,
            empty=empty,
            footnote=" · ".join(footnote_parts) if out_rows else "",
            section=section,
        )

    items_stock_on, shelf_stock_on = shop_stock_mode_flags(shop, pos=pos)

    stock_groups = details.get("stock_purchases") or []
    stock_out = details.get("stock_out") or []
    if items_stock_on or (stock_groups or stock_out):
        tables.append(
            _stocked_in_table(
                "Stocked in",
                stock_groups,
                "No stock added this day.",
                section="day_report_sec_item_stock",
            )
        )
        tables.append(
            _stocked_out_table(
                "Stocked out",
                stock_out,
                details.get("stock_out_est_total"),
                "No stock removed this day.",
                section="day_report_sec_item_stock",
            )
        )

    shelf_groups = details.get("shelf_purchases") or []
    shelf_out = details.get("shelf_out") or []
    if shelf_stock_on or (shelf_groups or shelf_out):
        tables.append(
            _stocked_in_table(
                "Shelf stocked in",
                shelf_groups,
                "No shelf stock added this day.",
                section="day_report_sec_shelf_stock",
            )
        )
        tables.append(
            _stocked_out_table(
                "Shelf stocked out",
                shelf_out,
                None,
                "No shelf stock removed this day.",
                section="day_report_sec_shelf_stock",
            )
        )

    tables = [
        t
        for t in tables
        if day_report_section_enabled(profile, t.get("section"))
    ]

    alerts = []
    if is_open:
        alerts.append(
            _alert(
                "info",
                "Day still open",
                "Figures are a live preview — sales and paid-outs update "
                "until the day is closed.",
            )
        )

    page = {
        "headline": f"Day session — {shop.name}",
        "period_label": period_label,
        "lead": lead,
        "back_href": back_href,
        "back_label": "Back to cashiers",
        "download_href": f"{request.path}?{urlencode({'download': '1'})}",
        "receipts_print_modal": True,
        "receipts_print_url": request.path,
        "receipts_print_title": "Print day report",
        "receipts_print_lead": (
            "Pick a paper size, then print or download the A4 PDF."
        ),
        "receipts_print_default_paper": print_paper_default,
        "list_table_layout": True,
        "hide_date_filters": True,
        "day_session_layout": True,
        "sales_count": sales_count,
        "alerts": alerts,
        "metrics": metrics,
        "insights": [],
        "tables": tables,
    }
    if allow_edit:
        from .module_permissions import employee_may

        if employee_may(profile, "analytics", "edit-balances"):

            def _amt(value):
                if value is None:
                    return ""
                try:
                    return str(int(Decimal(value).quantize(Decimal("1"))))
                except Exception:
                    return ""

            page["edit_session"] = {
                "post_url": request.get_full_path(),
                "is_open": is_open,
                "channels": channels,
                "opening": {
                    "cash": _amt(session.opening_cash),
                    "mpesa": _amt(session.opening_mpesa),
                    "card": _amt(session.opening_card),
                },
                "closing": {
                    "cash": _amt(session.closing_cash),
                    "mpesa": _amt(session.closing_mpesa),
                    "card": _amt(session.closing_card),
                },
            }
    flags = _pos_feature_flags(pos)
    for key, _label in channel_cols:
        # A channel the money flow shows (activated or carrying figures)
        # must keep its columns — never let company-level flags strip it.
        flags[key] = True
    page = _apply_pos_visibility(page, flags)
    return {
        "section_slug": "balances",
        "include_all_time": False,
        "page": page,
    }


def _credit_payment_totals_by_shop(
    *, shop_ids, start, end
) -> tuple[
    dict[int, tuple[int, Decimal]],
    dict[int, tuple[int, Decimal]],
    dict[int, tuple[int, Decimal]],
]:
    """Credit account collections in period, split by cash / M-Pesa / card per shop."""
    paid_cash_by_shop: dict[int, tuple[int, Decimal]] = {}
    paid_mpesa_by_shop: dict[int, tuple[int, Decimal]] = {}
    paid_card_by_shop: dict[int, tuple[int, Decimal]] = {}
    events = _within_created_range(
        ClientCreditAccountEvent.objects.filter(
            shop_id__in=shop_ids,
            kind__in=(
                ClientCreditAccountEventKind.PAYMENT_CASH,
                ClientCreditAccountEventKind.PAYMENT_MPESA,
                ClientCreditAccountEventKind.PAYMENT_CARD,
            ),
        ),
        start,
        end,
        lookup="occurred_at",
    )
    for row in events.values("shop_id", "kind").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("amount"), _zero()),
    ):
        shop_id = row["shop_id"]
        if not shop_id:
            continue
        entry = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        if row["kind"] == ClientCreditAccountEventKind.PAYMENT_MPESA:
            paid_mpesa_by_shop[shop_id] = entry
        elif row["kind"] == ClientCreditAccountEventKind.PAYMENT_CARD:
            paid_card_by_shop[shop_id] = entry
        else:
            paid_cash_by_shop[shop_id] = entry
    return paid_cash_by_shop, paid_mpesa_by_shop, paid_card_by_shop


def _credit_payment_totals_by_bucket(
    *, shop_ids, start, end, bucket_kind: str
) -> tuple[
    dict[str, tuple[int, Decimal]],
    dict[str, tuple[int, Decimal]],
    dict[str, tuple[int, Decimal]],
]:
    """Credit account collections in period, split by cash / M-Pesa / card per bucket."""
    paid_cash_by_bucket: dict[str, tuple[int, Decimal]] = {}
    paid_mpesa_by_bucket: dict[str, tuple[int, Decimal]] = {}
    paid_card_by_bucket: dict[str, tuple[int, Decimal]] = {}
    trunc = _created_at_trunc("occurred_at", bucket_kind)
    events = _within_created_range(
        ClientCreditAccountEvent.objects.filter(
            shop_id__in=shop_ids,
            kind__in=(
                ClientCreditAccountEventKind.PAYMENT_CASH,
                ClientCreditAccountEventKind.PAYMENT_MPESA,
                ClientCreditAccountEventKind.PAYMENT_CARD,
            ),
        ),
        start,
        end,
        lookup="occurred_at",
    )
    for row in events.annotate(bucket=trunc).values("bucket", "kind").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("amount"), _zero()),
    ):
        label = _bucket_label(row["bucket"], bucket_kind)
        if not label:
            continue
        entry = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        if row["kind"] == ClientCreditAccountEventKind.PAYMENT_MPESA:
            paid_mpesa_by_bucket[label] = entry
        elif row["kind"] == ClientCreditAccountEventKind.PAYMENT_CARD:
            paid_card_by_bucket[label] = entry
        else:
            paid_cash_by_bucket[label] = entry
    return paid_cash_by_bucket, paid_mpesa_by_bucket, paid_card_by_bucket


def _sales_breakdown_table(
    *,
    filters,
    shop_ids,
    start,
    end,
    sales,
    credits,
) -> dict | None:
    config = _period_breakdown_config(filters)
    if not config or not start or not end:
        return None

    bucket_kind = config["bucket_kind"]
    labels = _iter_bucket_labels(start, end, bucket_kind)
    if not labels:
        return None

    money_field = DecimalField(max_digits=14, decimal_places=2)
    zero_money = Value(0, output_field=money_field)
    cash_expr = Case(
        When(settled_from_credit=False, then=F("cash_amount")),
        default=zero_money,
        output_field=money_field,
    )
    mpesa_expr = Case(
        When(settled_from_credit=False, then=F("mpesa_amount")),
        default=zero_money,
        output_field=money_field,
    )
    card_expr = Case(
        When(settled_from_credit=False, then=F("card_amount")),
        default=zero_money,
        output_field=money_field,
    )
    unpaid_due_expr = Greatest(
        ExpressionWrapper(
            F("total") - F("amount_paid"),
            output_field=money_field,
        ),
        zero_money,
    )

    trunc = _revenue_moment_trunc(bucket_kind)
    total_by_bucket: dict[str, tuple[int, Decimal]] = {}
    cash_by_bucket: dict[str, tuple[int, Decimal]] = {}
    mpesa_by_bucket: dict[str, tuple[int, Decimal]] = {}
    card_by_bucket: dict[str, tuple[int, Decimal]] = {}
    unpaid_credit_by_bucket: dict[str, tuple[int, Decimal]] = {}
    (
        paid_cash_by_bucket,
        paid_mpesa_by_bucket,
        paid_card_by_bucket,
    ) = _credit_payment_totals_by_bucket(
        shop_ids=shop_ids,
        start=start,
        end=end,
        bucket_kind=bucket_kind,
    )

    for row in sales.annotate(bucket=trunc).values("bucket").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
        cash=Coalesce(Sum(cash_expr), _zero()),
        mpesa=Coalesce(Sum(mpesa_expr), _zero()),
        card=Coalesce(Sum(card_expr), _zero()),
    ):
        label = _bucket_label(row["bucket"], bucket_kind)
        if not label:
            continue
        total_by_bucket[label] = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        cash_by_bucket[label] = (0, Decimal(row["cash"] or 0))
        mpesa_by_bucket[label] = (0, Decimal(row["mpesa"] or 0))
        card_by_bucket[label] = (0, Decimal(row["card"] or 0))

    for row in credits.annotate(bucket=trunc).values("bucket").annotate(
        unpaid_docs=Count("id", filter=Q(total__gt=F("amount_paid"))),
        unpaid=Coalesce(Sum(unpaid_due_expr), _zero()),
    ):
        label = _bucket_label(row["bucket"], bucket_kind)
        if not label:
            continue
        unpaid_credit_by_bucket[label] = (
            int(row["unpaid_docs"] or 0),
            Decimal(row["unpaid"] or 0),
        )

    trading = _trading_by_bucket_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        bucket_kind=bucket_kind,
        kinds=[ShopReceiptKind.SALE],
        receipt_status=filters.get("receipt_status"),
    )

    paid_total_by_bucket: dict[str, tuple[int, Decimal]] = {}
    general_by_bucket: dict[str, tuple[int, Decimal]] = {}
    for label in labels:
        sale_docs, _sale_amt = total_by_bucket.get(label, (0, _zero()))
        unpaid_docs, unpaid_amt = unpaid_credit_by_bucket.get(label, (0, _zero()))
        cash_amt = cash_by_bucket.get(label, (0, _zero()))[1]
        mpesa_amt = mpesa_by_bucket.get(label, (0, _zero()))[1]
        card_amt = card_by_bucket.get(label, (0, _zero()))[1]
        paid_cash_docs, paid_cash_amt = paid_cash_by_bucket.get(label, (0, _zero()))
        paid_mpesa_docs, paid_mpesa_amt = paid_mpesa_by_bucket.get(label, (0, _zero()))
        paid_card_docs, paid_card_amt = paid_card_by_bucket.get(label, (0, _zero()))
        paid_amt = (
            Decimal(paid_cash_amt or 0)
            + Decimal(paid_mpesa_amt or 0)
            + Decimal(paid_card_amt or 0)
        )
        paid_total_by_bucket[label] = (
            int(paid_cash_docs or 0)
            + int(paid_mpesa_docs or 0)
            + int(paid_card_docs or 0),
            paid_amt,
        )
        general_by_bucket[label] = (
            int(sale_docs or 0) + int(unpaid_docs or 0),
            Decimal(cash_amt or 0)
            + Decimal(mpesa_amt or 0)
            + Decimal(card_amt or 0)
            + paid_amt
            + Decimal(unpaid_amt or 0),
        )

    metric_maps = [
        ("Cash", cash_by_bucket, "cash", "money"),
        ("M-Pesa", mpesa_by_bucket, "mpesa", "money"),
        ("Card", card_by_bucket, "card", "money"),
        ("Paid cash", paid_cash_by_bucket, "credits", "money"),
        ("Paid M-Pesa", paid_mpesa_by_bucket, "credits", "money"),
        ("Paid card", paid_card_by_bucket, "credits", "money"),
        ("Paid total", paid_total_by_bucket, "credits", "pair"),
        ("Unpaid", unpaid_credit_by_bucket, "unpaid", "pair"),
        ("General total", general_by_bucket, "total", "pair"),
    ]
    column_titles = {
        "Cash": "POS cash payments on sale receipts",
        "M-Pesa": "POS M-Pesa payments on sale receipts",
        "Card": "POS card payments on sale receipts",
        "Paid cash": "Credit account collections paid in cash this bucket",
        "Paid M-Pesa": "Credit account collections paid via M-Pesa this bucket",
        "Paid card": "Credit account collections paid via card this bucket",
        "Paid total": "Paid cash + Paid M-Pesa + Paid card",
        "Unpaid": "Outstanding balance on open credit receipts this bucket",
        "General total": "Cash + M-Pesa + Card + Paid total + Unpaid",
    }

    columns = [config["column_label"]]
    for label, _by_bucket, band, cell_kind in metric_maps:
        band_start = label in {
            "Cash",
            "M-Pesa",
            "Card",
            "Paid cash",
            "Unpaid",
            "General total",
        }
        if cell_kind == "money":
            columns.append(
                {
                    "label": label,
                    "title": column_titles.get(label, ""),
                    "band": band,
                    "band_start": band_start,
                    "money": True,
                }
            )
        else:
            columns.append(
                {
                    "label": label,
                    "pair": True,
                    "pair_qty": "Docs",
                    "pair_amt": "Amt",
                    "total": label == "General total",
                    "band": band,
                    "band_start": band_start,
                    "title": column_titles.get(label, ""),
                }
            )
    columns.extend(
        [
            {
                "label": "Stock",
                "title": "Buying cost of items sold",
                "band": "result",
                "band_start": True,
            },
            {
                "label": "Expected profit",
                "title": "Selling value − stock (ex-tax, this bucket)",
                "band": "result",
            },
            {
                "label": "Actual profit",
                "title": "Cash + M-Pesa + Card + Paid total − stock (this bucket)",
                "band": "result",
            },
        ]
    )

    def _metric_cell(cell_kind: str, qty, amount):
        if cell_kind == "money":
            return _money_cell(amount, title=_money_ksh(amount))
        return _qty_amount_cell(
            qty,
            amount,
            title=f"{int(qty or 0)} receipts · {_money_ksh(amount)}",
        )

    table_rows = []
    for label in labels:
        total_docs, total_amt = general_by_bucket.get(label, (0, _zero()))
        unpaid_docs, unpaid_amt = unpaid_credit_by_bucket.get(label, (0, _zero()))
        row = trading.get(label) or {}
        selling = Decimal(row.get("value") or 0)
        stock = Decimal(row.get("cogs") or 0)
        paid_amt = paid_total_by_bucket.get(label, (0, _zero()))[1]
        if (
            not total_docs
            and not unpaid_docs
            and total_amt == 0
            and unpaid_amt == 0
            and selling == 0
            and stock == 0
            and paid_amt == 0
        ):
            continue
        collected = (
            cash_by_bucket.get(label, (0, _zero()))[1]
            + mpesa_by_bucket.get(label, (0, _zero()))[1]
            + card_by_bucket.get(label, (0, _zero()))[1]
            + paid_amt
        )
        expected_profit = selling - stock
        actual_profit = collected - stock
        expected_margin = (
            ((expected_profit / selling) * Decimal("100")).quantize(Decimal("0.1"))
            if selling > 0
            else _zero()
        )
        actual_margin = (
            ((actual_profit / collected) * Decimal("100")).quantize(Decimal("0.1"))
            if collected > 0
            else _zero()
        )
        cells = [label]
        for _col, by_bucket, _band, cell_kind in metric_maps:
            qty, amount = by_bucket.get(label, (0, _zero()))
            cells.append(_metric_cell(cell_kind, qty, amount))
        cells.append(_money_cell(stock, title=f"Stock value {_money_ksh(stock)}"))
        cells.append(
            _money_cell(
                expected_profit,
                tone="good" if expected_profit >= 0 else "bad",
                title=(
                    f"Expected profit {_money_ksh(expected_profit)} · "
                    f"margin {expected_margin}%"
                ),
            )
        )
        cells.append(
            _money_cell(
                actual_profit,
                tone="good" if actual_profit >= 0 else "bad",
                title=(
                    f"Actual profit {_money_ksh(actual_profit)} · "
                    f"margin {actual_margin}%"
                ),
            )
        )
        table_rows.append(cells)

    return _table(
        config["title"],
        columns,
        table_rows,
        empty=config["empty"],
        shop_grid=True,
        footnote=(
            f"{config['title']} for the selected range. "
            "Cash, M-Pesa and Card are POS sale payments; "
            "Paid cash / Paid M-Pesa / Paid card are credit account collections in this bucket; "
            "Paid total = Paid cash + Paid M-Pesa + Paid card; "
            "Unpaid = open credit balances; "
            "General total = Cash + M-Pesa + Card + Paid total + Unpaid; "
            "Expected profit = selling value − stock; "
            "Actual profit = Cash + M-Pesa + Card + Paid total − stock."
        ),
    )


def _build_sales(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    flags = filters.get("pos_flags") or _pos_feature_flags()
    sales, _prev, credits, *_rest = _common_receipt_sets(filters)

    money_field = DecimalField(max_digits=14, decimal_places=2)
    zero_money = Value(0, output_field=money_field)
    cash_expr = Case(
        When(settled_from_credit=False, then=F("cash_amount")),
        default=zero_money,
        output_field=money_field,
    )
    mpesa_expr = Case(
        When(settled_from_credit=False, then=F("mpesa_amount")),
        default=zero_money,
        output_field=money_field,
    )
    card_expr = Case(
        When(settled_from_credit=False, then=F("card_amount")),
        default=zero_money,
        output_field=money_field,
    )
    unpaid_due_expr = Greatest(
        ExpressionWrapper(
            F("total") - F("amount_paid"),
            output_field=money_field,
        ),
        zero_money,
    )

    total_by_shop: dict[int, tuple[int, Decimal]] = {}
    cash_by_shop: dict[int, tuple[int, Decimal]] = {}
    mpesa_by_shop: dict[int, tuple[int, Decimal]] = {}
    card_by_shop: dict[int, tuple[int, Decimal]] = {}
    unpaid_credit_by_shop: dict[int, tuple[int, Decimal]] = {}
    if flags["credit_collections"]:
        (
            paid_cash_by_shop,
            paid_mpesa_by_shop,
            paid_card_by_shop,
        ) = _credit_payment_totals_by_shop(
            shop_ids=shop_ids,
            start=start,
            end=end,
        )
    else:
        paid_cash_by_shop, paid_mpesa_by_shop, paid_card_by_shop = {}, {}, {}
    for row in sales.values("shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
        cash=Coalesce(Sum(cash_expr), _zero()),
        mpesa=Coalesce(Sum(mpesa_expr), _zero()),
        card=Coalesce(Sum(card_expr), _zero()),
    ):
        shop_id = row["shop_id"]
        total_by_shop[shop_id] = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        cash_by_shop[shop_id] = (0, Decimal(row["cash"] or 0))
        mpesa_by_shop[shop_id] = (0, Decimal(row["mpesa"] or 0))
        card_by_shop[shop_id] = (0, Decimal(row["card"] or 0))

    if flags["credit"]:
        for row in credits.values("shop_id").annotate(
            unpaid_docs=Count("id", filter=Q(total__gt=F("amount_paid"))),
            unpaid=Coalesce(Sum(unpaid_due_expr), _zero()),
        ):
            unpaid_credit_by_shop[row["shop_id"]] = (
                int(row["unpaid_docs"] or 0),
                Decimal(row["unpaid"] or 0),
            )

    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.SALE],
        receipt_status=filters.get("receipt_status"),
    )
    stock_by_shop: dict[int, Decimal] = {}
    selling_by_shop: dict[int, Decimal] = {}
    expected_profit_by_shop: dict[int, Decimal] = {}
    actual_profit_by_shop: dict[int, Decimal] = {}
    paid_total_by_shop: dict[int, tuple[int, Decimal]] = {}
    general_by_shop: dict[int, tuple[int, Decimal]] = {}
    for shop in shops:
        row = trading.get(shop.pk) or {}
        selling = Decimal(row.get("value") or 0)
        stock = Decimal(row.get("cogs") or 0)
        cash_amt = (
            cash_by_shop.get(shop.pk, (0, _zero()))[1] if flags["cash"] else _zero()
        )
        mpesa_amt = (
            mpesa_by_shop.get(shop.pk, (0, _zero()))[1] if flags["mpesa"] else _zero()
        )
        card_amt = (
            card_by_shop.get(shop.pk, (0, _zero()))[1] if flags["card"] else _zero()
        )
        paid_cash_docs, paid_cash_amt = paid_cash_by_shop.get(shop.pk, (0, _zero()))
        paid_mpesa_docs, paid_mpesa_amt = paid_mpesa_by_shop.get(shop.pk, (0, _zero()))
        paid_card_docs, paid_card_amt = paid_card_by_shop.get(shop.pk, (0, _zero()))
        unpaid_docs, unpaid_amt = unpaid_credit_by_shop.get(shop.pk, (0, _zero()))
        sale_docs = total_by_shop.get(shop.pk, (0, _zero()))[0]
        paid_amt = (
            Decimal(paid_cash_amt or 0)
            + Decimal(paid_mpesa_amt or 0)
            + Decimal(paid_card_amt or 0)
        )
        collected = (
            Decimal(cash_amt or 0)
            + Decimal(mpesa_amt or 0)
            + Decimal(card_amt or 0)
            + paid_amt
        )
        general_amt = collected + Decimal(unpaid_amt or 0)
        selling_by_shop[shop.pk] = selling
        stock_by_shop[shop.pk] = stock
        expected_profit_by_shop[shop.pk] = selling - stock
        actual_profit_by_shop[shop.pk] = collected - stock
        paid_total_by_shop[shop.pk] = (
            int(paid_cash_docs or 0)
            + int(paid_mpesa_docs or 0)
            + int(paid_card_docs or 0),
            paid_amt,
        )
        general_by_shop[shop.pk] = (
            int(sale_docs or 0) + int(unpaid_docs or 0),
            general_amt,
        )

    metric_maps = [
        ("Cash", cash_by_shop, "cash", "money"),
        ("M-Pesa", mpesa_by_shop, "mpesa", "money"),
        ("Card", card_by_shop, "card", "money"),
        ("Paid cash", paid_cash_by_shop, "credits", "money"),
        ("Paid M-Pesa", paid_mpesa_by_shop, "credits", "money"),
        ("Paid card", paid_card_by_shop, "credits", "money"),
        ("Paid total", paid_total_by_shop, "credits", "pair"),
        ("Unpaid", unpaid_credit_by_shop, "unpaid", "pair"),
        ("General total", general_by_shop, "total", "pair"),
    ]
    column_titles = {
        "Cash": "POS cash payments on sale receipts",
        "M-Pesa": "POS M-Pesa payments on sale receipts",
        "Card": "POS card payments on sale receipts",
        "Paid cash": "Credit account collections paid in cash this period",
        "Paid M-Pesa": "Credit account collections paid via M-Pesa this period",
        "Paid card": "Credit account collections paid via card this period",
        "Paid total": "Paid cash + Paid M-Pesa + Paid card",
        "Unpaid": "Outstanding balance on open credit receipts this period",
        "General total": "Cash + M-Pesa + Card + Paid total + Unpaid",
    }

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -general_by_shop.get(shop.pk, (0, _zero()))[1],
            shop.name.lower(),
        ),
    )

    columns = ["Shop"]
    for label, _by_shop, band, cell_kind in metric_maps:
        band_start = label in {
            "Cash",
            "M-Pesa",
            "Card",
            "Paid cash",
            "Unpaid",
            "General total",
        }
        if cell_kind == "money":
            columns.append(
                {
                    "label": label,
                    "title": column_titles.get(label, ""),
                    "band": band,
                    "band_start": band_start,
                    "money": True,
                }
            )
        else:
            columns.append(
                {
                    "label": label,
                    "pair": True,
                    "pair_qty": "Docs",
                    "pair_amt": "Amt",
                    "total": label == "General total",
                    "band": band,
                    "band_start": band_start,
                    "title": column_titles.get(label, ""),
                }
            )
    columns.extend(
        [
            {
                "label": "Stock",
                "title": "Buying cost of items sold",
                "band": "result",
                "band_start": True,
            },
            {
                "label": "Expected profit",
                "title": "Selling value − stock (ex-tax, this period)",
                "band": "result",
            },
            {
                "label": "Actual profit",
                "title": "Cash + M-Pesa + Card + Paid total − stock (this period)",
                "band": "result",
            },
        ]
    )

    def _metric_cell(cell_kind: str, qty, amount):
        if cell_kind == "money":
            return _money_cell(amount, title=_money_ksh(amount))
        return _qty_amount_cell(
            qty,
            amount,
            title=f"{int(qty or 0)} receipts · {_money_ksh(amount)}",
        )

    table_rows = []
    for shop in shops_sorted:
        cells = [shop.name]
        for _label, by_shop, _band, cell_kind in metric_maps:
            qty, amount = by_shop.get(shop.pk, (0, _zero()))
            cells.append(_metric_cell(cell_kind, qty, amount))
        stock = stock_by_shop.get(shop.pk, _zero())
        expected_profit = expected_profit_by_shop.get(shop.pk, _zero())
        actual_profit = actual_profit_by_shop.get(shop.pk, _zero())
        revenue = selling_by_shop.get(shop.pk, _zero())
        collected = (
            cash_by_shop.get(shop.pk, (0, _zero()))[1]
            + mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
            + card_by_shop.get(shop.pk, (0, _zero()))[1]
            + paid_total_by_shop.get(shop.pk, (0, _zero()))[1]
        )
        expected_margin = (
            ((expected_profit / revenue) * Decimal("100")).quantize(Decimal("0.1"))
            if revenue > 0
            else _zero()
        )
        actual_margin = (
            ((actual_profit / collected) * Decimal("100")).quantize(Decimal("0.1"))
            if collected > 0
            else _zero()
        )
        cells.append(_money_cell(stock, title=f"Stock value {_money_ksh(stock)}"))
        cells.append(
            _money_cell(
                expected_profit,
                tone="good" if expected_profit >= 0 else "bad",
                title=(
                    f"Expected profit {_money_ksh(expected_profit)} · "
                    f"margin {expected_margin}%"
                ),
            )
        )
        cells.append(
            _money_cell(
                actual_profit,
                tone="good" if actual_profit >= 0 else "bad",
                title=(
                    f"Actual profit {_money_ksh(actual_profit)} · "
                    f"margin {actual_margin}%"
                ),
            )
        )
        table_rows.append(cells)

    total_docs = 0
    total_amount = _zero()
    cash_amount = _zero()
    mpesa_amount = _zero()
    card_amount = _zero()
    paid_cash_amount = _zero()
    paid_mpesa_amount = _zero()
    paid_card_amount = _zero()
    unpaid_credit_amount = _zero()
    for shop in shops_sorted:
        qty, amount = general_by_shop.get(shop.pk, (0, _zero()))
        total_docs += int(qty or 0)
        total_amount += Decimal(amount or 0)
        cash_amount += cash_by_shop.get(shop.pk, (0, _zero()))[1]
        mpesa_amount += mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
        card_amount += card_by_shop.get(shop.pk, (0, _zero()))[1]
        paid_cash_amount += paid_cash_by_shop.get(shop.pk, (0, _zero()))[1]
        paid_mpesa_amount += paid_mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
        paid_card_amount += paid_card_by_shop.get(shop.pk, (0, _zero()))[1]
        unpaid_credit_amount += unpaid_credit_by_shop.get(shop.pk, (0, _zero()))[1]
    total_stock = sum((stock_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero())
    total_selling = sum(
        (selling_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero()
    )
    paid_credit_amount = paid_cash_amount + paid_mpesa_amount + paid_card_amount
    total_collected = cash_amount + mpesa_amount + card_amount + paid_credit_amount
    total_expected_profit = total_selling - total_stock
    total_actual_profit = total_collected - total_stock
    expected_margin_pct = (
        ((total_expected_profit / total_selling) * Decimal("100")).quantize(Decimal("0.1"))
        if total_selling > 0
        else _zero()
    )
    actual_margin_pct = (
        ((total_actual_profit / total_collected) * Decimal("100")).quantize(Decimal("0.1"))
        if total_collected > 0
        else _zero()
    )

    if shops_sorted:
        total_cells = ["Total"]
        for _label, by_shop, _band, cell_kind in metric_maps:
            total_qty = 0
            col_amount = _zero()
            for shop in shops_sorted:
                qty, amount = by_shop.get(shop.pk, (0, _zero()))
                total_qty += Decimal(qty or 0)
                col_amount += Decimal(amount or 0)
            total_cells.append(_metric_cell(cell_kind, total_qty, col_amount))
        total_cells.append(
            _money_cell(total_stock, title=f"Stock value {_money_ksh(total_stock)}")
        )
        total_cells.append(
            _money_cell(
                total_expected_profit,
                tone="good" if total_expected_profit >= 0 else "bad",
                title=(
                    f"Expected profit {_money_ksh(total_expected_profit)} · "
                    f"margin {expected_margin_pct}%"
                ),
            )
        )
        total_cells.append(
            _money_cell(
                total_actual_profit,
                tone="good" if total_actual_profit >= 0 else "bad",
                title=(
                    f"Actual profit {_money_ksh(total_actual_profit)} · "
                    f"margin {actual_margin_pct}%"
                ),
            )
        )
        table_rows.append(total_cells)

    active_shops = sum(
        1
        for shop in shops_sorted
        if general_by_shop.get(shop.pk, (0, _zero()))[0] > 0
        or unpaid_credit_by_shop.get(shop.pk, (0, _zero()))[0] > 0
        or paid_total_by_shop.get(shop.pk, (0, _zero()))[0] > 0
    )

    page = {
        "headline": "Sales",
        "lead": (
            "Sale receipts by shop — cash, M-Pesa, card, paid cash / M-Pesa / card, unpaid, "
            "general total, stock, expected profit, and actual profit. "
            "Day shows hourly, month shows daily, year shows monthly."
        ),
        "alerts": [],
        "summary_board": _sales_summary_board(
            total_amount=total_amount,
            total_docs=total_docs,
            cash_amount=cash_amount,
            mpesa_amount=mpesa_amount,
            card_amount=card_amount,
            paid_cash_amount=paid_cash_amount,
            paid_mpesa_amount=paid_mpesa_amount,
            paid_card_amount=paid_card_amount,
            unpaid_credit_amount=unpaid_credit_amount,
            stock_amount=total_stock,
            expected_profit_amount=total_expected_profit,
            actual_profit_amount=total_actual_profit,
            active_shops=active_shops,
            shop_count=len(shops_sorted),
        ),
        "insights": [],
        "tables": [
            _table(
                "Sales by shop",
                columns,
                table_rows,
                empty="No sales for selected shops and period.",
                shop_grid=True,
                footnote=(
                    "Cash, M-Pesa and Card are POS sale payments. "
                    "Paid cash / Paid M-Pesa / Paid card are credit account collections this period. "
                    "Paid total = Paid cash + Paid M-Pesa + Paid card. "
                    "Unpaid = open credit balances this period. "
                    "General total = Cash + M-Pesa + Card + Paid total + Unpaid. "
                    "Expected profit = selling value − stock. "
                    "Actual profit = Cash + M-Pesa + Card + Paid total − stock."
                ),
            )
        ],
    }
    breakdown = _sales_breakdown_table(
        filters=filters,
        shop_ids=shop_ids,
        start=start,
        end=end,
        sales=sales,
        credits=credits,
    )
    if breakdown is not None:
        page["tables"].append(breakdown)
    return page


def _build_items(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]

    trading = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.SALE, ShopReceiptKind.CREDIT],
        receipt_status=filters.get("receipt_status"),
    )
    sold_lines = (
        ShopReceiptLine.objects.filter(receipt__in=trading)
        .values("item_id", "item_name", "receipt__shop_id")
        .annotate(
            units=Coalesce(Sum(F("quantity") - F("returned_quantity")), _zero()),
            value=Coalesce(
                Sum(
                    ExpressionWrapper(
                        (F("quantity") - F("returned_quantity")) * F("unit_price"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                _zero(),
            ),
            cogs=Coalesce(
                Sum(
                    ExpressionWrapper(
                        (F("quantity") - F("returned_quantity")) * F("unit_cost"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    )
                ),
                _zero(),
            ),
        )
    )

    # item_id -> {name, by_shop: {shop_id: (units, value, cogs)}, totals}
    by_item: dict[int | None, dict] = {}
    for row in sold_lines:
        units = Decimal(row["units"] or 0)
        value = Decimal(row["value"] or 0)
        cogs = Decimal(row["cogs"] or 0)
        if units <= 0 and not value:
            continue
        item_id = row["item_id"]
        entry = by_item.get(item_id)
        if entry is None:
            entry = {
                "name": row["item_name"] or "Item",
                "by_shop": {},
                "total_units": 0,
                "total_value": _zero(),
                "total_cogs": _zero(),
            }
            by_item[item_id] = entry
        elif row["item_name"] and not entry["name"]:
            entry["name"] = row["item_name"]
        shop_id = row["receipt__shop_id"]
        prev_units, prev_value, prev_cogs = entry["by_shop"].get(
            shop_id, (0, _zero(), _zero())
        )
        entry["by_shop"][shop_id] = (
            prev_units + units,
            prev_value + value,
            prev_cogs + cogs,
        )
        entry["total_units"] += units
        entry["total_value"] += value
        entry["total_cogs"] += cogs

    # Fallback COGS for lines with zero unit_cost — apply at item total level.
    missing = list(
        ShopReceiptLine.objects.filter(receipt__in=trading, unit_cost=0)
        .exclude(item_id__isnull=True)
        .values("item_id", "quantity", "returned_quantity")
    )
    if missing:
        from items.services import last_buying_prices_for_items

        prices = last_buying_prices_for_items(
            {row["item_id"] for row in missing if row["item_id"]}
        )
        for row in missing:
            entry = by_item.get(row["item_id"])
            if entry is None:
                continue
            unit = Decimal(prices.get(row["item_id"]) or 0)
            if unit <= 0:
                continue
            remaining = max(
                Decimal("0"),
                Decimal(row["quantity"] or 0)
                - Decimal(row["returned_quantity"] or 0),
            )
            if remaining <= 0:
                continue
            entry["total_cogs"] += (unit * remaining).quantize(Decimal("0.01"))

    sold_rows = []
    loss_makers = 0
    for entry in sorted(
        by_item.values(),
        key=lambda row: (
            -(row["total_value"] - row["total_cogs"]),
            row["name"].lower(),
        ),
    ):
        gross = entry["total_value"] - entry["total_cogs"]
        margin = (
            ((gross / entry["total_value"]) * Decimal("100")).quantize(Decimal("0.1"))
            if entry["total_value"] > 0
            else _zero()
        )
        if gross < 0:
            loss_makers += 1
        cells = [entry["name"]]
        for shop in shops:
            shop_units, shop_value, _shop_cogs = entry["by_shop"].get(
                shop.pk, (0, _zero(), _zero())
            )
            cells.append(_qty_amount_cell(shop_units, shop_value))
        cells.append(_qty_amount_cell(entry["total_units"], entry["total_value"]))
        cells.append(_money_cell(entry["total_cogs"]))
        cells.append(
            _money_cell(gross, tone="good" if gross >= 0 else "bad")
        )
        cells.append(
            _pct_cell(margin, tone="good" if margin >= 0 else "bad")
        )
        sold_rows.append(cells)

    columns = ["Item"]
    for index, shop in enumerate(shops):
        col = _shop_col(shop, pair=True)
        col["band"] = "flow"
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.extend(
        [
            {**_pair_total_col(), "band": "total", "band_start": True},
            {"label": "COGS", "band": "result", "band_start": True},
            {"label": "Gross", "band": "result"},
            {"label": "Margin", "band": "result"},
        ]
    )

    total_sales = sum((e["total_value"] for e in by_item.values()), _zero())
    total_cogs = sum((e["total_cogs"] for e in by_item.values()), _zero())
    total_gross = total_sales - total_cogs
    overall_margin = (
        ((total_gross / total_sales) * Decimal("100")).quantize(Decimal("0.1"))
        if total_sales > 0
        else _zero()
    )

    alerts = []
    if loss_makers:
        alerts.append(
            _alert(
                "warning",
                "Items sold below cost",
                f"{loss_makers} item{'s' if loss_makers != 1 else ''} show negative "
                f"gross profit in this period.",
                "Raise selling price or check buying cost.",
            )
        )

    return {
        "headline": "Items sold",
        "lead": "Ranked by gross profit. Margin uses stamped cost (or last buy fallback).",
        "alerts": alerts,
        "show_search": True,
        "search_placeholder": "Search items…",
        "search_empty": "No items match that search.",
        "summary_board": _items_summary_board(
            total_sales=total_sales,
            total_cogs=total_cogs,
            total_gross=total_gross,
            overall_margin=overall_margin,
            loss_makers=loss_makers,
            item_count=len(by_item),
        ),
        "insights": [],
        "tables": [
            _table(
                "All items sold by shop",
                columns,
                sold_rows,
                empty="No items sold in this period.",
                footnote="Includes sales and credits. Qty/Amt are net of returns.",
                shop_grid=True,
                searchable=True,
                table_class="ax-table--items",
            )
        ],
    }


_ITEM_REPORT_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_ITEM_REPORT_WEEKDAY_FULL = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _local_report_day(value):
    """Local calendar date — bucketing stays in Python because MySQL TruncDate
    on related DateTimeFields can return NULL on some MariaDB/XAMPP setups."""
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value).date()


def _num_cell(label, *, title: str = "") -> dict:
    """Plain right-aligned number cell (shares the money cell styling)."""
    return {"kind": "money", "label": label, "title": title or label}


def _item_report_best_day(entry):
    """Best-selling weekday for a day/weekday bucket set -> (label, title)."""
    weekdays = entry["weekdays"]
    if not weekdays:
        return "—", ""
    wd, wd_units = max(weekdays.items(), key=lambda pair: (pair[1], -pair[0]))
    wd_days = sum(1 for day in entry["days"] if day.weekday() == wd)
    title = (
        f"{_ITEM_REPORT_WEEKDAY_FULL[wd]}s — {_qty_label(wd_units)} units "
        f"across {wd_days} {_ITEM_REPORT_WEEKDAY_FULL[wd]}"
        f"{'s' if wd_days != 1 else ''}"
    )
    if entry["day_units"]:
        top_day, top_units = max(
            entry["day_units"].items(), key=lambda pair: (pair[1], pair[0])
        )
        title += (
            f" · top day {top_day.strftime('%d %b %Y')} "
            f"({_qty_label(top_units)} units)"
        )
    return _ITEM_REPORT_WEEKDAY_SHORT[wd], title


def _item_report_stats(units, value, cogs, days_count) -> dict:
    """Per-selling-day averages + profit share for one item x shop cell."""
    gross = Decimal(value) - Decimal(cogs)
    return {
        "gross": gross,
        "avg_qty": (
            (Decimal(units) / days_count).quantize(Decimal("0.1"))
            if days_count
            else _zero()
        ),
        "avg_profit": (
            (gross / days_count).quantize(Decimal("0.01"))
            if days_count
            else _zero()
        ),
        "pct": (
            ((gross / Decimal(value)) * Decimal("100")).quantize(Decimal("0.1"))
            if value > 0
            else _zero()
        ),
    }


def _item_report_amount(value) -> str:
    """Dense profit/day cell — whole units on five-figure averages so the
    matrix and PDF never wrap a number mid-digit (full value stays in title)."""
    amount = Decimal(value or 0)
    if abs(amount) >= Decimal("10000"):
        return f"{int(amount.quantize(Decimal('1'))):,}"
    return _money_dense(amount)


def _item_report_pct_label(pct) -> str:
    pct = Decimal(pct or 0)
    if abs(pct) >= Decimal("1000"):
        return f"{int(pct.quantize(Decimal('1'))):,}%"
    return f"{pct}%"


def _item_report_quad(*, stats, day, title) -> dict:
    return {
        "kind": "quad",
        "qty": _qty_label(stats["avg_qty"]),
        "amount": _item_report_amount(stats["avg_profit"]),
        "pct": _item_report_pct_label(stats["pct"]),
        "day": day,
        "tone": "good" if stats["gross"] >= 0 else "bad",
        "title": title,
    }


def _item_report_empty_quad(label) -> dict:
    return {
        "kind": "empty",
        "title": f"No sales at {label}",
    }


def _item_report_row(*, meta, cat, shops, shop_name, grand_value) -> dict:
    """One matrix row: item + sales share + per-shop quad cells + total."""
    pairs = dict(meta["pairs"])
    total_units = _zero()
    total_value = _zero()
    total_cogs = _zero()
    merged = {"days": set(), "day_units": {}, "weekdays": {}}
    for entry in pairs.values():
        total_units += entry["units"]
        total_value += entry["value"]
        total_cogs += entry["cogs"]
        merged["days"] |= entry["days"]
        for day, day_qty in entry["day_units"].items():
            merged["day_units"][day] = merged["day_units"].get(day, _zero()) + day_qty
        for wd, wd_qty in entry["weekdays"].items():
            merged["weekdays"][wd] = merged["weekdays"].get(wd, _zero()) + wd_qty

    share = (
        ((total_value / grand_value) * Decimal("100")).quantize(Decimal("0.1"))
        if grand_value > 0
        else _zero()
    )
    shop_count = sum(1 for entry in pairs.values() if entry["days"])
    cells = [
        {
            "label": meta["name"],
            "sub": f"{shop_count} shop{'s' if shop_count != 1 else ''}",
        },
        {
            "kind": "share",
            "label": f"{share}%",
            "pct": str(share),
            "title": (
                f"{meta['name']} — {_money_ksh(total_value)} of "
                f"{_money_ksh(grand_value)} total sales"
            ),
        },
    ]
    for shop in shops:
        entry = pairs.get(shop.pk)
        label = shop_name.get(shop.pk) or "Shop"
        if entry is None or not entry["days"]:
            cells.append(_item_report_empty_quad(label))
            continue
        stats = _item_report_stats(
            entry["units"], entry["value"], entry["cogs"], len(entry["days"])
        )
        day, day_title = _item_report_best_day(entry)
        cells.append(
            _item_report_quad(
                stats=stats,
                day=day,
                title=(
                    f"{label} — avg {_qty_label(stats['avg_qty'])} units/day"
                    f" · avg {_money_ksh(stats['avg_profit'])}/day"
                    f" · {stats['pct']}% profit"
                    f"{f' · {day_title}' if day_title else ''}"
                ),
            )
        )
    totals = _item_report_stats(
        total_units, total_value, total_cogs, len(merged["days"])
    )
    day, day_title = _item_report_best_day(merged)
    cells.append(
        _item_report_quad(
            stats=totals,
            day=day,
            title=(
                f"All shops — avg {_qty_label(totals['avg_qty'])} units/day"
                f" · avg {_money_ksh(totals['avg_profit'])}/day"
                f" · {totals['pct']}% profit"
                f"{f' · {day_title}' if day_title else ''}"
            ),
        )
    )
    return {
        "cells": cells,
        "search_text": f"{meta['name']} {cat}",
        "_cat": cat,
        "_value": total_value,
        "_cogs": total_cogs,
    }


def _build_item_report(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    shop_name = {
        shop.pk: ((getattr(shop, "name", None) or "").strip() or "Shop")
        for shop in shops
    }
    start, end = filters["start"], filters["end"]

    trading = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.SALE, ShopReceiptKind.CREDIT],
        receipt_status=filters.get("receipt_status"),
    )
    lines = ShopReceiptLine.objects.filter(receipt__in=trading).values(
        "item_id",
        "item_name",
        "receipt__shop_id",
        "receipt__created_at",
        "quantity",
        "returned_quantity",
        "unit_price",
        "unit_cost",
    )

    # (item_id, shop_id) -> totals + local day/weekday buckets.
    by_pair: dict[tuple, dict] = {}
    fallback_qty: dict[tuple, Decimal] = {}
    for row in lines:
        units = Decimal(row["quantity"] or 0) - Decimal(
            row["returned_quantity"] or 0
        )
        unit_price = Decimal(row["unit_price"] or 0)
        unit_cost = Decimal(row["unit_cost"] or 0)
        value = (units * unit_price).quantize(Decimal("0.01"))
        if units <= 0 and not value:
            continue
        key = (row["item_id"], row["receipt__shop_id"])
        entry = by_pair.get(key)
        if entry is None:
            entry = {
                "name": row["item_name"] or "Item",
                "units": _zero(),
                "value": _zero(),
                "cogs": _zero(),
                "days": set(),
                "day_units": {},
                "weekdays": {},
            }
            by_pair[key] = entry
        elif row["item_name"] and not entry["name"]:
            entry["name"] = row["item_name"]
        entry["units"] += units
        entry["value"] += value
        if unit_cost > 0:
            entry["cogs"] += (units * unit_cost).quantize(Decimal("0.01"))
        else:
            fallback_qty[key] = fallback_qty.get(key, _zero()) + units
        if units <= 0:
            continue
        day = _local_report_day(row["receipt__created_at"])
        if day is None:
            continue
        entry["days"].add(day)
        entry["day_units"][day] = entry["day_units"].get(day, _zero()) + units
        wd = day.weekday()
        entry["weekdays"][wd] = entry["weekdays"].get(wd, _zero()) + units

    # Fallback COGS for lines with zero unit_cost, per item x shop.
    if fallback_qty:
        from items.services import last_buying_prices_for_items

        prices = last_buying_prices_for_items(
            {key[0] for key in fallback_qty if key[0]}
        )
        for key, qty in fallback_qty.items():
            if qty <= 0 or not key[0]:
                continue
            unit = Decimal(prices.get(key[0]) or 0)
            if unit <= 0:
                continue
            entry = by_pair.get(key)
            if entry is not None:
                entry["cogs"] += (unit * qty).quantize(Decimal("0.01"))

    item_ids = {key[0] for key in by_pair if key[0]}
    categories = (
        dict(Item.objects.filter(pk__in=item_ids).values_list("pk", "category"))
        if item_ids
        else {}
    )

    # Server-side search — same matching as the on-page table filter so the
    # PDF download honours what the user typed.
    search_query = (filters.get("search_query") or "").strip().lower()
    tokens = [token for token in search_query.split() if token]
    if tokens:
        def _pair_matches(item_id, name):
            cat = (categories.get(item_id) or "").strip() or "Uncategorised"
            haystack = f"{name or ''} {cat}".lower()
            return all(token in haystack for token in tokens)

        by_pair = {
            key: entry
            for key, entry in by_pair.items()
            if _pair_matches(key[0], entry["name"])
        }
        item_ids = {key[0] for key in by_pair if key[0]}

    # Items grouped by category and ranked by profit; each item gets one row
    # with a quad cell per shop (avg qty · avg profit · % profit · day).
    item_gross: dict = {}
    cat_gross: dict = {}
    cat_value: dict = {}
    item_meta: dict = {}
    grand_value = _zero()
    for (item_id, shop_id), entry in by_pair.items():
        gross = entry["value"] - entry["cogs"]
        item_gross[item_id] = item_gross.get(item_id, _zero()) + gross
        grand_value += entry["value"]
        meta = item_meta.get(item_id)
        if meta is None:
            cat = (categories.get(item_id) or "").strip() or "Uncategorised"
            meta = {"name": entry["name"], "cat": cat, "pairs": []}
            item_meta[item_id] = meta
            cat_gross.setdefault(cat, _zero())
            cat_value.setdefault(cat, _zero())
        meta["pairs"].append((shop_id, entry))
        cat_gross[meta["cat"]] += gross
        cat_value[meta["cat"]] += entry["value"]

    rows = []
    total_sales = _zero()
    total_cogs = _zero()
    for cat in sorted(cat_gross, key=lambda c: (-cat_gross[c], c.lower())):
        cat_item_ids = sorted(
            (iid for iid, m in item_meta.items() if m["cat"] == cat),
            key=lambda iid: (
                -item_gross[iid],
                (item_meta[iid]["name"] or "").lower(),
            ),
        )
        item_total = len(cat_item_ids)
        cat_share = (
            ((cat_value[cat] / grand_value) * Decimal("100")).quantize(
                Decimal("0.1")
            )
            if grand_value > 0
            else _zero()
        )
        rows.append(
            {
                "group_header": True,
                "label": (
                    f"{cat} — {item_total} item{'s' if item_total != 1 else ''}"
                    f" · {cat_share}% of sales"
                    f" · {_money_ksh(cat_gross[cat])} profit"
                ),
                "search_text": cat,
            }
        )
        for iid in cat_item_ids:
            rows.append(
                _item_report_row(
                    meta=item_meta[iid],
                    cat=cat,
                    shops=shops,
                    shop_name=shop_name,
                    grand_value=grand_value,
                )
            )
    for row in rows:
        if row.get("group_header"):
            continue
        total_sales += row["_value"]
        total_cogs += row["_cogs"]

    # Trailing totals row — a shop's selling days are the union of the days
    # any listed item sold there; grand totals merge every pair.
    shop_totals: dict = {}
    grand = {
        "units": _zero(),
        "value": _zero(),
        "cogs": _zero(),
        "days": set(),
        "day_units": {},
        "weekdays": {},
    }
    for (_, sid), entry in by_pair.items():
        bucket = shop_totals.setdefault(
            sid,
            {
                "units": _zero(),
                "value": _zero(),
                "cogs": _zero(),
                "days": set(),
                "day_units": {},
                "weekdays": {},
            },
        )
        for field in ("units", "value", "cogs"):
            bucket[field] += entry[field]
            grand[field] += entry[field]
        for scope in (bucket, grand):
            scope["days"] |= entry["days"]
            for day, day_qty in entry["day_units"].items():
                scope["day_units"][day] = (
                    scope["day_units"].get(day, _zero()) + day_qty
                )
            for wd, wd_qty in entry["weekdays"].items():
                scope["weekdays"][wd] = (
                    scope["weekdays"].get(wd, _zero()) + wd_qty
                )

    if rows:
        total_cells = [
            {"label": "All items"},
            {
                "kind": "share",
                "label": "100%",
                "pct": "100",
                "title": "All items — full share of total sales",
            },
        ]
        for shop in shops:
            bucket = shop_totals.get(shop.pk)
            label = shop_name.get(shop.pk) or "Shop"
            if bucket is None or not bucket["days"]:
                total_cells.append(_item_report_empty_quad(label))
                continue
            stats = _item_report_stats(
                bucket["units"],
                bucket["value"],
                bucket["cogs"],
                len(bucket["days"]),
            )
            day, day_title = _item_report_best_day(bucket)
            total_cells.append(
                _item_report_quad(
                    stats=stats,
                    day=day,
                    title=(
                        f"{label} — all items avg "
                        f"{_qty_label(stats['avg_qty'])} units/day"
                        f" · avg {_money_ksh(stats['avg_profit'])}/day"
                        f" · {stats['pct']}% profit"
                        f"{f' · {day_title}' if day_title else ''}"
                    ),
                )
            )
        stats = _item_report_stats(
            grand["units"], grand["value"], grand["cogs"], len(grand["days"])
        )
        day, day_title = _item_report_best_day(grand)
        total_cells.append(
            _item_report_quad(
                stats=stats,
                day=day,
                title=(
                    f"All shops — all items avg "
                    f"{_qty_label(stats['avg_qty'])} units/day"
                    f" · avg {_money_ksh(stats['avg_profit'])}/day"
                    f" · {stats['pct']}% profit"
                    f"{f' · {day_title}' if day_title else ''}"
                ),
            )
        )
        rows.append(
            {
                "cells": total_cells,
                "search_text": "all items total",
                "_total": True,
            }
        )

    total_gross = total_sales - total_cogs
    overall_margin = (
        ((total_gross / total_sales) * Decimal("100")).quantize(Decimal("0.1"))
        if total_sales > 0
        else _zero()
    )
    loss_makers = sum(1 for gross in item_gross.values() if gross < 0)

    alerts = []
    if loss_makers:
        alerts.append(
            _alert(
                "warning",
                "Items sold below cost",
                f"{loss_makers} item{'s' if loss_makers != 1 else ''} show negative "
                f"profit in this period.",
                "Raise selling price or check buying cost.",
            )
        )

    board = _items_summary_board(
        total_sales=total_sales,
        total_cogs=total_cogs,
        total_gross=total_gross,
        overall_margin=overall_margin,
        loss_makers=loss_makers,
        item_count=len(item_gross),
    )
    board["hero"]["label"] = "Profit"
    board["hero"]["hint"] = (
        f"Margin {overall_margin}% · {len(item_gross)} "
        f"item{'s' if len(item_gross) != 1 else ''} sold"
    )

    quad_labels = {
        "quad": True,
        "sub_qty": "Qty/day",
        "sub_amt": "Profit/day",
        "sub_pct": "Margin",
        "sub_day": "Top day",
    }
    columns = [
        {"label": "Item"},
        {
            "label": "% of sales",
            "share": True,
            "money": True,
            "title": "Item's share of total sales value",
        },
    ]
    for index, shop in enumerate(shops):
        col = _shop_col(shop)
        col.update({**quad_labels, "band": "flow"})
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append(
        {
            "label": "Total",
            "title": "All selected shops — per selling day",
            **quad_labels,
            "total": True,
            "band": "total",
            "band_start": True,
        }
    )

    return {
        "headline": "Item report",
        "lead": (
            "Ranked by profit — each shop column shows qty/day, profit/day, "
            "margin, and the top-selling weekday."
        ),
        "alerts": alerts,
        "show_search": True,
        "search_placeholder": "Search items or categories…",
        "search_empty": "No rows match that search.",
        "summary_board": board,
        "insights": [],
        "tables": [
            _table(
                "Item report by shop",
                columns,
                rows,
                empty="No items sold in this period.",
                footnote=(
                    "Sales and credits, net of returns. Qty/day and "
                    "profit/day are per selling day in this period. Margin = "
                    "profit ÷ revenue. Top day is the weekday the item sells "
                    "the most at that shop — hover for units and top date."
                ),
                shop_grid=True,
                searchable=True,
            )
        ],
    }


def _build_stock(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]

    stock_rows = list(
        ShopStock.objects.filter(shop_id__in=shop_ids)
        .select_related("item")
        .order_by("item__name")
    )

    by_item: dict[int, dict] = {}
    total_value = _zero()
    zero_cost_qty = 0
    for row in stock_rows:
        item = row.item
        if item is None:
            continue
        qty = Decimal(row.quantity or 0)
        if qty <= 0:
            continue
        avg = Decimal(row.average_cost or 0)
        value = (avg * qty).quantize(Decimal("0.01"))
        if avg <= 0:
            zero_cost_qty += qty
        entry = by_item.get(item.pk)
        if entry is None:
            entry = {
                "name": item.name or "Item",
                "by_shop": {},
                "total_qty": 0,
                "total_value": _zero(),
            }
            by_item[item.pk] = entry
        entry["by_shop"][row.shop_id] = (qty, value)
        entry["total_qty"] += qty
        entry["total_value"] += value
        total_value += value

    table_rows = []
    for entry in sorted(
        by_item.values(),
        key=lambda row: (-row["total_value"], row["name"].lower()),
    ):
        cells = [entry["name"]]
        for shop in shops:
            qty, value = entry["by_shop"].get(shop.pk, (0, _zero()))
            cells.append(_qty_amount_cell(qty, value) if qty else "—")
        cells.append(_qty_amount_cell(entry["total_qty"], entry["total_value"]))
        table_rows.append(cells)

    columns = ["Item"]
    for index, shop in enumerate(shops):
        col = _shop_col(shop, pair=True)
        col["band"] = "flow"
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append({**_pair_total_col(), "band": "total", "band_start": True})

    total_units = sum(
        (e["total_qty"] for e in by_item.values()), Decimal("0")
    )
    item_count = len(by_item)

    purchases = _zero()
    for row in (
        StockMovement.objects.filter(
            shop_id__in=shop_ids,
            movement_type=StockMovementType.IN,
            created_at__gte=start,
            created_at__lt=end,
        )
        .values("shop_id")
        .annotate(
            amount=Coalesce(
                Sum(F("lines__buying_price") * F("lines__quantity")),
                _zero(),
            ),
        )
    ):
        purchases += Decimal(row["amount"] or 0)

    top_buy_item = (
        StockMovementLine.objects.filter(
            movement__shop_id__in=shop_ids,
            movement__movement_type=StockMovementType.IN,
            movement__entry_source=StockEntrySource.BUY_ITEMS,
            movement__created_at__gte=start,
            movement__created_at__lt=end,
            item__isnull=False,
        )
        .values("item_id", "item__name")
        .annotate(transaction_count=Count("movement_id", distinct=True))
        .order_by("-transaction_count", "item__name")
        .first()
    )
    supply_analytics_href = ""
    supply_analytics_label = ""
    if top_buy_item:
        from django.urls import reverse

        from employees.access import role_url_segment

        supply_analytics_href = reverse(
            "employees:analytics_section",
            kwargs={"role_segment": role_url_segment(filters["role"]), "section": "supply"},
        )
        supply_analytics_href += f"?item_id={top_buy_item['item_id']}"
        supply_analytics_label = (
            f"Top buy item: {top_buy_item['item__name']} "
            f"({top_buy_item['transaction_count']} transactions)"
        )

    cogs_total = sum(
        (
            amt
            for _d, amt in _cogs_by_shop_for_period(
                shop_ids=shop_ids, start=start, end=end
            ).values()
        ),
        _zero(),
    )
    shrinkage_total = sum(
        (
            amt
            for _d, amt in _shrinkage_by_shop_for_period(
                shop_ids=shop_ids, start=start, end=end
            ).values()
        ),
        _zero(),
    )
    net_inventory_move = purchases - cogs_total - shrinkage_total

    alerts = []
    if zero_cost_qty > 0:
        alerts.append(
            _alert(
                "warning",
                "Stock without cost",
                f"{_qty_label(zero_cost_qty)} unit(s) on hand have average cost 0 — "
                f"valuation and COGS may be understated.",
                "Record stock-in with buying price, or revalue after next purchase.",
            )
        )

    stock_page_rows, stock_pager = _page_table_rows(
        table_rows, filters, searchable=True
    )
    return {
        "headline": "Stock on hand",
        "lead": (
            "Value = qty x shop average cost. "
            "Period check: purchases - COGS - shrinkage should explain inventory movement."
        ),
        "alerts": alerts,
        "supply_analytics_href": supply_analytics_href,
        "supply_analytics_label": supply_analytics_label,
        "show_search": True,
        "search_placeholder": "Search stock items…",
        "search_empty": "No items match that search.",
        "summary_board": _stock_summary_board(
            total_value=total_value,
            total_units=total_units,
            item_count=item_count,
            purchases=purchases,
            cogs_total=cogs_total,
            shrinkage_total=shrinkage_total,
            net_inventory_move=net_inventory_move,
        ),
        "insights": [],
        "tables": [
            _table(
                "Stock value by shop",
                columns,
                stock_page_rows,
                empty="No stock for selected shops.",
                footnote=(
                    "Qty/Amt cells show quantity and stock value. "
                    "Implied opening is derived (no day-open stock snapshot yet). "
                    "Inter-shop transfers can skew the period bridge."
                ),
                shop_grid=True,
                searchable=True,
                table_class="ax-table--stock",
                pager=stock_pager,
            )
        ],
    }


def _supply_summary_board(
    *,
    item_name: str,
    delivery_count: int,
    unit_count: int,
    supplier_count: int,
    total_spend,
    average_price,
) -> dict:
    spend = Decimal(total_spend or 0)
    avg = Decimal(average_price or 0)
    return {
        "hero": {
            "label": "Buy items item",
            "value": item_name,
            "hint": (
                f"{int(delivery_count or 0)} deliveries · "
                f"{_qty_label(unit_count)} units · "
                f"{int(supplier_count or 0)} supplier"
                f"{'s' if int(supplier_count or 0) != 1 else ''}"
            ),
        },
        "tiles": [
            {
                "label": "Suppliers",
                "value": str(int(supplier_count or 0)),
                "hint": "Distinct suppliers recorded",
                "icon": "truck",
                "tone": "neutral",
            },
            {
                "label": "Deliveries",
                "value": str(int(delivery_count or 0)),
                "hint": "Buy items stock-ins",
                "icon": "package-plus",
                "tone": "flow",
            },
            {
                "label": "Units",
                "value": _qty_label(unit_count),
                "hint": "Total quantity received",
                "icon": "layers",
                "tone": "neutral",
            },
            {
                "label": "Total spend",
                "value": _money_ksh(spend),
                "hint": "Sum of line totals at buying price",
                "icon": "banknote",
                "tone": "cost",
            },
            {
                "label": "Avg unit price",
                "value": _money_ksh(avg),
                "hint": "Weighted by units received",
                "icon": "scale",
                "tone": "good",
            },
        ],
    }


def _delivery_time_score(*, actual_minutes, expected_minutes) -> float | None:
    """0–100 score from accept→complete time vs ETA. On time / early = 100."""
    if actual_minutes is None or expected_minutes is None:
        return None
    try:
        actual = int(actual_minutes)
        expected = int(expected_minutes)
    except (TypeError, ValueError):
        return None
    if expected <= 0 or actual < 0:
        return None
    if actual <= expected:
        return 100.0
    # Linear drop to 0 at 2× ETA.
    return max(0.0, 100.0 * (2.0 * expected - actual) / expected)


def _delivery_rating_payload(scores: list[float]) -> dict:
    """Aggregate timed-delivery scores into a display rating."""
    if not scores:
        return {
            "rating": None,
            "rating_label": "—",
            "rating_stars": None,
            "rating_tone": "",
            "timed_count": 0,
        }
    avg = sum(scores) / len(scores)
    stars = round(avg / 20.0, 1)  # 0–5
    if avg >= 90:
        label, tone = "Excellent", "great"
    elif avg >= 75:
        label, tone = "Good", "good"
    elif avg >= 50:
        label, tone = "Fair", "fair"
    else:
        label, tone = "Slow", "low"
    return {
        "rating": int(round(avg)),
        "rating_label": label,
        "rating_stars": stars,
        "rating_tone": tone,
        "timed_count": len(scores),
    }


def _build_deliveries(filters):
    """Customer delivery jobs — heatmap, pin markers, and rider completion counts."""
    import re
    from collections import defaultdict
    from decimal import Decimal

    from shops.models import ShopDelivery, ShopDeliveryStatus

    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    qs = _within_created_range(
        ShopDelivery.objects.filter(shop_id__in=shop_ids),
        start,
        end,
        lookup="created_at",
    ).select_related(
        "receipt",
        "shop",
        "accepted_by__user",
    )

    done_statuses = (
        ShopDeliveryStatus.DELIVERED,
        ShopDeliveryStatus.DELIVERED_PAID,
        ShopDeliveryStatus.COMPLETED,
    )

    # Plot every active/finished job with a pin — open ones still need riders.
    map_statuses = (
        ShopDeliveryStatus.OPEN,
        ShopDeliveryStatus.ACCEPTED,
        *done_statuses,
    )
    def _coords_for_delivery(row):
        if row.latitude is not None and row.longitude is not None:
            return float(row.latitude), float(row.longitude)
        # Recover pins from maps links when lat/lng columns were left blank.
        url = (row.maps_url or "").strip()
        if not url:
            return None
        match = re.search(
            r"[?&]query=(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
            url,
        )
        if not match:
            match = re.search(
                r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
                url,
            )
        if not match:
            return None
        try:
            return float(match.group(1)), float(match.group(2))
        except (TypeError, ValueError):
            return None

    map_points = []
    for row in qs.filter(status__in=map_statuses):
        coords = _coords_for_delivery(row)
        if coords is None:
            continue
        lat, lng = coords
        map_points.append(
            {
                "id": row.pk,
                "lat": lat,
                "lng": lng,
                "label": row.receipt.receipt_number,
                "client": row.client_name or "Client",
                "phone": row.client_phone or "",
                "location": row.location_text,
                "shop": row.shop.name,
                "status": row.get_status_display(),
                "status_key": row.status,
                "rider": (
                    (
                        row.accepted_by.user.get_full_name()
                        or row.accepted_by.user.username
                    )
                    if row.accepted_by_id
                    else ""
                ),
                "maps_url": row.maps_url or "",
                # Completed jobs weigh denser on the heatmap.
                "weight": 2.0 if row.status in done_statuses else 1.0,
            }
        )

    rider_rows = list(
        qs.filter(accepted_by__isnull=False)
        .values(
            "accepted_by_id",
            "accepted_by__employee_id",
            "accepted_by__user__first_name",
            "accepted_by__user__last_name",
            "accepted_by__user__username",
        )
        .annotate(
            total=Count("id"),
            completed=Count(
                "id",
                filter=Q(status__in=done_statuses),
            ),
            accepted=Count(
                "id",
                filter=Q(status=ShopDeliveryStatus.ACCEPTED),
            ),
        )
        .order_by("-completed", "-total", "accepted_by__employee_id")
    )

    scores_by_rider: dict[int, list[float]] = defaultdict(list)
    minutes_by_rider: dict[int, list[int]] = defaultdict(list)
    for job in qs.filter(
        accepted_by__isnull=False,
        status__in=done_statuses,
    ):
        actual = job.actual_minutes
        score = _delivery_time_score(
            actual_minutes=actual,
            expected_minutes=job.expected_minutes,
        )
        if score is not None:
            scores_by_rider[job.accepted_by_id].append(score)
        if actual is not None:
            minutes_by_rider[job.accepted_by_id].append(int(actual))

    riders = []
    for row in rider_rows:
        first = (row.get("accepted_by__user__first_name") or "").strip()
        last = (row.get("accepted_by__user__last_name") or "").strip()
        username = (row.get("accepted_by__user__username") or "").strip()
        name = f"{first} {last}".strip() or username or "Rider"
        rider_id = row["accepted_by_id"]
        rating = _delivery_rating_payload(scores_by_rider.get(rider_id, []))
        timed_minutes = minutes_by_rider.get(rider_id) or []
        avg_minutes = (
            int(round(sum(timed_minutes) / len(timed_minutes)))
            if timed_minutes
            else None
        )
        riders.append(
            {
                "id": rider_id,
                "employee_id": row.get("accepted_by__employee_id") or "",
                "name": name,
                "total": int(row["total"] or 0),
                "completed": int(row["completed"] or 0),
                "delivered": int(row["completed"] or 0),
                "accepted": int(row["accepted"] or 0),
                "in_progress": int(row["accepted"] or 0),
                "avg_minutes": avg_minutes,
                **rating,
            }
        )

    open_count = qs.filter(status=ShopDeliveryStatus.OPEN).count()
    accepted_count = qs.filter(status=ShopDeliveryStatus.ACCEPTED).count()
    completed_count = qs.filter(status__in=done_statuses).count()
    total_value = qs.aggregate(total=Sum("receipt__total"))["total"] or Decimal("0")

    recent = list(
        qs.select_related("receipt", "shop", "accepted_by__user").order_by(
            "-created_at"
        )[:40]
    )
    recent_rows = []
    for row in recent:
        rider_name = ""
        if row.accepted_by_id:
            rider_name = (
                row.accepted_by.user.get_full_name()
                or row.accepted_by.user.username
            )
        recent_rows.append(
            {
                "id": row.pk,
                "receipt": row.receipt.receipt_number,
                "shop": row.shop.name,
                "client": row.client_name or "—",
                "phone": row.client_phone or "—",
                "location": row.location_text,
                "status": row.get_status_display(),
                "status_key": row.status,
                "rider": rider_name or "—",
                "created_at": row.created_at,
                "maps_url": row.maps_url or "",
                "has_coords": row.has_coordinates(),
                "actual_minutes": row.actual_minutes,
                "eta_minutes": row.expected_minutes,
                "timing_label": row.timing_variance_label or "",
                "timing_tone": row.timing_variance_tone or "",
            }
        )

    return {
        "headline": "Deliveries",
        "lead": (
            "Heatmap and pins for delivery locations, rider breakdown by time rating, "
            "and a recent deliveries list."
        ),
        "period_label": filters.get("period_label") or "",
        "hide_date_filters": False,
        "open_count": open_count,
        "accepted_count": accepted_count,
        "completed_count": completed_count,
        "total_count": open_count + accepted_count + completed_count,
        "total_value": total_value,
        "map_points": map_points,
        "riders": riders,
        "recent_rows": recent_rows,
        "tables": [
            {
                "title": "Riders",
                "columns": [
                    {"key": "name", "label": "Rider"},
                    {"key": "employee_id", "label": "ID"},
                    {"key": "delivered", "label": "Delivered"},
                    {"key": "in_progress", "label": "In progress"},
                    {"key": "rating_label", "label": "Delivery rating"},
                    {"key": "avg_minutes", "label": "Avg minutes"},
                ],
                "rows": [
                    {
                        "name": r["name"],
                        "employee_id": r["employee_id"],
                        "delivered": r["delivered"],
                        "in_progress": r["in_progress"],
                        "rating_label": (
                            f"{r['rating']}% · {r['rating_label']}"
                            if r["rating"] is not None
                            else "—"
                        ),
                        "avg_minutes": (
                            r["avg_minutes"] if r["avg_minutes"] is not None else "—"
                        ),
                    }
                    for r in riders
                ],
            }
        ],
    }


def _build_supply(filters):
    """Supplier performance for stock received through the My Shop Buy items flow."""
    from django.urls import reverse

    from employees.access import role_url_segment

    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    try:
        selected_item_id = int(filters.get("supply_item_id") or 0)
    except (TypeError, ValueError):
        selected_item_id = 0

    buy_lines = _within_created_range(
        StockMovementLine.objects.filter(
            movement__shop_id__in=shop_ids,
            movement__movement_type=StockMovementType.IN,
            movement__entry_source=StockEntrySource.BUY_ITEMS,
            item__isnull=False,
        ),
        start,
        end,
        lookup="movement__created_at",
    )
    item_choices = [
        {
            "id": row["item_id"],
            "name": row["item__name"],
            "label": (
                f"{row['item__name']} · {int(row['transaction_count'] or 0)} deliveries"
            ),
        }
        for row in buy_lines.values("item_id", "item__name")
        .annotate(transaction_count=Count("movement_id", distinct=True))
        .order_by("-transaction_count", "item__name")[:30]
    ]
    top_item = (
        buy_lines.values("item_id", "item__name")
        .annotate(
            transaction_count=Count("movement_id", distinct=True),
            quantity=Sum("quantity"),
        )
        .order_by("-transaction_count", "-quantity", "item__name")
        .first()
    )
    if not selected_item_id and top_item:
        selected_item_id = top_item["item_id"]

    selected_item = (
        Item.objects.filter(pk=selected_item_id).only("id", "name").first()
        if selected_item_id
        else None
    )
    back_href = reverse(
        "employees:analytics_section",
        kwargs={
            "role_segment": role_url_segment(filters["role"]),
            "section": "stock",
        },
    )
    if selected_item is None:
        return {
            "headline": "Supply analytics",
            "lead": (
                "Supplier data is calculated only from stock added through "
                "the My Shop Buy items popup."
            ),
            "back_href": back_href,
            "back_label": "Stock on hand",
            "top_item": None,
            "selected_item": None,
            "item_choices": item_choices,
            "supplier_rows": [],
            "totals": None,
            "summary_board": None,
        }

    grouped = {}
    for line in (
        buy_lines.filter(item_id=selected_item.pk)
        .select_related("movement__shop")
        .order_by("-movement__created_at", "-pk")
    ):
        name = (line.supplier_name or "").strip() or "Supplier not recorded"
        phone = " ".join(
            part
            for part in (
                (line.supplier_phone_country_code or "").strip(),
                (line.supplier_phone_number or "").strip(),
            )
            if part
        )
        key = "|".join(
            (
                name.casefold(),
                (line.supplier_phone_country_code or "").strip(),
                (line.supplier_phone_number or "").strip(),
            )
        )
        entry = grouped.setdefault(
            key,
            {
                "name": name,
                "phone": phone,
                "has_name": bool((line.supplier_name or "").strip()),
                "movement_ids": set(),
                "quantity": 0,
                "amount": _zero(),
            },
        )
        quantity = Decimal(line.quantity or 0)
        unit_price = Decimal(line.buying_price or 0)
        entry["movement_ids"].add(line.movement_id)
        entry["quantity"] += quantity
        entry["amount"] += unit_price * quantity

    supplier_rows = []
    for entry in sorted(
        grouped.values(), key=lambda row: (-len(row["movement_ids"]), row["name"].casefold())
    ):
        average_price = (
            entry["amount"] / entry["quantity"] if entry["quantity"] else _zero()
        )
        supplier_rows.append(
            {
                "name": entry["name"],
                "phone": entry["phone"],
                "account_href": (
                    shelf_supplier_account_url(
                        filters["role"],
                        name=entry["name"],
                        phone=entry["phone"],
                    )
                    if entry["has_name"]
                    else ""
                ),
                "supply_count": len(entry["movement_ids"]),
                "quantity": _qty_number(entry["quantity"]),
                "average_price": _money_ksh(average_price),
                "total_value": _money_ksh(entry["amount"]),
            }
        )

    total_deliveries = sum(row["supply_count"] for row in supplier_rows)
    total_units = sum(
        (Decimal(entry["quantity"] or 0) for entry in grouped.values()),
        Decimal("0"),
    )
    total_spend = sum(
        (Decimal(entry["amount"] or 0) for entry in grouped.values()), _zero()
    )
    weighted_avg = (
        total_spend / total_units if total_units else _zero()
    )
    totals = {
        "supply_count": total_deliveries,
        "quantity": _qty_number(total_units),
        "total_value": _money_ksh(total_spend),
        "average_price": _money_ksh(weighted_avg),
    }

    return {
        "headline": "Supply analytics",
        "lead": (
            "Who supplied this item, how often, and at what price — "
            "from My Shop Buy items stock-ins only."
        ),
        "back_href": back_href,
        "back_label": "Stock on hand",
        "top_item": top_item,
        "selected_item": selected_item,
        "item_choices": item_choices,
        "supplier_rows": supplier_rows,
        "totals": totals,
        "summary_board": _supply_summary_board(
            item_name=selected_item.name,
            delivery_count=total_deliveries,
            unit_count=total_units,
            supplier_count=len(supplier_rows),
            total_spend=total_spend,
            average_price=weighted_avg,
        ),
    }


def _build_quotations(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    _sales, _prev, _credits, quotes, _expenses = _common_receipt_sets(filters)
    query = filters.get("query") or ""
    role = filters["role"]

    quotes_by_shop: dict[int, tuple[int, Decimal]] = {}
    for row in quotes.values("shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
    ):
        quotes_by_shop[row["shop_id"]] = (
            int(row["docs"] or 0),
            Decimal(row["amount"] or 0),
        )

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -quotes_by_shop.get(shop.pk, (0, _zero()))[1],
            shop.name.lower(),
        ),
    )

    columns = [
        "Shop",
        {
            "label": "Quotations",
            "pair": True,
            "pair_qty": "Docs",
            "pair_amt": "Amt",
            "total": True,
        },
    ]

    list_base = analytics_receipts_list_url(role, "quotations")
    from urllib.parse import parse_qs, urlencode

    base_params = parse_qs(query, keep_blank_values=True) if query else {}

    table_rows = []
    for shop in shops_sorted:
        qty, amount = quotes_by_shop.get(shop.pk, (0, _zero()))
        params = {k: v[0] for k, v in base_params.items()}
        params["shop_id"] = str(shop.pk)
        shop_query = urlencode(params)
        table_rows.append(
            [
                {
                    "href": f"{list_base}?{shop_query}" if shop_query else list_base,
                    "label": shop.name,
                },
                _qty_amount_cell(
                    qty,
                    amount,
                    title=f"{int(qty or 0)} · {_money_ksh(amount)}",
                ),
            ]
        )

    if shops_sorted:
        total_qty = 0
        total_amount = _zero()
        for shop in shops_sorted:
            qty, amount = quotes_by_shop.get(shop.pk, (0, _zero()))
            total_qty += int(qty or 0)
            total_amount += Decimal(amount or 0)
        table_rows.append(
            [
                "Total",
                _qty_amount_cell(
                    total_qty,
                    total_amount,
                    title=f"{total_qty} · {_money_ksh(total_amount)}",
                ),
            ]
        )

    return {
        "headline": "Quotations",
        "lead": "",
        "alerts": [],
        "metrics": [],
        "insights": [],
        "tables": [
            _table(
                "Quotations by shop",
                columns,
                table_rows,
                empty="No quotations for selected shops and period.",
            )
        ],
    }


def _build_gifts(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    gifts = _receipts_qs(
        shop_ids=shop_ids,
        start=filters["start"],
        end=filters["end"],
        kinds=[ShopReceiptKind.GIFT],
        receipt_status=filters.get("receipt_status"),
    )
    query = filters.get("query") or ""
    role = filters["role"]

    gifts_by_shop: dict[int, tuple[int, Decimal]] = {}
    for row in gifts.values("shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
    ):
        gifts_by_shop[row["shop_id"]] = (
            int(row["docs"] or 0),
            Decimal(row["amount"] or 0),
        )

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -gifts_by_shop.get(shop.pk, (0, _zero()))[1],
            shop.name.lower(),
        ),
    )

    columns = [
        "Shop",
        {
            "label": "Gifts",
            "pair": True,
            "pair_qty": "Docs",
            "pair_amt": "Amt",
            "total": True,
        },
    ]

    list_base = analytics_receipts_list_url(role, "gifts")
    from urllib.parse import parse_qs, urlencode

    base_params = parse_qs(query, keep_blank_values=True) if query else {}

    table_rows = []
    for shop in shops_sorted:
        qty, amount = gifts_by_shop.get(shop.pk, (0, _zero()))
        params = {k: v[0] for k, v in base_params.items()}
        params["shop_id"] = str(shop.pk)
        shop_query = urlencode(params)
        table_rows.append(
            [
                {
                    "href": f"{list_base}?{shop_query}" if shop_query else list_base,
                    "label": shop.name,
                },
                _qty_amount_cell(
                    qty,
                    amount,
                    title=f"{int(qty or 0)} · {_money_ksh(amount)}",
                ),
            ]
        )

    if shops_sorted:
        total_qty = 0
        total_amount = _zero()
        for shop in shops_sorted:
            qty, amount = gifts_by_shop.get(shop.pk, (0, _zero()))
            total_qty += int(qty or 0)
            total_amount += Decimal(amount or 0)
        table_rows.append(
            [
                "Total",
                _qty_amount_cell(
                    total_qty,
                    total_amount,
                    title=f"{total_qty} · {_money_ksh(total_amount)}",
                ),
            ]
        )

    return {
        "headline": "Gifts",
        "lead": "",
        "alerts": [],
        "metrics": [],
        "insights": [],
        "tables": [
            _table(
                "Gifts by shop",
                columns,
                table_rows,
                empty="No gifts for selected shops and period.",
            )
        ],
    }


def _trade_receipts_qs(shop_ids, start, end, *, receipt_status=None, flags=None):
    flags = flags or _pos_feature_flags()
    kinds_q = Q(pk__in=[])
    if flags["trade_out"]:
        kinds_q |= Q(kind=ShopReceiptKind.TRADE_OUT)
    if flags["trade_in"]:
        kinds_q |= Q(kind=ShopReceiptKind.TRADE_IN)
    if flags["tradings"]:
        kinds_q |= Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
    qs = (
        _within_created_range(
            ShopReceipt.objects.filter(
                Q(shop_id__in=shop_ids) | Q(source_shop_id__in=shop_ids)
            ),
            start,
            end,
        )
        .filter(kinds_q)
    )
    statuses = _analytics_receipt_statuses(receipt_status)
    if statuses is not None:
        # Fully returned trades are stored as CANCELLED — keep them visible
        # regardless of the pending/confirmed scope (they are cleared records).
        qs = qs.filter(
            Q(status__in=statuses) | Q(status=ShopReceiptStatus.CANCELLED)
        )
    return qs


def _trade_receipt_columns():
    return [
        "Trade",
        "Type",
        "Client",
        "Shop",
        {
            "label": "Items",
            "pair": True,
            "pair_qty": "Qty",
            "pair_amt": "Lines",
        },
        {
            "label": "Value",
            "title": "Value of the trade",
            "band": "total",
            "band_start": True,
        },
        {
            "label": "Cleared",
            "title": "Cash/M-Pesa paid plus exchange stock-in value",
            "band": "cash",
            "band_start": True,
        },
        {
            "label": "Balance",
            "title": "Amount still owed on this trade",
            "band": "mpesa",
            "band_start": True,
        },
        "Status",
    ]


def _trade_receipt_display_value(receipt) -> Decimal:
    """Receipt total; fully returned (cancelled) trades keep their line value."""
    total = Decimal(receipt.total or 0)
    if total > 0 or receipt.status != ShopReceiptStatus.CANCELLED:
        return total
    return sum(
        (Decimal(line.line_total or 0) for line in receipt.lines.all()),
        Decimal("0.00"),
    )


def _trade_receipt_rows(qs, *, role, query, client_id):
    """One row per trade receipt, linked to that client's settle page."""
    from django.urls import reverse

    from employees.access import role_url_segment
    from shops.trade_settlement import trade_balance_due

    table_rows = []
    total_value = _zero()
    total_cleared = _zero()
    total_balance = _zero()
    open_count = 0
    item_units = 0
    trade_count = 0
    trade_in_count = 0
    trade_out_count = 0
    trade_in_value = _zero()
    trade_out_value = _zero()

    for receipt in qs:
        trade_count += 1
        lines = list(receipt.lines.all())
        qty = sum(
            (Decimal(line.quantity or 0) for line in lines), Decimal("0")
        )
        item_units += qty
        line_count = len(lines)
        items_label = ", ".join(
            f"{line.item_name} ×{_qty_label(line.quantity)}"
            for line in lines[:3]
            if Decimal(line.quantity or 0) > 0
        )
        if line_count > 3:
            items_label = f"{items_label}…"
        if not items_label:
            items_label = "—"

        paid = Decimal(receipt.amount_paid or 0)
        exchange = Decimal(getattr(receipt, "trade_exchange_value", 0) or 0)
        cleared = (paid + exchange).quantize(Decimal("0.01"))
        value = _trade_receipt_display_value(receipt)
        if receipt.kind in {ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN}:
            balance = trade_balance_due(receipt)
        else:
            balance = _zero()
        total_value += value
        total_cleared += cleared
        total_balance += balance
        if balance > 0:
            open_count += 1

        if receipt.kind == ShopReceiptKind.TRADE_IN:
            type_label = "Trade in"
            trade_in_count += 1
            trade_in_value += value
        else:
            type_label = "Trade out"
            trade_out_count += 1
            trade_out_value += value

        client = (receipt.client_name or "Walk-in").strip() or "Walk-in"
        if receipt.client_phone:
            client = f"{client} · {receipt.client_phone}"

        if receipt.status == ShopReceiptStatus.CANCELLED:
            status_label = "Returned"
            status_tone = "bad"
        elif receipt.kind == ShopReceiptKind.SALE and receipt.settled_from_trade:
            status_label = "Converted to sale"
            status_tone = "good"
        elif balance <= 0:
            status_label = "Cleared"
            status_tone = "good"
        else:
            status_label = "Open"
            status_tone = "warn"

        detail_href = reverse(
            "employees:analytics_trade_receipt",
            kwargs={
                "role_segment": role_url_segment(role),
                "client_id": client_id,
                "receipt_id": receipt.pk,
            },
        )
        if query:
            detail_href = f"{detail_href}?{query}"

        table_rows.append(
            {
                "search_text": f"{receipt.receipt_number} {type_label} {client} {items_label}",
                "cells": [
                    {
                        "href": detail_href,
                        "label": receipt.receipt_number,
                    },
                    {
                        "label": type_label,
                        "tone": "good" if type_label == "Trade in" else "warn",
                    },
                    {
                        "label": client,
                        "title": items_label,
                    },
                    receipt.shop.name if receipt.shop else "—",
                    _qty_amount_cell(
                        qty,
                        line_count,
                        title=items_label,
                    ),
                    _money_cell(
                        receipt.total,
                        title=f"Value {_money_ksh(receipt.total)}",
                    ),
                    _money_cell(
                        cleared,
                        title=(
                            f"Paid {_money_ksh(paid)} · exchange {_money_ksh(exchange)}"
                        ),
                    ),
                    _money_cell(
                        balance,
                        tone="warn" if balance > 0 else "neutral",
                        title=f"Balance {_money_ksh(balance)}",
                    ),
                    {
                        "label": status_label,
                        "tone": status_tone,
                    },
                ],
            }
        )

    if table_rows:
        table_rows.append(
            [
                "Total",
                f"{trade_in_count} in · {trade_out_count} out",
                f"{trade_count} trade{'' if trade_count == 1 else 's'}",
                f"{open_count} open",
                _qty_amount_cell(
                    item_units,
                    trade_count,
                    title=f"{_qty_label(item_units)} units across {trade_count} trades",
                ),
                _money_cell(total_value, title=f"Value {_money_ksh(total_value)}"),
                _money_cell(
                    total_cleared, title=f"Cleared {_money_ksh(total_cleared)}"
                ),
                _money_cell(
                    total_balance,
                    tone="warn" if total_balance > 0 else "neutral",
                    title=f"Balance {_money_ksh(total_balance)}",
                ),
                "",
            ]
        )

    return {
        "rows": table_rows,
        "trade_count": trade_count,
        "open_count": open_count,
        "trade_in_count": trade_in_count,
        "trade_out_count": trade_out_count,
        "trade_in_value": trade_in_value,
        "trade_out_value": trade_out_value,
        "total_value": total_value,
        "total_balance": total_balance,
    }


def _build_tradings(filters):
    """Client accounts that have trade ins or trade outs in the period."""
    from django.urls import reverse

    from employees.access import role_url_segment
    from shops.trade_settlement import trade_balance_due

    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    role = filters["role"]
    query = filters.get("query") or ""

    qs = _trade_receipts_qs(
        shop_ids, start, end, receipt_status=filters.get("receipt_status")
    ).select_related("client")
    qs = qs.prefetch_related("lines")

    accounts = {}
    unlinked = 0
    for receipt in qs:
        if not receipt.client_id:
            unlinked += 1
            continue
        bucket = accounts.get(receipt.client_id)
        if bucket is None:
            client = receipt.client
            bucket = {
                "client_id": receipt.client_id,
                "name": (
                    (getattr(client, "full_name", None) or receipt.client_name or "Client")
                    .strip()
                    or "Client"
                ),
                "phone": (
                    getattr(client, "phone_number", None) or receipt.client_phone or ""
                ).strip(),
                "trade_in_count": 0,
                "trade_in_value": _zero(),
                "trade_out_count": 0,
                "trade_out_value": _zero(),
                "balance": _zero(),
            }
            accounts[receipt.client_id] = bucket
        value = _trade_receipt_display_value(receipt)
        if receipt.kind == ShopReceiptKind.TRADE_IN:
            bucket["trade_in_count"] += 1
            bucket["trade_in_value"] += value
        else:
            bucket["trade_out_count"] += 1
            bucket["trade_out_value"] += value
        if receipt.kind in {ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN}:
            bucket["balance"] += trade_balance_due(receipt)

    ordered = sorted(
        accounts.values(),
        key=lambda row: (-row["balance"], row["name"].lower(), row["client_id"]),
    )

    columns = [
        "Client",
        {"label": "Phone", "phone": True, "title": "Client phone number"},
        {
            "label": "Trade in",
            "pair": True,
            "pair_qty": "Docs",
            "pair_amt": "Value",
        },
        {
            "label": "Trade out",
            "pair": True,
            "pair_qty": "Docs",
            "pair_amt": "Value",
        },
        {
            "label": "Balance",
            "title": "Open amount still to clear",
            "band": "mpesa",
            "band_start": True,
        },
    ]

    table_rows = []
    total_in = _zero()
    total_out = _zero()
    total_balance = _zero()
    in_docs = 0
    out_docs = 0
    segment = role_url_segment(role)
    for account in ordered:
        total_in += account["trade_in_value"]
        total_out += account["trade_out_value"]
        total_balance += account["balance"]
        in_docs += account["trade_in_count"]
        out_docs += account["trade_out_count"]
        href = reverse(
            "employees:analytics_trade_detail",
            kwargs={"role_segment": segment, "client_id": account["client_id"]},
        )
        if query:
            href = f"{href}?{query}"
        table_rows.append(
            {
                "search_text": f"{account['name']} {account['phone']}",
                "cells": [
                    {"href": href, "label": account["name"]},
                    account["phone"] or "—",
                    _qty_amount_cell(
                        account["trade_in_count"],
                        account["trade_in_value"],
                        title=(
                            f"{account['trade_in_count']} trade in · "
                            f"{_money_ksh(account['trade_in_value'])}"
                        ),
                    ),
                    _qty_amount_cell(
                        account["trade_out_count"],
                        account["trade_out_value"],
                        title=(
                            f"{account['trade_out_count']} trade out · "
                            f"{_money_ksh(account['trade_out_value'])}"
                        ),
                    ),
                    _money_cell(
                        account["balance"],
                        tone="warn" if account["balance"] > 0 else "neutral",
                        title=f"Balance {_money_ksh(account['balance'])}",
                    ),
                ],
            }
        )

    if table_rows:
        table_rows.append(
            [
                "Total",
                f"{len(ordered)} client{'' if len(ordered) == 1 else 's'}",
                _qty_amount_cell(
                    in_docs,
                    total_in,
                    title=f"{in_docs} trade in · {_money_ksh(total_in)}",
                ),
                _qty_amount_cell(
                    out_docs,
                    total_out,
                    title=f"{out_docs} trade out · {_money_ksh(total_out)}",
                ),
                _money_cell(
                    total_balance,
                    tone="warn" if total_balance > 0 else "neutral",
                    title=f"Balance {_money_ksh(total_balance)}",
                ),
            ]
        )

    period_label = filters.get("report_period_label") or "selected period"
    footnote = "Open a client to see every trade in and trade out."
    if unlinked:
        footnote = (
            f"{footnote} {unlinked} trade"
            f"{'' if unlinked == 1 else 's'} have no client account and are not listed."
        )
    return {
        "headline": "Tradings",
        "lead": (
            f"Client trade accounts for {period_label}. "
            "Open a client to see their trade ins and trade outs."
        ),
        "alerts": [],
        "show_search": True,
        "download_href": _pdf_download_href(filters.get("query") or ""),
        "search_placeholder": "Search client or phone",
        "list_table_layout": True,
        "metrics": [
            {
                "label": "Clients",
                "value": str(len(ordered)),
                "hint": "Accounts with a trade in this period",
            },
            {
                "label": "Trade in",
                "value": _money_ksh(total_in),
                "hint": f"{in_docs} document{'' if in_docs == 1 else 's'} received",
            },
            {
                "label": "Trade out",
                "value": _money_ksh(total_out),
                "hint": f"{out_docs} document{'' if out_docs == 1 else 's'} sent",
            },
            {
                "label": "Balance left",
                "value": _money_ksh(total_balance),
                "hint": "Still to clear with the client",
            },
        ],
        "insights": [],
        "tables": [
            _table(
                "Client accounts",
                columns,
                table_rows,
                empty="No client trade accounts for selected shops and period.",
                footnote=footnote,
                searchable=True,
            )
        ],
    }


def _trade_view_href(query: str, view: str) -> str:
    """Current filter query with the view tab swapped."""
    from urllib.parse import parse_qs, urlencode

    params = parse_qs(query or "", keep_blank_values=True)
    flat = {key: values[0] for key, values in params.items()}
    flat.pop("download", None)
    if view == "trades":
        flat.pop("view", None)
    else:
        flat["view"] = view
    return f"?{urlencode(flat)}"


def _client_trade_items_table(receipts) -> dict:
    """Per-item quantity rollup across one client's trade receipts."""
    buckets: dict = {}
    for receipt in receipts:
        is_trade_in = receipt.kind == ShopReceiptKind.TRADE_IN
        for line in receipt.lines.all():
            qty = Decimal(line.quantity or 0) - Decimal(
                line.returned_quantity or 0
            )
            if qty <= 0:
                continue
            name = (line.item_name or "Item").strip() or "Item"
            key = line.item_id or f"name:{name.lower()}"
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "name": name,
                    "qty_in": Decimal("0"),
                    "qty_out": Decimal("0"),
                    "value_in": _zero(),
                    "value_out": _zero(),
                }
                buckets[key] = bucket
            value = (Decimal(line.unit_price or 0) * qty).quantize(
                Decimal("0.01")
            )
            if is_trade_in:
                bucket["qty_in"] += qty
                bucket["value_in"] += value
            else:
                bucket["qty_out"] += qty
                bucket["value_out"] += value

    ordered = sorted(
        buckets.values(),
        key=lambda row: (
            -(row["qty_in"] + row["qty_out"]),
            row["name"].lower(),
        ),
    )

    columns = [
        "Item",
        {
            "label": "Trade in",
            "title": "Qty and value received from the client",
            "pair": True,
            "pair_qty": "Qty",
            "pair_amt": "Amt",
            "band": "cash",
            "band_start": True,
        },
        {
            "label": "Trade out",
            "title": "Qty and value given to the client",
            "pair": True,
            "pair_qty": "Qty",
            "pair_amt": "Amt",
            "band": "mpesa",
            "band_start": True,
        },
        {
            "label": "Total",
            "title": "Trade in + trade out",
            "pair": True,
            "pair_qty": "Qty",
            "pair_amt": "Amt",
            "total": True,
            "band": "total",
            "band_start": True,
        },
    ]

    rows = []
    total_qty_in = Decimal("0")
    total_qty_out = Decimal("0")
    total_value_in = _zero()
    total_value_out = _zero()
    for bucket in ordered:
        total_qty_in += bucket["qty_in"]
        total_qty_out += bucket["qty_out"]
        total_value_in += bucket["value_in"]
        total_value_out += bucket["value_out"]
        total_qty = bucket["qty_in"] + bucket["qty_out"]
        total_value = bucket["value_in"] + bucket["value_out"]
        rows.append(
            {
                "search_text": bucket["name"],
                "cells": [
                    bucket["name"],
                    _qty_amount_cell(
                        bucket["qty_in"],
                        bucket["value_in"],
                        title=(
                            f"{_qty_label(bucket['qty_in'])} in · "
                            f"{_money_ksh(bucket['value_in'])}"
                        ),
                    ),
                    _qty_amount_cell(
                        bucket["qty_out"],
                        bucket["value_out"],
                        title=(
                            f"{_qty_label(bucket['qty_out'])} out · "
                            f"{_money_ksh(bucket['value_out'])}"
                        ),
                    ),
                    _qty_amount_cell(
                        total_qty,
                        total_value,
                        title=(
                            f"{_qty_label(total_qty)} · "
                            f"{_money_ksh(total_value)}"
                        ),
                    ),
                ],
            }
        )

    if rows:
        rows.append(
            [
                "Total",
                _qty_amount_cell(
                    total_qty_in,
                    total_value_in,
                    title=(
                        f"{_qty_label(total_qty_in)} in · "
                        f"{_money_ksh(total_value_in)}"
                    ),
                ),
                _qty_amount_cell(
                    total_qty_out,
                    total_value_out,
                    title=(
                        f"{_qty_label(total_qty_out)} out · "
                        f"{_money_ksh(total_value_out)}"
                    ),
                ),
                _qty_amount_cell(
                    total_qty_in + total_qty_out,
                    total_value_in + total_value_out,
                    title=(
                        f"{_qty_label(total_qty_in + total_qty_out)} · "
                        f"{_money_ksh(total_value_in + total_value_out)}"
                    ),
                ),
            ]
        )

    return _table(
        "Items",
        columns,
        rows,
        empty="No traded items for this client in the selected period.",
        footnote="Quantities net of returned items.",
        searchable=True,
    )


def build_client_tradings_page(*, profile, request, client_id: int) -> dict:
    """Trade ins and trade outs for one client."""
    from employees.workspace import analytics_section_url

    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        raise Http404("Client not found.")

    filters = _filters_context(
        profile,
        request,
        allow_all_time=True,
        default_all_time=False,
    )
    filters["role"] = profile.role
    query = request.GET.urlencode()
    role = profile.role
    qs = (
        _trade_receipts_qs(
            filters["active_shop_ids"],
            filters["start"],
            filters["end"],
            receipt_status=filters.get("receipt_status"),
        )
        .filter(client_id=client.pk)
        .select_related("shop", "client", "source_shop")
        .prefetch_related("lines")
        .order_by("-created_at", "-id")
    )
    summary = _trade_receipt_rows(qs, role=role, query=query, client_id=client.pk)
    back_href = analytics_section_url(role, "tradings")
    if query:
        back_href = f"{back_href}?{query}"
    period_label = filters.get("report_period_label") or "selected period"
    phone = (client.phone_number or "").strip()

    view = (request.GET.get("view") or "").strip().lower()
    show_items = view == "items"
    view_tabs = [
        {
            "label": "Trades",
            "href": _trade_view_href(query, "trades"),
            "active": not show_items,
        },
        {
            "label": "Items",
            "href": _trade_view_href(query, "items"),
            "active": show_items,
        },
    ]
    if show_items:
        tables = [_client_trade_items_table(qs)]
        search_placeholder = "Search item"
    else:
        tables = [
            _table(
                "Trades",
                _trade_receipt_columns(),
                summary["rows"],
                empty="No trade ins or trade outs for this client in the selected period.",
                footnote="Open a trade to pay, exchange, or return items.",
                searchable=True,
            )
        ]
        search_placeholder = "Search trade or item"
    return _apply_pos_visibility(
        {
        "headline": client.full_name,
        "lead": (
            f"{phone + ' · ' if phone else ''}"
            f"Trade ins and trade outs for {period_label}."
        ),
        "period_label": period_label,
        "back_href": back_href,
        "back_label": "All clients",
        "alerts": [],
        "show_search": True,
        "download_href": _pdf_download_href(query),
        "search_placeholder": search_placeholder,
        "list_table_layout": True,
        "view": "items" if show_items else "trades",
        "view_tabs": view_tabs,
        "metrics": [
            {
                "label": "Trade in",
                "value": _money_ksh(summary["trade_in_value"]),
                "hint": f"{summary['trade_in_count']} document{'' if summary['trade_in_count'] == 1 else 's'}",
            },
            {
                "label": "Trade out",
                "value": _money_ksh(summary["trade_out_value"]),
                "hint": f"{summary['trade_out_count']} document{'' if summary['trade_out_count'] == 1 else 's'}",
            },
            {
                "label": "Open trades",
                "value": str(summary["open_count"]),
                "hint": "Still awaiting payment or exchange",
            },
            {
                "label": "Balance left",
                "value": _money_ksh(summary["total_balance"]),
                "hint": "Still to clear with this client",
            },
        ],
        "insights": [],
        "tables": tables,
        },
        filters["pos_flags"],
    )


def build_trade_out_detail(*, profile, receipt_id: int, request=None) -> dict:
    """Detail + settle context for one trade-out receipt."""
    from django.urls import reverse

    from employees.access import role_url_segment
    from shops.trade_settlement import trade_balance_due

    filters = _filters_context(
        profile,
        request,
        allow_all_time=True,
        default_all_time=True,
    )
    shop_ids = filters["active_shop_ids"]
    receipt = (
        ShopReceipt.objects.filter(pk=receipt_id)
        .filter(Q(shop_id__in=shop_ids) | Q(source_shop_id__in=shop_ids))
        .filter(
            Q(kind=ShopReceiptKind.TRADE_OUT)
            | Q(kind=ShopReceiptKind.TRADE_IN)
            | Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
        )
        .select_related("shop", "source_shop", "client", "created_by", "created_by__user")
        .prefetch_related("lines", "lines__item")
        .first()
    )
    if receipt is None:
        raise Http404("Trade out not found.")

    lines = []
    for line in receipt.lines.all():
        remaining = line.remaining_quantity or 0
        serials = list(line.remaining_serial_numbers or [])
        lines.append(
            {
                "id": line.pk,
                "name": line.item_name,
                "qty": _qty_number(line.quantity),
                "remaining": _qty_number(remaining),
                "returned": _qty_number(line.returned_quantity),
                "unit_price": _money_ksh(line.unit_price),
                "unit_price_raw": str(Decimal(line.unit_price or 0)),
                "line_total": _money_ksh(line.line_total),
                "serials": serials,
                "track_serial": bool(serials)
                or bool(getattr(getattr(line, "item", None), "track_serial_number", False)),
            }
        )

    paid = Decimal(receipt.amount_paid or 0)
    exchange = Decimal(getattr(receipt, "trade_exchange_value", 0) or 0)
    is_trade_in = receipt.kind == ShopReceiptKind.TRADE_IN
    balance = (
        trade_balance_due(receipt)
        if receipt.kind in {ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN}
        else _zero()
    )
    can_settle = (
        receipt.kind in {ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN}
        and receipt.status != ShopReceiptStatus.CANCELLED
        and balance > 0
    )
    can_return = (
        receipt.kind in {ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN}
        and receipt.status != ShopReceiptStatus.CANCELLED
        and any(float(row["remaining"] or 0) > 0 for row in lines)
    )

    settlements = []
    for event in receipt.trade_settlements or []:
        etype = (event.get("type") or "").strip().lower()
        if etype == "payment":
            settlements.append(
                {
                    "label": f"Payment · {(event.get('method') or 'cash').title()}",
                    "detail": _money_ksh(event.get("amount")),
                    "when": event.get("at") or "",
                }
            )
        elif etype == "exchange":
            settlements.append(
                {
                    "label": (
                        f"Exchange · {event.get('item_name') or 'Item'} "
                        f"×{event.get('qty') or 0}"
                    ),
                    "detail": _money_ksh(event.get("value")),
                    "when": event.get("at") or "",
                }
            )
        elif etype == "return":
            settlements.append(
                {
                    "label": (
                        f"Return · {event.get('detail') or 'Item'} "
                        f"×{event.get('qty') or 0}"
                    ),
                    "detail": "Restocked",
                    "when": event.get("at") or "",
                }
            )

    role = profile.role
    query = request.GET.urlencode() if request is not None else ""
    if receipt.client_id:
        back_href = reverse(
            "employees:analytics_trade_detail",
            kwargs={
                "role_segment": role_url_segment(role),
                "client_id": receipt.client_id,
            },
        )
    else:
        back_href = reverse(
            "employees:analytics_section",
            kwargs={
                "role_segment": role_url_segment(role),
                "section": "tradings",
            },
        )
    if query:
        back_href = f"{back_href}?{query}"
    settle_url = reverse(
        "employees:analytics_trade_settle",
        kwargs={
            "role_segment": role_url_segment(role),
            "receipt_id": receipt.pk,
        },
    )
    catalog_url = reverse(
        "employees:my_shop_catalog",
        kwargs={"shop_id": receipt.shop_id},
    )

    return {
        **filters,
        "receipt": receipt,
        "receipt_number": receipt_tally_code(receipt),
        "shop_name": receipt.shop.name if receipt.shop else "—",
        "client_name": receipt.client_name or "Walk-in",
        "client_phone": receipt.client_phone or "",
        "lines": lines,
        "total": _money_ksh(receipt.total),
        "paid": _money_ksh(paid),
        "exchange_value": _money_ksh(exchange),
        "balance": _money_ksh(balance),
        "balance_raw": str(balance),
        "can_settle": can_settle,
        "can_return": can_return,
        "is_trade_in": is_trade_in,
        "is_open_trade": receipt.kind in {
            ShopReceiptKind.TRADE_OUT,
            ShopReceiptKind.TRADE_IN,
        },
        "settlements": settlements,
        "back_href": back_href,
        "settle_url": settle_url,
        "catalog_url": catalog_url,
        "created_label": timezone.localtime(receipt.created_at).strftime(
            "%d %b %Y · %H:%M"
        ),
        "cashier": _cashier_label(receipt.created_by) or "—",
        "status_label": receipt.get_status_display(),
        "kind_label": receipt.get_kind_display(),
        "pos_pay_cash": filters["pos_flags"]["pay_cash"],
        "pos_pay_mpesa": filters["pos_flags"]["pay_mpesa"],
        "pos_pay_card": filters["pos_flags"]["pay_card"],
    }


def _build_credit_report(filters):
    """Detailed credit report — per-shop rollup plus client credit accounts."""
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    _sales, _prev, credits, _quotes, _expenses = _common_receipt_sets(filters)

    total_by_shop: dict[int, tuple[int, Decimal]] = {}
    paid_by_shop: dict[int, Decimal] = {}
    due_by_shop: dict[int, Decimal] = {}
    for row in credits.values("shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
        paid=Coalesce(Sum("amount_paid"), _zero()),
    ):
        shop_id = row["shop_id"]
        amount = Decimal(row["amount"] or 0)
        paid = Decimal(row["paid"] or 0)
        total_by_shop[shop_id] = (int(row["docs"] or 0), amount)
        paid_by_shop[shop_id] = paid
        due_by_shop[shop_id] = _due_amount(amount, paid)

    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.CREDIT],
        receipt_status=filters.get("receipt_status"),
    )
    stock_by_shop: dict[int, Decimal] = {}
    selling_by_shop: dict[int, Decimal] = {}
    profit_by_shop: dict[int, Decimal] = {}
    for shop in shops:
        row = trading.get(shop.pk) or {}
        selling = Decimal(row.get("value") or 0)
        stock = Decimal(row.get("cogs") or 0)
        selling_by_shop[shop.pk] = selling
        stock_by_shop[shop.pk] = stock
        profit_by_shop[shop.pk] = selling - stock

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -total_by_shop.get(shop.pk, (0, _zero()))[1],
            shop.name.lower(),
        ),
    )

    columns = [
        "Shop",
        {
            "label": "Paid",
            "title": "Amount collected on credit receipts",
            "band": "cash",
            "band_start": True,
        },
        {
            "label": "Due",
            "title": "Unpaid credit balance in this period",
            "band": "mpesa",
            "band_start": True,
        },
        {
            "label": "Total",
            "pair": True,
            "pair_qty": "Docs",
            "pair_amt": "Amt",
            "total": True,
            "band": "total",
            "band_start": True,
        },
        {
            "label": "% paid",
            "title": "Share of credit collected",
            "band": "total",
        },
        {
            "label": "Stock",
            "title": "Buying cost of items sold on credit",
            "band": "result",
            "band_start": True,
        },
        {
            "label": "Profit",
            "title": "Selling value − stock (ex-tax, this period)",
            "band": "result",
        },
    ]

    table_rows = []
    for shop in shops_sorted:
        docs, amount = total_by_shop.get(shop.pk, (0, _zero()))
        paid = paid_by_shop.get(shop.pk, _zero())
        due = due_by_shop.get(shop.pk, _zero())
        stock = stock_by_shop.get(shop.pk, _zero())
        profit = profit_by_shop.get(shop.pk, _zero())
        selling = selling_by_shop.get(shop.pk, _zero())
        margin = (
            ((profit / selling) * Decimal("100")).quantize(Decimal("0.1"))
            if selling > 0
            else _zero()
        )
        paid_pct = _credit_items_paid_pct(amount, paid)
        table_rows.append(
            [
                shop.name,
                _money_cell(paid, title=f"Paid {_money_ksh(paid)}"),
                _money_cell(
                    due,
                    tone="warn" if due > 0 else "neutral",
                    title=f"Due {_money_ksh(due)}",
                ),
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{int(docs or 0)} · {_money_ksh(amount)}",
                ),
                _pct_cell(
                    paid_pct,
                    tone=(
                        "good"
                        if paid_pct >= Decimal("100")
                        else "warn"
                        if paid_pct > 0
                        else "bad"
                        if amount > 0
                        else "neutral"
                    ),
                    title=f"{paid_pct}% of credit paid back",
                ),
                _money_cell(stock, title=f"Stock value {_money_ksh(stock)}"),
                _money_cell(
                    profit,
                    tone="good" if profit >= 0 else "bad",
                    title=f"Profit {_money_ksh(profit)} · margin {margin}%",
                ),
            ]
        )

    total_docs = 0
    total_amount = _zero()
    total_paid = _zero()
    total_due = _zero()
    for shop in shops_sorted:
        docs, amount = total_by_shop.get(shop.pk, (0, _zero()))
        total_docs += int(docs or 0)
        total_amount += Decimal(amount or 0)
        total_paid += paid_by_shop.get(shop.pk, _zero())
        total_due += due_by_shop.get(shop.pk, _zero())
    total_stock = sum((stock_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero())
    total_selling = sum(
        (selling_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero()
    )
    total_profit = total_selling - total_stock
    margin_pct = (
        ((total_profit / total_selling) * Decimal("100")).quantize(Decimal("0.1"))
        if total_selling > 0
        else _zero()
    )

    if shops_sorted:
        total_paid_pct = _credit_items_paid_pct(total_amount, total_paid)
        table_rows.append(
            [
                "Total",
                _money_cell(total_paid, title=f"Paid {_money_ksh(total_paid)}"),
                _money_cell(
                    total_due,
                    tone="warn" if total_due > 0 else "neutral",
                    title=f"Due {_money_ksh(total_due)}",
                ),
                _qty_amount_cell(
                    total_docs,
                    total_amount,
                    title=f"{total_docs} · {_money_ksh(total_amount)}",
                ),
                _pct_cell(
                    total_paid_pct,
                    tone=(
                        "good"
                        if total_paid_pct >= Decimal("100")
                        else "warn"
                        if total_paid_pct > 0
                        else "bad"
                        if total_amount > 0
                        else "neutral"
                    ),
                    title=f"{total_paid_pct}% of credit paid back",
                ),
                _money_cell(total_stock, title=f"Stock value {_money_ksh(total_stock)}"),
                _money_cell(
                    total_profit,
                    tone="good" if total_profit >= 0 else "bad",
                    title=f"Profit {_money_ksh(total_profit)} · margin {margin_pct}%",
                ),
            ]
        )

    active_shops = sum(
        1 for shop in shops_sorted if total_by_shop.get(shop.pk, (0, _zero()))[0] > 0
    )

    items_table, unpaid_items = _credit_items_detail_table(filters)
    period_label = filters.get("report_period_label") or "selected period"

    alerts = []
    if unpaid_items:
        alerts.append(
            _alert(
                "warning",
                "Items with no payment",
                f"{unpaid_items} item{'s' if unpaid_items != 1 else ''} on "
                f"credit show 0% paid back in this period.",
                "Follow up with the clients holding these credits.",
            )
        )

    return {
        "headline": "Credit reports",
        "lead": (
            f"Items picked on credit by shop — credit, paid back, and "
            f"balance for {period_label}."
        ),
        "alerts": alerts,
        "show_search": True,
        "download_href": _pdf_download_href(filters.get("query") or ""),
        "search_placeholder": "Search items or shops…",
        "search_empty": "No items match that search.",
        "summary_board": _credits_summary_board(
            total_amount=total_amount,
            total_docs=total_docs,
            paid_amount=total_paid,
            due_amount=total_due,
            stock_amount=total_stock,
            profit_amount=total_profit,
            active_shops=active_shops,
            shop_count=len(shops_sorted),
        ),
        "insights": [],
        "tables": [
            _table(
                "Credits by shop",
                columns,
                table_rows,
                empty=(
                    "Select a shop to view credits."
                    if not shop_ids
                    else "No credits for selected shops and period."
                ),
                shop_grid=True,
                footnote=(
                    "Paid, Due, and Total are credit receipts in this period. "
                    "% paid = paid ÷ credit total. Stock and profit use "
                    "ex-tax item value net of returns for receipts in this "
                    "period. Profit = selling value − stock."
                ),
            ),
            items_table,
        ],
    }


def _credit_items_board(
    *, total_credit, total_paid, total_due, docs, item_count, shop_count
) -> dict:
    repaid_pct = (
        ((total_paid / total_credit) * Decimal("100")).quantize(Decimal("0.1"))
        if total_credit > 0
        else _zero()
    )
    due_pct = (
        ((total_due / total_credit) * Decimal("100")).quantize(Decimal("0.1"))
        if total_credit > 0
        else _zero()
    )
    return {
        "hero": {
            "label": "Credit",
            "value": _money_ksh(total_credit),
            "hint": (
                f"{docs} receipt{'s' if docs != 1 else ''} · "
                f"{item_count} item{'s' if item_count != 1 else ''} · "
                f"{shop_count} shop{'s' if shop_count != 1 else ''}"
            ),
        },
        "tiles": [
            {
                "label": "Paid back",
                "value": _money_ksh(total_paid),
                "hint": (
                    f"{repaid_pct}% of credit"
                    if total_credit > 0
                    else "No credits yet"
                ),
                "icon": "wallet",
                "tone": "cash",
            },
            {
                "label": "Still due",
                "value": _money_ksh(total_due),
                "hint": (
                    f"{due_pct}% of credit"
                    if total_credit > 0
                    else "No credits yet"
                ),
                "icon": "credit-card",
                "tone": "warn" if total_due > 0 else "good",
            },
            {
                "label": "Repaid",
                "value": f"{repaid_pct}%",
                "hint": "Share of credit collected",
                "icon": "trending-up",
                "tone": (
                    "good"
                    if total_credit > 0 and repaid_pct >= Decimal("100")
                    else "warn"
                    if repaid_pct > 0
                    else "neutral"
                ),
            },
        ],
    }


def _credit_items_best_day(entry):
    """Weekday the item is credited most -> (label, title)."""
    weekdays = entry["weekdays"]
    if not weekdays:
        return "—", ""
    wd, wd_units = max(weekdays.items(), key=lambda pair: (pair[1], -pair[0]))
    wd_days = sum(1 for day in entry["days"] if day.weekday() == wd)
    title = (
        f"{_ITEM_REPORT_WEEKDAY_FULL[wd]}s — {_qty_label(wd_units)} units "
        f"across {wd_days} {_ITEM_REPORT_WEEKDAY_FULL[wd]}"
        f"{'s' if wd_days != 1 else ''}"
    )
    if entry["day_units"]:
        top_day, top_units = max(
            entry["day_units"].items(), key=lambda pair: (pair[1], pair[0])
        )
        title += (
            f" · top day {top_day.strftime('%d %b %Y')} "
            f"({_qty_label(top_units)} units)"
        )
    return _ITEM_REPORT_WEEKDAY_SHORT[wd], title


def _credit_items_paid_pct(credit, paid) -> Decimal:
    if credit <= 0:
        return _zero()
    pct = (Decimal(paid) / Decimal(credit)) * Decimal("100")
    if pct > Decimal("100"):
        pct = Decimal("100")
    return pct.quantize(Decimal("0.1"))


def _credit_items_quad(*, units, credit, paid, day, title) -> dict:
    pct = _credit_items_paid_pct(credit, paid)
    return {
        "kind": "quad",
        "qty": _qty_label(units),
        "amount": _money_dense(credit),
        "pct": f"{pct}%",
        "day": day,
        "tone": (
            "good"
            if pct >= Decimal("100")
            else "warn"
            if pct > 0
            else "bad"
            if credit > 0
            else "neutral"
        ),
        "title": title,
    }


def _credit_items_empty_quad(label) -> dict:
    return {
        "kind": "quad",
        "qty": "—",
        "amount": "—",
        "pct": "—",
        "day": "—",
        "title": f"No credits at {label}",
    }


def _credit_items_cell_title(label, *, units, credit, paid, pct, day_title=""):
    due = _due_amount(credit, paid)
    return (
        f"{label} — {_qty_label(units)} units on credit"
        f" · {_money_ksh(credit)} credit"
        f" · {_money_ksh(paid)} paid ({pct}%)"
        f" · {_money_ksh(due)} due"
        f"{f' · {day_title}' if day_title else ''}"
    )


def _credit_items_row(*, meta, cat, shops, shop_name) -> dict:
    """One matrix row: item + per-shop quad cells + total."""
    pairs = dict(meta["pairs"])
    total_units = _zero()
    total_credit = _zero()
    total_paid = _zero()
    merged = {"days": set(), "day_units": {}, "weekdays": {}}
    for entry in pairs.values():
        total_units += entry["units"]
        total_credit += entry["credit"]
        total_paid += entry["paid"]
        merged["days"] |= entry["days"]
        for day, day_qty in entry["day_units"].items():
            merged["day_units"][day] = merged["day_units"].get(day, _zero()) + day_qty
        for wd, wd_qty in entry["weekdays"].items():
            merged["weekdays"][wd] = merged["weekdays"].get(wd, _zero()) + wd_qty

    cells = [{"label": meta["name"]}]
    for shop in shops:
        entry = pairs.get(shop.pk)
        label = shop_name.get(shop.pk) or "Shop"
        if entry is None or not entry["days"]:
            cells.append(_credit_items_empty_quad(label))
            continue
        pct = _credit_items_paid_pct(entry["credit"], entry["paid"])
        day, day_title = _credit_items_best_day(entry)
        cells.append(
            _credit_items_quad(
                units=entry["units"],
                credit=entry["credit"],
                paid=entry["paid"],
                day=day,
                title=_credit_items_cell_title(
                    label,
                    units=entry["units"],
                    credit=entry["credit"],
                    paid=entry["paid"],
                    pct=pct,
                    day_title=day_title,
                ),
            )
        )
    pct = _credit_items_paid_pct(total_credit, total_paid)
    day, day_title = _credit_items_best_day(merged)
    cells.append(
        _credit_items_quad(
            units=total_units,
            credit=total_credit,
            paid=total_paid,
            day=day,
            title=_credit_items_cell_title(
                "All shops",
                units=total_units,
                credit=total_credit,
                paid=total_paid,
                pct=pct,
                day_title=day_title,
            ),
        )
    )
    return {
        "cells": cells,
        "search_text": f"{meta['name']} {cat}",
        "_cat": cat,
        "_credit": total_credit,
        "_paid": total_paid,
    }


def _credit_items_pairs(filters) -> dict:
    """(item_id, shop_id) credit aggregates for receipts in the period.

    Payments land on the receipt, not the line — each item gets its share of
    the receipt total (by net line value) and the receipt's paid ratio is
    applied to that share, so item credits sum to the real credit figures.
    """
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    shop_name = {
        shop.pk: ((getattr(shop, "name", None) or "").strip() or "Shop")
        for shop in shops
    }
    start, end = filters["start"], filters["end"]

    receipts = _receipts_qs(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.CREDIT],
        receipt_status=filters.get("receipt_status"),
    )
    receipt_meta: dict[int, dict] = {}
    for row in receipts.values("id", "shop_id", "total", "amount_paid"):
        total = Decimal(row["total"] or 0)
        paid = Decimal(row["amount_paid"] or 0)
        paid_ratio = _zero()
        if total > 0:
            paid_ratio = paid / total
            if paid_ratio < 0:
                paid_ratio = _zero()
            elif paid_ratio > 1:
                paid_ratio = Decimal("1")
        receipt_meta[row["id"]] = {
            "shop_id": row["shop_id"],
            "total": total,
            "paid_ratio": paid_ratio,
        }

    lines = list(
        ShopReceiptLine.objects.filter(receipt_id__in=set(receipt_meta)).values(
            "receipt_id",
            "item_id",
            "item_name",
            "receipt__created_at",
            "quantity",
            "returned_quantity",
            "unit_price",
        )
    )
    net_by_receipt: dict[int, Decimal] = {}
    for row in lines:
        units = Decimal(row["quantity"] or 0) - Decimal(
            row["returned_quantity"] or 0
        )
        net_by_receipt[row["receipt_id"]] = net_by_receipt.get(
            row["receipt_id"], _zero()
        ) + (units * Decimal(row["unit_price"] or 0)).quantize(Decimal("0.01"))

    # (item_id, shop_id) -> totals + local day/weekday buckets.
    by_pair: dict[tuple, dict] = {}
    for row in lines:
        receipt_id = row["receipt_id"]
        meta = receipt_meta.get(receipt_id)
        receipt_net = net_by_receipt.get(receipt_id) or _zero()
        if meta is None or receipt_net <= 0:
            continue
        units = Decimal(row["quantity"] or 0) - Decimal(
            row["returned_quantity"] or 0
        )
        line_net = (units * Decimal(row["unit_price"] or 0)).quantize(
            Decimal("0.01")
        )
        credit = line_net * (meta["total"] / receipt_net)
        key = (row["item_id"], meta["shop_id"])
        entry = by_pair.get(key)
        if entry is None:
            entry = {
                "name": row["item_name"] or "Item",
                "units": _zero(),
                "credit": _zero(),
                "paid": _zero(),
                "days": set(),
                "day_units": {},
                "weekdays": {},
            }
            by_pair[key] = entry
        elif row["item_name"] and not entry["name"]:
            entry["name"] = row["item_name"]
        entry["units"] += units
        entry["credit"] += credit
        entry["paid"] += credit * meta["paid_ratio"]
        if units <= 0:
            continue
        day = _local_report_day(row["receipt__created_at"])
        if day is None:
            continue
        entry["days"].add(day)
        entry["day_units"][day] = entry["day_units"].get(day, _zero()) + units
        wd = day.weekday()
        entry["weekdays"][wd] = entry["weekdays"].get(wd, _zero()) + units

    return {
        "shops": shops,
        "shop_name": shop_name,
        "docs": len(receipt_meta),
        "by_pair": by_pair,
    }


def _credit_items_categories(by_pair) -> dict:
    item_ids = {key[0] for key in by_pair if key[0]}
    return (
        dict(Item.objects.filter(pk__in=item_ids).values_list("pk", "category"))
        if item_ids
        else {}
    )


def _credit_items_search(by_pair, categories, search_query: str) -> dict:
    """Server-side search — same matching as the on-page table filter so the
    PDF download honours what the user typed."""
    tokens = [token for token in (search_query or "").strip().lower().split() if token]
    if not tokens:
        return by_pair

    def _pair_matches(item_id, name):
        cat = (categories.get(item_id) or "").strip() or "Uncategorised"
        haystack = f"{name or ''} {cat}".lower()
        return all(token in haystack for token in tokens)

    return {
        key: entry
        for key, entry in by_pair.items()
        if _pair_matches(key[0], entry["name"])
    }


def _credit_items_paid_tone(pct, credit) -> str:
    if pct >= Decimal("100"):
        return "good"
    if pct > 0:
        return "warn"
    return "bad" if credit > 0 else "neutral"


def _credit_report_quad(*, units, credit, paid, title) -> dict:
    """Quad cell for the credit report matrix: qty · credit · paid · % paid."""
    pct = _credit_items_paid_pct(credit, paid)
    return {
        "kind": "quad",
        "qty": _qty_label(units),
        "amount": _money_dense(credit),
        "pct": _money_dense(paid),
        "day": f"{pct}%",
        "tone": _credit_items_paid_tone(pct, credit),
        "title": title,
    }


def _credit_report_row(*, meta, cat, shops, shop_name) -> dict:
    """One matrix row: item + per-shop quad cells (qty · credit · paid · %)."""
    pairs = dict(meta["pairs"])
    total_units = _zero()
    total_credit = _zero()
    total_paid = _zero()
    for entry in pairs.values():
        total_units += entry["units"]
        total_credit += entry["credit"]
        total_paid += entry["paid"]

    cells = [{"label": meta["name"]}]
    for shop in shops:
        entry = pairs.get(shop.pk)
        label = shop_name.get(shop.pk) or "Shop"
        if entry is None:
            cells.append(_credit_items_empty_quad(label))
            continue
        pct = _credit_items_paid_pct(entry["credit"], entry["paid"])
        cells.append(
            _credit_report_quad(
                units=entry["units"],
                credit=entry["credit"],
                paid=entry["paid"],
                title=_credit_items_cell_title(
                    label,
                    units=entry["units"],
                    credit=entry["credit"],
                    paid=entry["paid"],
                    pct=pct,
                ),
            )
        )
    pct = _credit_items_paid_pct(total_credit, total_paid)
    cells.append(
        _credit_report_quad(
            units=total_units,
            credit=total_credit,
            paid=total_paid,
            title=_credit_items_cell_title(
                "All shops",
                units=total_units,
                credit=total_credit,
                paid=total_paid,
                pct=pct,
            ),
        )
    )
    return {
        "cells": cells,
        "search_text": f"{meta['name']} {cat}",
        "_cat": cat,
        "_credit": total_credit,
        "_paid": total_paid,
    }


def _credit_items_detail_table(filters):
    """Item x shop matrix — each shop column shows qty, credit, paid, % paid."""
    pairs_data = _credit_items_pairs(filters)
    shops = pairs_data["shops"]
    shop_name = pairs_data["shop_name"]
    categories = _credit_items_categories(pairs_data["by_pair"])
    search_query = (filters.get("search_query") or "").strip().lower()
    by_pair = _credit_items_search(
        pairs_data["by_pair"], categories, search_query
    )

    item_credit: dict = {}
    item_paid: dict = {}
    item_units: dict = {}
    cat_credit: dict = {}
    item_meta: dict = {}
    for (item_id, shop_id), entry in by_pair.items():
        item_credit[item_id] = item_credit.get(item_id, _zero()) + entry["credit"]
        item_paid[item_id] = item_paid.get(item_id, _zero()) + entry["paid"]
        item_units[item_id] = item_units.get(item_id, _zero()) + entry["units"]
        meta = item_meta.get(item_id)
        if meta is None:
            cat = (categories.get(item_id) or "").strip() or "Uncategorised"
            meta = {"name": entry["name"], "cat": cat, "pairs": []}
            item_meta[item_id] = meta
            cat_credit.setdefault(cat, _zero())
        meta["pairs"].append((shop_id, entry))
        cat_credit[meta["cat"]] += entry["credit"]

    rows = []
    for cat in sorted(cat_credit, key=lambda c: (-cat_credit[c], c.lower())):
        rows.append(
            {
                "group_header": True,
                "label": f"{cat} — {_money_dense(cat_credit[cat])}",
                "search_text": cat,
            }
        )
        cat_item_ids = sorted(
            (iid for iid, m in item_meta.items() if m["cat"] == cat),
            key=lambda iid: (
                -item_units[iid],
                -item_credit[iid],
                (item_meta[iid]["name"] or "").lower(),
            ),
        )
        for iid in cat_item_ids:
            rows.append(
                _credit_report_row(
                    meta=item_meta[iid],
                    cat=cat,
                    shops=shops,
                    shop_name=shop_name,
                )
            )

    # Trailing totals row — grand qty/credit/paid/% paid per shop.
    shop_totals: dict = {}
    grand = {"units": _zero(), "credit": _zero(), "paid": _zero()}
    for (_, sid), entry in by_pair.items():
        bucket = shop_totals.setdefault(
            sid, {"units": _zero(), "credit": _zero(), "paid": _zero()}
        )
        for field in ("units", "credit", "paid"):
            bucket[field] += entry[field]
            grand[field] += entry[field]

    if rows:
        total_cells = [{"label": "All items"}]
        for shop in shops:
            bucket = shop_totals.get(shop.pk)
            label = shop_name.get(shop.pk) or "Shop"
            if bucket is None or not bucket["credit"]:
                total_cells.append(_credit_items_empty_quad(label))
                continue
            pct = _credit_items_paid_pct(bucket["credit"], bucket["paid"])
            total_cells.append(
                _credit_report_quad(
                    units=bucket["units"],
                    credit=bucket["credit"],
                    paid=bucket["paid"],
                    title=_credit_items_cell_title(
                        f"{label} — all items",
                        units=bucket["units"],
                        credit=bucket["credit"],
                        paid=bucket["paid"],
                        pct=pct,
                    ),
                )
            )
        pct = _credit_items_paid_pct(grand["credit"], grand["paid"])
        total_cells.append(
            _credit_report_quad(
                units=grand["units"],
                credit=grand["credit"],
                paid=grand["paid"],
                title=_credit_items_cell_title(
                    "All shops — all items",
                    units=grand["units"],
                    credit=grand["credit"],
                    paid=grand["paid"],
                    pct=pct,
                ),
            )
        )
        rows.append(
            {
                "cells": total_cells,
                "search_text": "all items total",
                "_total": True,
            }
        )

    unpaid_items = sum(
        1
        for iid in item_meta
        if item_credit[iid] > 0 and item_paid[iid] <= 0
    )

    columns = [{"label": "Item"}]
    for index, shop in enumerate(shops):
        col = _shop_col(shop)
        col.update(
            {
                "quad": True,
                "sub_qty": "Qty",
                "sub_amt": "Credit",
                "sub_pct": "Paid",
                "sub_day": "% paid",
                "band": "flow",
            }
        )
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append(
        {
            "label": "Total",
            "title": "All selected shops",
            "quad": True,
            "sub_qty": "Qty",
            "sub_amt": "Credit",
            "sub_pct": "Paid",
            "sub_day": "% paid",
            "total": True,
            "band": "total",
            "band_start": True,
        }
    )

    return (
        _table(
            "Items picked on credit",
            columns,
            rows,
            empty="No credit items for selected shops and period.",
            footnote=(
                "Categories ranked by credit sales; items ranked by units "
                "sold on credit. Each shop column shows qty, credit, paid, "
                "and % paid back — hover a cell for the due amount. Net of "
                "returns; each item's credit is its share of the receipt "
                "total (incl. tax)."
            ),
            shop_grid=True,
            searchable=True,
        ),
        unpaid_items,
    )


def _credit_due_today_followups(filters) -> dict:
    """Credit accounts with follow-up date today for the Credits page popup."""
    shop_ids = list(filters.get("active_shop_ids") or [])
    role = filters.get("role")
    today = timezone.localdate()
    if not shop_ids or not role:
        return {
            "date": today.isoformat(),
            "date_label": today.strftime("%d %b %Y"),
            "count": 0,
            "rows": [],
        }

    clients = (
        Client.objects.filter(credit_follow_up_date=today)
        .order_by("full_name", "id")
    )
    rows = []
    for client in clients:
        open_qs = (
            ShopReceipt.objects.filter(
                client_id=client.pk,
                kind=ShopReceiptKind.CREDIT,
                shop_id__in=shop_ids,
            )
            .exclude(status=ShopReceiptStatus.CANCELLED)
            .select_related("shop")
        )
        balance = _zero()
        open_count = 0
        shops = set()
        for receipt in open_qs:
            due = _due_amount(receipt.total, receipt.amount_paid)
            if due <= 0:
                continue
            balance += due
            open_count += 1
            if receipt.shop_id:
                shops.add((getattr(receipt.shop, "name", "") or "").strip() or "—")
        if balance <= 0:
            continue
        href = client_credit_account_url(
            role,
            client.pk,
            query="from=credit-clients",
            from_credits=True,
        )
        rows.append(
            {
                "client_id": client.pk,
                "client_name": (client.full_name or "").strip() or "Customer",
                "phone": (client.phone_number or "").strip(),
                "shop": ", ".join(sorted(shops)) if shops else "—",
                "open_count": open_count,
                "due": _money_ksh(balance),
                "due_raw": str(balance),
                "href": href,
            }
        )
    return {
        "date": today.isoformat(),
        "date_label": today.strftime("%d %b %Y"),
        "count": len(rows),
        "rows": rows,
    }


def _build_credits(filters):
    """Credits landing — items matrix: credit and % paid back per item x shop."""
    pairs_data = _credit_items_pairs(filters)
    shops = pairs_data["shops"]
    shop_name = pairs_data["shop_name"]
    docs = pairs_data["docs"]

    categories = _credit_items_categories(pairs_data["by_pair"])
    search_query = (filters.get("search_query") or "").strip().lower()
    by_pair = _credit_items_search(
        pairs_data["by_pair"], categories, search_query
    )

    # Items grouped by category (highest credit sales first) and ranked by
    # units sold on credit; each item gets one row with a quad cell per shop
    # (qty · credit · % paid · day).
    item_credit: dict = {}
    item_units: dict = {}
    cat_credit: dict = {}
    item_meta: dict = {}
    for (item_id, shop_id), entry in by_pair.items():
        item_credit[item_id] = item_credit.get(item_id, _zero()) + entry["credit"]
        item_units[item_id] = item_units.get(item_id, _zero()) + entry["units"]
        meta = item_meta.get(item_id)
        if meta is None:
            cat = (categories.get(item_id) or "").strip() or "Uncategorised"
            meta = {"name": entry["name"], "cat": cat, "pairs": []}
            item_meta[item_id] = meta
            cat_credit.setdefault(cat, _zero())
        meta["pairs"].append((shop_id, entry))
        cat_credit[meta["cat"]] += entry["credit"]

    rows = []
    total_credit = _zero()
    total_paid = _zero()
    unpaid_items = 0
    for cat in sorted(cat_credit, key=lambda c: (-cat_credit[c], c.lower())):
        rows.append(
            {
                "group_header": True,
                "label": f"{cat} — {_money_dense(cat_credit[cat])}",
                "search_text": cat,
            }
        )
        cat_item_ids = sorted(
            (iid for iid, m in item_meta.items() if m["cat"] == cat),
            key=lambda iid: (
                -item_units[iid],
                -item_credit[iid],
                (item_meta[iid]["name"] or "").lower(),
            ),
        )
        for iid in cat_item_ids:
            rows.append(
                _credit_items_row(
                    meta=item_meta[iid],
                    cat=cat,
                    shops=shops,
                    shop_name=shop_name,
                )
            )
    for row in rows:
        if row.get("group_header"):
            continue
        total_credit += row["_credit"]
        total_paid += row["_paid"]
        if row["_credit"] > 0 and row["_paid"] <= 0:
            unpaid_items += 1

    # Trailing totals row — grand credit/paid per shop across listed items.
    shop_totals: dict = {}
    grand = {
        "units": _zero(),
        "credit": _zero(),
        "paid": _zero(),
        "days": set(),
        "day_units": {},
        "weekdays": {},
    }
    for (_, sid), entry in by_pair.items():
        bucket = shop_totals.setdefault(
            sid,
            {
                "units": _zero(),
                "credit": _zero(),
                "paid": _zero(),
                "days": set(),
                "day_units": {},
                "weekdays": {},
            },
        )
        for field in ("units", "credit", "paid"):
            bucket[field] += entry[field]
            grand[field] += entry[field]
        for scope in (bucket, grand):
            scope["days"] |= entry["days"]
            for day, day_qty in entry["day_units"].items():
                scope["day_units"][day] = (
                    scope["day_units"].get(day, _zero()) + day_qty
                )
            for wd, wd_qty in entry["weekdays"].items():
                scope["weekdays"][wd] = (
                    scope["weekdays"].get(wd, _zero()) + wd_qty
                )

    if rows:
        total_cells = [{"label": "All items"}]
        for shop in shops:
            bucket = shop_totals.get(shop.pk)
            label = shop_name.get(shop.pk) or "Shop"
            if bucket is None or not bucket["days"]:
                total_cells.append(_credit_items_empty_quad(label))
                continue
            pct = _credit_items_paid_pct(bucket["credit"], bucket["paid"])
            day, day_title = _credit_items_best_day(bucket)
            total_cells.append(
                _credit_items_quad(
                    units=bucket["units"],
                    credit=bucket["credit"],
                    paid=bucket["paid"],
                    day=day,
                    title=_credit_items_cell_title(
                        f"{label} — all items",
                        units=bucket["units"],
                        credit=bucket["credit"],
                        paid=bucket["paid"],
                        pct=pct,
                        day_title=day_title,
                    ),
                )
            )
        pct = _credit_items_paid_pct(grand["credit"], grand["paid"])
        day, day_title = _credit_items_best_day(grand)
        total_cells.append(
            _credit_items_quad(
                units=grand["units"],
                credit=grand["credit"],
                paid=grand["paid"],
                day=day,
                title=_credit_items_cell_title(
                    "All shops — all items",
                    units=grand["units"],
                    credit=grand["credit"],
                    paid=grand["paid"],
                    pct=pct,
                    day_title=day_title,
                ),
            )
        )
        rows.append(
            {
                "cells": total_cells,
                "search_text": "all items total",
                "_total": True,
            }
        )

    total_due = _due_amount(total_credit, total_paid)

    alerts = []
    if unpaid_items:
        alerts.append(
            _alert(
                "warning",
                "Items with no payment",
                f"{unpaid_items} item{'s' if unpaid_items != 1 else ''} on "
                f"credit show 0% paid back in this period.",
                "Follow up with the clients holding these credits.",
            )
        )

    columns = [{"label": "Item"}]
    for index, shop in enumerate(shops):
        col = _shop_col(shop)
        col.update(
            {
                "quad": True,
                "sub_qty": "Qty",
                "sub_amt": "Credit",
                "sub_pct": "% paid",
                "sub_day": "Day",
                "band": "flow",
            }
        )
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append(
        {
            "label": "Total",
            "title": "All selected shops",
            "quad": True,
            "sub_qty": "Qty",
            "sub_amt": "Credit",
            "sub_pct": "% paid",
            "sub_day": "Day",
            "total": True,
            "band": "total",
            "band_start": True,
        }
    )

    period_label = filters.get("report_period_label") or "selected period"
    due_today = _credit_due_today_followups(filters)
    return {
        "headline": "Credits",
        "lead": (
            f"Items on credit for {period_label} — each shop column shows "
            f"qty, credit amount, % paid back, and top credit day."
        ),
        "alerts": alerts,
        "show_search": True,
        "search_query": search_query,
        "search_placeholder": "Search items or shops…",
        "search_empty": "No rows match that search.",
        "download_href": _pdf_download_href(filters.get("query") or ""),
        "summary_board": _credit_items_board(
            total_credit=total_credit,
            total_paid=total_paid,
            total_due=total_due,
            docs=docs,
            item_count=len(item_credit),
            shop_count=len(shops),
        ),
        "insights": [],
        "credit_due_today": due_today,
        "tables": [
            _table(
                "Credit items by shop",
                columns,
                rows,
                empty=(
                    "Select a shop to view credits."
                    if not shops
                    else "No credit items for selected shops and period."
                ),
                footnote=(
                    "Credit receipts net of returns, this period. Each item's "
                    "credit is its share of the receipt total (incl. tax). "
                    "% paid applies the receipt's collected share to that "
                    "item — hover a cell for paid and due amounts. Day is the "
                    "weekday the item is credited most at that shop."
                ),
                shop_grid=True,
                searchable=True,
            ),
        ],
    }


WALKIN_CLIENT_KEY = "walkin"


def _client_credit_stock_map(receipts_qs) -> dict:
    """Buying cost of items on each client's credit-account receipts."""
    line_qs = ShopReceiptLine.objects.filter(receipt__in=receipts_qs)
    remaining_cogs = ExpressionWrapper(
        F("unit_cost") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    stock: dict = {}
    for row in line_qs.values("receipt__client_id").annotate(
        cogs=Coalesce(Sum(remaining_cogs), _zero())
    ):
        client_id = row["receipt__client_id"]
        if client_id is None:
            client_id = WALKIN_CLIENT_KEY
        stock[client_id] = Decimal(row["cogs"] or 0).quantize(Decimal("0.01"))

    missing = list(
        line_qs.filter(unit_cost=0)
        .exclude(item_id__isnull=True)
        .values("receipt__client_id", "item_id", "quantity", "returned_quantity")
    )
    if missing:
        from items.services import last_buying_prices_for_items

        fallback = last_buying_prices_for_items(
            {row["item_id"] for row in missing if row["item_id"]}
        )
        for row in missing:
            unit = Decimal(fallback.get(row["item_id"]) or 0)
            remaining = max(
                Decimal("0"),
                Decimal(row["quantity"] or 0)
                - Decimal(row["returned_quantity"] or 0),
            )
            if unit <= 0 or remaining <= 0:
                continue
            client_id = row["receipt__client_id"]
            if client_id is None:
                client_id = WALKIN_CLIENT_KEY
            stock[client_id] = stock.get(client_id, _zero()) + (
                unit * remaining
            ).quantize(Decimal("0.01"))
    return stock


def _build_clients(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    role = filters["role"]
    query = filters.get("query") or ""
    from_credits = bool(filters.get("from_credits"))
    start = filters.get("start")
    end = filters.get("end")
    period_label = filters.get("report_period_label") or "selected period"
    # Credits embed stays credit-only; standalone Clients supports sales/credits/quotations.
    flags = filters.get("pos_flags") or _pos_feature_flags()
    receipt_kind = (
        "credits"
        if from_credits
        else _enabled_client_receipt_kind(
            _parse_client_receipt_kind(filters.get("receipt_kind")), flags
        )
    )
    credit_mode = receipt_kind == "credits"
    period_scoped = start is not None and end is not None
    outstanding_due = Greatest(
        ExpressionWrapper(
            F("total") - F("amount_paid"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        Value(0, output_field=DecimalField(max_digits=12, decimal_places=2)),
    )

    all_receipts = ShopReceipt.objects.filter(shop_id__in=shop_ids).filter(
        _client_activity_receipts_q(receipt_kind, flags)
    )
    if filters.get("receipt_status"):
        all_receipts = _apply_analytics_receipt_status(
            all_receipts, filters.get("receipt_status")
        )
    else:
        all_receipts = all_receipts.exclude(status=ShopReceiptStatus.CANCELLED)
    receipts = (
        _within_created_range(all_receipts, start, end) if period_scoped else all_receipts
    )

    # client_id -> {shop_id: (docs, amount_or_owed)}
    by_client_shop: dict[int, dict[int, tuple[int, Decimal]]] = {}
    totals_by_client: dict[int, tuple[int, Decimal]] = {}
    paid_by_client: dict[int, Decimal] = {}
    gross_by_client: dict[int, Decimal] = {}
    amount_agg = (
        Coalesce(Sum(outstanding_due), _zero())
        if credit_mode
        else Coalesce(Sum("total"), _zero())
    )
    for row in receipts.values("client_id", "shop_id").annotate(
        docs=Count("id"),
        amount=amount_agg,
        paid=Coalesce(Sum("amount_paid"), _zero()),
        gross=Coalesce(Sum("total"), _zero()),
    ):
        client_id = row["client_id"]
        if client_id is None:
            client_id = WALKIN_CLIENT_KEY
        docs_n = int(row["docs"] or 0)
        amount = (
            _due_amount(row["amount"], 0)
            if credit_mode
            else Decimal(row["amount"] or 0)
        )
        if docs_n <= 0:
            continue
        shop_map = by_client_shop.setdefault(client_id, {})
        prev_docs, prev_amount = shop_map.get(row["shop_id"], (0, _zero()))
        shop_map[row["shop_id"]] = (prev_docs + docs_n, prev_amount + amount)
        total_docs, total_amount = totals_by_client.get(client_id, (0, _zero()))
        totals_by_client[client_id] = (
            total_docs + docs_n,
            total_amount + amount,
        )
        paid_by_client[client_id] = paid_by_client.get(client_id, _zero()) + Decimal(
            row["paid"] or 0
        )
        gross_by_client[client_id] = gross_by_client.get(
            client_id, _zero()
        ) + Decimal(row["gross"] or 0)

    stock_by_client = _client_credit_stock_map(receipts) if credit_mode else {}

    if period_scoped:
        client_keys_in_scope = set(totals_by_client.keys())
    else:
        client_keys_in_scope = {
            key if key is not None else WALKIN_CLIENT_KEY
            for key in all_receipts.values_list("client_id", flat=True).distinct()
        }
    has_walkins = WALKIN_CLIENT_KEY in client_keys_in_scope
    client_ids_in_scope = {
        key for key in client_keys_in_scope if key != WALKIN_CLIENT_KEY
    }
    ranked = []
    for client in Client.objects.filter(pk__in=client_ids_in_scope).order_by(
        "full_name", "id"
    ):
        name = client.full_name or "Client"
        phone = (client.phone_number or "").strip()
        total_docs, total_amount = totals_by_client.get(client.pk, (0, _zero()))
        ranked.append(
            (
                total_amount,
                name,
                phone,
                client.pk,
                by_client_shop.get(client.pk) or {},
                total_docs,
                total_amount,
            )
        )
    if has_walkins:
        walk_docs, walk_amount = totals_by_client.get(
            WALKIN_CLIENT_KEY, (0, _zero())
        )
        ranked.append(
            (
                walk_amount,
                "Walk-in",
                "",
                WALKIN_CLIENT_KEY,
                by_client_shop.get(WALKIN_CLIENT_KEY) or {},
                walk_docs,
                walk_amount,
            )
        )

    if credit_mode:
        # Open balances first (highest owed), then cleared clients (owed 0) at the bottom.
        ranked.sort(
            key=lambda row: (
                Decimal(row[0] or 0) <= 0,
                -Decimal(row[0] or 0),
                row[1].lower(),
            )
        )
    else:
        ranked.sort(key=lambda row: (-Decimal(row[0] or 0), row[1].lower()))

    pair_qty = "Txns" if credit_mode else "Docs"
    pair_amt = "Owed" if credit_mode else "Amt"
    kind_labels = {
        "all": "sales, credits, quotations, and trades",
        "trade_out": "trade outs",
        "trade_in": "trade ins",
        "tradings": "trades",
        "sales": "sales",
        "credits": "credit receipts",
        "quotations": "quotations",
    }
    kind_label = kind_labels.get(receipt_kind, "documents")

    clients_from = (filters.get("clients_from") or "").strip()
    row_query = query
    if clients_from:
        row_query = (
            f"{query}&from={clients_from}" if query else f"from={clients_from}"
        )

    walkin_href = ""
    if has_walkins:
        from urllib.parse import parse_qs, urlencode

        from employees.workspace import analytics_section_url

        walkin_params = parse_qs(query, keep_blank_values=True) if query else {}
        for drop_key in ("q", "download", "from", "client"):
            walkin_params.pop(drop_key, None)
        walkin_params["client"] = ["walkin"]
        if credit_mode and not walkin_params.get("receipt_kind"):
            walkin_params["receipt_kind"] = ["credits"]
        encoded = urlencode(walkin_params, doseq=True)
        walkin_href = analytics_section_url(role, "receipts")
        if encoded:
            walkin_href = f"{walkin_href}?{encoded}"

    client_rows = []
    for (
        _sort_amount,
        name,
        phone,
        client_id,
        by_shop,
        total_docs,
        total_amount,
    ) in ranked:
        if client_id == WALKIN_CLIENT_KEY:
            client_cell = {
                "href": walkin_href,
                "label": name,
                "sub": "No client record",
            }
            phone_cell = {
                "label": "—",
                "title": "Receipts with no linked client record",
            }
        else:
            client_cell = {
                "href": client_credit_account_url(
                    role, client_id, query=row_query, from_credits=from_credits
                ),
                "label": name,
            }
            phone_cell = {
                "label": phone or "—",
                "title": phone or "No phone on file",
            }
        cells = [client_cell, phone_cell]
        for shop in shops:
            shop_docs, shop_amount = by_shop.get(shop.pk, (0, _zero()))
            if credit_mode:
                title = (
                    f"{shop_docs} credit transaction"
                    f"{'' if shop_docs == 1 else 's'} · "
                    f"owed {_money_ksh(shop_amount)}"
                )
            else:
                title = (
                    f"{shop_docs} document"
                    f"{'' if shop_docs == 1 else 's'} · "
                    f"{_money_ksh(shop_amount)}"
                )
            cells.append(_qty_amount_cell(shop_docs, shop_amount, title=title))
        if credit_mode:
            total_title = (
                f"{total_docs} credit transaction"
                f"{'' if total_docs == 1 else 's'} · "
                f"owed {_money_ksh(total_amount)}"
            )
        else:
            total_title = (
                f"{total_docs} document"
                f"{'' if total_docs == 1 else 's'} · "
                f"{_money_ksh(total_amount)}"
            )
        cells.append(_qty_amount_cell(total_docs, total_amount, title=total_title))
        if credit_mode:
            stock_value = stock_by_client.get(client_id, _zero())
            cells.append(
                _money_cell(
                    stock_value,
                    title=f"Stock value {_money_ksh(stock_value)}",
                )
            )
        client_rows.append(
            {
                "cells": cells,
                "search_text": f"{name} {phone}".strip(),
                "paid": paid_by_client.get(client_id, _zero()),
                "gross": gross_by_client.get(client_id, _zero()),
            }
        )

    if credit_mode and client_rows:
        total_cells = ["Total", "—"]
        all_docs = 0
        all_amount = _zero()
        for shop in shops:
            s_docs = 0
            s_amount = _zero()
            for shop_map in by_client_shop.values():
                docs_n, amount_n = shop_map.get(shop.pk, (0, _zero()))
                s_docs += docs_n
                s_amount += amount_n
            all_docs += s_docs
            all_amount += s_amount
            total_cells.append(
                _qty_amount_cell(
                    s_docs,
                    s_amount,
                    title=f"{s_docs} · owed {_money_ksh(s_amount)}",
                )
            )
        total_cells.append(
            _qty_amount_cell(
                all_docs,
                all_amount,
                title=f"{all_docs} · owed {_money_ksh(all_amount)}",
            )
        )
        total_stock = sum(stock_by_client.values(), _zero())
        total_cells.append(
            _money_cell(
                total_stock,
                title=f"Stock value {_money_ksh(total_stock)}",
            )
        )
        client_rows.append(
            {
                "cells": total_cells,
                "search_text": "Total",
                "paid": sum(paid_by_client.values(), _zero()),
                "gross": sum(gross_by_client.values(), _zero()),
            }
        )

    columns = [
        "Name",
        {
            "label": "Phone",
            "phone": True,
            "title": "Client phone number",
        },
    ]
    for shop in shops:
        columns.append(_shop_col(shop, pair=True, pair_qty=pair_qty, pair_amt=pair_amt))
    columns.append(_pair_total_col(pair_qty=pair_qty, pair_amt=pair_amt))
    if credit_mode:
        columns.append(
            {
                "label": "Stock",
                "title": "Buying cost of items on the client's credit receipts",
                "money": True,
                "band": "result",
                "band_start": True,
            }
        )

    alerts = []
    if from_credits and period_scoped:
        period_count = receipts.count()
        all_count = all_receipts.count()
        other_count = max(0, all_count - period_count)
        if other_count > 0:
            from employees.workspace import analytics_section_url

            all_query = _query_with_all_time(query)
            all_href = (
                filters.get("credits_home_href")
                or analytics_section_url(role, "credits")
            )
            if all_query:
                all_href = f"{all_href}?{all_query}"
            alerts.append(
                _alert(
                    "info",
                    "Other credits outside this period",
                    (
                        f"{other_count} credit receipt"
                        f"{'' if other_count == 1 else 's'} in the selected shops "
                        f"fall outside {period_label}."
                    ),
                    href=all_href,
                    href_label="View other credits",
                )
            )

    if credit_mode:
        if period_scoped:
            footnote = (
                "Each client row opens the account ledger for the same period. "
                "Txns and Owed are credit receipts created in this period for the "
                "selected shops (including cleared credits). Txns = non-cancelled "
                "credit receipts; Owed = unpaid balance per receipt, never below "
                "zero. Stock = buying cost of items on those receipts net of "
                "returns. Cleared clients (owed 0) are listed last. The Walk-in "
                "row combines credit receipts with no linked client record."
            )
            empty = (
                "Select a shop to view credit clients."
                if not shop_ids
                else f"No credit clients for the selected shops in {period_label}."
            )
        else:
            footnote = (
                "Each client row opens the account ledger. "
                "Txns and Owed are all-time for the selected shops (including "
                "cleared credits). Txns = non-cancelled credit receipts; "
                "Owed = unpaid balance per receipt, never below zero. "
                "Stock = buying cost of items on those receipts net of returns. "
                "Cleared clients (owed 0) are listed last. The Walk-in row "
                "combines credit receipts with no linked client record."
            )
            empty = (
                "Select a shop to view credit clients."
                if not shop_ids
                else "No credit clients in the selected shops."
            )
        table_title = "Client credit accounts by shop"
        lead = ""
    else:
        scope_note = period_label if period_scoped else "all time"
        footnote = (
            f"Each client row opens the account ledger. Docs and Amt are "
            f"{kind_label} for the selected shops in {scope_note}. "
            "Cancelled receipts are excluded. The Walk-in row combines all "
            "receipts with no linked client record and opens the receipts "
            "list filtered to walk-ins."
        )
        empty = (
            "Select a shop to view clients."
            if not shop_ids
            else f"No clients with {kind_label} for the selected shops in {scope_note}."
        )
        table_title = {
            "all": "Clients by shop",
            "sales": "Sales clients by shop",
            "quotations": "Quotation clients by shop",
        }.get(receipt_kind, "Clients by shop")
        lead = f"Clients with {kind_label} · {scope_note}."

    client_rows, client_pager = _page_table_rows(
        client_rows,
        filters,
        searchable=True,
        tail_rows=1 if credit_mode and client_rows else 0,
    )
    return {
        "headline": "Clients",
        "lead": lead,
        "alerts": alerts,
        "metrics": [],
        "insights": [],
        "show_search": True,
        "search_placeholder": "Search clients by name or phone…",
        "search_empty": "No clients match that search.",
        "receipt_kind": receipt_kind,
        "receipt_kind_options": _client_receipt_kind_options(flags),
        "tables": [
            _table(
                table_title,
                columns,
                client_rows,
                empty=empty,
                footnote=footnote,
                shop_grid=True,
                searchable=True,
                pager=client_pager,
            )
        ],
    }


def _build_employees(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    sales = _receipts_qs(
        shop_ids=shop_ids,
        start=filters["start"],
        end=filters["end"],
        kinds=[ShopReceiptKind.SALE],
        receipt_status=filters.get("receipt_status"),
    )

    # staff_key -> {label, by_shop: {shop_id: (count, total)}, totals}
    by_staff: dict = {}
    for row in sales.values(
        "created_by_id",
        "created_by__employee_id",
        "created_by__user__first_name",
        "created_by__user__last_name",
        "created_by__user__username",
        "shop_id",
    ).annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
    ):
        docs = int(row["docs"] or 0)
        amount = Decimal(row["amount"] or 0)
        if docs <= 0 and not amount:
            continue
        first = (row["created_by__user__first_name"] or "").strip()
        last = (row["created_by__user__last_name"] or "").strip()
        name = (
            f"{first} {last}".strip()
            or row["created_by__user__username"]
            or "Staff"
        )
        emp_id = row["created_by__employee_id"] or ""
        label = f"{name} · {emp_id}" if emp_id else name
        key = row["created_by_id"] if row["created_by_id"] is not None else f"name:{label}"
        entry = by_staff.get(key)
        if entry is None:
            entry = {
                "label": label,
                "staff_pk": row["created_by_id"],
                "by_shop": {},
                "total_docs": 0,
                "total_amount": _zero(),
            }
            by_staff[key] = entry
        shop_id = row["shop_id"]
        prev_docs, prev_amount = entry["by_shop"].get(shop_id, (0, _zero()))
        entry["by_shop"][shop_id] = (prev_docs + docs, prev_amount + amount)
        entry["total_docs"] += docs
        entry["total_amount"] += amount

    columns = ["Staff"]
    for shop in shops:
        columns.append(_shop_col(shop, pair=True, pair_qty="Docs", pair_amt="Amt"))
    columns.append(_pair_total_col(pair_qty="Docs", pair_amt="Amt"))

    table_rows = []
    role = filters.get("role")
    query = filters.get("query") or ""
    for entry in sorted(
        by_staff.values(),
        key=lambda row: (-row["total_amount"], row["label"].lower()),
    ):
        if entry["staff_pk"] and role is not None:
            cells = [
                {
                    "href": analytics_employee_url(
                        role,
                        entry["staff_pk"],
                        query=query,
                    ),
                    "label": entry["label"],
                }
            ]
        else:
            cells = [entry["label"]]
        for shop in shops:
            docs, amount = entry["by_shop"].get(shop.pk, (0, _zero()))
            cells.append(
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{docs} · {_money_ksh(amount)}",
                )
            )
        cells.append(
            _qty_amount_cell(
                entry["total_docs"],
                entry["total_amount"],
                title=f"{entry['total_docs']} · {_money_ksh(entry['total_amount'])}",
            )
        )
        table_rows.append(cells)

    return {
        "headline": "Employees",
        "lead": "",
        "alerts": [],
        "metrics": [],
        "insights": [],
        "tables": [
            _table(
                "Cashier sales by shop",
                columns,
                table_rows,
                empty="No cashier sales for selected shops and period.",
            )
        ],
    }


SUPPLIER_STATUS_FILTERS = (
    ("all", "All"),
    ("not_paid", "Not paid"),
    ("paid", "Paid"),
)


def _parse_supplier_status(raw) -> str:
    """Normalise suppliers analytics ?status=… (default: all = paid + not paid)."""
    key = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "all",
        "all": "all",
        "paid": "paid",
        "not_paid": "not_paid",
        "unpaid": "not_paid",
        "notpaid": "not_paid",
        # Legacy aliases → not paid (any outstanding balance).
        "pending": "not_paid",
        "pending_payment": "not_paid",
    }
    return aliases.get(key, "all")


def _supplier_status_label(status_key: str) -> str:
    return dict(SUPPLIER_STATUS_FILTERS).get(status_key, "All")


def _supplier_movement_matches_status(
    *, status_key: str, total: Decimal, paid: Decimal, due: Decimal
) -> bool:
    """Whether a stock-in movement belongs in the selected payment status."""
    if status_key == "all":
        return True
    if status_key == "not_paid":
        return due > 0
    if status_key == "paid":
        return total > 0 and due <= 0
    return True


def _supplier_entity_cell(role, kind, supplier, query: str) -> dict:
    phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
    name = getattr(supplier, "name", None) or "Supplier"
    cell = {
        "href": supplier_account_url(role, kind, supplier.pk, query=query),
        "label": name,
    }
    if phone:
        cell["sub"] = phone
    return cell


def _supplier_label(supplier) -> str:
    phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
    name = getattr(supplier, "name", None) or "Supplier"
    return f"{name} · {phone}" if phone else name


def _supplier_match_key(name, country_code, phone_number) -> tuple[str, str, str]:
    return (
        (name or "").strip().lower(),
        (country_code or "").strip(),
        (phone_number or "").strip(),
    )


def _supplier_phone_digits(*parts) -> str:
    """Canonical national phone digits — merges +254 / 0-prefix formats."""
    digits = "".join(
        ch for ch in "".join(str(part or "") for part in parts) if ch.isdigit()
    )
    digits = digits.lstrip("0")
    while digits.startswith("254") and len(digits) > 9:
        digits = digits[3:]
    return digits


def _supplier_identity_key(name, *phone_parts) -> tuple[str, str]:
    """Cross-dataset supplier identity: lowercased name + national phone."""
    return ((name or "").strip().lower(), _supplier_phone_digits(*phone_parts))


def _supplier_ledger_stats(suppliers, by_supplier_shop: dict) -> dict:
    """Aggregate supplier counts / entries / outstanding for KPI strip."""
    on_file = len(suppliers)
    active = 0
    with_balance = 0
    total_entries = 0
    total_balance = _zero()
    for supplier in suppliers:
        by_shop = by_supplier_shop.get(supplier.pk) or {}
        entries = 0
        balance = _zero()
        for shop_entries, shop_balance in by_shop.values():
            entries += int(shop_entries or 0)
            balance += Decimal(shop_balance or 0)
        total_entries += entries
        total_balance += balance
        if entries > 0 or balance > 0:
            active += 1
        if balance > 0:
            with_balance += 1
    return {
        "on_file": on_file,
        "active": active,
        "with_balance": with_balance,
        "entries": total_entries,
        "balance": total_balance,
    }


def _supplier_shop_rows(
    *,
    suppliers,
    shops,
    by_supplier_shop: dict,
    kind: str,
    role,
    query: str,
    supplier_priority: dict | None = None,
    cell_for=None,
) -> tuple[list, dict[int, tuple[int, Decimal]]]:
    """Build matrix rows: supplier link + En/Bal per shop + total.

    When supplier_priority is provided (0 = not paid, 1 = paid), not-paid
    suppliers sort above paid ones; within each group, highest Bal first.
    cell_for overrides the first-cell renderer (default: supplier account
    link); shelf suppliers pass one since they have no account page.
    """
    shop_totals: dict[int, tuple[int, Decimal]] = {
        shop.pk: (0, _zero()) for shop in shops
    }
    ranked = []
    for supplier in suppliers:
        by_shop = by_supplier_shop.get(supplier.pk) or {}
        total_entries = 0
        total_balance = _zero()
        for entries, balance in by_shop.values():
            total_entries += int(entries or 0)
            total_balance += Decimal(balance or 0)
        priority = 0
        if supplier_priority is not None:
            priority = int(supplier_priority.get(supplier.pk, 1))
        ranked.append(
            (
                priority,
                total_balance,
                _supplier_label(supplier).lower(),
                supplier,
                by_shop,
                total_entries,
                total_balance,
            )
        )
    ranked.sort(key=lambda row: (row[0], -row[1], row[2]))

    rows = []
    grand_entries = 0
    grand_balance = _zero()
    for (
        _priority,
        _balance,
        _sort_label,
        supplier,
        by_shop,
        total_entries,
        total_balance,
    ) in ranked:
        grand_entries += total_entries
        grand_balance += total_balance
        if cell_for is not None:
            cells = [cell_for(supplier)]
        else:
            cells = [_supplier_entity_cell(role, kind, supplier, query)]
        for shop in shops:
            shop_entries, shop_balance = by_shop.get(shop.pk, (0, _zero()))
            prev_entries, prev_balance = shop_totals.get(shop.pk, (0, _zero()))
            shop_totals[shop.pk] = (
                prev_entries + int(shop_entries or 0),
                prev_balance + Decimal(shop_balance or 0),
            )
            cells.append(
                _qty_amount_cell(
                    shop_entries,
                    shop_balance,
                    title=f"{shop_entries} entries · {_money_ksh(shop_balance)}",
                    tone="warn" if Decimal(shop_balance or 0) > 0 else "neutral",
                )
                if shop_entries or shop_balance
                else "—"
            )
        cells.append(
            _qty_amount_cell(
                total_entries,
                total_balance,
                title=f"{total_entries} entries · {_money_ksh(total_balance)}",
                tone="warn" if Decimal(total_balance or 0) > 0 else "neutral",
            )
        )
        rows.append(cells)

    if ranked:
        total_cells = ["Total"]
        for shop in shops:
            shop_entries, shop_balance = shop_totals.get(shop.pk, (0, _zero()))
            total_cells.append(
                _qty_amount_cell(
                    shop_entries,
                    shop_balance,
                    title=f"{shop_entries} entries · {_money_ksh(shop_balance)}",
                    tone="warn" if Decimal(shop_balance or 0) > 0 else "neutral",
                )
            )
        total_cells.append(
            _qty_amount_cell(
                grand_entries,
                grand_balance,
                title=f"{grand_entries} entries · {_money_ksh(grand_balance)}",
                tone="warn" if Decimal(grand_balance or 0) > 0 else "neutral",
            )
        )
        rows.append(total_cells)

    return rows, shop_totals


def _supplier_summary_board(
    stats: dict,
    *,
    shop_count: int,
    shops_with_balance: int,
    entity_label: str = "Suppliers",
    icon: str = "truck",
) -> dict:
    balance = Decimal(stats["balance"] or 0)
    on_file = int(stats["on_file"] or 0)
    active = int(stats["active"] or 0)
    with_balance = int(stats["with_balance"] or 0)
    entries = int(stats["entries"] or 0)
    shops = int(shop_count or 0)
    exposed = int(shops_with_balance or 0)
    return {
        "hero": {
            "label": "Outstanding balance",
            "value": _money_ksh(balance),
            "hint": (
                f"{entries:,} entries · {with_balance} supplier"
                f"{'s' if with_balance != 1 else ''} with balance"
            ),
            "tone": "warn" if balance > 0 else "good",
        },
        "tiles": [
            {
                "label": entity_label,
                "value": str(on_file),
                "hint": f"{active} with activity",
                "icon": icon,
                "tone": "shops",
            },
            {
                "label": "Entries",
                "value": f"{entries:,}",
                "hint": "Linked receipts",
                "icon": "file-text",
                "tone": "sales",
            },
            {
                "label": "Shops",
                "value": str(exposed),
                "hint": f"of {shops} with balance" if shops else "selected",
                "icon": "store",
                "tone": "warn" if exposed else "good",
            },
        ],
    }


def _supplier_pair_columns(shops) -> list:
    columns = ["Supplier"]
    for index, shop in enumerate(shops):
        col = _shop_col(shop, pair=True, pair_qty="En", pair_amt="Bal")
        col["band"] = "flow"
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append(
        {**_pair_total_col(pair_qty="En", pair_amt="Bal"), "band": "total", "band_start": True}
    )
    return columns


def _supplier_status_amount(*, status_key: str, due, paid, supplier_due) -> Decimal:
    """Cell amount for the Bal column under the selected status filter."""
    if status_key == "paid":
        return paid
    if status_key == "all":
        return due if supplier_due > 0 else paid
    return due


def _empty_supplier_stats() -> dict:
    return {
        "on_file": 0,
        "active": 0,
        "with_balance": 0,
        "entries": 0,
        "balance": _zero(),
    }


def _item_supplier_data(*, shop_ids, shops, start, end, status_key):
    """Item stock-in suppliers → identity-keyed entries for matrix building.

    Returns ``{identity_key: {"supplier", "cells", "priority"}}`` where cells
    maps ``shop_id -> (entries, status-adjusted amount)``.
    """
    stock_suppliers = list(Supplier.objects.order_by("name", "id"))
    key_to_supplier_id = {
        _supplier_match_key(
            supplier.name, supplier.phone_country_code, supplier.phone_number
        ): supplier.pk
        for supplier in stock_suppliers
    }
    suppliers_by_id = {supplier.pk: supplier for supplier in stock_suppliers}

    # movement_id -> {supplier_id, shop_id, total, paid}
    movements: dict[int, dict] = {}
    stock_lines = _within_created_range(
        StockMovementLine.objects.filter(
            movement__shop_id__in=shop_ids,
            movement__movement_type=StockMovementType.IN,
        ),
        start,
        end,
        lookup="movement__created_at",
    ).select_related("movement").only(
        "quantity",
        "buying_price",
        "supplier_name",
        "supplier_phone_country_code",
        "supplier_phone_number",
        "movement_id",
        "movement__shop_id",
        "movement__amount_paid",
    )
    for line in stock_lines:
        movement = line.movement
        if movement is None:
            continue
        supplier_id = key_to_supplier_id.get(
            _supplier_match_key(
                line.supplier_name,
                line.supplier_phone_country_code,
                line.supplier_phone_number,
            )
        )
        if supplier_id is None:
            continue
        qty = Decimal(line.quantity or 0)
        unit = Decimal(line.buying_price or 0)
        line_total = (unit * qty).quantize(Decimal("0.01"))
        bundle = movements.get(movement.pk)
        if bundle is None:
            movements[movement.pk] = {
                "supplier_id": supplier_id,
                "shop_id": movement.shop_id,
                "total": line_total,
                "paid": Decimal(movement.amount_paid or 0),
            }
        else:
            bundle["total"] += line_total

    # supplier -> shop -> {entries, due, paid} for period supply only.
    stock_detail: dict[int, dict[int, dict]] = {}
    for bundle in movements.values():
        total = Decimal(bundle["total"] or 0)
        paid = Decimal(bundle["paid"] or 0)
        due = _due_amount(total, paid)
        if not _supplier_movement_matches_status(
            status_key=status_key, total=total, paid=paid, due=due
        ):
            continue
        supplier_id = bundle["supplier_id"]
        shop_id = bundle["shop_id"]
        cell = stock_detail.setdefault(supplier_id, {}).setdefault(
            shop_id, {"entries": 0, "due": _zero(), "paid": _zero()}
        )
        cell["entries"] += 1
        cell["due"] += due
        cell["paid"] += paid

    supplier_priority: dict[int, int] = {}
    for supplier_id, shop_map in stock_detail.items():
        supplier_due = sum(
            (Decimal(cell["due"] or 0) for cell in shop_map.values()),
            _zero(),
        )
        # 0 = not paid (any outstanding), 1 = fully paid — unpaid first in All.
        supplier_priority[supplier_id] = 0 if supplier_due > 0 else 1

    # Only suppliers who supplied in this filtered period.
    matched_ids = set(stock_detail.keys())
    entries: dict[tuple, dict] = {}
    for sid in matched_ids:
        supplier = suppliers_by_id.get(sid)
        if supplier is None:
            continue
        key = _supplier_identity_key(
            supplier.name, supplier.phone_country_code, supplier.phone_number
        )
        target = entries.get(key)
        if target is None:
            target = entries[key] = {
                "supplier": supplier,
                "cells": {},
                "priority": 1,
            }
        for shop_id, cell in stock_detail[sid].items():
            prev = target["cells"].get(shop_id, (0, _zero(), _zero()))
            target["cells"][shop_id] = (
                prev[0] + int(cell["entries"] or 0),
                prev[1] + Decimal(cell["due"] or 0),
                prev[2] + Decimal(cell["paid"] or 0),
            )
        target["priority"] = min(
            target["priority"], supplier_priority.get(sid, 1)
        )
    return entries


def _supplier_status_cells(cells: dict, status_key: str) -> dict:
    """(entries, due, paid) per shop → (entries, status-adjusted amount)."""
    supplier_due = sum(
        (Decimal(due or 0) for _n, due, _paid in cells.values()), _zero()
    )
    converted = {}
    for shop_id, (n, due, paid) in cells.items():
        converted[shop_id] = (
            n,
            _supplier_status_amount(
                status_key=status_key,
                due=Decimal(due or 0),
                paid=Decimal(paid or 0),
                supplier_due=supplier_due,
            ),
        )
    return converted


def _item_supplier_rows(*, shop_ids, shops, start, end, status_key, role, query):
    """Item stock-in suppliers (Supplier registry) → matrix rows + stats."""
    data = _item_supplier_data(
        shop_ids=shop_ids, shops=shops, start=start, end=end,
        status_key=status_key,
    )
    suppliers = [entry["supplier"] for entry in data.values()]
    by_pk = {
        entry["supplier"].pk: _supplier_status_cells(entry["cells"], status_key)
        for entry in data.values()
    }
    priority = {
        entry["supplier"].pk: entry["priority"] for entry in data.values()
    }
    rows, shop_totals = _supplier_shop_rows(
        suppliers=suppliers,
        shops=shops,
        by_supplier_shop=by_pk,
        kind="stock",
        role=role,
        query=query,
        supplier_priority=priority if status_key == "all" else None,
    )
    stats = _supplier_ledger_stats(suppliers, by_pk)
    return rows, stats, shop_totals


def shelf_supplier_account_url(
    role, *, name: str, phone: str = "", query: str = ""
) -> str:
    """Shelf supplier ledger URL — keyed by name/phone, not a Supplier pk."""
    from urllib.parse import parse_qs, urlencode

    from django.urls import reverse

    from employees.access import role_url_segment

    params = parse_qs(query, keep_blank_values=True)
    params["name"] = [name or ""]
    params["phone"] = [phone or ""]
    href = reverse(
        "employees:analytics_shelf_supplier_account",
        kwargs={"role_segment": role_url_segment(role)},
    )
    encoded = urlencode({k: v[0] for k, v in params.items()})
    return f"{href}?{encoded}" if encoded else href


def _shelf_supplier_cell(supplier, *, role, query: str) -> dict:
    """Name + phone cell linking to the shelf supplier ledger."""
    phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
    cell = {
        "href": shelf_supplier_account_url(
            role,
            name=getattr(supplier, "name", "") or "",
            phone=getattr(supplier, "phone_number", "") or "",
            query=query,
        ),
        "label": getattr(supplier, "name", None) or "Supplier",
    }
    if phone:
        cell["sub"] = phone
    return cell


def _shelf_supplier_data(*, shop_ids, shops, start, end, status_key):
    """Shelf stock-in suppliers → identity-keyed entries for matrix building.

    Each movement carries its own amount_paid — paid/partial payments made
    on the shelf supplier account are reflected here.
    """
    supplier_by_key: dict[tuple, SimpleNamespace] = {}
    # (name, phone) -> shop -> {entries, due, paid}
    shelf_detail: dict[tuple, dict[int, dict]] = {}
    shelf_lines = _within_created_range(
        ShelfStockMovement.objects.filter(
            shelf_item__shop_id__in=shop_ids,
            movement_type=ShelfStockMovementType.IN,
        ).exclude(supplier_name=""),
        start,
        end,
    ).values_list(
        "shelf_item__shop_id",
        "supplier_name",
        "supplier_phone",
        "amount_paid",
        "quantity",
        "buying_price",
    )
    for shop_id, raw_name, raw_phone, raw_paid, qty, unit in shelf_lines:
        name = (raw_name or "").strip()
        phone = (raw_phone or "").strip()
        if not name:
            continue
        key = _supplier_identity_key(name, phone)
        supplier = supplier_by_key.get(key)
        if supplier is None:
            supplier = SimpleNamespace(
                pk=key,
                name=name,
                phone_country_code="",
                phone_number=phone,
            )
            supplier_by_key[key] = supplier
        total = (Decimal(unit or 0) * Decimal(qty or 0)).quantize(
            Decimal("0.01")
        )
        paid = Decimal(raw_paid or 0)
        due = _due_amount(total, paid)
        if not _supplier_movement_matches_status(
            status_key=status_key, total=total, paid=paid, due=due
        ):
            continue
        cell = shelf_detail.setdefault(key, {}).setdefault(
            shop_id, {"entries": 0, "due": _zero(), "paid": _zero()}
        )
        cell["entries"] += 1
        cell["due"] += due
        cell["paid"] += paid

    entries: dict[tuple, dict] = {}
    for key, shop_map in shelf_detail.items():
        supplier_due = sum(
            (Decimal(cell["due"] or 0) for cell in shop_map.values()),
            _zero(),
        )
        cells = {
            shop_id: (
                int(cell["entries"] or 0),
                Decimal(cell["due"] or 0),
                Decimal(cell["paid"] or 0),
            )
            for shop_id, cell in shop_map.items()
        }
        entries[key] = {
            "supplier": supplier_by_key[key],
            "cells": cells,
            "priority": 0 if supplier_due > 0 else 1,
        }
    return entries


def _merged_supplier_cell(supplier, *, role, query: str) -> dict:
    """Supplier cell for the combined table — item suppliers open the stock
    account, shelf-only suppliers open the name/phone ledger."""
    if isinstance(supplier, Supplier):
        cell = {
            "href": supplier_account_url(role, "stock", supplier.pk, query=query),
            "label": supplier.name or "Supplier",
        }
        phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
    else:
        return _shelf_supplier_cell(supplier, role=role, query=query)
    if phone:
        cell["sub"] = phone
    return cell


def _shelf_supplier_rows(*, shop_ids, shops, start, end, status_key, role, query):
    """Shelf stock-in suppliers (free-text on ShelfStockMovement) → rows + stats."""
    data = _shelf_supplier_data(
        shop_ids=shop_ids, shops=shops, start=start, end=end,
        status_key=status_key,
    )
    suppliers = [entry["supplier"] for entry in data.values()]
    by_pk = {
        entry["supplier"].pk: _supplier_status_cells(entry["cells"], status_key)
        for entry in data.values()
    }
    priority = {
        entry["supplier"].pk: entry["priority"] for entry in data.values()
    }
    rows, shop_totals = _supplier_shop_rows(
        suppliers=suppliers,
        shops=shops,
        by_supplier_shop=by_pk,
        kind="stock",
        role=role,
        query=query,
        supplier_priority=priority if status_key == "all" else None,
        cell_for=lambda supplier: _shelf_supplier_cell(
            supplier, role=role, query=query
        ),
    )
    stats = _supplier_ledger_stats(suppliers, by_pk)
    return rows, stats, shop_totals


def _merged_supplier_rows(
    *, item_entries, shelf_entries, shops, status_key, role, query
):
    """Combine item + shelf supplier entries into one merged matrix."""
    merged: dict[tuple, dict] = {}
    for entry in list(item_entries.values()) + list(shelf_entries.values()):
        supplier = entry["supplier"]
        key = _supplier_identity_key(
            supplier.name, supplier.phone_country_code, supplier.phone_number
        )
        target = merged.get(key)
        if target is None:
            target = merged[key] = {
                "supplier": supplier,
                "cells": {},
                "priority": entry["priority"],
            }
        if isinstance(supplier, Supplier):
            target["supplier"] = supplier
        for shop_id, (n, due, paid) in entry["cells"].items():
            prev = target["cells"].get(shop_id, (0, _zero(), _zero()))
            target["cells"][shop_id] = (
                prev[0] + n,
                prev[1] + Decimal(due or 0),
                prev[2] + Decimal(paid or 0),
            )
        target["priority"] = min(target["priority"], entry["priority"])

    suppliers = [entry["supplier"] for entry in merged.values()]
    by_pk = {
        entry["supplier"].pk: _supplier_status_cells(entry["cells"], status_key)
        for entry in merged.values()
    }
    priority = {
        entry["supplier"].pk: entry["priority"] for entry in merged.values()
    }
    rows, shop_totals = _supplier_shop_rows(
        suppliers=suppliers,
        shops=shops,
        by_supplier_shop=by_pk,
        kind="stock",
        role=role,
        query=query,
        supplier_priority=priority if status_key == "all" else None,
        cell_for=lambda supplier: _merged_supplier_cell(
            supplier, role=role, query=query
        ),
    )
    stats = _supplier_ledger_stats(suppliers, by_pk)
    return rows, stats, shop_totals


def _item_supplied_data(*, shop_ids, start, end, status_key):
    """Supplied catalog items → ``{item_id: {"item", "cells": {shop_id: (qty, amt)}}}``.

    Only stock-in lines that carry a supplier name count — this view exists to
    reach the supplier to pay. A line counts when its receipt matches the
    payment-status filter (receipt total = its supplier-linked lines, paid =
    the movement's amount_paid — same convention as the supplier matrices).
    """
    lines = list(
        _within_created_range(
            StockMovementLine.objects.filter(
                movement__shop_id__in=shop_ids,
                movement__movement_type=StockMovementType.IN,
                item__isnull=False,
            ).exclude(supplier_name=""),
            start,
            end,
            lookup="movement__created_at",
        ).select_related("movement", "item")
    )

    receipts: dict[int, dict] = {}
    for line in lines:
        movement = line.movement
        if movement is None:
            continue
        info = receipts.get(movement.pk)
        if info is None:
            info = receipts[movement.pk] = {
                "total": _zero(),
                "paid": Decimal(movement.amount_paid or 0),
            }
        qty = Decimal(line.quantity or 0)
        unit = Decimal(line.buying_price or 0)
        info["total"] += (unit * qty).quantize(Decimal("0.01"))

    matched_ids = {
        mid
        for mid, info in receipts.items()
        if _supplier_movement_matches_status(
            status_key=status_key,
            total=info["total"],
            paid=info["paid"],
            due=_due_amount(info["total"], info["paid"]),
        )
    }

    entries: dict[int, dict] = {}
    for line in lines:
        movement = line.movement
        item = line.item
        if movement is None or item is None or movement.pk not in matched_ids:
            continue
        qty = Decimal(line.quantity or 0)
        unit = Decimal(line.buying_price or 0)
        entry = entries.get(item.pk)
        if entry is None:
            entry = entries[item.pk] = {"item": item, "cells": {}}
        prev_qty, prev_amt = entry["cells"].get(
            movement.shop_id, (_zero(), _zero())
        )
        entry["cells"][movement.shop_id] = (
            prev_qty + qty,
            prev_amt + (unit * qty).quantize(Decimal("0.01")),
        )
    return entries


def _item_supplied_pair_columns(shops) -> list:
    columns = ["Item"]
    for index, shop in enumerate(shops):
        col = _shop_col(shop, pair=True, pair_qty="Qty", pair_amt="Amt")
        col["band"] = "flow"
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append(
        {**_pair_total_col(pair_qty="Qty", pair_amt="Amt"), "band": "total", "band_start": True}
    )
    return columns


def _shelf_supplied_data(*, shop_ids, start, end, status_key):
    """Supplied shelf items → ``{shelf_item_id: {"item", "cells"}}``.

    Each shelf stock-in movement is its own receipt: total = qty × unit,
    paid = the movement's amount_paid — same convention as item stock-ins.
    """
    movements = _within_created_range(
        ShelfStockMovement.objects.filter(
            shelf_item__shop_id__in=shop_ids,
            movement_type=ShelfStockMovementType.IN,
        ).exclude(supplier_name=""),
        start,
        end,
    ).select_related("shelf_item")

    entries: dict[int, dict] = {}
    for movement in movements:
        item = movement.shelf_item
        if item is None:
            continue
        qty = Decimal(movement.quantity or 0)
        unit = Decimal(movement.buying_price or 0)
        total = (unit * qty).quantize(Decimal("0.01"))
        paid = Decimal(movement.amount_paid or 0)
        if not _supplier_movement_matches_status(
            status_key=status_key,
            total=total,
            paid=paid,
            due=_due_amount(total, paid),
        ):
            continue
        entry = entries.get(item.pk)
        if entry is None:
            entry = entries[item.pk] = {"item": item, "cells": {}}
        prev_qty, prev_amt = entry["cells"].get(
            item.shop_id, (_zero(), _zero())
        )
        entry["cells"][item.shop_id] = (
            prev_qty + qty,
            prev_amt + total,
        )
    return entries


def _item_supplied_rows(*, entries, shops, role, query, url_for=None, sub_of=None):
    """Supplied-item entries → matrix rows (alphabetical) + totals row."""
    if url_for is None:
        url_for = lambda item: item_suppliers_url(role, item.pk, query=query)
    ranked = sorted(
        entries.values(),
        key=lambda entry: (
            (entry["item"].name or "").lower(),
            int(entry["item"].pk or 0),
        ),
    )
    shop_totals: dict[int, tuple] = {
        shop.pk: (_zero(), _zero()) for shop in shops
    }
    rows = []
    grand_qty = _zero()
    grand_amt = _zero()
    for entry in ranked:
        item = entry["item"]
        cell = {
            "href": url_for(item),
            "label": item.name or "Item",
        }
        sub = sub_of(item) if sub_of is not None else item.category
        if (sub or "").strip():
            cell["sub"] = sub
        cells = [cell]
        total_qty = _zero()
        total_amt = _zero()
        for shop in shops:
            qty, amt = entry["cells"].get(shop.pk, (_zero(), _zero()))
            prev_qty, prev_amt = shop_totals.get(shop.pk, (_zero(), _zero()))
            shop_totals[shop.pk] = (prev_qty + qty, prev_amt + amt)
            total_qty += qty
            total_amt += amt
            cells.append(
                _qty_amount_cell(
                    qty,
                    amt,
                    title=f"{_qty_label(qty)} supplied · {_money_ksh(amt)}",
                )
                if qty or amt
                else "—"
            )
        grand_qty += total_qty
        grand_amt += total_amt
        cells.append(
            _qty_amount_cell(
                total_qty,
                total_amt,
                title=f"{_qty_label(total_qty)} supplied · {_money_ksh(total_amt)}",
            )
        )
        rows.append(cells)

    if ranked:
        total_cells = ["Total"]
        for shop in shops:
            qty, amt = shop_totals.get(shop.pk, (_zero(), _zero()))
            total_cells.append(
                _qty_amount_cell(
                    qty,
                    amt,
                    title=f"{_qty_label(qty)} supplied · {_money_ksh(amt)}",
                )
            )
        total_cells.append(
            _qty_amount_cell(
                grand_qty,
                grand_amt,
                title=f"{_qty_label(grand_qty)} supplied · {_money_ksh(grand_amt)}",
            )
        )
        rows.append(total_cells)
    return rows


def _items_supplied_view_spec(
    status_key: str, period_label: str, *, noun: str = "items"
):
    """Empty/footnote copy for the items-supplied matrix view."""
    if status_key == "paid":
        return {
            "empty": f"No {noun} on paid supplier receipts for {period_label}.",
            "footnote": (
                f"Showing {noun} from fully paid receipts for {period_label}. "
                f"Qty = units supplied; Amt = supplied value."
            ),
        }
    if status_key == "not_paid":
        return {
            "empty": (
                f"No {noun} on not-paid supplier receipts for {period_label}."
            ),
            "footnote": (
                f"Showing {noun} from not-paid receipts for {period_label}. "
                f"Qty = units supplied; Amt = supplied value."
            ),
        }
    return {
        "empty": f"No supplied {noun} for {period_label}.",
        "footnote": (
            f"{noun.capitalize()} supplied in {period_label} — open an item "
            f"to see the suppliers to pay. "
            f"Qty = units supplied; Amt = supplied value."
        ),
    }


def _supplier_view_spec(status_key: str, period_label: str, *, scope: str):
    """Empty/footnote copy for a suppliers matrix view."""
    if status_key == "all":
        return {
            "empty": f"No supplier receipts for {period_label}.",
            "footnote": (
                f"Click a supplier to review receipts and pay. "
                f"Only {scope} with supply in {period_label}; "
                f"not paid at the top, then paid."
            ),
        }
    if status_key == "paid":
        return {
            "empty": f"No paid supplier receipts for {period_label}.",
            "footnote": (
                f"Showing paid receipts for {period_label}, "
                f"sorted by highest paid amount."
            ),
        }
    return {
        "empty": f"No not-paid supplier receipts for {period_label}.",
        "footnote": (
            f"Showing not-paid receipts for {period_label}, "
            f"sorted by highest outstanding balance."
        ),
    }


def _build_suppliers(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    query = filters.get("query") or ""
    role = filters["role"]
    start, end = filters.get("start"), filters.get("end")
    period_label = filters.get("report_period_label") or "All time"
    status_key = _parse_supplier_status(filters.get("supplier_status"))
    status_label = _supplier_status_label(status_key)
    amount_is_paid = status_key == "paid"
    view = (filters.get("supplier_view") or "").strip().lower()
    mode = (filters.get("supplier_mode") or "").strip().lower()
    if view == "supplied":
        # Legacy standalone tab — now the items view's supplied mode.
        view, mode = "items", "supplied"
    if view not in ("items", "shelf"):
        view = ""
        mode = ""
    supplied_mode = mode == "supplied"

    # Stock mode is set company-wide but read per shop via effective settings
    # so any shop-level override is honoured: item suppliers list only while
    # Items stock is on, shelf suppliers only while Shelf mode is on.
    item_shops = []
    shelf_shops = []
    for shop in shops:
        pos = get_effective_pos_settings(shop)
        if getattr(pos, "enable_stock_tracking", True):
            item_shops.append(shop)
        if getattr(pos, "enable_shelf_mode", False):
            shelf_shops.append(shop)
    if not item_shops and not shelf_shops:
        item_shops = list(shops)

    item_entries = (
        _item_supplier_data(
            shop_ids=[shop.pk for shop in item_shops],
            shops=item_shops,
            start=start,
            end=end,
            status_key=status_key,
        )
        if item_shops
        else {}
    )
    shelf_entries = (
        _shelf_supplier_data(
            shop_ids=[shop.pk for shop in shelf_shops],
            shops=shelf_shops,
            start=start,
            end=end,
            status_key=status_key,
        )
        if shelf_shops
        else {}
    )

    def _rows_from(data, *, mode_shops, cell_for=None):
        suppliers = [entry["supplier"] for entry in data.values()]
        by_pk = {
            entry["supplier"].pk: _supplier_status_cells(
                entry["cells"], status_key
            )
            for entry in data.values()
        }
        priority = {
            entry["supplier"].pk: entry["priority"] for entry in data.values()
        }
        rows, shop_totals = _supplier_shop_rows(
            suppliers=suppliers,
            shops=mode_shops,
            by_supplier_shop=by_pk,
            kind="stock",
            role=role,
            query=query,
            supplier_priority=priority if status_key == "all" else None,
            cell_for=cell_for,
        )
        stats = _supplier_ledger_stats(suppliers, by_pk)
        return rows, stats, shop_totals

    tables = []
    stats = _empty_supplier_stats()
    balance_shop_ids: set[int] = set()

    if view == "items" and supplied_mode:
        supplied_entries = _item_supplied_data(
            shop_ids=[shop.pk for shop in item_shops],
            start=start,
            end=end,
            status_key=status_key,
        )
        supplied_rows = _item_supplied_rows(
            entries=supplied_entries,
            shops=item_shops,
            role=role,
            query=query,
        )
        supplied_rows, supplied_pager = _page_table_rows(
            supplied_rows,
            filters,
            searchable=True,
            tail_rows=1 if supplied_rows else 0,
        )
        # The summary board keeps supplier stats — it is the payment context
        # the items list is meant to reach.
        view_stats = _supplier_ledger_stats(
            [entry["supplier"] for entry in item_entries.values()],
            {
                entry["supplier"].pk: _supplier_status_cells(
                    entry["cells"], status_key
                )
                for entry in item_entries.values()
            },
        )
        for key in stats:
            stats[key] += view_stats[key]
        for cells_map in (
            _supplier_status_cells(entry["cells"], status_key)
            for entry in item_entries.values()
        ):
            balance_shop_ids.update(
                shop_id
                for shop_id, (_entries, amount) in cells_map.items()
                if Decimal(amount or 0) > 0
            )
        spec = _items_supplied_view_spec(status_key, period_label)
        tables.append(
            _table(
                "Items supplied by shop",
                _item_supplied_pair_columns(item_shops),
                supplied_rows,
                empty=spec["empty"],
                footnote=spec["footnote"],
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
                pager=supplied_pager,
            )
        )
    elif view == "shelf" and supplied_mode:
        shelf_supplied_entries = _shelf_supplied_data(
            shop_ids=[shop.pk for shop in shelf_shops],
            start=start,
            end=end,
            status_key=status_key,
        )
        shelf_supplied_rows = _item_supplied_rows(
            entries=shelf_supplied_entries,
            shops=shelf_shops,
            role=role,
            query=query,
            url_for=lambda item: shelf_item_suppliers_url(
                role, item.pk, query=query
            ),
            sub_of=lambda item: (item.category or item.unit or ""),
        )
        shelf_supplied_rows, shelf_supplied_pager = _page_table_rows(
            shelf_supplied_rows,
            filters,
            searchable=True,
            tail_rows=1 if shelf_supplied_rows else 0,
        )
        view_stats = _supplier_ledger_stats(
            [entry["supplier"] for entry in shelf_entries.values()],
            {
                entry["supplier"].pk: _supplier_status_cells(
                    entry["cells"], status_key
                )
                for entry in shelf_entries.values()
            },
        )
        for key in stats:
            stats[key] += view_stats[key]
        for cells_map in (
            _supplier_status_cells(entry["cells"], status_key)
            for entry in shelf_entries.values()
        ):
            balance_shop_ids.update(
                shop_id
                for shop_id, (_entries, amount) in cells_map.items()
                if Decimal(amount or 0) > 0
            )
        spec = _items_supplied_view_spec(
            status_key, period_label, noun="shelf items"
        )
        tables.append(
            _table(
                "Shelf items supplied by shop",
                _item_supplied_pair_columns(shelf_shops),
                shelf_supplied_rows,
                empty=spec["empty"],
                footnote=spec["footnote"],
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
                pager=shelf_supplied_pager,
            )
        )
    elif view == "items":
        rows, view_stats, shop_totals = _rows_from(
            item_entries, mode_shops=item_shops
        )
        rows, view_pager = _page_table_rows(
            rows, filters, searchable=True, tail_rows=1 if rows else 0
        )
        for key in stats:
            stats[key] += view_stats[key]
        balance_shop_ids.update(
            shop_id
            for shop_id, (_entries, balance) in shop_totals.items()
            if Decimal(balance or 0) > 0
        )
        spec = _supplier_view_spec(
            status_key, period_label, scope="item suppliers"
        )
        tables.append(
            _table(
                "Item suppliers by shop",
                _supplier_pair_columns(item_shops),
                rows,
                empty=spec["empty"],
                footnote=spec["footnote"],
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
                pager=view_pager,
            )
        )
    elif view == "shelf":
        rows, view_stats, shop_totals = _rows_from(
            shelf_entries,
            mode_shops=shelf_shops,
            cell_for=lambda supplier: _shelf_supplier_cell(
                supplier, role=role, query=query
            ),
        )
        rows, view_pager = _page_table_rows(
            rows, filters, searchable=True, tail_rows=1 if rows else 0
        )
        for key in stats:
            stats[key] += view_stats[key]
        balance_shop_ids.update(
            shop_id
            for shop_id, (_entries, balance) in shop_totals.items()
            if Decimal(balance or 0) > 0
        )
        spec = _supplier_view_spec(
            status_key, period_label, scope="shelf suppliers"
        )
        tables.append(
            _table(
                "Shelf suppliers by shop",
                _supplier_pair_columns(shelf_shops),
                rows,
                empty=spec["empty"],
                footnote=spec["footnote"],
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
                pager=view_pager,
            )
        )
    else:
        rows, merged_stats, shop_totals = _merged_supplier_rows(
            item_entries=item_entries,
            shelf_entries=shelf_entries,
            shops=shops,
            status_key=status_key,
            role=role,
            query=query,
        )
        rows, merged_pager = _page_table_rows(
            rows, filters, searchable=True, tail_rows=1 if rows else 0
        )
        for key in stats:
            stats[key] += merged_stats[key]
        balance_shop_ids.update(
            shop_id
            for shop_id, (_entries, balance) in shop_totals.items()
            if Decimal(balance or 0) > 0
        )
        spec = _supplier_view_spec(
            status_key, period_label, scope="suppliers"
        )
        tables.append(
            _table(
                "Suppliers by shop",
                _supplier_pair_columns(shops),
                rows,
                empty=spec["empty"],
                footnote=spec["footnote"],
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
                pager=merged_pager,
            )
        )

    if supplied_mode:
        scope = "Items supplied" if view == "items" else "Shelf items supplied"
    elif view == "items":
        scope = "Item suppliers"
    elif view == "shelf":
        scope = "Shelf suppliers"
    elif item_shops and shelf_shops:
        scope = "Item and shelf suppliers"
    elif shelf_shops:
        scope = "Shelf suppliers"
    else:
        scope = "Stock suppliers"
    if supplied_mode:
        noun = "Items" if view == "items" else "Shelf items"
        if status_key == "all":
            lead = (
                f"{noun} supplied in {period_label}. "
                f"Open an item to see the suppliers to pay. "
                f"Qty = units supplied; Amt = supplied value."
            )
        elif status_key == "paid":
            lead = (
                f"{noun} on fully paid supplier receipts for {period_label}. "
                f"Qty = units supplied; Amt = supplied value."
            )
        else:
            lead = (
                f"{noun} on not-paid supplier receipts for {period_label}. "
                f"Qty = units supplied; Amt = supplied value."
            )
    elif status_key == "all":
        lead = (
            f"{scope} who supplied in {period_label}. "
            f"Not paid first, then paid. "
            f"En = stock receipts; Bal = unpaid owed, or paid amount when settled."
        )
    elif status_key == "paid":
        lead = (
            f"Fully paid {scope.lower()} for {period_label}. "
            f"En = paid receipts; Bal = amount paid."
        )
    else:
        lead = (
            f"Not-paid {scope.lower()} for {period_label}. "
            f"En = unpaid receipts; Bal = amount still owed."
        )

    summary = _supplier_summary_board(
        stats,
        shop_count=len(shops),
        shops_with_balance=len(balance_shop_ids),
        entity_label="Suppliers",
        icon="truck",
    )
    if amount_is_paid:
        summary["hero"]["label"] = "Paid amount"
        summary["hero"]["hint"] = (
            f"{stats['entries']:,} paid receipt"
            f"{'s' if stats['entries'] != 1 else ''} · "
            f"{stats['active']} supplier"
            f"{'s' if stats['active'] != 1 else ''}"
        )
        summary["hero"]["tone"] = "good" if stats["balance"] > 0 else "neutral"
    elif status_key == "all":
        summary["hero"]["label"] = "Balance"
        summary["hero"]["hint"] = (
            f"{stats['entries']:,} receipt"
            f"{'s' if stats['entries'] != 1 else ''} · "
            f"{stats['active']} supplier"
            f"{'s' if stats['active'] != 1 else ''} with supply · "
            "not paid first"
        )

    view_tabs = []
    if view in ("items", "shelf"):
        view_tabs = [
            {
                "label": "Suppliers",
                "href": _supplier_mode_href(query, view, ""),
                "active": not supplied_mode,
            },
            {
                "label": "Items supplied",
                "href": _supplier_mode_href(query, view, "supplied"),
                "active": supplied_mode,
            },
        ]

    return {
        "headline": "All suppliers",
        "period_label": period_label,
        "lead": lead,
        "view": view,
        "list_mode": mode,
        "view_tabs": view_tabs,
        "status_filter": status_key,
        "status_options": SUPPLIER_STATUS_FILTERS,
        "status_label": status_label,
        "bal_is_paid": amount_is_paid,
        "show_search": True,
        "search_placeholder": (
            "Search items…" if supplied_mode else "Search suppliers…"
        ),
        "search_empty": (
            "No items match that search."
            if supplied_mode
            else "No suppliers match that search."
        ),
        "alerts": [],
        "summary_board": summary,
        "insights": [],
        "tables": tables,
    }


def _build_expenses(filters):
    from urllib.parse import parse_qs, urlencode

    from employees.workspace import analytics_section_url

    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    query = filters.get("query") or ""
    role = filters["role"]
    start, end = filters.get("start"), filters.get("end")
    period_label = filters.get("report_period_label") or "All time"
    category_slug = (filters.get("expense_category") or "").strip().lower()
    category_labels = dict(ExpenseCategory.choices)

    params = parse_qs(query, keep_blank_values=True) if query else {}
    base_params = {k: v for k, v in params.items() if k != "category"}
    filter_query = urlencode({k: v[0] for k, v in base_params.items()})

    def _category_href(slug: str) -> str:
        href = analytics_section_url(role, "expenses")
        link_params = dict(base_params)
        link_params["category"] = [slug]
        encoded = urlencode({k: v[0] for k, v in link_params.items()})
        return f"{href}?{encoded}" if encoded else f"{href}?category={slug}"

    def _expenses_index_href() -> str:
        href = analytics_section_url(role, "expenses")
        return f"{href}?{filter_query}" if filter_query else href

    period_expenses = _within_created_range(
        Expense.objects.filter(shop_id__in=shop_ids),
        start,
        end,
    )

    if category_slug:
        category_label = category_labels.get(
            category_slug
        ) or category_slug.replace("_", " ").title()
        return _build_expense_category_suppliers(
            filters,
            category_slug=category_slug,
            category_label=category_label,
            shops=shops,
            period_expenses=period_expenses.filter(
                category__iexact=category_slug
            ),
            period_label=period_label,
            back_href=_expenses_index_href(),
        )

    by_category_shop: dict[str, dict[int, tuple[int, Decimal]]] = {}
    for row in period_expenses.values("category", "shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("amount"), _zero()),
    ):
        slug = (row["category"] or "").strip().lower()
        if not slug:
            continue
        shop_map = by_category_shop.setdefault(slug, {})
        shop_map[row["shop_id"]] = (
            int(row["docs"] or 0),
            Decimal(row["amount"] or 0),
        )

    ranked = []
    for slug, by_shop in by_category_shop.items():
        total_docs = 0
        total_amount = _zero()
        for docs, amount in by_shop.values():
            total_docs += int(docs or 0)
            total_amount += Decimal(amount or 0)
        ranked.append(
            (
                total_amount,
                (category_labels.get(slug) or slug).lower(),
                slug,
                by_shop,
                total_docs,
                total_amount,
            )
        )
    ranked.sort(key=lambda row: (-row[0], row[1]))

    columns = ["Expense"]
    for index, shop in enumerate(shops):
        col = _shop_col(shop, pair=True, pair_qty="Docs", pair_amt="Amt")
        col["band"] = "flow"
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    columns.append(
        {
            **_pair_total_col(pair_qty="Docs", pair_amt="Amt"),
            "band": "total",
            "band_start": True,
        }
    )

    table_rows = []
    grand_docs = 0
    grand_amount = _zero()
    shop_totals: dict[int, tuple[int, Decimal]] = {
        shop.pk: (0, _zero()) for shop in shops
    }
    for (
        _amt,
        _sort_label,
        slug,
        by_shop,
        total_docs,
        total_amount,
    ) in ranked:
        grand_docs += total_docs
        grand_amount += total_amount
        label = category_labels.get(slug) or slug.replace("_", " ").title()
        cells = [
            {
                "href": _category_href(slug),
                "label": label,
                "sub": f"{total_docs} entr{'y' if total_docs == 1 else 'ies'}",
            }
        ]
        for shop in shops:
            docs, amount = by_shop.get(shop.pk, (0, _zero()))
            prev_docs, prev_amount = shop_totals.get(shop.pk, (0, _zero()))
            shop_totals[shop.pk] = (
                prev_docs + int(docs or 0),
                prev_amount + Decimal(amount or 0),
            )
            cells.append(
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{docs} · {_money_ksh(amount)}",
                    tone="warn" if Decimal(amount or 0) > 0 else "neutral",
                )
                if docs or amount
                else "—"
            )
        cells.append(
            _qty_amount_cell(
                total_docs,
                total_amount,
                title=f"{total_docs} · {_money_ksh(total_amount)}",
                tone="warn" if total_amount > 0 else "neutral",
            )
        )
        table_rows.append(cells)

    if ranked:
        total_cells = ["Total"]
        for shop in shops:
            docs, amount = shop_totals.get(shop.pk, (0, _zero()))
            total_cells.append(
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{docs} · {_money_ksh(amount)}",
                    tone="warn" if Decimal(amount or 0) > 0 else "neutral",
                )
            )
        total_cells.append(
            _qty_amount_cell(
                grand_docs,
                grand_amount,
                title=f"{grand_docs} · {_money_ksh(grand_amount)}",
                tone="warn" if grand_amount > 0 else "neutral",
            )
        )
        table_rows.append(total_cells)

    category_count = len(ranked)
    return {
        "headline": "Expenses",
        "period_label": period_label,
        "lead": (
            f"Expense categories for {period_label}. "
            "Open a category to see every supplier and how many times they appear."
        ),
        "show_search": True,
        "search_placeholder": "Search expenses…",
        "search_empty": "No expenses match that search.",
        "alerts": [],
        "metrics": [
            _metric("Categories", str(category_count), hint="With activity"),
            _metric("Entries", f"{grand_docs:,}", hint="Expense receipts"),
            _metric(
                "Total",
                _money_ksh(grand_amount),
                hint=period_label,
                tone="warn" if grand_amount > 0 else "neutral",
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                "Expenses by shop",
                columns,
                table_rows,
                empty="No expenses recorded for selected shops and period.",
                footnote=(
                    f"Showing {period_label}, sorted by highest amount. "
                    "Click an expense to view its suppliers and entry counts."
                ),
                shop_grid=True,
                searchable=True,
                table_class="ax-table--expenses",
            )
        ],
    }


def _build_expense_category_suppliers(
    filters,
    *,
    category_slug: str,
    category_label: str,
    shops,
    period_expenses,
    period_label: str,
    back_href: str,
) -> dict:
    from urllib.parse import parse_qs, urlencode

    from django.utils import timezone

    role = filters["role"]
    query = filters.get("query") or ""
    params = parse_qs(query, keep_blank_values=True) if query else {}
    params["from"] = ["expenses"]
    params["category"] = [category_slug]
    supplier_query = urlencode({k: v[0] for k, v in params.items()})

    expenses = list(
        period_expenses.select_related(
            "shop",
            "supplier",
            "created_by",
            "created_by__user",
        ).order_by("-created_at", "-id")
    )

    supplier_ids = {
        expense.supplier_id for expense in expenses if expense.supplier_id is not None
    }
    suppliers_by_id = {
        supplier.pk: supplier
        for supplier in ExpenseSupplier.objects.filter(pk__in=supplier_ids)
    }

    can_pay = bool(filters.get("can_pay_accounts"))
    columns = [
        {"label": "Date", "title": "Date purchased"},
        {"label": "Supplier", "title": "Expense supplier"},
        {"label": "Shop", "title": "Shop"},
        {"label": "Detail", "title": "Expense name"},
        {"label": "Price", "title": "Expense amount"},
        {"label": "Paid", "title": "Amount paid so far"},
        {"label": "Due", "title": "Balance still owed"},
        {"label": "Status", "title": "Payment status"},
    ]
    if can_pay:
        columns.append({"label": "Pay", "title": "Record a payment"})

    table_rows = []
    grand_amount = _zero()
    grand_paid = _zero()
    grand_due = _zero()
    supplier_keys: set[str] = set()
    for expense in expenses:
        amount = Decimal(expense.amount or 0)
        paid = Decimal(expense.amount_paid or 0)
        due = _due_amount(amount, paid)
        status = _payment_status_for_due(due, paid)
        status_tone = _status_tone(status)
        grand_amount += amount
        grand_paid += paid
        grand_due += due
        when = timezone.localtime(expense.created_at) if expense.created_at else None
        date_label = when.strftime("%d %b %Y") if when else "—"
        date_title = when.strftime("%d %b %Y · %H:%M") if when else "—"

        linked = (
            suppliers_by_id.get(expense.supplier_id)
            if expense.supplier_id is not None
            else None
        )
        if linked is not None:
            supplier_name = linked.name
            supplier_phone = (
                f"{linked.phone_country_code} {linked.phone_number}".strip()
            )
            supplier_key = f"id:{linked.pk}"
            supplier_cell = {
                "href": supplier_account_url(
                    role, "expense", linked.pk, query=supplier_query
                ),
                "label": supplier_name,
                "sub": supplier_phone or None,
            }
        else:
            supplier_name = (expense.supplier_name or "").strip() or "No supplier"
            dial = (expense.supplier_phone_country_code or "").strip()
            phone = (expense.supplier_phone_number or "").strip()
            supplier_phone = f"{dial} {phone}".strip()
            supplier_key = f"name:{supplier_name.lower()}|{dial}|{phone}"
            supplier_cell = {
                "label": supplier_name,
                "sub": supplier_phone or None,
            }
        supplier_keys.add(supplier_key)

        shop_name = expense.shop.name if expense.shop_id else "—"
        detail = (expense.name or "").strip() or category_label
        cells = [
            {"label": date_label, "title": date_title},
            supplier_cell,
            shop_name,
            detail,
            _money_cell(
                amount,
                tone="warn" if amount > 0 else "neutral",
                title=f"Price {_money_ksh(amount)}",
            ),
            _money_cell(
                paid,
                tone="good" if paid > 0 else "neutral",
                title=f"Paid {_money_ksh(paid)}",
            ),
            _money_cell(
                due,
                tone="bad" if due > 0 else "neutral",
                title=f"Due {_money_ksh(due)}",
            ),
            {
                "kind": "status",
                "label": status,
                "tone": status_tone,
                "title": f"Paid {_money_ksh(paid)} · Due {_money_ksh(due)}",
            },
        ]
        if can_pay:
            cells.append(
                {
                    "kind": "pay",
                    "label": "Pay",
                    "pay_kind": "expense",
                    "pay_id": expense.pk,
                    "account_id": expense.supplier_id or "",
                    "supplier_name": supplier_name,
                    "supplier_phone": supplier_phone,
                    "number": format_simple_doc_number("E", expense.pk),
                    "due": _money_ksh(due),
                    "due_raw": str(due),
                    "shop": shop_name,
                    "can_pay": due > 0,
                }
            )
        table_rows.append(
            {
                "cells": cells,
                "search_text": " ".join(
                    part
                    for part in (
                        date_label,
                        supplier_name,
                        supplier_phone,
                        shop_name,
                        detail,
                        status,
                    )
                    if part
                ),
            }
        )

    if table_rows:
        total_cells = [
            "Total",
            f"{len(supplier_keys)} supplier"
            f"{'' if len(supplier_keys) == 1 else 's'}",
            f"{len(expenses)} purchase"
            f"{'' if len(expenses) == 1 else 's'}",
            "",
            _money_cell(
                grand_amount,
                tone="warn" if grand_amount > 0 else "neutral",
                title=f"Total {_money_ksh(grand_amount)}",
            ),
            _money_cell(
                grand_paid,
                tone="good" if grand_paid > 0 else "neutral",
                title=f"Paid {_money_ksh(grand_paid)}",
            ),
            _money_cell(
                grand_due,
                tone="bad" if grand_due > 0 else "neutral",
                title=f"Due {_money_ksh(grand_due)}",
            ),
            "",
        ]
        if can_pay:
            total_cells.append("")
        table_rows.append(total_cells)

    return {
        "headline": category_label,
        "period_label": period_label,
        "lead": (
            f"Purchases for {category_label.lower()} in {period_label}. "
            "Each row shows the supplier, price, paid and due amounts, "
            "and payment status."
        ),
        "back_href": back_href,
        "back_label": "Back to expenses",
        "expense_category": category_slug,
        "expense_pay": can_pay,
        "expense_pay_url": _account_pay_url(role) if can_pay else "",
        "expense_balance": _money_ksh(grand_due),
        "expense_balance_raw": str(grand_due),
        "show_search": True,
        "search_placeholder": "Search suppliers or details…",
        "search_empty": "No purchases match that search.",
        "alerts": [],
        "metrics": [
            _metric("Suppliers", str(len(supplier_keys)), hint="On this expense"),
            _metric("Purchases", f"{len(expenses):,}", hint="Times recorded"),
            _metric(
                "Total",
                _money_ksh(grand_amount),
                hint=period_label,
                tone="warn" if grand_amount > 0 else "neutral",
            ),
            _metric(
                "Balance",
                _money_ksh(grand_due),
                hint="Unpaid + partial",
                tone="bad" if grand_due > 0 else "good",
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                f"{category_label} · purchases",
                columns,
                table_rows,
                empty=(
                    f"No purchases recorded for {category_label.lower()} "
                    "in this period."
                ),
                footnote=(
                    f"Showing {period_label}, newest first. "
                    "Price is the expense amount; date is when it was purchased. "
                    "Click a supplier to open their expense account"
                    + (
                        ", or use Pay to clear an expense fully or in part."
                        if can_pay
                        else "."
                    )
                ),
                shop_grid=False,
                searchable=True,
                table_class="ax-table--expenses",
            )
        ],
    }


def _normalize_analytics_receipt_kind(raw: str) -> str:
    receipt_kind = (raw or "").strip().lower()
    if receipt_kind in ("", "all", "any"):
        return ""
    if receipt_kind in ("sale", "sales"):
        return "sales"
    if receipt_kind in ("credit", "credits"):
        return "credits"
    if receipt_kind in ("quotation", "quotations", "quote"):
        return "quotations"
    if receipt_kind in ("gift", "gifts"):
        return "gifts"
    if receipt_kind in ("trade_out", "trade-out"):
        return "trade_out"
    if receipt_kind in ("trade_in", "trade-in"):
        return "trade_in"
    if receipt_kind in ("stock", "stock-suppliers", "stock_suppliers"):
        return "stock"
    if receipt_kind in (
        "expense",
        "expenses",
        "expense-suppliers",
        "expense_suppliers",
    ):
        return "expense"
    if receipt_kind in (
        "advancement",
        "advancements",
        "advance",
        "advances",
        "prepayment",
        "prepayments",
        "deposit",
        "deposits",
    ):
        return "advancements"
    if receipt_kind in ("cancelled", "cancel"):
        return "cancelled"
    if receipt_kind in ("partial-returns", "partial_returns", "partial"):
        return "partial-returns"
    return receipt_kind


_BROWSE_KIND_FLAG = {
    "sales": "sale",
    "credits": "credit",
    "quotations": "quotation",
    "gifts": "gift",
    "trade_out": "trade_out",
    "trade_in": "trade_in",
}


def _browse_kind_enabled(slug: str, flags: dict) -> bool:
    key = _BROWSE_KIND_FLAG.get(slug)
    return key is None or bool(flags.get(key, True))


# Type pill tones for the receipts browse table (ax-status.is-*).
_RECEIPT_KIND_CELL_TONE = {
    ShopReceiptKind.SALE: "good",
    ShopReceiptKind.CREDIT: "warn",
    ShopReceiptKind.QUOTATION: "info",
    ShopReceiptKind.TRADE_OUT: "violet",
    ShopReceiptKind.TRADE_IN: "violet",
    ShopReceiptKind.GIFT: "violet",
    "stock": "shops",
    "expense": "cost",
    "advancement": "good",
    "advancements": "good",
}

# Status pill tones keyed by ShopReceiptStatus value.
_RECEIPT_STATUS_CELL_TONE = {
    ShopReceiptStatus.CONFIRMED: "good",
    ShopReceiptStatus.ACTIVE: "info",
    ShopReceiptStatus.PARTIAL_RETURN: "warn",
    ShopReceiptStatus.CANCELLED: "bad",
}

# Supplier payment-status labels (stock in / expense receipts) → pill tone.
_SUPPLIER_STATUS_CELL_TONE = {
    "paid": "good",
    "partial": "warn",
    "unpaid": "bad",
}


def _collect_analytics_receipts_browse(*, filters, include_receipt_actions: bool = False):
    """Shared receipt browse payload for analytics receipts + return pages."""
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    flags = filters.get("pos_flags") or _pos_feature_flags()
    receipt_kind = _normalize_analytics_receipt_kind(filters.get("receipt_kind"))
    if receipt_kind and not _browse_kind_enabled(receipt_kind, flags):
        receipt_kind = ""
    receipt_status = filters.get("receipt_status")
    apply_status = receipt_kind not in {"cancelled", "partial-returns"}
    walkins_only = filters.get("client_scope") in ("walkin", "walk-in", "walk_in")

    all_receipts = ShopReceipt.objects.filter(
        shop_id__in=shop_ids, created_at__gte=start, created_at__lt=end
    )
    if walkins_only:
        all_receipts = all_receipts.filter(client__isnull=True)
    if apply_status:
        all_receipts = _apply_analytics_receipt_status(all_receipts, receipt_status)
    stock_by_shop = (
        {}
        if walkins_only
        else _stock_in_receipt_totals_by_shop(
            shop_ids=shop_ids, start=start, end=end
        )
    )
    expense_by_shop = (
        {}
        if walkins_only
        else _expense_receipt_totals_by_shop(
            shop_ids=shop_ids, start=start, end=end
        )
    )
    prepay_by_shop: dict[int, tuple[int, Decimal]] = {}
    if not walkins_only:
        for row in (
            CustomerPrepayment.objects.filter(
                shop_id__in=shop_ids,
                created_at__gte=start,
                created_at__lt=end,
            )
            .values("shop_id")
            .annotate(
                docs=Count("id"),
                amount=Coalesce(Sum("amount"), _zero()),
            )
        ):
            prepay_by_shop[row["shop_id"]] = (
                int(row["docs"] or 0),
                Decimal(row["amount"] or 0),
            )

    kind_specs = tuple(
        row
        for row in (
            ("sales", "Sales"),
            ("credits", "Credits"),
            ("quotations", "Quotations"),
            ("gifts", "Gifts"),
            ("trade_out", "Trade out"),
            ("trade_in", "Trade in"),
            ("stock", "Stock suppliers"),
            ("expense", "Expense suppliers"),
            ("advancements", "Advancements"),
            ("cancelled", "Cancelled"),
            ("partial-returns", "Partial returns"),
        )
        if _browse_kind_enabled(row[0], flags)
        and not (walkins_only and row[0] in ("stock", "expense", "advancements"))
    )
    all_kind_specs = kind_specs
    if receipt_kind:
        filtered_specs = tuple(row for row in kind_specs if row[0] == receipt_kind)
        if filtered_specs:
            kind_specs = filtered_specs

    columns = ["Type"]
    for index, shop in enumerate(shops):
        col = _shop_col(shop, pair=True, pair_qty="Docs", pair_amt="Amt")
        col["band"] = "flow"
        if index == 0:
            col["band_start"] = True
        columns.append(col)
    total_col = _pair_total_col(pair_qty="Docs", pair_amt="Amt")
    total_col["band"] = "result"
    total_col["band_start"] = True
    columns.append(total_col)

    table_rows = []
    shop_totals: dict[int, list] = {shop.pk: [0, _zero()] for shop in shops}
    grand_docs = 0
    grand_amount = _zero()
    active_kinds = 0
    for kind_slug, label in kind_specs:
        kind_tone = {"cancelled": "bad", "partial-returns": "warn"}.get(
            kind_slug, "good"
        )
        by_shop: dict[int, tuple[int, Decimal]] = {}
        if kind_slug == "stock":
            by_shop = stock_by_shop
        elif kind_slug == "expense":
            by_shop = expense_by_shop
        elif kind_slug == "advancements":
            by_shop = prepay_by_shop
        else:
            kind_filter = _analytics_receipt_kind_filter(kind_slug)
            for row in (
                all_receipts.filter(kind_filter)
                .values("shop_id")
                .annotate(
                    docs=Count("id"),
                    amount=Coalesce(Sum("total"), _zero()),
                )
            ):
                by_shop[row["shop_id"]] = (
                    int(row["docs"] or 0),
                    Decimal(row["amount"] or 0),
                )
        total_docs = 0
        total_amount = _zero()
        cells = [label]
        for shop in shops:
            docs, amount = by_shop.get(shop.pk, (0, _zero()))
            total_docs += docs
            total_amount += amount
            shop_totals[shop.pk][0] += docs
            shop_totals[shop.pk][1] += amount
            cells.append(
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{docs} docs · {_money_ksh(amount)}",
                    tone=kind_tone if docs else "neutral",
                )
            )
        cells.append(
            _qty_amount_cell(
                total_docs,
                total_amount,
                title=f"{total_docs} docs · {_money_ksh(total_amount)}",
                tone=kind_tone if total_docs else "neutral",
            )
        )
        table_rows.append(cells)
        grand_docs += total_docs
        grand_amount += total_amount
        if total_docs > 0:
            active_kinds += 1
    if kind_specs:
        total_cells = ["Total"]
        for shop in shops:
            docs, amount = shop_totals[shop.pk]
            total_cells.append(
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{docs} docs · {_money_ksh(amount)}",
                    tone="good" if docs else "neutral",
                )
            )
        total_cells.append(
            _qty_amount_cell(
                grand_docs,
                grand_amount,
                title=f"{grand_docs} docs · {_money_ksh(grand_amount)}",
                tone="good" if grand_docs else "neutral",
            )
        )
        table_rows.append(total_cells)

    merged = []
    include_pos = receipt_kind in (
        "",
        "sales",
        "credits",
        "quotations",
        "gifts",
        "trade_out",
        "trade_in",
        "cancelled",
        "partial-returns",
    )
    include_stock = not walkins_only and receipt_kind in ("", "stock")
    include_expense = not walkins_only and receipt_kind in ("", "expense")
    include_advancements = (
        not walkins_only and receipt_kind in ("", "advancements")
    )

    def _row_payload(
        *,
        number,
        kind_label,
        client_name,
        phone,
        items,
        total,
        when,
        when_dt,
        sort_id,
        shop="",
        kind_slug="",
        status_slug="",
        status_label="",
        total_raw=None,
        receipt_id=None,
        shop_id=None,
        items_detail=None,
        raw_number="",
    ):
        items_label = (items or "").strip() or "—"
        items_cell = {
            "kind": "items",
            "label": items_label,
            "title": items_label,
            "doc": f"{number} · {kind_label}",
        }
        if items_detail:
            items_cell["items_json"] = json.dumps(items_detail)
        shop_label = (shop or "").strip()
        phone_label = (phone or "").strip()
        client_sub = "" if phone_label in {"", "—"} else phone_label
        kind_tone = _RECEIPT_KIND_CELL_TONE.get(
            (kind_slug or "").strip().lower()
        ) or _RECEIPT_KIND_CELL_TONE.get(
            (kind_label or "").strip().lower().replace(" ", "_"), "neutral"
        )
        status_text = (status_label or "").strip() or "—"
        status_tone = _RECEIPT_STATUS_CELL_TONE.get(
            (status_slug or "").strip().lower()
        ) or _SUPPLIER_STATUS_CELL_TONE.get(status_text.lower(), "neutral")
        if (status_slug or "").strip().lower() == ShopReceiptStatus.CANCELLED:
            money_tone = "bad"
        elif total_raw is not None and Decimal(total_raw or 0) <= 0:
            money_tone = "neutral"
        else:
            money_tone = "good"
        cells = [
            {"label": number, "sub": shop_label},
            {"kind": "status", "label": kind_label, "tone": kind_tone},
            {"label": client_name, "sub": client_sub},
            items_cell,
            {
                "kind": "money",
                "label": total,
                "tone": money_tone,
                "title": total,
            },
            {"kind": "status", "label": status_text, "tone": status_tone},
            when,
        ]
        payload = {
            "number": number,
            "kind": kind_label,
            "client_name": client_name,
            "phone": phone,
            "items": items_label,
            "total": total,
            "when": when,
            "reference": "",
            "when_dt": when_dt,
            "sort_id": sort_id,
            "cells": cells,
            "search_text": " ".join(
                str(part)
                for part in (
                    *(
                        cell.get("label") if isinstance(cell, dict) else cell
                        for cell in cells
                    ),
                    raw_number,
                    shop_label,
                    client_sub,
                )
                if part
            ),
        }
        if include_receipt_actions and receipt_id is not None:
            payload["receipt_id"] = receipt_id
            payload["shop_id"] = shop_id
        return payload

    if include_pos:
        pos_qs = (
            ShopReceipt.objects.filter(
                shop_id__in=shop_ids,
                created_at__gte=start,
                created_at__lt=end,
            )
            .select_related("shop", "created_by", "created_by__user")
            .prefetch_related("lines")
            .order_by("-created_at", "-id")
        )
        if receipt_kind in (
            "sales",
            "credits",
            "quotations",
            "gifts",
            "trade_out",
            "trade_in",
            "cancelled",
            "partial-returns",
        ):
            pos_qs = pos_qs.filter(_analytics_receipt_kind_filter(receipt_kind))
        else:
            # "All types" — only kinds backed by enabled POS features.
            enabled_q = Q(pk__in=[])
            if flags["sale"]:
                enabled_q |= Q(kind=ShopReceiptKind.SALE)
            else:
                if flags["credit"]:
                    enabled_q |= Q(kind=ShopReceiptKind.SALE, settled_from_credit=True)
                if flags["tradings"]:
                    enabled_q |= Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
            if flags["credit"]:
                enabled_q |= Q(kind=ShopReceiptKind.CREDIT)
            if flags["quotation"]:
                enabled_q |= Q(kind=ShopReceiptKind.QUOTATION)
            if flags["gift"]:
                enabled_q |= Q(kind=ShopReceiptKind.GIFT)
            if flags["trade_out"]:
                enabled_q |= Q(kind=ShopReceiptKind.TRADE_OUT)
            if flags["trade_in"]:
                enabled_q |= Q(kind=ShopReceiptKind.TRADE_IN)
            pos_qs = pos_qs.filter(enabled_q)
        if apply_status:
            pos_qs = _apply_analytics_receipt_status(pos_qs, receipt_status)
        if walkins_only:
            pos_qs = pos_qs.filter(client__isnull=True)
        for row in pos_qs:
            client_name = (row.client_name or "").strip() or "Walk-in"
            phone = (row.client_phone or "").strip() or "—"
            items_detail = _receipt_line_items_detail(row.lines.all())
            merged.append(
                _row_payload(
                    number=receipt_tally_code(row),
                    raw_number=row.receipt_number,
                    kind_label=row.get_kind_display(),
                    client_name=client_name,
                    phone=phone,
                    items=_items_label_from_detail(items_detail),
                    items_detail=items_detail,
                    total=_money_ksh(row.total),
                    when=timezone.localtime(row.created_at).strftime(
                        "%d %b %Y · %H:%M"
                    ),
                    when_dt=row.created_at,
                    sort_id=row.pk,
                    shop=row.shop.name if row.shop_id else "",
                    kind_slug=row.kind,
                    status_slug=row.status,
                    status_label=row.get_status_display(),
                    total_raw=row.total,
                    receipt_id=row.pk,
                    shop_id=row.shop_id,
                )
            )
    if include_stock:
        for row in _list_stock_supplier_receipt_rows(
            shop_ids=shop_ids, start=start, end=end
        ):
            merged.append(
                _row_payload(
                    number=row["number"],
                    kind_label=row["kind"],
                    client_name=row.get("client_name") or row["client"] or "—",
                    phone=row.get("phone") or "—",
                    items=row.get("items") or "—",
                    items_detail=row.get("items_detail"),
                    total=row["total"],
                    when=row["when"],
                    when_dt=row["when_dt"],
                    sort_id=row["sort_id"],
                    shop=row.get("shop") or "",
                    kind_slug="stock",
                    status_label=row.get("status") or "",
                    total_raw=row.get("total_raw"),
                )
            )
    if include_expense:
        for row in _list_expense_supplier_receipt_rows(
            shop_ids=shop_ids, start=start, end=end
        ):
            merged.append(
                _row_payload(
                    number=row["number"],
                    kind_label=row["kind"],
                    client_name=row.get("client_name") or row["client"] or "—",
                    phone=row.get("phone") or "—",
                    items=row.get("items") or "—",
                    items_detail=row.get("items_detail"),
                    total=row["total"],
                    when=row["when"],
                    when_dt=row["when_dt"],
                    sort_id=row["sort_id"],
                    shop=row.get("shop") or "",
                    kind_slug="expense",
                    status_label=row.get("status") or "",
                    total_raw=row.get("total_raw"),
                )
            )
    if include_advancements:
        for row in _list_prepayment_receipt_rows(
            shop_ids=shop_ids, start=start, end=end
        ):
            merged.append(
                _row_payload(
                    number=row["number"],
                    kind_label=row["kind"],
                    client_name=row.get("client_name") or row["client"] or "—",
                    phone=row.get("phone") or "—",
                    items=row.get("items") or "—",
                    items_detail=row.get("items_detail"),
                    total=row["total"],
                    when=row["when"],
                    when_dt=row["when_dt"],
                    sort_id=row["sort_id"],
                    shop=row.get("shop") or "",
                    kind_slug="advancement",
                    status_label=row.get("status") or "",
                    total_raw=row.get("total_raw"),
                )
            )
    merged.sort(
        key=lambda item: (item["when_dt"], item["sort_id"]),
        reverse=True,
    )

    kind_label = (
        dict(kind_specs).get(receipt_kind, "All types")
        if receipt_kind
        else "All types"
    )
    period_label = filters.get("report_period_label") or "selected period"
    return {
        "receipt_kind": receipt_kind,
        "receipt_kind_options": [("all", "All types"), *all_kind_specs],
        "kind_label": kind_label,
        "period_label": period_label,
        "shops": shops,
        "kind_specs": kind_specs,
        "active_kinds": active_kinds,
        "grand_docs": grand_docs,
        "grand_amount": grand_amount,
        "columns": columns,
        "table_rows": table_rows,
        "receipt_rows": merged,
        "receipt_total": len(merged),
        "walkins_only": walkins_only,
    }


def _build_receipts(filters):
    """All-receipts browse — filter by type and period; Confirm / Return tools."""
    from django.urls import reverse

    from employees.access import role_url_segment

    role = filters["role"]
    segment = role_url_segment(role)
    browse = _collect_analytics_receipts_browse(
        filters=filters, include_receipt_actions=False
    )
    receipt_kind = browse["receipt_kind"]
    kind_label = browse["kind_label"]
    period_label = browse["period_label"]
    list_footnote = (
        f"{browse['receipt_total']} receipt"
        f"{'' if browse['receipt_total'] == 1 else 's'}"
        + (f" · {kind_label}" if receipt_kind else "")
        + f" for {period_label}."
        + (" Walk-in clients only." if browse.get("walkins_only") else "")
    )

    return {
        "headline": "Receipts",
        "lead": (
            "Walk-in receipts only — receipts with no linked client record."
            if browse.get("walkins_only")
            else "Filter by receipt type and period across selected shops."
        ),
        "list_table_layout": True,
        "show_search": True,
        "search_placeholder": "Search receipts…",
        "search_empty": "No receipts match that search.",
        "receipts_print_modal": True,
        "receipt_items_modal": True,
        "receipts_print_url": reverse(
            "employees:analytics_receipts_print",
            kwargs={"role_segment": segment},
        ),
        "receipt_kind": receipt_kind or "all",
        "receipt_kind_options": browse["receipt_kind_options"],
        "alerts": [],
        "metrics": [
            _metric(
                "Types",
                str(len(browse["kind_specs"])),
                hint=f"{browse['active_kinds']} with activity",
            ),
            _metric(
                "Documents",
                f"{browse['grand_docs']:,}",
                hint=period_label,
            ),
            _metric(
                "Shops",
                str(len(browse["shops"])),
                hint="In this view",
            ),
            _metric(
                "Total value",
                _money_ksh(browse["grand_amount"]),
                hint="All document types" if not receipt_kind else kind_label,
                tone="good" if browse["grand_amount"] > 0 else "neutral",
            ),
        ],
        "trends": [],
        "charts": [],
        "insights": [],
        "sections": [],
        "tables": [
            _table(
                f"All receipts · {period_label}",
                [
                    "Receipt",
                    "Type",
                    "Client",
                    "Items",
                    "Total",
                    "Status",
                    "Date & time",
                ],
                browse["receipt_rows"],
                empty="No receipts for selected shops and period.",
                footnote=list_footnote,
                shop_grid=False,
                searchable=True,
            ),
            _table(
                "Documents by shop",
                browse["columns"],
                browse["table_rows"],
                empty="No receipts for selected shops and period.",
                footnote="Docs = receipt count · Amt = document total.",
            ),
        ],
    }


def build_return_receipts_page(*, profile, request) -> dict:
    """Return / cancel receipts ledger across shops."""
    from django.urls import reverse

    from employees.access import role_url_segment

    filters = _filters_context(
        profile, request, default_receipt_status=ANALYTICS_RECEIPT_STATUS_ALL
    )
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    filters["receipt_kind"] = (request.GET.get("receipt_kind") or "").strip().lower()
    page = _build_return_receipts(filters)
    segment = role_url_segment(profile.role)
    page["back_href"] = reverse(
        "employees:analytics_section",
        kwargs={
            "role_segment": segment,
            "section": "receipts",
        },
    )
    page["back_label"] = "Back to receipts"
    page["return_audits_href"] = reverse(
        "employees:analytics_return_receipts_audits",
        kwargs={"role_segment": segment},
    )
    page["return_audits_label"] = "Returned receipts audit"
    return {
        **filters,
        "section_slug": "return-receipts",
        "page": page,
    }


def build_return_receipts_audits(*, profile, request=None, status_filter: str = "") -> dict:
    """Audit trail of cancelled / partially returned receipts with reason, items, and actor."""
    from django.urls import reverse

    from employees.access import role_url_segment
    from shops.services import employee_display_name

    date_filter = _date_filter_context(request)
    start, end = date_filter.get("start"), date_filter.get("end")
    filter_shops = list(actionable_shops_for_profile(profile))
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    allowed_shop_ids = [shop.pk for shop in filter_shops]

    try:
        shop_filter_id = int((getattr(request, "GET", {}) or {}).get("shop_id") or 0)
    except (TypeError, ValueError):
        shop_filter_id = 0
    if shop_filter_id and shop_filter_id not in shops_by_id:
        shop_filter_id = 0

    try:
        employee_filter_id = int(
            (getattr(request, "GET", {}) or {}).get("employee_id") or 0
        )
    except (TypeError, ValueError):
        employee_filter_id = 0

    filter_employees = list(
        EmployeeProfile.objects.filter(status=EmployeeStatus.ACTIVE)
        .select_related("user")
        .only(
            "id",
            "employee_id",
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        )
        .order_by("employee_id")[:300]
    )
    if employee_filter_id and not any(
        emp.pk == employee_filter_id for emp in filter_employees
    ):
        employee_filter_id = 0

    status_raw = (status_filter or "").strip().lower()
    status_options = [
        {"slug": "", "label": "Cancelled & partial returns"},
        {"slug": "cancelled", "label": "Cancelled only"},
        {"slug": "partial_return", "label": "Partial returns only"},
    ]
    if status_raw not in {"cancelled", "partial_return"}:
        status_raw = ""
    status_filter_label = next(
        (opt["label"] for opt in status_options if opt["slug"] == status_raw),
        status_options[0]["label"],
    )
    if status_raw:
        status_values = [status_raw]
    else:
        status_values = [
            ShopReceiptStatus.CANCELLED,
            ShopReceiptStatus.PARTIAL_RETURN,
        ]

    shop_ids = [shop_filter_id] if shop_filter_id else allowed_shop_ids
    period_bits = [date_filter.get("report_period_label") or ""]
    if status_raw:
        period_bits.append(status_filter_label)
    period_label = " · ".join(bit for bit in period_bits if bit)
    empty_message = (
        "No matching returned receipts for this filter."
        if status_raw or shop_filter_id or employee_filter_id
        else "No cancelled or partially returned receipts recorded yet."
    )
    segment = role_url_segment(profile.role)
    back_href = reverse(
        "employees:analytics_return_receipts",
        kwargs={"role_segment": segment},
    )
    empty = {
        **date_filter,
        "headline": "Returned receipts audit",
        "lead": (
            "Receipts cancelled or partially returned — reason, returned items, "
            "and who initiated the return."
        ),
        "period_label": period_label,
        "status_options": status_options,
        "status_filter": status_raw,
        "status_filter_label": status_filter_label,
        "filter_shops": filter_shops,
        "filter_employees": filter_employees,
        "selected_shop_id": shop_filter_id or 0,
        "selected_employee_id": employee_filter_id or 0,
        "back_href": back_href,
        "back_label": "Back to return receipt",
        "rows": [],
        "receipt_count": 0,
        "cancelled_count": 0,
        "partial_count": 0,
        "empty_message": empty_message,
        "summary_board": {
            "hero": {
                "label": "Returned receipts",
                "value": "0",
                "hint": "No returns yet",
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Cancelled",
                    "value": "0",
                    "hint": "Fully returned",
                    "icon": "ban",
                    "tone": "bad",
                },
                {
                    "label": "Partial returns",
                    "value": "0",
                    "hint": "Some items kept",
                    "icon": "undo-2",
                    "tone": "warn",
                },
                {
                    "label": "Initiators",
                    "value": "0",
                    "hint": "Staff who returned",
                    "icon": "user-check",
                    "tone": "shops",
                },
            ],
        },
    }
    if not shop_ids:
        return empty

    qs = ShopReceipt.objects.filter(
        shop_id__in=shop_ids,
        status__in=status_values,
    )
    if employee_filter_id:
        qs = qs.filter(last_returned_by_id=employee_filter_id)
    qs = _within_created_range(qs, start, end, lookup="last_returned_at")
    receipts = list(
        qs.select_related(
            "shop",
            "last_returned_by",
            "last_returned_by__user",
        )
        .prefetch_related("lines")
        .order_by("-last_returned_at", "-pk")[:1000]
    )

    actor_ids = set()
    for receipt in receipts:
        if receipt.last_returned_by_id:
            actor_ids.add(receipt.last_returned_by_id)
        for line in receipt.lines.all():
            for batch in line.return_batches or []:
                if not isinstance(batch, dict):
                    continue
                by_id = batch.get("by_id")
                try:
                    by_id = int(by_id) if by_id is not None else 0
                except (TypeError, ValueError):
                    by_id = 0
                if by_id:
                    actor_ids.add(by_id)

    actors_by_id = {
        emp.pk: emp
        for emp in EmployeeProfile.objects.filter(pk__in=actor_ids).select_related(
            "user"
        )
    }

    rows = []
    cancelled_count = 0
    partial_count = 0
    initiator_ids: set[int] = set()
    for receipt in receipts:
        if receipt.status == ShopReceiptStatus.CANCELLED:
            cancelled_count += 1
            tone = "bad"
            icon = "ban"
        else:
            partial_count += 1
            tone = "warn"
            icon = "undo-2"

        item_parts = []
        reasons: list[str] = []
        seen_reasons: set[str] = set()
        batch_actor_ids: list[int] = []
        for line in receipt.lines.all():
            batches = [
                batch
                for batch in (line.return_batches or [])
                if isinstance(batch, dict)
            ]
            returned_qty = Decimal(line.returned_quantity or 0)
            if returned_qty <= 0 and not batches:
                continue
            qty_label = _qty_label(returned_qty if returned_qty > 0 else 0)
            if batches:
                batch_qty = sum(
                    Decimal(str(batch.get("qty") or 0)) for batch in batches
                )
                if batch_qty > 0:
                    qty_label = _qty_label(batch_qty)
            name = (line.item_name or "Item").strip() or "Item"
            item_parts.append(f"{name} × {qty_label}")
            for batch in batches:
                reason = (batch.get("reason") or "").strip()
                if reason and reason not in seen_reasons:
                    seen_reasons.add(reason)
                    reasons.append(reason)
                by_id = batch.get("by_id")
                try:
                    by_id = int(by_id) if by_id is not None else 0
                except (TypeError, ValueError):
                    by_id = 0
                if by_id:
                    batch_actor_ids.append(by_id)

        if not item_parts:
            item_parts = ["—"]
        if not reasons and receipt.status == ShopReceiptStatus.CANCELLED:
            reasons = ["Receipt cancelled"]

        actor = receipt.last_returned_by
        if actor is None and batch_actor_ids:
            actor = actors_by_id.get(batch_actor_ids[-1])
        actor_label = employee_display_name(actor) if actor else "—"
        if actor and getattr(actor, "employee_id", None):
            actor_label = f"{actor_label} ({actor.employee_id})".strip()
        if actor and actor.pk:
            initiator_ids.add(actor.pk)

        when = receipt.last_returned_at
        when_label = (
            timezone.localtime(when).strftime("%d %b %Y · %H:%M") if when else "—"
        )
        client = (receipt.client_name or "").strip() or "—"
        rows.append(
            {
                "id": receipt.pk,
                "when_label": when_label,
                "receipt_number": receipt_tally_code(receipt),
                "kind_label": receipt.get_kind_display(),
                "status": receipt.status,
                "status_label": receipt.get_status_display(),
                "tone": tone,
                "icon": icon,
                "shop": receipt.shop.name if receipt.shop_id else "—",
                "client": client,
                "items": " · ".join(item_parts),
                "reason": " · ".join(reasons) if reasons else "—",
                "actor": actor_label,
            }
        )

    receipt_count = len(rows)
    return {
        **date_filter,
        "headline": "Returned receipts audit",
        "lead": (
            "Receipts cancelled or partially returned — reason, returned items, "
            "and who initiated the return."
        ),
        "period_label": period_label,
        "status_options": status_options,
        "status_filter": status_raw,
        "status_filter_label": status_filter_label,
        "filter_shops": filter_shops,
        "filter_employees": filter_employees,
        "selected_shop_id": shop_filter_id or 0,
        "selected_employee_id": employee_filter_id or 0,
        "back_href": back_href,
        "back_label": "Back to return receipt",
        "rows": rows,
        "receipt_count": receipt_count,
        "cancelled_count": cancelled_count,
        "partial_count": partial_count,
        "empty_message": empty_message,
        "summary_board": {
            "hero": {
                "label": "Returned receipts",
                "value": str(receipt_count),
                "hint": (
                    f"{cancelled_count} cancelled · {partial_count} partial"
                ),
                "tone": "neutral",
            },
            "tiles": [
                {
                    "label": "Cancelled",
                    "value": str(cancelled_count),
                    "hint": "Fully returned",
                    "icon": "ban",
                    "tone": "bad",
                },
                {
                    "label": "Partial returns",
                    "value": str(partial_count),
                    "hint": "Some items kept",
                    "icon": "undo-2",
                    "tone": "warn",
                },
                {
                    "label": "Initiators",
                    "value": str(len(initiator_ids)),
                    "hint": "Staff who returned",
                    "icon": "user-check",
                    "tone": "shops",
                },
            ],
        },
    }


def _build_return_receipts(filters):
    from django.urls import reverse

    from employees.access import role_url_segment

    browse = _collect_analytics_receipts_browse(
        filters=filters, include_receipt_actions=True
    )
    receipt_kind = browse["receipt_kind"]
    kind_label = browse["kind_label"]
    period_label = browse["period_label"]
    list_footnote = (
        f"{browse['receipt_total']} receipt"
        f"{'' if browse['receipt_total'] == 1 else 's'}"
        + (f" · {kind_label}" if receipt_kind else "")
        + f" for {period_label}."
        + " Sales/credit rows open return; supplier receipts are listed for review."
    )

    role = filters["role"]
    segment = role_url_segment(role)
    detail_template = reverse(
        "employees:analytics_receipt_detail",
        kwargs={
            "role_segment": segment,
            "shop_id": 0,
            "receipt_id": 0,
        },
    )
    return_template = reverse(
        "employees:analytics_receipt_return",
        kwargs={
            "role_segment": segment,
            "shop_id": 0,
            "receipt_id": 0,
        },
    )
    verify_url = reverse(
        "employees:analytics_receipt_verify_login",
        kwargs={"role_segment": segment},
    )

    return {
        "headline": "Return receipt",
        "lead": (
            "Open a receipt to return items or cancel a sale or credit."
        ),
        "ledger_layout": True,
        "list_table_layout": True,
        "show_search": True,
        "search_placeholder": "Search receipts…",
        "search_empty": "No receipts match that search.",
        "receipt_modal": True,
        "receipt_items_modal": True,
        "receipt_detail_url_template": detail_template,
        "receipt_return_url_template": return_template,
        "receipt_verify_login_url": verify_url,
        "receipt_kind": receipt_kind or "all",
        "receipt_kind_options": browse["receipt_kind_options"],
        "alerts": [],
        "metrics": [
            _metric(
                "Types",
                str(len(browse["kind_specs"])),
                hint=f"{browse['active_kinds']} with activity",
            ),
            _metric(
                "Documents",
                f"{browse['grand_docs']:,}",
                hint=period_label,
            ),
            _metric(
                "Shops",
                str(len(browse["shops"])),
                hint="In this view",
            ),
            _metric(
                "Total value",
                _money_ksh(browse["grand_amount"]),
                hint="All document types" if not receipt_kind else kind_label,
                tone="good" if browse["grand_amount"] > 0 else "neutral",
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                f"All receipts · {period_label}",
                [
                    "Receipt",
                    "Type",
                    "Client",
                    "Items",
                    "Total",
                    "Status",
                    "Date & time",
                ],
                browse["receipt_rows"],
                empty="No receipts for selected shops and period.",
                footnote=list_footnote,
                shop_grid=False,
                searchable=True,
            ),
            _table(
                "Documents by shop",
                browse["columns"],
                browse["table_rows"],
                empty="No receipts for selected shops and period.",
                footnote="Docs = receipt count · Amt = document total.",
            ),
        ],
    }

