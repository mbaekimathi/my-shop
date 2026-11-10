"""Stock take: create requests, hide covered qty, collect shop counts, compare."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from items.models import (
    Item,
    ShopStock,
    StockTake,
    StockTakeAssignment,
    StockTakeAssignmentStatus,
    StockTakeCount,
    StockTakeScope,
    StockTakeStatus,
)
from shops.services import category_from_form_token

STOCK_TAKE_HIDE_CACHE_KEY = "stock_take_open_hide:v1"
STOCK_TAKE_HIDE_CACHE_TTL = 30
_ZERO = Decimal("0")


def _quantize(value) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        qty = _ZERO
    return qty.quantize(Decimal("0.001"))


def invalidate_stock_take_hide_cache() -> None:
    cache.delete(STOCK_TAKE_HIDE_CACHE_KEY)


def open_stock_take_hide_sets() -> dict:
    """Item ids / categories covered by open stock takes (numbers stay Hidden)."""
    cached = cache.get(STOCK_TAKE_HIDE_CACHE_KEY)
    if isinstance(cached, dict):
        return {
            "item_ids": set(cached.get("item_ids") or []),
            "categories": set(cached.get("categories") or []),
            "active": bool(cached.get("active")),
        }

    item_ids: set[int] = set()
    categories: set[str] = set()
    for take in StockTake.objects.filter(status=StockTakeStatus.OPEN).only(
        "scope_mode", "categories", "item_ids"
    ):
        if take.scope_mode == StockTakeScope.CATEGORY:
            for raw in take.categories or []:
                categories.add(str(raw or "").strip())
        else:
            for raw in take.item_ids or []:
                try:
                    item_ids.add(int(raw))
                except (TypeError, ValueError):
                    continue

    payload = {
        "item_ids": list(item_ids),
        "categories": list(categories),
        "active": bool(item_ids or categories),
    }
    cache.set(STOCK_TAKE_HIDE_CACHE_KEY, payload, STOCK_TAKE_HIDE_CACHE_TTL)
    return {
        "item_ids": item_ids,
        "categories": categories,
        "active": payload["active"],
    }


def stock_take_hides_item(item_id, category="") -> bool:
    state = open_stock_take_hide_sets()
    if not state["active"]:
        return False
    cat = str(category or "").strip()
    if cat in state["categories"]:
        return True
    try:
        return int(item_id) in state["item_ids"]
    except (TypeError, ValueError):
        return False


def resolve_stock_take_items(*, scope_mode: str, categories, item_ids) -> list[Item]:
    mode = (scope_mode or "").strip().lower()
    qs = Item.objects.filter(is_suspended=False).order_by("category", "name")
    if mode == StockTakeScope.CATEGORY:
        cleaned = []
        for raw in categories or []:
            name = category_from_form_token(raw)
            if name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            raise ValidationError("Select at least one category for the stock take.")
        return list(qs.filter(category__in=cleaned))
    if mode == StockTakeScope.ITEMS:
        cleaned_ids = []
        for raw in item_ids or []:
            try:
                item_id = int(raw)
            except (TypeError, ValueError):
                continue
            if item_id not in cleaned_ids:
                cleaned_ids.append(item_id)
        if not cleaned_ids:
            raise ValidationError("Select at least one item for the stock take.")
        found = list(qs.filter(pk__in=cleaned_ids))
        if not found:
            raise ValidationError("Select at least one item for the stock take.")
        return found
    raise ValidationError("Choose whether to stock-take by category or by item.")


@transaction.atomic
def create_stock_take(
    *,
    profile,
    scope_mode: str,
    categories,
    item_ids,
    shop_ids,
    deadline,
    note: str = "",
) -> StockTake:
    mode = (scope_mode or "").strip().lower()
    if mode not in {StockTakeScope.CATEGORY, StockTakeScope.ITEMS}:
        raise ValidationError("Choose whether to stock-take by category or by item.")
    if deadline is None:
        raise ValidationError("Set a deadline for the stock take.")
    if timezone.is_naive(deadline):
        deadline = timezone.make_aware(deadline, timezone.get_current_timezone())
    if deadline <= timezone.now():
        raise ValidationError("Deadline must be in the future.")

    shops = []
    seen = set()
    from shops.models import Shop

    for raw in shop_ids or []:
        try:
            shop_id = int(raw)
        except (TypeError, ValueError):
            continue
        if shop_id in seen:
            continue
        seen.add(shop_id)
        shop = (
            Shop.objects.filter(pk=shop_id, is_hidden=False, is_suspended=False)
            .order_by()
            .first()
        )
        if shop:
            shops.append(shop)
    if not shops:
        raise ValidationError("Select at least one shop.")

    items = resolve_stock_take_items(
        scope_mode=mode, categories=categories, item_ids=item_ids
    )
    cleaned_categories = []
    cleaned_item_ids = []
    if mode == StockTakeScope.CATEGORY:
        for item in items:
            cat = item.category or ""
            if cat not in cleaned_categories:
                cleaned_categories.append(cat)
    else:
        cleaned_item_ids = [item.pk for item in items]

    take = StockTake.objects.create(
        scope_mode=mode,
        categories=cleaned_categories,
        item_ids=cleaned_item_ids,
        deadline=deadline,
        status=StockTakeStatus.OPEN,
        note=(note or "").strip()[:240],
        created_by=profile,
    )

    stock_map = {
        (row.shop_id, row.item_id): row.quantity
        for row in ShopStock.objects.filter(
            shop_id__in=[shop.pk for shop in shops],
            item_id__in=[item.pk for item in items],
        ).only("shop_id", "item_id", "quantity")
    }

    for shop in shops:
        assignment = StockTakeAssignment.objects.create(
            stock_take=take,
            shop=shop,
            status=StockTakeAssignmentStatus.PENDING,
        )
        StockTakeCount.objects.bulk_create(
            [
                StockTakeCount(
                    assignment=assignment,
                    item=item,
                    system_qty=_quantize(stock_map.get((shop.pk, item.pk), _ZERO)),
                )
                for item in items
            ]
        )

    invalidate_stock_take_hide_cache()
    return take


@transaction.atomic
def close_stock_take(*, take: StockTake, profile=None) -> StockTake:
    if take.status == StockTakeStatus.CLOSED:
        return take
    take.status = StockTakeStatus.CLOSED
    take.closed_at = timezone.now()
    take.closed_by = profile
    take.save(update_fields=["status", "closed_at", "closed_by"])
    invalidate_stock_take_hide_cache()
    return take


def pending_stock_take_for_shop(shop) -> StockTakeAssignment | None:
    """Oldest open pending assignment for this shop (popup target)."""
    return (
        StockTakeAssignment.objects.filter(
            shop=shop,
            status=StockTakeAssignmentStatus.PENDING,
            stock_take__status=StockTakeStatus.OPEN,
        )
        .select_related("stock_take")
        .prefetch_related("counts__item")
        .order_by("stock_take__deadline", "id")
        .first()
    )


def _qty_label(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def stock_take_assignment_payload(assignment: StockTakeAssignment) -> dict:
    take = assignment.stock_take
    lines = []
    answered = 0
    for count in assignment.counts.all():
        item = count.item
        is_answered = count.counted_qty is not None
        if is_answered:
            answered += 1
        lines.append(
            {
                "count_id": count.pk,
                "item_id": item.pk,
                "name": item.name,
                "category": item.category or "",
                "answered": is_answered,
                "counted_qty": (
                    _qty_label(_quantize(count.counted_qty)) if is_answered else ""
                ),
            }
        )
    return {
        "assignment_id": assignment.pk,
        "stock_take_id": take.pk,
        "deadline": take.deadline.isoformat() if take.deadline else "",
        "deadline_label": timezone.localtime(take.deadline).strftime(
            "%d %b %Y %H:%M"
        )
        if take.deadline
        else "",
        "note": take.note or "",
        "overdue": bool(take.deadline and take.deadline < timezone.now()),
        "answered_count": answered,
        "total_count": len(lines),
        "lines": lines,
    }


def _parse_count_qty(raw, *, item_name: str) -> Decimal:
    if raw in (None, ""):
        raise ValidationError(f"Enter a count for {item_name}.")
    try:
        qty = _quantize(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Invalid count for {item_name}.") from exc
    if qty < 0:
        raise ValidationError(f"Count for {item_name} cannot be negative.")
    return qty


def _shop_line_result(count: StockTakeCount) -> dict:
    item = count.item
    system = _quantize(count.system_qty)
    counted = (
        _quantize(count.counted_qty) if count.counted_qty is not None else None
    )
    match = counted is not None and counted == system
    return {
        "count_id": count.pk,
        "item_id": item.pk,
        "name": item.name,
        "category": item.category or "",
        "counted_qty": _qty_label(counted) if counted is not None else "—",
        "match": match,
        # System qty intentionally omitted for shop floor feedback.
    }


def _grade_payload(*, correct: int, total: int) -> dict:
    wrong = max(0, total - correct)
    percent = int(round((100 * correct / total), 0)) if total else 0
    if percent >= 100:
        grade = "perfect"
        grade_label = "Perfect count"
        headline = "Flawless stock take"
    elif percent >= 80:
        grade = "great"
        grade_label = "Great accuracy"
        headline = "Almost there"
    elif percent >= 50:
        grade = "fair"
        grade_label = "Needs a check"
        headline = "Mixed results"
    else:
        grade = "low"
        grade_label = "Review needed"
        headline = "Let's recount carefully"
    return {
        "marks": correct,
        "correct": correct,
        "max_marks": total,
        "wrong": wrong,
        "percent": percent,
        "grade": grade,
        "grade_label": grade_label,
        "headline": headline,
    }


def build_stock_take_result(assignment: StockTakeAssignment) -> dict:
    """Shop scorecard: correct/wrong + marks. Never exposes system qty."""
    rows = list(assignment.counts.select_related("item").all())
    lines = [_shop_line_result(count) for count in rows]
    correct = sum(1 for line in lines if line["match"])
    payload = _grade_payload(correct=correct, total=len(lines))
    payload.update(
        {
            "assignment_id": assignment.pk,
            "complete": True,
            "lines": lines,
        }
    )
    return payload


def _finalize_assignment_if_complete(
    assignment: StockTakeAssignment, *, submitted_by_label: str
) -> bool:
    pending = assignment.counts.filter(counted_qty__isnull=True).exists()
    if pending:
        return False
    if assignment.status != StockTakeAssignmentStatus.SUBMITTED:
        assignment.status = StockTakeAssignmentStatus.SUBMITTED
        assignment.submitted_at = timezone.now()
        assignment.submitted_by_label = (submitted_by_label or "").strip()[:160]
        assignment.save(
            update_fields=["status", "submitted_at", "submitted_by_label"]
        )
    return True


@transaction.atomic
def submit_stock_take_one(
    *,
    assignment: StockTakeAssignment,
    count_id,
    qty,
    submitted_by_label: str = "",
) -> dict:
    """Save one item count, return that item's result (no system qty)."""
    assignment = (
        StockTakeAssignment.objects.select_for_update()
        .select_related("stock_take")
        .prefetch_related("counts__item")
        .get(pk=assignment.pk)
    )
    if assignment.stock_take.status != StockTakeStatus.OPEN:
        raise ValidationError("This stock take is closed.")
    if assignment.status == StockTakeAssignmentStatus.SUBMITTED:
        raise ValidationError("This shop already submitted its stock take.")

    try:
        count_pk = int(count_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Choose an item to count.") from exc

    count_rows = {row.pk: row for row in assignment.counts.all()}
    row = count_rows.get(count_pk)
    if row is None:
        raise ValidationError("That item is not part of this stock take.")

    row.counted_qty = _parse_count_qty(qty, item_name=row.item.name)
    row.save(update_fields=["counted_qty"])

    complete = _finalize_assignment_if_complete(
        assignment, submitted_by_label=submitted_by_label
    )
    line = _shop_line_result(row)
    all_rows = list(assignment.counts.select_related("item").all())
    answered = sum(1 for item in all_rows if item.counted_qty is not None)
    total = len(all_rows)
    correct_so_far = sum(
        1
        for item in all_rows
        if item.counted_qty is not None
        and _quantize(item.counted_qty) == _quantize(item.system_qty)
    )
    payload = {
        "assignment_id": assignment.pk,
        "complete": complete,
        "line": line,
        "answered_count": answered,
        "total_count": total,
        "marks_so_far": correct_so_far,
        "progress_percent": int(round((100 * answered / total), 0)) if total else 0,
    }
    if complete:
        payload["result"] = build_stock_take_result(assignment)
    return payload


@transaction.atomic
def submit_stock_take_counts(
    *,
    assignment: StockTakeAssignment,
    counts: dict,
    submitted_by_label: str = "",
) -> dict:
    assignment = (
        StockTakeAssignment.objects.select_for_update()
        .select_related("stock_take")
        .prefetch_related("counts__item")
        .get(pk=assignment.pk)
    )
    if assignment.stock_take.status != StockTakeStatus.OPEN:
        raise ValidationError("This stock take is closed.")
    if assignment.status == StockTakeAssignmentStatus.SUBMITTED:
        raise ValidationError("This shop already submitted its stock take.")

    count_rows = {row.pk: row for row in assignment.counts.all()}
    if not count_rows:
        raise ValidationError("No items to count.")

    updates = []
    for count_id, row in count_rows.items():
        raw = counts.get(str(count_id), counts.get(count_id))
        row.counted_qty = _parse_count_qty(raw, item_name=row.item.name)
        updates.append(row)

    StockTakeCount.objects.bulk_update(updates, ["counted_qty"])
    _finalize_assignment_if_complete(
        assignment, submitted_by_label=submitted_by_label
    )
    return build_stock_take_result(assignment)


def build_stock_take_comparison(take: StockTake) -> dict:
    assignments = list(
        take.assignments.select_related("shop")
        .prefetch_related("counts__item")
        .order_by("shop__name")
    )
    item_map: dict[int, dict] = {}
    for assignment in assignments:
        for count in assignment.counts.all():
            item = count.item
            row = item_map.setdefault(
                item.pk,
                {
                    "item": item,
                    "shops": {},
                },
            )
            counted = count.counted_qty
            system = _quantize(count.system_qty)
            variance = None if counted is None else _quantize(counted) - system
            row["shops"][assignment.shop_id] = {
                "system_qty": system,
                "counted_qty": counted,
                "variance": variance,
                "submitted": assignment.status
                == StockTakeAssignmentStatus.SUBMITTED,
                "match": variance == 0 if variance is not None else False,
            }

    rows = []
    for row in sorted(
        item_map.values(),
        key=lambda entry: (
            (entry["item"].category or "").lower(),
            entry["item"].name.lower(),
        ),
    ):
        cells = []
        for assignment in assignments:
            cell = row["shops"].get(assignment.shop_id)
            if cell is None:
                cell = {
                    "system_qty": _ZERO,
                    "counted_qty": None,
                    "variance": None,
                    "submitted": False,
                    "match": False,
                }
            cells.append(cell)
        rows.append({"item": row["item"], "cells": cells})

    submitted = sum(
        1
        for a in assignments
        if a.status == StockTakeAssignmentStatus.SUBMITTED
    )
    return {
        "assignments": assignments,
        "rows": rows,
        "submitted_count": submitted,
        "shop_count": len(assignments),
        "pending_count": len(assignments) - submitted,
        "overdue": bool(
            take.status == StockTakeStatus.OPEN
            and take.deadline
            and take.deadline < timezone.now()
        ),
    }
