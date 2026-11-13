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

STOCK_TAKE_HIDE_CACHE_KEY = "stock_take_open_hide:v3"
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
    # Drop prior cache keys so old category-wide hide payloads cannot linger.
    cache.delete("stock_take_open_hide:v2")


def open_stock_take_hide_sets() -> dict:
    """Pending open stock-take coverage, keyed by participating shop.

    Only the exact count-line items for a pending shop stay Hidden. Shops that
    are not on the take (or have already submitted) keep showing quantities.
    """
    cached = cache.get(STOCK_TAKE_HIDE_CACHE_KEY)
    if isinstance(cached, dict) and "by_shop" in cached:
        by_shop = {}
        for raw_shop_id, coverage in (cached.get("by_shop") or {}).items():
            try:
                shop_id = int(raw_shop_id)
            except (TypeError, ValueError):
                continue
            item_ids = set()
            for raw in coverage.get("item_ids") or []:
                try:
                    item_ids.add(int(raw))
                except (TypeError, ValueError):
                    continue
            if item_ids:
                by_shop[shop_id] = {"item_ids": item_ids}
        return {
            "by_shop": by_shop,
            "active": bool(by_shop),
        }

    by_shop: dict[int, set[int]] = {}
    pending_assignment_ids = list(
        StockTakeAssignment.objects.filter(
            status=StockTakeAssignmentStatus.PENDING,
            stock_take__status=StockTakeStatus.OPEN,
        ).values_list("id", "shop_id")
    )
    if pending_assignment_ids:
        assignment_shop = {
            int(assignment_id): int(shop_id)
            for assignment_id, shop_id in pending_assignment_ids
        }
        for assignment_id, item_id in StockTakeCount.objects.filter(
            assignment_id__in=assignment_shop.keys()
        ).values_list("assignment_id", "item_id"):
            shop_id = assignment_shop.get(int(assignment_id))
            if shop_id is None:
                continue
            by_shop.setdefault(shop_id, set()).add(int(item_id))

    payload = {
        "by_shop": {
            str(shop_id): {"item_ids": list(item_ids)}
            for shop_id, item_ids in by_shop.items()
            if item_ids
        },
        "active": bool(by_shop),
    }
    cache.set(STOCK_TAKE_HIDE_CACHE_KEY, payload, STOCK_TAKE_HIDE_CACHE_TTL)
    return {
        "by_shop": {
            shop_id: {"item_ids": item_ids} for shop_id, item_ids in by_shop.items()
        },
        "active": payload["active"],
    }


def stock_take_hides_item(item_id, category="", shop_id=None) -> bool:
    """Hide on-hand numbers only for selected take items at pending shops.

    Requires shop_id. Without a shop, stock-take masking does not apply — other
    shops and aggregates keep showing quantities.
    """
    if shop_id is None:
        return False
    try:
        shop_key = int(shop_id)
        item_key = int(item_id)
    except (TypeError, ValueError):
        return False
    state = open_stock_take_hide_sets()
    if not state["active"]:
        return False
    coverage = state["by_shop"].get(shop_key)
    if not coverage:
        return False
    return item_key in coverage["item_ids"]


def resolve_stock_take_items(
    *, scope_mode: str, categories, item_ids, allow_item_ids=None
) -> list[Item]:
    mode = (scope_mode or "").strip().lower()
    allowed = set()
    for raw in allow_item_ids or []:
        try:
            allowed.add(int(raw))
        except (TypeError, ValueError):
            continue
    qs = Item.objects.order_by("category", "name")
    if not allowed:
        qs = qs.filter(is_suspended=False)
    else:
        from django.db.models import Q

        qs = qs.filter(Q(is_suspended=False) | Q(pk__in=allowed))
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


def _resolve_stock_take_shops(shop_ids, *, allow_shop_ids=None) -> list:
    shops = []
    seen = set()
    from shops.models import Shop

    allowed = set()
    for raw in allow_shop_ids or []:
        try:
            allowed.add(int(raw))
        except (TypeError, ValueError):
            continue

    for raw in shop_ids or []:
        try:
            shop_id = int(raw)
        except (TypeError, ValueError):
            continue
        if shop_id in seen:
            continue
        seen.add(shop_id)
        qs = Shop.objects.filter(pk=shop_id).order_by()
        if shop_id not in allowed:
            qs = qs.filter(is_hidden=False, is_suspended=False)
        shop = qs.first()
        if shop:
            shops.append(shop)
    if not shops:
        raise ValidationError("Select at least one shop.")
    return shops


def _normalize_stock_take_deadline(deadline, *, require_future: bool):
    if deadline is None:
        raise ValidationError("Set a deadline for the stock take.")
    if timezone.is_naive(deadline):
        deadline = timezone.make_aware(deadline, timezone.get_current_timezone())
    if require_future and deadline <= timezone.now():
        raise ValidationError("Deadline must be in the future.")
    return deadline


def _cleaned_scope_payload(*, mode: str, items: list[Item]) -> tuple[list[str], list[int]]:
    cleaned_categories: list[str] = []
    cleaned_item_ids: list[int] = []
    if mode == StockTakeScope.CATEGORY:
        for item in items:
            cat = item.category or ""
            if cat not in cleaned_categories:
                cleaned_categories.append(cat)
    else:
        cleaned_item_ids = [item.pk for item in items]
    return cleaned_categories, cleaned_item_ids


def _stock_map_for(*, shop_ids: list[int], item_ids: list[int]) -> dict:
    if not shop_ids or not item_ids:
        return {}
    return {
        (row.shop_id, row.item_id): row.quantity
        for row in ShopStock.objects.filter(
            shop_id__in=shop_ids,
            item_id__in=item_ids,
        ).only("shop_id", "item_id", "quantity")
    }


def _create_assignment_counts(*, assignment, shop_id: int, items: list[Item], stock_map: dict):
    StockTakeCount.objects.bulk_create(
        [
            StockTakeCount(
                assignment=assignment,
                item=item,
                system_qty=_quantize(stock_map.get((shop_id, item.pk), _ZERO)),
            )
            for item in items
        ]
    )


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
    deadline = _normalize_stock_take_deadline(deadline, require_future=True)
    shops = _resolve_stock_take_shops(shop_ids)
    items = resolve_stock_take_items(
        scope_mode=mode, categories=categories, item_ids=item_ids
    )
    cleaned_categories, cleaned_item_ids = _cleaned_scope_payload(mode=mode, items=items)

    take = StockTake.objects.create(
        scope_mode=mode,
        categories=cleaned_categories,
        item_ids=cleaned_item_ids,
        deadline=deadline,
        status=StockTakeStatus.OPEN,
        note=(note or "").strip()[:240],
        created_by=profile,
    )

    stock_map = _stock_map_for(
        shop_ids=[shop.pk for shop in shops],
        item_ids=[item.pk for item in items],
    )

    for shop in shops:
        assignment = StockTakeAssignment.objects.create(
            stock_take=take,
            shop=shop,
            status=StockTakeAssignmentStatus.PENDING,
        )
        _create_assignment_counts(
            assignment=assignment,
            shop_id=shop.pk,
            items=items,
            stock_map=stock_map,
        )

    invalidate_stock_take_hide_cache()
    return take


@transaction.atomic
def update_stock_take(
    *,
    take: StockTake,
    scope_mode: str,
    categories,
    item_ids,
    shop_ids,
    deadline=None,
    note: str | None = None,
) -> StockTake:
    """Edit an open stock take: add/remove shops and category/item coverage."""
    take = StockTake.objects.select_for_update().get(pk=take.pk)
    if take.status != StockTakeStatus.OPEN:
        raise ValidationError("Closed stock takes cannot be edited.")

    mode = (scope_mode or "").strip().lower()
    if mode not in {StockTakeScope.CATEGORY, StockTakeScope.ITEMS}:
        raise ValidationError("Choose whether to stock-take by category or by item.")

    assignments = list(
        StockTakeAssignment.objects.select_for_update()
        .filter(stock_take=take)
        .prefetch_related("counts")
    )
    by_shop = {row.shop_id: row for row in assignments}
    shops = _resolve_stock_take_shops(
        shop_ids, allow_shop_ids=list(by_shop.keys())
    )
    existing_item_ids = {
        count.item_id
        for assignment in assignments
        for count in assignment.counts.all()
    }
    items = resolve_stock_take_items(
        scope_mode=mode,
        categories=categories,
        item_ids=item_ids,
        allow_item_ids=existing_item_ids,
    )
    cleaned_categories, cleaned_item_ids = _cleaned_scope_payload(mode=mode, items=items)
    item_id_set = {item.pk for item in items}
    shop_id_set = {shop.pk for shop in shops}

    take.scope_mode = mode
    take.categories = cleaned_categories
    take.item_ids = cleaned_item_ids
    if note is not None:
        take.note = (note or "").strip()[:240]
    if deadline is not None:
        if timezone.is_naive(deadline):
            deadline = timezone.make_aware(deadline, timezone.get_current_timezone())
        # Keep an overdue deadline if the user did not change it; new values must be future.
        if deadline != take.deadline:
            deadline = _normalize_stock_take_deadline(deadline, require_future=True)
        take.deadline = deadline
    take.save(
        update_fields=["scope_mode", "categories", "item_ids", "note", "deadline"]
    )

    remove_ids = [row.pk for row in assignments if row.shop_id not in shop_id_set]
    if remove_ids:
        StockTakeAssignment.objects.filter(pk__in=remove_ids).delete()

    stock_map = _stock_map_for(
        shop_ids=list(shop_id_set),
        item_ids=list(item_id_set),
    )
    items_by_id = {item.pk: item for item in items}

    for shop in shops:
        assignment = by_shop.get(shop.pk)
        if assignment is None:
            assignment = StockTakeAssignment.objects.create(
                stock_take=take,
                shop=shop,
                status=StockTakeAssignmentStatus.PENDING,
            )
            _create_assignment_counts(
                assignment=assignment,
                shop_id=shop.pk,
                items=items,
                stock_map=stock_map,
            )
            continue

        existing_counts = list(assignment.counts.all())
        existing_item_ids = {count.item_id for count in existing_counts}
        drop_ids = [
            count.pk
            for count in existing_counts
            if count.item_id not in item_id_set
        ]
        if drop_ids:
            StockTakeCount.objects.filter(pk__in=drop_ids).delete()
            existing_item_ids &= item_id_set

        added_items = [
            items_by_id[item_id]
            for item_id in item_id_set
            if item_id not in existing_item_ids
        ]
        if added_items:
            _create_assignment_counts(
                assignment=assignment,
                shop_id=shop.pk,
                items=added_items,
                stock_map=stock_map,
            )
            if assignment.status == StockTakeAssignmentStatus.SUBMITTED:
                assignment.status = StockTakeAssignmentStatus.PENDING
                assignment.submitted_at = None
                assignment.submitted_by_label = ""
                assignment.save(
                    update_fields=[
                        "status",
                        "submitted_at",
                        "submitted_by_label",
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


def pending_stock_take_count_for_shop(shop) -> int:
    """How many open stock takes this shop still needs to submit."""
    return (
        StockTakeAssignment.objects.filter(
            shop=shop,
            status=StockTakeAssignmentStatus.PENDING,
            stock_take__status=StockTakeStatus.OPEN,
        )
        .order_by()
        .count()
    )


def build_stock_take_performance(*, current_shop=None, limit=20) -> list[dict]:
    """Recent stock takes with every shop's submission status and marks."""
    takes = list(
        StockTake.objects.prefetch_related(
            "assignments__shop",
            "assignments__counts",
        ).order_by("-created_at", "-id")[:limit]
    )
    boards = []
    for take in takes:
        assignments = sorted(
            take.assignments.all(),
            key=lambda row: ((row.shop.name or "").lower(), row.pk),
        )
        shop_rows = []
        submitted = 0
        for assignment in assignments:
            counts = list(assignment.counts.all())
            total = len(counts)
            answered = sum(1 for row in counts if row.counted_qty is not None)
            correct = sum(
                1
                for row in counts
                if row.counted_qty is not None
                and _quantize(row.counted_qty) == _quantize(row.system_qty)
            )
            is_submitted = (
                assignment.status == StockTakeAssignmentStatus.SUBMITTED
            )
            if is_submitted:
                submitted += 1
            percent = (
                int(round((100 * correct / total), 0))
                if is_submitted and total
                else None
            )
            shop_rows.append(
                {
                    "assignment": assignment,
                    "shop": assignment.shop,
                    "is_current": bool(
                        current_shop and assignment.shop_id == current_shop.pk
                    ),
                    "submitted": is_submitted,
                    "answered_count": answered,
                    "total_count": total,
                    "marks": correct if is_submitted else None,
                    "max_marks": total,
                    "percent": percent,
                    "submitted_at": assignment.submitted_at,
                }
            )
        boards.append(
            {
                "take": take,
                "shops": shop_rows,
                "shop_count": len(shop_rows),
                "submitted_count": submitted,
                "pending_count": len(shop_rows) - submitted,
                "overdue": bool(
                    take.status == StockTakeStatus.OPEN
                    and take.deadline
                    and take.deadline <= timezone.now()
                ),
            }
        )
    return boards


def _variance_display(*, variance: Decimal | None) -> dict:
    """Human labels for counted − system (excess / short / match)."""
    if variance is None:
        return {
            "variance": None,
            "variance_qty": None,
            "variance_kind": None,
            "variance_label": None,
            "match": False,
        }
    qty = _quantize(variance)
    abs_qty = abs(qty)
    label_qty = _qty_label(abs_qty)
    pieces = _pieces_word(abs_qty)
    if qty == 0:
        return {
            "variance": "0",
            "variance_qty": "0",
            "variance_kind": "match",
            "variance_label": "Match",
            "match": True,
        }
    if qty > 0:
        return {
            "variance": _qty_label(qty),
            "variance_qty": _qty_label(qty),
            "variance_kind": "excess",
            "variance_label": f"Excess by {label_qty} {pieces}",
            "match": False,
        }
    return {
        "variance": _qty_label(qty),
        "variance_qty": _qty_label(qty),
        "variance_kind": "short",
        "variance_label": f"Short by {label_qty} {pieces}",
        "match": False,
    }


def build_shop_stock_count_results(*, shop, limit=20) -> list[dict]:
    """This shop's stock-count line results: input vs system vs variance."""
    if shop is None:
        return []
    assignments = list(
        StockTakeAssignment.objects.filter(shop=shop)
        .select_related("stock_take")
        .prefetch_related("counts__item")
        .order_by("-stock_take__created_at", "-id")[:limit]
    )
    results = []
    for assignment in assignments:
        take = assignment.stock_take
        is_submitted = assignment.status == StockTakeAssignmentStatus.SUBMITTED
        rows = []
        correct = 0
        excess_count = 0
        short_count = 0
        for count in assignment.counts.all():
            item = count.item
            system = _quantize(count.system_qty)
            counted = (
                _quantize(count.counted_qty)
                if count.counted_qty is not None
                else None
            )
            variance = None if counted is None else counted - system
            display = _variance_display(variance=variance)
            if display["match"]:
                correct += 1
            elif display["variance_kind"] == "excess":
                excess_count += 1
            elif display["variance_kind"] == "short":
                short_count += 1
            rows.append(
                {
                    "item": item,
                    "counted_qty": (
                        _qty_label(counted) if counted is not None else None
                    ),
                    "system_qty": _qty_label(system),
                    **display,
                }
            )
        # Show mismatches first so excess/short jumps out after submit.
        if is_submitted:
            kind_order = {"short": 0, "excess": 1, "match": 2, None: 3}
            rows.sort(
                key=lambda row: (
                    kind_order.get(row.get("variance_kind"), 3),
                    (row["item"].name or "").lower(),
                    row["item"].pk,
                )
            )
        total = len(rows)
        percent = (
            int(round((100 * correct / total), 0))
            if is_submitted and total
            else None
        )
        results.append(
            {
                "take": take,
                "assignment": assignment,
                "submitted": is_submitted,
                "submitted_at": assignment.submitted_at,
                "submitted_by_label": assignment.submitted_by_label or "",
                "rows": rows,
                "marks": correct if is_submitted else None,
                "max_marks": total,
                "percent": percent,
                "excess_count": excess_count if is_submitted else None,
                "short_count": short_count if is_submitted else None,
                "overdue": bool(
                    take.status == StockTakeStatus.OPEN
                    and take.deadline
                    and take.deadline <= timezone.now()
                ),
            }
        )
    # Submitted scorecards first so this shop can open results immediately.
    results.sort(
        key=lambda row: (
            0 if row["submitted"] else 1,
            -(row["submitted_at"].timestamp() if row["submitted_at"] else 0),
            -row["take"].pk,
        )
    )
    return results


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
        "overdue": bool(take.deadline and take.deadline <= timezone.now()),
        "answered_count": answered,
        "total_count": len(lines),
        "lines": lines,
    }


def online_stock_take_assignment_payload(assignment: StockTakeAssignment) -> dict:
    """HQ/online count form: includes system qty so variance can update live."""
    payload = stock_take_assignment_payload(assignment)
    system_by_count = {
        count.pk: _qty_label(_quantize(count.system_qty))
        for count in assignment.counts.all()
    }
    for line in payload["lines"]:
        line["system_qty"] = system_by_count.get(line["count_id"], "0")
    return payload


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


def _pieces_word(abs_diff: Decimal) -> str:
    return "piece" if abs_diff == 1 else "pieces"


def _diff_status_label(*, match: bool, abs_diff: Decimal | None) -> str:
    if match:
        return "Correct"
    if abs_diff is None:
        return "Wrong"
    return f"Wrong by {_qty_label(abs_diff)} {_pieces_word(abs_diff)}"


def _shop_line_result(count: StockTakeCount) -> dict:
    item = count.item
    system = _quantize(count.system_qty)
    counted = (
        _quantize(count.counted_qty) if count.counted_qty is not None else None
    )
    match = counted is not None and counted == system
    diff = None if counted is None else counted - system
    abs_diff = None if diff is None else abs(diff)
    return {
        "count_id": count.pk,
        "item_id": item.pk,
        "name": item.name,
        "category": item.category or "",
        "counted_qty": _qty_label(counted) if counted is not None else "—",
        "match": match,
        "diff": _qty_label(diff) if diff is not None else "",
        "abs_diff": _qty_label(abs_diff) if abs_diff is not None else "",
        "status_label": _diff_status_label(match=match, abs_diff=abs_diff),
        # System qty intentionally omitted for shop floor feedback.
    }


def _grade_payload(*, correct: int, total: int, pieces_off: Decimal = _ZERO) -> dict:
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
    off = _quantize(pieces_off)
    return {
        "marks": correct,
        "correct": correct,
        "max_marks": total,
        "wrong": wrong,
        "pieces_off": _qty_label(off),
        "percent": percent,
        "grade": grade,
        "grade_label": grade_label,
        "headline": headline,
    }


def build_stock_take_result(assignment: StockTakeAssignment) -> dict:
    """Shop scorecard: correct/wrong + piece gaps. Never exposes system qty."""
    rows = list(assignment.counts.select_related("item").all())
    lines = [_shop_line_result(count) for count in rows]
    correct = sum(1 for line in lines if line["match"])
    pieces_off = _ZERO
    for count in rows:
        if count.counted_qty is None:
            continue
        pieces_off += abs(_quantize(count.counted_qty) - _quantize(count.system_qty))
    payload = _grade_payload(
        correct=correct, total=len(lines), pieces_off=pieces_off
    )
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
        # Unhide this shop's covered items; other pending shops stay hidden.
        invalidate_stock_take_hide_cache()
    return True


def _assert_assignment_submittable(assignment: StockTakeAssignment) -> None:
    """Reject closed, already-submitted, or past-deadline counts."""
    take = assignment.stock_take
    if take.status != StockTakeStatus.OPEN:
        raise ValidationError("This stock take is closed.")
    if assignment.status == StockTakeAssignmentStatus.SUBMITTED:
        raise ValidationError("This shop already submitted its stock take.")
    if take.deadline and take.deadline <= timezone.now():
        raise ValidationError(
            "The stock take deadline has passed. Counting is locked."
        )


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
    _assert_assignment_submittable(assignment)

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
    _assert_assignment_submittable(assignment)

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


def build_stock_take_shop_totals(*, start=None, end=None, shop_ids=None) -> dict:
    """Overall accuracy % per shop across all allocated counts in a period.

    Takes are included by ``created_at``. Every allocated item counts toward
    the denominator — unsubmitted (pending) assignments score 0 for those
    lines so missing submits pull the shop average down.
    """
    takes_qs = StockTake.objects.all()
    if start is not None:
        takes_qs = takes_qs.filter(created_at__gte=start)
    if end is not None:
        takes_qs = takes_qs.filter(created_at__lt=end)
    take_ids = list(takes_qs.values_list("id", flat=True))

    assignments_qs = (
        StockTakeAssignment.objects.filter(stock_take_id__in=take_ids)
        .select_related("shop")
        .prefetch_related("counts")
        .order_by("shop__name", "id")
    )
    if shop_ids is not None:
        assignments_qs = assignments_qs.filter(shop_id__in=list(shop_ids))

    by_shop: dict[int, dict] = {}
    for assignment in assignments_qs:
        shop = assignment.shop
        bucket = by_shop.setdefault(
            shop.pk,
            {
                "shop": shop,
                "correct": 0,
                "total": 0,
                "submitted_takes": 0,
                "pending_takes": 0,
                "take_count": 0,
            },
        )
        bucket["take_count"] += 1
        counts = list(assignment.counts.all())
        bucket["total"] += len(counts)
        is_submitted = assignment.status == StockTakeAssignmentStatus.SUBMITTED
        if is_submitted:
            bucket["submitted_takes"] += 1
            bucket["correct"] += sum(
                1
                for row in counts
                if row.counted_qty is not None
                and _quantize(row.counted_qty) == _quantize(row.system_qty)
            )
        else:
            bucket["pending_takes"] += 1

    shops = []
    for bucket in by_shop.values():
        total = bucket["total"]
        correct = bucket["correct"]
        has_score = total > 0
        grade = _grade_payload(correct=correct, total=total) if has_score else None
        shops.append(
            {
                "shop": bucket["shop"],
                "marks": grade["marks"] if grade else None,
                "max_marks": total if has_score else 0,
                "percent": grade["percent"] if grade else None,
                "grade": grade["grade"] if grade else None,
                "grade_label": grade["grade_label"] if grade else None,
                "submitted_takes": bucket["submitted_takes"],
                "pending_takes": bucket["pending_takes"],
                "take_count": bucket["take_count"],
                "has_score": has_score,
            }
        )
    shops.sort(
        key=lambda row: (
            0 if row["has_score"] else 1,
            -(row["percent"] if row["percent"] is not None else -1),
            (row["shop"].name or "").lower(),
            row["shop"].pk,
        )
    )
    scored = [row for row in shops if row["has_score"]]
    overall_correct = sum(row["marks"] or 0 for row in scored)
    overall_total = sum(row["max_marks"] or 0 for row in scored)
    overall = (
        _grade_payload(correct=overall_correct, total=overall_total)
        if overall_total
        else None
    )
    return {
        "shops": shops,
        "shop_count": len(shops),
        "scored_shop_count": len(scored),
        "take_count": len(take_ids),
        "overall_percent": overall["percent"] if overall else None,
        "overall_marks": overall["marks"] if overall else None,
        "overall_max_marks": overall_total if overall_total else 0,
        "overall_grade": overall["grade"] if overall else None,
    }


def build_stock_take_comparison(take: StockTake) -> dict:
    assignments = list(
        take.assignments.select_related("shop")
        .prefetch_related("counts__item")
        .order_by("shop__name")
    )
    item_map: dict[int, dict] = {}
    shop_rows = []
    for assignment in assignments:
        counts = list(assignment.counts.all())
        is_submitted = assignment.status == StockTakeAssignmentStatus.SUBMITTED
        correct = 0
        for count in counts:
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
            match = variance == 0 if variance is not None else False
            if match:
                correct += 1
            row["shops"][assignment.shop_id] = {
                "system_qty": system,
                "counted_qty": counted,
                "variance": variance,
                "submitted": is_submitted,
                "match": match,
            }
        total = len(counts)
        grade = _grade_payload(correct=correct, total=total) if is_submitted and total else None
        shop_rows.append(
            {
                "assignment": assignment,
                "shop": assignment.shop,
                "status": assignment.status,
                "submitted": is_submitted,
                "marks": grade["marks"] if grade else None,
                "max_marks": total,
                "percent": grade["percent"] if grade else None,
                "grade": grade["grade"] if grade else None,
                "grade_label": grade["grade_label"] if grade else None,
            }
        )

    def _row_variance_kind(cells) -> str | None:
        """Worst submitted variance on the row: short → excess → match."""
        has_short = False
        has_excess = False
        has_match = False
        for cell in cells:
            variance = cell.get("variance")
            if variance is None:
                continue
            if variance < 0:
                has_short = True
            elif variance > 0:
                has_excess = True
            else:
                has_match = True
        if has_short:
            return "short"
        if has_excess:
            return "excess"
        if has_match:
            return "match"
        return None

    def _row_sort_key(entry):
        kind_order = {"short": 0, "excess": 1, "match": 2, None: 3}
        # Among shorts/excess, larger absolute gap first; then name.
        worst_gap = _ZERO
        for cell in entry["cells"]:
            variance = cell.get("variance")
            if variance is None:
                continue
            gap = abs(_quantize(variance))
            if gap > worst_gap:
                worst_gap = gap
        return (
            kind_order.get(entry.get("variance_kind"), 3),
            -worst_gap,
            (entry["item"].category or "").lower(),
            entry["item"].name.lower(),
            entry["item"].pk,
        )

    rows = []
    for entry in item_map.values():
        cells = []
        for assignment in assignments:
            cell = entry["shops"].get(assignment.shop_id)
            if cell is None:
                cell = {
                    "system_qty": _ZERO,
                    "counted_qty": None,
                    "variance": None,
                    "submitted": False,
                    "match": False,
                }
            cells.append(cell)
        rows.append(
            {
                "item": entry["item"],
                "cells": cells,
                "variance_kind": _row_variance_kind(cells),
            }
        )
    rows.sort(key=_row_sort_key)

    submitted = sum(1 for shop in shop_rows if shop["submitted"])
    return {
        "assignments": assignments,
        "shops": shop_rows,
        "rows": rows,
        "submitted_count": submitted,
        "shop_count": len(assignments),
        "pending_count": len(assignments) - submitted,
        "overdue": bool(
            take.status == StockTakeStatus.OPEN
            and take.deadline
            and take.deadline <= timezone.now()
        ),
    }
