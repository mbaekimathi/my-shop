"""Read-only POS client segmentation and message placeholder rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from items.models import Item
from shops.models import Client, ShopReceipt, ShopReceiptKind, ShopReceiptStatus
from shops.services import _normalize_phone

from .constants import (
    AUDIENCE_CREDIT,
    AUDIENCE_LEADS,
    AUDIENCE_QUOTATION,
    AUDIENCE_SALE,
    AUDIENCE_TYPE_CHOICES,
    AUDIENCE_TYPES,
    AUDIENCE_WHATSAPP,
    SPEND_TIER_BOUNDS,
    SPEND_TIER_CHOICES,
    TRANSACTION_MIN_CHOICES,
)


@dataclass
class Recipient:
    client_id: int | None
    full_name: str
    phone: str
    phone_normalized: str
    last_purchase_at: Any
    lifetime_spend: Decimal
    last_product: str
    categories: list[str]
    audience_meta: str = ""
    group_keys: list[str] = field(default_factory=list)
    chat_id: str = ""
    destination_type: str = "contact"  # contact | group

    @property
    def first_name(self) -> str:
        parts = (self.full_name or "").strip().split()
        return parts[0] if parts else ""

    @property
    def last_name(self) -> str:
        parts = (self.full_name or "").strip().split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""


def list_product_categories() -> list[str]:
    return list(
        Item.objects.exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )


def _parse_item_ids(raw) -> list[int]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        out = []
        for part in parts:
            try:
                out.append(int(part))
            except ValueError:
                continue
        return out
    if isinstance(raw, (list, tuple, set)):
        out = []
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    try:
        return [int(raw)]
    except (TypeError, ValueError):
        return []


def _item_counts_for_audience(
    *,
    kinds: list[str],
    shop_id: int | None,
    shop_ids: list[int] | None = None,
    outstanding_only: bool = False,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Popular items bought by this audience, with distinct client counts."""
    rows = (
        _base_receipt_qs(
            kinds=kinds,
            shop_id=shop_id,
            shop_ids=shop_ids,
            outstanding_only=outstanding_only,
        )
        .filter(lines__item_id__isnull=False)
        .values("lines__item_id", "lines__item_name")
        .annotate(clients=Count("client_id", distinct=True))
        .order_by("-clients", "lines__item_name")[: max(1, limit)]
    )
    out = []
    seen = set()
    for row in rows:
        item_id = row.get("lines__item_id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        label = (row.get("lines__item_name") or f"Item #{item_id}").strip()
        out.append(
            {
                "value": str(item_id),
                "label": label,
                "count": int(row.get("clients") or 0),
            }
        )
    return out


def _client_ids_for_items(
    item_ids: list[int],
    *,
    kinds: list[str],
    shop_id: int | None,
    shop_ids: list[int] | None = None,
    outstanding_only: bool = False,
) -> set[int]:
    if not item_ids:
        return set()
    return set(
        _base_receipt_qs(
            kinds=kinds,
            shop_id=shop_id,
            shop_ids=shop_ids,
            outstanding_only=outstanding_only,
        )
        .filter(lines__item_id__in=item_ids)
        .values_list("client_id", flat=True)
        .distinct()
    )


def companion_item_ids(
    item_ids: list[int],
    *,
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
    limit: int = 8,
) -> list[int]:
    """Items commonly bought on the same receipt as the given items."""
    from django.db.models import Subquery
    from shops.models import ShopReceiptKind, ShopReceiptLine, ShopReceiptStatus

    ids = [int(pk) for pk in item_ids if pk]
    if not ids:
        return []
    valid = Q(
        receipt__kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
        receipt__status__in=(
            ShopReceiptStatus.ACTIVE,
            ShopReceiptStatus.PARTIAL_RETURN,
        ),
        item_id__in=ids,
    )
    if shop_id:
        valid &= Q(receipt__shop_id=shop_id)
    elif shop_ids is not None:
        valid &= Q(receipt__shop_id__in=list(shop_ids))
    receipt_ids = ShopReceiptLine.objects.filter(valid).values("receipt_id")
    rows = (
        ShopReceiptLine.objects.filter(receipt_id__in=Subquery(receipt_ids))
        .exclude(item_id__isnull=True)
        .exclude(item_id__in=ids)
        .values("item_id")
        .annotate(together=Count("receipt_id", distinct=True))
        .order_by("-together")[: max(1, limit)]
    )
    found = [int(row["item_id"]) for row in rows if row.get("item_id")]
    if found:
        return found
    categories = list(
        Item.objects.filter(pk__in=ids)
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
    )
    if not categories:
        return []
    fallback = (
        Item.objects.filter(is_suspended=False, category__in=categories)
        .exclude(pk__in=ids)
        .order_by("name", "id")
        .values_list("pk", flat=True)[: max(1, limit)]
    )
    return [int(pk) for pk in fallback]


def bought_item_ids_for_filters(filters: dict | None = None, *, limit: int = 80) -> list[int]:
    """Item ids this audience has bought, filter item first when set."""
    f = parse_filters(filters)
    kinds = _receipt_kinds_for_audience(f["audience_type"])
    outstanding = bool(f.get("outstanding_only")) and f["audience_type"] == AUDIENCE_CREDIT
    if f["audience_type"] == AUDIENCE_LEADS:
        from shops.models import ShopReceiptKind

        kinds = [
            ShopReceiptKind.SALE,
            ShopReceiptKind.CREDIT,
            ShopReceiptKind.QUOTATION,
        ]
        outstanding = False
    popular = _item_counts_for_audience(
        kinds=kinds,
        shop_id=f["shop_id"],
        shop_ids=f.get("shop_ids"),
        outstanding_only=outstanding,
        limit=limit,
    )
    ordered = []
    seen = set()
    for pk in list(f.get("item_ids") or []):
        if pk and pk not in seen:
            seen.add(pk)
            ordered.append(pk)
    for row in popular:
        try:
            pk = int(row.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if pk and pk not in seen:
            seen.add(pk)
            ordered.append(pk)
    return ordered


def _parse_client_ids(raw) -> list[int]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        out = []
        for part in parts:
            try:
                out.append(int(part))
            except ValueError:
                continue
        return out
    if isinstance(raw, (list, tuple, set)):
        out = []
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    try:
        return [int(raw)]
    except (TypeError, ValueError):
        return []


def parse_filters(raw: dict | None) -> dict[str, Any]:
    raw = raw or {}
    audience_type = (raw.get("audience_type") or AUDIENCE_SALE).strip().lower()
    if audience_type not in AUDIENCE_TYPES or audience_type == AUDIENCE_WHATSAPP:
        audience_type = AUDIENCE_SALE

    category = (raw.get("category") or "").strip()
    # Support multi-select product category groups
    categories = raw.get("categories") or raw.get("groups") or []
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(",") if c.strip()]
    elif not isinstance(categories, (list, tuple)):
        categories = []
    categories = [str(c).strip() for c in categories if str(c).strip()]
    if category and category not in categories:
        categories = [category, *categories]

    spend_tier = (raw.get("spend_tier") or "").strip().lower()
    item_ids = _parse_item_ids(raw.get("item_ids") or raw.get("items") or [])
    min_tx_raw = str(raw.get("min_transactions") or "").strip()
    min_transactions = None
    if min_tx_raw.isdigit():
        min_transactions = int(min_tx_raw)
        if min_transactions < 1:
            min_transactions = None
    last_days_raw = str(raw.get("last_purchase_days") or "").strip()
    last_purchase_days = None
    if last_days_raw.isdigit():
        last_purchase_days = int(last_days_raw)

    shop_id = raw.get("shop_id")
    try:
        shop_id = int(shop_id) if shop_id not in (None, "", "0") else None
    except (TypeError, ValueError):
        shop_id = None

    shop_ids = None
    if "shop_ids" in raw:
        shop_ids = []
        for value in raw.get("shop_ids") or []:
            try:
                shop_ids.append(int(value))
            except (TypeError, ValueError):
                continue

    search = (raw.get("search") or raw.get("q") or "").strip()
    client_ids = _parse_client_ids(raw.get("client_ids") or raw.get("selected_ids"))
    destinations = raw.get("destinations") or raw.get("wa_destinations") or []
    if isinstance(destinations, str):
        destinations = [d.strip() for d in destinations.split(",") if d.strip()]
    elif not isinstance(destinations, (list, tuple)):
        destinations = []
    destinations = [str(d).strip() for d in destinations if str(d).strip()]

    outstanding_only = raw.get("outstanding_only")
    if isinstance(outstanding_only, str):
        outstanding_only = outstanding_only.strip().lower() in {"1", "true", "yes", "on"}
    else:
        outstanding_only = bool(outstanding_only)
    # Credits default to people with an open balance.
    if audience_type == AUDIENCE_CREDIT and raw.get("outstanding_only") is None:
        outstanding_only = True

    return {
        "audience_type": audience_type,
        "category": categories[0] if len(categories) == 1 else "",
        "categories": categories,
        "spend_tier": spend_tier if spend_tier in dict(SPEND_TIER_CHOICES) else "",
        "item_ids": item_ids,
        "min_transactions": min_transactions,
        "last_purchase_days": last_purchase_days,
        "shop_id": shop_id,
        "shop_ids": shop_ids,
        "shop_scoped": bool(raw.get("shop_scoped")),
        "search": search,
        "client_ids": client_ids,
        "destinations": destinations,
        "outstanding_only": outstanding_only,
    }


def _receipt_kinds_for_audience(audience_type: str) -> list[str]:
    if audience_type == AUDIENCE_SALE:
        return [ShopReceiptKind.SALE]
    if audience_type == AUDIENCE_CREDIT:
        return [ShopReceiptKind.CREDIT]
    if audience_type == AUDIENCE_QUOTATION:
        return [ShopReceiptKind.QUOTATION]
    return [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT, ShopReceiptKind.QUOTATION]


def _base_receipt_qs(
    *,
    kinds: list[str],
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
    outstanding_only: bool = False,
):
    qs = (
        ShopReceipt.objects.filter(kind__in=kinds, client_id__isnull=False)
        .exclude(status=ShopReceiptStatus.CANCELLED)
    )
    if shop_id:
        qs = qs.filter(shop_id=shop_id)
    elif shop_ids is not None:
        qs = qs.filter(shop_id__in=list(shop_ids))
    if outstanding_only:
        qs = qs.filter(total__gt=F("amount_paid"))
    return qs


def constrain_filters_to_profile(filters: dict | None, profile) -> dict[str, Any]:
    """Restrict WhatsApp audience shop_id / shop_ids to allocated shops."""
    from employees.models import SHOP_ASSIGNABLE_ROLES
    from items.services import actionable_shops_for_profile

    parsed = parse_filters(filters)
    allowed = [shop.pk for shop in actionable_shops_for_profile(profile)]
    shop_id = parsed.get("shop_id")
    if shop_id is not None and shop_id not in allowed:
        parsed["shop_id"] = None
        parsed["shop_ids"] = []
    else:
        parsed["shop_ids"] = allowed
    parsed["shop_scoped"] = getattr(profile, "role", None) in SHOP_ASSIGNABLE_ROLES
    return parsed


def _client_ids_for_categories(
    categories: list[str],
    *,
    kinds: list[str],
    shop_id: int | None,
    shop_ids: list[int] | None = None,
    outstanding_only: bool = False,
) -> set[int]:
    if not categories:
        return set()
    qs = (
        _base_receipt_qs(
            kinds=kinds,
            shop_id=shop_id,
            shop_ids=shop_ids,
            outstanding_only=outstanding_only,
        )
        .filter(lines__item__category__in=categories)
        .values_list("client_id", flat=True)
        .distinct()
    )
    return {int(pk) for pk in qs if pk}


def _last_product_for_clients(
    client_ids: list[int],
    *,
    kinds: list[str],
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
) -> dict[int, str]:
    if not client_ids:
        return {}
    receipts = (
        _base_receipt_qs(
            kinds=kinds,
            shop_id=shop_id,
            shop_ids=shop_ids,
        )
        .filter(client_id__in=client_ids)
        .order_by("client_id", "-created_at")
        .prefetch_related("lines")
    )
    seen: set[int] = set()
    out: dict[int, str] = {}
    for receipt in receipts:
        cid = receipt.client_id
        if cid in seen:
            continue
        seen.add(cid)
        line = next(iter(receipt.lines.all()), None)
        out[cid] = (line.item_name if line else "") or ""
    return out


def _categories_for_clients(
    client_ids: list[int],
    *,
    kinds: list[str],
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
) -> dict[int, list[str]]:
    if not client_ids:
        return {}
    rows = (
        _base_receipt_qs(
            kinds=kinds,
            shop_id=shop_id,
            shop_ids=shop_ids,
        )
        .filter(client_id__in=client_ids, lines__item__isnull=False)
        .values_list("client_id", "lines__item__category")
        .distinct()
    )
    out: dict[int, set[str]] = {}
    for cid, cat in rows:
        if not cat:
            continue
        out.setdefault(int(cid), set()).add(cat)
    return {cid: sorted(cats) for cid, cats in out.items()}


def _group_counts_for_audience(
    *,
    kinds: list[str],
    shop_id: int | None,
    shop_ids: list[int] | None = None,
    outstanding_only: bool = False,
) -> list[dict[str, Any]]:
    rows = (
        _base_receipt_qs(
            kinds=kinds,
            shop_id=shop_id,
            shop_ids=shop_ids,
            outstanding_only=outstanding_only,
        )
        .filter(lines__item__isnull=False)
        .exclude(lines__item__category="")
        .values("lines__item__category")
        .annotate(clients=Count("client_id", distinct=True))
        .order_by("lines__item__category")
    )
    return [
        {
            "value": row["lines__item__category"],
            "label": row["lines__item__category"],
            "count": int(row["clients"] or 0),
        }
        for row in rows
        if row["lines__item__category"]
    ]


def audience_summary(
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
    shop_scoped: bool = False,
) -> list[dict[str, Any]]:
    """Counts for the Sales / Credits / Quotations / Leads / WhatsApp tabs."""
    summary = []
    for value, label in AUDIENCE_TYPE_CHOICES:
        if value == AUDIENCE_WHATSAPP:
            summary.append(
                {
                    "value": value,
                    "label": label,
                    "count": None,
                    "hint": "Groups & contacts on the linked WhatsApp",
                }
            )
            continue
        if value == AUDIENCE_LEADS:
            leads_qs = Client.objects.all()
            if shop_scoped:
                lead_ids = _base_receipt_qs(
                    kinds=[
                        ShopReceiptKind.SALE,
                        ShopReceiptKind.CREDIT,
                        ShopReceiptKind.QUOTATION,
                    ],
                    shop_id=shop_id,
                    shop_ids=shop_ids,
                ).values_list("client_id", flat=True)
                leads_qs = leads_qs.filter(pk__in=lead_ids)
            with_phone = 0
            for phone in leads_qs.values_list(
                "phone_normalized", "phone_number"
            ):
                if _normalize_phone(phone[0] or phone[1] or ""):
                    with_phone += 1
            summary.append(
                {
                    "value": value,
                    "label": label,
                    "count": with_phone,
                    "hint": "All client contacts",
                }
            )
            continue
        kinds = _receipt_kinds_for_audience(value)
        outstanding = value == AUDIENCE_CREDIT
        count = (
            _base_receipt_qs(
                kinds=kinds,
                shop_id=shop_id,
                shop_ids=shop_ids,
                outstanding_only=outstanding,
            )
            .values("client_id")
            .distinct()
            .count()
        )
        hint = {
            AUDIENCE_SALE: "Clients with sales",
            AUDIENCE_CREDIT: "Clients with open credit",
            AUDIENCE_QUOTATION: "Clients with quotations",
        }.get(value, "")
        summary.append(
            {"value": value, "label": label, "count": count, "hint": hint}
        )
    return summary


def _query_receipt_audience(f: dict[str, Any]) -> list[Recipient]:
    kinds = _receipt_kinds_for_audience(f["audience_type"])
    outstanding = bool(f.get("outstanding_only")) and f["audience_type"] == AUDIENCE_CREDIT
    receipts = _base_receipt_qs(
        kinds=kinds,
        shop_id=f["shop_id"],
        shop_ids=f.get("shop_ids"),
        outstanding_only=outstanding,
    )

    if f["categories"]:
        client_ids = _client_ids_for_categories(
            f["categories"],
            kinds=kinds,
            shop_id=f["shop_id"],
            shop_ids=f.get("shop_ids"),
            outstanding_only=outstanding,
        )
        if not client_ids:
            return []
        receipts = receipts.filter(client_id__in=client_ids)

    if f.get("item_ids"):
        item_client_ids = _client_ids_for_items(
            f["item_ids"],
            kinds=kinds,
            shop_id=f["shop_id"],
            shop_ids=f.get("shop_ids"),
            outstanding_only=outstanding,
        )
        if not item_client_ids:
            return []
        receipts = receipts.filter(client_id__in=item_client_ids)

    if f["last_purchase_days"]:
        since = timezone.now() - timedelta(days=f["last_purchase_days"])
        recent_ids = (
            receipts.filter(created_at__gte=since)
            .values_list("client_id", flat=True)
            .distinct()
        )
        receipts = receipts.filter(client_id__in=recent_ids)

    aggregates = (
        receipts.values("client_id")
        .annotate(
            last_purchase_at=Max("created_at"),
            lifetime_spend=Coalesce(Sum("total"), Decimal("0")),
            txn_count=Count("id"),
        )
        .order_by("client_id")
    )

    if f.get("min_transactions"):
        aggregates = aggregates.filter(txn_count__gte=int(f["min_transactions"]))

    if f["spend_tier"]:
        low, high = SPEND_TIER_BOUNDS[f["spend_tier"]]
        if high is None:
            aggregates = aggregates.filter(lifetime_spend__gte=low)
        else:
            aggregates = aggregates.filter(
                lifetime_spend__gte=low, lifetime_spend__lte=high
            )

    rows = list(aggregates)
    client_ids = [int(r["client_id"]) for r in rows]
    return _build_recipients(
        client_ids,
        row_meta={
            int(r["client_id"]): {
                "last_purchase_at": r["last_purchase_at"],
                "lifetime_spend": Decimal(r["lifetime_spend"] or 0),
            }
            for r in rows
        },
        kinds=kinds,
        search=f.get("search") or "",
        selected_ids=set(f.get("client_ids") or []),
        apply_selected=False,
        audience_meta=f["audience_type"],
        shop_id=f.get("shop_id"),
        shop_ids=f.get("shop_ids"),
    )


def _query_leads_audience(f: dict[str, Any]) -> list[Recipient]:
    """All client contacts; optional product-category groups narrow the list."""
    kinds = [ShopReceiptKind.SALE, ShopReceiptKind.CREDIT, ShopReceiptKind.QUOTATION]
    clients_qs = Client.objects.all().only(
        "id", "full_name", "phone_number", "phone_normalized"
    )
    if f.get("shop_scoped"):
        lead_ids = _base_receipt_qs(
            kinds=kinds,
            shop_id=f.get("shop_id"),
            shop_ids=f.get("shop_ids"),
        ).values_list("client_id", flat=True)
        clients_qs = clients_qs.filter(pk__in=lead_ids)

    if f["categories"]:
        allowed = _client_ids_for_categories(
            f["categories"],
            kinds=kinds,
            shop_id=f["shop_id"],
            shop_ids=f.get("shop_ids"),
            outstanding_only=False,
        )
        if not allowed:
            return []
        clients_qs = clients_qs.filter(pk__in=allowed)

    if f.get("item_ids"):
        allowed_items = _client_ids_for_items(
            f["item_ids"],
            kinds=kinds,
            shop_id=f["shop_id"],
            shop_ids=f.get("shop_ids"),
            outstanding_only=False,
        )
        if not allowed_items:
            return []
        clients_qs = clients_qs.filter(pk__in=allowed_items)

    if f.get("min_transactions"):
        min_tx = int(f["min_transactions"])
        txn_qs = ShopReceipt.objects.filter(
            client_id__isnull=False,
            kind__in=kinds,
        ).exclude(status=ShopReceiptStatus.CANCELLED)
        if f["shop_id"]:
            txn_qs = txn_qs.filter(shop_id=f["shop_id"])
        elif f.get("shop_ids") is not None:
            txn_qs = txn_qs.filter(shop_id__in=list(f["shop_ids"]))
        txn_client_ids = [
            int(row["client_id"])
            for row in (
                txn_qs.values("client_id")
                .annotate(txn_count=Count("id"))
                .filter(txn_count__gte=min_tx)
            )
        ]
        if not txn_client_ids:
            return []
        clients_qs = clients_qs.filter(pk__in=txn_client_ids)

    if f.get("search"):
        q = f["search"]
        clients_qs = clients_qs.filter(
            Q(full_name__icontains=q)
            | Q(phone_number__icontains=q)
            | Q(phone_normalized__icontains=q)
        )

    clients = list(clients_qs.order_by("full_name", "id"))
    client_ids = [c.pk for c in clients]

    spend_qs = ShopReceipt.objects.filter(
        client_id__in=client_ids,
        kind__in=[ShopReceiptKind.SALE, ShopReceiptKind.CREDIT],
    ).exclude(status=ShopReceiptStatus.CANCELLED)
    if f.get("shop_id"):
        spend_qs = spend_qs.filter(shop_id=f["shop_id"])
    elif f.get("shop_ids") is not None:
        spend_qs = spend_qs.filter(shop_id__in=list(f["shop_ids"]))
    spend_rows = (
        spend_qs.values("client_id")
        .annotate(
            last_purchase_at=Max("created_at"),
            lifetime_spend=Coalesce(Sum("total"), Decimal("0")),
        )
    )
    row_meta = {
        int(r["client_id"]): {
            "last_purchase_at": r["last_purchase_at"],
            "lifetime_spend": Decimal(r["lifetime_spend"] or 0),
        }
        for r in spend_rows
    }

    return _build_recipients_from_clients(
        clients,
        row_meta=row_meta,
        kinds=kinds,
        audience_meta=AUDIENCE_LEADS,
        shop_id=f.get("shop_id"),
        shop_ids=f.get("shop_ids"),
    )


def _build_recipients(
    client_ids: list[int],
    *,
    row_meta: dict[int, dict],
    kinds: list[str],
    search: str,
    selected_ids: set[int],
    apply_selected: bool,
    audience_meta: str,
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
) -> list[Recipient]:
    clients_qs = Client.objects.filter(pk__in=client_ids).only(
        "id", "full_name", "phone_number", "phone_normalized"
    )
    if search:
        clients_qs = clients_qs.filter(
            Q(full_name__icontains=search)
            | Q(phone_number__icontains=search)
            | Q(phone_normalized__icontains=search)
        )
    clients = list(clients_qs.order_by("full_name", "id"))
    if apply_selected and selected_ids:
        clients = [c for c in clients if c.pk in selected_ids]
    return _build_recipients_from_clients(
        clients,
        row_meta=row_meta,
        kinds=kinds,
        audience_meta=audience_meta,
        shop_id=shop_id,
        shop_ids=shop_ids,
    )


def _build_recipients_from_clients(
    clients: list[Client],
    *,
    row_meta: dict[int, dict],
    kinds: list[str],
    audience_meta: str,
    shop_id: int | None = None,
    shop_ids: list[int] | None = None,
) -> list[Recipient]:
    client_ids = [c.pk for c in clients]
    last_products = _last_product_for_clients(
        client_ids, kinds=kinds, shop_id=shop_id, shop_ids=shop_ids
    )
    categories_map = _categories_for_clients(
        client_ids, kinds=kinds, shop_id=shop_id, shop_ids=shop_ids
    )
    recipients: list[Recipient] = []
    for client in clients:
        phone = _normalize_phone(client.phone_normalized or client.phone_number)
        if not phone:
            continue
        meta = row_meta.get(client.pk, {})
        cats = categories_map.get(client.pk, [])
        recipients.append(
            Recipient(
                client_id=client.pk,
                full_name=client.full_name or "",
                phone=phone,
                phone_normalized=phone,
                last_purchase_at=meta.get("last_purchase_at"),
                lifetime_spend=Decimal(meta.get("lifetime_spend") or 0),
                last_product=last_products.get(client.pk, ""),
                categories=cats,
                audience_meta=audience_meta,
                group_keys=cats,
                chat_id=f"{phone}@c.us",
                destination_type="contact",
            )
        )
    return recipients


def _query_whatsapp_audience(f: dict[str, Any]) -> list[Recipient]:
    """Personal WhatsApp contacts/groups were removed with the VPS bridge."""
    return []


def query_recipients(
    filters: dict | None = None, *, limit: int | None = None
) -> list[Recipient]:
    """
    Read-only segment of clients / WhatsApp destinations.

    Filters:
      audience_type — sale | credit | quotation | leads | whatsapp
      categories / groups — product category group(s), or contacts/groups for WhatsApp
      client_ids — POS client selection
      destinations — WhatsApp chat ids / phones when audience is whatsapp
      last_purchase_days, spend_tier, shop_id, search, outstanding_only
    """
    f = parse_filters(filters)
    if f["audience_type"] == AUDIENCE_WHATSAPP:
        recipients = _query_whatsapp_audience(f)
    elif f["audience_type"] == AUDIENCE_LEADS:
        recipients = _query_leads_audience(f)
        selected = set(f.get("client_ids") or [])
        if selected:
            recipients = [r for r in recipients if r.client_id in selected]
    else:
        recipients = _query_receipt_audience(f)
        selected = set(f.get("client_ids") or [])
        if selected:
            recipients = [r for r in recipients if r.client_id in selected]

    if limit is not None:
        recipients = recipients[: max(0, limit)]
    return recipients


def recipient_count(filters: dict | None = None) -> int:
    return len(query_recipients(filters))


def render_placeholders(template: str, recipient: Recipient) -> str:
    text = template or ""
    last_date = ""
    if recipient.last_purchase_at:
        last_date = timezone.localtime(recipient.last_purchase_at).strftime("%d %b %Y")
    spend = f"{recipient.lifetime_spend:,.2f}"
    replacements = {
        "{first_name}": recipient.first_name,
        "{last_name}": recipient.last_name,
        "{full_name}": recipient.full_name,
        "{last_product}": recipient.last_product or "",
        "{last_purchase_date}": last_date,
        "{lifetime_spend}": spend,
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


def preview_message(
    template: str,
    filters: dict | None = None,
    *,
    client_id: int | None = None,
    destination_key: str | None = None,
) -> dict[str, Any]:
    f = parse_filters(filters)
    preview_filters = {**f, "client_ids": [], "destinations": []}
    recipients = query_recipients(preview_filters)
    if not recipients:
        return {
            "ok": True,
            "count": 0,
            "preview": "",
            "recipient": None,
        }
    chosen = None
    if destination_key:
        chosen = next(
            (
                r
                for r in recipients
                if (r.chat_id or r.phone) == destination_key
            ),
            None,
        )
    if chosen is None and client_id:
        chosen = next((r for r in recipients if r.client_id == client_id), None)
    selected = set(f.get("client_ids") or [])
    if chosen is None and selected:
        chosen = next((r for r in recipients if r.client_id in selected), None)
    dest_selected = set(f.get("destinations") or [])
    if chosen is None and dest_selected:
        chosen = next(
            (
                r
                for r in recipients
                if (r.chat_id or r.phone) in dest_selected
            ),
            None,
        )
    if chosen is None:
        chosen = recipients[0]
    return {
        "ok": True,
        "count": len(recipients),
        "preview": render_placeholders(template, chosen),
        "recipient": {
            "client_id": chosen.client_id,
            "full_name": chosen.full_name,
            "phone": chosen.phone,
            "chat_id": chosen.chat_id,
            "destination_type": chosen.destination_type,
            "last_product": chosen.last_product,
            "lifetime_spend": str(chosen.lifetime_spend),
            "last_purchase_at": (
                chosen.last_purchase_at.isoformat() if chosen.last_purchase_at else None
            ),
        },
        "unknown_placeholders": sorted(set(_PLACEHOLDER_RE.findall(template or ""))),
    }


def recipients_payload(
    filters: dict | None = None, *, sample: int = 500
) -> dict[str, Any]:
    f = parse_filters(filters)
    list_filters = {**f, "client_ids": [], "destinations": []}
    all_rows = query_recipients(list_filters)
    selected_clients = set(f.get("client_ids") or [])
    selected_destinations = set(f.get("destinations") or [])
    sample_rows = all_rows[: max(0, sample)]

    if f["audience_type"] == AUDIENCE_WHATSAPP:
        both = query_recipients({**list_filters, "categories": ["contacts", "groups"]})
        contact_n = sum(1 for r in both if r.destination_type == "contact")
        group_n = sum(1 for r in both if r.destination_type == "group")
        groups = [
            {"value": "groups", "label": "Groups", "count": group_n},
            {"value": "contacts", "label": "Contacts", "count": contact_n},
        ]
        selected_count = (
            len(selected_destinations) if selected_destinations else len(all_rows)
        )
        bridge_error = ""
        items = []
        if not both:
            bridge_error = ""
    else:
        bridge_error = ""
        kinds = _receipt_kinds_for_audience(f["audience_type"])
        outstanding = (
            bool(f.get("outstanding_only")) and f["audience_type"] == AUDIENCE_CREDIT
        )
        item_kinds = (
            [
                ShopReceiptKind.SALE,
                ShopReceiptKind.CREDIT,
                ShopReceiptKind.QUOTATION,
            ]
            if f["audience_type"] == AUDIENCE_LEADS
            else kinds
        )
        if f["audience_type"] == AUDIENCE_LEADS:
            groups = _group_counts_for_audience(
                kinds=item_kinds,
                shop_id=f["shop_id"],
                shop_ids=f.get("shop_ids"),
                outstanding_only=False,
            )
            items = _item_counts_for_audience(
                kinds=item_kinds,
                shop_id=f["shop_id"],
                shop_ids=f.get("shop_ids"),
                outstanding_only=False,
            )
        else:
            groups = _group_counts_for_audience(
                kinds=kinds,
                shop_id=f["shop_id"],
                shop_ids=f.get("shop_ids"),
                outstanding_only=outstanding,
            )
            items = _item_counts_for_audience(
                kinds=kinds,
                shop_id=f["shop_id"],
                shop_ids=f.get("shop_ids"),
                outstanding_only=outstanding,
            )
        selected_count = len(selected_clients) if selected_clients else len(all_rows)

    return {
        "ok": True,
        "count": len(all_rows),
        "selected_count": selected_count,
        "filters": f,
        "bridge_error": bridge_error,
        "audience_types": [
            {"value": value, "label": label}
            for value, label in AUDIENCE_TYPE_CHOICES
        ],
        "audience_summary": audience_summary(
            shop_id=f["shop_id"], shop_ids=f.get("shop_ids")
        ),
        "categories": list_product_categories(),
        "groups": groups,
        "items": items,
        "spend_tiers": [
            {"value": value, "label": label} for value, label in SPEND_TIER_CHOICES
        ],
        "transaction_mins": [
            {"value": value, "label": label} for value, label in TRANSACTION_MIN_CHOICES
        ],
        "recipients": [
            {
                "client_id": r.client_id,
                "full_name": r.full_name,
                "phone": r.phone,
                "chat_id": r.chat_id or "",
                "destination_type": r.destination_type,
                "destination_key": r.chat_id or r.phone,
                "last_product": r.last_product,
                "lifetime_spend": str(r.lifetime_spend),
                "last_purchase_at": (
                    r.last_purchase_at.isoformat() if r.last_purchase_at else None
                ),
                "categories": r.categories,
                "groups": r.group_keys,
                "selected": (
                    (not selected_clients and not selected_destinations)
                    or (r.client_id is not None and r.client_id in selected_clients)
                    or ((r.chat_id or r.phone) in selected_destinations)
                ),
            }
            for r in sample_rows
        ],
    }


_WA_GROUP_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?chat\.whatsapp\.com/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
WHATSAPP_WEB_URL = "https://web.whatsapp.com/"


def normalize_whatsapp_group_invite(value: str, *, required: bool = False) -> str:
    from django.core.exceptions import ValidationError

    raw = (value or "").strip()
    if not raw:
        if required:
            raise ValidationError(
                "Paste a WhatsApp group invite link (chat.whatsapp.com/…)."
            )
        return ""
    match = _WA_GROUP_INVITE_RE.search(raw)
    if not match:
        raise ValidationError(
            "Paste a WhatsApp group invite link (chat.whatsapp.com/…)."
        )
    return f"https://chat.whatsapp.com/{match.group(1)}"


def list_whatsapp_contacts() -> list[dict[str, Any]]:
    from shops.services import _whatsapp_url, format_kenya_phone

    rows = []
    for client in Client.objects.all().order_by("full_name", "id"):
        phone = format_kenya_phone(client.phone_normalized or client.phone_number)
        rows.append(
            {
                "id": client.pk,
                "full_name": (client.full_name or "").strip() or "Contact",
                "phone": phone or client.phone_number,
                "wa_url": _whatsapp_url(
                    phone=client.phone_normalized or client.phone_number,
                    text="",
                ),
            }
        )
    return rows


def add_whatsapp_contact(*, full_name: str, phone: str, profile=None):
    from django.core.exceptions import ValidationError
    from shops.services import upsert_client

    name = (full_name or "").strip()
    if not name:
        raise ValidationError("Enter a contact name.")
    client = upsert_client(full_name=name, phone=phone, profile=profile)
    if client is None:
        raise ValidationError("Enter a valid Kenyan phone number.")
    return client


def list_whatsapp_groups() -> list[dict[str, Any]]:
    from .models import WhatsAppGroup

    groups = []
    qs = WhatsAppGroup.objects.prefetch_related("members").order_by("name", "id")
    for group in qs:
        members = [
            {
                "id": member.pk,
                "full_name": (member.full_name or "").strip() or "Contact",
                "phone": member.phone_number,
            }
            for member in group.members.all().order_by("full_name", "id")
        ]
        groups.append(
            {
                "id": group.pk,
                "name": group.name,
                "invite_link": group.invite_link,
                "source": group.source,
                "open_url": group.invite_link or WHATSAPP_WEB_URL,
                "member_count": len(members),
                "members": members,
            }
        )
    return groups


def create_whatsapp_group(
    *,
    name: str,
    invite_link: str = "",
    member_ids: list[int] | None = None,
    profile=None,
    source: str = "",
):
    from django.core.exceptions import ValidationError

    from .models import (
        WHATSAPP_GROUP_CREATED,
        WHATSAPP_GROUP_JOINED,
        WhatsAppGroup,
    )

    title = (name or "").strip()
    invite = normalize_whatsapp_group_invite(invite_link, required=False)
    kind = (source or "").strip() or (
        WHATSAPP_GROUP_JOINED if invite else WHATSAPP_GROUP_CREATED
    )
    if kind not in {WHATSAPP_GROUP_CREATED, WHATSAPP_GROUP_JOINED}:
        kind = WHATSAPP_GROUP_CREATED
    if not title:
        if invite:
            title = "WhatsApp group"
        else:
            raise ValidationError("Enter a group name.")
    ids = []
    seen = set()
    for raw in member_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    members = list(Client.objects.filter(pk__in=ids)) if ids else []
    group = WhatsAppGroup.objects.create(
        name=title,
        invite_link=invite,
        source=kind,
        created_by=profile,
    )
    if members:
        group.members.set(members)
    return group


def join_whatsapp_group(
    *,
    name: str,
    invite_link: str,
    member_ids: list[int] | None = None,
    profile=None,
):
    from .models import WHATSAPP_GROUP_JOINED, WhatsAppGroup

    invite = normalize_whatsapp_group_invite(invite_link, required=True)
    existing = WhatsAppGroup.objects.filter(invite_link=invite).first()
    if existing:
        title = (name or "").strip()
        updates = []
        if title and title != existing.name:
            existing.name = title
            updates.append("name")
        if existing.source != WHATSAPP_GROUP_JOINED:
            existing.source = WHATSAPP_GROUP_JOINED
            updates.append("source")
        if updates:
            updates.append("updated_at")
            existing.save(update_fields=updates)
        if member_ids:
            ids = []
            seen = set()
            for raw in member_ids:
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    continue
                if value > 0 and value not in seen:
                    seen.add(value)
                    ids.append(value)
            if ids:
                existing.members.add(*Client.objects.filter(pk__in=ids))
        return existing
    return create_whatsapp_group(
        name=name,
        invite_link=invite,
        member_ids=member_ids,
        profile=profile,
        source=WHATSAPP_GROUP_JOINED,
    )
