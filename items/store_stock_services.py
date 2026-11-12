"""Central store stock in/out — separate from shop stock / apply_stock_movement."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from employees.models import EmployeeProfile, EmployeeStatus
from items.models import Item, StoreStock, StoreStockMovement, StoreStockMovementType


def _parse_qty(raw) -> Decimal:
    try:
        qty = Decimal(str(raw or "").strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid quantity.") from exc
    if qty <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    return qty.quantize(Decimal("0.001"))


def store_qty_for_item(item_id) -> Decimal:
    row = StoreStock.objects.filter(item_id=item_id).only("quantity").first()
    return row.quantity if row is not None else Decimal("0")


def list_store_stock_items(*, query: str = "") -> list[dict]:
    """Active catalog items with current central-store qty for the list UI."""
    q = (query or "").strip()
    items = (
        Item.objects.filter(is_suspended=False)
        .order_by("category", "name")
        .only("id", "name", "category")
    )
    if q:
        filt = Q(name__icontains=q) | Q(category__icontains=q)
        if q.isdigit():
            filt = filt | Q(pk=int(q))
        items = items.filter(filt)
    qty_by_item = {
        row.item_id: row.quantity
        for row in StoreStock.objects.filter(item_id__in=items.values_list("id", flat=True))
        .only("item_id", "quantity")
    }
    rows = []
    for item in items:
        qty = qty_by_item.get(item.pk) or Decimal("0")
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "label": f"{item.name} · {item.category}",
                "store_qty": qty,
                "store_qty_display": str(qty),
                "search_text": f"{item.name} {item.category} {item.pk}".lower(),
            }
        )
    return rows


def search_store_items(*, query: str, limit: int = 12) -> list[dict]:
    rows = list_store_stock_items(query=query)
    limited = rows[: max(1, min(int(limit or 12), 30))]
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "label": row["label"],
            "store_qty": row["store_qty_display"],
        }
        for row in limited
    ]


def parse_store_stock_qty_lines(post_data) -> list[tuple[int, Decimal]]:
    """Read qty_<item_id> fields; skip blanks and zeros."""
    lines = []
    for key, raw in (post_data or {}).items():
        if not str(key).startswith("qty_"):
            continue
        item_raw = str(key)[4:].strip()
        if not item_raw.isdigit():
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            qty = _parse_qty(text)
        except ValidationError:
            raise ValidationError(f"Invalid quantity for item {item_raw}.")
        lines.append((int(item_raw), qty))
    if not lines:
        raise ValidationError("Enter a quantity for at least one item.")
    return lines


@transaction.atomic
def record_store_stock_lines(
    *,
    mode: str,
    actor: EmployeeProfile,
    lines: list[tuple[int, Decimal]],
    received_by_id,
    note: str = "",
) -> list[StoreStockMovement]:
    mode = (mode or "").strip().lower()
    if mode not in {StoreStockMovementType.IN, StoreStockMovementType.OUT}:
        raise ValidationError("Unknown store stock mode.")
    if not lines:
        raise ValidationError("Enter a quantity for at least one item.")
    movements = []
    for item_id, qty in lines:
        if mode == StoreStockMovementType.IN:
            movements.append(
                record_store_stock_in(
                    actor=actor,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=received_by_id,
                    note=note,
                )
            )
        else:
            movements.append(
                record_store_stock_out(
                    actor=actor,
                    item_id=item_id,
                    quantity=qty,
                    received_by_id=received_by_id,
                    note=note,
                )
            )
    return movements


def search_active_employees(*, query: str, limit: int = 12) -> list[dict]:
    q = (query or "").strip()
    people = (
        EmployeeProfile.objects.filter(status=EmployeeStatus.ACTIVE)
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "user__username")
    )
    if q:
        people = people.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
        )
    rows = []
    for profile in people[: max(1, min(int(limit or 12), 30))]:
        name = (profile.user.get_full_name() or profile.user.username or "").strip()
        if not name:
            continue
        rows.append(
            {
                "id": profile.pk,
                "name": name,
                "label": name,
            }
        )
    return rows


def _resolve_item(item_id) -> Item:
    try:
        item = Item.objects.get(pk=int(item_id), is_suspended=False)
    except (TypeError, ValueError, Item.DoesNotExist) as exc:
        raise ValidationError("Select a valid item.") from exc
    return item


def _resolve_counterpart(received_by_id, *, mode: str) -> EmployeeProfile:
    try:
        profile = EmployeeProfile.objects.select_related("user").get(
            pk=int(received_by_id),
            status=EmployeeStatus.ACTIVE,
        )
    except (TypeError, ValueError, EmployeeProfile.DoesNotExist) as exc:
        if mode == StoreStockMovementType.IN:
            raise ValidationError("Select who delivered the stock.") from exc
        raise ValidationError("Select the employee who received the stock.") from exc
    return profile


@transaction.atomic
def record_store_stock_in(
    *,
    actor: EmployeeProfile,
    item_id,
    quantity,
    received_by_id,
    note: str = "",
) -> StoreStockMovement:
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError("Sign in to record store stock in.")
    item = _resolve_item(item_id)
    qty = _parse_qty(quantity)
    receiver = _resolve_counterpart(
        received_by_id, mode=StoreStockMovementType.IN
    )
    stock, _ = StoreStock.objects.select_for_update().get_or_create(item=item)
    stock.quantity = (stock.quantity or Decimal("0")) + qty
    stock.save(update_fields=["quantity", "updated_at"])
    return StoreStockMovement.objects.create(
        movement_type=StoreStockMovementType.IN,
        item=item,
        quantity=qty,
        balance_after=stock.quantity,
        created_by=actor,
        received_by=receiver,
        note=(note or "").strip()[:255],
    )


@transaction.atomic
def record_store_stock_out(
    *,
    actor: EmployeeProfile,
    item_id,
    quantity,
    received_by_id,
    note: str = "",
) -> StoreStockMovement:
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError("Sign in to record store stock out.")
    item = _resolve_item(item_id)
    qty = _parse_qty(quantity)
    receiver = _resolve_counterpart(
        received_by_id, mode=StoreStockMovementType.OUT
    )
    stock, _ = StoreStock.objects.select_for_update().get_or_create(item=item)
    current = stock.quantity or Decimal("0")
    if qty > current:
        raise ValidationError(
            f"Store only has {current} available — cannot stock out {qty}."
        )
    stock.quantity = current - qty
    stock.save(update_fields=["quantity", "updated_at"])
    return StoreStockMovement.objects.create(
        movement_type=StoreStockMovementType.OUT,
        item=item,
        quantity=qty,
        balance_after=stock.quantity,
        created_by=actor,
        received_by=receiver,
        note=(note or "").strip()[:255],
    )


def _employee_display_name(profile) -> str:
    if profile is None:
        return "—"
    user = getattr(profile, "user", None)
    if user is None:
        return "—"
    return (user.get_full_name() or user.username or "—").strip() or "—"


def build_store_stock_report(*, request) -> dict:
    """Period + item filtered central-store movement report."""
    from django.db.models import Sum
    from django.utils import timezone
    from employees.analytics_services import _date_filter_context

    date_filter = _date_filter_context(
        request, allow_all_time=True, default_all_time=True
    )
    start = date_filter.get("start")
    end = date_filter.get("end")

    raw_item_id = (request.GET.get("item_id") or "").strip()
    selected_item_id = None
    selected_item = None
    if raw_item_id.isdigit():
        selected_item = (
            Item.objects.filter(pk=int(raw_item_id), is_suspended=False)
            .only("id", "name", "category")
            .first()
        )
        if selected_item is not None:
            selected_item_id = selected_item.pk

    movement_type = (request.GET.get("type") or "all").strip().lower()
    if movement_type not in {"all", "in", "out"}:
        movement_type = "all"

    qs = StoreStockMovement.objects.select_related(
        "item",
        "created_by__user",
        "received_by__user",
    )
    if start is not None and end is not None:
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    if selected_item_id is not None:
        qs = qs.filter(item_id=selected_item_id)
    if movement_type in {StoreStockMovementType.IN, StoreStockMovementType.OUT}:
        qs = qs.filter(movement_type=movement_type)

    qs = qs.order_by("-created_at", "-id")
    in_total = qs.filter(
        movement_type=StoreStockMovementType.IN
    ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    out_total = qs.filter(
        movement_type=StoreStockMovementType.OUT
    ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    movements = list(qs[:500])

    rows = []
    for movement in movements:
        counterpart_label = (
            "Delivered by"
            if movement.movement_type == StoreStockMovementType.IN
            else "Received by"
        )
        rows.append(
            {
                "id": movement.pk,
                "when": timezone.localtime(movement.created_at).strftime(
                    "%d %b %Y · %H:%M"
                ),
                "movement_type": movement.movement_type,
                "movement_label": (
                    "Stock in"
                    if movement.movement_type == StoreStockMovementType.IN
                    else "Stock out"
                ),
                "item_id": movement.item_id,
                "item_name": movement.item.name,
                "item_category": movement.item.category,
                "quantity": movement.quantity,
                "quantity_display": str(movement.quantity),
                "balance_after": movement.balance_after,
                "balance_after_display": str(movement.balance_after),
                "recorded_by": _employee_display_name(movement.created_by),
                "counterpart_label": counterpart_label,
                "counterpart": _employee_display_name(movement.received_by),
                "note": (movement.note or "").strip(),
                "search_text": (
                    f"{movement.item.name} {movement.item.category} "
                    f"{movement.movement_type} {movement.note}"
                ).lower(),
            }
        )

    item_options = list_store_stock_items()
    current_store_qty = None
    if selected_item_id is not None:
        current_store_qty = store_qty_for_item(selected_item_id)

    return {
        **date_filter,
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(movements) >= 500,
        "movement_type": movement_type,
        "selected_item_id": selected_item_id or "",
        "selected_item_label": (
            f"{selected_item.name} · {selected_item.category}"
            if selected_item is not None
            else ""
        ),
        "item_options": item_options,
        "current_store_qty": (
            str(current_store_qty) if current_store_qty is not None else ""
        ),
        "total_in": in_total,
        "total_out": out_total,
        "total_in_display": str(in_total),
        "total_out_display": str(out_total),
        "type_options": (
            ("all", "All movements"),
            ("in", "Stock in"),
            ("out", "Stock out"),
        ),
    }
