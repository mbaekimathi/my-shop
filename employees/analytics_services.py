"""Decision-oriented analytics for the Analytics workspace module."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404

from employees.models import EmployeeProfile, EmployeeStatus
from items.models import (
    Item,
    ShopStock,
    StockMovement,
    StockMovementLine,
    StockMovementType,
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
        "summary": "What needs attention across the business right now.",
    },
    {
        "slug": "revenue",
        "label": "Revenue",
        "icon": "banknote",
        "summary": "Where money is earned, spent, and whether shops stay profitable.",
    },
    {
        "slug": "sales",
        "label": "Sales",
        "icon": "shopping-bag",
        "summary": "Which shops and products drive sales — and which lag.",
    },
    {
        "slug": "items",
        "label": "Items",
        "icon": "tags",
        "summary": "All items sold in the period — units, receipts, and value.",
    },
    {
        "slug": "stock",
        "label": "Stock",
        "icon": "package",
        "summary": "Stockout risk, movement pressure, and replenishment priorities.",
    },
    {
        "slug": "quotations",
        "label": "Quotations",
        "icon": "file-text",
        "summary": "Open quote value and shops with the strongest pipeline.",
    },
    {
        "slug": "credits",
        "label": "Credits",
        "icon": "credit-card",
        "summary": "Client credit balances with a link into each credit account.",
    },
    {
        "slug": "clients",
        "label": "Clients",
        "icon": "contact",
        "summary": "Client list with credit balances and account access.",
    },
    {
        "slug": "employees",
        "label": "Employees",
        "icon": "users",
        "summary": "Staffing readiness and who is closing sales.",
    },
    {
        "slug": "suppliers",
        "label": "Suppliers",
        "icon": "truck",
        "summary": "Supplier list with balances and account access.",
    },
    {
        "slug": "expenses",
        "label": "Expenses",
        "icon": "wallet",
        "summary": "Expense suppliers with balances and account access.",
    },
    {
        "slug": "receipts",
        "label": "Receipts",
        "icon": "receipt",
        "summary": "Document mix, cancellations, and return pressure.",
    },
)

ANALYTICS_SECTION_BY_SLUG = {row["slug"]: row for row in ANALYTICS_SECTIONS}


def _money(value) -> str:
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    return f"{amount:,.2f}"


def _money_ksh(value) -> str:
    return f"KSh {_money(value)}"


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


def apply_account_payment(
    *,
    profile,
    kind: str,
    account_id: int,
    amount,
    payment_method: str = "cash",
    stk_payment_id: str = "",
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
    shop_ids = {shop.pk for shop in actionable_shops_for_profile(profile)}
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
                Expense.objects.select_for_update()
                .filter(shop_id__in=shop_ids, supplier_id=supplier.pk)
                .order_by("created_at", "pk")
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
            _stock_lines_for_supplier(supplier, list(shop_ids)).select_related(
                "movement"
            )
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


def _pct(part, whole) -> str:
    whole = Decimal(whole or 0)
    if whole <= 0:
        return "—"
    return f"{(Decimal(part or 0) / whole * 100).quantize(Decimal('0.1'))}%"


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
    qs = ShopReceipt.objects.filter(
        shop_id__in=shop_ids,
        created_at__gte=start,
        created_at__lt=end,
    )
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
    return {"label": label, "value": value, "hint": hint, "tone": tone}


def _table(title, columns, rows, empty="No data for this period.", footnote=""):
    return {
        "title": title,
        "columns": columns,
        "rows": rows,
        "empty": empty,
        "footnote": footnote,
    }


def _insight(title, body):
    return {"title": title, "body": body}


def _filters_context(profile, request):
    from items.views import _report_range_bounds

    range_type, start, end, filter_context = _report_range_bounds(request)
    filter_shops = actionable_shops_for_profile(profile)
    shops_by_id = {shop.pk: shop for shop in filter_shops}
    selected_shop_ids = _parse_shop_ids(request.GET.getlist("shop_id"), shops_by_id)
    active_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]
    delta = end - start
    prev_start, prev_end = start - delta, start
    return {
        **filter_context,
        "filter_shops": filter_shops,
        "selected_shop_ids": selected_shop_ids,
        "active_shop_ids": active_shop_ids,
        "report_range_label": {
            "day": "Day",
            "period": "Period",
            "month": "Month",
            "year": "Year",
        }.get(range_type, "Day"),
        "range_type": range_type,
        "start": start,
        "end": end,
        "prev_start": prev_start,
        "prev_end": prev_end,
    }


def get_analytics_section(slug: str) -> dict:
    section = ANALYTICS_SECTION_BY_SLUG.get((slug or "").strip().lower())
    if section is None:
        raise Http404("Analytics section not found.")
    return section


def build_analytics_page(*, profile, request, section_slug: str = "overview") -> dict:
    section = get_analytics_section(section_slug)
    filters = _filters_context(profile, request)
    filters["role"] = profile.role
    filters["query"] = request.GET.urlencode()
    builders = {
        "overview": _build_overview,
        "revenue": _build_revenue,
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
    return {
        **filters,
        "section": section,
        "section_slug": section["slug"],
        "analytics_sections": ANALYTICS_SECTIONS,
        "page": page,
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
    if client is None:
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
    balance = _zero()
    open_count = 0
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
        if due > 0:
            open_count += 1
        rows.append(
            {
                "id": f"credit-{row.pk}",
                "pay_kind": "credit",
                "pay_id": row.pk,
                "number": row.receipt_number,
                "shop": row.shop.name if row.shop else "—",
                "status": _payment_status_for_due(due, paid),
                "total": _money_ksh(total),
                "paid": _money_ksh(paid),
                "due": _money_ksh(due),
                "due_raw": str(due),
                "can_pay": due > 0,
                "when": row.created_at,
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


def build_supplier_account(*, profile, kind: str, supplier_id: int) -> dict:
    """Ledger for a stock or expense supplier, grouped by receipt / stock-in event."""
    kind = (kind or "").strip().lower()
    if kind not in ("expense", "stock"):
        raise Http404("Supplier type not found.")

    shop_ids = [shop.pk for shop in actionable_shops_for_profile(profile)]

    if kind == "expense":
        supplier = ExpenseSupplier.objects.filter(pk=supplier_id).first()
        if supplier is None:
            raise Http404("Supplier not found.")
        expenses = list(
            Expense.objects.filter(shop_id__in=shop_ids, supplier_id=supplier.pk)
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
            "empty_message": "No expense receipts for this supplier in your shops.",
            "account_kind": "expense",
            "account_id": supplier.pk,
            "can_pay": balance > 0,
        }

    supplier = Supplier.objects.filter(pk=supplier_id).first()
    if supplier is None:
        raise Http404("Supplier not found.")

    lines = list(
        _stock_lines_for_supplier(supplier, shop_ids)
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
        "empty_message": "No stock-in receipts for this supplier in your shops.",
        "account_kind": "stock",
        "account_id": supplier.pk,
        "can_pay": balance > 0,
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


def _build_overview(filters):
    sales, prev_sales, credits, quotes, expenses = _common_receipt_sets(filters)
    shop_ids = filters["active_shop_ids"]
    rev = _sum_total(sales)
    prev_rev = _sum_total(prev_sales)
    exp = expenses.aggregate(total=Coalesce(Sum("amount"), _zero()))["total"] or _zero()
    net = rev - exp
    credit_total = _sum_total(credits)
    quote_total = _sum_total(quotes)
    unpaid_exp = (
        expenses.filter(payment_status=ExpensePaymentStatus.UNPAID).aggregate(
            total=Coalesce(Sum("amount"), _zero())
        )["total"]
        or _zero()
    )
    stockouts = ShopStock.objects.filter(shop_id__in=shop_ids, quantity=0).count()
    low_stock = ShopStock.objects.filter(
        shop_id__in=shop_ids, quantity__gt=0, quantity__lte=5
    ).count()
    pending_staff = EmployeeProfile.objects.filter(
        status=EmployeeStatus.PENDING_APPROVAL
    ).count()
    cancelled = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=filters["start"],
            created_at__lt=filters["end"],
            status=ShopReceiptStatus.CANCELLED,
        ).count()
    )
    sales_count = sales.count()

    alerts = []
    if net < 0:
        alerts.append(
            _alert(
                "danger",
                "Business is losing money this period",
                f"Net is {_money_ksh(net)} after {_money_ksh(exp)} expenses on {_money_ksh(rev)} sales.",
                "Cut non-essential spend or push sales in lagging shops.",
            )
        )
    elif exp > 0 and rev > 0 and (exp / rev) >= Decimal("0.4"):
        alerts.append(
            _alert(
                "warn",
                "Expense ratio is high",
                f"Expenses are {_pct(exp, rev)} of sales revenue.",
                "Review top expense categories before approving new costs.",
            )
        )
    else:
        alerts.append(
            _alert(
                "ok",
                "Revenue covers operating costs",
                f"Net position is {_money_ksh(net)} for {filters.get('report_period_label')}.",
                "Protect margin while scaling winning shops.",
            )
        )

    if stockouts:
        alerts.append(
            _alert(
                "danger",
                f"{stockouts} stock-out line{'s' if stockouts != 1 else ''}",
                "Zero-quantity SKUs cannot sell until replenished.",
                "Prioritise stock-in or inter-shop transfers for those items.",
            )
        )
    elif low_stock:
        alerts.append(
            _alert(
                "warn",
                f"{low_stock} low-stock line{'s' if low_stock != 1 else ''} (≤ 5)",
                "These SKUs are close to stockout.",
                "Reorder or transfer before weekend / peak demand.",
            )
        )

    if unpaid_exp > 0:
        alerts.append(
            _alert(
                "warn",
                "Unpaid expenses outstanding",
                f"{_money_ksh(unpaid_exp)} is still unpaid this period.",
                "Clear critical vendor balances to avoid supply disruption.",
            )
        )
    if credit_total > 0 and rev > 0 and credit_total >= rev * Decimal("0.25"):
        alerts.append(
            _alert(
                "warn",
                "Credit exposure is elevated",
                f"Credits are {_pct(credit_total, rev)} of sales value.",
                "Tighten credit approvals for high-balance clients.",
            )
        )
    if pending_staff:
        alerts.append(
            _alert(
                "warn",
                f"{pending_staff} staff awaiting approval",
                "Pending accounts cannot operate on the floor.",
                "Clear HR approvals for cashiers who need access.",
            )
        )
    if cancelled and sales_count and cancelled / max(sales_count, 1) >= 0.1:
        alerts.append(
            _alert(
                "warn",
                "Cancellation rate is high",
                f"{cancelled} cancelled receipts vs {sales_count} sales.",
                "Audit void reasons with shop managers.",
            )
        )

    delta = rev - prev_rev
    tone = "good" if delta >= 0 else "bad"
    metrics = [
        _metric("Sales revenue", _money_ksh(rev), f"Prev {_money_ksh(prev_rev)}", tone),
        _metric("Net", _money_ksh(net), "Sales − expenses", "good" if net >= 0 else "bad"),
        _metric("Expense ratio", _pct(exp, rev), _money_ksh(exp), "bad" if exp > rev * Decimal("0.4") else "neutral"),
        _metric("Credit book", _money_ksh(credit_total), f"{credits.count()} notes"),
        _metric("Quote pipeline", _money_ksh(quote_total), f"{quotes.count()} quotes"),
        _metric("Stock risk", str(stockouts + low_stock), f"{stockouts} out · {low_stock} low"),
    ]

    shop_rows = []
    for row in (
        sales.values("shop__name")
        .annotate(count=Count("id"), total=Coalesce(Sum("total"), _zero()))
        .order_by("-total")[:6]
    ):
        shop_rows.append(
            [row["shop__name"] or "Shop", str(row["count"]), _money_ksh(row["total"])]
        )

    return {
        "headline": "Decision overview",
        "lead": "Use the alerts below to prioritise actions, then drill into each analytics page.",
        "alerts": alerts,
        "metrics": metrics,
        "insights": [
            _insight(
                "Sales momentum",
                (
                    f"Revenue moved {_money_ksh(abs(delta))} "
                    f"{'up' if delta >= 0 else 'down'} versus the previous equal period."
                ),
            ),
            _insight(
                "Cash at risk",
                (
                    f"Watch unpaid expenses ({_money_ksh(unpaid_exp)}) and open credits "
                    f"({_money_ksh(credit_total)}) together — both tie up cash."
                ),
            ),
        ],
        "tables": [
            _table(
                "Shops to back or coach",
                ["Shop", "Sales", "Revenue"],
                shop_rows,
                empty="No sales in this period.",
                footnote="Focus coaching on the bottom shops; scale inventory where revenue concentrates.",
            )
        ],
    }


def _build_revenue(filters):
    sales, prev_sales, credits, quotes, expenses = _common_receipt_sets(filters)
    rev = _sum_total(sales)
    prev = _sum_total(prev_sales)
    cash = sales.aggregate(v=Coalesce(Sum("cash_amount"), _zero()))["v"] or _zero()
    mpesa = sales.aggregate(v=Coalesce(Sum("mpesa_amount"), _zero()))["v"] or _zero()
    exp = expenses.aggregate(v=Coalesce(Sum("amount"), _zero()))["v"] or _zero()
    net = rev - exp
    credit_total = _sum_total(credits)
    quote_total = _sum_total(quotes)

    alerts = []
    if net < 0:
        alerts.append(
            _alert(
                "danger",
                "Negative net",
                f"Expenses exceed sales by {_money_ksh(abs(net))}.",
                "Freeze discretionary spend until net recovers.",
            )
        )
    if mpesa == 0 and cash > 0:
        alerts.append(
            _alert(
                "warn",
                "No M-Pesa captured",
                "All recorded sales payment is cash — verify till reconciliation.",
                "Confirm cashiers are selecting the correct payment method.",
            )
        )
    if rev > 0 and credit_total > rev:
        alerts.append(
            _alert(
                "danger",
                "Credits exceed sales",
                "Credit notes are larger than sales value this period.",
                "Investigate returns and credit policy immediately.",
            )
        )

    shop_profit = []
    exp_by_shop = {
        row["shop__name"]: row["total"]
        for row in expenses.values("shop__name").annotate(
            total=Coalesce(Sum("amount"), _zero())
        )
    }
    for row in (
        sales.values("shop__name")
        .annotate(total=Coalesce(Sum("total"), _zero()), count=Count("id"))
        .order_by("-total")
    ):
        name = row["shop__name"] or "Shop"
        shop_exp = exp_by_shop.get(name, _zero())
        shop_net = (row["total"] or _zero()) - shop_exp
        shop_profit.append(
            [
                name,
                str(row["count"]),
                _money_ksh(row["total"]),
                _money_ksh(shop_exp),
                _money_ksh(shop_net),
            ]
        )

    return {
        "headline": "Revenue decisions",
        "lead": "Judge profitability by net, not top-line sales alone.",
        "alerts": alerts
        or [
            _alert(
                "ok",
                "Revenue mix looks workable",
                f"Net {_money_ksh(net)} on {_money_ksh(rev)} sales.",
                "Keep pushing high-margin shops and contain expense growth.",
            )
        ],
        "metrics": [
            _metric("Sales", _money_ksh(rev), f"Prev {_money_ksh(prev)}", "good" if rev >= prev else "bad"),
            _metric("Cash", _money_ksh(cash), _pct(cash, rev)),
            _metric("M-Pesa", _money_ksh(mpesa), _pct(mpesa, rev)),
            _metric("Expenses", _money_ksh(exp), _pct(exp, rev), "bad" if exp > rev else "neutral"),
            _metric("Net", _money_ksh(net), "Sales − expenses", "good" if net >= 0 else "bad"),
            _metric("Credits", _money_ksh(credit_total), f"Quotes {_money_ksh(quote_total)}"),
        ],
        "insights": [
            _insight(
                "Payment mix",
                f"Cash {_pct(cash, rev)} · M-Pesa {_pct(mpesa, rev)}. Skewed mix may signal till or device issues.",
            ),
            _insight(
                "Period change",
                f"Sales are {_money_ksh(abs(rev - prev))} {'higher' if rev >= prev else 'lower'} than the previous equal window.",
            ),
        ],
        "tables": [
            _table(
                "Shop contribution (sales vs expenses)",
                ["Shop", "Sales #", "Sales", "Expenses", "Net"],
                shop_profit,
                empty="No shop revenue this period.",
                footnote="Shops with negative net need cost review or sales intervention first.",
            )
        ],
    }


def _build_sales(filters):
    sales, prev_sales, *_rest = _common_receipt_sets(filters)
    lines = ShopReceiptLine.objects.filter(receipt__in=sales)
    units = (
        lines.aggregate(v=Coalesce(Sum(F("quantity") - F("returned_quantity")), 0))["v"]
        or 0
    )
    rev = _sum_total(sales)
    prev = _sum_total(prev_sales)
    count = sales.count()
    avg_ticket = (rev / count) if count else _zero()

    top_items = []
    for row in (
        lines.values("item_name")
        .annotate(
            units=Coalesce(Sum(F("quantity") - F("returned_quantity")), 0),
            value=Coalesce(
                Sum((F("quantity") - F("returned_quantity")) * F("unit_price")),
                _zero(),
            ),
        )
        .order_by("-value")[:8]
    ):
        top_items.append(
            [row["item_name"] or "Item", str(int(row["units"] or 0)), _money_ksh(row["value"])]
        )

    by_shop = []
    for row in (
        sales.values("shop__name")
        .annotate(count=Count("id"), total=Coalesce(Sum("total"), _zero()))
        .order_by("-total")
    ):
        by_shop.append(
            [
                row["shop__name"] or "Shop",
                str(row["count"]),
                _money_ksh(row["total"]),
                _pct(row["total"], rev),
            ]
        )

    concentration = Decimal("0")
    if top_items and rev > 0:
        # approximate from first item value string is hard; recompute
        top_value = (
            lines.values("item_name")
            .annotate(
                value=Coalesce(
                    Sum((F("quantity") - F("returned_quantity")) * F("unit_price")),
                    _zero(),
                )
            )
            .order_by("-value")
            .first()
        )
        if top_value:
            concentration = Decimal(top_value["value"] or 0)

    alerts = []
    if count == 0:
        alerts.append(
            _alert(
                "danger",
                "No sales recorded",
                "Nothing to analyse for this shop/period filter.",
                "Confirm shops are open and receipts are being posted.",
            )
        )
    elif concentration and rev and concentration / rev >= Decimal("0.35"):
        alerts.append(
            _alert(
                "warn",
                "Sales are concentrated in one item",
                f"Top item is about {_pct(concentration, rev)} of sales value.",
                "Protect stock for that SKU and diversify promotions.",
            )
        )
    if count and avg_ticket < Decimal("500"):
        alerts.append(
            _alert(
                "warn",
                "Average ticket is low",
                f"Average sale is {_money_ksh(avg_ticket)}.",
                "Push bundles / upsells on high-velocity counters.",
            )
        )

    return {
        "headline": "Sales decisions",
        "lead": "Double down on winning shops and SKUs; intervene where volume stalls.",
        "alerts": alerts
        or [
            _alert(
                "ok",
                "Sales activity is present",
                f"{count} sales · {units} units · avg ticket {_money_ksh(avg_ticket)}.",
                "Keep inventory aligned to top movers.",
            )
        ],
        "metrics": [
            _metric("Sales", str(count), f"Prev rev {_money_ksh(prev)}"),
            _metric("Units", str(int(units)), "Net of returns"),
            _metric("Revenue", _money_ksh(rev), "Active sales", "good" if rev >= prev else "bad"),
            _metric("Avg ticket", _money_ksh(avg_ticket), "Revenue ÷ sales"),
            _metric("Shops selling", str(len(by_shop)), f"of {len(filters['active_shop_ids'])} selected"),
            _metric("Top SKU share", _pct(concentration, rev), "Concentration risk"),
        ],
        "insights": [
            _insight(
                "Where to stock",
                "Top items table shows what must not stock out. Bottom shops need coaching or assortment fixes.",
            )
        ],
        "tables": [
            _table(
                "Shop league table",
                ["Shop", "Sales", "Revenue", "Share"],
                by_shop,
                empty="No shop sales.",
                footnote="Lowest-share shops are first candidates for promotion or staffing checks.",
            ),
            _table(
                "Priority SKUs",
                ["Item", "Units", "Value"],
                top_items,
                empty="No item sales.",
                footnote="Keep these SKUs replenished first.",
            ),
        ],
    }


def _build_items(filters):
    total = Item.objects.count()
    active = Item.objects.filter(is_suspended=False).count()
    suspended = Item.objects.filter(is_suspended=True).count()
    shop_ids = filters["active_shop_ids"]
    with_stock = (
        ShopStock.objects.filter(shop_id__in=shop_ids, quantity__gt=0)
        .values("item_id")
        .distinct()
        .count()
    )
    never_stocked = max(active - with_stock, 0)

    sales = _receipts_qs(
        shop_ids=shop_ids,
        start=filters["start"],
        end=filters["end"],
        kinds=[ShopReceiptKind.SALE],
    )
    sold_lines = (
        ShopReceiptLine.objects.filter(receipt__in=sales)
        .values("item_id", "item_name")
        .annotate(
            units=Coalesce(Sum(F("quantity") - F("returned_quantity")), 0),
            value=Coalesce(
                Sum((F("quantity") - F("returned_quantity")) * F("unit_price")),
                _zero(),
            ),
            receipts=Count("receipt_id", distinct=True),
        )
        .order_by("-units", "item_name")
    )
    sold_rows = []
    sold_units = 0
    sold_value = _zero()
    for row in sold_lines:
        units = int(row["units"] or 0)
        if units <= 0 and not (row["value"] or 0):
            continue
        value = Decimal(row["value"] or 0)
        sold_units += units
        sold_value += value
        sold_rows.append(
            [
                row["item_name"] or "Item",
                str(units),
                str(row["receipts"] or 0),
                _money_ksh(value),
            ]
        )

    alerts = []
    if total and suspended / total >= 0.2:
        alerts.append(
            _alert(
                "warn",
                "Large suspended share",
                f"{_pct(suspended, total)} of catalog is suspended.",
                "Archive dead SKUs or reactivate sellable ones.",
            )
        )
    if never_stocked:
        alerts.append(
            _alert(
                "warn",
                f"{never_stocked} active items with no stock in selected shops",
                "Active catalog entries that cannot sell.",
                "Stock them or suspend to keep POS clean.",
            )
        )
    if not sold_rows:
        alerts.append(
            _alert(
                "warn",
                "No items sold in this period",
                "Nothing matched the selected shops and dates.",
                "Widen the date range or confirm shops are posting sales.",
            )
        )

    return {
        "headline": "Items sold",
        "lead": "Every item sold in the selected shops and period — use this to prioritise replenishment.",
        "alerts": alerts
        or [
            _alert(
                "ok",
                "Sales cover a clear item set",
                f"{len(sold_rows)} items · {sold_units} units · {_money_ksh(sold_value)}.",
                "Keep top movers in stock; review zero-sale active SKUs separately.",
            )
        ],
        "metrics": [
            _metric("Items sold", str(len(sold_rows)), filters.get("report_period_label", "")),
            _metric("Units sold", str(sold_units), "Net of returns"),
            _metric("Sales value", _money_ksh(sold_value), "From sold lines"),
            _metric("Catalog", str(total), f"{active} active"),
            _metric("Suspended", str(suspended), _pct(suspended, total), "bad" if suspended else "neutral"),
            _metric("No stock", str(never_stocked), "Active but unavailable", "bad" if never_stocked else "neutral"),
        ],
        "insights": [
            _insight(
                "Replenish from this list",
                "Items at the top moved the most units. Stock those first before expanding the catalog.",
            )
        ],
        "tables": [
            _table(
                "All items sold",
                ["Item", "Units", "Sales", "Value"],
                sold_rows,
                empty="No items sold in this period.",
                footnote="Units are net of returns. Sales = number of receipts that included the item.",
            )
        ],
    }


def _build_stock(filters):
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    units = (
        ShopStock.objects.filter(shop_id__in=shop_ids).aggregate(
            v=Coalesce(Sum("quantity"), 0)
        )["v"]
        or 0
    )
    skus = (
        ShopStock.objects.filter(shop_id__in=shop_ids, quantity__gt=0)
        .values("item_id")
        .distinct()
        .count()
    )
    stockouts = list(
        ShopStock.objects.filter(shop_id__in=shop_ids, quantity=0)
        .select_related("shop", "item")
        .order_by("item__name")[:12]
    )
    low = list(
        ShopStock.objects.filter(shop_id__in=shop_ids, quantity__gt=0, quantity__lte=5)
        .select_related("shop", "item")
        .order_by("quantity", "item__name")[:12]
    )
    pending_requests = StockMovement.objects.filter(
        requested_from_shop_id__in=shop_ids,
        movement_type=StockMovementType.REQUEST,
        request_status=StockRequestStatus.PENDING,
    ).count()
    moves = {
        key: StockMovement.objects.filter(
            shop_id__in=shop_ids,
            created_at__gte=start,
            created_at__lt=end,
            movement_type=kind,
        ).aggregate(v=Coalesce(Sum("lines__quantity"), 0))["v"]
        or 0
        for key, kind in (
            ("in", StockMovementType.IN),
            ("out", StockMovementType.OUT),
            ("request", StockMovementType.REQUEST),
        )
    }

    alerts = []
    if stockouts:
        alerts.append(
            _alert(
                "danger",
                f"{len(stockouts)}+ stock-out lines need replenishment",
                "These SKUs are at zero in at least one selected shop.",
                "Raise stock-in or accept pending transfer requests.",
            )
        )
    if pending_requests:
        alerts.append(
            _alert(
                "warn",
                f"{pending_requests} incoming transfer request{'s' if pending_requests != 1 else ''} waiting",
                "Unanswered requests delay shops that already asked for stock.",
                "Open Stock requests and accept/decline with a delivery note.",
            )
        )
    if moves["out"] > moves["in"] * 2 and moves["out"] > 0:
        alerts.append(
            _alert(
                "warn",
                "Outbound movement far exceeds stock-in",
                f"Out {moves['out']} vs in {moves['in']} units this period.",
                "Check whether replenishment is lagging demand.",
            )
        )

    low_rows = [
        [row.shop.name, row.item.name if row.item else "—", str(row.quantity)]
        for row in low
    ]
    out_rows = [
        [row.shop.name, row.item.name if row.item else "—", "0"]
        for row in stockouts
    ]

    return {
        "headline": "Stock decisions",
        "lead": "Prevent lost sales by clearing stockouts and pending transfers first.",
        "alerts": alerts
        or [
            _alert(
                "ok",
                "No urgent stock alarms",
                f"{units} units across {skus} SKUs in selected shops.",
                "Keep monitoring low-stock lines weekly.",
            )
        ],
        "metrics": [
            _metric("On hand", str(units), f"{skus} SKUs"),
            _metric("Stock-outs", str(len(stockouts)), "Qty = 0", "bad" if stockouts else "good"),
            _metric("Low stock", str(len(low)), "Qty 1–5", "warn" if low else "good"),
            _metric("Units in", str(moves["in"]), "Period"),
            _metric("Units out", str(moves["out"]), "Period"),
            _metric("Pending requests", str(pending_requests), "Awaiting supply shop", "warn" if pending_requests else "neutral"),
        ],
        "insights": [
            _insight(
                "Replenishment order",
                "Clear stock-outs, then low stock ≤ 5, then honour pending inter-shop requests.",
            )
        ],
        "tables": [
            _table(
                "Stock-outs (act first)",
                ["Shop", "Item", "Qty"],
                out_rows,
                empty="No zero-stock lines.",
            ),
            _table(
                "Low stock watchlist",
                ["Shop", "Item", "Qty"],
                low_rows,
                empty="No low-stock lines.",
                footnote="Transfer from overstocked shops when possible before buying new stock.",
            ),
        ],
    }


def _build_quotations(filters):
    _sales, _prev, _credits, quotes, _expenses = _common_receipt_sets(filters)
    total = _sum_total(quotes)
    count = quotes.count()
    by_shop = [
        [
            row["shop__name"] or "Shop",
            str(row["count"]),
            _money_ksh(row["total"]),
            _pct(row["total"], total),
        ]
        for row in quotes.values("shop__name")
        .annotate(count=Count("id"), total=Coalesce(Sum("total"), _zero()))
        .order_by("-total")
    ]
    recent = [
        [
            row.receipt_number,
            row.shop.name if row.shop else "—",
            row.client_name or "—",
            _money_ksh(row.total),
            row.created_at.strftime("%d %b · %H:%M"),
        ]
        for row in quotes.select_related("shop").order_by("-created_at")[:10]
    ]
    alerts = []
    if count == 0:
        alerts.append(
            _alert(
                "warn",
                "No quotations in this period",
                "Pipeline is empty for the selected filters.",
                "Ask shops to quote large deals before discounting on the spot.",
            )
        )
    elif total > 0:
        alerts.append(
            _alert(
                "ok",
                "Quote pipeline has value",
                f"{count} quotes worth {_money_ksh(total)}.",
                "Follow up the largest quotes within 48 hours.",
            )
        )

    return {
        "headline": "Quotation decisions",
        "lead": "Quotes are future sales — prioritise follow-up by value.",
        "alerts": alerts,
        "metrics": [
            _metric("Quotes", str(count), filters.get("report_period_label", "")),
            _metric("Pipeline", _money_ksh(total), "Non-cancelled"),
            _metric("Avg quote", _money_ksh(total / count if count else 0), "Value ÷ count"),
            _metric("Shops quoting", str(len(by_shop)), f"of {len(filters['active_shop_ids'])}"),
        ],
        "insights": [
            _insight(
                "Conversion focus",
                "Call clients on the highest-value quotes first. Quotations do not move stock until converted to sales.",
            )
        ],
        "tables": [
            _table("Pipeline by shop", ["Shop", "Quotes", "Value", "Share"], by_shop, empty="No quotes."),
            _table("Latest quotes", ["#", "Shop", "Client", "Total", "When"], recent, empty="No quotes."),
        ],
    }


def _build_credits(filters):
    _sales, _prev, credits, _quotes, _expenses = _common_receipt_sets(filters)
    sales = _receipts_qs(
        shop_ids=filters["active_shop_ids"],
        start=filters["start"],
        end=filters["end"],
        kinds=[ShopReceiptKind.SALE],
    )
    total = _sum_total(credits)
    sales_total = _sum_total(sales)
    count = credits.count()

    open_credits = ShopReceipt.objects.filter(
        shop_id__in=filters["active_shop_ids"],
        kind=ShopReceiptKind.CREDIT,
    ).exclude(status=ShopReceiptStatus.CANCELLED)

    client_rows = []
    for row in (
        open_credits.exclude(client_id=None)
        .values("client_id", "client_name", "client_phone")
        .annotate(
            credits=Count("id", filter=Q(total__gt=F("amount_paid"))),
            balance=Coalesce(Sum(F("total") - F("amount_paid")), _zero()),
        )
        .filter(balance__gt=0)
        .order_by("-balance", "client_name")
    ):
        client_id = row["client_id"]
        name = row["client_name"] or "Client"
        phone = row["client_phone"] or ""
        label = f"{name} · {phone}" if phone else name
        client_rows.append(
            [
                label,
                str(int(row["credits"] or 0)),
                _money_ksh(row["balance"]),
                {
                    "href": client_credit_account_url(
                        filters["role"],
                        client_id,
                        query=filters.get("query") or "",
                    ),
                    "label": "View",
                },
            ]
        )

    # Also include period-only credits that somehow lack client_id (name-only).
    for row in (
        credits.filter(client_id=None)
        .exclude(client_name="")
        .values("client_name", "client_phone")
        .annotate(count=Count("id"), total=Coalesce(Sum("total"), _zero()))
        .order_by("-total")
    ):
        name = row["client_name"] or "Client"
        phone = row["client_phone"] or ""
        label = f"{name} · {phone}" if phone else name
        client_rows.append(
            [
                label,
                str(row["count"]),
                _money_ksh(row["total"]),
                {"label": "—", "href": ""},
            ]
        )

    open_balance = (
        open_credits.aggregate(v=Coalesce(Sum(F("total") - F("amount_paid")), _zero()))[
            "v"
        ]
        or _zero()
    )
    alerts = []
    if open_balance and sales_total and open_balance / max(sales_total, Decimal("0.01")) >= Decimal("0.2"):
        alerts.append(
            _alert(
                "danger",
                "Credit balances are high vs sales",
                f"Open balance {_money_ksh(open_balance)} vs sales {_money_ksh(sales_total)}.",
                "Open client accounts and tighten new credit approvals.",
            )
        )
    elif count:
        alerts.append(
            _alert(
                "warn",
                "Credit notes issued this period",
                f"{count} credits totalling {_money_ksh(total)}.",
                "Review client accounts with the largest balances.",
            )
        )
    elif not client_rows:
        alerts.append(
            _alert(
                "ok",
                "No client credit balances",
                "No open credit accounts under the current shop filters.",
                "Keep approval rules consistent across shops.",
            )
        )
    else:
        alerts.append(
            _alert(
                "ok",
                "Credit book is available",
                f"{len(client_rows)} clients · open balance {_money_ksh(open_balance)}.",
                "Use View to open each client credit account.",
            )
        )

    return {
        "headline": "Client credit accounts",
        "lead": "Clients with credit, how many credits they hold, and their total open balance.",
        "alerts": alerts,
        "metrics": [
            _metric("Clients", str(len(client_rows)), "With open credit"),
            _metric("Period credits", str(count), filters.get("report_period_label", "")),
            _metric("Period value", _money_ksh(total), _pct(total, sales_total) + " of sales"),
            _metric("Open balance", _money_ksh(open_balance), "All open credits", "bad" if open_balance else "good"),
        ],
        "insights": [
            _insight(
                "Account follow-up",
                "Sort by total balance and open View on the largest accounts first.",
            )
        ],
        "tables": [
            _table(
                "Clients on credit",
                ["Client", "Credits", "Total balance", "View"],
                client_rows,
                empty="No clients with credit balances.",
                footnote="Credits = open credit receipts. Total balance = outstanding amount (excludes cancelled).",
            )
        ],
    }


def _build_clients(filters):
    shop_ids = filters["active_shop_ids"]
    sales = _receipts_qs(
        shop_ids=shop_ids,
        start=filters["start"],
        end=filters["end"],
        kinds=[ShopReceiptKind.SALE],
    )
    total_clients = Client.objects.count()
    new_clients = Client.objects.filter(
        created_at__gte=filters["start"], created_at__lt=filters["end"]
    ).count()
    rev = _sum_total(sales)

    open_credits = (
        ShopReceipt.objects.filter(
            shop_id__in=shop_ids,
            kind=ShopReceiptKind.CREDIT,
        )
        .exclude(status=ShopReceiptStatus.CANCELLED)
        .exclude(client_id=None)
    )
    credit_by_client = {
        row["client_id"]: row
        for row in open_credits.values("client_id").annotate(
            credits=Count("id", filter=Q(total__gt=F("amount_paid"))),
            balance=Coalesce(Sum(F("total") - F("amount_paid")), _zero()),
        )
    }
    open_balance = (
        open_credits.aggregate(v=Coalesce(Sum(F("total") - F("amount_paid")), _zero()))[
            "v"
        ]
        or _zero()
    )

    client_rows = []
    ranked = []
    for client in Client.objects.order_by("full_name", "id"):
        credit = credit_by_client.get(client.pk) or {}
        credits_n = int(credit.get("credits") or 0)
        balance = Decimal(credit.get("balance") or 0)
        phone = client.phone_number or ""
        label = f"{client.full_name} · {phone}" if phone else client.full_name
        ranked.append((balance, label, credits_n, client.pk))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    for balance, label, credits_n, client_id in ranked:
        client_rows.append(
            [
                label,
                str(credits_n),
                _money_ksh(balance),
                {
                    "href": client_credit_account_url(
                        filters["role"],
                        client_id,
                        query=filters.get("query") or "",
                    ),
                    "label": "View",
                },
            ]
        )

    with_credit = sum(1 for row in client_rows if int(row[1]) > 0)
    alerts = []
    if open_balance and rev and open_balance / max(rev, Decimal("0.01")) >= Decimal("0.2"):
        alerts.append(
            _alert(
                "danger",
                "Client credit balances are high vs sales",
                f"Open balance {_money_ksh(open_balance)} vs sales {_money_ksh(rev)}.",
                "Open the largest accounts via View and follow up.",
            )
        )
    elif with_credit:
        alerts.append(
            _alert(
                "warn",
                f"{with_credit} client{'s' if with_credit != 1 else ''} on credit",
                f"Open balance {_money_ksh(open_balance)} across the client book.",
                "Use View to open each client account.",
            )
        )
    elif not client_rows:
        alerts.append(
            _alert(
                "warn",
                "No clients on file",
                "The client register is empty.",
                "Capture name and phone on credit and quotation checkouts.",
            )
        )
    else:
        alerts.append(
            _alert(
                "ok",
                "Client register is ready",
                f"{total_clients} clients · {new_clients} new this period.",
                "Open View on any client to see their credit account.",
            )
        )

    return {
        "headline": "Client accounts",
        "lead": "All clients with credit count, total balance, and a link into each account.",
        "alerts": alerts,
        "metrics": [
            _metric("Clients", str(total_clients), "On file"),
            _metric("New in period", str(new_clients), filters.get("report_period_label", "")),
            _metric("On credit", str(with_credit), "Open credit receipts"),
            _metric("Open balance", _money_ksh(open_balance), "All open credits", "bad" if open_balance else "good"),
        ],
        "insights": [
            _insight(
                "Account access",
                "View opens the client credit account — ledger, outstanding balance, and credit history.",
            )
        ],
        "tables": [
            _table(
                "Clients",
                ["Client", "Credits", "Total balance", "View"],
                client_rows,
                empty="No clients on file.",
                footnote="Credits = open credit receipts. Total balance = outstanding amount (excludes cancelled).",
            )
        ],
    }


def _build_employees(filters):
    sales = _receipts_qs(
        shop_ids=filters["active_shop_ids"],
        start=filters["start"],
        end=filters["end"],
        kinds=[ShopReceiptKind.SALE],
    )
    total = EmployeeProfile.objects.count()
    active = EmployeeProfile.objects.filter(
        status=EmployeeStatus.ACTIVE, user__is_active=True
    ).count()
    pending = EmployeeProfile.objects.filter(
        status=EmployeeStatus.PENDING_APPROVAL
    ).count()
    suspended = EmployeeProfile.objects.filter(
        status=EmployeeStatus.SUSPENDED
    ).count()
    cashiers = list(
        sales.values(
            "created_by__employee_id",
            "created_by__user__first_name",
            "created_by__user__last_name",
            "created_by__user__username",
        )
        .annotate(count=Count("id"), total=Coalesce(Sum("total"), _zero()))
        .order_by("-total")[:10]
    )
    rows = []
    for row in cashiers:
        first = (row["created_by__user__first_name"] or "").strip()
        last = (row["created_by__user__last_name"] or "").strip()
        name = f"{first} {last}".strip() or row["created_by__user__username"] or "Staff"
        rows.append(
            [
                name,
                row["created_by__employee_id"] or "—",
                str(row["count"]),
                _money_ksh(row["total"]),
            ]
        )
    alerts = []
    if pending:
        alerts.append(
            _alert(
                "warn",
                f"{pending} employees pending approval",
                "They cannot work until approved.",
                "Open HR Approvals and clear ready candidates.",
            )
        )
    if not rows and sales.count() == 0:
        alerts.append(
            _alert(
                "warn",
                "No cashier sales to rank",
                "No sales in this period for selected shops.",
                "Check day open status and staffing on quiet shops.",
            )
        )

    return {
        "headline": "People decisions",
        "lead": "Approve waiting staff and coach cashiers by sales outcomes.",
        "alerts": alerts
        or [
            _alert(
                "ok",
                "Staffing looks operable",
                f"{active} active · {suspended} suspended.",
                "Use the cashier table for coaching conversations.",
            )
        ],
        "metrics": [
            _metric("Profiles", str(total), ""),
            _metric("Active", str(active), _pct(active, total), "good"),
            _metric("Pending", str(pending), "Need HR action", "warn" if pending else "good"),
            _metric("Suspended", str(suspended), "", "bad" if suspended else "neutral"),
            _metric("Selling cashiers", str(len(rows)), "With sales this period"),
        ],
        "insights": [
            _insight(
                "Performance spread",
                "Large gaps between top and bottom cashiers usually mean training or shift coverage issues — not just 'effort'.",
            )
        ],
        "tables": [
            _table(
                "Cashier leaderboard",
                ["Staff", "ID", "Sales", "Revenue"],
                rows,
                empty="No cashier sales.",
                footnote="Pair top performers with quieter counters for shadow shifts.",
            )
        ],
    }


def _build_suppliers(filters):
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    query = filters.get("query") or ""
    role = filters["role"]

    period_expenses = Expense.objects.filter(
        shop_id__in=shop_ids, created_at__gte=start, created_at__lt=end
    )
    unpaid_period = (
        period_expenses.aggregate(
            v=Coalesce(Sum(F("amount") - F("amount_paid")), _zero())
        )["v"]
        or _zero()
    )
    stock_in_period = StockMovement.objects.filter(
        shop_id__in=shop_ids,
        movement_type=StockMovementType.IN,
        created_at__gte=start,
        created_at__lt=end,
    )
    stock_in_value = (
        stock_in_period.aggregate(
            v=Coalesce(Sum(F("lines__buying_price") * F("lines__quantity")), _zero())
        )["v"]
        or _zero()
    )

    ranked = []

    # Expense suppliers (open unpaid/partial balance across selected shops).
    exp_qs = Expense.objects.filter(shop_id__in=shop_ids).exclude(supplier_id=None)
    exp_stats = {
        row["supplier_id"]: row
        for row in exp_qs.values("supplier_id").annotate(
            entries=Count("id"),
            total=Coalesce(Sum("amount"), _zero()),
            balance=Coalesce(Sum(F("amount") - F("amount_paid")), _zero()),
        )
    }
    for supplier in ExpenseSupplier.objects.order_by("name", "id"):
        stats = exp_stats.get(supplier.pk) or {}
        entries = int(stats.get("entries") or 0)
        balance = Decimal(stats.get("balance") or 0)
        phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
        label = f"{supplier.name} · {phone}" if phone else supplier.name
        ranked.append(
            (
                balance,
                label,
                "Expense",
                entries,
                "expense",
                supplier.pk,
            )
        )

    # Stock suppliers matched to stock-in lines.
    for supplier in Supplier.objects.order_by("name", "id"):
        lines = list(
            StockMovementLine.objects.filter(
                movement__shop_id__in=shop_ids,
                movement__movement_type=StockMovementType.IN,
                supplier_name__iexact=supplier.name,
                supplier_phone_country_code=supplier.phone_country_code,
                supplier_phone_number=supplier.phone_number,
            ).select_related("movement")
        )
        by_movement = {}
        for line in lines:
            movement = line.movement
            if movement is None:
                continue
            key = movement.pk
            if key not in by_movement:
                by_movement[key] = {"movement": movement, "total": _zero()}
            qty = int(line.quantity or 0)
            unit = Decimal(line.buying_price or 0)
            by_movement[key]["total"] += (unit * qty).quantize(Decimal("0.01"))
        entries = len(by_movement)
        balance = _zero()
        for bundle in by_movement.values():
            paid = Decimal(bundle["movement"].amount_paid or 0)
            balance += _due_amount(bundle["total"], paid)
        phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
        label = f"{supplier.name} · {phone}" if phone else supplier.name
        ranked.append(
            (
                balance,
                label,
                "Stock",
                entries,
                "stock",
                supplier.pk,
            )
        )

    ranked.sort(key=lambda row: (-row[0], row[1]))
    supplier_rows = [
        [
            label,
            kind_label,
            str(entries),
            _money_ksh(balance),
            {
                "href": supplier_account_url(role, kind, supplier_id, query=query),
                "label": "View",
            },
        ]
        for balance, label, kind_label, entries, kind, supplier_id in ranked
    ]

    open_balance = sum((row[0] for row in ranked), _zero())
    with_balance = sum(1 for row in ranked if row[0] > 0)
    alerts = []
    if unpaid_period > 0:
        alerts.append(
            _alert(
                "warn",
                "Unpaid supplier bills this period",
                f"{_money_ksh(unpaid_period)} marked unpaid.",
                "Open supplier accounts with the largest balances first.",
            )
        )
    if with_balance:
        alerts.append(
            _alert(
                "warn" if open_balance else "ok",
                f"{with_balance} supplier{'s' if with_balance != 1 else ''} with open balance",
                f"Open balance {_money_ksh(open_balance)} across stock and expense vendors.",
                "Use View to open each supplier account.",
            )
        )
    elif not supplier_rows:
        alerts.append(
            _alert(
                "warn",
                "No suppliers on file",
                "Stock and expense supplier registers are empty.",
                "Capture supplier details on stock-in and expense entry.",
            )
        )
    else:
        alerts.append(
            _alert(
                "ok",
                "Supplier register is ready",
                f"{len(supplier_rows)} suppliers · stock-in {_money_ksh(stock_in_value)} this period.",
                "Open View on any supplier to see their account.",
            )
        )

    return {
        "headline": "Supplier accounts",
        "lead": "All suppliers with entry counts, open balance, and a link into each account.",
        "alerts": alerts,
        "metrics": [
            _metric("Suppliers", str(len(supplier_rows)), "Stock + expense"),
            _metric("Open balances", str(with_balance), _money_ksh(open_balance), "bad" if open_balance else "good"),
            _metric("Stock-in value", _money_ksh(stock_in_value), filters.get("report_period_label", "")),
            _metric("Unpaid expenses", _money_ksh(unpaid_period), "This period", "bad" if unpaid_period else "good"),
        ],
        "insights": [
            _insight(
                "Account access",
                "View opens the supplier account — purchases or expenses and what is still unpaid.",
            )
        ],
        "tables": [
            _table(
                "Suppliers",
                ["Supplier", "Type", "Entries", "Total balance", "View"],
                supplier_rows,
                empty="No suppliers on file.",
                footnote="Total balance = unpaid / partial amounts. Entries = linked expenses or stock-in lines.",
            )
        ],
    }


def _build_expenses(filters):
    shop_ids = filters["active_shop_ids"]
    sales, _prev, _credits, _quotes, expenses = _common_receipt_sets(filters)
    rev = _sum_total(sales)
    total = expenses.aggregate(v=Coalesce(Sum("amount"), _zero()))["v"] or _zero()
    paid = (
        expenses.filter(payment_status=ExpensePaymentStatus.PAID).aggregate(
            v=Coalesce(Sum("amount"), _zero())
        )["v"]
        or _zero()
    )
    unpaid = (
        expenses.aggregate(v=Coalesce(Sum(F("amount") - F("amount_paid")), _zero()))["v"]
        or _zero()
    )
    partial = (
        expenses.filter(payment_status=ExpensePaymentStatus.PARTIAL).aggregate(
            v=Coalesce(Sum("amount"), _zero())
        )["v"]
        or _zero()
    )
    query = filters.get("query") or ""
    role = filters["role"]

    # Open balance across selected shops (not only this period) for each expense supplier.
    all_exp = Expense.objects.filter(shop_id__in=shop_ids).exclude(supplier_id=None)
    exp_stats = {
        row["supplier_id"]: row
        for row in all_exp.values("supplier_id").annotate(
            entries=Count("id"),
            total=Coalesce(Sum("amount"), _zero()),
            balance=Coalesce(Sum(F("amount") - F("amount_paid")), _zero()),
        )
    }
    period_by_supplier = {
        row["supplier_id"]: row
        for row in expenses.exclude(supplier_id=None)
        .values("supplier_id")
        .annotate(
            entries=Count("id"),
            total=Coalesce(Sum("amount"), _zero()),
        )
    }

    ranked = []
    for supplier in ExpenseSupplier.objects.order_by("name", "id"):
        stats = exp_stats.get(supplier.pk) or {}
        period = period_by_supplier.get(supplier.pk) or {}
        entries = int(stats.get("entries") or 0)
        balance = Decimal(stats.get("balance") or 0)
        period_total = Decimal(period.get("total") or 0)
        phone = f"{supplier.phone_country_code} {supplier.phone_number}".strip()
        label = f"{supplier.name} · {phone}" if phone else supplier.name
        ranked.append((balance, period_total, label, entries, supplier.pk))

    ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
    from urllib.parse import parse_qs, urlencode

    params = parse_qs(query, keep_blank_values=True) if query else {}
    params["from"] = ["expenses"]
    expense_query = urlencode({k: v[0] for k, v in params.items()})
    supplier_rows = [
        [
            label,
            str(entries),
            _money_ksh(balance),
            _money_ksh(period_total),
            {
                "href": supplier_account_url(
                    role, "expense", supplier_id, query=expense_query
                ),
                "label": "View",
            },
        ]
        for balance, period_total, label, entries, supplier_id in ranked
    ]

    with_balance = sum(1 for row in ranked if row[0] > 0)
    open_balance = sum((row[0] for row in ranked), _zero())
    alerts = []
    if rev > 0 and total / rev >= Decimal("0.5"):
        alerts.append(
            _alert(
                "danger",
                "Expenses consume most of sales",
                f"Expense ratio is {_pct(total, rev)}.",
                "Pause non-critical vendors until sales catch up.",
            )
        )
    if unpaid > 0:
        alerts.append(
            _alert(
                "warn",
                "Unpaid expenses this period",
                f"{_money_ksh(unpaid)} unpaid · {_money_ksh(partial)} partial.",
                "Open the largest supplier balances via View.",
            )
        )
    if not supplier_rows:
        alerts.append(
            _alert(
                "warn",
                "No expense suppliers on file",
                "The expense supplier register is empty.",
                "Capture supplier details when registering expenses.",
            )
        )
    elif not alerts:
        alerts.append(
            _alert(
                "ok",
                "Expense suppliers are listed",
                f"{len(supplier_rows)} vendors · open balance {_money_ksh(open_balance)}.",
                "Use View to open each expense supplier account.",
            )
        )

    return {
        "headline": "Expense suppliers",
        "lead": "Expense suppliers with entry counts, open balance, and a link into each account.",
        "alerts": alerts,
        "metrics": [
            _metric("Suppliers", str(len(supplier_rows)), "Expense vendors"),
            _metric("Period spend", _money_ksh(total), _pct(total, rev) + " of sales"),
            _metric("Unpaid", _money_ksh(unpaid), "This period", "bad" if unpaid else "good"),
            _metric("Open balance", _money_ksh(open_balance), f"{with_balance} with balance", "bad" if open_balance else "good"),
            _metric("Paid", _money_ksh(paid), "This period"),
        ],
        "insights": [
            _insight(
                "Vendor follow-up",
                "View opens the expense supplier account — ledger lines and what is still unpaid.",
            )
        ],
        "tables": [
            _table(
                "Expense suppliers",
                ["Supplier", "Entries", "Total balance", "Period spend", "View"],
                supplier_rows,
                empty="No expense suppliers on file.",
                footnote="Total balance = unpaid / partial across shops. Period spend uses the selected date range.",
            )
        ],
    }


def _build_receipts(filters):
    shop_ids = filters["active_shop_ids"]
    start, end = filters["start"], filters["end"]
    all_receipts = ShopReceipt.objects.filter(
        shop_id__in=shop_ids, created_at__gte=start, created_at__lt=end
    )
    sales_n = all_receipts.filter(kind=ShopReceiptKind.SALE).exclude(
        status=ShopReceiptStatus.CANCELLED
    ).count()
    credit_n = all_receipts.filter(kind=ShopReceiptKind.CREDIT).exclude(
        status=ShopReceiptStatus.CANCELLED
    ).count()
    quote_n = all_receipts.filter(kind=ShopReceiptKind.QUOTATION).exclude(
        status=ShopReceiptStatus.CANCELLED
    ).count()
    cancelled = all_receipts.filter(status=ShopReceiptStatus.CANCELLED).count()
    partial = all_receipts.filter(status=ShopReceiptStatus.PARTIAL_RETURN).count()
    total_n = all_receipts.count()
    recent = [
        [
            row.receipt_number,
            row.get_kind_display(),
            row.shop.name if row.shop else "—",
            row.client_name or "Walk-in",
            _money_ksh(row.total),
            row.get_status_display(),
            row.created_at.strftime("%d %b · %H:%M"),
        ]
        for row in all_receipts.select_related("shop").order_by("-created_at")[:15]
    ]
    alerts = []
    if total_n and cancelled / total_n >= 0.08:
        alerts.append(
            _alert(
                "danger",
                "Cancellation rate is concerning",
                f"{cancelled} cancelled of {total_n} documents ({_pct(cancelled, total_n)}).",
                "Review void permissions and require manager codes.",
            )
        )
    if partial:
        alerts.append(
            _alert(
                "warn",
                "Partial returns present",
                f"{partial} receipts partially returned.",
                "Confirm returned units hit stock and were not re-sold incorrectly.",
            )
        )

    return {
        "headline": "Receipt decisions",
        "lead": "Document quality signals process control — cancellations and returns are the red flags.",
        "alerts": alerts
        or [
            _alert(
                "ok",
                "Receipt stream looks controlled",
                f"{total_n} documents · {cancelled} cancelled.",
                "Spot-check a sample of voids each week.",
            )
        ],
        "metrics": [
            _metric("Documents", str(total_n), filters.get("report_period_label", "")),
            _metric("Sales", str(sales_n), _pct(sales_n, total_n)),
            _metric("Credits", str(credit_n), _pct(credit_n, total_n)),
            _metric("Quotations", str(quote_n), _pct(quote_n, total_n)),
            _metric("Cancelled", str(cancelled), _pct(cancelled, total_n), "bad" if cancelled else "good"),
            _metric("Partial returns", str(partial), "", "warn" if partial else "neutral"),
        ],
        "insights": [
            _insight(
                "Control checklist",
                "High cancels + high credits usually means training or policy gaps at the till — not just 'mistakes'.",
            )
        ],
        "tables": [
            _table(
                "Latest documents",
                ["#", "Type", "Shop", "Client", "Total", "Status", "When"],
                recent,
                empty="No receipts.",
            )
        ],
    }
