"""Shared stock-movement ledger page (period summary + timeline).

Renders the same movements UX as the stock-management movements page
(``items/stock_report.html``) for ledger-backed stock sources that do not
flow through the shop-floor ``StockMovement`` pipeline:

- ``"shelf"``: :class:`ShelfStockMovement` / :class:`ShelfItem` — per-shop
  shelf stock (used by the store-manager shelf page and the
  stock-management Shelf stock tab).
- ``"store"``: :class:`StoreStockMovement` / :class:`Item` — the central
  store item ledger (store-manager Item stock movement page).

The page mirrors ``items/movement_report.html``: Time range, View by
(Period summary / Timeline), Type multi-filter, item scope (All /
Category / Item picker), live search, per-item summary with drill-down
into a receipt-grouped timeline, pagination and PDF download.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

from django.http import JsonResponse

from .models import (
    ShelfItem,
    ShelfStockMovement,
    ShelfStockMovementType,
    StockOutReason,
    StoreStock,
    StoreStockMovement,
    StoreStockMovementType,
)
from .views import (
    MOVEMENT_EVENT_FILTER_LABELS,
    _employee_display_name,
    _event_filters_are_all,
    _filter_item_summary_movement_events,
    _filter_movement_events,
    _filter_movement_events_by_search,
    _filter_movement_item_rows_by_search,
    _filter_timeline_display_events,
    _group_movement_events_by_item,
    _group_timeline_events_by_receipt,
    _movement_event_filter_label,
    _movements_report_params,
    _normalize_report_search_q,
    _ordered_event_filters,
    _parse_id_list,
    _parse_movement_event_filters,
    _parse_report_kind,
    _parse_stock_view_by,
    _qty_num,
    _report_page_urls,
    _report_range_bounds,
    _stock_report_download,
    _suspended_item_ids,
    _summarize_movement_events,
    _trade_receipt_number_from_text,
)

CENTRAL_STORE_LABEL = "Central store"
SHOP_PORTAL_ACTOR = "Shop portal"

_RANGE_LABELS = {
    "day": "Single day",
    "period": "Period",
    "month": "Month",
    "year": "Year",
}

_TRADE_LABELS = {
    StockOutReason.TRADE_IN: "Trade in",
    StockOutReason.TRADE_OUT: "Trade out",
}


def _actor_name(profile, *, fallback=""):
    name = _employee_display_name(profile)
    if name == "—":
        return fallback or name
    return name


def _trade_label_from_text(text):
    """Trade overlay label from a kind/note string (POS mirror notes)."""
    kind = (text or "").strip().lower()
    if not kind or "trade" not in kind:
        return ""
    if "return" in kind:
        return "Trade return"
    if "exchange" in kind:
        return "Trade exchange"
    if "out" in kind:
        return "Trade out"
    if "in" in kind:
        return "Trade in"
    return "Trade"


def _money_note_bits(*, buying_price=None, refund="", refund_amount=None):
    bits = []
    if buying_price is not None:
        bits.append(f"Buy {buying_price}")
    if refund:
        label = f"Refund {refund}"
        if refund_amount is not None:
            label += f" ({refund_amount})"
        bits.append(label)
    return bits


# ---------------------------------------------------------------------------
# Shelf stock source (ShelfItem / ShelfStockMovement)
# ---------------------------------------------------------------------------


def _shelf_items_for_shops(shop_ids):
    qs = ShelfItem.objects.order_by("name")
    if shop_ids:
        qs = qs.filter(shop_id__in=list(shop_ids))
    return list(qs)


def _shelf_namespaces(shop_ids):
    return [
        SimpleNamespace(
            pk=item.pk,
            name=item.name,
            category=item.category or "",
            unit=item.unit or "",
            is_suspended=bool(item.is_archived),
            item_badge="Archived" if item.is_archived else "",
            shop_id=item.shop_id,
        )
        for item in _shelf_items_for_shops(shop_ids)
    ]


def _shelf_categories(shop_ids):
    qs = ShelfItem.objects.order_by("category")
    if shop_ids:
        qs = qs.filter(shop_id__in=list(shop_ids))
    return [
        value
        for value in qs.values_list("category", flat=True).distinct()
        if (value or "").strip()
    ]


def _shelf_stock_lookups(shop_ids):
    """(stock_by_item, stock_by_item_shop, archived_ids) for shelf items."""
    stock_by_item = {}
    stock_by_item_shop = {}
    archived = set()
    for item in ShelfItem.objects.only(
        "id", "shop_id", "quantity", "is_archived"
    ).filter(shop_id__in=list(shop_ids)):
        stock_by_item_shop[(item.pk, item.shop_id)] = item.quantity
        stock_by_item[item.pk] = (
            stock_by_item.get(item.pk, Decimal("0")) + item.quantity
        )
        if item.is_archived:
            archived.add(item.pk)
    return stock_by_item, stock_by_item_shop, archived


def _shelf_system_qty(item_ids, shop_ids, *, day_end=None):
    """On-shelf qty per shelf item, rewound to day_end when the range is past."""
    from django.utils import timezone as dj_timezone

    item_ids = [pk for pk in (item_ids or []) if pk is not None]
    totals = {pk: Decimal("0") for pk in item_ids}
    if not item_ids:
        return totals
    for item_id, qty in ShelfItem.objects.filter(
        pk__in=item_ids
    ).values_list("id", "quantity"):
        totals[item_id] = qty
    now = dj_timezone.now()
    if day_end is None or day_end >= now:
        return totals
    qs = ShelfStockMovement.objects.filter(
        shelf_item_id__in=item_ids, created_at__gte=day_end
    )
    if shop_ids:
        qs = qs.filter(shelf_item__shop_id__in=list(shop_ids))
    for item_id, movement_type, qty in qs.values_list(
        "shelf_item_id", "movement_type", "quantity"
    ):
        signed = qty if movement_type != ShelfStockMovementType.OUT else -qty
        totals[item_id] = totals.get(item_id, Decimal("0")) - signed
    return totals


def _shelf_buying_prices(rows, shop_ids=None):
    """Latest recorded buying price per shelf item (for PDF estimates)."""
    from django.db.models import Max

    item_ids = [row.get("item_id") for row in rows if row.get("item_id")]
    if not item_ids:
        return {}
    latest = (
        ShelfStockMovement.objects.filter(
            shelf_item_id__in=item_ids, buying_price__isnull=False
        )
        .values("shelf_item_id")
        .annotate(last=Max("created_at"))
    )
    last_at = {row["shelf_item_id"]: row["last"] for row in latest}
    prices = {}
    for shelf_item_id, created_at, price in (
        ShelfStockMovement.objects.filter(
            shelf_item_id__in=item_ids, buying_price__isnull=False
        ).values_list("shelf_item_id", "created_at", "buying_price")
    ):
        if created_at == last_at.get(shelf_item_id):
            prices[shelf_item_id] = price
    return prices


def _shelf_movement_events(
    *,
    shop_ids,
    day_start,
    day_end,
    item_mode,
    selected_categories,
    selected_item_ids,
):
    qs = (
        ShelfStockMovement.objects.filter(
            created_at__gte=day_start,
            created_at__lt=day_end,
        )
        .select_related("shelf_item__shop", "created_by__user")
        .order_by("created_at", "id")
    )
    if shop_ids:
        qs = qs.filter(shelf_item__shop_id__in=list(shop_ids))
    if item_mode == "category" and selected_categories:
        qs = qs.filter(shelf_item__category__in=selected_categories)
    elif item_mode == "items" and selected_item_ids:
        qs = qs.filter(shelf_item_id__in=selected_item_ids)

    events = []
    for mv in qs:
        item = mv.shelf_item
        shop = item.shop
        is_out = mv.movement_type == ShelfStockMovementType.OUT
        is_register = mv.movement_type == ShelfStockMovementType.REGISTER
        reason_label = mv.get_reason_display() if mv.reason else ""
        supplier = (mv.supplier_name or "").strip()
        trade = _TRADE_LABELS.get(mv.reason or "", "")
        if is_out:
            from_label = shop.name
            to_label = supplier or reason_label or "—"
            seller = "—"
        else:
            from_label = supplier or "—"
            to_label = shop.name
            seller = supplier or "—"
        pay = mv.get_payment_status_display() if mv.payment_status else ""
        note_bits = _money_note_bits(
            buying_price=mv.buying_price if not is_out else None,
            refund=mv.refund,
            refund_amount=mv.refund_amount,
        )
        if (mv.note or "").strip():
            note_bits.append(mv.note.strip())
        events.append(
            {
                "happened_at": mv.created_at,
                "event_type": mv.movement_type,  # register / in / out
                "event_label": (
                    "Registered" if is_register else ("Stock out" if is_out else "Stock in")
                ),
                "shop_name": shop.name,
                "shop_id": shop.pk,
                "source_shop_id": shop.pk,
                "destination_shop_id": None,
                "from_shop_name": "",
                "to_shop_name": "",
                "item_name": item.name,
                "item_name_snapshot": "",
                "item_category": item.category or "",
                "item_id": item.pk,
                "item_suspended": bool(item.is_archived),
                "item_badge": "Archived" if item.is_archived else "",
                "quantity": _qty_num(mv.quantity),
                "reason": reason_label,
                "payment_status": pay,
                "pay": pay,
                "note": " · ".join(note_bits),
                "by": _actor_name(mv.created_by, fallback=SHOP_PORTAL_ACTOR),
                "seller": seller,
                "sent_by": "",
                "requested_by": "",
                "received_by": "",
                "serial_numbers": [],
                "movement_id": mv.pk,
                "receipt_number": "",
                "receipt_status": "",
                "trade": trade,
                "from_label": from_label,
                "to_label": to_label,
                "transfer_direction": "",
                "counts_toward_transfer": False,
                "balance_after": mv.balance_after,
            }
        )
    return events


# ---------------------------------------------------------------------------
# Central store source (Item / StoreStockMovement)
# ---------------------------------------------------------------------------


def _store_items_queryset():
    from .models import Item

    return Item.objects.order_by("category", "name")


def _store_categories():
    from .models import Item

    return list(
        Item.objects.order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )


def _store_stock_lookup():
    """{item_id: central-store qty} for the summary Stock column."""
    return {
        row.item_id: row.quantity
        for row in StoreStock.objects.only("item_id", "quantity")
    }


def _store_system_qty(item_ids, shop_ids=None, *, day_end=None):
    """Central-store qty per item, rewound to day_end when the range is past."""
    from django.utils import timezone as dj_timezone

    item_ids = [pk for pk in (item_ids or []) if pk is not None]
    totals = {pk: Decimal("0") for pk in item_ids}
    if not item_ids:
        return totals
    for item_id, qty in StoreStock.objects.filter(
        item_id__in=item_ids
    ).values_list("item_id", "quantity"):
        totals[item_id] = qty
    now = dj_timezone.now()
    if day_end is None or day_end >= now:
        return totals
    for item_id, movement_type, qty in StoreStockMovement.objects.filter(
        item_id__in=item_ids, created_at__gte=day_end
    ).values_list("item_id", "movement_type", "quantity"):
        signed = qty if movement_type == StoreStockMovementType.IN else -qty
        totals[item_id] = totals.get(item_id, Decimal("0")) - signed
    return totals


def _store_buying_prices(rows, shop_ids=None):
    """Latest recorded buying price per item from the store ledger."""
    from django.db.models import Max

    item_ids = [row.get("item_id") for row in rows if row.get("item_id")]
    if not item_ids:
        return {}
    latest = (
        StoreStockMovement.objects.filter(
            item_id__in=item_ids, buying_price__isnull=False
        )
        .values("item_id")
        .annotate(last=Max("created_at"))
    )
    last_at = {row["item_id"]: row["last"] for row in latest}
    prices = {}
    for item_id, created_at, price in StoreStockMovement.objects.filter(
        item_id__in=item_ids, buying_price__isnull=False
    ).values_list("item_id", "created_at", "buying_price"):
        if created_at == last_at.get(item_id):
            prices[item_id] = price
    return prices


def _store_movement_events(
    *,
    day_start,
    day_end,
    item_mode,
    selected_categories,
    selected_item_ids,
):
    from .store_stock_services import STORE_SOURCE_POS

    qs = (
        StoreStockMovement.objects.filter(
            created_at__gte=day_start,
            created_at__lt=day_end,
        )
        .select_related("item", "created_by__user", "received_by__user")
        .order_by("created_at", "id")
    )
    if item_mode == "category" and selected_categories:
        qs = qs.filter(item__category__in=selected_categories)
    elif item_mode == "items" and selected_item_ids:
        qs = qs.filter(item_id__in=selected_item_ids)

    events = []
    for mv in qs:
        item = mv.item
        is_out = mv.movement_type == StoreStockMovementType.OUT
        is_pos = (mv.source or "").strip() == STORE_SOURCE_POS
        raw_note = (mv.note or "").strip()
        receipt_number = ""
        kind_label = ""
        note_shop = ""
        if is_pos and " · " in raw_note:
            parts = raw_note.split(" · ")
            receipt_number = parts[0].strip()
            kind_label = parts[1].strip() if len(parts) > 1 else ""
            note_shop = parts[2].strip() if len(parts) > 2 else ""
        elif not is_pos:
            receipt_number = _trade_receipt_number_from_text(raw_note)
        reason_label = mv.get_reason_display() if mv.reason else ""
        trade = _TRADE_LABELS.get(mv.reason or "", "") or _trade_label_from_text(
            kind_label
        )

        kind_l = kind_label.lower()
        if is_pos and "sale" in kind_l:
            event_type = "sale"
        elif is_pos and "return" in kind_l:
            event_type = "return"
        else:
            event_type = "out" if is_out else "in"
        if is_pos and kind_label:
            event_label = kind_label
        else:
            event_label = "Stock out" if is_out else "Stock in"

        if is_out:
            from_label = CENTRAL_STORE_LABEL
            to_label = note_shop or (mv.supplier_name or "").strip() or reason_label or "—"
            seller = "—"
        else:
            from_label = (
                (mv.supplier_name or "").strip() or note_shop or "—"
            )
            to_label = CENTRAL_STORE_LABEL
            seller = (mv.supplier_name or "").strip() or "—"
        pay = mv.get_payment_status_display() if mv.payment_status else ""
        note_bits = _money_note_bits(
            buying_price=mv.buying_price if not is_out else None,
            refund=mv.refund,
            refund_amount=mv.refund_amount,
        )
        if raw_note:
            note_bits.append(raw_note)
        events.append(
            {
                "happened_at": mv.created_at,
                "event_type": event_type,
                "event_label": event_label,
                "shop_name": note_shop,
                "shop_id": None,
                "source_shop_id": None,
                "destination_shop_id": None,
                "from_shop_name": "",
                "to_shop_name": "",
                "item_name": item.name if item else "Deleted item",
                "item_name_snapshot": "",
                "item_category": item.category if item else "",
                "item_id": mv.item_id,
                "item_suspended": bool(item and item.is_suspended),
                "item_badge": (
                    "Suspended" if (item and item.is_suspended) else ""
                ),
                "quantity": _qty_num(mv.quantity),
                "reason": reason_label,
                "payment_status": pay,
                "pay": pay,
                "note": " · ".join(note_bits),
                "by": _actor_name(mv.created_by),
                "seller": seller,
                "sent_by": "",
                "requested_by": "",
                "received_by": (
                    "" if is_pos else _actor_name(mv.received_by)
                ),
                "serial_numbers": [],
                "movement_id": mv.pk,
                "receipt_number": receipt_number,
                "receipt_status": "",
                "trade": trade,
                "from_label": from_label,
                "to_label": to_label,
                "transfer_direction": "",
                "counts_toward_transfer": False,
                "balance_after": mv.balance_after,
            }
        )
    return events


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

_MOVEMENT_LEDGER_SOURCES = {
    "shelf": {
        "type_order": ("register", "in", "out"),
        "filename_prefix": "shelf-stock-movements",
        "badge": "Archived",
        "has_shops": True,
    },
    "store": {
        "type_order": ("in", "out", "sale", "return"),
        "filename_prefix": "store-stock-movements",
        "badge": "Suspended",
        "has_shops": False,
    },
}


def _source_items(source, shop_ids):
    if source == "shelf":
        return _shelf_namespaces(shop_ids)
    return list(_store_items_queryset())


def _source_categories(source, shop_ids):
    if source == "shelf":
        return _shelf_categories(shop_ids)
    return _store_categories()


def _source_events(
    source,
    *,
    shop_ids,
    day_start,
    day_end,
    item_mode,
    selected_categories,
    selected_item_ids,
):
    if source == "shelf":
        return _shelf_movement_events(
            shop_ids=shop_ids,
            day_start=day_start,
            day_end=day_end,
            item_mode=item_mode,
            selected_categories=selected_categories,
            selected_item_ids=selected_item_ids,
        )
    return _store_movement_events(
        day_start=day_start,
        day_end=day_end,
        item_mode=item_mode,
        selected_categories=selected_categories,
        selected_item_ids=selected_item_ids,
    )


def _picker_items(source, shop_ids):
    items = _source_items(source, shop_ids)
    return [
        {
            "id": item.pk,
            "name": item.name,
            "category": item.category or "",
            "is_suspended": bool(getattr(item, "is_suspended", False)),
            "badge": getattr(item, "item_badge", "") or "",
        }
        for item in items
    ]


def _company_pdf_meta():
    from shops.services import get_company_profile

    company = get_company_profile()
    logo_path = ""
    if getattr(company, "logo", None):
        try:
            logo_path = company.logo.path
        except Exception:
            logo_path = ""
    return {
        "company_name": (getattr(company, "name", None) or "").strip() or "MY-SHOP",
        "company_phone": (getattr(company, "phone_number", None) or "").strip(),
        "company_email": (getattr(company, "email", None) or "").strip(),
        "company_location": (getattr(company, "location", None) or "").strip(),
        "logo_path": logo_path,
    }


def build_movement_ledger_page(
    request,
    *,
    source,
    filter_shops=(),
    base_params=(),
    shop_params=("shop_id",),
    page_title="Stock movement",
):
    """Build the shared ledger movements page.

    Returns ``(response, context)``: ``response`` is a JsonResponse/PDF when
    the request asks for the item-picker catalog or a download, else None;
    ``context`` feeds ``items/movement_report.html`` (plus a few page extras
    such as ``report_params`` for sidebar builders).
    """
    if source not in _MOVEMENT_LEDGER_SOURCES:
        raise ValueError(f"Unknown movement ledger source: {source!r}")
    cfg = _MOVEMENT_LEDGER_SOURCES[source]
    type_order = cfg["type_order"]
    filter_shops = list(filter_shops or [])
    shops_by_id = {shop.pk: shop for shop in filter_shops}

    base_params = [(str(k), str(v)) for k, v in (base_params or []) if str(v) != ""]

    def _page_url(params):
        merged = list(base_params)
        merged.extend(params)
        return f"{request.path}?{urlencode(merged)}" if merged else request.path

    # Shop scope (shelf only — the store ledger has no shop dimension).
    requested_shop_ids = []
    for name in shop_params or ():
        requested_shop_ids.extend(_parse_id_list(request.GET.getlist(name)))
    seen_shops = set()
    selected_shop_ids = []
    for pk in requested_shop_ids:
        if pk in shops_by_id and pk not in seen_shops:
            seen_shops.add(pk)
            selected_shop_ids.append(pk)
    scope_shop_ids = selected_shop_ids or [shop.pk for shop in filter_shops]

    # Lazy item-picker catalog for the filter form's search boxes.
    if (request.GET.get("item_picker") or "").strip() == "1":
        return (
            JsonResponse({"items": _picker_items(source, scope_shop_ids)}),
            {},
        )

    range_type, day_start, day_end, filter_context = _report_range_bounds(request)

    view_by = _parse_stock_view_by(
        request.GET.get("view_by"),
        range_type=range_type,
        page_mode="movements",
    )

    item_mode = (request.GET.get("item_mode") or "all").strip().lower()
    if item_mode not in ("all", "category", "items"):
        item_mode = "all"

    search_q = _normalize_report_search_q(request.GET.get("q"))
    if item_mode != "all":
        search_q = ""

    categories = _source_categories(source, scope_shop_ids)
    selected_categories = [
        value.strip()
        for value in request.GET.getlist("category")
        if (value or "").strip() and (value or "").strip() in set(categories)
    ]

    selected_item_ids_raw = _parse_id_list(request.GET.getlist("item_id"))
    if item_mode == "category" and not selected_categories:
        item_mode = "all"
    elif item_mode == "items" and not selected_item_ids_raw:
        item_mode = "all"

    scope_items = _source_items(source, scope_shop_ids)
    items_by_id = {item.pk: item for item in scope_items}
    if item_mode != "items":
        # Only resolve selected ids — the picker loads the catalog lazily.
        scope_items = []
    selected_item_ids = [
        pk for pk in selected_item_ids_raw if pk in items_by_id
    ]
    if item_mode == "items" and not selected_item_ids:
        item_mode = "all"
    selected_filter_items = [
        items_by_id[pk] for pk in selected_item_ids if pk in items_by_id
    ]
    filter_items_json = (
        json.dumps(_picker_items(source, scope_shop_ids))
        if item_mode == "items"
        else "[]"
    )

    # Type multi-filter (source-specific options).
    event_filters = _parse_movement_event_filters(
        request.GET.getlist("event_type")
    )
    if not _event_filters_are_all(event_filters):
        cleaned = frozenset(key for key in event_filters if key in type_order)
        event_filters = (
            frozenset({"all"})
            if not cleaned or len(cleaned) >= len(type_order)
            else cleaned
        )
    event_filter = (
        "all"
        if _event_filters_are_all(event_filters)
        else (
            _ordered_event_filters(event_filters, type_order=type_order)[0]
            if len(event_filters) == 1
            else "multi"
        )
    )
    selected_event_types = set(
        _ordered_event_filters(event_filters, type_order=type_order)
    )
    if "all" in selected_event_types:
        selected_event_types = set()
    event_filter_options = [
        {"value": key, "label": MOVEMENT_EVENT_FILTER_LABELS.get(key, key)}
        for key in type_order
    ]
    event_filter_label = _movement_event_filter_label(
        event_filters, type_order=type_order
    )

    # ---- Events -------------------------------------------------------------
    movement_events = _source_events(
        source,
        shop_ids=scope_shop_ids,
        day_start=day_start,
        day_end=day_end,
        item_mode=item_mode,
        selected_categories=selected_categories,
        selected_item_ids=selected_item_ids,
    )
    if not _event_filters_are_all(event_filters):
        movement_events = _filter_movement_events(movement_events, event_filters)

    is_item_movement_detail = (
        view_by == "timeline"
        and item_mode == "items"
        and len(selected_item_ids) == 1
    )
    is_item_movement_summary = view_by == "item" and not is_item_movement_detail

    # Current-stock lookups for the source's ledger.
    if source == "shelf":
        stock_by_item, stock_by_item_shop, suspended_ids = _shelf_stock_lookups(
            scope_shop_ids
        )
    else:
        stock_by_item = _store_stock_lookup()
        stock_by_item_shop = {}
        suspended_ids = _suspended_item_ids(
            [event.get("item_id") for event in movement_events if event.get("item_id")]
        )

    movement_event_groups = []
    movement_item_rows = []
    movement_item_totals = {
        "current_stock": 0,
        "event_count": 0,
        "units_in": 0,
        "units_out": 0,
        "units_transfer_in": 0,
        "units_transfer_out": 0,
        "units_sale": 0,
        "units_return": 0,
        "units_trade": 0,
    }
    units_in = units_out = units_request = 0
    units_sale = units_transfer_in = units_transfer_out = units_return = 0
    timeline_page = 1
    timeline_page_size = 100
    timeline_total_groups = 0
    timeline_total_pages = 1
    timeline_prev_url = ""
    timeline_next_url = ""
    timeline_current_stock = 0

    group_by_shop = cfg["has_shops"] and len(scope_shop_ids) > 1
    show_all_items = _event_filters_are_all(event_filters)

    if view_by == "timeline":
        display_events = _filter_timeline_display_events(movement_events)
        if search_q:
            display_events = _filter_movement_events_by_search(
                display_events, search_q
            )
        (
            units_in,
            units_out,
            units_request,
            units_sale,
            units_transfer_in,
            units_transfer_out,
            units_return,
        ) = _summarize_movement_events(display_events)
        timeline_item_ids = {
            event.get("item_id") for event in display_events if event.get("item_id")
        }
        if is_item_movement_detail and selected_item_ids:
            timeline_item_ids = set(selected_item_ids)
        if source == "shelf":
            timeline_current_stock = sum(
                (stock_by_item.get(pk, 0) for pk in timeline_item_ids),
                Decimal("0"),
            ) if timeline_item_ids else 0
        else:
            timeline_current_stock = sum(
                (stock_by_item.get(pk, 0) for pk in timeline_item_ids),
                Decimal("0"),
            ) if timeline_item_ids else 0
        try:
            as_float = float(timeline_current_stock)
            timeline_current_stock = (
                int(as_float) if as_float.is_integer() else round(as_float, 3)
            )
        except (TypeError, ValueError):
            pass
        movement_event_groups = _group_timeline_events_by_receipt(display_events)

        is_download = (request.GET.get("download") or "").strip() == "1"
        if movement_event_groups and not is_download:
            try:
                timeline_page = max(1, int(request.GET.get("page") or 1))
            except (TypeError, ValueError):
                timeline_page = 1
            timeline_total_groups = len(movement_event_groups)
            timeline_total_pages = max(
                1,
                (timeline_total_groups + timeline_page_size - 1)
                // timeline_page_size,
            )
            if timeline_page > timeline_total_pages:
                timeline_page = timeline_total_pages
            start = (timeline_page - 1) * timeline_page_size
            movement_event_groups = movement_event_groups[
                start : start + timeline_page_size
            ]
            timeline_prev_url, timeline_next_url = _report_page_urls(
                request, timeline_page, timeline_total_pages
            )
        movement_events = display_events
    else:
        summary_events = _filter_item_summary_movement_events(movement_events)
        if search_q:
            summary_events = _filter_movement_events_by_search(
                summary_events, search_q
            )
        (
            units_in,
            units_out,
            units_request,
            units_sale,
            units_transfer_in,
            units_transfer_out,
            units_return,
        ) = _summarize_movement_events(summary_events)
        extra_items = None
        if show_all_items and group_by_shop:
            extra_items = scope_items or _source_items(source, scope_shop_ids)
        movement_item_rows = _group_movement_events_by_item(
            summary_events,
            scope_shop_ids,
            shops_by_id=shops_by_id,
            group_by_shop=group_by_shop,
            extra_items=extra_items,
            require_events=not show_all_items,
            stock_by_item=stock_by_item,
            stock_by_item_shop=stock_by_item_shop or None,
            suspended_ids=suspended_ids,
        )
        if search_q:
            movement_item_rows = _filter_movement_item_rows_by_search(
                movement_item_rows, search_q
            )
        for row in movement_item_rows:
            row["hide_item_id"] = row.get("item_id") if source == "store" else None
            row["item_badge"] = cfg["badge"] if row.get("is_suspended") else ""
            if row.get("is_item_total"):
                continue
            movement_item_totals["current_stock"] += row["current_stock"]
            movement_item_totals["event_count"] += row["event_count"]
            movement_item_totals["units_in"] += row["units_in"]
            movement_item_totals["units_out"] += row["units_out"]
            movement_item_totals["units_transfer_in"] += row["units_transfer_in"]
            movement_item_totals["units_transfer_out"] += row[
                "units_transfer_out"
            ]
            movement_item_totals["units_sale"] += row["units_sale"]
            movement_item_totals["units_return"] += row["units_return"]
            movement_item_totals["units_trade"] += row.get("units_trade", 0)
        movement_events = summary_events

    report_params = _movements_report_params(
        range_type=range_type,
        filter_context=filter_context,
        item_mode=item_mode,
        event_filter=event_filters,
        view_by=view_by,
        selected_shop_ids=selected_shop_ids,
        selected_categories=selected_categories,
        selected_item_ids=selected_item_ids,
        search_q=search_q,
        type_order=type_order,
    )

    movements_back_url = ""
    if is_item_movement_detail:
        back_params = _movements_report_params(
            range_type=range_type,
            filter_context=filter_context,
            item_mode="all",
            event_filter=event_filters,
            view_by="item",
            selected_shop_ids=selected_shop_ids,
            selected_categories=selected_categories,
            selected_item_ids=[],
            search_q=search_q,
            type_order=type_order,
        )
        movements_back_url = _page_url(
            [(key, value) for key, value in back_params.items()]
        )
    elif is_item_movement_summary:
        for row in movement_item_rows:
            if not row.get("item_id") or row.get("is_item_total"):
                row["detail_url"] = ""
                continue
            shop_filter = selected_shop_ids
            if row.get("shop_id"):
                shop_filter = [row["shop_id"]]
            detail_params = _movements_report_params(
                range_type=range_type,
                filter_context=filter_context,
                item_mode="items",
                event_filter=event_filters,
                view_by="timeline",
                selected_shop_ids=shop_filter,
                selected_categories=selected_categories,
                selected_item_ids=[row["item_id"]],
                item_id=row["item_id"],
                search_q="",
                type_order=type_order,
            )
            row["detail_url"] = _page_url(
                [(key, value) for key, value in detail_params.items()]
            )

    if cfg["has_shops"]:
        if selected_shop_ids:
            shop_label = ", ".join(
                shops_by_id[sid].name
                for sid in selected_shop_ids
                if sid in shops_by_id
            )
        elif len(filter_shops) == 1:
            shop_label = filter_shops[0].name
        else:
            shop_label = "All shops"
    else:
        shop_label = CENTRAL_STORE_LABEL
    movement_subtitle = (
        f"{shop_label} · {_RANGE_LABELS[range_type]} · "
        f"{filter_context.get('report_period_label') or ''}"
    )

    if (request.GET.get("download") or "").strip() == "1":
        source_meta = {
            "type_order": type_order,
            "filename_prefix": cfg["filename_prefix"],
            "buying_price_resolver": (
                _shelf_buying_prices if source == "shelf" else _store_buying_prices
            ),
            "system_qty_resolver": (
                _shelf_system_qty if source == "shelf" else _store_system_qty
            ),
        }
        return (
            _stock_report_download(
                page_mode="movements",
                filter_context=filter_context,
                is_item_movement_summary=is_item_movement_summary,
                movement_events=movement_events,
                movement_item_rows=movement_item_rows,
                movement_item_group_by_shop=group_by_shop,
                item_report_rows=[],
                item_report_group_by_shop=False,
                event_filter=event_filters,
                view_by=view_by,
                report_kind=_parse_report_kind(request.GET.get("report_kind")),
                day_end=day_end,
                shop_label=shop_label,
                shop_ids=scope_shop_ids,
                source_meta=source_meta,
                **_company_pdf_meta(),
            ),
            {},
        )

    detail_item = (
        items_by_id.get(selected_item_ids[0]) if is_item_movement_detail else None
    )

    item_picker_url = _page_url([("item_picker", "1")])

    context = {
        "movement_title": page_title,
        "movement_subtitle": movement_subtitle,
        "shop_label": shop_label,
        "stock_mode": "movements",
        "is_movements_page": True,
        "view_by": view_by,
        "movement_hidden_params": base_params,
        "filter_shops": filter_shops,
        "selected_shop_ids": set(selected_shop_ids),
        "report_range_label": _RANGE_LABELS[range_type],
        "item_mode": item_mode,
        "categories": categories,
        "selected_categories": set(selected_categories),
        "selected_filter_items": selected_filter_items,
        "filter_items_json": filter_items_json,
        "item_picker_url": item_picker_url,
        "search_q": search_q,
        "event_filter": event_filter,
        "event_filters": event_filters,
        "event_filter_options": event_filter_options,
        "selected_event_types": selected_event_types,
        "event_filter_label": event_filter_label,
        "report_kind": _parse_report_kind(request.GET.get("report_kind")),
        "movement_pdf_audit": True,
        "is_item_movement_summary": is_item_movement_summary,
        "is_item_movement_detail": is_item_movement_detail,
        "detail_item": detail_item,
        "detail_item_hide_id": (
            detail_item.pk if (detail_item is not None and source == "store") else None
        ),
        "movements_back_url": movements_back_url,
        "movement_events": movement_events,
        "movement_event_groups": movement_event_groups,
        "movement_item_rows": movement_item_rows,
        "movement_item_totals": movement_item_totals,
        "movement_item_group_by_shop": group_by_shop,
        "movement_count": len(movement_events),
        "units_in": units_in,
        "units_out": units_out,
        "units_request": units_request,
        "units_transfer_in": units_transfer_in,
        "units_transfer_out": units_transfer_out,
        "units_sale": units_sale,
        "units_return": units_return,
        "timeline_page": timeline_page,
        "timeline_page_size": timeline_page_size,
        "timeline_total_groups": timeline_total_groups,
        "timeline_total_pages": timeline_total_pages,
        "timeline_prev_url": timeline_prev_url,
        "timeline_next_url": timeline_next_url,
        "timeline_current_stock": timeline_current_stock,
        "report_params": report_params,
        "range_type": range_type,
        # Catalog stock-visibility masking only applies to real Items.
        "mask_stock": source == "store",
        **filter_context,
    }
    return None, context
