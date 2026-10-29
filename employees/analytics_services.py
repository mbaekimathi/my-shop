"""Decision-oriented analytics for the Analytics workspace module."""

from __future__ import annotations

import logging
from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

from django.db.models import (
    Case,
    Count,
    DateTimeField,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
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
    Expense,
    ExpenseCategory,
    ExpensePaymentStatus,
    ExpenseSupplier,
    ShopDaySession,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)
from shops.services import _qty_label, _qty_number, format_simple_doc_number

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
        "label": "Balances",
        "icon": "scale",
        "summary": "Live cashbox per shop — opening, sales, expenses paid, suppliers paid.",
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
        "slug": "quotations",
        "label": "Quotations",
        "icon": "file-text",
        "summary": "Quotations by shop — count and value, plus totals.",
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
        "summary": "Confirm pending sales receipts across shops.",
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


def _client_activity_receipts_q(kind: str) -> Q:
    """Receipt kinds shown on the Clients analytics pages."""
    if kind == "credits":
        return _credit_account_receipts_q()
    if kind == "sales":
        return Q(kind=ShopReceiptKind.SALE)
    if kind == "quotations":
        return Q(kind=ShopReceiptKind.QUOTATION)
    if kind == "trade_out":
        return Q(kind=ShopReceiptKind.TRADE_OUT)
    if kind == "trade_in":
        return Q(kind=ShopReceiptKind.TRADE_IN)
    if kind == "tradings":
        return Q(kind__in=(ShopReceiptKind.TRADE_OUT, ShopReceiptKind.TRADE_IN))
    return (
        Q(kind=ShopReceiptKind.SALE)
        | Q(kind=ShopReceiptKind.CREDIT)
        | Q(kind=ShopReceiptKind.QUOTATION)
        | Q(kind=ShopReceiptKind.TRADE_OUT)
        | Q(kind=ShopReceiptKind.TRADE_IN)
    )


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
    *, kind: str, account_id: int, amount, balance_after, receipt_ref: str = ""
) -> None:
    """Post-commit: tell the supplier their payment arrived."""
    try:
        from communications.automations import maybe_send_supplier_payment_notice

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
        )
    except Exception:
        logger.exception(
            "Supplier payment notice failed for %s account %s", kind, account_id
        )


def apply_account_payment(
    *,
    profile,
    kind: str,
    account_id: int,
    amount,
    payment_method: str = "cash",
    stk_payment_id: str = "",
    shop_ids=None,
    start=None,
    end=None,
    receipt_ids=None,
) -> dict:
    """Apply a payment to an account, clearing receipts oldest → newest (FIFO).

    When ``receipt_ids`` is provided for credit accounts, only those receipts are
    eligible — still allocated oldest → newest within the selection.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction

    kind = (kind or "").strip().lower()
    if kind not in ("credit", "expense", "stock"):
        raise ValidationError("Unknown payment type.")

    method = (payment_method or "cash").strip().lower()
    if kind == "credit" and method not in ("cash", "mpesa"):
        raise ValidationError("Choose cash or M-Pesa.")
    if kind != "credit":
        method = "cash"

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
    mpesa_receipt_number = ""

    with transaction.atomic():
        if kind == "credit":
            client = Client.objects.filter(pk=account_id).first()
            if client is None:
                raise ValidationError("Client not found.")

            if method == "mpesa":
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

            if method == "mpesa":
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
            pay_label = "M-Pesa" if method == "mpesa" else "Cash"
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
        balance_before = _zero()
        for mid in order:
            movement = locked.get(mid) or by_movement[mid]["movement"]
            balance_before += _due_amount(
                by_movement[mid]["total"], movement.amount_paid
            )
        if balance_before <= 0:
            raise ValidationError("This account has no balance due.")
        if pay_amount > balance_before:
            raise ValidationError(
                f"Amount exceeds balance due ({_money_ksh(balance_before)})."
            )
        for mid in order:
            if remaining <= 0:
                break
            movement = locked.get(mid)
            if movement is None:
                continue
            total = by_movement[mid]["total"]
            due = _due_amount(total, movement.amount_paid)
            if due <= 0:
                continue
            apply = min(remaining, due)
            paid = (Decimal(movement.amount_paid or 0) + apply).quantize(
                Decimal("0.01")
            )
            due_after = _due_amount(total, paid)
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
            remaining = (remaining - apply).quantize(Decimal("0.01"))
            cleared += 1
        balance_after = _zero()
        for mid in order:
            movement = locked.get(mid) or by_movement[mid]["movement"]
            balance_after += _due_amount(
                by_movement[mid]["total"], movement.amount_paid
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


def update_credit_receipt_due_date(*, profile, receipt_id: int, credit_due_date: str) -> dict:
    """Update the expected payment date on one credit receipt."""
    from datetime import date

    from django.core.exceptions import ValidationError
    from django.db import transaction

    raw = (credit_due_date or "").strip()
    if not raw:
        raise ValidationError("Payment due date is required.")
    try:
        due_date = date.fromisoformat(raw)
    except ValueError:
        raise ValidationError("Enter a valid payment due date.")

    with transaction.atomic():
        receipt = _credit_receipt_for_profile(profile, receipt_id)
        if receipt is None:
            raise ValidationError("Credit receipt not found.")
        due = _due_amount(receipt.total, receipt.amount_paid)
        from django.utils import timezone

        today = timezone.localdate()
        old_due_date = receipt.credit_due_date
        if old_due_date == due_date:
            pay_by = due_date.strftime("%d %b %Y")
            return {
                "ok": True,
                "receipt_id": receipt.pk,
                "pay_by": pay_by,
                "pay_by_raw": due_date.isoformat(),
                "pay_by_overdue": due > 0 and due_date < today,
                "message": f"Payment due date is already {pay_by}.",
            }
        receipt.credit_due_date = due_date
        receipt.save(update_fields=["credit_due_date"])
        from shops.credit_audit import log_credit_due_date_change

        log_credit_due_date_change(
            receipt=receipt,
            old_date=old_due_date,
            new_date=due_date,
            actor=profile,
        )
        pay_by = due_date.strftime("%d %b %Y")
        return {
            "ok": True,
            "receipt_id": receipt.pk,
            "pay_by": pay_by,
            "pay_by_raw": due_date.isoformat(),
            "pay_by_overdue": due > 0 and due_date < today,
            "message": f"Payment due date updated to {pay_by}.",
        }


def apply_credit_receipt_payment(
    *,
    profile,
    receipt_id: int,
    amount,
    payment_method: str = "cash",
    stk_payment_id: str = "",
) -> dict:
    """Apply a payment to one credit receipt (not FIFO across the account)."""
    from django.core.exceptions import ValidationError
    from django.db import transaction

    method = (payment_method or "cash").strip().lower()
    if method not in ("cash", "mpesa"):
        raise ValidationError("Choose cash or M-Pesa.")

    pay_amount = _parse_pay_amount(amount)
    mpesa_receipt_number = ""

    with transaction.atomic():
        receipt = _credit_receipt_for_profile(profile, receipt_id)
        if receipt is None:
            raise ValidationError("Credit receipt not found.")

        due = _due_amount(receipt.total, receipt.amount_paid)
        if due <= 0:
            raise ValidationError("This receipt has no balance due.")
        if pay_amount > due:
            raise ValidationError(f"Amount exceeds due on this receipt ({_money_ksh(due)}).")

        if method == "mpesa":
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

        pay_label = "M-Pesa" if method == "mpesa" else "Cash"
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
) -> dict:
    """Apply a cash payment to one stock-in or expense receipt."""
    from django.core.exceptions import ValidationError
    from django.db import transaction

    kind = (kind or "").strip().lower()
    if kind not in ("expense", "stock"):
        raise ValidationError("Unknown payment type.")

    pay_amount = _parse_pay_amount(amount)
    shop_filter = _allocated_shop_filter(profile, shop_ids=shop_ids)
    active_shop_ids = shop_filter["active_shop_ids"]

    with transaction.atomic():
        if kind == "expense":
            supplier = ExpenseSupplier.objects.filter(pk=account_id).first()
            if supplier is None:
                raise ValidationError("Supplier not found.")
            expense = (
                Expense.objects.select_for_update()
                .filter(
                    pk=receipt_id,
                    supplier_id=supplier.pk,
                    shop_id__in=active_shop_ids,
                )
                .first()
            )
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
            balance_after = sum(
                (
                    _due_amount(row.amount, row.amount_paid)
                    for row in Expense.objects.filter(
                        shop_id__in=active_shop_ids, supplier_id=supplier.pk
                    )
                ),
                _zero(),
            )
            number = format_simple_doc_number("E", expense.pk)
            transaction.on_commit(
                lambda: _notify_supplier_after_payment(
                    kind="expense",
                    account_id=supplier.pk,
                    amount=pay_amount,
                    balance_after=balance_after,
                    receipt_ref=number,
                )
            )
            return {
                "ok": True,
                "kind": kind,
                "receipt_id": expense.pk,
                "account_id": supplier.pk,
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
        if supplier is None:
            raise ValidationError("Supplier not found.")
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
        matching_lines = list(
            _stock_lines_for_supplier(supplier, active_shop_ids).filter(
                movement_id=movement.pk
            )
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
        by_movement = {}
        for line in _stock_lines_for_supplier(supplier, active_shop_ids).select_related(
            "movement"
        ):
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
        number = format_simple_doc_number("I", movement.pk)
        transaction.on_commit(
            lambda: _notify_supplier_after_payment(
                kind="stock",
                account_id=supplier.pk,
                amount=pay_amount,
                balance_after=balance_after,
                receipt_ref=number,
            )
        )
        return {
            "ok": True,
            "kind": kind,
            "receipt_id": movement.pk,
            "account_id": supplier.pk,
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


def _receipts_qs(*, shop_ids, start, end, kinds=None, exclude_cancelled=True):
    qs = ShopReceipt.objects.filter(shop_id__in=shop_ids)
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    if kinds:
        qs = qs.filter(kind__in=kinds)
    if exclude_cancelled:
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
    paid_cash_amount,
    paid_mpesa_amount,
    unpaid_credit_amount,
    stock_amount,
    expected_profit_amount,
    actual_profit_amount,
    active_shops: int,
    shop_count: int,
) -> dict:
    total = Decimal(total_amount or 0)
    cash = Decimal(cash_amount or 0)
    mpesa = Decimal(mpesa_amount or 0)
    paid_cash = Decimal(paid_cash_amount or 0)
    paid_mpesa = Decimal(paid_mpesa_amount or 0)
    paid_credit = paid_cash + paid_mpesa
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
    collected = cash + mpesa + paid_credit
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
    }


def _insight(title, body):
    return {"title": title, "body": body}


SHRINKAGE_REASONS = frozenset(
    {
        StockOutReason.WASTE,
        StockOutReason.DISPLAY,
    }
)


def _trading_line_qs(*, shop_ids, start, end, kinds):
    qs = ShopReceiptLine.objects.filter(
        receipt__shop_id__in=shop_ids,
        receipt__kind__in=kinds,
    ).exclude(receipt__status=ShopReceiptStatus.CANCELLED)
    if start is not None:
        qs = qs.filter(receipt__created_at__gte=start)
    if end is not None:
        qs = qs.filter(receipt__created_at__lt=end)
    return qs


def _trading_by_shop_for_period(
    *, shop_ids, start, end, kinds=None
) -> dict[int, dict]:
    """Net selling value and COGS by shop for receipts created in [start, end)."""
    kinds = list(kinds or [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT])
    trading_line_qs = _trading_line_qs(
        shop_ids=shop_ids, start=start, end=end, kinds=kinds
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


def _cogs_by_shop_for_period(
    *, shop_ids, start, end, kinds=None
) -> dict[int, tuple[int, Decimal]]:
    """COGS from stamped sale/credit line costs (with last-buy fallback)."""
    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids, start=start, end=end, kinds=kinds
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
    if getter is not None and hasattr(getter, "get"):
        range_raw = (getter.get("range") or "").strip().lower()
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
        }

    class _QueryRequest:
        GET = getter or {}

    range_type, start, end, filter_context = _report_range_bounds(
        request
        if request is not None and not allow_all_time and data is None
        else _QueryRequest()
    )
    delta = end - start
    return {
        **filter_context,
        "range_type": range_type,
        "start": start,
        "end": end,
        "prev_start": start - delta,
        "prev_end": start,
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
    return {
        **date_filter,
        "filter_shops": filter_shops,
        "selected_shop_ids": selected_shop_ids,
        "active_shop_ids": active_shop_ids,
        "credits_shop_scoped": shop_scope == "allocated",
        "credits_require_shop_pick": False,
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
    )
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    filters["search_query"] = (request.GET.get("q") or "").strip()
    filters["receipt_kind"] = (request.GET.get("receipt_kind") or "").strip().lower()
    filters["expense_category"] = (request.GET.get("category") or "").strip().lower()
    filters["supply_item_id"] = (request.GET.get("item_id") or "").strip()
    filters["supplier_status"] = _parse_supplier_status(request.GET.get("status"))
    builders = {
        "overview": _build_overview,
        "revenue": _build_revenue,
        "balances": _build_balances,
        "sales": _build_sales,
        "items": _build_items,
        "stock": _build_stock,
        "supply": _build_supply,
        "quotations": _build_quotations,
        "tradings": _build_tradings,
        "credits": _build_credits,
        "clients": _build_clients,
        "employees": _build_employees,
        "suppliers": _build_suppliers,
        "expenses": _build_expenses,
        "receipts": _build_receipts,
    }
    page = builders[section["slug"]](filters)
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
    page = _build_item_report(filters)
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
    key = (kind or "").strip().lower()
    if key == "sales":
        return Q(kind=ShopReceiptKind.SALE) & ~Q(status=ShopReceiptStatus.CANCELLED)
    if key == "credits":
        return Q(kind=ShopReceiptKind.CREDIT) & ~Q(status=ShopReceiptStatus.CANCELLED)
    if key == "quotations":
        return Q(kind=ShopReceiptKind.QUOTATION) & ~Q(
            status=ShopReceiptStatus.CANCELLED
        )
    if key == "trade_in":
        return Q(kind=ShopReceiptKind.TRADE_IN) & ~Q(status=ShopReceiptStatus.CANCELLED)
    if key == "trade_out":
        return Q(kind=ShopReceiptKind.TRADE_OUT) & ~Q(status=ShopReceiptStatus.CANCELLED)
    if key in {"tradings", "trades", "trade"}:
        return (
            Q(kind=ShopReceiptKind.TRADE_OUT)
            | Q(kind=ShopReceiptKind.TRADE_IN)
            | Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
        ) & ~Q(status=ShopReceiptStatus.CANCELLED)
    if key == "cancelled":
        return Q(status=ShopReceiptStatus.CANCELLED)
    if key == "partial-returns":
        return Q(status=ShopReceiptStatus.PARTIAL_RETURN)
    raise Http404("Receipt type not found.")


def get_analytics_receipt_kind(kind: str) -> dict:
    spec = ANALYTICS_RECEIPT_KINDS.get((kind or "").strip().lower())
    if spec is None:
        raise Http404("Receipt type not found.")
    return spec


def _cashier_label(profile) -> str:
    if not profile or not getattr(profile, "user", None):
        return ""
    return (
        profile.user.get_full_name()
        or profile.employee_id
        or profile.user.username
        or ""
    )


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
        cashier = _cashier_label(getattr(expense, "created_by", None))
        haystack = " ".join(
            [
                number,
                shop_name,
                supplier,
                supplier_phone,
                expense.name or "",
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


def build_analytics_receipts_list(*, profile, request, kind: str) -> dict:
    """Filterable receipt list for one analytics document type across shops."""
    spec = get_analytics_receipt_kind(kind)
    filters = _filters_context(profile, request)
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

    qs = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .filter(_analytics_receipt_kind_filter(slug))
        .select_related("shop", "created_by", "created_by__user")
        .order_by("-created_at", "-id")
    )
    if search:
        qs = qs.filter(
            Q(receipt_number__icontains=search)
            | Q(client_name__icontains=search)
            | Q(client_phone__icontains=search)
            | Q(shop__name__icontains=search)
        )

    rows = []
    for row in qs:
        cashier = _cashier_label(row.created_by)
        client = row.client_name or "Walk-in"
        if row.client_phone:
            client = f"{client} · {row.client_phone}"
        rows.append(
            {
                "number": row.receipt_number,
                "shop": row.shop.name if row.shop else "—",
                "client": client,
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
    ("sale", "Sales"),
    ("delivery", "Delivery notes"),
    ("goods_received", "Goods received"),
    ("all", "All types"),
)


def _confirm_receipt_sale_rows(*, shop_ids, start, end, status_key, search, limit=500):
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
    qs = (
        ShopReceipt.objects.filter(shop_id__in=shop_ids, kind=ShopReceiptKind.SALE)
        .select_related("shop", "created_by", "created_by__user")
    )
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)
    if status_key in status_map:
        qs = qs.filter(status=status_map[status_key])
    if search:
        qs = qs.filter(
            Q(receipt_number__icontains=search)
            | Q(client_name__icontains=search)
            | Q(client_phone__icontains=search)
            | Q(shop__name__icontains=search)
        )
    qs = qs.annotate(
        status_rank=Case(
            When(status=ShopReceiptStatus.ACTIVE, then=Value(0)),
            When(status=ShopReceiptStatus.PARTIAL_RETURN, then=Value(1)),
            When(status=ShopReceiptStatus.CANCELLED, then=Value(2)),
            When(status=ShopReceiptStatus.CONFIRMED, then=Value(3)),
            default=Value(9),
            output_field=IntegerField(),
        )
    ).order_by("status_rank", "-created_at", "-id")[:limit]

    rows = []
    for row in qs:
        is_pending = row.status == ShopReceiptStatus.ACTIVE
        rows.append(
            {
                "receipt_id": row.pk,
                "shop_id": row.shop_id,
                "source": "pos",
                "number": row.receipt_number,
                "shop": row.shop.name if row.shop else "—",
                "client": row.client_name or "Walk-in",
                "client_phone": row.client_phone or "",
                "total": _money_ksh(row.total),
                "status": row.status,
                "status_label": row.get_status_display(),
                "kind": "sale",
                "kind_label": "Sale",
                "payment_label": row.get_payment_method_display(),
                "when": timezone.localtime(row.created_at).strftime("%d %b %Y · %H:%M"),
                "when_dt": row.created_at,
                "cashier": _cashier_label(row.created_by) or "—",
                "can_confirm": is_pending,
                "can_cancel": is_pending,
                "status_rank": 0
                if row.status == ShopReceiptStatus.ACTIVE
                else 1
                if row.status == ShopReceiptStatus.PARTIAL_RETURN
                else 2
                if row.status == ShopReceiptStatus.CANCELLED
                else 3,
            }
        )
    return rows


def _confirm_receipt_transfer_rows(
    *, shop_ids, start, end, status_key, search, kind_key, limit=500
):
    """Inter-shop transfer delivery notes and goods-received slips."""
    from shops.services import DOC_NUMBER_PREFIX, format_simple_doc_number

    qs = (
        StockMovement.objects.filter(movement_type=StockMovementType.REQUEST)
        .filter(Q(shop_id__in=shop_ids) | Q(requested_from_shop_id__in=shop_ids))
        .select_related(
            "shop",
            "requested_from_shop",
            "created_by",
            "created_by__user",
            "responded_by",
            "responded_by__user",
        )
        .prefetch_related("lines__item")
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

    qs = qs.order_by("-created_at", "-id")[: max(limit * 2, limit)]
    search_l = (search or "").strip().lower()
    rows = []
    for movement in qs:
        is_pending = movement.request_status == StockRequestStatus.PENDING
        is_fulfilled = movement.request_status == StockRequestStatus.FULFILLED
        is_declined = movement.request_status == StockRequestStatus.DECLINED
        from_shop = movement.shop
        to_shop = movement.requested_from_shop
        from_name = from_shop.name if from_shop else "—"
        to_name = to_shop.name if to_shop else "—"
        route = f"{from_name} → {to_name}"
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
            status_rank = 3
            cashier = _cashier_label(movement.responded_by) or _cashier_label(
                movement.created_by
            )
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
                status_rank = 2
            else:
                status = "pending"
                status_label = "In transit"
                status_rank = 0
            cashier = _cashier_label(movement.created_by)
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
                cashier or "",
                kind_label,
                status_label,
                (movement.notes or ""),
                (getattr(movement, "rider_name", None) or ""),
            ]
        ).lower()
        if search_l and search_l not in haystack:
            continue

        units = _qty_number(
            sum(
                (Decimal(line.quantity or 0) for line in movement.lines.all()),
                Decimal("0"),
            )
        )
        rows.append(
            {
                "receipt_id": movement.pk,
                "shop_id": shop_id,
                "source": source,
                "number": number,
                "shop": shop_name,
                "client": route,
                "client_phone": "",
                "total": f"{units} unit{'' if units == 1 else 's'}",
                "status": status,
                "status_label": status_label,
                "kind": kind,
                "kind_label": kind_label,
                "payment_label": "Transfer",
                "when": timezone.localtime(when_dt).strftime("%d %b %Y · %H:%M"),
                "when_dt": when_dt,
                "cashier": cashier or "—",
                "can_confirm": False,
                "can_cancel": False,
                "status_rank": status_rank,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def build_confirm_receipts_page(*, profile, request) -> dict:
    """List sales and inter-shop transfer delivery receipts for review."""
    from django.urls import reverse

    from employees.access import role_url_segment

    filters = _filters_context(profile, request, allow_all_time=True, default_all_time=True)
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    search = (request.GET.get("q") or "").strip()
    status_key = (request.GET.get("status") or "").strip().lower()
    kind_key = (request.GET.get("kind") or "sale").strip().lower()
    if kind_key not in {value for value, _label in CONFIRM_RECEIPT_KIND_FILTERS}:
        kind_key = "sale"

    rows = []
    if kind_key in ("sale", "all"):
        rows.extend(
            _confirm_receipt_sale_rows(
                shop_ids=shop_ids,
                start=start,
                end=end,
                status_key=status_key,
                search=search,
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
            )
        )

    rows.sort(
        key=lambda row: (
            row.get("status_rank", 9),
            -(row.get("when_dt").timestamp() if row.get("when_dt") else 0),
            -int(row.get("receipt_id") or 0),
        )
    )
    rows = rows[:500]

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

    if kind_key == "sale":
        count_label = "sales receipt"
    elif kind_key == "delivery":
        count_label = "delivery note"
    elif kind_key == "goods_received":
        count_label = "goods received slip"
    else:
        count_label = "receipt"

    return {
        **filters,
        "search": search,
        "status_filter": status_key,
        "status_options": CONFIRM_RECEIPT_STATUS_FILTERS,
        "kind_filter": kind_key,
        "kind_options": CONFIRM_RECEIPT_KIND_FILTERS,
        "rows": rows,
        "total_count": len(rows),
        "count_label": count_label,
        "page": {
            "headline": "Confirm receipts",
            "lead": (
                "Sales receipts open for confirm/cancel. Delivery notes and goods "
                "received slips from stock transfers are listed for review."
            ),
            "detail_url_template": detail_template,
            "confirm_url_template": confirm_template,
            "cancel_url_template": cancel_template,
        },
    }


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
    return {
        "client": client,
        "balance": _money_ksh(balance),
        "balance_raw": str(balance),
        "credit_count": open_count,
        "receipt_count": len(rows),
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
    receipt_kind = _parse_client_receipt_kind(raw_kind)

    client = Client.objects.filter(pk=client_id).first()
    if client is None or not shop_ids:
        raise Http404("Client not found.")
    if not (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .filter(_client_activity_receipts_q("all"))
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exists()
    ):
        raise Http404("Client not found.")

    base_qs = (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .filter(_client_activity_receipts_q(receipt_kind))
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

    return {
        "client": client,
        "receipt_count": len(rows),
        "item_count": len(item_rows),
        "receipt_kind": receipt_kind,
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
    }


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


def _pdf_download_href(query: str) -> str:
    """Current filter query with the download flag swapped on."""
    from urllib.parse import parse_qs, urlencode

    params = parse_qs(query or "", keep_blank_values=True)
    flat = {key: values[0] for key, values in params.items()}
    flat.pop("download", None)
    flat["download"] = "pdf"
    return f"?{urlencode(flat)}"


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

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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

    brand = (brand or "MY-SHOP").strip() or "MY-SHOP"
    printed_at = timezone.localtime()

    ink = colors.HexColor("#0b1220")
    muted = colors.HexColor("#5b6b7c")
    line = colors.HexColor("#e2e8f0")
    line_strong = colors.HexColor("#cbd5e1")
    surface = colors.HexColor("#f8fafc")
    accent = colors.HexColor("#0f766e")
    accent_soft = colors.HexColor("#ccfbf1")
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
        textColor=accent,
        leading=10,
        spaceAfter=1,
    )
    title_style = ParagraphStyle(
        "PdfTitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=16,
        textColor=ink,
        leading=19,
        spaceAfter=1,
    )
    subtitle_style = ParagraphStyle(
        "PdfSubtitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=9,
        textColor=muted,
        leading=12,
    )
    meta_right = ParagraphStyle(
        "PdfMetaRight",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=8,
        textColor=muted,
        leading=10,
        alignment=TA_RIGHT,
    )
    meta_right_strong = ParagraphStyle(
        "PdfMetaRightStrong",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=9,
        textColor=ink,
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
        alignment=TA_CENTER,
    )
    kpi_value = ParagraphStyle(
        "PdfKpiValue",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=11,
        textColor=ink,
        leading=13,
        alignment=TA_CENTER,
    )
    kpi_hint = ParagraphStyle(
        "PdfKpiHint",
        parent=styles["Normal"],
        fontName=MANROPE_PDF,
        fontSize=7,
        textColor=muted,
        leading=9,
        alignment=TA_CENTER,
    )
    head_cell = ParagraphStyle(
        "PdfHead",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=muted,
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
        textColor=ink,
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
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, accent),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 6 * mm))

    cards = [card for card in (kpi_cards or [])[:6] if card]
    if cards:
        kpi_cols = []
        for label, value, hint in cards:
            block = [
                Paragraph(_esc(str(label or "")).upper(), kpi_label),
                Paragraph(_esc(value if value is not None else "—"), kpi_value),
            ]
            if hint:
                block.append(Paragraph(_esc(hint), kpi_hint))
            kpi_cols.append(block)
        kpi = Table(
            [kpi_cols],
            colWidths=[doc.width / max(len(kpi_cols), 1)] * len(kpi_cols),
        )
        kpi.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), surface),
                    ("BOX", (0, 0), (-1, -1), 0.6, line_strong),
                    ("LINEABOVE", (0, 0), (-1, 0), 1.4, accent),
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

    def _style_data_table(n_rows: int, has_total: bool, head_rows: int = 1) -> TableStyle:
        cmds = [
            ("BACKGROUND", (0, 0), (-1, head_rows - 1), surface),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, head_rows - 1), 5.5),
            ("BOTTOMPADDING", (0, 0), (-1, head_rows - 1), 5.5),
            ("TOPPADDING", (0, head_rows), (-1, -1), 5),
            ("BOTTOMPADDING", (0, head_rows), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, head_rows - 1), 0.9, line_strong),
            ("LINEBELOW", (0, head_rows), (-1, -1), 0.4, line),
            ("BACKGROUND", (0, head_rows), (-1, -1), white),
        ]
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
                for gidx, (label, span) in enumerate(header_groups):
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
                    elif gidx == 0:
                        span_cmds.append(("SPAN", (0, 0), (0, 1)))
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
                body.append(
                    [
                        Paragraph(
                            _esc(value),
                            _cell_style(aligns[idx], is_total),
                        )
                        for idx, value in enumerate(cells)
                    ]
                )
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
            for offset, kind in enumerate(row_kinds):
                r = offset + len(header_rows)
                if kind == "group":
                    style.add("SPAN", (0, r), (-1, r))
                    style.add("BACKGROUND", (0, r), (-1, r), accent_soft)
                    style.add("LINEABOVE", (0, r), (-1, r), 0.6, accent)
                elif kind == "total":
                    style.add("LINEABOVE", (0, r), (-1, r), 1.0, accent)
                    style.add("BACKGROUND", (0, r), (-1, r), accent_soft)
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
            self.setFont(MANROPE_PDF, 7)
            self.setFillColor(muted)
            label = f"Page {self._pageNumber} of {page_count}"
            self.drawRightString(page_size[0] - right_m, 6 * mm, label)
            self.setStrokeColor(accent_soft)
            self.setLineWidth(2)
            self.line(left_m, 8.5 * mm, page_size[0] - right_m - 28 * mm, 8.5 * mm)

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
        if table.get("title") == "Client accounts":
            sections.append(_tradings_accounts_pdf_section(table))
        else:
            sections.append(_client_trades_pdf_section(table))

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


def _item_report_pdf_section(table: dict) -> dict:
    """Item report section mirroring the on-page shop-column matrix."""
    columns = list(table.get("columns") or [])
    quad_cols = [
        col for col in columns if isinstance(col, dict) and col.get("quad")
    ]

    header_groups = [("Item", 1)]
    headers = [""]
    aligns = ["left"]
    for col in quad_cols:
        header_groups.append((col.get("label") or "Shop", 4))
        headers += [
            col.get("sub_qty") or "Qty",
            col.get("sub_amt") or "Amt",
            col.get("sub_pct") or "%",
            col.get("sub_day") or "Day",
        ]
        aligns += ["right", "right", "right", "center"]

    rows = []
    for row in table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if row.get("group_header"):
            rows.append({"kind": "group", "label": row.get("label") or ""})
            continue
        cells = list(row.get("cells") or [])
        flat = [_pdf_cell_label(cells[0]) if cells else "—"]
        for cell in cells[1:]:
            if isinstance(cell, dict) and cell.get("kind") == "quad":
                flat += [
                    str(cell.get("qty") or "—"),
                    str(cell.get("amount") or "—"),
                    str(cell.get("pct") or "—"),
                    str(cell.get("day") or "—"),
                ]
            else:
                flat += ["—", "—", "—", "—"]
        rows.append(
            {
                "kind": "total" if row.get("_total") else "data",
                "cells": flat,
            }
        )

    fractions = [0.14]
    for _col in quad_cols:
        fractions += [0.042, 0.058, 0.04, 0.036]
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


def _stock_lines_for_supplier(supplier: Supplier, shop_ids):
    return StockMovementLine.objects.filter(
        movement__shop_id__in=shop_ids,
        movement__movement_type=StockMovementType.IN,
        supplier_name__iexact=supplier.name,
        supplier_phone_country_code=supplier.phone_country_code,
        supplier_phone_number=supplier.phone_number,
    ).select_related("movement", "movement__shop", "item")


def build_supplier_account(
    *, profile, kind: str, supplier_id: int, request=None, shop_ids=None
) -> dict:
    """Ledger for a stock or expense supplier, grouped by receipt / stock-in event."""
    kind = (kind or "").strip().lower()
    if kind not in ("expense", "stock"):
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

    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if supplier is None:
        raise Http404("Supplier not found.")
    if not shop_ids or not _stock_lines_for_supplier(supplier, shop_ids).exists():
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

    phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
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


def _common_receipt_sets(filters):
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    prev_start, prev_end = filters["prev_start"], filters["prev_end"]
    sales = _receipts_qs(
        shop_ids=shop_ids, start=start, end=end, kinds=[ShopReceiptKind.SALE]
    )
    prev_sales = _receipts_qs(
        shop_ids=shop_ids,
        start=prev_start,
        end=prev_end,
        kinds=[ShopReceiptKind.SALE],
    )
    credits = _receipts_qs(
        shop_ids=shop_ids, start=start, end=end, kinds=[ShopReceiptKind.CREDIT]
    )
    quotes = _receipts_qs(
        shop_ids=shop_ids, start=start, end=end, kinds=[ShopReceiptKind.QUOTATION]
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
        "opening_credit": _zero(),
        "expected_opening_cash": _zero(),
        "expected_opening_mpesa": _zero(),
        "expected_opening_credit": _zero(),
        "opening_cash_variance": _zero(),
        "opening_mpesa_variance": _zero(),
        "opening_credit_variance": _zero(),
        "closing_cash": _zero(),
        "closing_mpesa": _zero(),
        "closing_credit": _zero(),
        "session_open_cash": _zero(),
        "session_open_mpesa": _zero(),
        "cash_sales": _zero(),
        "mpesa_sales": _zero(),
        "cash_refunds": _zero(),
        "mpesa_refunds": _zero(),
        "expenses": _zero(),
        "expenses_paid": _zero(),
        "drawings_paid": _zero(),
        "suppliers_paid": _zero(),
        "expected_cash": _zero(),
        "expected_mpesa": _zero(),
        "cash_variance": _zero(),
        "mpesa_variance": _zero(),
        "live_cash": _zero(),
        "live_mpesa": _zero(),
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
            expected_credit = _zero()
        else:
            expected_cash = Decimal(prior.closing_cash or 0)
            expected_mpesa = Decimal(prior.closing_mpesa or 0)
            expected_credit = Decimal(prior.closing_credit or 0)
        actual_cash = Decimal(session.opening_cash or 0)
        actual_mpesa = Decimal(session.opening_mpesa or 0)
        actual_credit = Decimal(session.opening_credit or 0)
        return {
            "expected_opening_cash": expected_cash,
            "expected_opening_mpesa": expected_mpesa,
            "expected_opening_credit": expected_credit,
            "opening_cash_variance": actual_cash - expected_cash,
            "opening_mpesa_variance": actual_mpesa - expected_mpesa,
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
        entry["expected_opening_credit"] += opening["expected_opening_credit"]
        entry["opening_cash_variance"] += opening["opening_cash_variance"]
        entry["opening_mpesa_variance"] += opening["opening_mpesa_variance"]
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
            credit=Coalesce(Sum("opening_credit"), _zero()),
        )
    ):
        entry = by_shop[row["shop_id"]]
        entry["open_sessions"] = int(row["sessions"] or 0)
        entry["opening_cash"] = Decimal(row["cash"] or 0)
        entry["opening_mpesa"] = Decimal(row["mpesa"] or 0)
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
            "opened_at",
            "closed_at",
            "opening_cash",
            "opening_mpesa",
            "opening_credit",
            "closing_cash",
            "closing_mpesa",
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
            "opened_at",
            "closed_at",
            "opening_cash",
            "opening_mpesa",
            "opening_credit",
            "closing_cash",
            "closing_mpesa",
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
    if activity_sessions:
        from shops.services import _collect_return_payment_events

        min_opened = min(session.opened_at for session in activity_sessions)
        max_closed = max(
            (session.closed_at or now) for session in activity_sessions
        )
        for row in ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            kind=ShopReceiptKind.SALE,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
        ).values(
            "shop_id",
            "created_at",
            "cash_amount",
            "mpesa_amount",
            "return_payment_events",
        ):
            sales_by_shop[row["shop_id"]].append(row)
        for shop_id in shop_ids:
            for event in _collect_return_payment_events(
                shop_id=shop_id, start=min_opened, end=max_closed
            ):
                refunds_by_shop[shop_id].append(event)
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
        from shops.services import _receipt_original_tender

        cash_sales = _zero()
        mpesa_sales = _zero()
        for sale in sales_by_shop.get(session.shop_id, []):
            when = sale["created_at"]
            if session.opened_at <= when < window_end:
                cash, mpesa = _receipt_original_tender(
                    sale.get("cash_amount"),
                    sale.get("mpesa_amount"),
                    sale.get("return_payment_events"),
                )
                cash_sales += cash
                mpesa_sales += mpesa

        cash_refunds = _zero()
        mpesa_refunds = _zero()
        for event in refunds_by_shop.get(session.shop_id, []):
            when = event.get("at")
            if when is not None and session.opened_at <= when < window_end:
                cash_refunds += Decimal(event.get("cash") or 0)
                mpesa_refunds += Decimal(event.get("mpesa") or 0)

        expenses_paid = _zero()
        drawings_paid = _zero()
        drawings_cash = _zero()
        drawings_mpesa = _zero()
        for expense in expenses_by_shop.get(session.shop_id, []):
            when = expense["created_at"]
            if session.opened_at <= when < window_end:
                paid = Decimal(expense["amount_paid"] or 0)
                if (
                    str(expense.get("category") or "").strip().lower()
                    == ExpenseCategory.OWNER_DRAWINGS
                ):
                    cash_part = Decimal(expense.get("cash_amount") or 0)
                    mpesa_part = Decimal(expense.get("mpesa_amount") or 0)
                    if cash_part == 0 and mpesa_part == 0:
                        cash_part = paid
                    drawings_cash += cash_part
                    drawings_mpesa += mpesa_part
                    drawings_paid += cash_part + mpesa_part
                else:
                    expenses_paid += paid

        suppliers_paid = _zero()
        for payment in suppliers_by_shop.get(session.shop_id, []):
            when = payment["created_at"]
            if session.opened_at <= when < window_end:
                suppliers_paid += Decimal(payment["amount_paid"] or 0)

        opening_cash = Decimal(session.opening_cash or 0)
        opening_mpesa = Decimal(session.opening_mpesa or 0)
        expected_cash = (
            opening_cash
            + cash_sales
            - cash_refunds
            - expenses_paid
            - drawings_cash
            - suppliers_paid
        )
        expected_mpesa = (
            opening_mpesa + mpesa_sales - mpesa_refunds - drawings_mpesa
        )
        return {
            "cash_sales": cash_sales,
            "mpesa_sales": mpesa_sales,
            "cash_refunds": cash_refunds,
            "mpesa_refunds": mpesa_refunds,
            "expenses_paid": expenses_paid,
            "drawings_paid": drawings_paid,
            "drawings_cash": drawings_cash,
            "drawings_mpesa": drawings_mpesa,
            "suppliers_paid": suppliers_paid,
            "expected_cash": expected_cash,
            "expected_mpesa": expected_mpesa,
        }

    session_rows = []
    for session in closed_sessions:
        entry = by_shop[session.shop_id]
        entry["close_sessions"] += 1
        entry["closing_cash"] += Decimal(session.closing_cash or 0)
        entry["closing_mpesa"] += Decimal(session.closing_mpesa or 0)
        entry["closing_credit"] += Decimal(session.closing_credit or 0)
        entry["session_open_cash"] += Decimal(session.opening_cash or 0)
        entry["session_open_mpesa"] += Decimal(session.opening_mpesa or 0)

        activity = _activity_for(session, window_end=session.closed_at)
        cash_sales = activity["cash_sales"]
        mpesa_sales = activity["mpesa_sales"]
        expenses_paid = activity["expenses_paid"]
        drawings_paid = activity["drawings_paid"]
        suppliers_paid = activity["suppliers_paid"]
        expected_cash = activity["expected_cash"]
        expected_mpesa = activity["expected_mpesa"]
        closing_cash = Decimal(session.closing_cash or 0)
        closing_mpesa = Decimal(session.closing_mpesa or 0)
        cash_variance = closing_cash - expected_cash
        mpesa_variance = closing_mpesa - expected_mpesa

        entry["cash_sales"] += cash_sales
        entry["mpesa_sales"] += mpesa_sales
        entry["cash_refunds"] += activity["cash_refunds"]
        entry["mpesa_refunds"] += activity["mpesa_refunds"]
        entry["expenses"] += expenses_paid
        entry["expenses_paid"] += expenses_paid
        entry["drawings_paid"] += drawings_paid
        entry["suppliers_paid"] += suppliers_paid
        entry["expected_cash"] += expected_cash
        entry["expected_mpesa"] += expected_mpesa
        entry["cash_variance"] += cash_variance
        entry["mpesa_variance"] += mpesa_variance

        opening = opening_by_session_id.get(session.pk, {})
        session_rows.append(
            {
                "shop_id": session.shop_id,
                "is_open": False,
                "opened_at": session.opened_at,
                "closed_at": session.closed_at,
                "opening_cash": Decimal(session.opening_cash or 0),
                "opening_mpesa": Decimal(session.opening_mpesa or 0),
                "opening_credit": Decimal(session.opening_credit or 0),
                "expected_opening_cash": opening.get(
                    "expected_opening_cash", _zero()
                ),
                "expected_opening_mpesa": opening.get(
                    "expected_opening_mpesa", _zero()
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
                "opening_credit_variance": opening.get(
                    "opening_credit_variance", _zero()
                ),
                "closing_cash": closing_cash,
                "closing_mpesa": closing_mpesa,
                "closing_credit": Decimal(session.closing_credit or 0),
                "cash_sales": cash_sales,
                "mpesa_sales": mpesa_sales,
                "cash_refunds": activity["cash_refunds"],
                "mpesa_refunds": activity["mpesa_refunds"],
                "expenses": expenses_paid,
                "expenses_paid": expenses_paid,
                "drawings_paid": drawings_paid,
                "suppliers_paid": suppliers_paid,
                "expected_cash": expected_cash,
                "expected_mpesa": expected_mpesa,
                "cash_variance": cash_variance,
                "mpesa_variance": mpesa_variance,
            }
        )

    live_by_shop: dict[int, dict] = {}
    for session in open_now_sessions:
        entry = by_shop[session.shop_id]
        activity = _activity_for(session, window_end=now)
        cash_sales = activity["cash_sales"]
        mpesa_sales = activity["mpesa_sales"]
        expenses_paid = activity["expenses_paid"]
        drawings_paid = activity["drawings_paid"]
        suppliers_paid = activity["suppliers_paid"]
        expected_cash = activity["expected_cash"]
        expected_mpesa = activity["expected_mpesa"]
        opening_cash = Decimal(session.opening_cash or 0)
        opening_mpesa = Decimal(session.opening_mpesa or 0)
        opening_credit = Decimal(session.opening_credit or 0)

        entry["is_open"] = True
        entry["live_cash"] = expected_cash
        entry["live_mpesa"] = expected_mpesa
        entry["live_total"] = expected_cash + expected_mpesa

        live_by_shop[session.shop_id] = {
            "is_open": True,
            "opened_at": session.opened_at,
            "opening_cash": opening_cash,
            "opening_mpesa": opening_mpesa,
            "opening_credit": opening_credit,
            "cash_sales": cash_sales,
            "mpesa_sales": mpesa_sales,
            "cash_refunds": activity["cash_refunds"],
            "mpesa_refunds": activity["mpesa_refunds"],
            "expenses_paid": expenses_paid,
            "drawings_paid": drawings_paid,
            "suppliers_paid": suppliers_paid,
            "live_cash": expected_cash,
            "live_mpesa": expected_mpesa,
            "live_total": expected_cash + expected_mpesa,
            "closing_cash": None,
            "closing_mpesa": None,
        }

        opening = opening_by_session_id.get(session.pk, {})
        session_rows.append(
            {
                "shop_id": session.shop_id,
                "is_open": True,
                "opened_at": session.opened_at,
                "closed_at": None,
                "opening_cash": opening_cash,
                "opening_mpesa": opening_mpesa,
                "opening_credit": opening_credit,
                "expected_opening_cash": opening.get(
                    "expected_opening_cash", _zero()
                ),
                "expected_opening_mpesa": opening.get(
                    "expected_opening_mpesa", _zero()
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
                "opening_credit_variance": opening.get(
                    "opening_credit_variance", _zero()
                ),
                "closing_cash": _zero(),
                "closing_mpesa": _zero(),
                "closing_credit": _zero(),
                "cash_sales": cash_sales,
                "mpesa_sales": mpesa_sales,
                "cash_refunds": activity["cash_refunds"],
                "mpesa_refunds": activity["mpesa_refunds"],
                "expenses": expenses_paid,
                "expenses_paid": expenses_paid,
                "drawings_paid": drawings_paid,
                "suppliers_paid": suppliers_paid,
                "expected_cash": expected_cash,
                "expected_mpesa": expected_mpesa,
                "cash_variance": _zero(),
                "mpesa_variance": _zero(),
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
        entry = by_shop[shop_id]
        entry["live_cash"] = closing_cash
        entry["live_mpesa"] = closing_mpesa
        entry["live_total"] = closing_cash + closing_mpesa
        live_by_shop[shop_id] = {
            "is_open": False,
            "opened_at": None,
            "opening_cash": _zero(),
            "opening_mpesa": _zero(),
            "opening_credit": _zero(),
            "cash_sales": _zero(),
            "mpesa_sales": _zero(),
            "expenses_paid": _zero(),
            "drawings_paid": _zero(),
            "suppliers_paid": _zero(),
            "live_cash": closing_cash,
            "live_mpesa": closing_mpesa,
            "live_total": closing_cash + closing_mpesa,
            "closing_cash": closing_cash,
            "closing_mpesa": closing_mpesa,
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
            "expected_cash",
            "expected_mpesa",
            "session_open_cash",
            "session_open_mpesa",
            "cash_sales",
            "mpesa_sales",
            "expenses",
            "expenses_paid",
            "drawings_paid",
            "suppliers_paid",
            "live_cash",
            "live_mpesa",
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
    sales, _prev_sales, credits, _quotes, expenses = _common_receipt_sets(filters)

    sale_docs_by_shop: dict[int, int] = {
        row["shop_id"]: int(row["docs"] or 0)
        for row in sales.values("shop_id").annotate(docs=Count("id"))
    }
    credit_docs_by_shop: dict[int, int] = {
        row["shop_id"]: int(row["docs"] or 0)
        for row in credits.values("shop_id").annotate(docs=Count("id"))
    }
    trading = _trading_by_shop_for_period(shop_ids=shop_ids, start=start, end=end)
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
        credit_amt = Decimal(row.get("credit_value") or 0)
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

    summary_columns = [
        "Metric",
        {
            "label": "Entries",
            "money": True,
            "title": "Receipts or expense documents counted",
        },
        {"label": "Amount", "money": True},
    ]
    summary_rows = [
        [
            "All sales",
            total_sale_docs,
            _money_cell(total_sales, title=f"All sales {_money_ksh(total_sales)}"),
        ],
        [
            "All credits",
            total_credit_docs,
            _money_cell(
                total_credits, title=f"All credits {_money_ksh(total_credits)}"
            ),
        ],
        {
            "cells": [
                "Total revenue",
                total_docs,
                _money_cell(
                    total_revenue,
                    title=f"Total revenue {_money_ksh(total_revenue)} (sales + credits)",
                ),
            ],
            "key": True,
        },
        [
            "Stock value sold",
            "—",
            _money_cell(
                total_stock,
                title=f"Stock value sold {_money_ksh(total_stock)} — buying cost of items sold",
            ),
        ],
        {
            "cells": [
                "Gross margin",
                "—",
                _money_cell(
                    total_gross,
                    tone="good" if total_gross >= 0 else "bad",
                    title=f"Gross margin {_money_ksh(total_gross)} · {total_gross_pct}% of revenue",
                ),
            ],
            "key": True,
        },
        [
            "Expenses cost used",
            total_expense_docs,
            _money_cell(
                total_expenses,
                tone="warn" if total_expenses > 0 else "neutral",
                title=f"Expenses {_money_ksh(total_expenses)} — excludes owner drawings",
            ),
        ],
        {
            "cells": [
                "Profit",
                "—",
                _money_cell(
                    total_profit,
                    tone="good" if total_profit >= 0 else "bad",
                    title=f"Profit {_money_ksh(total_profit)} (gross margin − expenses)",
                ),
            ],
            "key": True,
        },
        {
            "cells": [
                "Profit margin",
                "—",
                _pct_cell(
                    total_margin,
                    tone="good" if total_profit >= 0 else "bad",
                    title=f"Profit margin {total_margin}% of revenue",
                ),
            ],
            "key": True,
        },
    ]

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
        "metrics": [],
        "trends": [],
        "charts": [],
        "sections": [],
        "insights": [],
        "tables": [
            _table(
                "Company summary",
                summary_columns,
                summary_rows,
                empty="No activity for the selected period.",
                shop_grid=False,
                footnote=(
                    "Combined figures across the selected shops for this period."
                ),
            ),
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
    *, shop_ids, start, end, bucket_kind: str, kinds=None
) -> dict[str, dict]:
    """Net selling value and COGS keyed by time-bucket label."""
    kinds = list(kinds or [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT])
    trading_line_qs = _trading_line_qs(
        shop_ids=shop_ids, start=start, end=end, kinds=kinds
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
    trunc = _created_at_trunc("receipt__created_at", bucket_kind)
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
    trunc = _created_at_trunc("created_at", bucket_kind)
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
        shop_ids=shop_ids, start=start, end=end, bucket_kind=bucket_kind
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
    credit_docs_by_bucket = _docs_by_bucket(credits, bucket_kind=bucket_kind)

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
        credit_amt = Decimal(row.get("credit_value") or 0)
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
    sales, _prev_sales, credits, _quotes, _expenses = _common_receipt_sets(filters)

    sale_docs_by_shop: dict[int, int] = {
        row["shop_id"]: int(row["docs"] or 0)
        for row in sales.values("shop_id").annotate(docs=Count("id"))
    }
    credit_docs_by_shop: dict[int, int] = {
        row["shop_id"]: int(row["docs"] or 0)
        for row in credits.values("shop_id").annotate(docs=Count("id"))
    }
    trading = _trading_by_shop_for_period(shop_ids=shop_ids, start=start, end=end)

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
        credit_amt = Decimal(row.get("credit_value") or 0)
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


def _build_balances(filters):
    """Live cashbox per shop from open/close balances, sales, and paid outflows."""
    from django.utils import timezone

    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    day_balances = _day_balance_data(filters)
    by_shop = day_balances["by_shop"]
    totals = day_balances["totals"]
    live_by_shop = day_balances.get("live_by_shop") or {}

    live_cash_total = sum(
        (Decimal(row.get("live_cash") or 0) for row in live_by_shop.values()),
        _zero(),
    )
    live_mpesa_total = sum(
        (Decimal(row.get("live_mpesa") or 0) for row in live_by_shop.values()),
        _zero(),
    )
    live_total = sum(
        (Decimal(row.get("live_total") or 0) for row in live_by_shop.values()),
        _zero(),
    )
    open_shop_count = sum(1 for row in live_by_shop.values() if row.get("is_open"))
    expenses_paid_live = sum(
        (
            Decimal(row.get("expenses_paid") or 0)
            for row in live_by_shop.values()
            if row.get("is_open")
        ),
        _zero(),
    )
    drawings_paid_live = sum(
        (
            Decimal(row.get("drawings_paid") or 0)
            for row in live_by_shop.values()
            if row.get("is_open")
        ),
        _zero(),
    )
    suppliers_paid_live = sum(
        (
            Decimal(row.get("suppliers_paid") or 0)
            for row in live_by_shop.values()
            if row.get("is_open")
        ),
        _zero(),
    )

    live_metrics = [
        _metric(
            "Total",
            _money_ksh(live_total),
            hint=f"{open_shop_count} shop{'s' if open_shop_count != 1 else ''} open",
            tone="bad" if live_total < 0 else "good",
        ),
        _metric(
            "Cash",
            _money_ksh(live_cash_total),
            hint="Open + cash sales − paid out",
            tone="bad" if live_cash_total < 0 else "neutral",
        ),
        _metric(
            "M-Pesa",
            _money_ksh(live_mpesa_total),
            hint="Open + M-Pesa sales",
            tone="bad" if live_mpesa_total < 0 else "neutral",
        ),
        _metric(
            "Expenses paid (open)",
            _money_ksh(expenses_paid_live),
            hint="Operating costs from till",
            tone="warn" if expenses_paid_live > 0 else "neutral",
        ),
        _metric(
            "Drawings paid (open)",
            _money_ksh(drawings_paid_live),
            hint="Owner equity — not expense",
            tone="warn" if drawings_paid_live > 0 else "neutral",
        ),
        _metric(
            "Suppliers paid (open)",
            _money_ksh(suppliers_paid_live),
            hint="Stock payments today",
            tone="warn" if suppliers_paid_live > 0 else "neutral",
        ),
    ]
    live_metric_group = {
        "label": "Live cashbox",
        "icon": "wallet",
        "tone": "live",
        "wide": True,
        "rows": [
            {
                "label": "In till",
                "metrics": [
                    live_metrics[1],  # Cash first for cash|M-Pesa compare
                    live_metrics[2],
                    live_metrics[0],
                ],
            },
            {
                "label": "Paid out",
                "metrics": [
                    live_metrics[3],
                    live_metrics[4],
                    live_metrics[5],
                ],
            },
        ],
    }
    cash_metrics = [
        _metric(
            "Expected opening cash",
            _money_ksh(totals["expected_opening_cash"]),
            hint="Yesterday's closing cash",
        ),
        _metric(
            "Actual opening cash",
            _money_ksh(totals["opening_cash"]),
            hint=f"{int(totals['open_sessions'] or 0)} days opened",
        ),
        _metric(
            "Opening cash variance",
            _money_ksh(totals["opening_cash_variance"]),
            hint="Actual − expected",
            tone=_variance_tone(totals["opening_cash_variance"]),
        ),
        _metric(
            "Expected closing cash",
            _money_ksh(totals["expected_cash"]),
            hint="Open + cash sales − expenses − drawings − suppliers",
        ),
        _metric(
            "Actual closing cash",
            _money_ksh(totals["closing_cash"]),
            hint=f"{int(totals['close_sessions'] or 0)} days closed",
        ),
        _metric(
            "Closing cash variance",
            _money_ksh(totals["cash_variance"]),
            hint="Actual − expected",
            tone=_variance_tone(totals["cash_variance"]),
        ),
    ]
    mpesa_metrics = [
        _metric(
            "Expected opening M-Pesa",
            _money_ksh(totals["expected_opening_mpesa"]),
            hint="Yesterday's closing M-Pesa",
        ),
        _metric(
            "Actual opening M-Pesa",
            _money_ksh(totals["opening_mpesa"]),
        ),
        _metric(
            "Opening M-Pesa variance",
            _money_ksh(totals["opening_mpesa_variance"]),
            hint="Actual − expected",
            tone=_variance_tone(totals["opening_mpesa_variance"]),
        ),
        _metric(
            "Expected closing M-Pesa",
            _money_ksh(totals["expected_mpesa"]),
            hint="Open + M-Pesa sales",
        ),
        _metric(
            "Actual closing M-Pesa",
            _money_ksh(totals["closing_mpesa"]),
        ),
        _metric(
            "Closing M-Pesa variance",
            _money_ksh(totals["mpesa_variance"]),
            hint="Actual − expected",
            tone=_variance_tone(totals["mpesa_variance"]),
        ),
    ]
    metrics = live_metrics + cash_metrics + mpesa_metrics

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            0 if live_by_shop.get(shop.pk, {}).get("is_open") else 1,
            -Decimal(live_by_shop.get(shop.pk, {}).get("live_total") or 0),
            shop.name.lower(),
        ),
    )

    # Columns interleaved Cash | M-Pesa at each stage for side-by-side compare.
    live_columns = [
        "Shop",
        "Status",
        {
            "label": "Open cash",
            "band": "cash-open",
            "band_start": True,
        },
        {"label": "Open M-Pesa", "band": "mpesa-open"},
        {"label": "Cash sales", "band": "cash-open", "band_start": True},
        {"label": "M-Pesa sales", "band": "mpesa-open"},
        {"label": "Expenses paid", "band": "cash-open", "band_start": True},
        {"label": "Drawings paid", "band": "cash-open"},
        {"label": "Suppliers paid", "band": "cash-open"},
        {
            "label": "Live cash",
            "band": "cash-close",
            "band_start": True,
        },
        {"label": "Live M-Pesa", "band": "mpesa-close"},
        {"label": "Close cash", "band": "cash-close", "band_start": True},
        {"label": "Close M-Pesa", "band": "mpesa-close"},
        {
            "label": "Live total",
            "band": "cash-close",
            "band_start": True,
        },
    ]

    live_rows = []
    for shop in shops_sorted:
        live = live_by_shop.get(shop.pk)
        if not live:
            continue
        is_open = bool(live.get("is_open"))
        status_label = "Open" if is_open else "Closed"
        close_cash = live.get("closing_cash")
        close_mpesa = live.get("closing_mpesa")
        live_rows.append(
            [
                shop.name,
                {
                    "label": status_label,
                    "tone": "good" if is_open else "neutral",
                },
                _money_ksh(live.get("opening_cash") or 0)
                if is_open
                else "—",
                _money_ksh(live.get("opening_mpesa") or 0)
                if is_open
                else "—",
                _money_ksh(live.get("cash_sales") or 0) if is_open else "—",
                _money_ksh(live.get("mpesa_sales") or 0) if is_open else "—",
                _money_ksh(live.get("expenses_paid") or 0) if is_open else "—",
                _money_ksh(live.get("drawings_paid") or 0) if is_open else "—",
                _money_ksh(live.get("suppliers_paid") or 0) if is_open else "—",
                {
                    "label": _money_ksh(live.get("live_cash") or 0),
                    "tone": "bad"
                    if Decimal(live.get("live_cash") or 0) < 0
                    else "good",
                },
                {
                    "label": _money_ksh(live.get("live_mpesa") or 0),
                    "tone": "bad"
                    if Decimal(live.get("live_mpesa") or 0) < 0
                    else "good",
                },
                _money_ksh(close_cash)
                if close_cash is not None and not is_open
                else ("—" if is_open else _money_ksh(0)),
                _money_ksh(close_mpesa)
                if close_mpesa is not None and not is_open
                else ("—" if is_open else _money_ksh(0)),
                {
                    "label": _money_ksh(live.get("live_total") or 0),
                    "tone": "bad"
                    if Decimal(live.get("live_total") or 0) < 0
                    else "good",
                },
            ]
        )

    if live_rows:
        live_rows.append(
            [
                "Total",
                f"{open_shop_count} open",
                "—",
                "—",
                "—",
                "—",
                _money_ksh(expenses_paid_live),
                _money_ksh(drawings_paid_live),
                _money_ksh(suppliers_paid_live),
                _money_ksh(live_cash_total),
                _money_ksh(live_mpesa_total),
                "—",
                "—",
                {
                    "label": _money_ksh(live_total),
                    "tone": "good",
                },
            ]
        )

    summary_columns = [
        "Shop",
        {
            "label": "Exp. open cash",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-open",
            "band_start": True,
        },
        {
            "label": "Exp. open M-Pesa",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-open",
        },
        {
            "label": "Open cash",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-open",
            "band_start": True,
        },
        {
            "label": "Open M-Pesa",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-open",
        },
        {
            "label": "Open cash var",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-open",
            "band_start": True,
        },
        {
            "label": "Open M-Pesa var",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-open",
        },
        {
            "label": "Exp. close cash",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-close",
            "band_start": True,
        },
        {
            "label": "Exp. close M-Pesa",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-close",
        },
        {
            "label": "Close cash",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-close",
            "band_start": True,
        },
        {
            "label": "Close M-Pesa",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-close",
        },
        {
            "label": "Close cash var",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-close",
            "band_start": True,
        },
        {
            "label": "Close M-Pesa var",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-close",
        },
    ]

    def _pair(
        shop_id: int,
        sessions_key: str,
        amount_key: str,
        *,
        variance: bool = False,
    ):
        entry = by_shop.get(shop_id, {})
        qty = int(entry.get(sessions_key) or 0)
        amount = Decimal(entry.get(amount_key) or 0)
        tone = _variance_tone(amount) if variance else "neutral"
        return _qty_amount_cell(
            qty,
            amount,
            title=f"{qty} · {_money_ksh(amount)}",
            tone=tone,
        )

    summary_rows = []
    period_shops = sorted(
        shops,
        key=lambda shop: (
            -Decimal(by_shop.get(shop.pk, {}).get("closing_cash") or 0),
            shop.name.lower(),
        ),
    )
    for shop in period_shops:
        entry = by_shop.get(shop.pk)
        if not entry:
            continue
        if not (
            entry["open_sessions"]
            or entry["close_sessions"]
            or entry["opening_cash"]
            or entry["closing_cash"]
            or entry["opening_mpesa"]
            or entry["closing_mpesa"]
        ):
            continue
        summary_rows.append(
            [
                shop.name,
                _pair(shop.pk, "open_sessions", "expected_opening_cash"),
                _pair(shop.pk, "open_sessions", "expected_opening_mpesa"),
                _pair(shop.pk, "open_sessions", "opening_cash"),
                _pair(shop.pk, "open_sessions", "opening_mpesa"),
                _pair(
                    shop.pk,
                    "open_sessions",
                    "opening_cash_variance",
                    variance=True,
                ),
                _pair(
                    shop.pk,
                    "open_sessions",
                    "opening_mpesa_variance",
                    variance=True,
                ),
                _pair(shop.pk, "close_sessions", "expected_cash"),
                _pair(shop.pk, "close_sessions", "expected_mpesa"),
                _pair(shop.pk, "close_sessions", "closing_cash"),
                _pair(shop.pk, "close_sessions", "closing_mpesa"),
                _pair(
                    shop.pk,
                    "close_sessions",
                    "cash_variance",
                    variance=True,
                ),
                _pair(
                    shop.pk,
                    "close_sessions",
                    "mpesa_variance",
                    variance=True,
                ),
            ]
        )

    if summary_rows:
        summary_rows.append(
            [
                "Total",
                _qty_amount_cell(
                    totals["open_sessions"], totals["expected_opening_cash"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"], totals["expected_opening_mpesa"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"], totals["opening_cash"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"], totals["opening_mpesa"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"],
                    totals["opening_cash_variance"],
                    tone=_variance_tone(totals["opening_cash_variance"]),
                ),
                _qty_amount_cell(
                    totals["open_sessions"],
                    totals["opening_mpesa_variance"],
                    tone=_variance_tone(totals["opening_mpesa_variance"]),
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["expected_cash"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["expected_mpesa"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["closing_cash"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["closing_mpesa"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"],
                    totals["cash_variance"],
                    tone=_variance_tone(totals["cash_variance"]),
                ),
                _qty_amount_cell(
                    totals["close_sessions"],
                    totals["mpesa_variance"],
                    tone=_variance_tone(totals["mpesa_variance"]),
                ),
            ]
        )

    shops_by_id = {shop.pk: shop for shop in shops}
    session_columns = [
        "Shop",
        "Opened",
        "Closed",
        "Exp. open cash",
        "Exp. open M-Pesa",
        "Open cash",
        "Open M-Pesa",
        "Open cash var",
        "Open M-Pesa var",
        "Cash sales",
        "M-Pesa sales",
        "Expenses paid",
        "Drawings paid",
        "Suppliers paid",
        "Exp. close cash",
        "Exp. close M-Pesa",
        "Close cash",
        "Close M-Pesa",
        "Close cash var",
        "Close M-Pesa var",
    ]
    session_rows = []
    for row in sorted(
        day_balances["session_rows"],
        key=lambda item: item["closed_at"] or item["opened_at"] or timezone.now(),
        reverse=True,
    ):
        shop = shops_by_id.get(row["shop_id"])
        shop_name = shop.name if shop else f"Shop {row['shop_id']}"
        opened = timezone.localtime(row["opened_at"]).strftime("%d %b %H:%M")
        if row.get("is_open") or row["closed_at"] is None:
            closed = "Open"
        else:
            closed = timezone.localtime(row["closed_at"]).strftime("%d %b %H:%M")
        cash_var = row["cash_variance"]
        mpesa_var = row["mpesa_variance"]
        open_cash_var = row["opening_cash_variance"]
        open_mpesa_var = row["opening_mpesa_variance"]
        is_open_row = bool(row.get("is_open"))
        session_rows.append(
            [
                shop_name,
                opened,
                closed,
                _money_ksh(row["expected_opening_cash"]),
                _money_ksh(row["expected_opening_mpesa"]),
                _money_ksh(row["opening_cash"]),
                _money_ksh(row["opening_mpesa"]),
                {
                    "label": _money_ksh(open_cash_var),
                    "tone": _variance_tone(open_cash_var),
                },
                {
                    "label": _money_ksh(open_mpesa_var),
                    "tone": _variance_tone(open_mpesa_var),
                },
                _money_ksh(row["cash_sales"]),
                _money_ksh(row["mpesa_sales"]),
                _money_ksh(row.get("expenses_paid", row.get("expenses"))),
                _money_ksh(row.get("drawings_paid") or 0),
                _money_ksh(row.get("suppliers_paid") or 0),
                _money_ksh(row["expected_cash"]),
                _money_ksh(row["expected_mpesa"]),
                "—" if is_open_row else _money_ksh(row["closing_cash"]),
                "—" if is_open_row else _money_ksh(row["closing_mpesa"]),
                {
                    "label": "—" if is_open_row else _money_ksh(cash_var),
                    "tone": "neutral" if is_open_row else _variance_tone(cash_var),
                },
                {
                    "label": "—" if is_open_row else _money_ksh(mpesa_var),
                    "tone": "neutral"
                    if is_open_row
                    else _variance_tone(mpesa_var),
                },
            ]
        )

    return {
        "headline": "Day balances",
        "lead": (
            "Live cashbox = opening + sales − expenses paid − owner drawings − suppliers paid. "
            "Closed shops show last recorded closing balances. "
            "Period variances compare expected vs actual open/close counts."
        ),
        "alerts": [],
        "metrics": metrics,
        "metric_groups": [
            live_metric_group,
            _till_metric_group(
                label="Cash till (period)",
                icon="banknote",
                tone="cash",
                metrics=cash_metrics,
            ),
            _till_metric_group(
                label="M-Pesa till (period)",
                icon="smartphone",
                tone="mpesa",
                metrics=mpesa_metrics,
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                "Live cashbox by shop",
                live_columns,
                live_rows,
                empty="No open or recently closed shop days to show a live cashbox.",
                footnote=(
                    "Open shops: live cash = opening cash + cash sales − expenses paid "
                    "− owner drawings − suppliers paid; live M-Pesa = opening M-Pesa + M-Pesa sales. "
                    "Owner drawings reduce cash but are equity, not operating expense. "
                    "Closed shops: live amounts are the last recorded closing balances. "
                    "Cash and M-Pesa columns sit side by side at each stage for easier compare."
                ),
                shop_grid=True,
            ),
            _table(
                "Balances by shop (period)",
                summary_columns,
                summary_rows,
                empty="No shop day open/close records for this period.",
                footnote=(
                    "Expected opening = previous day's closing balance. "
                    "Expected closing cash = opening + cash sales − expenses paid "
                    "− owner drawings − suppliers paid. Expected closing M-Pesa = opening + M-Pesa sales. "
                    "Variance = actual − expected. Cash and M-Pesa sit side by side at each stage."
                ),
                shop_grid=True,
            ),
            _table(
                "Day sessions",
                session_columns,
                session_rows,
                empty="No shop day sessions in this period.",
                table_class="ax-table--sessions",
            ),
        ],
    }


def _credit_payment_totals_by_shop(
    *, shop_ids, start, end
) -> tuple[dict[int, tuple[int, Decimal]], dict[int, tuple[int, Decimal]]]:
    """Credit account collections in period, split by cash / M-Pesa per shop."""
    paid_cash_by_shop: dict[int, tuple[int, Decimal]] = {}
    paid_mpesa_by_shop: dict[int, tuple[int, Decimal]] = {}
    events = _within_created_range(
        ClientCreditAccountEvent.objects.filter(
            shop_id__in=shop_ids,
            kind__in=(
                ClientCreditAccountEventKind.PAYMENT_CASH,
                ClientCreditAccountEventKind.PAYMENT_MPESA,
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
        else:
            paid_cash_by_shop[shop_id] = entry
    return paid_cash_by_shop, paid_mpesa_by_shop


def _credit_payment_totals_by_bucket(
    *, shop_ids, start, end, bucket_kind: str
) -> tuple[dict[str, tuple[int, Decimal]], dict[str, tuple[int, Decimal]]]:
    """Credit account collections in period, split by cash / M-Pesa per time bucket."""
    paid_cash_by_bucket: dict[str, tuple[int, Decimal]] = {}
    paid_mpesa_by_bucket: dict[str, tuple[int, Decimal]] = {}
    trunc = _created_at_trunc("occurred_at", bucket_kind)
    events = _within_created_range(
        ClientCreditAccountEvent.objects.filter(
            shop_id__in=shop_ids,
            kind__in=(
                ClientCreditAccountEventKind.PAYMENT_CASH,
                ClientCreditAccountEventKind.PAYMENT_MPESA,
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
        else:
            paid_cash_by_bucket[label] = entry
    return paid_cash_by_bucket, paid_mpesa_by_bucket


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
    unpaid_due_expr = Greatest(
        ExpressionWrapper(
            F("total") - F("amount_paid"),
            output_field=money_field,
        ),
        zero_money,
    )

    trunc = _created_at_trunc("created_at", bucket_kind)
    total_by_bucket: dict[str, tuple[int, Decimal]] = {}
    cash_by_bucket: dict[str, tuple[int, Decimal]] = {}
    mpesa_by_bucket: dict[str, tuple[int, Decimal]] = {}
    unpaid_credit_by_bucket: dict[str, tuple[int, Decimal]] = {}
    paid_cash_by_bucket, paid_mpesa_by_bucket = _credit_payment_totals_by_bucket(
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
    ):
        label = _bucket_label(row["bucket"], bucket_kind)
        if not label:
            continue
        total_by_bucket[label] = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        cash_by_bucket[label] = (0, Decimal(row["cash"] or 0))
        mpesa_by_bucket[label] = (0, Decimal(row["mpesa"] or 0))

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
    )

    paid_total_by_bucket: dict[str, tuple[int, Decimal]] = {}
    general_by_bucket: dict[str, tuple[int, Decimal]] = {}
    for label in labels:
        sale_docs, _sale_amt = total_by_bucket.get(label, (0, _zero()))
        unpaid_docs, unpaid_amt = unpaid_credit_by_bucket.get(label, (0, _zero()))
        cash_amt = cash_by_bucket.get(label, (0, _zero()))[1]
        mpesa_amt = mpesa_by_bucket.get(label, (0, _zero()))[1]
        paid_cash_docs, paid_cash_amt = paid_cash_by_bucket.get(label, (0, _zero()))
        paid_mpesa_docs, paid_mpesa_amt = paid_mpesa_by_bucket.get(label, (0, _zero()))
        paid_amt = Decimal(paid_cash_amt or 0) + Decimal(paid_mpesa_amt or 0)
        paid_total_by_bucket[label] = (
            int(paid_cash_docs or 0) + int(paid_mpesa_docs or 0),
            paid_amt,
        )
        general_by_bucket[label] = (
            int(sale_docs or 0) + int(unpaid_docs or 0),
            Decimal(cash_amt or 0)
            + Decimal(mpesa_amt or 0)
            + paid_amt
            + Decimal(unpaid_amt or 0),
        )

    metric_maps = [
        ("Cash", cash_by_bucket, "cash", "money"),
        ("M-Pesa", mpesa_by_bucket, "mpesa", "money"),
        ("Paid cash", paid_cash_by_bucket, "credits", "money"),
        ("Paid M-Pesa", paid_mpesa_by_bucket, "credits", "money"),
        ("Paid total", paid_total_by_bucket, "credits", "pair"),
        ("Unpaid", unpaid_credit_by_bucket, "unpaid", "pair"),
        ("General total", general_by_bucket, "total", "pair"),
    ]
    column_titles = {
        "Cash": "POS cash payments on sale receipts",
        "M-Pesa": "POS M-Pesa payments on sale receipts",
        "Paid cash": "Credit account collections paid in cash this bucket",
        "Paid M-Pesa": "Credit account collections paid via M-Pesa this bucket",
        "Paid total": "Paid cash + Paid M-Pesa",
        "Unpaid": "Outstanding balance on open credit receipts this bucket",
        "General total": "Cash + M-Pesa + Paid total + Unpaid",
    }

    columns = [config["column_label"]]
    for label, _by_bucket, band, cell_kind in metric_maps:
        band_start = label in {
            "Cash",
            "M-Pesa",
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
                "title": "Cash + M-Pesa + Paid total − stock (this bucket)",
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
            "Cash and M-Pesa are POS sale payments; "
            "Paid cash / Paid M-Pesa are credit account collections in this bucket; "
            "Paid total = Paid cash + Paid M-Pesa; "
            "Unpaid = open credit balances; "
            "General total = Cash + M-Pesa + Paid total + Unpaid; "
            "Expected profit = selling value − stock; "
            "Actual profit = Cash + M-Pesa + Paid total − stock."
        ),
    )


def _build_sales(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
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
    unpaid_credit_by_shop: dict[int, tuple[int, Decimal]] = {}
    paid_cash_by_shop, paid_mpesa_by_shop = _credit_payment_totals_by_shop(
        shop_ids=shop_ids,
        start=start,
        end=end,
    )
    for row in sales.values("shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
        cash=Coalesce(Sum(cash_expr), _zero()),
        mpesa=Coalesce(Sum(mpesa_expr), _zero()),
    ):
        shop_id = row["shop_id"]
        total_by_shop[shop_id] = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        cash_by_shop[shop_id] = (0, Decimal(row["cash"] or 0))
        mpesa_by_shop[shop_id] = (0, Decimal(row["mpesa"] or 0))

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
        cash_amt = cash_by_shop.get(shop.pk, (0, _zero()))[1]
        mpesa_amt = mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
        paid_cash_docs, paid_cash_amt = paid_cash_by_shop.get(shop.pk, (0, _zero()))
        paid_mpesa_docs, paid_mpesa_amt = paid_mpesa_by_shop.get(shop.pk, (0, _zero()))
        unpaid_docs, unpaid_amt = unpaid_credit_by_shop.get(shop.pk, (0, _zero()))
        sale_docs = total_by_shop.get(shop.pk, (0, _zero()))[0]
        paid_amt = Decimal(paid_cash_amt or 0) + Decimal(paid_mpesa_amt or 0)
        collected = Decimal(cash_amt or 0) + Decimal(mpesa_amt or 0) + paid_amt
        general_amt = collected + Decimal(unpaid_amt or 0)
        selling_by_shop[shop.pk] = selling
        stock_by_shop[shop.pk] = stock
        expected_profit_by_shop[shop.pk] = selling - stock
        actual_profit_by_shop[shop.pk] = collected - stock
        paid_total_by_shop[shop.pk] = (
            int(paid_cash_docs or 0) + int(paid_mpesa_docs or 0),
            paid_amt,
        )
        general_by_shop[shop.pk] = (
            int(sale_docs or 0) + int(unpaid_docs or 0),
            general_amt,
        )

    metric_maps = [
        ("Cash", cash_by_shop, "cash", "money"),
        ("M-Pesa", mpesa_by_shop, "mpesa", "money"),
        ("Paid cash", paid_cash_by_shop, "credits", "money"),
        ("Paid M-Pesa", paid_mpesa_by_shop, "credits", "money"),
        ("Paid total", paid_total_by_shop, "credits", "pair"),
        ("Unpaid", unpaid_credit_by_shop, "unpaid", "pair"),
        ("General total", general_by_shop, "total", "pair"),
    ]
    column_titles = {
        "Cash": "POS cash payments on sale receipts",
        "M-Pesa": "POS M-Pesa payments on sale receipts",
        "Paid cash": "Credit account collections paid in cash this period",
        "Paid M-Pesa": "Credit account collections paid via M-Pesa this period",
        "Paid total": "Paid cash + Paid M-Pesa",
        "Unpaid": "Outstanding balance on open credit receipts this period",
        "General total": "Cash + M-Pesa + Paid total + Unpaid",
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
                "title": "Cash + M-Pesa + Paid total − stock (this period)",
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
    paid_cash_amount = _zero()
    paid_mpesa_amount = _zero()
    unpaid_credit_amount = _zero()
    for shop in shops_sorted:
        qty, amount = general_by_shop.get(shop.pk, (0, _zero()))
        total_docs += int(qty or 0)
        total_amount += Decimal(amount or 0)
        cash_amount += cash_by_shop.get(shop.pk, (0, _zero()))[1]
        mpesa_amount += mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
        paid_cash_amount += paid_cash_by_shop.get(shop.pk, (0, _zero()))[1]
        paid_mpesa_amount += paid_mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
        unpaid_credit_amount += unpaid_credit_by_shop.get(shop.pk, (0, _zero()))[1]
    total_stock = sum((stock_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero())
    total_selling = sum(
        (selling_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero()
    )
    paid_credit_amount = paid_cash_amount + paid_mpesa_amount
    total_collected = cash_amount + mpesa_amount + paid_credit_amount
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
            "Sale receipts by shop — cash, M-Pesa, paid cash / M-Pesa, unpaid, "
            "general total, stock, expected profit, and actual profit. "
            "Day shows hourly, month shows daily, year shows monthly."
        ),
        "alerts": [],
        "summary_board": _sales_summary_board(
            total_amount=total_amount,
            total_docs=total_docs,
            cash_amount=cash_amount,
            mpesa_amount=mpesa_amount,
            paid_cash_amount=paid_cash_amount,
            paid_mpesa_amount=paid_mpesa_amount,
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
                    "Cash and M-Pesa are POS sale payments. "
                    "Paid cash / Paid M-Pesa are credit account collections this period. "
                    "Paid total = Paid cash + Paid M-Pesa. "
                    "Unpaid = open credit balances this period. "
                    "General total = Cash + M-Pesa + Paid total + Unpaid. "
                    "Expected profit = selling value − stock. "
                    "Actual profit = Cash + M-Pesa + Paid total − stock."
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


def _item_report_quad(*, stats, day, title) -> dict:
    return {
        "kind": "quad",
        "qty": _qty_label(stats["avg_qty"]),
        "amount": _money_dense(stats["avg_profit"]),
        "pct": f"{stats['pct']}%",
        "day": day,
        "tone": "good" if stats["gross"] >= 0 else "bad",
        "title": title,
    }


def _item_report_empty_quad(label) -> dict:
    return {
        "kind": "quad",
        "qty": "—",
        "amount": "—",
        "pct": "—",
        "day": "—",
        "title": f"No sales at {label}",
    }


def _item_report_row(*, meta, cat, shops, shop_name) -> dict:
    """One matrix row: item + per-shop quad cells + total."""
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

    cells = [{"label": meta["name"]}]
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
    item_meta: dict = {}
    for (item_id, shop_id), entry in by_pair.items():
        gross = entry["value"] - entry["cogs"]
        item_gross[item_id] = item_gross.get(item_id, _zero()) + gross
        meta = item_meta.get(item_id)
        if meta is None:
            cat = (categories.get(item_id) or "").strip() or "Uncategorised"
            meta = {"name": entry["name"], "cat": cat, "pairs": []}
            item_meta[item_id] = meta
            cat_gross.setdefault(cat, _zero())
        meta["pairs"].append((shop_id, entry))
        cat_gross[meta["cat"]] += gross

    rows = []
    total_sales = _zero()
    total_cogs = _zero()
    for cat in sorted(cat_gross, key=lambda c: (-cat_gross[c], c.lower())):
        rows.append(
            {
                "group_header": True,
                "label": f"{cat} — {_money_dense(cat_gross[cat])}",
                "search_text": cat,
            }
        )
        cat_item_ids = sorted(
            (iid for iid, m in item_meta.items() if m["cat"] == cat),
            key=lambda iid: (
                -item_gross[iid],
                (item_meta[iid]["name"] or "").lower(),
            ),
        )
        for iid in cat_item_ids:
            rows.append(
                _item_report_row(
                    meta=item_meta[iid],
                    cat=cat,
                    shops=shops,
                    shop_name=shop_name,
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
        total_cells = [{"label": "All items"}]
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
    board["hero"]["hint"] = board["hero"]["hint"].replace("Margin", "% profit")

    columns = [{"label": "Item"}]
    for index, shop in enumerate(shops):
        col = _shop_col(shop)
        col.update(
            {
                "quad": True,
                "sub_qty": "Avg qty",
                "sub_amt": "Avg pft",
                "sub_pct": "% pft",
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
            "title": "All selected shops — per selling day",
            "quad": True,
            "sub_qty": "Avg qty",
            "sub_amt": "Avg pft",
            "sub_pct": "% pft",
            "sub_day": "Day",
            "total": True,
            "band": "total",
            "band_start": True,
        }
    )

    return {
        "headline": "Item report",
        "lead": "Ranked by profit — each shop column shows avg qty, avg profit, % profit, and best-selling day.",
        "alerts": alerts,
        "show_search": True,
        "search_placeholder": "Search items or shops…",
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
                    "Sales and credits, net of returns. Avg qty and avg "
                    "profit are per selling day in this period. % profit = "
                    "profit ÷ revenue. Day is the weekday the item sells "
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
                table_rows,
                empty="No stock for selected shops.",
                footnote=(
                    "Qty/Amt cells show quantity and stock value. "
                    "Implied opening is derived (no day-open stock snapshot yet). "
                    "Inter-shop transfers can skew the period bridge."
                ),
                shop_grid=True,
                searchable=True,
                table_class="ax-table--stock",
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
            "supply_history": {},
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
                "movement_ids": set(),
                "quantity": 0,
                "amount": _zero(),
                "details": [],
            },
        )
        quantity = Decimal(line.quantity or 0)
        unit_price = Decimal(line.buying_price or 0)
        entry["movement_ids"].add(line.movement_id)
        entry["quantity"] += quantity
        entry["amount"] += unit_price * quantity
        entry["details"].append(
            {
                "date": timezone.localtime(line.movement.created_at).strftime(
                    "%d %b %Y, %H:%M"
                ),
                "shop": line.movement.shop.name if line.movement.shop_id else "—",
                "quantity": _qty_number(quantity),
                "unit_price": _money_ksh(unit_price),
                "amount": _money_ksh(unit_price * quantity),
            }
        )

    supplier_rows = []
    supply_history = {}
    for index, entry in enumerate(
        sorted(grouped.values(), key=lambda row: (-len(row["movement_ids"]), row["name"].casefold())),
        start=1,
    ):
        history_key = f"supplier-{index}"
        average_price = (
            entry["amount"] / entry["quantity"] if entry["quantity"] else _zero()
        )
        supply_history[history_key] = entry["details"]
        supplier_rows.append(
            {
                "history_key": history_key,
                "name": entry["name"],
                "phone": entry["phone"],
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
        "supply_history": supply_history,
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


def _trade_receipts_qs(shop_ids, start, end):
    return (
        _within_created_range(
            ShopReceipt.objects.filter(
                Q(shop_id__in=shop_ids) | Q(source_shop_id__in=shop_ids)
            ),
            start,
            end,
        )
        .filter(
            Q(kind=ShopReceiptKind.TRADE_OUT)
            | Q(kind=ShopReceiptKind.TRADE_IN)
            | Q(kind=ShopReceiptKind.SALE, settled_from_trade=True)
        )
    )


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

    qs = _trade_receipts_qs(shop_ids, start, end).select_related("client")
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
        _trade_receipts_qs(filters["active_shop_ids"], filters["start"], filters["end"])
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
    return {
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
        "search_placeholder": "Search trade or item",
        "list_table_layout": True,
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
        "tables": [
            _table(
                "Trades",
                _trade_receipt_columns(),
                summary["rows"],
                empty="No trade ins or trade outs for this client in the selected period.",
                footnote="Open a trade to pay, exchange, or return items.",
                searchable=True,
            )
        ],
    }


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
        receipt.kind == ShopReceiptKind.TRADE_OUT
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
        "receipt_number": receipt.receipt_number,
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
        shop_ids=shop_ids, start=start, end=end, kinds=[ShopReceiptKind.CREDIT]
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

    clients_page = _build_clients({**filters, "from_credits": True})
    alerts += list(clients_page.get("alerts") or [])
    client_table = (clients_page.get("tables") or [None])[0]
    if client_table is not None:
        client_table["search_placeholder"] = (
            clients_page.get("search_placeholder")
            or "Search clients by name or phone…"
        )
        client_table["search_empty"] = (
            clients_page.get("search_empty") or "No clients match that search."
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
    return {
        "headline": "Credits",
        "lead": (
            f"Items on credit for {period_label} — each shop column shows "
            f"qty, credit amount, % paid back, and top credit day. "
            f"Open a client below to view their account."
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
            *([client_table] if client_table is not None else []),
        ],
    }


def _client_credit_stock_map(receipts_qs) -> dict[int, Decimal]:
    """Buying cost of items on each client's credit-account receipts."""
    line_qs = ShopReceiptLine.objects.filter(receipt__in=receipts_qs)
    remaining_cogs = ExpressionWrapper(
        F("unit_cost") * (F("quantity") - F("returned_quantity")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    stock: dict[int, Decimal] = {}
    for row in line_qs.values("receipt__client_id").annotate(
        cogs=Coalesce(Sum(remaining_cogs), _zero())
    ):
        client_id = row["receipt__client_id"]
        if client_id is None:
            continue
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
    receipt_kind = (
        "credits" if from_credits else _parse_client_receipt_kind(filters.get("receipt_kind"))
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

    all_receipts = (
        ShopReceipt.objects.filter(shop_id__in=shop_ids)
        .filter(_client_activity_receipts_q(receipt_kind))
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exclude(client_id=None)
    )
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
            continue
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
        client_ids_in_scope = set(totals_by_client.keys())
    else:
        client_ids_in_scope = set(
            all_receipts.values_list("client_id", flat=True).distinct()
        )
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
        client_cell = {
            "href": client_credit_account_url(
                role, client_id, query=query, from_credits=from_credits
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
            all_href = analytics_section_url(role, "credits")
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
                "returns. Cleared clients (owed 0) are listed last."
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
                "Cleared clients (owed 0) are listed last."
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
            "Cancelled receipts are excluded."
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
        "tables": [
            _table(
                table_title,
                columns,
                client_rows,
                empty=empty,
                footnote=footnote,
                shop_grid=True,
                searchable=True,
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
    for entry in sorted(
        by_staff.values(),
        key=lambda row: (-row["total_amount"], row["label"].lower()),
    ):
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
) -> tuple[list, dict[int, tuple[int, Decimal]]]:
    """Build matrix rows: supplier link + En/Bal per shop + total.

    When supplier_priority is provided (0 = not paid, 1 = paid), not-paid
    suppliers sort above paid ones; within each group, highest Bal first.
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

    stock_by_supplier_shop: dict[int, dict[int, tuple[int, Decimal]]] = {}
    supplier_priority: dict[int, int] = {}
    for supplier_id, shop_map in stock_detail.items():
        supplier_due = sum(
            (Decimal(cell["due"] or 0) for cell in shop_map.values()),
            _zero(),
        )
        # 0 = not paid (any outstanding), 1 = fully paid — unpaid first in All.
        supplier_priority[supplier_id] = 0 if supplier_due > 0 else 1
        converted = {}
        for shop_id, cell in shop_map.items():
            entries = int(cell["entries"] or 0)
            due = Decimal(cell["due"] or 0)
            paid = Decimal(cell["paid"] or 0)
            if status_key == "paid":
                amount = paid
            elif status_key == "all":
                amount = due if supplier_due > 0 else paid
            else:
                amount = due
            converted[shop_id] = (entries, amount)
        stock_by_supplier_shop[supplier_id] = converted

    # Only suppliers who supplied in this filtered period.
    matched_ids = set(stock_by_supplier_shop.keys())
    suppliers_for_rows = [
        suppliers_by_id[sid] for sid in matched_ids if sid in suppliers_by_id
    ]

    stock_rows, shop_totals = _supplier_shop_rows(
        suppliers=suppliers_for_rows,
        shops=shops,
        by_supplier_shop=stock_by_supplier_shop,
        kind="stock",
        role=role,
        query=query,
        supplier_priority=supplier_priority if status_key == "all" else None,
    )
    stats = _supplier_ledger_stats(suppliers_for_rows, stock_by_supplier_shop)
    shops_with_balance = sum(
        1 for shop in shops if shop_totals.get(shop.pk, (0, _zero()))[1] > 0
    )

    if status_key == "all":
        lead = (
            f"Stock suppliers who supplied in {period_label}. "
            f"Not paid first, then paid. "
            f"En = stock receipts; Bal = unpaid owed, or paid amount when settled."
        )
        empty = f"No supplier stock receipts for {period_label}."
        footnote = (
            f"Click a supplier to review receipts and pay. "
            f"Only suppliers with supply in {period_label}; "
            f"not paid at the top, then paid."
        )
    elif status_key == "paid":
        lead = (
            f"Fully paid stock suppliers for {period_label}. "
            f"En = paid receipts; Bal = amount paid."
        )
        empty = f"No paid supplier receipts for {period_label}."
        footnote = (
            f"Showing paid receipts for {period_label}, "
            f"sorted by highest paid amount."
        )
    else:
        lead = (
            f"Not-paid stock suppliers for {period_label}. "
            f"En = unpaid receipts; Bal = amount still owed."
        )
        empty = f"No not-paid supplier receipts for {period_label}."
        footnote = (
            f"Showing not-paid receipts for {period_label}, "
            f"sorted by highest outstanding balance."
        )

    summary = _supplier_summary_board(
        stats,
        shop_count=len(shops),
        shops_with_balance=shops_with_balance,
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

    return {
        "headline": "All suppliers",
        "period_label": period_label,
        "lead": lead,
        "status_filter": status_key,
        "status_options": SUPPLIER_STATUS_FILTERS,
        "status_label": status_label,
        "bal_is_paid": amount_is_paid,
        "show_search": True,
        "search_placeholder": "Search suppliers…",
        "search_empty": "No suppliers match that search.",
        "alerts": [],
        "summary_board": summary,
        "insights": [],
        "tables": [
            _table(
                "Suppliers by shop",
                _supplier_pair_columns(shops),
                stock_rows,
                empty=empty,
                footnote=footnote,
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
            )
        ],
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
    valid_categories = set(category_labels)

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

    if category_slug and category_slug in valid_categories:
        return _build_expense_category_suppliers(
            filters,
            category_slug=category_slug,
            category_label=category_labels[category_slug],
            shops=shops,
            period_expenses=period_expenses.filter(category=category_slug),
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

    columns = [
        {"label": "Date", "title": "Date purchased"},
        {"label": "Supplier", "title": "Expense supplier"},
        {"label": "Shop", "title": "Shop"},
        {"label": "Detail", "title": "Expense name"},
        {"label": "Price", "title": "Expense amount"},
    ]

    table_rows = []
    grand_amount = _zero()
    supplier_keys: set[str] = set()
    for expense in expenses:
        amount = Decimal(expense.amount or 0)
        grand_amount += amount
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
        table_rows.append(
            [
                {"label": date_label, "title": date_title},
                supplier_cell,
                shop_name,
                detail,
                _money_cell(
                    amount,
                    tone="warn" if amount > 0 else "neutral",
                    title=f"Price {_money_ksh(amount)}",
                ),
            ]
        )

    if table_rows:
        table_rows.append(
            [
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
            ]
        )

    return {
        "headline": category_label,
        "period_label": period_label,
        "lead": (
            f"Purchases for {category_label.lower()} in {period_label}. "
            "Each row shows the supplier, price, and date purchased."
        ),
        "back_href": back_href,
        "back_label": "Back to expenses",
        "expense_category": category_slug,
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
                    "Click a supplier to open their expense account."
                ),
                shop_grid=False,
                searchable=True,
                table_class="ax-table--expenses",
            )
        ],
    }


def _build_receipts(filters):
    """Receipts hub — Confirm and Return links only."""
    from django.urls import reverse

    from employees.access import role_url_segment

    role = filters["role"]
    query = filters.get("query") or ""
    segment = role_url_segment(role)

    confirm_href = reverse(
        "employees:analytics_confirm_receipts",
        kwargs={"role_segment": segment},
    )
    return_href = reverse(
        "employees:analytics_return_receipts",
        kwargs={"role_segment": segment},
    )
    if query:
        confirm_href = f"{confirm_href}?{query}"
        return_href = f"{return_href}?{query}"

    return {
        "headline": "Receipts",
        "lead": "Choose a receipts tool below.",
        "hide_date_filters": True,
        "alerts": [],
        "metrics": [],
        "trends": [],
        "charts": [],
        "insights": [],
        "tables": [],
        "sections": [
            _overview_section(
                slug="confirm-receipts",
                title="Confirm receipts",
                icon="badge-check",
                href=confirm_href,
                value="Open",
                hint="Confirm or cancel pending sales receipts",
                body=(
                    "Sales only — pending first, then partial returns, cancelled, "
                    "and confirmed."
                ),
            ),
            _overview_section(
                slug="return-receipts",
                title="Return receipt",
                icon="undo-2",
                href=return_href,
                value="Open",
                hint="Return items or cancel sales and credits",
                body="Open a receipt to return stock and update the sale.",
            ),
        ],
    }

def build_return_receipts_page(*, profile, request) -> dict:
    """Return / cancel receipts ledger across shops."""
    from django.urls import reverse

    from employees.access import role_url_segment

    filters = _filters_context(profile, request)
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    filters["receipt_kind"] = (request.GET.get("receipt_kind") or "").strip().lower()
    page = _build_return_receipts(filters)
    page["back_href"] = reverse(
        "employees:analytics_section",
        kwargs={
            "role_segment": role_url_segment(profile.role),
            "section": "receipts",
        },
    )
    page["back_label"] = "Back to receipts"
    return {
        **filters,
        "section_slug": "return-receipts",
        "page": page,
    }


def _build_return_receipts(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    role = filters["role"]
    receipt_kind = (filters.get("receipt_kind") or "").strip().lower()
    if receipt_kind in ("", "all", "any"):
        receipt_kind = ""
    elif receipt_kind in ("sale", "sales"):
        receipt_kind = "sales"
    elif receipt_kind in ("credit", "credits"):
        receipt_kind = "credits"
    elif receipt_kind in ("quotation", "quotations", "quote"):
        receipt_kind = "quotations"
    elif receipt_kind in ("trade_out", "trade-out"):
        receipt_kind = "trade_out"
    elif receipt_kind in ("trade_in", "trade-in"):
        receipt_kind = "trade_in"
    elif receipt_kind in ("stock", "stock-suppliers", "stock_suppliers"):
        receipt_kind = "stock"
    elif receipt_kind in (
        "expense",
        "expenses",
        "expense-suppliers",
        "expense_suppliers",
    ):
        receipt_kind = "expense"
    elif receipt_kind in ("cancelled", "cancel"):
        receipt_kind = "cancelled"
    elif receipt_kind in ("partial-returns", "partial_returns", "partial"):
        receipt_kind = "partial-returns"

    all_receipts = ShopReceipt.objects.filter(
        shop_id__in=shop_ids, created_at__gte=start, created_at__lt=end
    )
    stock_by_shop = _stock_in_receipt_totals_by_shop(
        shop_ids=shop_ids, start=start, end=end
    )
    expense_by_shop = _expense_receipt_totals_by_shop(
        shop_ids=shop_ids, start=start, end=end
    )

    kind_specs = (
        ("sales", "Sales"),
        ("credits", "Credits"),
        ("quotations", "Quotations"),
        ("trade_out", "Trade out"),
        ("trade_in", "Trade in"),
        ("stock", "Stock suppliers"),
        ("expense", "Expense suppliers"),
        ("cancelled", "Cancelled"),
        ("partial-returns", "Partial returns"),
    )
    if receipt_kind:
        filtered_specs = tuple(row for row in kind_specs if row[0] == receipt_kind)
        if filtered_specs:
            kind_specs = filtered_specs

    columns = ["Type"]
    for shop in shops:
        columns.append(
            _shop_col(shop, pair=True, pair_qty="Docs", pair_amt="Amt")
        )
    columns.append(_pair_total_col(pair_qty="Docs", pair_amt="Amt"))

    table_rows = []
    grand_docs = 0
    grand_amount = _zero()
    active_kinds = 0
    for kind_slug, label in kind_specs:
        by_shop: dict[int, tuple[int, Decimal]] = {}
        if kind_slug == "stock":
            by_shop = stock_by_shop
        elif kind_slug == "expense":
            by_shop = expense_by_shop
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
            cells.append(
                _qty_amount_cell(
                    docs,
                    amount,
                    title=f"{docs} docs · {_money_ksh(amount)}",
                )
            )
        cells.append(
            _qty_amount_cell(
                total_docs,
                total_amount,
                title=f"{total_docs} docs · {_money_ksh(total_amount)}",
            )
        )
        table_rows.append(cells)
        grand_docs += total_docs
        grand_amount += total_amount
        if total_docs > 0:
            active_kinds += 1

    merged = []
    include_pos = receipt_kind in (
        "",
        "sales",
        "credits",
        "quotations",
        "trade_out",
        "trade_in",
        "cancelled",
        "partial-returns",
    )
    include_stock = receipt_kind in ("", "stock")
    include_expense = receipt_kind in ("", "expense")

    if include_pos:
        pos_qs = (
            ShopReceipt.objects.filter(
                shop_id__in=shop_ids,
                created_at__gte=start,
                created_at__lt=end,
            )
            .select_related("shop", "created_by", "created_by__user")
            .order_by("-created_at", "-id")
        )
        if receipt_kind in (
            "sales",
            "credits",
            "quotations",
            "trade_out",
            "trade_in",
            "cancelled",
            "partial-returns",
        ):
            pos_qs = pos_qs.filter(_analytics_receipt_kind_filter(receipt_kind))
        for row in pos_qs:
            cashier = _cashier_label(row.created_by)
            client = row.client_name or "Walk-in"
            if row.client_phone:
                client = f"{client} · {row.client_phone}"
            merged.append(
                {
                    "receipt_id": row.pk,
                    "shop_id": row.shop_id,
                    "when_dt": row.created_at,
                    "sort_id": row.pk,
                    "cells": [
                        row.receipt_number,
                        row.get_kind_display(),
                        row.shop.name if row.shop else "—",
                        client,
                        _money_ksh(row.total),
                        row.get_status_display(),
                        timezone.localtime(row.created_at).strftime("%d %b %Y · %H:%M"),
                        cashier or "—",
                    ],
                }
            )
    if include_stock:
        for row in _list_stock_supplier_receipt_rows(
            shop_ids=shop_ids, start=start, end=end
        ):
            merged.append(
                {
                    "when_dt": row["when_dt"],
                    "sort_id": row["sort_id"],
                    "cells": [
                        row["number"],
                        row["kind"],
                        row["shop"],
                        row["client"],
                        row["total"],
                        row["status"],
                        row["when"],
                        row["cashier"],
                    ],
                }
            )
    if include_expense:
        for row in _list_expense_supplier_receipt_rows(
            shop_ids=shop_ids, start=start, end=end
        ):
            merged.append(
                {
                    "when_dt": row["when_dt"],
                    "sort_id": row["sort_id"],
                    "cells": [
                        row["number"],
                        row["kind"],
                        row["shop"],
                        row["client"],
                        row["total"],
                        row["status"],
                        row["when"],
                        row["cashier"],
                    ],
                }
            )
    merged.sort(
        key=lambda item: (item["when_dt"], item["sort_id"]),
        reverse=True,
    )
    receipt_rows = merged
    receipt_total = len(receipt_rows)

    kind_label = (
        dict(kind_specs).get(receipt_kind, "All types")
        if receipt_kind
        else "All types"
    )
    period_label = filters.get("report_period_label") or "selected period"
    list_footnote = (
        f"{receipt_total} receipt{'' if receipt_total == 1 else 's'}"
        + (f" · {kind_label}" if receipt_kind else "")
        + f" for {period_label}."
        + " Sales/credit rows open return; supplier receipts are listed for review."
    )

    from django.urls import reverse

    from employees.access import role_url_segment

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
        "receipt_detail_url_template": detail_template,
        "receipt_return_url_template": return_template,
        "receipt_verify_login_url": verify_url,
        "receipt_kind": receipt_kind or "all",
        "alerts": [],
        "metrics": [
            _metric(
                "Types",
                str(len(kind_specs)),
                hint=f"{active_kinds} with activity",
            ),
            _metric(
                "Documents",
                f"{grand_docs:,}",
                hint=period_label,
            ),
            _metric(
                "Shops",
                str(len(shops)),
                hint="In this view",
            ),
            _metric(
                "Total value",
                _money_ksh(grand_amount),
                hint="All document types" if not receipt_kind else kind_label,
                tone="good" if grand_amount > 0 else "neutral",
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                f"All receipts · {period_label}",
                [
                    "Receipt",
                    "Type",
                    "Shop",
                    "Client / supplier",
                    "Total",
                    "Status",
                    "When",
                    "Cashier",
                ],
                receipt_rows,
                empty="No receipts for selected shops and period.",
                footnote=list_footnote,
                shop_grid=False,
                searchable=True,
            ),
            _table(
                "Documents by shop",
                columns,
                table_rows,
                empty="No receipts for selected shops and period.",
                footnote="Docs = receipt count · Amt = document total.",
            ),
        ],
    }

