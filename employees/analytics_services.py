"""Decision-oriented analytics for the Analytics workspace module."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404

from employees.models import EmployeeProfile, EmployeeStatus
from items.models import (
    Item,
    ShopStock,
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
from shops.services import format_simple_doc_number

ANALYTICS_SECTIONS = (
    {
        "slug": "overview",
        "label": "Overview",
        "icon": "layout-grid",
        "summary": "Result trends — sales, expenses, net, and day open/close balances.",
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
        "summary": "Expected vs actual opening (yesterday close) and closing (system sales).",
    },
    {
        "slug": "sales",
        "label": "Sales",
        "icon": "shopping-bag",
        "summary": "Sales receipts by shop — cash, M-Pesa, stock value, and profit.",
    },
    {
        "slug": "credits",
        "label": "Credits",
        "icon": "credit-card",
        "summary": "Credit receipts by shop — paid, due, stock value, and profit.",
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
        "slug": "quotations",
        "label": "Quotations",
        "icon": "file-text",
        "summary": "Quotations by shop — count and value, plus totals.",
    },
    {
        "slug": "clients",
        "label": "Clients",
        "icon": "contact",
        "summary": "All clients with credits and balance by shop, plus totals.",
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
        "summary": "Stock suppliers by shop — entries and balance, plus totals.",
    },
    {
        "slug": "expenses",
        "label": "Expenses",
        "icon": "wallet",
        "summary": "Expense suppliers by shop — entries and balance, plus totals.",
    },
    {
        "slug": "receipts",
        "label": "Receipts",
        "icon": "receipt",
        "summary": "Receipt documents by shop — count and value per shop, plus totals.",
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
        "credits",
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
    qty_label = str(int(qty or 0))
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
) -> dict:
    """Apply a payment to an account, clearing receipts oldest → newest (FIFO)."""
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

            receipts = list(
                ShopReceipt.objects.select_for_update()
                .filter(
                    client_id=client.pk,
                    kind=ShopReceiptKind.CREDIT,
                    shop_id__in=shop_ids,
                )
                .exclude(status=ShopReceiptStatus.CANCELLED)
                .order_by("created_at", "pk")
            )
            balance_before = sum(
                (_due_amount(row.total, row.amount_paid) for row in receipts),
                _zero(),
            )
            if balance_before <= 0:
                raise ValidationError("This account has no balance due.")
            if pay_amount > balance_before:
                raise ValidationError(
                    f"Amount exceeds balance due ({_money_ksh(balance_before)})."
                )
            for receipt in receipts:
                if remaining <= 0:
                    break
                due = _due_amount(receipt.total, receipt.amount_paid)
                if due <= 0:
                    continue
                apply = min(remaining, due)
                receipt.amount_paid = (
                    Decimal(receipt.amount_paid or 0) + apply
                ).quantize(Decimal("0.01"))
                update_fields = ["amount_paid"]
                if method == "mpesa" and mpesa_receipt_number and not receipt.mpesa_receipt_number:
                    receipt.mpesa_receipt_number = mpesa_receipt_number
                    update_fields.append("mpesa_receipt_number")
                receipt.save(update_fields=update_fields)
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

            balance_after = sum(
                (_due_amount(row.total, row.amount_paid) for row in receipts),
                _zero(),
            )
            pay_label = "M-Pesa" if method == "mpesa" else "Cash"
            ref_bit = (
                f" · Ref {mpesa_receipt_number}" if mpesa_receipt_number else ""
            )
            return {
                "ok": True,
                "kind": kind,
                "account_id": client.pk,
                "cleared": cleared,
                "payment_method": method,
                "mpesa_receipt_number": mpesa_receipt_number,
                "account_balance": _money_ksh(balance_after),
                "account_balance_raw": str(balance_after),
                "message": (
                    f"{pay_label} payment of {_money_ksh(pay_amount)} applied "
                    f"oldest → newest across {cleared} receipt"
                    f"{'' if cleared == 1 else 's'}{ref_bit}."
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
            qty = int(line.quantity or 0)
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

        receipt.amount_paid = (
            Decimal(receipt.amount_paid or 0) + pay_amount
        ).quantize(Decimal("0.01"))
        update_fields = ["amount_paid"]
        if method == "mpesa" and mpesa_receipt_number and not receipt.mpesa_receipt_number:
            receipt.mpesa_receipt_number = mpesa_receipt_number
            update_fields.append("mpesa_receipt_number")
        receipt.save(update_fields=update_fields)

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
        return {
            "ok": True,
            "kind": "credit",
            "receipt_id": receipt.pk,
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
                f"{receipt.receipt_number}{ref_bit}."
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
            qty = int(line.quantity or 0)
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
            qty = int(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            info["total"] += (unit * qty).quantize(Decimal("0.01"))
        for info in by_movement.values():
            balance_after += _due_amount(info["total"], info["paid"])
        number = format_simple_doc_number("I", movement.pk)
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


def _alert(level, title, detail, action=""):
    return {
        "level": level,
        "title": title,
        "detail": detail,
        "action": action,
    }


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
    stock_amount,
    profit_amount,
    active_shops: int,
    shop_count: int,
) -> dict:
    total = Decimal(total_amount or 0)
    cash = Decimal(cash_amount or 0)
    mpesa = Decimal(mpesa_amount or 0)
    stock = Decimal(stock_amount or 0)
    profit = Decimal(profit_amount or 0)
    cash_share = (
        f"{((cash / total) * Decimal('100')).quantize(Decimal('0.1'))}% of total"
        if total > 0
        else "No sales yet"
    )
    mpesa_share = (
        f"{((mpesa / total) * Decimal('100')).quantize(Decimal('0.1'))}% of total"
        if total > 0
        else "No sales yet"
    )
    margin = (
        f"{((profit / total) * Decimal('100')).quantize(Decimal('0.1'))}%"
        if total > 0
        else "—"
    )
    shops = active_shops or shop_count
    return {
        "hero": {
            "label": "Total sales",
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
) -> dict:
    due = Decimal(balance or 0) if not isinstance(balance, Decimal) else balance
    paid_total = Decimal(total_paid or 0) if not isinstance(total_paid, Decimal) else total_paid
    open_n = int(open_count or 0)
    receipts = int(receipt_count or 0)
    shops = int(shop_count or 0)
    return {
        "hero": {
            "label": "Outstanding balance",
            "value": _money_ksh(due) if isinstance(balance, Decimal) else str(balance),
            "hint": (
                f"{open_n} open credit{'s' if open_n != 1 else ''} · "
                f"{receipts} receipt{'s' if receipts != 1 else ''}"
            ),
            "tone": "warn" if due > 0 else "good",
        },
        "tiles": [
            {
                "label": "Open credits",
                "value": str(open_n),
                "hint": "Still owing",
                "icon": "credit-card",
                "tone": "warn" if open_n else "good",
            },
            {
                "label": "Paid so far",
                "value": _money_ksh(paid_total),
                "hint": "Across all receipts",
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
                f"{int(total_units or 0)} units · {item_count} item"
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
                0, int(row["quantity"] or 0) - int(row["returned_quantity"] or 0)
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
            qty = int(row["quantity"] or 0)
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


def _date_filter_context(request=None, *, data=None, allow_all_time=False) -> dict:
    """Parse day / period / month / year filters. All-time when range is missing."""
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
    if allow_all_time and range_raw not in ("day", "period", "month", "year"):
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
        request if request is not None and not allow_all_time and data is None else _QueryRequest()
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


def _filters_context(profile, request, *, allow_all_time=False):
    date_filter = _date_filter_context(request, allow_all_time=allow_all_time)
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
    filters = _filters_context(
        profile,
        request,
        allow_all_time=section["slug"] == "suppliers",
    )
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    builders = {
        "overview": _build_overview,
        "revenue": _build_revenue,
        "balances": _build_balances,
        "sales": _build_sales,
        "items": _build_items,
        "stock": _build_stock,
        "quotations": _build_quotations,
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


def build_analytics_receipts_list(*, profile, request, kind: str) -> dict:
    """Filterable receipt list for one analytics document type across shops."""
    spec = get_analytics_receipt_kind(kind)
    filters = _filters_context(profile, request)
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    search = (request.GET.get("q") or "").strip()

    qs = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .filter(_analytics_receipt_kind_filter(spec["slug"]))
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

    total_count = qs.count()
    limit = 500
    receipts = list(qs[:limit])
    rows = []
    for row in receipts:
        cashier = ""
        if row.created_by and row.created_by.user:
            cashier = (
                row.created_by.user.get_full_name()
                or row.created_by.employee_id
                or row.created_by.user.username
            )
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
                "when": row.created_at.strftime("%d %b %Y · %H:%M"),
                "cashier": cashier or "—",
            }
        )

    return {
        **filters,
        "kind": spec,
        "search": search,
        "rows": rows,
        "total_count": total_count,
        "returned_count": len(rows),
        "truncated": total_count > limit,
        "page": {
            "headline": spec["label"],
            "lead": "",
        },
    }


def client_credit_account_url(role, client_id, *, query: str = "") -> str:
    from django.urls import reverse

    from employees.access import role_url_segment

    href = reverse(
        "employees:analytics_client_account",
        kwargs={
            "role_segment": role_url_segment(role),
            "client_id": int(client_id),
        },
    )
    if query:
        return f"{href}?{query}"
    return href


def build_client_credit_account(*, profile, client_id: int) -> dict:
    """Full credit ledger and outstanding balance for one client."""
    shop_ids = [shop.pk for shop in actionable_shops_for_profile(profile)]
    client = Client.objects.filter(pk=client_id).first()
    if client is None or not shop_ids:
        raise Http404("Client not found.")
    if not (
        ShopReceipt.objects.filter(client_id=client.pk, shop_id__in=shop_ids)
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exists()
    ):
        raise Http404("Client not found.")

    receipts = list(
        ShopReceipt.objects.filter(
            client_id=client.pk,
            kind=ShopReceiptKind.CREDIT,
            shop_id__in=shop_ids,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
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
            qty = int(line.quantity or 0) - int(line.returned_quantity or 0)
            if qty <= 0:
                continue
            unit = Decimal(line.unit_price or 0)
            lines.append(
                {
                    "name": line.item_name or "Item",
                    "qty": qty,
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
                "when": row.created_at,
                "pay_by": pay_by,
                "pay_by_raw": pay_by_raw,
                "pay_by_overdue": pay_by_overdue,
                "cashier": cashier or "—",
                "item_count": len(lines),
                "lines": lines,
            }
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
        ),
        "rows": rows,
        "ledger_title": "Credit receipts",
        "empty_message": "No credit receipts for this client in your shops.",
        "account_kind": "credit",
        "account_id": client.pk,
        "can_pay": balance > 0,
    }


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
            qty = int(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            line_total = (unit * qty).quantize(Decimal("0.01"))
            receipt_total += line_total
            receipt_lines.append(
                {
                    "name": line.item.name if line.item else "Item",
                    "qty": qty,
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
    expenses = Expense.objects.filter(
        shop_id__in=shop_ids, created_at__gte=start, created_at__lt=end
    )
    return sales, prev_sales, credits, quotes, expenses


def _day_balance_data(filters) -> dict:
    """Opening/closing day balances and till variance for the active period."""
    from collections import defaultdict

    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
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
        "expenses": _zero(),
        "expected_cash": _zero(),
        "expected_mpesa": _zero(),
        "cash_variance": _zero(),
        "mpesa_variance": _zero(),
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

    sales_by_shop: dict[int, list] = defaultdict(list)
    expenses_by_shop: dict[int, list] = defaultdict(list)
    if closed_sessions:
        min_opened = min(session.opened_at for session in closed_sessions)
        max_closed = max(session.closed_at for session in closed_sessions)
        for row in (
            ShopReceipt.objects.filter(
                shop_id__in=shop_ids,
                kind=ShopReceiptKind.SALE,
                created_at__gte=min_opened,
                created_at__lt=max_closed,
            )
            .exclude(status=ShopReceiptStatus.CANCELLED)
            .values("shop_id", "created_at", "cash_amount", "mpesa_amount")
        ):
            sales_by_shop[row["shop_id"]].append(row)
        for row in Expense.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=min_opened,
            created_at__lt=max_closed,
        ).values("shop_id", "created_at", "amount"):
            expenses_by_shop[row["shop_id"]].append(row)

    session_rows = []
    for session in closed_sessions:
        entry = by_shop[session.shop_id]
        entry["close_sessions"] += 1
        entry["closing_cash"] += Decimal(session.closing_cash or 0)
        entry["closing_mpesa"] += Decimal(session.closing_mpesa or 0)
        entry["closing_credit"] += Decimal(session.closing_credit or 0)
        entry["session_open_cash"] += Decimal(session.opening_cash or 0)
        entry["session_open_mpesa"] += Decimal(session.opening_mpesa or 0)

        cash_sales = _zero()
        mpesa_sales = _zero()
        for sale in sales_by_shop.get(session.shop_id, []):
            when = sale["created_at"]
            if session.opened_at <= when < session.closed_at:
                cash_sales += Decimal(sale["cash_amount"] or 0)
                mpesa_sales += Decimal(sale["mpesa_amount"] or 0)

        expense_total = _zero()
        for expense in expenses_by_shop.get(session.shop_id, []):
            when = expense["created_at"]
            if session.opened_at <= when < session.closed_at:
                expense_total += Decimal(expense["amount"] or 0)

        expected_cash = (
            Decimal(session.opening_cash or 0) + cash_sales - expense_total
        )
        expected_mpesa = Decimal(session.opening_mpesa or 0) + mpesa_sales
        closing_cash = Decimal(session.closing_cash or 0)
        closing_mpesa = Decimal(session.closing_mpesa or 0)
        cash_variance = closing_cash - expected_cash
        mpesa_variance = closing_mpesa - expected_mpesa

        entry["cash_sales"] += cash_sales
        entry["mpesa_sales"] += mpesa_sales
        entry["expenses"] += expense_total
        entry["expected_cash"] += expected_cash
        entry["expected_mpesa"] += expected_mpesa
        entry["cash_variance"] += cash_variance
        entry["mpesa_variance"] += mpesa_variance

        opening = opening_by_session_id.get(session.pk, {})
        session_rows.append(
            {
                "shop_id": session.shop_id,
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
                "expenses": expense_total,
                "expected_cash": expected_cash,
                "expected_mpesa": expected_mpesa,
                "cash_variance": cash_variance,
                "mpesa_variance": mpesa_variance,
            }
        )

    totals = dict(empty_shop)
    for entry in by_shop.values():
        for key, value in entry.items():
            totals[key] += value

    return {
        "by_shop": dict(by_shop),
        "totals": totals,
        "session_rows": session_rows,
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
    """Simple result trends: big deltas, sales timeline, shop movement."""
    from datetime import timedelta

    from django.utils import timezone

    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    prev_start, prev_end = filters["prev_start"], filters["prev_end"]
    sales, prev_sales, credits, _quotes, expenses = _common_receipt_sets(filters)
    day_balances = _day_balance_data(filters)

    sales_total = _sum_total(sales)
    prev_sales_total = _sum_total(prev_sales)
    sales_subtotal = Decimal(
        sales.aggregate(amount=Coalesce(Sum("subtotal"), _zero()))["amount"] or 0
    )
    credit_subtotal = Decimal(
        credits.aggregate(amount=Coalesce(Sum("subtotal"), _zero()))["amount"] or 0
    )
    net_sales = sales_subtotal + credit_subtotal
    cogs_by_shop = _cogs_by_shop_for_period(shop_ids=shop_ids, start=start, end=end)
    shrinkage_by_shop = _shrinkage_by_shop_for_period(
        shop_ids=shop_ids, start=start, end=end
    )
    opex_by_shop, drawings_by_shop = _opex_and_drawings_by_shop(expenses)
    cogs_total = sum((amt for _d, amt in cogs_by_shop.values()), _zero())
    shrinkage_total = sum((amt for _d, amt in shrinkage_by_shop.values()), _zero())
    opex_total = sum((amt for _d, amt in opex_by_shop.values()), _zero())
    drawings_total = sum((amt for _d, amt in drawings_by_shop.values()), _zero())
    gross_total = net_sales - cogs_total
    operating_total = gross_total - opex_total - shrinkage_total

    prev_sales_qs = _receipts_qs(
        shop_ids=shop_ids,
        start=prev_start,
        end=prev_end,
        kinds=[ShopReceiptKind.SALE],
    )
    prev_credits_qs = _receipts_qs(
        shop_ids=shop_ids,
        start=prev_start,
        end=prev_end,
        kinds=[ShopReceiptKind.CREDIT],
    )
    prev_net_sales = Decimal(
        prev_sales_qs.aggregate(amount=Coalesce(Sum("subtotal"), _zero()))["amount"]
        or 0
    ) + Decimal(
        prev_credits_qs.aggregate(amount=Coalesce(Sum("subtotal"), _zero()))["amount"]
        or 0
    )
    prev_cogs = sum(
        (
            amt
            for _d, amt in _cogs_by_shop_for_period(
                shop_ids=shop_ids, start=prev_start, end=prev_end
            ).values()
        ),
        _zero(),
    )
    prev_shrinkage = sum(
        (
            amt
            for _d, amt in _shrinkage_by_shop_for_period(
                shop_ids=shop_ids, start=prev_start, end=prev_end
            ).values()
        ),
        _zero(),
    )
    prev_expenses = Expense.objects.filter(
        shop_id__in=shop_ids,
        created_at__gte=prev_start,
        created_at__lt=prev_end,
    )
    prev_opex_by_shop, _prev_drawings = _opex_and_drawings_by_shop(prev_expenses)
    prev_opex = sum((amt for _d, amt in prev_opex_by_shop.values()), _zero())
    prev_gross = prev_net_sales - prev_cogs
    prev_operating = prev_gross - prev_opex - prev_shrinkage

    credit_total = _sum_total(credits)
    credit_payments = Decimal(
        credits.aggregate(paid=Coalesce(Sum("amount_paid"), _zero()))["paid"] or 0
    )
    credit_due = credit_total - credit_payments

    sales_hint, sales_tone = _trend_hint(sales_total, prev_sales_total)
    expense_hint, expense_tone = _trend_hint(opex_total, prev_opex, invert=True)
    operating_hint, operating_tone = _trend_hint(operating_total, prev_operating)
    gross_hint, gross_tone = _trend_hint(gross_total, prev_gross)

    opening_cash = day_balances["totals"]["opening_cash"]
    closing_cash = day_balances["totals"]["closing_cash"]
    cash_variance = day_balances["totals"]["cash_variance"]
    open_days = int(day_balances["totals"]["open_sessions"] or 0)
    close_days = int(day_balances["totals"]["close_sessions"] or 0)

    duration = end - start
    if duration <= timedelta(hours=36):
        step = timedelta(hours=1)
        label_fmt = "%H:%M"
        bucket_kind = "hour"
    elif duration <= timedelta(days=62):
        step = timedelta(days=1)
        label_fmt = "%d %b"
        bucket_kind = "day"
    else:
        step = None
        label_fmt = "%b %Y"
        bucket_kind = "month"

    def _point_label(moment):
        local = timezone.localtime(moment)
        if bucket_kind == "hour":
            local = local.replace(minute=0, second=0, microsecond=0)
        elif bucket_kind == "day":
            local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return local.strftime(label_fmt)

    def _label_map(rows):
        mapped: dict[str, Decimal] = {}
        for created_at, amount in rows:
            if created_at is None:
                continue
            key = _point_label(created_at)
            mapped[key] = mapped.get(key, _zero()) + Decimal(amount or 0)
        return mapped

    sales_by_label = _label_map(sales.values_list("created_at", "total"))
    expense_by_label = _label_map(expenses.values_list("created_at", "amount"))
    credits_by_label = _label_map(credits.values_list("created_at", "total"))
    payments_by_label = _label_map(
        credits.values_list("created_at", "amount_paid")
    )

    labels: list[str] = []
    if bucket_kind == "month":
        cursor = timezone.localtime(start).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end_local = timezone.localtime(end)
        while cursor < end_local:
            labels.append(cursor.strftime(label_fmt))
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = cursor.replace(year=year, month=month)
    else:
        cursor = timezone.localtime(start)
        if bucket_kind == "day":
            cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        elif bucket_kind == "hour":
            cursor = cursor.replace(minute=0, second=0, microsecond=0)
        end_local = timezone.localtime(end)
        while cursor < end_local:
            labels.append(cursor.strftime(label_fmt))
            cursor = cursor + step

    if len(labels) > 48:
        stride = max(1, len(labels) // 48)
        labels = labels[::stride]

    timeline = [
        (
            label,
            sales_by_label.get(label, _zero()),
            expense_by_label.get(label, _zero()),
        )
        for label in labels
    ]
    line_chart = _trend_line_chart(timeline)
    credit_timeline = [
        (
            label,
            credits_by_label.get(label, _zero()),
            payments_by_label.get(label, _zero()),
        )
        for label in labels
    ]
    credit_line_chart = _trend_line_chart(credit_timeline)

    variance_hint = (
        "balanced"
        if cash_variance == 0
        else f"{'over' if cash_variance > 0 else 'short'} vs expected"
    )
    margin_pct = (
        ((gross_total / net_sales) * Decimal("100")).quantize(Decimal("0.1"))
        if net_sales > 0
        else _zero()
    )
    trends = [
        {
            "label": "Sales",
            "value": _money_ksh(sales_total),
            "delta": sales_hint,
            "tone": sales_tone,
            "spark": _sparkline([row[1] for row in timeline]),
        },
        {
            "label": "Gross profit",
            "value": _money_ksh(gross_total),
            "delta": f"{gross_hint} · margin {margin_pct}%",
            "tone": gross_tone,
            "spark": _sparkline([row[1] for row in timeline]),
        },
        {
            "label": "OpEx",
            "value": _money_ksh(opex_total),
            "delta": expense_hint,
            "tone": expense_tone,
            "spark": _sparkline([row[2] for row in timeline]),
        },
        {
            "label": "Operating profit",
            "value": _money_ksh(operating_total),
            "delta": operating_hint,
            "tone": operating_tone,
            "spark": _sparkline([row[1] - row[2] for row in timeline]),
        },
        {
            "label": "Opening cash",
            "value": _money_ksh(opening_cash),
            "delta": f"{open_days} day{'s' if open_days != 1 else ''} opened",
            "tone": "neutral",
            "spark": _sparkline([opening_cash, closing_cash]),
        },
        {
            "label": "Closing cash",
            "value": _money_ksh(closing_cash),
            "delta": f"{close_days} day{'s' if close_days != 1 else ''} closed",
            "tone": "neutral",
            "spark": _sparkline([opening_cash, closing_cash]),
        },
        {
            "label": "Cash variance",
            "value": _money_ksh(cash_variance),
            "delta": variance_hint,
            "tone": _variance_tone(cash_variance),
            "spark": _sparkline(
                [day_balances["totals"]["expected_cash"], closing_cash]
            ),
        },
    ]

    sales_by_shop = {
        row["shop_id"]: Decimal(row["amount"] or 0)
        for row in sales.values("shop_id").annotate(
            amount=Coalesce(Sum("total"), _zero())
        )
    }
    prev_sales_by_shop = {
        row["shop_id"]: Decimal(row["amount"] or 0)
        for row in prev_sales.values("shop_id").annotate(
            amount=Coalesce(Sum("total"), _zero())
        )
    }

    balance_by_shop = day_balances["by_shop"]
    shop_chart_rows = []
    for shop in shops:
        current = sales_by_shop.get(shop.pk, _zero())
        prior = prev_sales_by_shop.get(shop.pk, _zero())
        balances = balance_by_shop.get(shop.pk, {})
        opening = Decimal(balances.get("opening_cash") or 0)
        closing = Decimal(balances.get("closing_cash") or 0)
        if not current and not prior and not opening and not closing:
            continue
        shop_chart_rows.append(
            {
                "label": shop.name,
                "opening": opening,
                "sales": current,
                "closing": closing,
                "prior": prior,
                "sort": max(abs(current), abs(prior), abs(opening), abs(closing)),
            }
        )
    shop_chart_rows.sort(key=lambda row: (-row["sort"], row["label"].lower()))
    for row in shop_chart_rows:
        del row["sort"]
    shop_chart = _shop_movement_chart(shop_chart_rows)

    alerts = []
    if shrinkage_total > 0:
        alerts.append(
            _alert(
                "warning",
                "Shrinkage in period",
                f"Waste/display stock-outs cost {_money_ksh(shrinkage_total)} "
                f"(included in operating profit).",
                "Review stock-out reasons on the Stock page.",
            )
        )
    if drawings_total > 0:
        alerts.append(
            _alert(
                "info",
                "Owner drawings",
                f"{_money_ksh(drawings_total)} recorded — equity, not operating expense.",
            )
        )

    return {
        "headline": "Trends",
        "lead": (
            "Operating profit = net sales (ex-tax) − COGS − OpEx − shrinkage. "
            "Cash variance is till control, not profit."
        ),
        "alerts": alerts,
        "metrics": [
            _metric(
                "Net sales",
                _money_ksh(net_sales),
                hint="Sales + credits, ex-tax",
            ),
            _metric("COGS", _money_ksh(cogs_total), hint="Cost of goods sold"),
            _metric(
                "Gross profit",
                _money_ksh(gross_total),
                hint=f"Margin {margin_pct}%",
                tone="success" if gross_total >= 0 else "danger",
            ),
            _metric(
                "Operating profit",
                _money_ksh(operating_total),
                hint="After OpEx and shrinkage",
                tone="success" if operating_total >= 0 else "danger",
            ),
        ],
        "trends": trends,
        "charts": [
            {
                "kind": "line",
                "title": "Sales vs expenses",
                "subtitle": "Through this period",
                "line": line_chart,
                "legend_a": "Sales",
                "legend_b": "Expenses",
                "empty": "No activity in this period.",
            },
            {
                "kind": "line",
                "title": "Credits vs payments",
                "subtitle": (
                    f"Issued {_money_ksh(credit_total)} · "
                    f"paid {_money_ksh(credit_payments)} · "
                    f"due {_money_ksh(credit_due)}"
                ),
                "line": credit_line_chart,
                "legend_a": "Credits",
                "legend_b": "Payments",
                "series_b": "payments",
                "empty": "No credit activity in this period.",
            },
            {
                "kind": "shop_bars",
                "title": "Shop movement",
                "subtitle": "Open · sales · close · prior",
                "bars_chart": shop_chart,
                "empty": "No shop sales or day balances to chart.",
            },
        ],
        "sections": [],
        "insights": [],
        "tables": [],
    }


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
            total_qty += int(qty or 0)
            total_amount += Decimal(amount or 0)
            cells.append(
                _qty_amount_cell(
                    qty,
                    amount,
                    title=f"{int(qty or 0)} · {_money_ksh(amount)}",
                )
            )
        cells.append(
            _qty_amount_cell(
                total_qty,
                total_amount,
                title=f"{total_qty} · {_money_ksh(total_amount)}",
            )
        )
        rows.append(cells)
    return rows


def _build_revenue(filters):
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(filters["active_shop_ids"])]
    shop_ids = [shop.pk for shop in shops]
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
    opex_by_shop, _drawings_by_shop = _opex_and_drawings_by_shop(expenses)

    sales_by_shop: dict[int, tuple[int, Decimal]] = {}
    credits_by_shop: dict[int, tuple[int, Decimal]] = {}
    total_by_shop: dict[int, tuple[int, Decimal]] = {}
    stock_by_shop: dict[int, Decimal] = {}
    expenses_by_shop: dict[int, Decimal] = {}
    profit_by_shop: dict[int, Decimal] = {}
    for shop in shops:
        row = trading.get(shop.pk) or {}
        sale_docs = sale_docs_by_shop.get(shop.pk, 0)
        credit_docs = credit_docs_by_shop.get(shop.pk, 0)
        sale_amt = Decimal(row.get("sale_value") or 0)
        credit_amt = Decimal(row.get("credit_value") or 0)
        stock = Decimal(row.get("cogs") or 0)
        opex = opex_by_shop.get(shop.pk, (0, _zero()))[1]
        sales_by_shop[shop.pk] = (sale_docs, sale_amt)
        credits_by_shop[shop.pk] = (credit_docs, credit_amt)
        total_by_shop[shop.pk] = (sale_docs + credit_docs, sale_amt + credit_amt)
        stock_by_shop[shop.pk] = stock
        expenses_by_shop[shop.pk] = opex
        profit_by_shop[shop.pk] = sale_amt + credit_amt - stock - opex

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
                "title": "Operating expenses (excludes owner drawings)",
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
                    title=f"{int(qty or 0)} · {_money_ksh(amount)}",
                )
            )
        stock = stock_by_shop.get(shop.pk, _zero())
        opex = expenses_by_shop.get(shop.pk, _zero())
        profit = profit_by_shop.get(shop.pk, _zero())
        revenue = total_by_shop.get(shop.pk, (0, _zero()))[1]
        margin = (
            ((profit / revenue) * Decimal("100")).quantize(Decimal("0.1"))
            if revenue > 0
            else _zero()
        )
        cells.append(_money_cell(stock, title=f"Stock value {_money_ksh(stock)}"))
        cells.append(_money_cell(opex, title=f"Expenses {_money_ksh(opex)}"))
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
    for shop in shops_sorted:
        sale_docs, sale_amt = sales_by_shop.get(shop.pk, (0, _zero()))
        credit_docs, credit_amt = credits_by_shop.get(shop.pk, (0, _zero()))
        total_sale_docs += int(sale_docs or 0)
        total_sale_amt += Decimal(sale_amt or 0)
        total_credit_docs += int(credit_docs or 0)
        total_credit_amt += Decimal(credit_amt or 0)
    total_docs = total_sale_docs + total_credit_docs
    total_revenue = total_sale_amt + total_credit_amt
    total_stock = sum((stock_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero())
    total_expenses = sum(
        (expenses_by_shop.get(shop.pk, _zero()) for shop in shops_sorted), _zero()
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
                _money_cell(total_expenses, title=f"Expenses {_money_ksh(total_expenses)}"),
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

    return {
        "headline": "Revenue",
        "lead": "Sales and credits by shop, with stock, expenses, and profit.",
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
                hint="Excludes owner drawings",
            ),
            _metric(
                "Profit",
                _money_ksh(total_profit),
                hint=f"Margin {margin_pct}%",
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
                    "(excluding owner drawings). "
                    "Profit = total − stock − expenses."
                ),
            )
        ],
    }


def _build_balances(filters):
    """Day open/close balances vs expected till from sales and expenses."""
    from django.utils import timezone

    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    day_balances = _day_balance_data(filters)
    by_shop = day_balances["by_shop"]
    totals = day_balances["totals"]

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
            hint="Open + cash sales − expenses",
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
    metrics = cash_metrics + mpesa_metrics

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -Decimal(by_shop.get(shop.pk, {}).get("closing_cash") or 0),
            shop.name.lower(),
        ),
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
        {"label": "Open cash", "pair": True, "pair_qty": "Days", "pair_amt": "Amt", "band": "cash-open"},
        {"label": "Open var", "pair": True, "pair_qty": "Days", "pair_amt": "Amt", "band": "cash-open"},
        {
            "label": "Exp. close cash",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "cash-close",
            "band_start": True,
        },
        {"label": "Close cash", "pair": True, "pair_qty": "Days", "pair_amt": "Amt", "band": "cash-close"},
        {"label": "Close var", "pair": True, "pair_qty": "Days", "pair_amt": "Amt", "band": "cash-close"},
        {
            "label": "Exp. open M-Pesa",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-open",
            "band_start": True,
        },
        {"label": "Open M-Pesa", "pair": True, "pair_qty": "Days", "pair_amt": "Amt", "band": "mpesa-open"},
        {
            "label": "Open M-Pesa var",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-open",
        },
        {
            "label": "Exp. close M-Pesa",
            "pair": True,
            "pair_qty": "Days",
            "pair_amt": "Amt",
            "band": "mpesa-close",
            "band_start": True,
        },
        {"label": "Close M-Pesa", "pair": True, "pair_qty": "Days", "pair_amt": "Amt", "band": "mpesa-close"},
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
    for shop in shops_sorted:
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
                _pair(shop.pk, "open_sessions", "opening_cash"),
                _pair(
                    shop.pk,
                    "open_sessions",
                    "opening_cash_variance",
                    variance=True,
                ),
                _pair(shop.pk, "close_sessions", "expected_cash"),
                _pair(shop.pk, "close_sessions", "closing_cash"),
                _pair(
                    shop.pk,
                    "close_sessions",
                    "cash_variance",
                    variance=True,
                ),
                _pair(shop.pk, "open_sessions", "expected_opening_mpesa"),
                _pair(shop.pk, "open_sessions", "opening_mpesa"),
                _pair(
                    shop.pk,
                    "open_sessions",
                    "opening_mpesa_variance",
                    variance=True,
                ),
                _pair(shop.pk, "close_sessions", "expected_mpesa"),
                _pair(shop.pk, "close_sessions", "closing_mpesa"),
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
                    totals["open_sessions"], totals["opening_cash"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"],
                    totals["opening_cash_variance"],
                    tone=_variance_tone(totals["opening_cash_variance"]),
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["expected_cash"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["closing_cash"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"],
                    totals["cash_variance"],
                    tone=_variance_tone(totals["cash_variance"]),
                ),
                _qty_amount_cell(
                    totals["open_sessions"], totals["expected_opening_mpesa"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"], totals["opening_mpesa"]
                ),
                _qty_amount_cell(
                    totals["open_sessions"],
                    totals["opening_mpesa_variance"],
                    tone=_variance_tone(totals["opening_mpesa_variance"]),
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["expected_mpesa"]
                ),
                _qty_amount_cell(
                    totals["close_sessions"], totals["closing_mpesa"]
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
        "Open cash",
        "Open var",
        "Cash sales",
        "Expenses",
        "Exp. close cash",
        "Close cash",
        "Close var",
        "Exp. open M-Pesa",
        "Open M-Pesa",
        "Open M-Pesa var",
        "M-Pesa sales",
        "Exp. close M-Pesa",
        "Close M-Pesa",
        "Close M-Pesa var",
    ]
    session_rows = []
    for row in sorted(
        day_balances["session_rows"],
        key=lambda item: item["closed_at"] or item["opened_at"],
        reverse=True,
    ):
        shop = shops_by_id.get(row["shop_id"])
        shop_name = shop.name if shop else f"Shop {row['shop_id']}"
        opened = timezone.localtime(row["opened_at"]).strftime("%d %b %H:%M")
        closed = timezone.localtime(row["closed_at"]).strftime("%d %b %H:%M")
        cash_var = row["cash_variance"]
        mpesa_var = row["mpesa_variance"]
        open_cash_var = row["opening_cash_variance"]
        open_mpesa_var = row["opening_mpesa_variance"]
        session_rows.append(
            [
                shop_name,
                opened,
                closed,
                _money_ksh(row["expected_opening_cash"]),
                _money_ksh(row["opening_cash"]),
                {
                    "label": _money_ksh(open_cash_var),
                    "tone": _variance_tone(open_cash_var),
                },
                _money_ksh(row["cash_sales"]),
                _money_ksh(row["expenses"]),
                _money_ksh(row["expected_cash"]),
                _money_ksh(row["closing_cash"]),
                {
                    "label": _money_ksh(cash_var),
                    "tone": _variance_tone(cash_var),
                },
                _money_ksh(row["expected_opening_mpesa"]),
                _money_ksh(row["opening_mpesa"]),
                {
                    "label": _money_ksh(open_mpesa_var),
                    "tone": _variance_tone(open_mpesa_var),
                },
                _money_ksh(row["mpesa_sales"]),
                _money_ksh(row["expected_mpesa"]),
                _money_ksh(row["closing_mpesa"]),
                {
                    "label": _money_ksh(mpesa_var),
                    "tone": _variance_tone(mpesa_var),
                },
            ]
        )

    return {
        "headline": "Day balances",
        "lead": (
            "Expected opening uses yesterday's closing till. Expected closing uses "
            "system sales and expenses. Variances compare actual recorded balances."
        ),
        "alerts": [],
        "metrics": metrics,
        "metric_groups": [
            _till_metric_group(
                label="Cash till",
                icon="banknote",
                tone="cash",
                metrics=cash_metrics,
            ),
            _till_metric_group(
                label="M-Pesa till",
                icon="smartphone",
                tone="mpesa",
                metrics=mpesa_metrics,
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                "Balances by shop",
                summary_columns,
                summary_rows,
                empty="No shop day open/close records for this period.",
                footnote=(
                    "Expected opening = previous day's closing balance. "
                    "Expected closing cash = opening + cash sales − expenses. "
                    "Expected closing M-Pesa = opening + M-Pesa sales. "
                    "Variance = actual − expected."
                ),
                shop_grid=True,
            ),
            _table(
                "Closed day sessions",
                session_columns,
                session_rows,
                empty="No closed shop days in this period.",
                table_class="ax-table--sessions",
            ),
        ],
    }


def _build_sales(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    sales, *_rest = _common_receipt_sets(filters)

    total_by_shop: dict[int, tuple[int, Decimal]] = {}
    cash_by_shop: dict[int, tuple[int, Decimal]] = {}
    mpesa_by_shop: dict[int, tuple[int, Decimal]] = {}
    for row in sales.values("shop_id").annotate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), _zero()),
        cash=Coalesce(Sum("cash_amount"), _zero()),
        mpesa=Coalesce(Sum("mpesa_amount"), _zero()),
    ):
        shop_id = row["shop_id"]
        total_by_shop[shop_id] = (int(row["docs"] or 0), Decimal(row["amount"] or 0))
        cash_by_shop[shop_id] = (0, Decimal(row["cash"] or 0))
        mpesa_by_shop[shop_id] = (0, Decimal(row["mpesa"] or 0))

    trading = _trading_by_shop_for_period(
        shop_ids=shop_ids,
        start=start,
        end=end,
        kinds=[ShopReceiptKind.SALE],
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

    metric_maps = [
        ("Cash", cash_by_shop, "cash"),
        ("M-Pesa", mpesa_by_shop, "mpesa"),
        ("Total", total_by_shop, "total"),
    ]

    shops_sorted = sorted(
        shops,
        key=lambda shop: (
            -total_by_shop.get(shop.pk, (0, _zero()))[1],
            shop.name.lower(),
        ),
    )

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
                "band_start": True,
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
                "label": "Profit",
                "title": "Selling value − stock (ex-tax, this period)",
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
                    title=f"{int(qty or 0)} · {_money_ksh(amount)}",
                )
            )
        stock = stock_by_shop.get(shop.pk, _zero())
        profit = profit_by_shop.get(shop.pk, _zero())
        revenue = selling_by_shop.get(shop.pk, _zero())
        margin = (
            ((profit / revenue) * Decimal("100")).quantize(Decimal("0.1"))
            if revenue > 0
            else _zero()
        )
        cells.append(_money_cell(stock, title=f"Stock value {_money_ksh(stock)}"))
        cells.append(
            _money_cell(
                profit,
                tone="good" if profit >= 0 else "bad",
                title=f"Profit {_money_ksh(profit)} · margin {margin}%",
            )
        )
        table_rows.append(cells)

    total_docs = 0
    total_amount = _zero()
    cash_amount = _zero()
    mpesa_amount = _zero()
    for shop in shops_sorted:
        qty, amount = total_by_shop.get(shop.pk, (0, _zero()))
        total_docs += int(qty or 0)
        total_amount += Decimal(amount or 0)
        cash_amount += cash_by_shop.get(shop.pk, (0, _zero()))[1]
        mpesa_amount += mpesa_by_shop.get(shop.pk, (0, _zero()))[1]
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
        total_cells = ["Total"]
        for _label, by_shop, _band in metric_maps:
            total_qty = 0
            col_amount = _zero()
            for shop in shops_sorted:
                qty, amount = by_shop.get(shop.pk, (0, _zero()))
                total_qty += int(qty or 0)
                col_amount += Decimal(amount or 0)
            total_cells.append(
                _qty_amount_cell(
                    total_qty,
                    col_amount,
                    title=f"{total_qty} · {_money_ksh(col_amount)}",
                )
            )
        total_cells.append(
            _money_cell(total_stock, title=f"Stock value {_money_ksh(total_stock)}")
        )
        total_cells.append(
            _money_cell(
                total_profit,
                tone="good" if total_profit >= 0 else "bad",
                title=f"Profit {_money_ksh(total_profit)} · margin {margin_pct}%",
            )
        )
        table_rows.append(total_cells)

    active_shops = sum(
        1 for shop in shops_sorted if total_by_shop.get(shop.pk, (0, _zero()))[0] > 0
    )

    return {
        "headline": "Sales",
        "lead": "Sale receipts by shop — cash, M-Pesa, stock value, and profit.",
        "alerts": [],
        "summary_board": _sales_summary_board(
            total_amount=total_amount,
            total_docs=total_docs,
            cash_amount=cash_amount,
            mpesa_amount=mpesa_amount,
            stock_amount=total_stock,
            profit_amount=total_profit,
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
                    "Cash, M-Pesa, and Total are receipt payments in this period. "
                    "Stock and profit use ex-tax item value net of returns "
                    "for receipts in this period. Profit = selling value − stock."
                ),
            )
        ],
    }


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
            units=Coalesce(Sum(F("quantity") - F("returned_quantity")), 0),
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
        units = int(row["units"] or 0)
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
                0, int(row["quantity"] or 0) - int(row["returned_quantity"] or 0)
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
        qty = int(row.quantity or 0)
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

    total_units = sum((e["total_qty"] for e in by_item.values()), 0)
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
                f"{zero_cost_qty} unit(s) on hand have average cost 0 — "
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


def _build_credits(filters):
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

    return {
        "headline": "Credits",
        "lead": "Credit receipts by shop — paid, due, stock value, and profit.",
        "alerts": [],
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
                empty="No credits for selected shops and period.",
                shop_grid=True,
                footnote=(
                    "Paid, Due, and Total are credit receipts in this period. "
                    "Stock and profit use ex-tax item value net of returns "
                    "for receipts in this period. Profit = selling value − stock."
                ),
            )
        ],
    }


def _build_clients(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    role = filters["role"]
    query = filters.get("query") or ""

    open_credits = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            kind=ShopReceiptKind.CREDIT,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exclude(client_id=None)
    )

    # client_id -> {shop_id: (credits, balance)}
    credit_by_client_shop: dict[int, dict[int, tuple[int, Decimal]]] = {}
    totals_by_client: dict[int, tuple[int, Decimal]] = {}
    for row in open_credits.values("client_id", "shop_id").annotate(
        credits=Count("id", filter=Q(total__gt=F("amount_paid"))),
        balance=Coalesce(Sum(F("total") - F("amount_paid")), _zero()),
    ):
        client_id = row["client_id"]
        if client_id is None:
            continue
        credits_n = int(row["credits"] or 0)
        balance = Decimal(row["balance"] or 0)
        if credits_n <= 0 and balance <= 0:
            continue
        shop_map = credit_by_client_shop.setdefault(client_id, {})
        prev_credits, prev_balance = shop_map.get(row["shop_id"], (0, _zero()))
        shop_map[row["shop_id"]] = (prev_credits + credits_n, prev_balance + balance)
        total_credits, total_balance = totals_by_client.get(client_id, (0, _zero()))
        totals_by_client[client_id] = (
            total_credits + credits_n,
            total_balance + balance,
        )

    client_ids_in_scope = set(
        ShopReceipt.objects.filter(shop_id__in=shop_ids)
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exclude(client_id=None)
        .values_list("client_id", flat=True)
        .distinct()
    )
    ranked = []
    for client in Client.objects.filter(pk__in=client_ids_in_scope).order_by(
        "full_name", "id"
    ):
        phone = client.phone_number or ""
        label = f"{client.full_name} · {phone}" if phone else client.full_name
        total_credits, total_balance = totals_by_client.get(client.pk, (0, _zero()))
        ranked.append(
            (
                total_balance,
                label,
                client.pk,
                credit_by_client_shop.get(client.pk) or {},
                total_credits,
                total_balance,
            )
        )

    ranked.sort(key=lambda row: (-row[0], row[1].lower()))
    client_rows = []
    for (
        _balance,
        label,
        client_id,
        by_shop,
        total_credits,
        total_balance,
    ) in ranked:
        cells = [
            {
                "href": client_credit_account_url(role, client_id, query=query),
                "label": label,
            }
        ]
        for shop in shops:
            shop_credits, shop_balance = by_shop.get(shop.pk, (0, _zero()))
            cells.append(
                _qty_amount_cell(
                    shop_credits,
                    shop_balance,
                    title=f"{shop_credits} credits · {_money_ksh(shop_balance)}",
                )
            )
        cells.append(
            _qty_amount_cell(
                total_credits,
                total_balance,
                title=f"{total_credits} credits · {_money_ksh(total_balance)}",
            )
        )
        client_rows.append(cells)

    columns = ["Client"]
    for shop in shops:
        columns.append(
            _shop_col(shop, pair=True, pair_qty="Cr", pair_amt="Bal")
        )
    columns.append(_pair_total_col(pair_qty="Cr", pair_amt="Bal"))

    return {
        "headline": "Clients",
        "lead": "",
        "alerts": [],
        "metrics": [],
        "insights": [],
        "tables": [
            _table(
                "Clients by shop",
                columns,
                client_rows,
                empty="No clients on file.",
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
) -> tuple[list, dict[int, tuple[int, Decimal]]]:
    """Build matrix rows: supplier link + En/Bal per shop + total. Balance first."""
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
        ranked.append(
            (
                total_balance,
                _supplier_label(supplier).lower(),
                supplier,
                by_shop,
                total_entries,
                total_balance,
            )
        )
    ranked.sort(key=lambda row: (-row[0], row[1]))

    rows = []
    grand_entries = 0
    grand_balance = _zero()
    for (
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

    stock_suppliers = list(Supplier.objects.order_by("name", "id"))
    key_to_supplier_id = {
        _supplier_match_key(
            supplier.name, supplier.phone_country_code, supplier.phone_number
        ): supplier.pk
        for supplier in stock_suppliers
    }

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
        qty = int(line.quantity or 0)
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

    stock_by_supplier_shop: dict[int, dict[int, tuple[int, Decimal]]] = {}
    for bundle in movements.values():
        supplier_id = bundle["supplier_id"]
        shop_id = bundle["shop_id"]
        due = _due_amount(bundle["total"], bundle["paid"])
        shop_map = stock_by_supplier_shop.setdefault(supplier_id, {})
        prev_entries, prev_balance = shop_map.get(shop_id, (0, _zero()))
        shop_map[shop_id] = (prev_entries + 1, prev_balance + due)

    stock_suppliers = [
        supplier for supplier in stock_suppliers if supplier.pk in stock_by_supplier_shop
    ]
    stock_rows, shop_totals = _supplier_shop_rows(
        suppliers=stock_suppliers,
        shops=shops,
        by_supplier_shop=stock_by_supplier_shop,
        kind="stock",
        role=role,
        query=query,
    )
    stats = _supplier_ledger_stats(stock_suppliers, stock_by_supplier_shop)
    shops_with_balance = sum(
        1 for shop in shops if shop_totals.get(shop.pk, (0, _zero()))[1] > 0
    )

    return {
        "headline": "Stock suppliers",
        "period_label": period_label,
        "lead": (
            f"Purchase balances for {period_label}. "
            "En = stock receipts; Bal = unpaid amount still owed."
        ),
        "hide_date_filters": True,
        "show_search": True,
        "search_placeholder": "Search suppliers…",
        "search_empty": "No suppliers match that search.",
        "alerts": [],
        "summary_board": _supplier_summary_board(
            stats,
            shop_count=len(shops),
            shops_with_balance=shops_with_balance,
            entity_label="Suppliers",
            icon="truck",
        ),
        "insights": [],
        "tables": [
            _table(
                "Balances by shop",
                _supplier_pair_columns(shops),
                stock_rows,
                empty="No stock suppliers on file.",
                footnote=(
                    f"Click a supplier to review receipts and pay. "
                    f"Showing {period_label}, sorted by highest outstanding balance."
                ),
                shop_grid=True,
                searchable=True,
                table_class="ax-table--suppliers",
            )
        ],
    }


def _build_expenses(filters):
    from urllib.parse import parse_qs, urlencode

    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    query = filters.get("query") or ""
    role = filters["role"]

    # Preserve back-link context into expense supplier accounts.
    params = parse_qs(query, keep_blank_values=True) if query else {}
    params["from"] = ["expenses"]
    expense_query = urlencode({k: v[0] for k, v in params.items()})

    exp_by_supplier_shop: dict[int, dict[int, tuple[int, Decimal]]] = {}
    for row in (
        Expense.objects.filter(shop_id__in=shop_ids)
        .exclude(supplier_id=None)
        .values("supplier_id", "shop_id")
        .annotate(
            entries=Count("id"),
            balance=Coalesce(Sum(F("amount") - F("amount_paid")), _zero()),
        )
    ):
        supplier_id = row["supplier_id"]
        if supplier_id is None:
            continue
        entries = int(row["entries"] or 0)
        balance = Decimal(row["balance"] or 0)
        shop_map = exp_by_supplier_shop.setdefault(supplier_id, {})
        prev_entries, prev_balance = shop_map.get(row["shop_id"], (0, _zero()))
        shop_map[row["shop_id"]] = (prev_entries + entries, prev_balance + balance)

    expense_suppliers = [
        supplier
        for supplier in ExpenseSupplier.objects.order_by("name", "id")
        if supplier.pk in exp_by_supplier_shop
    ]
    expense_rows, shop_totals = _supplier_shop_rows(
        suppliers=expense_suppliers,
        shops=shops,
        by_supplier_shop=exp_by_supplier_shop,
        kind="expense",
        role=role,
        query=expense_query,
    )
    stats = _supplier_ledger_stats(expense_suppliers, exp_by_supplier_shop)
    shops_with_balance = sum(
        1 for shop in shops if shop_totals.get(shop.pk, (0, _zero()))[1] > 0
    )

    return {
        "headline": "Expense suppliers",
        "lead": (
            "Live outstanding expense balances by shop. "
            "En = expense entries; Bal = unpaid amount still owed."
        ),
        "hide_date_filters": True,
        "show_search": True,
        "search_placeholder": "Search suppliers…",
        "search_empty": "No suppliers match that search.",
        "alerts": [],
        "summary_board": _supplier_summary_board(
            stats,
            shop_count=len(shops),
            shops_with_balance=shops_with_balance,
            entity_label="Suppliers",
            icon="wallet",
        ),
        "insights": [],
        "tables": [
            _table(
                "Balances by shop",
                _supplier_pair_columns(shops),
                expense_rows,
                empty="No expense suppliers on file.",
                footnote=(
                    "Click a supplier to review receipts and pay. "
                    "Sorted by highest outstanding balance."
                ),
                shop_grid=True,
                searchable=True,
                table_class="ax-table--expenses",
            )
        ],
    }


def _build_receipts(filters):
    shop_ids = filters["active_shop_ids"]
    shops = [shop for shop in filters["filter_shops"] if shop.pk in set(shop_ids)]
    start, end = filters["start"], filters["end"]
    query = filters.get("query") or ""
    role = filters["role"]

    all_receipts = ShopReceipt.objects.filter(
        shop_id__in=shop_ids, created_at__gte=start, created_at__lt=end
    )

    kind_specs = (
        ("sales", "Sales"),
        ("credits", "Credits"),
        ("quotations", "Quotations"),
        ("cancelled", "Cancelled"),
        ("partial-returns", "Partial returns"),
    )

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
        kind_filter = _analytics_receipt_kind_filter(kind_slug)
        by_shop: dict[int, tuple[int, Decimal]] = {}
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
        cells = [
            {
                "href": analytics_receipts_list_url(role, kind_slug, query=query),
                "label": label,
            }
        ]
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

    receipt_qs = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("shop", "created_by", "created_by__user")
        .order_by("-created_at", "-id")
    )
    receipt_total = receipt_qs.count()
    receipt_limit = 500
    receipt_rows = []
    for row in receipt_qs[:receipt_limit]:
        cashier = ""
        if row.created_by and row.created_by.user:
            cashier = (
                row.created_by.user.get_full_name()
                or row.created_by.employee_id
                or row.created_by.user.username
            )
        client = row.client_name or "Walk-in"
        if row.client_phone:
            client = f"{client} · {row.client_phone}"
        receipt_rows.append(
            {
                "receipt_id": row.pk,
                "shop_id": row.shop_id,
                "cells": [
                    row.receipt_number,
                    row.get_kind_display(),
                    row.shop.name if row.shop else "—",
                    client,
                    _money_ksh(row.total),
                    row.get_status_display(),
                    row.created_at.strftime("%d %b %Y · %H:%M"),
                    cashier or "—",
                ],
            }
        )

    list_footnote = (
        f"Showing {len(receipt_rows)} of {receipt_total} receipt"
        f"{'' if receipt_total == 1 else 's'} for this period"
        + (" (capped at 500)." if receipt_total > receipt_limit else ".")
        + " Select a receipt to open return."
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
        "headline": "Receipts",
        "lead": "Document counts by shop, plus every receipt in the selected period.",
        "ledger_layout": True,
        "show_search": True,
        "search_placeholder": "Search receipts…",
        "search_empty": "No receipts match that search.",
        "receipt_modal": True,
        "receipt_detail_url_template": detail_template,
        "receipt_return_url_template": return_template,
        "receipt_verify_login_url": verify_url,
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
                hint="In selected period",
            ),
            _metric(
                "Shops",
                str(len(shops)),
                hint="In this view",
            ),
            _metric(
                "Total value",
                _money_ksh(grand_amount),
                hint="All document types",
                tone="good" if grand_amount > 0 else "neutral",
            ),
        ],
        "insights": [],
        "tables": [
            _table(
                "Documents by shop",
                columns,
                table_rows,
                empty="No receipts for selected shops and period.",
                footnote="Docs = receipt count · Amt = document total. Open a type for a filtered list.",
            ),
            _table(
                "All receipts",
                [
                    "Receipt",
                    "Type",
                    "Shop",
                    "Client",
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
        ],
    }
