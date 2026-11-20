"""Stock operations for the chat: inter-shop transfers and stock-in.

Writes the same `items_stockmovement` / `items_stockmovementline` rows the
MY-SHOP workspace creates, so movements appear in stock reports and the
receiving shop's pending-request notifications (`supplier_notified=False`).

Transfer semantics mirror MY-SHOP: a transfer is a *request* — stock only
moves when the receiving shop confirms it (in MY-SHOP stock requests). The
chat message card is the instant notification.

Stock-in applies immediately: ShopStock + Item.stock grow and the shop's
weighted average cost is blended with the incoming buying price.
"""

import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .myshop_models import (
    Client,
    CompanyPosSettings,
    CompanyStockSettings,
    Expense,
    ExpenseSupplier,
    Item,
    ItemSerial,
    Shop,
    ShopDaySession,
    ShopItemPrice,
    ShopPosSettings,
    ShopReceipt,
    ShopReceiptLine,
    ShopStock,
    StockMovement,
    StockMovementLine,
    StoreStock,
    StoreStockMovement,
    Supplier,
)

QTY_QUANTUM = Decimal("0.001")
ZERO_QTY = Decimal("0.000")

MOVEMENT_IN = "in"
MOVEMENT_REQUEST = "request"
REQUEST_PENDING = "pending"
ENTRY_BUY_ITEMS = "buy_items"
REASON_TRANSFER = "transfer"
SOURCE_NOTE = "via MY-CHATBOT"

DEFAULT_DIAL = "+254"


class StockError(Exception):
    """Raised with a user-facing message; caught by the API views."""


def _mirror_store_movement(
    *, item, qty, mode, employee_id, note: str = ""
) -> None:
    """Mirror a POS receipt line into the central store ledger.

    Decrements (out) / increments (in) items_storestock and writes an
    items_storestockmovement row tagged source='pos'. Never blocks — the
    shop floor stock check is authoritative — so the store balance can go
    negative when the item was never stocked into the central store.
    """
    if (
        not employee_id
        or not getattr(item, "stock_count_enabled", True)
        or not getattr(item, "deduct_stock_on_sale", True)
    ):
        # Movements need an actor; items with stock counting switched off on
        # the item-stock page (or sale deduction off on the stock tracking
        # page) are manual-only and don't move on sales.
        return
    stock, _ = StoreStock.objects.select_for_update().get_or_create(
        item_id=item.pk, defaults={"quantity": ZERO_QTY}
    )
    delta = qty if mode == MOVEMENT_IN else -qty
    stock.quantity = _quantize_qty((stock.quantity or ZERO_QTY) + delta)
    stock.save(update_fields=["quantity", "updated_at"])
    StoreStockMovement.objects.create(
        movement_type=mode,
        item_id=item.pk,
        quantity=qty,
        balance_after=stock.quantity,
        created_by_id=employee_id,
        received_by_id=employee_id,
        source="pos",
        note=(note or "")[:255],
    )


# ---------------------------------------------------------------------------
# Number / phone helpers (mirroring items.services)
# ---------------------------------------------------------------------------


def _quantize_qty(value) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = ZERO_QTY
    if amount < 0:
        amount = ZERO_QTY
    return amount.quantize(QTY_QUANTUM)


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    if amount < 0:
        amount = Decimal("0")
    return amount.quantize(Decimal("0.01"))


def weighted_average_cost(*, old_qty, old_avg, in_qty, in_price) -> Decimal:
    old_q = _quantize_qty(old_qty)
    new_q = _quantize_qty(in_qty)
    old_avg = _money(old_avg)
    in_price = _money(in_price)
    if new_q <= 0:
        return old_avg
    if old_q <= 0:
        return in_price
    total = old_q + new_q
    return ((old_avg * old_q + in_price * new_q) / total).quantize(Decimal("0.01"))


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def normalize_national_phone(phone: str, dial: str = "") -> str:
    digits = _digits(phone)
    cc = _digits(dial)
    if cc and digits.startswith(cc) and len(digits) > len(cc):
        digits = digits[len(cc) :]
    digits = digits.lstrip("0")
    return digits[:9]


def parse_supplier_phone(raw: str):
    """Split free-typed phone into (dial_code, national_number).

    Defaults to Kenya (+254): '+254712…', '254712…', '0712…' or '712…'
    all normalise to dial '+254' + 9-digit national number. A different
    country code is honoured when the number is given with a leading '+'.
    """
    raw = (raw or "").strip()
    has_plus = raw.startswith("+")
    digits = _digits(raw)
    if not digits:
        return "", ""
    if has_plus and digits and not digits.startswith("254"):
        # Non-Kenyan explicit dial code: first up-to-3 digits are the code.
        for clen in (3, 2, 1):
            cc, national = digits[:clen], digits[clen:]
            if 6 <= len(national) <= 12:
                return "+" + cc, national
    if digits.startswith("254") and len(digits) > 9:
        digits = digits[3:]
    national = digits.lstrip("0")[:9]
    return DEFAULT_DIAL, national


def upsert_supplier(*, name: str, dial: str, phone: str) -> Supplier | None:
    name = (name or "").strip().upper()
    dial = (dial or "").strip()
    phone = normalize_national_phone(phone, dial)
    if not name or not dial or not phone:
        return None
    supplier, _ = Supplier.objects.update_or_create(
        phone_country_code=dial,
        phone_number=phone,
        defaults={"name": name, "phone_country_iso": "KE"},
    )
    return supplier


def upsert_expense_supplier(*, name: str, dial: str, phone: str) -> ExpenseSupplier | None:
    """Expense vendor upsert — mirrors MY-SHOP's upsert_expense_supplier."""
    name = (name or "").strip().upper()
    dial = (dial or "").strip()
    phone = normalize_national_phone(phone, dial)
    if not name or not dial or not phone:
        return None
    supplier, _ = ExpenseSupplier.objects.update_or_create(
        phone_country_code=dial,
        phone_number=phone,
        defaults={"name": name, "phone_country_iso": "KE"},
    )
    return supplier


# ---------------------------------------------------------------------------
# Lookups for live suggestions
# ---------------------------------------------------------------------------


def item_search(shop, query: str, limit: int = 8) -> list:
    tokens = [t for t in (query or "").lower().split() if t]
    qs = Item.objects.filter(is_suspended=False)
    for token in tokens:
        qs = qs.filter(name__icontains=token)
    items = list(qs.order_by("name")[: limit * 2])
    # Prefer names that start with the query — feels like WhatsApp pickers.
    items.sort(
        key=lambda i: (
            0 if i.name.lower().startswith(" ".join(tokens) or query.strip().lower()) else 1,
            i.name.lower(),
        )
    )
    items = items[:limit]
    if not items:
        return []
    stock_rows = {
        r.item_id: r
        for r in ShopStock.objects.filter(
            shop_id=shop.pk, item_id__in=[i.pk for i in items]
        )
    }
    price_rows = {
        r.item_id: r.price
        for r in ShopItemPrice.objects.filter(
            shop_id=shop.pk, item_id__in=[i.pk for i in items], is_sold=True
        )
    }
    results = []
    for item in items:
        price = price_rows.get(item.pk) or item.shop_price or item.minimum_selling_price
        stock = stock_rows.get(item.pk)
        results.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "price": str(_money(price)),
                "stock": str(_quantize_qty(stock.quantity if stock else 0)),
                "avg_cost": str(_money(stock.average_cost if stock else 0)),
                "track_serial": bool(item.track_serial_number),
            }
        )
    return results


def serial_search(shop, item_id, query: str, limit: int = 8) -> list:
    """Available serial numbers at the shop for a tracked item."""
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return []
    qs = ItemSerial.objects.filter(
        item_id=iid, shop_id=shop.pk, is_available=True
    )
    q = (query or "").strip()
    if q:
        qs = qs.filter(serial_number__icontains=q)
    return [
        {"serial": s.serial_number}
        for s in qs.order_by("serial_number")[:limit]
    ]


def client_search(q: str, limit: int = 8):
    """Registered client suggestions by name or phone (global registry)."""
    q = (q or "").strip()
    if not q:
        return []
    digits = re.sub(r"\D", "", q)
    qs = Client.objects.filter(
        Q(full_name__icontains=q) | Q(phone_number__icontains=q)
    )
    if digits:
        qs = qs | Client.objects.filter(phone_normalized__icontains=digits)
    qs = qs.distinct()[:limit]
    return [
        {"id": c.pk, "name": c.full_name, "phone": c.phone_number}
        for c in qs
    ]


def upsert_client(full_name: str, phone: str):
    """Find or create a client by normalized phone — mirrors upsert_client."""
    phone = (phone or "").strip()
    name = (full_name or "").strip().upper()
    if not phone and not name:
        return None
    digits = re.sub(r"\D", "", phone)[-9:] if phone else ""
    if digits:
        c = Client.objects.filter(phone_normalized__endswith=digits).first()
        if c:
            if name and c.full_name != name:
                c.full_name = name
                c.save(update_fields=["full_name", "updated_at"])
            return c
    return Client.objects.create(
        full_name=name or "CUSTOMER",
        phone_number=phone,
        phone_normalized=digits or phone,
    )


def supplier_search(query: str, limit: int = 8, *, kind: str = "stock") -> list:
    """Live name/phone suggestions for stock-in or expense suppliers.

    ``kind="expense"`` searches ``ExpenseSupplier``; anything else uses the
    stock-in ``Supplier`` table. Matches on name substring or phone digits.
    """
    phrase = (query or "").strip()
    digits = _digits(phrase)
    Model = ExpenseSupplier if kind == "expense" else Supplier
    qs = Model.objects.all()
    if phrase:
        from django.db.models import Q

        cond = Q(name__icontains=phrase)
        if digits:
            cond |= Q(phone_number__icontains=digits)
            cond |= Q(phone_country_code__icontains=digits)
            # Also match as typed (spaces/leading 0) against national number.
            national = digits.lstrip("0")
            if national and national != digits:
                cond |= Q(phone_number__icontains=national)
        qs = qs.filter(cond)
    return [
        {
            "id": s.pk,
            "name": s.name,
            "dial": s.phone_country_code,
            "phone": s.phone_number,
            "label": f"{s.name} ({s.phone_country_code} {s.phone_number})",
        }
        for s in qs.order_by("name")[:limit]
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _normalize_serials(values) -> list:
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    seen, serials = set(), []
    for value in values or []:
        serial = str(value or "").strip().upper()
        if not serial or serial in seen:
            continue
        if not re.match(r"^[A-Z0-9\-_./]{3,120}$", serial):
            raise StockError(f'"{serial}" is not a valid serial number.')
        seen.add(serial)
        serials.append(serial)
    return serials


def _parse_lines(raw_lines, shop, *, need_stock: bool, sale_flow: bool = False):
    if not isinstance(raw_lines, list) or not raw_lines:
        raise StockError("Add at least one item with a quantity.")
    if len(raw_lines) > 20:
        raise StockError("Too many lines — split it into smaller transfers.")

    item_ids, parsed = [], []
    for index, raw in enumerate(raw_lines, 1):
        try:
            item_id = int((raw or {}).get("item_id"))
        except (TypeError, ValueError):
            raise StockError(f"Line {index}: pick an item from the suggestions.")
        serials = _normalize_serials((raw or {}).get("serials") or [])
        qty = (raw or {}).get("quantity")
        if serials:
            quantity = Decimal(len(serials))
        else:
            try:
                quantity = Decimal(str(qty or ""))
            except (InvalidOperation, TypeError):
                raise StockError(f"Line {index}: enter a valid quantity.")
        if quantity <= 0:
            raise StockError(f"Line {index}: quantity must be greater than zero.")
        if quantity != quantity.quantize(QTY_QUANTUM):
            raise StockError(f"Line {index}: up to 3 decimal places allowed.")
        price_raw = (raw or {}).get("buying_price")
        buying_price = None
        if price_raw not in (None, ""):
            try:
                buying_price = _money(price_raw)
            except (InvalidOperation, TypeError):
                raise StockError(f"Line {index}: enter a valid buying price.")
        parsed.append(
            {
                "item_id": item_id,
                "quantity": quantity,
                "buying_price": buying_price,
                "serials": serials,
            }
        )
        item_ids.append(item_id)

    items = {
        i.pk: i
        for i in Item.objects.select_for_update().filter(pk__in=set(item_ids))
    }
    stocks = {
        s.item_id: s
        for s in ShopStock.objects.select_for_update().filter(
            shop_id=shop.pk, item_id__in=set(item_ids)
        )
    }
    for line in parsed:
        item = items.get(line["item_id"])
        if item is None:
            raise StockError("One of the items could not be found.")
        if item.is_suspended:
            raise StockError(f'"{item.name}" is suspended and cannot be moved.')
        line["item"] = item
        if item.track_serial_number and not line["serials"] and not need_stock:
            raise StockError(
                f'"{item.name}" is serial-tracked — scan or enter its serial '
                "numbers; the count becomes the quantity."
            )
        if need_stock:
            stock = stocks.get(item.pk)
            available = stock.quantity if stock else ZERO_QTY
            tracks_sale = getattr(item, "deduct_stock_on_sale", True)
            if (tracks_sale or not sale_flow) and available < line["quantity"]:
                raise StockError(
                    f'Not enough "{item.name}" at {shop.name} '
                    f"(have {available}, sending {line['quantity']})."
                )
            line["shop_stock"] = stock
    return parsed


def _resolve_transfer_serials(item: Item, shop, line: dict):
    """Serials for a transfer line: explicit scanned list or auto-pick."""
    if line["serials"]:
        serials = line["serials"]
        available = {
            s.serial_number
            for s in ItemSerial.objects.select_for_update().filter(
                item_id=item.pk,
                shop_id=shop.pk,
                serial_number__in=serials,
                is_available=True,
            )
        }
        missing = [s for s in serials if s not in available]
        if missing:
            raise StockError(
                f'"{item.name}": serial not in stock at {shop.name} '
                f'({", ".join(missing[:5])}).'
            )
        return serials
    qty = line["quantity"]
    wanted = int(qty)
    if qty != wanted:
        raise StockError(
            f'"{item.name}" transfers in whole units — serial-tracked items '
            "need a whole-number quantity."
        )
    rows = list(
        ItemSerial.objects.filter(
            item_id=item.pk, shop_id=shop.pk, is_available=True
        ).order_by("serial_number")[:wanted]
    )
    if len(rows) != wanted:
        raise StockError(
            f'"{item.name}": only {len(rows)} serial'
            f'{"s" if len(rows) != 1 else ""} in stock at {shop.name}.'
        )
    return [r.serial_number for r in rows]


# ---------------------------------------------------------------------------
# Transfer (request → receiver confirms in MY-SHOP)
# ---------------------------------------------------------------------------


@transaction.atomic
def create_transfer(me, destination, raw_lines, staff_code="",
                    note: str = "", require_member: bool = False,
                    employee_id=None):
    """Returns (movement, parsed_lines)."""
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, me, require_member=require_member
        )
    assert_employee_may(
        employee_id,
        ("my-shop", "stock_requests"),
        ("stock-management", "request"),
    )
    if destination is None:
        raise StockError("Select a shop to transfer to.")
    if destination.pk == me.pk:
        raise StockError("You cannot transfer stock to your own shop.")
    note = (note or "").strip()
    if (
        getattr(get_stock_requirements(), "require_note_on_request", False)
        and not note
    ):
        raise StockError("Enter a note for this transfer request.")

    lines = _parse_lines(raw_lines, me, need_stock=True)
    for line in lines:
        item = line["item"]
        line["serials"] = (
            _resolve_transfer_serials(item, me, line)
            if item.track_serial_number
            else []
        )

    movement = StockMovement.objects.create(
        movement_type=MOVEMENT_REQUEST,
        entry_source="",
        shop_id=me.pk,
        requested_from_shop_id=destination.pk,
        request_status=REQUEST_PENDING,
        supplier_notified=False,  # the receiving shop's pending notification
        requester_notified=True,
        created_by_id=(employee_id or None),
        notes=(f"{note} · {SOURCE_NOTE}" if note else SOURCE_NOTE),
    )
    for line in lines:
        StockMovementLine.objects.create(
            movement_id=movement.pk,
            item_id=line["item"].pk,
            item_name=line["item"].name,
            quantity=line["quantity"],
            buying_price=line["buying_price"],
            unit_cost=Decimal("0"),
            reason=REASON_TRANSFER,
            serial_numbers=line["serials"],
        )
    return movement, lines


# ---------------------------------------------------------------------------
# Stock in (immediate — mirrors items.services stock-in path)
# ---------------------------------------------------------------------------


STOCK_PAYMENT_STATUSES = ("unpaid", "paid", "partial")


def get_stock_requirements() -> CompanyStockSettings:
    """Company-wide compulsory-field flags (singleton row, pk=1)."""
    row = CompanyStockSettings.objects.filter(pk=1).first()
    if row is None:
        row = CompanyStockSettings(pk=1)
    return row


@transaction.atomic
def create_stock_in(me, raw_lines, *, supplier_name: str, supplier_phone: str,
                    payment_status: str = "", staff_code: str = "",
                    require_member: bool = False, employee_id=None):
    """Returns (movement, parsed_lines)."""
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, me, require_member=require_member
        )
    assert_employee_may(
        employee_id,
        ("my-shop", "buy_stock"),
        ("stock-management", "in"),
    )
    stock_req = get_stock_requirements()
    name = (supplier_name or "").strip()
    dial, national = parse_supplier_phone(supplier_phone)
    if getattr(stock_req, "require_supplier_on_in", False):
        if not name:
            raise StockError("Enter the supplier's name.")
        if not national:
            raise StockError("Enter the supplier's phone number.")
    status = (payment_status or "").strip().lower()
    if getattr(stock_req, "require_payment_status_on_in", False):
        if status not in STOCK_PAYMENT_STATUSES:
            raise StockError(
                "Choose a payment status (unpaid, paid, or partial)."
            )
    elif status not in STOCK_PAYMENT_STATUSES:
        status = ""

    lines = _parse_lines(raw_lines, me, need_stock=False)
    if getattr(stock_req, "require_buying_price_on_in", False):
        for line in lines:
            if line["buying_price"] is None:
                raise StockError(
                    f'Enter a buying price for "{line["item"].name}".'
                )
    for line in lines:
        if not line["serials"]:
            continue
        item = line["item"]
        taken = set(
            ItemSerial.objects.filter(
                item_id=item.pk,
                serial_number__in=line["serials"],
                is_available=True,
            ).values_list("serial_number", flat=True)
        )
        if taken:
            raise StockError(
                f'"{item.name}": serial already in stock ('
                + ", ".join(sorted(taken)[:5])
                + ")."
            )

    movement = StockMovement.objects.create(
        movement_type=MOVEMENT_IN,
        entry_source=ENTRY_BUY_ITEMS,
        shop_id=me.pk,
        request_status="",
        payment_status=status,
        created_by_id=(employee_id or None),
        notes=SOURCE_NOTE,
    )

    paid_total = Decimal("0")
    for line in lines:
        item = line["item"]
        qty = _quantize_qty(line["quantity"])
        price = line["buying_price"]

        stock = line.get("shop_stock")
        if stock is None:
            stock, _ = ShopStock.objects.select_for_update().get_or_create(
                shop_id=me.pk, item_id=item.pk, defaults={"quantity": ZERO_QTY}
            )
        if item.track_serial_number:
            existing = {
                s.serial_number: s
                for s in ItemSerial.objects.filter(
                    item_id=item.pk, serial_number__in=line["serials"]
                )
            }
            to_create = []
            for serial in line["serials"]:
                row = existing.get(serial)
                if row is None:
                    to_create.append(
                        ItemSerial(
                            item_id=item.pk,
                            shop_id=me.pk,
                            serial_number=serial,
                            is_available=True,
                        )
                    )
                else:
                    row.is_available = True
                    row.shop_id = me.pk
                    row.save(
                        update_fields=["is_available", "shop_id", "updated_at"]
                    )
            if to_create:
                ItemSerial.objects.bulk_create(to_create)

        StockMovementLine.objects.create(
            movement_id=movement.pk,
            item_id=item.pk,
            item_name=item.name,
            quantity=qty,
            buying_price=price,
            unit_cost=Decimal("0"),
            payment_status=status,
            serial_numbers=line["serials"],
            supplier_name=name.upper(),
            supplier_phone_country_code=dial,
            supplier_phone_number=national,
        )

        if price and price > 0:
            stock.average_cost = weighted_average_cost(
                old_qty=stock.quantity,
                old_avg=stock.average_cost,
                in_qty=qty,
                in_price=price,
            )
            paid_total += (price * qty).quantize(Decimal("0.01"))
        stock.quantity = _quantize_qty(stock.quantity) + qty
        stock.save(update_fields=["quantity", "average_cost", "updated_at"])

        item.stock = _quantize_qty(item.stock) + qty
        item.save(update_fields=["stock"])

    if status == "paid" and paid_total > 0:
        movement.amount_paid = paid_total
        movement.save(update_fields=["amount_paid"])
    if name:
        upsert_supplier(name=name, dial=dial, phone=national)
    return movement, lines


# ---------------------------------------------------------------------------
# Transfer response — mirrors items.services.respond_to_stock_request
# ---------------------------------------------------------------------------


@transaction.atomic
def respond_to_transfer(me, movement_id, decision, received, reason="",
                        serials_by_line=None, staff_code="",
                        employee_id=None):
    """Confirm or decline a pending transfer at the receiving shop.

    Accept requires every received quantity to equal what was sent; then
    stock moves sender → receiver (weighted avg cost carried over) and any
    serials are reassigned. Same outcome as MY-SHOP's confirm screen.

    Both decisions need the 6-digit staff code of a member of the
    receiving shop — it is stored on `responded_by_id` so reports show
    which employee confirmed or declined.
    """
    decision = (decision or "").strip().lower()
    if decision not in ("accept", "decline"):
        raise StockError("Choose accept or decline.")

    movement = (
        StockMovement.objects.select_for_update().filter(pk=movement_id).first()
    )
    if movement is None:
        raise StockError("This stock transfer could not be found.")
    if (
        movement.movement_type != MOVEMENT_REQUEST
        or movement.request_status != REQUEST_PENDING
    ):
        raise StockError("This stock transfer is no longer pending.")
    if movement.requested_from_shop_id != me.pk:
        raise StockError("Only the receiving shop can respond to this transfer.")
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, me, require_member=True
        )
    assert_employee_may(
        employee_id,
        ("my-shop", "respond_stock_request"),
        ("stock-management", "request"),
    )
    sender_id, receiver_id = movement.shop_id, movement.requested_from_shop_id
    if not sender_id or not receiver_id:
        raise StockError("This stock transfer is missing shop details.")
    now = timezone.now()
    movement.responded_by_id = employee_id or None

    update = [
        "request_status", "response_note", "requester_notified",
        "supplier_notified", "responded_at", "responded_by_id",
    ]
    if decision == "decline":
        note = " ".join(str(reason or "").split()).strip()
        if len(note) < 3:
            raise StockError(
                "Enter a reason for declining (for example: wrong quantity)."
            )
        movement.request_status = "declined"
        movement.response_note = note[:500]
        movement.requester_notified = False  # notifies the sending shop
        movement.supplier_notified = True
        movement.responded_at = now
        movement.save(update_fields=update)
        return movement

    lines = list(
        StockMovementLine.objects.select_for_update()
        .filter(movement_id=movement.pk)
        .order_by("pk")
    )
    if not lines:
        raise StockError("This stock transfer has no items.")

    item_ids = [l.item_id for l in lines]
    items = {
        i.pk: i for i in Item.objects.select_for_update().filter(pk__in=item_ids)
    }
    stocks = {
        (s.shop_id, s.item_id): s
        for s in ShopStock.objects.select_for_update().filter(
            shop_id__in=[sender_id, receiver_id], item_id__in=item_ids
        )
    }

    received = received or {}
    serials_by_line = serials_by_line or {}
    errors, prepared = [], []
    for line in lines:
        item = items.get(line.item_id)
        name = line.item_name or (item.name if item else "item")
        sent = _quantize_qty(line.quantity)
        tracked = item is not None and item.track_serial_number and sent > 0
        posted_serials = _normalize_serials(
            serials_by_line.get(str(line.pk)) or []
        )
        raw = received.get(str(line.pk), received.get(line.pk))
        if tracked and posted_serials:
            # Serials scanned — they ARE the quantity received.
            got = sent
        elif raw in (None, ""):
            if tracked:
                errors.append(
                    f'"{name}": scan or enter the {int(sent)} serial '
                    "number(s) received."
                )
            else:
                errors.append(f'"{name}": enter the quantity received.')
            continue
        else:
            try:
                got = Decimal(str(raw).strip())
            except (InvalidOperation, TypeError):
                errors.append(f'"{name}": enter a valid received quantity.')
                continue
            if got != sent:
                errors.append(
                    f'"{name}": received quantity does not match this '
                    "transfer. Decline if the count is wrong."
                )
                continue

        sender_stock = stocks.get((sender_id, line.item_id))
        if sender_stock is None:
            sender_stock, _ = ShopStock.objects.get_or_create(
                shop_id=sender_id, item_id=line.item_id,
                defaults={"quantity": ZERO_QTY},
            )
            stocks[(sender_id, line.item_id)] = sender_stock
        receiver_stock = stocks.get((receiver_id, line.item_id))
        if receiver_stock is None:
            receiver_stock, _ = ShopStock.objects.get_or_create(
                shop_id=receiver_id, item_id=line.item_id,
                defaults={"quantity": ZERO_QTY},
            )
            stocks[(receiver_id, line.item_id)] = receiver_stock

        if sender_stock.quantity < sent:
            errors.append(
                f'Not enough "{name}" at the sending shop '
                f"(available {sender_stock.quantity}, transfer {sent})."
            )
            continue

        serial_objects = {}
        serials = []
        if tracked:
            stored = [
                str(s).strip().upper()
                for s in (line.serial_numbers or [])
                if str(s).strip()
            ]
            if posted_serials:
                if stored and sorted(posted_serials) != sorted(stored):
                    errors.append(
                        f'"{name}": serial numbers do not match this '
                        "transfer. Check what you received or decline."
                    )
                    continue
                serials = posted_serials
            else:
                serials = stored
            if serials:
                serial_objects = {
                    s.serial_number: s
                    for s in ItemSerial.objects.select_for_update().filter(
                        item_id=item.pk, shop_id=sender_id,
                        serial_number__in=serials, is_available=True,
                    )
                }
                missing = [s for s in serials if s not in serial_objects]
                if missing:
                    errors.append(
                        f'"{name}": serial no longer in stock at the sender '
                        f'({", ".join(missing[:5])}).'
                    )
                    continue
            else:
                rows = list(
                    ItemSerial.objects.select_for_update()
                    .filter(item_id=item.pk, shop_id=sender_id, is_available=True)
                    .order_by("serial_number")[: int(sent)]
                )
                serials = [r.serial_number for r in rows]
                serial_objects = {r.serial_number: r for r in rows}
            if len(serials) != sent:
                errors.append(
                    f'"{name}": need {sent} serials available at the sender.'
                )
                continue

        prepared.append(
            {
                "line": line, "item": item, "qty": sent,
                "sender_stock": sender_stock, "receiver_stock": receiver_stock,
                "serials": serials, "serial_objects": serial_objects,
            }
        )

    if errors:
        raise StockError(" ".join(errors))

    for row in prepared:
        line, item, qty = row["line"], row["item"], row["qty"]
        if qty > 0 and item is not None and item.track_serial_number:
            for serial in row["serials"]:
                obj = row["serial_objects"][serial]
                obj.shop_id = receiver_id
                obj.is_available = True
                obj.save(update_fields=["shop_id", "is_available", "updated_at"])
            line.serial_numbers = row["serials"]
            line.save(update_fields=["serial_numbers"])
        if qty <= 0:
            continue

        transfer_cost = _money(row["sender_stock"].average_cost)
        if transfer_cost > 0:
            row["receiver_stock"].average_cost = weighted_average_cost(
                old_qty=row["receiver_stock"].quantity,
                old_avg=row["receiver_stock"].average_cost,
                in_qty=qty,
                in_price=transfer_cost,
            )
        row["sender_stock"].quantity = (
            _quantize_qty(row["sender_stock"].quantity) - qty
        )
        row["sender_stock"].save(update_fields=["quantity", "updated_at"])
        row["receiver_stock"].quantity = (
            _quantize_qty(row["receiver_stock"].quantity) + qty
        )
        row["receiver_stock"].save(
            update_fields=["quantity", "average_cost", "updated_at"]
        )

    movement.request_status = "fulfilled"
    movement.requester_notified = False  # notifies the sending shop
    movement.supplier_notified = True
    movement.responded_at = now
    movement.save(update_fields=update)
    return movement


# ---------------------------------------------------------------------------
# Card payloads embedded in chat messages
# ---------------------------------------------------------------------------


def transfer_card(movement: StockMovement, sender, receiver, movement_lines,
                  readonly: bool = False) -> dict:
    return {
        "transfer": {
            "movement_id": movement.pk,
            "status": movement.request_status,
            "from_shop": sender.name,
            "to_shop": receiver.name,
            "from_shop_id": sender.pk,
            "to_shop_id": receiver.pk,
            "readonly": readonly,
            "responded_by": (
                employee_label(movement.responded_by_id)
                if movement.responded_by_id
                else ""
            ),
            "items": [
                {
                    "line_id": l.pk,
                    "name": l.item_name,
                    "qty": str(_quantize_qty(l.quantity)),
                    "serials": l.serial_numbers or [],
                    "serial_count": len(l.serial_numbers or []),
                }
                for l in movement_lines
            ],
        }
    }


def stock_in_card(movement: StockMovement, shop, lines, supplier_name) -> dict:
    return {
        "stock_in": {
            "movement_id": movement.pk,
            "shop": shop.name,
            "supplier": supplier_name,
            "items": [
                {
                    "name": line["item"].name,
                    "qty": str(line["quantity"]),
                    "serials": line.get("serials") or [],
                    "price": (
                        str(line["buying_price"])
                        if line["buying_price"] is not None
                        else ""
                    ),
                }
                for line in lines
            ],
        }
    }


# ---------------------------------------------------------------------------
# Sale — mirrors shops.services.complete_shop_checkout (kind=sale)
# ---------------------------------------------------------------------------


def _employee_row(employee_id):
    """Active employee by 6-digit code — mirrors verify_active_employee_code."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM employees_employeeprofile "
            "WHERE employee_id=%s AND status='active' LIMIT 1",
            [employee_id],
        )
        row = cur.fetchone()
    return row[0] if row else None


def _employee_allocated(emp_pk: int, shop_id: int) -> bool:
    """True when the employee is assigned to this shop (shop member)."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM employees_employeeprofile_assigned_shops "
            "WHERE employeeprofile_id=%s AND shop_id=%s LIMIT 1",
            [emp_pk, shop_id],
        )
        return cur.fetchone() is not None


def verify_employee_code(code: str, shop=None, require_member: bool = False) -> int:
    """Resolve a 6-digit staff code to an active employee profile pk.

    With `require_member`, the code must belong to a staff member allocated
    to `shop` — used when the acting user isn't a member of the shop and a
    shop member has to confirm the action.
    """
    code = (code or "").strip()
    if not code:
        if require_member:
            raise StockError(
                "Ask a member of this shop to enter their 6-digit staff code."
            )
        raise StockError("Enter your 6-digit staff code to complete.")
    emp_id = _employee_row(code)
    if emp_id is None:
        raise StockError("That staff code is not an active employee.")
    if (
        require_member
        and shop is not None
        and not _employee_allocated(emp_id, shop.pk)
    ):
        raise StockError(
            "Only a member of this shop can confirm — "
            "ask a shop staff member to enter their code."
        )
    return emp_id


def member_employee_id(code: str, shop) -> int | None:
    """Active employee pk when `code` belongs to a member of `shop`."""
    code = (code or "").strip()
    if not code or shop is None:
        return None
    emp_id = _employee_row(code)
    if emp_id is None or not _employee_allocated(emp_id, shop.pk):
        return None
    return emp_id


def assert_employee_may(employee_id, *keys):
    """Raise StockError when the HR permission matrix blocks every key.

    ``keys`` are ``(module_slug, submodule_slug)`` pairs — the same ones the
    MY-SHOP views and services gate on — and the action is allowed when the
    authorising employee may use ANY of them. ``employee_id`` is a profile pk
    (from ``verify_employee_code`` or the signed-in chat employee); a falsy or
    unknown pk skips the check (authentication was already enforced upstream).
    """
    if not employee_id:
        return
    from employees.models import EmployeeProfile
    from employees.module_permissions import employee_may

    profile = EmployeeProfile.objects.filter(pk=employee_id).first()
    if profile is not None and not any(
        employee_may(profile, module, submodule) for module, submodule in keys
    ):
        raise StockError("You do not have permission for this action.")


def employee_label(emp_pk) -> str:
    """Display name for an employee profile, used in card footers."""
    if not emp_pk:
        return ""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT u.first_name, u.last_name, u.username "
            "FROM employees_employeeprofile ep "
            "JOIN auth_user u ON u.id = ep.user_id "
            "WHERE ep.id=%s LIMIT 1",
            [emp_pk],
        )
        row = cur.fetchone()
    if row is None:
        return ""
    name = " ".join(p for p in (row[0] or "", row[1] or "") if p).strip()
    return name or (row[2] or "")


def _destination_shop(shop_id):
    """Active, visible destination shop or None — like services.shop_by_id."""
    try:
        return Shop.objects.get(
            pk=shop_id, is_suspended=False, is_hidden=False
        )
    except (Shop.DoesNotExist, ValueError, TypeError):
        return None


def _next_receipt_number(shop, prefix="S", kind=None):
    max_seq = 0
    for raw in ShopReceipt.objects.filter(
        shop_id=shop.pk,
        kind=kind or "sale",
        receipt_number__istartswith=prefix,
    ).values_list("receipt_number", flat=True):
        suffix = (raw or "").strip().upper()[len(prefix):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    seq = max_seq + 1
    for _ in range(1000):
        candidate = f"{prefix}{seq:04d}"
        if not ShopReceipt.objects.filter(
            shop_id=shop.pk, receipt_number=candidate
        ).exists():
            return candidate
        seq += 1
    return candidate


KIND_PREFIX = {"sale": "S", "credit": "C", "quotation": "Q"}

# Payment method -> POS settings toggle (mirrors shops PAYMENT_METHOD_TOGGLES).
_PAYMENT_METHOD_TOGGLES = {
    "cash": "enable_cash",
    "mpesa": "enable_mpesa",
    "card": "enable_card",
    "both": "enable_cash_mpesa",
    "cash_card": "enable_cash_card",
    "mpesa_card": "enable_mpesa_card",
    "cash_mpesa_card": "enable_cash_mpesa_card",
}

# Payment method -> tender channels (mirrors shops PAYMENT_METHOD_CHANNELS).
_PAYMENT_CHANNELS = {
    "cash": ("cash",),
    "mpesa": ("mpesa",),
    "card": ("card",),
    "both": ("cash", "mpesa"),
    "cash_card": ("cash", "card"),
    "mpesa_card": ("mpesa", "card"),
    "cash_mpesa_card": ("cash", "mpesa", "card"),
}


@transaction.atomic
def create_sale(me, raw_lines, *, payment_method, cash_amount=None,
                mpesa_amount=None, card_amount=None,
                client_name="", client_phone="",
                kind="sale", credit_due_date=None, staff_code="",
                share_whatsapp: bool = False,
                require_member: bool = False, employee_id=None):
    """Returns (receipt, prepared_lines). kind ∈ sale|credit|quotation."""
    if kind not in KIND_PREFIX:
        raise StockError("Choose sale, credit, or quotation.")
    pos = effective_pos_settings(me)
    if not kind_enabled(pos, kind):
        raise StockError(f"{receipt_label(kind)} is disabled for this shop.")
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, me, require_member=require_member
        )
    assert_employee_may(employee_id, ("my-shop", kind))
    if kind in ("sale", "credit"):
        require_shop_day_for_sale(me, pos=pos)

    # Client rules mirror MY-SHOP checkout: name/phone fields only exist
    # while enable_client_data is on; required flags force them.
    collect_client = bool(pos.get("enable_client_data", True))
    client_name = (client_name or "").strip()
    client_phone = (client_phone or "").strip()
    if collect_client and pos.get("require_client_phone") and not client_phone:
        raise StockError("Enter the client phone number before completing.")
    if collect_client and pos.get("require_client_name") and not client_name:
        raise StockError("Enter the client full name before completing.")
    # Credit + quotation need client info — same rule as MY-SHOP checkout.
    if (
        collect_client
        and kind in ("credit", "quotation")
        and not client_name
        and not client_phone
    ):
        raise StockError(
            f"Enter the client name or phone for a {kind}."
        )
    if client_name or client_phone:
        upsert_client(client_name, client_phone)
    if kind == "credit" and credit_due_date:
        if credit_due_date < timezone.localdate():
            raise StockError("Payment due date cannot be in the past.")

    method = "none"
    if kind == "sale":
        method = (payment_method or "").strip().lower()
        if method not in _PAYMENT_CHANNELS:
            raise StockError("Choose a valid payment method.")
        if not payment_method_enabled(pos, method):
            raise StockError(
                "That payment method is not enabled for this shop."
            )

    track_store = bool(pos["enable_stock_tracking"])

    # Sale needs stock like a transfer — same line parsing.
    lines = _parse_lines(raw_lines, me, need_stock=True, sale_flow=True)

    price_rows = {
        r.item_id: r
        for r in ShopItemPrice.objects.filter(
            shop_id=me.pk,
            item_id__in=[l["item"].pk for l in lines],
        )
    }
    for line in lines:
        item = line["item"]
        pr = price_rows.get(item.pk)
        if pr is not None and not pr.is_sold:
            raise StockError(f'"{item.name}" is not sold at {me.name}.')
        if item.track_serial_number:
            # Serials required — verified available at the selling shop.
            line["serial_rows"] = _resolve_transfer_serials(item, me, line)
            line["serials"] = line["serial_rows"]
        price = line["buying_price"]
        if not pos.get("enable_discount", True) or price is None or price <= 0:
            # Discounts off → the list price always applies. An existing
            # per-shop row is authoritative — including 0.00, which means the
            # item is deliberately free at this shop.
            if pr is not None:
                price = _money(pr.price)
            else:
                price = _money(item.shop_price or item.minimum_selling_price)
        if item.minimum_selling_price and 0 < price < item.minimum_selling_price:
            price = _money(item.minimum_selling_price)
        unit_cost = _money(line["shop_stock"].average_cost if line.get("shop_stock") else 0)
        # Free sales (price 0) skip the below-cost guard.
        if price > 0 and unit_cost > 0 and price < unit_cost:
            raise StockError(
                f'"{item.name}" is below unit cost '
                f"(sell {price}, cost {unit_cost})."
            )
        line["price"] = price
        line["unit_cost"] = unit_cost
        line["line_total"] = (price * line["quantity"]).quantize(Decimal("0.01"))
        line["line_cogs"] = (unit_cost * line["quantity"]).quantize(Decimal("0.01"))

    total = sum((l["line_total"] for l in lines), Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    split = {"cash": Decimal("0.00"), "mpesa": Decimal("0.00"),
             "card": Decimal("0.00")}
    channels = _PAYMENT_CHANNELS.get(method, ())
    if len(channels) == 1:
        split[channels[0]] = total
    elif channels:
        entered = {
            "cash": _money(cash_amount),
            "mpesa": _money(mpesa_amount),
            "card": _money(card_amount),
        }
        paid = sum(entered[ch] for ch in channels)
        if paid != total:
            if abs(paid - total) > Decimal("0.01"):
                raise StockError(
                    "Payment amounts must add up to the sale total."
                )
            last = channels[-1]
            entered[last] = (entered[last] + (total - paid)).quantize(
                Decimal("0.01")
            )
        for ch in channels:
            if entered[ch] <= 0:
                raise StockError(
                    "For split payment all amounts must be > 0."
                )
            split[ch] = entered[ch]
    cash, mpesa, card = split["cash"], split["mpesa"], split["card"]

    receipt = ShopReceipt.objects.create(
        shop_id=me.pk,
        receipt_number=_next_receipt_number(
            me, pos["receipt_format_" + kind], kind=kind
        ),
        created_by_id=(employee_id or None),
        kind=kind,
        payment_method=method,
        client_name=client_name.upper(),
        client_phone=client_phone,
        subtotal=total,
        tax_percent=Decimal("0"),
        tax_amount=Decimal("0"),
        total=total,
        amount_paid=total if kind == "sale" else Decimal("0.00"),
        cash_amount=cash,
        mpesa_amount=mpesa,
        card_amount=card,
        credit_due_date=credit_due_date if kind == "credit" else None,
        share_whatsapp=share_whatsapp,
        # Cash sales stay pending until confirmed on the Receipts page;
        # credits / quotations are recorded as confirmed immediately.
        status="active" if kind == "sale" else "confirmed",
    )
    for line in lines:
        item = line["item"]
        ShopReceiptLine.objects.create(
            receipt_id=receipt.pk,
            item_id=item.pk,
            item_name=item.name,
            quantity=line["quantity"],
            returned_quantity=Decimal("0"),
            unit_price=line["price"],
            unit_cost=line["unit_cost"],
            line_total=line["line_total"],
            line_cogs=line["line_cogs"],
            serial_numbers=line.get("serials") or [],
        )
        if kind == "quotation":
            # Quotations never touch stock or serials.
            continue
        if getattr(item, "deduct_stock_on_sale", True):
            stock = line["shop_stock"]
            stock.quantity = _quantize_qty(stock.quantity) - line["quantity"]
            stock.save(update_fields=["quantity", "updated_at"])
            item.stock = max(ZERO_QTY, _quantize_qty(item.stock) - line["quantity"])
            item.save(update_fields=["stock"])
            if track_store:
                _mirror_store_movement(
                    item=item,
                    qty=line["quantity"],
                    mode="out",
                    employee_id=employee_id,
                    note=(
                        f"{receipt.receipt_number} · {receipt_label(kind)} · {me.name}"
                    ),
                )
        if item.track_serial_number:
            for s in ItemSerial.objects.select_for_update().filter(
                item_id=item.pk,
                shop_id=me.pk,
                serial_number__in=line["serials"],
                is_available=True,
            ):
                s.is_available = False
                s.save(update_fields=["is_available", "updated_at"])
    return receipt, lines


RECEIPT_LABEL = {
    "sale": "Sale",
    "credit": "Credit",
    "quotation": "Quotation",
    "trade_out": "Trade out",
    "trade_in": "Trade in",
}


def receipt_label(kind: str) -> str:
    return RECEIPT_LABEL.get(kind, "Receipt")


def sale_card(receipt, lines, total) -> dict:
    label = receipt_label(receipt.kind)
    return {
        "sale": {
            "receipt": receipt.receipt_number,
            "kind": receipt.kind,
            "label": label,
            "icon": "repeat" if receipt.kind in TRADE_KINDS else "shopping-cart",
            "total": str(total),
            "payment": PAYMENT_LABELS.get(
                receipt.payment_method, receipt.payment_method
            ),
            "client": receipt.client_name or "",
            "due": (
                receipt.credit_due_date.strftime("%d %b %Y")
                if receipt.credit_due_date
                else ""
            ),
            "items": [
                {
                    "name": l["item"].name,
                    "qty": str(l["quantity"]),
                    "price": str(l["price"]),
                    "total": str(l["line_total"]),
                    "serials": l.get("serials") or [],
                }
                for l in lines
            ],
        }
    }


def _quotation_text(receipt, lines) -> str:
    shop = Shop.objects.filter(pk=receipt.shop_id).first()
    rows = [f"QUOTATION {receipt.receipt_number}"]
    if shop and shop.name:
        rows.append(shop.name)
    rows.append("")
    for l in lines:
        rows.append(
            f"{_quantize_qty(l['quantity'])}× {l['item'].name}"
            f" @ {l['price']:,.2f} = {l['line_total']:,.2f}"
        )
    rows.append("")
    rows.append(f"Total: KSh {receipt.total:,.2f}")
    if receipt.client_name:
        rows.append(f"Client: {receipt.client_name}")
    return "\n".join(rows)


def quotation_whatsapp_url(receipt, lines, *, share_whatsapp: bool) -> str:
    """wa.me link prefilled with the quotation text.

    Mirrors MY-SHOP's checkout share — addressed to the client's phone, or
    the WhatsApp share picker when no phone was captured.
    """
    if not share_whatsapp or receipt.kind != "quotation":
        return ""
    from urllib.parse import quote

    dial, national = parse_supplier_phone(receipt.client_phone)
    number = dial.lstrip("+") + national if national else ""
    text = quote(_quotation_text(receipt, lines))
    return f"https://wa.me/{number}?text={text}"


# ---------------------------------------------------------------------------
# Receipts page — list / detail / confirm / cancel / return
# Mirrors MY-SHOP shops.services confirm_shop_receipt, cancel_pending_*,
# return_shop_receipt_items (without importing MY-SHOP).
# ---------------------------------------------------------------------------

STATUS_ACTIVE = "active"
STATUS_CONFIRMED = "confirmed"
STATUS_PARTIAL_RETURN = "partial_return"
STATUS_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATUS_ACTIVE: "Pending",
    STATUS_CONFIRMED: "Confirmed",
    STATUS_PARTIAL_RETURN: "Partially returned",
    STATUS_CANCELLED: "Cancelled",
}

KIND_LABELS = {
    "sale": "Sale",
    "credit": "Credit",
    "quotation": "Quotation",
    "trade_out": "Trade out",
    "trade_in": "Trade in",
}

PAYMENT_LABELS = {
    "cash": "Cash",
    "mpesa": "M-Pesa",
    "card": "Card",
    "both": "Cash + M-Pesa",
    "cash_card": "Cash + Card",
    "mpesa_card": "M-Pesa + Card",
    "cash_mpesa_card": "Cash + M-Pesa + Card",
    "credit": "Credit",
}

ENTRY_CUSTOMER_RETURN = "customer_return"

RETURNABLE_KINDS = ("sale", "credit", "trade_out")


def _line_remaining_qty(line) -> Decimal:
    return max(
        ZERO_QTY,
        _quantize_qty(line.quantity) - _quantize_qty(line.returned_quantity),
    )


def _line_remaining_serials(line) -> list:
    sold = [str(s).strip() for s in (line.serial_numbers or []) if str(s).strip()]
    returned = {
        str(s).strip()
        for s in (line.returned_serial_numbers or [])
        if str(s).strip()
    }
    return [s for s in sold if s not in returned]


def _qty_label(value) -> str:
    q = _quantize_qty(value)
    if q == q.to_integral_value():
        return str(int(q))
    return str(q.normalize())


def _parse_period_bounds(*, mode="", day="", date_from="", date_to="", month="", year=""):
    """Return (start_dt, end_dt) aware datetimes for receipt list filters."""
    from datetime import date, datetime, time

    mode = (mode or "day").strip().lower()
    today = timezone.localdate()
    tz = timezone.get_current_timezone()

    def aware(d, t):
        return timezone.make_aware(datetime.combine(d, t), tz)

    if mode == "period":
        try:
            start = date.fromisoformat((date_from or "").strip())
        except ValueError:
            start = today
        try:
            end = date.fromisoformat((date_to or "").strip())
        except ValueError:
            end = start
        if end < start:
            start, end = end, start
        return aware(start, time.min), aware(end, time.max)

    if mode == "month":
        raw = (month or "").strip()
        try:
            y, m = [int(p) for p in raw.split("-", 1)]
            start = date(y, m, 1)
        except (TypeError, ValueError):
            start = date(today.year, today.month, 1)
        if start.month == 12:
            end = date(start.year, 12, 31)
        else:
            end = date(start.year, start.month + 1, 1)
            from datetime import timedelta

            end = end - timedelta(days=1)
        return aware(start, time.min), aware(end, time.max)

    if mode == "year":
        try:
            y = int((year or "").strip() or today.year)
        except (TypeError, ValueError):
            y = today.year
        return aware(date(y, 1, 1), time.min), aware(date(y, 12, 31), time.max)

    # day (default)
    try:
        d = date.fromisoformat((day or "").strip())
    except ValueError:
        d = today
    return aware(d, time.min), aware(d, time.max)


def _receipt_list_row(receipt) -> dict:
    created = timezone.localtime(receipt.created_at)
    status = receipt.status or STATUS_ACTIVE
    kind = receipt.kind or "sale"
    credit_due = Decimal("0.00")
    if kind == "credit" and status != STATUS_CANCELLED:
        _paid, credit_due = _credit_paid_and_due(receipt)
    can_confirm = (
        kind == "sale" and status in (STATUS_ACTIVE, STATUS_PARTIAL_RETURN)
    ) or (
        kind == "credit" and status != STATUS_CANCELLED and credit_due > 0
    )
    can_cancel = kind == "sale" and status == STATUS_ACTIVE
    can_return = kind in RETURNABLE_KINDS and status != STATUS_CANCELLED
    return {
        "id": receipt.pk,
        "receipt_number": receipt.receipt_number,
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind.title()),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.replace("_", " ").title()),
        "payment_method": receipt.payment_method or "",
        "payment_label": PAYMENT_LABELS.get(
            receipt.payment_method or "", (receipt.payment_method or "").title()
        ),
        "client_name": receipt.client_name or "",
        "client_phone": receipt.client_phone or "",
        "total": str(receipt.total),
        "amount_paid": str(receipt.amount_paid),
        "credit_due": str(credit_due),
        "mpesa_receipt_number": receipt.mpesa_receipt_number or "",
        "created_at": created.isoformat(),
        "created_label": created.strftime("%d %b %Y · %H:%M"),
        "cashier": employee_label(receipt.created_by_id),
        "can_confirm": can_confirm,
        "can_cancel": can_cancel,
        "can_return": can_return,
    }


def _receipt_line_row(line) -> dict:
    remaining = _line_remaining_qty(line)
    remaining_serials = _line_remaining_serials(line)
    return {
        "id": line.pk,
        "item_id": line.item_id,
        "item_name": line.item_name,
        "quantity": str(_quantize_qty(line.quantity)),
        "returned_quantity": str(_quantize_qty(line.returned_quantity)),
        "remaining_quantity": str(remaining),
        "unit_price": str(line.unit_price),
        "line_total": str(line.line_total),
        "remaining_total": str(
            (_money(line.unit_price) * remaining).quantize(Decimal("0.01"))
        ),
        "serial_numbers": list(line.serial_numbers or []),
        "returned_serial_numbers": list(line.returned_serial_numbers or []),
        "remaining_serial_numbers": remaining_serials,
        "track_serial": bool(line.serial_numbers or remaining_serials),
    }


def list_receipts(
    shop,
    *,
    mode="day",
    day="",
    date_from="",
    date_to="",
    month="",
    year="",
    status="",
    kind="",
    query="",
    limit=200,
) -> list:
    """Shop-scoped POS receipts for the Receipts page."""
    start_dt, end_dt = _parse_period_bounds(
        mode=mode,
        day=day,
        date_from=date_from,
        date_to=date_to,
        month=month,
        year=year,
    )
    try:
        limit_n = max(1, min(int(limit or 200), 500))
    except (TypeError, ValueError):
        limit_n = 200

    qs = ShopReceipt.objects.filter(
        shop_id=shop.pk,
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).order_by("-created_at", "-id")

    status_key = (status or "").strip().lower()
    if status_key in ("pending", "active"):
        qs = qs.filter(status=STATUS_ACTIVE)
    elif status_key == "confirmed":
        qs = qs.filter(status__in=(STATUS_CONFIRMED, STATUS_PARTIAL_RETURN))
    elif status_key == "cancelled":
        qs = qs.filter(status=STATUS_CANCELLED)
    elif status_key in ("partial_return", "partial"):
        qs = qs.filter(status=STATUS_PARTIAL_RETURN)

    kind_key = (kind or "").strip().lower()
    if kind_key in ("sale", "credit", "quotation", "trade_out", "trade_in"):
        qs = qs.filter(kind=kind_key)
    elif kind_key in ("sales_credits", "sale_credit"):
        qs = qs.filter(kind__in=("sale", "credit"))

    q = (query or "").strip()
    if q:
        qs = qs.filter(
            Q(receipt_number__icontains=q)
            | Q(client_name__icontains=q)
            | Q(client_phone__icontains=q)
        )

    return [_receipt_list_row(r) for r in qs[:limit_n]]


def receipt_detail(shop, receipt_id: int) -> dict:
    receipt = ShopReceipt.objects.filter(pk=receipt_id, shop_id=shop.pk).first()
    if receipt is None:
        raise StockError("Receipt not found for this shop.")
    lines = list(
        ShopReceiptLine.objects.filter(receipt_id=receipt.pk).order_by("id")
    )
    line_rows = [_receipt_line_row(line) for line in lines]
    returnable = []
    if (
        receipt.kind in RETURNABLE_KINDS
        and receipt.status != STATUS_CANCELLED
    ):
        for line, payload in zip(lines, line_rows):
            if _line_remaining_qty(line) <= 0:
                continue
            if line.serial_numbers and not payload["remaining_serial_numbers"]:
                continue
            returnable.append(payload)
    row = _receipt_list_row(receipt)
    return {
        **row,
        "subtotal": str(receipt.subtotal),
        "tax_percent": str(receipt.tax_percent),
        "tax_amount": str(receipt.tax_amount),
        "cash_amount": str(receipt.cash_amount),
        "mpesa_amount": str(receipt.mpesa_amount),
        "card_amount": str(receipt.card_amount),
        "lines": line_rows,
        "returnable_lines": returnable,
    }


def _credit_paid_and_due(receipt):
    total = _money(receipt.total)
    paid = _money(receipt.amount_paid)
    due = total - paid
    if due < 0:
        due = Decimal("0.00")
    return paid, due


def _payment_method_for_channels(channels) -> str:
    used = frozenset(str(c) for c in channels if c)
    for method, chans in _PAYMENT_CHANNELS.items():
        if frozenset(chans) == used:
            return method
    return "none"


def _sync_sale_payment_fields(receipt) -> None:
    """Mirror credit_settlement._sync_sale_payment_fields."""
    used = [
        channel
        for channel in ("cash", "mpesa", "card")
        if _money(getattr(receipt, f"{channel}_amount", 0)) > 0
    ]
    if used:
        receipt.payment_method = _payment_method_for_channels(used)
        return
    paid = _money(receipt.amount_paid)
    if (receipt.mpesa_receipt_number or "").strip():
        receipt.payment_method = "mpesa"
        receipt.mpesa_amount = paid
        return
    receipt.payment_method = "cash"
    receipt.cash_amount = paid


def _convert_settled_credit_to_sale(receipt) -> bool:
    """Mirror credit_settlement.convert_settled_credit_to_sale (no save)."""
    if getattr(receipt, "kind", None) != "credit":
        return False
    if getattr(receipt, "status", None) == STATUS_CANCELLED:
        return False
    _paid, due = _credit_paid_and_due(receipt)
    if due > 0:
        return False
    paid = _money(receipt.amount_paid)
    if paid <= 0 and _money(receipt.total) <= 0:
        return False
    receipt.kind = "sale"
    receipt.credit_due_date = None
    receipt.settled_from_credit = True
    _sync_sale_payment_fields(receipt)
    return True


def _record_credit_collection(
    receipt, *, amount, payment_method: str, mpesa_receipt_number: str = ""
) -> bool:
    """Mirror credit_settlement.record_credit_collection — applies a payment
    and converts the receipt to a sale when fully settled."""
    apply = _money(amount)
    method = (payment_method or "cash").strip().lower()
    receipt.amount_paid = _money(Decimal(receipt.amount_paid or 0) + apply)
    update_fields = ["amount_paid"]
    if method == "mpesa":
        receipt.mpesa_amount = _money(
            Decimal(receipt.mpesa_amount or 0) + apply
        )
        update_fields.append("mpesa_amount")
        ref = (mpesa_receipt_number or "").strip()
        if ref and not (receipt.mpesa_receipt_number or "").strip():
            receipt.mpesa_receipt_number = ref
            update_fields.append("mpesa_receipt_number")
    elif method == "card":
        receipt.card_amount = _money(
            Decimal(getattr(receipt, "card_amount", 0) or 0) + apply
        )
        update_fields.append("card_amount")
    else:
        receipt.cash_amount = _money(Decimal(receipt.cash_amount or 0) + apply)
        update_fields.append("cash_amount")

    converted = _convert_settled_credit_to_sale(receipt)
    if converted:
        for field in (
            "kind",
            "payment_method",
            "credit_due_date",
            "cash_amount",
            "mpesa_amount",
            "card_amount",
            "settled_from_credit",
        ):
            if field not in update_fields:
                update_fields.append(field)
    receipt.save(update_fields=update_fields)
    return converted


def _parse_payment_at(value):
    """Accept an ISO datetime string, a date, or None."""
    if value in (None, ""):
        return None
    if hasattr(value, "tzinfo"):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = timezone.datetime.fromisoformat(text)
        except ValueError:
            raise StockError("Enter a valid payment date and time.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _reopen_cancelled_receipt(shop, receipt, *, pos):
    """Mirror reopen_cancelled_shop_receipt(target=active): a cancelled sale
    goes back to pending — the cancel-time restock is reversed."""
    if receipt.kind != "sale":
        raise StockError("Only sales receipts can be reopened here.")
    lines = list(
        ShopReceiptLine.objects.select_for_update().filter(
            receipt_id=receipt.pk
        )
    )
    track = bool(pos.get("enable_stock_tracking", True))
    item_ids = {line.item_id for line in lines if line.item_id}
    items = {
        i.pk: i
        for i in Item.objects.select_for_update().filter(pk__in=item_ids)
    }
    stock_map = {
        s.item_id: s
        for s in ShopStock.objects.select_for_update().filter(
            shop_id=shop.pk, item_id__in=item_ids
        )
    }
    for line in lines:
        returned = _quantize_qty(line.returned_quantity or 0)
        returned_serials = list(line.returned_serial_numbers or [])
        if returned <= 0 and not returned_serials:
            continue
        item = items.get(line.item_id)
        if track and item is not None and getattr(
            item, "deduct_stock_on_sale", True
        ):
            qty = returned or _quantize_qty(len(returned_serials))
            stock = stock_map.get(line.item_id)
            if stock is None:
                stock = ShopStock.objects.create(
                    shop_id=shop.pk, item_id=line.item_id, quantity=0
                )
                stock_map[line.item_id] = stock
            stock.quantity = _quantize_qty(stock.quantity) - qty
            stock.save(update_fields=["quantity"])
            item.stock = _quantize_qty(item.stock or 0) - qty
            item.save(update_fields=["stock"])
            if returned_serials:
                ItemSerial.objects.filter(
                    item_id=line.item_id,
                    serial_number__in=returned_serials,
                ).update(is_available=False)
        line.returned_quantity = Decimal("0")
        line.returned_serial_numbers = []
        line.save(
            update_fields=["returned_quantity", "returned_serial_numbers"]
        )
    receipt.status = STATUS_ACTIVE
    receipt.save(update_fields=["status"])


def _confirm_one_receipt(
    shop,
    receipt,
    *,
    employee_id,
    pos,
    mpesa_receipt_number="",
    payment_at=None,
    amount=None,
    payment_method="",
    cash_amount=None,
    mpesa_amount=None,
    card_amount=None,
) -> dict:
    """Mirror shops.services.confirm_shop_receipt (sale + credit + gift)."""
    is_credit = receipt.kind == "credit"
    is_gift = receipt.kind == "gift"
    if not (is_credit or is_gift) and receipt.kind != "sale":
        raise StockError("Only sales, credits, and gifts can be confirmed.")
    if receipt.status == STATUS_CANCELLED:
        _reopen_cancelled_receipt(shop, receipt, pos=pos)
    elif receipt.status == STATUS_CONFIRMED and not is_credit:
        return {
            "ok": True,
            "message": f"Receipt {receipt.receipt_number} is already confirmed.",
            "receipt": receipt_detail(shop, receipt.pk),
            "receipt_number": receipt.receipt_number,
        }
    elif receipt.status not in (STATUS_ACTIVE, STATUS_PARTIAL_RETURN):
        raise StockError(
            "Only pending, cancelled, or partially returned receipts can be "
            "confirmed."
        )

    keep_partial_return = receipt.status == STATUS_PARTIAL_RETURN

    ref = (mpesa_receipt_number or "").strip()[:40]
    existing_ref = (receipt.mpesa_receipt_number or "").strip()
    require_details = bool(pos.get("require_mpesa_reference_on_confirm"))
    # A reference only makes sense for M-Pesa/Card channels — a cash-only
    # confirm has nothing to reference.
    if is_credit:
        ref_method = (payment_method or "").strip().lower()
        ref_applies = ref_method in {"mpesa", "card"} or (
            ref_method != "cash" and bool(ref or existing_ref)
        )
    else:
        confirm_method = (payment_method or "").strip().lower() or (
            receipt.payment_method or ""
        ).strip().lower()
        ref_applies = any(
            channel != "cash"
            for channel in _PAYMENT_CHANNELS.get(confirm_method, ())
        )
    if (
        require_details
        and ref_applies
        and not ref
        and not existing_ref
        and not is_gift
    ):
        raise StockError(
            "Enter the reference number before confirming this receipt."
        )
    paid_at = _parse_payment_at(payment_at)
    if require_details and paid_at is None and not is_gift:
        raise StockError(
            "Enter the payment date and time before confirming this receipt."
        )

    cleared_credit = False
    partial_credit = False
    if is_credit:
        _paid, due = _credit_paid_and_due(receipt)
        if due <= 0:
            apply_amount = Decimal("0.00")
        elif amount in (None, ""):
            # The compact chat confirm carries no amount field — a bare
            # confirm settles the whole balance.
            apply_amount = due
        else:
            apply_amount = _money(amount)
            if apply_amount <= 0:
                raise StockError("Confirm amount must be greater than zero.")
            if apply_amount > due:
                raise StockError(
                    f"Confirm amount cannot exceed the balance due "
                    f"(KSh {due:,.2f})."
                )
        method = (payment_method or "").strip().lower()
        if method not in {"cash", "mpesa", "card"}:
            method = "mpesa" if ref or existing_ref else "cash"
        if apply_amount > 0:
            if not payment_method_enabled(pos, method):
                raise StockError(
                    f"{method.capitalize()} payments are disabled for this "
                    "shop."
                )
            cleared_credit = _record_credit_collection(
                receipt,
                amount=apply_amount,
                payment_method=method,
                mpesa_receipt_number=ref,
            )
            receipt = ShopReceipt.objects.select_for_update().get(
                pk=receipt.pk
            )
        else:
            cleared_credit = _convert_settled_credit_to_sale(receipt)
            if cleared_credit:
                receipt.save(
                    update_fields=[
                        "kind",
                        "payment_method",
                        "credit_due_date",
                        "cash_amount",
                        "mpesa_amount",
                        "card_amount",
                        "settled_from_credit",
                    ]
                )

        if not cleared_credit and receipt.kind == "credit":
            # Partial confirmation — keep as open credit with updated balance.
            partial_credit = True
            if ref and not (receipt.mpesa_receipt_number or "").strip():
                receipt.mpesa_receipt_number = ref
                receipt.save(update_fields=["mpesa_receipt_number"])
            if paid_at is not None:
                receipt.confirmed_payment_at = paid_at
                receipt.save(update_fields=["confirmed_payment_at"])
            _paid_now, due_now = _credit_paid_and_due(receipt)
            return {
                "ok": True,
                "message": (
                    f"Credit {receipt.receipt_number} partially confirmed "
                    f"(paid KSh {_paid_now:,.2f}, balance KSh {due_now:,.2f})."
                ),
                "receipt": receipt_detail(shop, receipt.pk),
                "receipt_number": receipt.receipt_number,
                "partial_credit": True,
            }
        if not cleared_credit and receipt.kind != "sale":
            raise StockError("Could not clear this credit receipt.")

    receipt.status = (
        STATUS_PARTIAL_RETURN if keep_partial_return else STATUS_CONFIRMED
    )
    update_fields = ["status"]
    if not is_credit and not is_gift:
        # The confirmer may correct how the sale was paid (incl. card combos).
        method_key = (payment_method or "").strip().lower()
        if method_key:
            if method_key not in _PAYMENT_CHANNELS:
                raise StockError("Choose an enabled payment method.")
            if not payment_method_enabled(pos, method_key):
                raise StockError(
                    "That payment method is not enabled for this shop."
                )
            total = _money(receipt.total)
            channels = _PAYMENT_CHANNELS[method_key]
            receipt.cash_amount = Decimal("0.00")
            receipt.mpesa_amount = Decimal("0.00")
            receipt.card_amount = Decimal("0.00")
            if len(channels) == 1:
                setattr(receipt, f"{channels[0]}_amount", total)
            else:
                entered = {
                    "cash": _money(cash_amount),
                    "mpesa": _money(mpesa_amount),
                    "card": _money(card_amount),
                }
                paid = sum(entered[ch] for ch in channels)
                if paid != total:
                    if abs(paid - total) > Decimal("0.01"):
                        raise StockError(
                            "Payment amounts must add up to the sale total."
                        )
                    last = channels[-1]
                    entered[last] = (entered[last] + (total - paid)).quantize(
                        Decimal("0.01")
                    )
                for ch in channels:
                    if entered[ch] <= 0:
                        raise StockError(
                            "For split payment all amounts must be > 0."
                        )
                    setattr(receipt, f"{ch}_amount", entered[ch])
            receipt.payment_method = method_key
            update_fields += [
                "payment_method",
                "cash_amount",
                "mpesa_amount",
                "card_amount",
            ]
    if cleared_credit:
        for field in (
            "kind",
            "payment_method",
            "credit_due_date",
            "cash_amount",
            "mpesa_amount",
            "card_amount",
            "settled_from_credit",
        ):
            if field not in update_fields:
                update_fields.append(field)
    if ref and not is_gift:
        receipt.mpesa_receipt_number = ref
        update_fields.append("mpesa_receipt_number")
    if paid_at is not None and not is_gift:
        receipt.confirmed_payment_at = paid_at
        update_fields.append("confirmed_payment_at")
    receipt.save(update_fields=update_fields)

    message = f"Receipt {receipt.receipt_number} confirmed."
    if cleared_credit:
        message = (
            f"Credit {receipt.receipt_number} cleared fully and confirmed "
            "as a sale."
        )
    return {
        "ok": True,
        "message": message,
        "receipt": receipt_detail(shop, receipt.pk),
        "receipt_number": receipt.receipt_number,
        "cleared_credit": cleared_credit,
        "partial_credit": partial_credit,
    }


@transaction.atomic
def confirm_receipt(
    shop,
    receipt_id: int,
    *,
    staff_code="",
    mpesa_receipt_number="",
    payment_at=None,
    amount=None,
    payment_method="",
    cash_amount=None,
    mpesa_amount=None,
    card_amount=None,
) -> dict:
    employee_id = verify_employee_code(
        staff_code, shop, require_member=True
    )
    assert_employee_may(
        employee_id,
        ("my-shop", "receipts"),
        ("analytics", "confirm-receipts"),
    )
    receipt = (
        ShopReceipt.objects.select_for_update()
        .filter(pk=receipt_id, shop_id=shop.pk)
        .first()
    )
    if receipt is None:
        raise StockError("Receipt not found for this shop.")
    out = _confirm_one_receipt(
        shop,
        receipt,
        employee_id=employee_id,
        pos=effective_pos_settings(shop),
        mpesa_receipt_number=mpesa_receipt_number,
        payment_at=payment_at,
        amount=amount,
        payment_method=payment_method,
        cash_amount=cash_amount,
        mpesa_amount=mpesa_amount,
        card_amount=card_amount,
    )
    out["by"] = employee_label(employee_id)
    return out


def _append_return_batch(line, *, qty, serials, at, by_id, reason="") -> None:
    batches = [
        b for b in (line.return_batches or []) if isinstance(b, dict)
    ]
    batch = {
        "qty": float(_quantize_qty(qty)),
        "at": at.isoformat(),
        "by_id": by_id,
        "serials": [str(s).strip() for s in (serials or []) if str(s).strip()],
    }
    reason_text = (reason or "").strip()
    if reason_text:
        batch["reason"] = reason_text
    batches.append(batch)
    line.return_batches = batches


def _append_return_payment_event(
    receipt, *, at, cash, mpesa, by_id, card=0
) -> bool:
    cash = _money(cash)
    mpesa = _money(mpesa)
    card = _money(card)
    if cash <= 0 and mpesa <= 0 and card <= 0:
        return False
    events = [
        e for e in (receipt.return_payment_events or []) if isinstance(e, dict)
    ]
    events.append(
        {
            "at": at.isoformat(),
            "cash": str(cash),
            "mpesa": str(mpesa),
            "card": str(card),
            "by_id": by_id,
        }
    )
    receipt.return_payment_events = events
    return True


def _create_return_stock_movement(
    *, shop, receipt, employee_id, occurred_at, lines, reason=""
):
    prepared = [
        row
        for row in lines
        if row.get("item") is not None and _quantize_qty(row.get("qty")) > 0
    ]
    if not prepared:
        return None
    reason_text = (reason or "").strip()
    notes = f"Customer return on {receipt.receipt_number}"
    if reason_text:
        notes = f"{notes}: {reason_text}"
    movement = StockMovement.objects.create(
        movement_type=MOVEMENT_IN,
        entry_source=ENTRY_CUSTOMER_RETURN,
        shop_id=shop.pk,
        created_by_id=(employee_id or None),
        notes=notes,
        supplier_notified=True,
    )
    StockMovement.objects.filter(pk=movement.pk).update(created_at=occurred_at)
    for row in prepared:
        item = row["item"]
        qty = _quantize_qty(row["qty"])
        unit_cost = _money(row.get("unit_cost"))
        serials = [
            str(s).strip()
            for s in (row.get("serial_numbers") or [])
            if str(s).strip()
        ]
        line_note = f"Return on {receipt.receipt_number}"
        if reason_text:
            line_note = f"{line_note}: {reason_text}"
        StockMovementLine.objects.create(
            movement_id=movement.pk,
            item_id=item.pk,
            item_name=item.name,
            quantity=qty,
            buying_price=unit_cost,
            unit_cost=unit_cost,
            note=line_note,
            serial_numbers=serials,
        )
    return movement


@transaction.atomic
def return_receipt_items(
    shop, receipt_id: int, *, lines_payload, reason="", staff_code="", employee_id=None
) -> dict:
    """Partial or full return of sale/credit/trade_out lines. Restocks inventory."""
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, shop, require_member=True
        )
    assert_employee_may(
        employee_id,
        ("my-shop", "return_receipt"),
        ("analytics", "return-receipts"),
    )
    reason = (reason or "").strip()
    if not reason:
        raise StockError("Enter a reason for the return.")
    if len(reason) > 500:
        raise StockError("Return reason must be 500 characters or fewer.")

    receipt = (
        ShopReceipt.objects.select_for_update()
        .filter(pk=receipt_id, shop_id=shop.pk)
        .first()
    )
    if receipt is None:
        raise StockError("Receipt not found for this shop.")
    if receipt.kind == "quotation":
        raise StockError("Quotations cannot be returned.")
    if receipt.kind == "trade_in":
        raise StockError("Trade-in returns are not supported here.")
    if receipt.status == STATUS_CANCELLED:
        raise StockError("This receipt is already fully returned.")

    raw_lines = lines_payload or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise StockError("Select at least one item to return.")

    lines_by_id = {
        line.pk: line
        for line in ShopReceiptLine.objects.select_for_update().filter(
            receipt_id=receipt.pk
        )
    }
    prepared = []
    item_ids = set()

    for raw in raw_lines:
        try:
            line_id = int(raw.get("line_id") or raw.get("id") or 0)
        except (TypeError, ValueError):
            line_id = 0
        qty = _quantize_qty(raw.get("quantity") or raw.get("qty") or 0)
        serials = [
            str(s).strip()
            for s in (raw.get("serials") or raw.get("serial_numbers") or [])
            if str(s).strip()
        ]
        if serials and qty <= 0:
            qty = _quantize_qty(len(serials))
        elif serials and qty != _quantize_qty(len(serials)):
            raise StockError("Serial-tracked returns need one serial per unit.")
        if line_id <= 0 or qty <= 0:
            raise StockError("Each return line needs a valid item and quantity.")
        line = lines_by_id.get(line_id)
        if line is None:
            raise StockError(f"Receipt line #{line_id} was not found.")
        remaining = _line_remaining_qty(line)
        if qty > remaining:
            raise StockError(
                f"“{line.item_name}”: only {_qty_label(remaining)} unit(s) left to return."
            )
        remaining_serials = _line_remaining_serials(line)
        if remaining_serials:
            if not serials:
                raise StockError(
                    f"“{line.item_name}” requires serial numbers to return."
                )
            if len(serials) != int(qty) and _quantize_qty(len(serials)) != qty:
                raise StockError(
                    f"“{line.item_name}”: return quantity must match serial count."
                )
            missing = [s for s in serials if s not in remaining_serials]
            if missing:
                raise StockError(
                    f"“{line.item_name}”: serial not on this receipt ({', '.join(missing[:5])})."
                )
        else:
            serials = []

        serial_objects = {}
        if serials:
            if not line.item_id:
                raise StockError(
                    f"“{line.item_name}”: cannot verify serial numbers for this line."
                )
            found = {
                row.serial_number: row
                for row in ItemSerial.objects.select_for_update().filter(
                    item_id=line.item_id,
                    serial_number__in=serials,
                )
            }
            not_in_system = [s for s in serials if s not in found]
            if not_in_system:
                raise StockError(
                    f"“{line.item_name}”: serial not in the system "
                    f"({', '.join(not_in_system[:5])})."
                )
            already = [s for s in serials if found[s].is_available]
            if already:
                raise StockError(
                    f"“{line.item_name}”: serial already in stock "
                    f"({', '.join(already[:5])}). Cannot return."
                )
            serial_objects = found

        if line.item_id:
            item_ids.add(line.item_id)
        prepared.append(
            {
                "line": line,
                "qty": qty,
                "serial_numbers": serials,
                "serial_objects": serial_objects,
            }
        )

    if not prepared:
        raise StockError("Select at least one valid item to return.")

    items_by_id = {
        i.pk: i
        for i in Item.objects.select_for_update().filter(pk__in=item_ids)
    }
    stock_by_item = {
        row.item_id: row
        for row in ShopStock.objects.select_for_update().filter(
            shop_id=shop.pk, item_id__in=item_ids
        )
    }
    missing_stock = [iid for iid in item_ids if iid not in stock_by_item]
    for iid in missing_stock:
        stock_by_item[iid] = ShopStock.objects.create(
            shop_id=shop.pk, item_id=iid, quantity=ZERO_QTY, average_cost=Decimal("0")
        )

    now = timezone.now()
    movement_lines = []

    for row in prepared:
        line = row["line"]
        qty = row["qty"]
        serials = row["serial_numbers"]
        line.returned_quantity = _quantize_qty(line.returned_quantity) + qty
        if serials:
            existing = [
                str(s).strip()
                for s in (line.returned_serial_numbers or [])
                if str(s).strip()
            ]
            line.returned_serial_numbers = existing + serials
        remaining_after = _line_remaining_qty(line)
        line.line_total = (_money(line.unit_price) * remaining_after).quantize(
            Decimal("0.01")
        )
        _append_return_batch(
            line,
            qty=qty,
            serials=serials,
            at=now,
            by_id=employee_id,
            reason=reason,
        )
        line.save(
            update_fields=[
                "returned_quantity",
                "returned_serial_numbers",
                "return_batches",
                "line_total",
            ]
        )

        item = items_by_id.get(line.item_id) if line.item_id else None
        if item is None:
            continue
        stock = stock_by_item.get(item.pk)
        if stock is None:
            continue
        if getattr(item, "deduct_stock_on_sale", True):
            return_unit_cost = _money(line.unit_cost)
            stock.average_cost = weighted_average_cost(
                old_qty=stock.quantity,
                old_avg=stock.average_cost,
                in_qty=qty,
                in_price=return_unit_cost,
            )
            stock.quantity = _quantize_qty(stock.quantity) + qty
            stock.save(update_fields=["quantity", "average_cost", "updated_at"])
            item.stock = _quantize_qty(item.stock) + qty
            item.save(update_fields=["stock"])
            movement_lines.append(
                {
                    "item": item,
                    "qty": qty,
                    "unit_cost": return_unit_cost,
                    "serial_numbers": serials,
                }
            )
        if serials:
            for serial_no in serials:
                obj = row["serial_objects"].get(serial_no)
                if obj is None:
                    continue
                obj.is_available = True
                obj.shop_id = shop.pk
                obj.save(update_fields=["is_available", "shop_id", "updated_at"])

    _create_return_stock_movement(
        shop=shop,
        receipt=receipt,
        employee_id=employee_id,
        occurred_at=now,
        lines=movement_lines,
        reason=reason,
    )

    all_lines = list(
        ShopReceiptLine.objects.filter(receipt_id=receipt.pk).order_by("id")
    )
    remaining_subtotal = sum(
        (
            (_money(line.unit_price) * _line_remaining_qty(line)).quantize(
                Decimal("0.01")
            )
            for line in all_lines
        ),
        Decimal("0.00"),
    )
    tax_percent = _money(receipt.tax_percent)
    prior_cash = _money(receipt.cash_amount)
    prior_mpesa = _money(receipt.mpesa_amount)
    prior_card = _money(getattr(receipt, "card_amount", 0) or 0)

    if remaining_subtotal <= 0:
        tax_amount = Decimal("0.00")
        total = Decimal("0.00")
        cash_amount = Decimal("0.00")
        mpesa_amount = Decimal("0.00")
        card_amount = Decimal("0.00")
        status = STATUS_CANCELLED
    else:
        tax_amount = (remaining_subtotal * tax_percent / Decimal("100")).quantize(
            Decimal("0.01")
        )
        total = (remaining_subtotal + tax_amount).quantize(Decimal("0.01"))
        any_returned = any(
            _quantize_qty(line.returned_quantity) > 0 for line in all_lines
        )
        if any_returned:
            status = STATUS_PARTIAL_RETURN
        elif receipt.status == STATUS_CONFIRMED:
            status = STATUS_CONFIRMED
        else:
            status = STATUS_ACTIVE
        cash_amount = receipt.cash_amount
        mpesa_amount = receipt.mpesa_amount
        card_amount = getattr(receipt, "card_amount", 0) or Decimal("0.00")
        if receipt.kind == "sale":
            channels = _PAYMENT_CHANNELS.get(
                receipt.payment_method or "cash", ()
            )
            tender = {
                "cash": _money(cash_amount),
                "mpesa": _money(mpesa_amount),
                "card": _money(card_amount),
            }
            if len(channels) == 1:
                tender = {ch: Decimal("0.00") for ch in tender}
                tender[channels[0]] = total
            elif channels:
                old_total = _money(receipt.total)
                scaled = {ch: Decimal("0.00") for ch in tender}
                if old_total > 0:
                    ratio = total / old_total
                    for ch in channels[:-1]:
                        scaled[ch] = (tender[ch] * ratio).quantize(
                            Decimal("0.01")
                        )
                    scaled[channels[-1]] = (
                        total - sum(scaled.values())
                    ).quantize(Decimal("0.01"))
                else:
                    scaled[channels[-1]] = total
                tender = scaled
            else:
                tender = {ch: Decimal("0.00") for ch in tender}
            cash_amount = tender["cash"]
            mpesa_amount = tender["mpesa"]
            card_amount = tender["card"]
        elif receipt.kind not in ("credit", "trade_out"):
            cash_amount = Decimal("0.00")
            mpesa_amount = Decimal("0.00")
            card_amount = Decimal("0.00")

    receipt.subtotal = remaining_subtotal
    receipt.tax_amount = tax_amount
    receipt.total = total
    receipt.cash_amount = cash_amount
    receipt.mpesa_amount = mpesa_amount
    receipt.card_amount = card_amount
    receipt.status = status
    receipt.last_returned_at = now
    receipt.last_returned_by_id = employee_id
    update_fields = [
        "subtotal",
        "tax_amount",
        "total",
        "cash_amount",
        "mpesa_amount",
        "card_amount",
        "status",
        "last_returned_at",
        "last_returned_by_id",
    ]
    if receipt.kind == "sale" and _append_return_payment_event(
        receipt,
        at=now,
        cash=prior_cash - _money(cash_amount),
        mpesa=prior_mpesa - _money(mpesa_amount),
        card=prior_card - _money(card_amount),
        by_id=employee_id,
    ):
        update_fields.append("return_payment_events")
    paid = _money(receipt.amount_paid)
    if paid > total:
        receipt.amount_paid = total
        update_fields.append("amount_paid")
    receipt.save(update_fields=update_fields)

    returned_units = _qty_label(
        sum((row["qty"] for row in prepared), Decimal("0"))
    )
    if status == STATUS_CANCELLED:
        summary = (
            f"Receipt {receipt.receipt_number} fully returned and cancelled "
            f"({returned_units} unit(s) restocked)."
        )
    else:
        summary = (
            f"Returned {returned_units} unit(s) on {receipt.receipt_number}. "
            f"Remaining total KSh {total}."
        )
    return {
        "ok": True,
        "message": summary,
        "receipt": receipt_detail(shop, receipt.pk),
        "by": employee_label(employee_id),
    }


@transaction.atomic
def cancel_pending_receipt(
    shop, receipt_id: int, *, staff_code="", employee_id=None
) -> dict:
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, shop, require_member=True
        )
    assert_employee_may(
        employee_id,
        ("my-shop", "return_receipt"),
        ("analytics", "return-receipts"),
        ("analytics", "confirm-receipts"),
    )
    receipt = (
        ShopReceipt.objects.select_for_update()
        .filter(pk=receipt_id, shop_id=shop.pk)
        .first()
    )
    if receipt is None:
        raise StockError("Receipt not found for this shop.")
    if receipt.kind != "sale":
        raise StockError("Only sales receipts can be cancelled here.")
    if receipt.status != STATUS_ACTIVE:
        raise StockError("Only pending receipts can be cancelled here.")

    lines_payload = []
    for line in ShopReceiptLine.objects.filter(receipt_id=receipt.pk):
        remaining = _line_remaining_qty(line)
        if remaining <= 0:
            continue
        serials = list(_line_remaining_serials(line))
        entry = {"line_id": line.pk, "quantity": str(remaining)}
        if serials:
            entry["serial_numbers"] = serials
        lines_payload.append(entry)

    if not lines_payload:
        now = timezone.now()
        receipt.status = STATUS_CANCELLED
        receipt.last_returned_at = now
        receipt.last_returned_by_id = employee_id
        receipt.save(
            update_fields=["status", "last_returned_at", "last_returned_by_id"]
        )
        return {
            "ok": True,
            "message": f"Receipt {receipt.receipt_number} cancelled.",
            "receipt": receipt_detail(shop, receipt.pk),
            "by": employee_label(employee_id),
        }

    receipt_pk = receipt.pk
    number = receipt.receipt_number
    del receipt
    out = return_receipt_items(
        shop,
        receipt_pk,
        lines_payload=lines_payload,
        reason="Receipt cancelled",
        employee_id=employee_id,
    )
    out["message"] = f"Receipt {number} cancelled."
    return out


@transaction.atomic
def confirm_receipts_bulk(
    shop,
    ids,
    *,
    staff_code="",
    mpesa_receipt_number="",
    payment_at=None,
    amount=None,
    payment_method="",
    cash_amount=None,
    mpesa_amount=None,
    card_amount=None,
) -> dict:
    employee_id = verify_employee_code(
        staff_code, shop, require_member=True
    )
    assert_employee_may(
        employee_id,
        ("my-shop", "receipts"),
        ("analytics", "confirm-receipts"),
    )
    pos = effective_pos_settings(shop)
    results = []
    messages = []
    errors = []
    for rid in ids or []:
        try:
            rid_i = int(rid)
        except (TypeError, ValueError):
            errors.append(f"Invalid receipt id: {rid}")
            continue
        receipt = (
            ShopReceipt.objects.select_for_update()
            .filter(pk=rid_i, shop_id=shop.pk)
            .first()
        )
        if receipt is None:
            errors.append(f"Receipt #{rid_i} not found.")
            continue
        try:
            out = _confirm_one_receipt(
                shop,
                receipt,
                employee_id=employee_id,
                pos=pos,
                mpesa_receipt_number=mpesa_receipt_number,
                payment_at=payment_at,
                amount=amount,
                payment_method=payment_method,
                cash_amount=cash_amount,
                mpesa_amount=mpesa_amount,
                card_amount=card_amount,
            )
        except StockError as exc:
            errors.append(str(exc))
            continue
        results.append(out.get("receipt_number") or receipt.receipt_number)
        messages.append(out.get("message") or "")
    if not results and errors:
        raise StockError(
            errors[0] if len(errors) == 1 else "; ".join(errors[:3])
        )
    return {
        "ok": True,
        "message": messages[0] if len(results) == 1 and messages else
        f"Confirmed {len(results)} receipt(s).",
        "confirmed": results,
        "errors": errors,
        "by": employee_label(employee_id),
    }


def cancel_receipts_bulk(shop, ids, *, staff_code="") -> dict:
    employee_id = verify_employee_code(
        staff_code, shop, require_member=True
    )
    results = []
    errors = []
    for rid in ids or []:
        try:
            rid_i = int(rid)
        except (TypeError, ValueError):
            errors.append(f"Invalid receipt id: {rid}")
            continue
        try:
            out = cancel_pending_receipt(
                shop, rid_i, employee_id=employee_id
            )
            results.append(
                (out.get("receipt") or {}).get("receipt_number") or str(rid_i)
            )
        except StockError as exc:
            errors.append(str(exc))
    if not results and errors:
        raise StockError(
            errors[0] if len(errors) == 1 else "; ".join(errors[:3])
        )
    return {
        "ok": True,
        "message": f"Cancelled {len(results)} receipt(s).",
        "cancelled": results,
        "errors": errors,
        "by": employee_label(employee_id),
    }


def return_receipts_bulk(shop, receipts_payload, *, reason="", staff_code="") -> dict:
    employee_id = verify_employee_code(
        staff_code, shop, require_member=True
    )
    results = []
    errors = []
    for entry in receipts_payload or []:
        try:
            rid = int(entry.get("id") or entry.get("receipt_id") or 0)
        except (TypeError, ValueError):
            rid = 0
        if rid <= 0:
            errors.append("Each return needs a receipt id.")
            continue
        try:
            out = return_receipt_items(
                shop,
                rid,
                lines_payload=entry.get("lines") or [],
                reason=reason or entry.get("reason") or "",
                employee_id=employee_id,
            )
            results.append(
                (out.get("receipt") or {}).get("receipt_number") or str(rid)
            )
        except StockError as exc:
            errors.append(str(exc))
    if not results and errors:
        raise StockError(errors[0] if len(errors) == 1 else "; ".join(errors[:3]))
    return {
        "ok": True,
        "message": f"Returned items on {len(results)} receipt(s).",
        "returned": results,
        "errors": errors,
        "by": employee_label(employee_id),
    }


# ---------------------------------------------------------------------------
# Trade — mirrors shops.services.complete_shop_checkout for the trade kinds.
#
# Trade out: the client takes items now and owes their value — stock and
# serials leave like a sale, the receipt stays open until MY-SHOP settles it
# (payment or exchange). Trade in: the client brings items — stock grows and
# the receipt records what the shop owes them.
# ---------------------------------------------------------------------------

TRADE_KINDS = ("trade_out", "trade_in")

_POS_BOOL_FIELDS = (
    "enable_sale",
    "enable_credit",
    "enable_quotation",
    "enable_trade_out",
    "enable_trade_in",
    "enable_cash",
    "enable_mpesa",
    "enable_card",
    "enable_cash_mpesa",
    "enable_cash_card",
    "enable_mpesa_card",
    "enable_cash_mpesa_card",
    "enable_discount",
    "enable_stock_tracking",
    "enable_shelf_mode",
    "enable_open_close",
    "enable_client_data",
    "require_client_phone",
    "require_client_name",
    "require_mpesa_reference_on_confirm",
)
_POS_FORMAT_FIELDS = (
    "receipt_format_sale",
    "receipt_format_credit",
    "receipt_format_quotation",
    "receipt_format_trade_out",
    "receipt_format_trade_in",
)
_POS_DEFAULTS = {
    "enable_sale": True,
    "enable_credit": True,
    "enable_quotation": True,
    "enable_trade_out": True,
    "enable_trade_in": True,
    "enable_cash": True,
    "enable_mpesa": True,
    "enable_card": False,
    "enable_cash_mpesa": True,
    "enable_cash_card": False,
    "enable_mpesa_card": False,
    "enable_cash_mpesa_card": False,
    "enable_discount": True,
    "enable_stock_tracking": True,
    "enable_shelf_mode": False,
    "enable_open_close": True,
    "enable_client_data": True,
    "require_client_phone": False,
    "require_client_name": False,
    "require_mpesa_reference_on_confirm": False,
    "receipt_format_sale": "S",
    "receipt_format_credit": "C",
    "receipt_format_quotation": "Q",
    "receipt_format_trade_out": "T",
    "receipt_format_trade_in": "TI",
}


def effective_pos_settings(shop) -> dict:
    """Company POS defaults merged with the shop's overrides.

    Mirrors MY-SHOP's get_effective_pos_settings: a shop column applies while
    the field is listed in the shop's custom_pos_fields AND override_pos is
    on, or in custom_receipt_fields AND override_receipt is on — everything
    else keeps following the company value.
    """
    company = CompanyPosSettings.objects.filter(pk=1).first()
    shop_row = ShopPosSettings.objects.filter(shop_id=shop.pk).first()
    merged = dict(_POS_DEFAULTS)
    if company is not None:
        for name in merged:
            merged[name] = getattr(company, name, merged[name])
    if shop_row is not None:
        custom_pos = (
            set(shop_row.custom_pos_fields or []) if shop_row.override_pos else set()
        )
        custom_receipt = (
            set(shop_row.custom_receipt_fields or [])
            if shop_row.override_receipt
            else set()
        )
        for name in merged:
            if name in custom_pos or name in custom_receipt:
                merged[name] = getattr(shop_row, name, merged[name])
    for name in _POS_FORMAT_FIELDS:
        merged[name] = (
            (merged[name] or _POS_DEFAULTS[name]).strip().upper()
            or _POS_DEFAULTS[name]
        )
    return merged


def kind_enabled(pos: dict, kind: str) -> bool:
    return bool(pos.get("enable_" + kind, False))


def payment_method_enabled(pos: dict, method: str) -> bool:
    toggle = _PAYMENT_METHOD_TOGGLES.get((method or "").strip().lower())
    return bool(toggle and pos.get(toggle, False))


def get_open_shop_day(shop):
    return (
        ShopDaySession.objects.filter(shop_id=shop.pk, closed_at__isnull=True)
        .order_by("-opened_at")
        .first()
    )


def require_shop_day_for_sale(shop, pos=None):
    """Mirror of shops.services.require_shop_day_for_sale.

    Compulsory open/close off → trading allowed. Otherwise an open day
    session started today is required; a stale session from a previous day
    must be closed first. Till hand-over: once the shop has run at least
    one day, sales between a close and the next open are allowed — they
    land on the next opened session.
    """
    if pos is None:
        pos = effective_pos_settings(shop)
    if not pos.get("enable_open_close", True):
        return
    open_session = get_open_shop_day(shop)
    if open_session is None:
        if ShopDaySession.objects.filter(
            shop_id=shop.pk, closed_at__isnull=False
        ).exists():
            return
        raise StockError("Open the shop day before selling.")
    if timezone.localtime(open_session.opened_at).date() < timezone.localdate():
        raise StockError(
            "Yesterday’s shop day is still open. Close it, then open "
            "today’s day before selling."
        )


def trade_flags(shop) -> dict:
    """Which trade kinds this shop may run — drives the Trade FAB + panel."""
    pos = effective_pos_settings(shop)
    return {
        "trade_out": bool(pos["enable_trade_out"]),
        "trade_in": bool(pos["enable_trade_in"]),
    }


@transaction.atomic
def create_trade(me, raw_lines, *, kind, client_name="", client_phone="",
                 staff_code="", require_member=False, employee_id=None):
    """Returns (receipt, prepared_lines). kind ∈ trade_out|trade_in."""
    if kind not in TRADE_KINDS:
        raise StockError("Choose trade out or trade in.")
    pos = effective_pos_settings(me)
    if not pos["enable_" + kind]:
        raise StockError(
            f"{receipt_label(kind)} is disabled for this shop."
        )
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, me, require_member=require_member
        )
    assert_employee_may(employee_id, ("my-shop", kind))
    if kind == "trade_out":
        require_shop_day_for_sale(me, pos=pos)

    client_name = (client_name or "").strip().upper()
    client_phone = (client_phone or "").strip()
    # Client fields only exist while enable_client_data is on — MY-SHOP hides
    # them entirely otherwise.
    if pos.get("enable_client_data", True):
        if not client_name:
            raise StockError("Enter the client full name.")
        if not client_phone:
            raise StockError("Enter the client phone number.")
    client = (
        upsert_client(client_name, client_phone)
        if client_name or client_phone
        else None
    )

    track_stock = bool(pos["enable_stock_tracking"])
    lines = _parse_lines(
        raw_lines,
        me,
        need_stock=kind == "trade_out" and track_stock,
        sale_flow=True,
    )

    if kind == "trade_out":
        price_rows = {
            r.item_id: r
            for r in ShopItemPrice.objects.filter(
                shop_id=me.pk,
                item_id__in=[l["item"].pk for l in lines],
            )
        }
        for line in lines:
            item = line["item"]
            pr = price_rows.get(item.pk)
            if pr is not None and not pr.is_sold:
                raise StockError(f'"{item.name}" is not sold at {me.name}.')
            if item.track_serial_number:
                line["serials"] = _resolve_transfer_serials(item, me, line)
            price = line["buying_price"]
            if price is None or price <= 0:
                price = _money(
                    (pr.price if pr and pr.price else 0)
                    or item.shop_price
                    or item.minimum_selling_price
                )
            if item.minimum_selling_price and price < item.minimum_selling_price:
                price = _money(item.minimum_selling_price)
            unit_cost = _money(
                line["shop_stock"].average_cost
                if line.get("shop_stock")
                else 0
            )
            if unit_cost > 0 and price < unit_cost:
                raise StockError(
                    f'"{item.name}" is below unit cost '
                    f"(value {price}, cost {unit_cost})."
                )
            line["price"] = price
            line["unit_cost"] = unit_cost
            line["line_total"] = (price * line["quantity"]).quantize(
                Decimal("0.01")
            )
            line["line_cogs"] = (unit_cost * line["quantity"]).quantize(
                Decimal("0.01")
            )
    else:
        serials_all = {s for line in lines for s in line["serials"]}
        if serials_all:
            taken = set(
                ItemSerial.objects.filter(
                    serial_number__in=serials_all, is_available=True
                ).values_list("serial_number", flat=True)
            )
            if taken:
                raise StockError(
                    "Serial already in stock ("
                    + ", ".join(sorted(taken)[:5])
                    + ")."
                )
        for line in lines:
            item = line["item"]
            price = line["buying_price"]
            if price is None or price <= 0:
                raise StockError(
                    f'Enter the amount owed for "{item.name}".'
                )
            line["price"] = price
            # What the shop owes per unit becomes its cost — same as MY-SHOP.
            line["unit_cost"] = price
            line["line_total"] = (price * line["quantity"]).quantize(
                Decimal("0.01")
            )
            line["line_cogs"] = line["line_total"]

    total = sum((l["line_total"] for l in lines), Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    receipt = ShopReceipt.objects.create(
        shop_id=me.pk,
        receipt_number=_next_receipt_number(
            me, pos["receipt_format_" + kind], kind=kind
        ),
        created_by_id=(employee_id or None),
        kind=kind,
        payment_method="none",
        client_id=client.pk if client else None,
        client_name=client_name,
        client_phone=client_phone,
        subtotal=total,
        tax_percent=Decimal("0"),
        tax_amount=Decimal("0"),
        total=total,
        amount_paid=Decimal("0.00"),
        cash_amount=Decimal("0.00"),
        mpesa_amount=Decimal("0.00"),
        card_amount=Decimal("0.00"),
        status="active",
    )

    movement = None
    if track_stock and any(
        getattr(line["item"], "deduct_stock_on_sale", True) for line in lines
    ):
        if kind == "trade_out":
            movement_notes = f"Trade out {receipt.receipt_number}"
        else:
            movement_notes = f"Trade in {receipt.receipt_number} · {client_name}"
        movement = StockMovement.objects.create(
            movement_type="out" if kind == "trade_out" else MOVEMENT_IN,
            entry_source=kind,
            shop_id=me.pk,
            request_status="",
            created_by_id=(employee_id or None),
            notes=f"{movement_notes} · via MY-CHATBOT",
        )

    for line in lines:
        item = line["item"]
        ShopReceiptLine.objects.create(
            receipt_id=receipt.pk,
            item_id=item.pk,
            item_name=item.name,
            quantity=line["quantity"],
            returned_quantity=Decimal("0"),
            unit_price=line["price"],
            unit_cost=line["unit_cost"],
            line_total=line["line_total"],
            line_cogs=line["line_cogs"],
            serial_numbers=line.get("serials") or [],
        )
        if not track_stock:
            continue
        qty = _quantize_qty(line["quantity"])
        if movement is not None and getattr(item, "deduct_stock_on_sale", True):
            StockMovementLine.objects.create(
                movement_id=movement.pk,
                item_id=item.pk,
                item_name=item.name,
                quantity=qty,
                buying_price=(
                    line["unit_cost"] if kind == "trade_in" else None
                ),
                unit_cost=line["unit_cost"],
                reason="trade_out" if kind == "trade_out" else "",
                note=f"{receipt_label(kind)} · {receipt.receipt_number}",
                serial_numbers=line.get("serials") or [],
            )
        if kind == "trade_out":
            if getattr(item, "deduct_stock_on_sale", True):
                stock = line["shop_stock"]
                stock.quantity = _quantize_qty(stock.quantity) - qty
                stock.save(update_fields=["quantity", "updated_at"])
                item.stock = max(ZERO_QTY, _quantize_qty(item.stock) - qty)
                item.save(update_fields=["stock"])
                _mirror_store_movement(
                    item=item,
                    qty=qty,
                    mode="out",
                    employee_id=employee_id,
                    note=f"{receipt.receipt_number} · Trade out · {me.name}",
                )
            if item.track_serial_number:
                for s in ItemSerial.objects.select_for_update().filter(
                    item_id=item.pk,
                    shop_id=me.pk,
                    serial_number__in=line["serials"],
                    is_available=True,
                ):
                    s.is_available = False
                    s.save(update_fields=["is_available", "updated_at"])
        else:
            existing = {
                s.serial_number: s
                for s in ItemSerial.objects.filter(
                    item_id=item.pk, serial_number__in=line["serials"]
                )
            }
            to_create = []
            for serial in line["serials"]:
                row = existing.get(serial)
                if row is None:
                    to_create.append(
                        ItemSerial(
                            item_id=item.pk,
                            shop_id=me.pk,
                            serial_number=serial,
                            is_available=True,
                        )
                    )
                else:
                    row.is_available = True
                    row.shop_id = me.pk
                    row.save(
                        update_fields=["is_available", "shop_id", "updated_at"]
                    )
            if to_create:
                ItemSerial.objects.bulk_create(to_create)
            if getattr(item, "deduct_stock_on_sale", True):
                stock = line.get("shop_stock")
                if stock is None:
                    stock, _ = ShopStock.objects.select_for_update().get_or_create(
                        shop_id=me.pk,
                        item_id=item.pk,
                        defaults={"quantity": ZERO_QTY},
                    )
                stock.average_cost = weighted_average_cost(
                    old_qty=stock.quantity,
                    old_avg=stock.average_cost,
                    in_qty=qty,
                    in_price=line["unit_cost"],
                )
                stock.quantity = _quantize_qty(stock.quantity) + qty
                stock.save(update_fields=["quantity", "average_cost", "updated_at"])
                item.stock = _quantize_qty(item.stock) + qty
                item.save(update_fields=["stock"])
                _mirror_store_movement(
                    item=item,
                    qty=qty,
                    mode=MOVEMENT_IN,
                    employee_id=employee_id,
                    note=f"{receipt.receipt_number} · Trade in · {me.name}",
                )
    return receipt, lines


# ---------------------------------------------------------------------------
# Expense — mirrors shops.services.register_shop_expense.
#
# Supplier name + phone are required (upserted into shops_expensesupplier).
# Paid expenses deduct the full amount from the till as cash; unpaid and
# partial leave amount_paid at zero — same as MY-SHOP.
# ---------------------------------------------------------------------------

EXPENSE_CATEGORIES = (
    ("rent", "Rent"),
    ("utilities", "Utilities"),
    ("transport", "Transport"),
    ("salaries", "Salaries"),
    ("packaging", "Packaging"),
    ("maintenance", "Maintenance"),
    ("marketing", "Marketing"),
    ("office", "Office supplies"),
    ("security", "Security"),
    ("food", "Food & refreshments"),
    ("misc", "Miscellaneous"),
)
EXPENSE_CATEGORY_LABELS = dict(EXPENSE_CATEGORIES)
EXPENSE_STATUSES = ("paid", "partial", "unpaid")


def parse_expense_lines(payload) -> list[dict]:
    """Normalize expense cart lines — mirrors MY-SHOP's _parse_expense_lines.

    Accepts ``lines: [{category, name, amount}, ...]`` or the legacy single
    ``category`` / ``name`` / ``amount`` fields.
    """
    raw = payload.get("lines")
    if raw is None:
        raw = [
            {
                "category": payload.get("category") or "",
                "name": payload.get("name") or "",
                "amount": payload.get("amount"),
            }
        ]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise StockError("Add at least one expense.")
    lines = []
    label_categories = {}
    for slug, lbl in EXPENSE_CATEGORIES:
        label_categories[lbl.lower()] = slug
        label_categories[re.sub(r"[\s\-]+", "_", lbl.lower())] = slug
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise StockError(f"Expense {index + 1}: invalid line.")
        label = f"Expense {index + 1}"
        raw_category = (row.get("category") or "").strip()
        slug = re.sub(r"[\s\-]+", "_", raw_category).lower()
        if slug in EXPENSE_CATEGORY_LABELS:
            category = slug
        elif slug in label_categories:
            category = label_categories[slug]
        else:
            # Free-typed categories are allowed — stored uppercase.
            category = re.sub(r"\s+", " ", raw_category).upper()
        if not category or not re.search(r"[a-z0-9]", category, re.I):
            raise StockError(f"{label}: choose a category.")
        if category == "owner_drawings":
            raise StockError(
                f"{label}: owner drawings are recorded on the Day page."
            )
        if len(category) > 32:
            raise StockError(
                f"{label}: category is too long (32 characters max)."
            )
        name = (row.get("name") or "").strip().upper()
        if not name:
            raise StockError(f"{label}: enter the expense name.")
        if len(name) > 200:
            raise StockError(f"{label}: name is too long.")
        amount = _money(row.get("amount"))
        if amount <= 0:
            raise StockError(f"{label}: amount must be greater than zero.")
        lines.append({"category": category, "name": name, "amount": amount})
    return lines


@transaction.atomic
def create_expenses(me, *, lines=None, category="", name="", amount=None,
                    payment_status="", supplier_name="", supplier_phone="",
                    staff_code="", require_member=False, employee_id=None,
                    payload=None):
    """Create one or more Expense rows (shared supplier + payment status).

    Returns the list of created rows. ``payload`` (or ``lines`` / legacy
    single fields) supplies the cart lines — same shape MY-SHOP posts.
    """
    if employee_id is None:
        employee_id = verify_employee_code(
            staff_code, me, require_member=require_member
        )
    assert_employee_may(employee_id, ("my-shop", "register_expense"))
    if payload is not None:
        parsed = parse_expense_lines(payload)
    elif lines is not None:
        parsed = parse_expense_lines({"lines": lines})
    else:
        parsed = parse_expense_lines(
            {"category": category, "name": name, "amount": amount}
        )
    status = (payment_status or "").strip().lower()
    if status not in EXPENSE_STATUSES:
        raise StockError("Choose a payment status (unpaid, paid, or partial).")
    sname = (supplier_name or "").strip()
    dial, national = parse_supplier_phone(supplier_phone)
    if not sname:
        raise StockError("Supplier name is required.")
    if not national or len(national) != 9 or not national.isdigit():
        raise StockError("Enter a valid 9-digit supplier phone number.")

    supplier = upsert_expense_supplier(name=sname, dial=dial, phone=national)
    created = []
    for line in parsed:
        paid = line["amount"] if status == "paid" else Decimal("0.00")
        created.append(
            Expense.objects.create(
                shop_id=me.pk,
                category=line["category"],
                name=line["name"],
                amount=line["amount"],
                amount_paid=paid,
                cash_amount=paid,
                mpesa_amount=Decimal("0"),
                card_amount=Decimal("0"),
                payment_status=status,
                supplier_id=supplier.pk if supplier else None,
                supplier_name=sname.upper(),
                supplier_phone_country_code=dial,
                supplier_phone_number=national,
                created_by_id=(employee_id or None),
            )
        )
    return created


def create_expense(me, **kwargs):
    """Back-compat wrapper — returns the first (or only) created expense."""
    return create_expenses(me, **kwargs)[0]


def expense_card(expenses, shop) -> dict:
    """Card for one or more expenses registered together."""
    if not isinstance(expenses, (list, tuple)):
        expenses = [expenses]
    if not expenses:
        raise StockError("Add at least one expense.")
    total = sum((_money(e.amount) for e in expenses), Decimal("0.00"))
    first = expenses[0]
    items = [
        {
            "name": e.name,
            "category": EXPENSE_CATEGORY_LABELS.get(e.category, e.category),
            "amount": f"{_money(e.amount):,.2f}",
        }
        for e in expenses
    ]
    return {
        "expense": {
            "expense_id": first.pk,
            "shop": shop.name,
            "name": (
                first.name
                if len(expenses) == 1
                else f"{len(expenses)} expenses"
            ),
            "category": (
                EXPENSE_CATEGORY_LABELS.get(first.category, first.category)
                if len(expenses) == 1
                else ""
            ),
            "amount": f"{total:,.2f}",
            "status": first.payment_status.capitalize(),
            "supplier": first.supplier_name,
            "items": items,
        }
    }


# ---------------------------------------------------------------------------
# Pending member confirmation
#
# Staff acting on a shop they aren't allocated to can send a request instead
# of being blocked: the validated payload is stored on a card message in the
# shop's Assistant thread. A member of the shop then enters their staff code
# and the action executes under that member's employee id.
# ---------------------------------------------------------------------------


def _line_summary(lines) -> str:
    return ", ".join(
        f"{l['quantity']}× {l['item'].name}" for l in lines
    )


def _run_action(action: str, me, payload: dict, employee_id):
    """Execute one stored request — returns a result dict for card building."""
    payload = payload or {}
    if action == "transfer":
        destination = _destination_shop(payload.get("destination_shop_id"))
        movement, lines = create_transfer(
            me, destination, payload.get("lines") or [],
            note=payload.get("note") or "",
            employee_id=employee_id,
        )
        return {
            "action": action,
            "movement": movement,
            "movement_lines": list(
                StockMovementLine.objects.filter(
                    movement_id=movement.pk
                ).order_by("pk")
            ),
            "lines": lines,
            "destination": destination,
        }
    if action == "stockin":
        supplier_name = (payload.get("supplier_name") or "").strip().upper()
        movement, lines = create_stock_in(
            me,
            payload.get("lines") or [],
            supplier_name=supplier_name,
            supplier_phone=payload.get("supplier_phone") or "",
            payment_status=payload.get("payment_status") or "",
            employee_id=employee_id,
        )
        dial, national = parse_supplier_phone(
            payload.get("supplier_phone") or ""
        )
        return {
            "action": action,
            "movement": movement,
            "lines": lines,
            "supplier_name": supplier_name,
            "supplier_label": f"{supplier_name} ({dial} {national})",
        }
    if action == "expense":
        expenses = create_expenses(
            me,
            payload=payload,
            payment_status=payload.get("payment_status") or "",
            supplier_name=payload.get("supplier_name") or "",
            supplier_phone=payload.get("supplier_phone") or "",
            employee_id=employee_id,
        )
        return {"action": action, "expenses": expenses, "expense": expenses[0]}
    if action == "sale":
        due = None
        raw_due = (payload.get("credit_due_date") or "").strip()
        if raw_due:
            from datetime import date

            try:
                due = date.fromisoformat(raw_due[:10])
            except (TypeError, ValueError):
                raise StockError("Enter a valid payment due date.")
        share = bool(payload.get("share_whatsapp"))
        receipt, lines = create_sale(
            me,
            payload.get("lines") or [],
            kind=(payload.get("kind") or "sale").strip().lower(),
            payment_method=payload.get("payment_method") or "",
            cash_amount=payload.get("cash_amount"),
            mpesa_amount=payload.get("mpesa_amount"),
            card_amount=payload.get("card_amount"),
            client_name=payload.get("client_name") or "",
            client_phone=payload.get("client_phone") or "",
            credit_due_date=due,
            share_whatsapp=share,
            employee_id=employee_id,
        )
        return {
            "action": action,
            "receipt": receipt,
            "lines": lines,
            "whatsapp_url": quotation_whatsapp_url(
                receipt, lines, share_whatsapp=share
            ),
            "total": sum(
                (l["line_total"] for l in lines), Decimal("0.00")
            ).quantize(Decimal("0.01")),
        }
    if action == "trade":
        receipt, lines = create_trade(
            me,
            payload.get("lines") or [],
            kind=(payload.get("kind") or "").strip().lower(),
            client_name=payload.get("client_name") or "",
            client_phone=payload.get("client_phone") or "",
            employee_id=employee_id,
        )
        return {
            "action": action,
            "receipt": receipt,
            "lines": lines,
            "whatsapp_url": "",
            "total": sum(
                (l["line_total"] for l in lines), Decimal("0.00")
            ).quantize(Decimal("0.01")),
        }
    raise StockError("Unknown request type.")


class _PendingRollback(Exception):
    """Unwinds the dry-run transaction, carrying the computed result."""

    def __init__(self, result):
        super().__init__()
        self.result = result


def _pending_display(result: dict) -> dict:
    """Title/detail/item rows shown on a pending-confirmation card."""
    action = result["action"]
    if action == "transfer":
        return {
            "title": "Stock transfer",
            "detail": f"To {result['destination'].name}",
            "items": [
                {
                    "name": l.item_name,
                    "qty": str(_quantize_qty(l.quantity)),
                    "note": (
                        f"{len(l.serial_numbers)} SN"
                        if l.serial_numbers
                        else ""
                    ),
                    "serials": list(l.serial_numbers or []),
                }
                for l in result["movement_lines"]
            ],
            "total": "",
            "body": (
                "Pending member confirmation — transfer to "
                f"{result['destination'].name}: "
                + _line_summary(result["lines"])
            ),
        }
    if action == "stockin":
        return {
            "title": "Stock in",
            "detail": f"From {result['supplier_label']}",
            "items": [
                {
                    "name": l["item"].name,
                    "qty": str(_quantize_qty(l["quantity"])),
                    "note": (
                        str(_money(l["buying_price"]))
                        if l["buying_price"] is not None
                        else ""
                    ),
                    "serials": l.get("serials") or [],
                }
                for l in result["lines"]
            ],
            "total": "",
            "body": (
                "Pending member confirmation — stock in: "
                + _line_summary(result["lines"])
                + f" — {result['supplier_name']}"
            ),
        }
    if action == "expense":
        expenses = result.get("expenses") or [result["expense"]]
        total = sum((_money(e.amount) for e in expenses), Decimal("0.00"))
        supplier = expenses[0].supplier_name
        count = len(expenses)
        return {
            "title": "Expense" if count == 1 else f"{count} expenses",
            "detail": supplier,
            "items": [
                {
                    "name": e.name,
                    "qty": "",
                    "note": EXPENSE_CATEGORY_LABELS.get(
                        e.category, e.category
                    ),
                    "serials": [],
                }
                for e in expenses
            ],
            "total": f"KSh {total:,.2f}",
            "body": (
                "Pending member confirmation — "
                + (
                    f"expense: {expenses[0].name} · KSh "
                    f"{_money(expenses[0].amount):,.2f}"
                    if count == 1
                    else f"{count} expenses · KSh {total:,.2f}"
                )
                + f" — {supplier}"
            ),
        }
    receipt = result["receipt"]
    detail = []
    if receipt.kind == "sale":
        detail.append(
            PAYMENT_LABELS.get(receipt.payment_method, "")
        )
    if receipt.client_name:
        detail.append(f"Client {receipt.client_name}")
    if receipt.credit_due_date:
        detail.append("due " + receipt.credit_due_date.strftime("%d %b %Y"))
    total = result["total"]
    count = len(result["lines"])
    return {
        "title": {
            "sale": "New sale",
            "credit": "Credit sale",
            "quotation": "Quotation",
            "trade_out": "Trade out",
            "trade_in": "Trade in",
        }.get(receipt.kind, "Sale"),
        "detail": " · ".join(b for b in detail if b),
        "items": [
            {
                "name": l["item"].name,
                "qty": str(_quantize_qty(l["quantity"])),
                "note": str(_money(l["price"])),
                "serials": l.get("serials") or [],
            }
            for l in result["lines"]
        ],
        "total": f"KSh {total:,.2f}",
        "body": (
            f"Pending member confirmation — {receipt_label(receipt.kind)}: "
            f"{count} item"
            f"{'s' if count != 1 else ''}, KSh {total:,.2f}"
        ),
    }


def build_pending(action: str, me, payload: dict, *, requested_by: str = "",
                  requested_by_id=None):
    """Validate a request and build its pending-confirmation card.

    The real create_* runs inside a transaction that is always rolled back,
    so the payload is fully validated (stock, serials, prices, supplier) with
    nothing written. The payload is stored in the card meta and executed
    verbatim once a shop member enters their staff code.
    """
    try:
        with transaction.atomic():
            raise _PendingRollback(
                _run_action(action, me, payload, requested_by_id or 0)
            )
    except _PendingRollback as done:
        result = done.result
    display = _pending_display(result)
    return display["body"], {
        "pending_action": {
            "action": action,
            "status": "pending",
            "title": display["title"],
            "requested_by": (requested_by or "").strip(),
            "items": display["items"],
            "detail": display["detail"],
            "total": display["total"],
            "payload": payload,
        }
    }


def execute_pending(me, pa: dict, employee_id):
    """Run a stored pending action under the confirming member's name.

    Returns the final card meta + body plus the objects the view needs for
    follow-ups (destination/movement/lines for a transfer).
    """
    action = (pa or {}).get("action") or ""
    result = _run_action(
        action, me, (pa or {}).get("payload") or {}, employee_id
    )
    if action == "transfer":
        destination = result["destination"]
        result["meta"] = transfer_card(
            result["movement"],
            me,
            destination,
            result["movement_lines"],
            readonly=True,
        )
        result["body"] = (
            f"Stock transfer → {destination.name}: "
            + _line_summary(result["lines"])
        )
    elif action == "stockin":
        result["meta"] = stock_in_card(
            result["movement"], me, result["lines"], result["supplier_name"]
        )
        result["body"] = (
            "Stock received: " + _line_summary(result["lines"])
            + f" — {result['supplier_name']}"
        )
    elif action == "expense":
        expenses = result.get("expenses") or [result["expense"]]
        total = sum((_money(e.amount) for e in expenses), Decimal("0.00"))
        result["meta"] = expense_card(expenses, me)
        if len(expenses) == 1:
            e = expenses[0]
            result["body"] = (
                f"Expense {e.name} — KSh {_money(e.amount):,.2f}"
                f" · {e.payment_status} — {e.supplier_name}"
            )
        else:
            result["body"] = (
                f"{len(expenses)} expenses — KSh {total:,.2f}"
                f" · {expenses[0].payment_status} — {expenses[0].supplier_name}"
            )
    else:
        receipt = result["receipt"]
        count = len(result["lines"])
        result["meta"] = sale_card(
            receipt, result["lines"], result["total"]
        )
        result["body"] = (
            f"{receipt_label(receipt.kind)} {receipt.receipt_number} — "
            f"{count} item{'s' if count != 1 else ''}, "
            f"KSh {result['total']:,.2f}"
        )
    return result
