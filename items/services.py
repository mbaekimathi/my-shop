from decimal import Decimal, InvalidOperation
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery

from employees.countries import COUNTRY_DIAL_CODES

from .models import (
    Item,
    ItemSerial,
    ShopItemPrice,
    ShopStock,
    StockMovement,
    StockMovementLine,
    StockMovementType,
    StockRequestStatus,
    StockOutReason,
    StockPaymentStatus,
    Supplier,
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
PHONE_RE = re.compile(r"^[\d\s\-()]{7,20}$")
VALID_DIAL_CODES = {country["dial"] for country in COUNTRY_DIAL_CODES}
ISO_BY_DIAL = {country["dial"]: country["iso"] for country in COUNTRY_DIAL_CODES}


def _normalize_phone_digits(phone: str) -> str:
    return re.sub(r"\D+", "", phone or "")


def _normalize_national_phone(phone: str, dial: str = "") -> str:
    digits = _normalize_phone_digits(phone)
    cc = _normalize_phone_digits(dial)
    if cc and digits.startswith(cc) and len(digits) > len(cc):
        digits = digits[len(cc) :]
    digits = digits.lstrip("0")
    return digits[:9]


def item_text_search_q(query: str):
    """
    Build an Item text filter that prefers indexed name/category.

    Multi-word queries avoid scanning description (TEXT) on every token — that
    path dominated stock-catalog search latency.
    """
    from django.db.models import Q

    phrase = (query or "").strip()
    tokens = [t for t in phrase.lower().split() if t]
    if not tokens:
        return Q()
    if len(tokens) == 1:
        token = tokens[0]
        return (
            Q(name__icontains=token)
            | Q(category__icontains=token)
            | Q(description__icontains=token)
        )
    token_q = Q()
    for token in tokens:
        token_q &= Q(name__icontains=token) | Q(category__icontains=token)
    return Q(name__icontains=phrase) | token_q


def _paginate_queryset(qs, *, page: int, page_size: int):
    """Slice pagination with a single COUNT (avoids Django Paginator's second count)."""
    total = qs.count()
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    has_more = offset + page_size < total
    return {
        "total": total,
        "page": page,
        "items": items,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
    }


def _money_cost(value) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    if amount < 0:
        amount = Decimal("0")
    return amount.quantize(Decimal("0.01"))


def weighted_average_cost(
    *,
    old_qty: int,
    old_avg,
    in_qty: int,
    in_price,
) -> Decimal:
    """Blend existing average cost with an incoming quantity at a unit price."""
    old_q = max(0, int(old_qty or 0))
    new_q = max(0, int(in_qty or 0))
    old_avg_dec = _money_cost(old_avg)
    in_price_dec = _money_cost(in_price)
    if new_q <= 0:
        return old_avg_dec
    if old_q <= 0:
        return in_price_dec
    total_qty = old_q + new_q
    blended = (old_avg_dec * old_q) + (in_price_dec * new_q)
    return (blended / Decimal(total_qty)).quantize(Decimal("0.01"))


def apply_stock_in_average_cost(shop_stock: ShopStock, *, qty: int, unit_cost) -> Decimal:
    """
    Update shop stock weighted average for an inbound quantity.

    Expects ``shop_stock.quantity`` to be the on-hand qty *before* the inbound add.
    """
    qty = max(0, int(qty or 0))
    unit = _money_cost(unit_cost)
    old_qty = max(0, int(shop_stock.quantity or 0))
    new_avg = weighted_average_cost(
        old_qty=old_qty,
        old_avg=getattr(shop_stock, "average_cost", 0),
        in_qty=qty,
        in_price=unit,
    )
    shop_stock.average_cost = new_avg
    return new_avg


def resolve_sale_unit_cost(shop_stock: ShopStock | None, *, fallback=None) -> Decimal:
    """Unit cost to stamp on a sale/credit line."""
    if shop_stock is not None:
        avg = _money_cost(getattr(shop_stock, "average_cost", 0))
        if avg > 0:
            return avg
    return _money_cost(fallback)


def last_buying_prices_for_items(item_ids, *, prefer_shop_id=None) -> dict:
    """
    Latest stock-in buying price per item via subqueries (one Item query).
    Prefer prices from prefer_shop_id when provided, then fall back to any shop.
    """
    ids = [int(pk) for pk in item_ids if pk]
    if not ids:
        return {}

    def _latest_subquery(*, shop_id=None):
        qs = StockMovementLine.objects.filter(
            item_id=OuterRef("pk"),
            buying_price__isnull=False,
            movement__movement_type=StockMovementType.IN,
        )
        if shop_id is not None:
            qs = qs.filter(movement__shop_id=shop_id)
        return qs.order_by("-movement__created_at", "-id").values("buying_price")[:1]

    annotations = {"any_last_buy": Subquery(_latest_subquery())}
    if prefer_shop_id is not None:
        annotations["shop_last_buy"] = Subquery(
            _latest_subquery(shop_id=prefer_shop_id)
        )

    result = {}
    value_fields = ("pk", "shop_last_buy", "any_last_buy") if prefer_shop_id is not None else (
        "pk",
        "any_last_buy",
    )
    for row in Item.objects.filter(pk__in=ids).annotate(**annotations).values_list(
        *value_fields
    ):
        if prefer_shop_id is not None:
            item_id, shop_price, any_price = row
            price = shop_price if shop_price is not None else any_price
        else:
            item_id, price = row
        if price is not None:
            result[item_id] = price
    return result


def build_stock_catalog_page(
    *,
    shop_id=None,
    shop_ids=None,
    requested_from_shop_id=None,
    mode: str = "in",
    q: str = "",
    page=1,
    page_size=48,
    include_suspended: bool = True,
    include_totals: bool = True,
):
    """
    Paginated stock matrix rows for buy-stock / stock-management action modes
    and current-stock (view) mode.
    """
    from django.db.models import Sum

    from shops.models import Shop

    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size or 48)
    except (TypeError, ValueError):
        page_size = 48
    page_size = min(max(page_size, 12), 96)
    mode = (mode or "in").strip().lower()
    if mode not in ("in", "out", "request", "view"):
        mode = "in"

    qs = Item.objects.only(
        "id",
        "category",
        "name",
        "description",
        "track_serial_number",
        "is_suspended",
    ).order_by("category", "name")
    if not include_suspended:
        qs = qs.filter(is_suspended=False)

    query = (q or "").strip()
    if query:
        qs = qs.filter(item_text_search_q(query))

    page_data = _paginate_queryset(qs, page=page, page_size=page_size)
    total = page_data["total"]
    items = page_data["items"]
    has_more = page_data["has_more"]
    next_page = page_data["next_page"]
    page = page_data["page"]
    item_ids = [item.pk for item in items]

    # Multi-shop matrix for view and for in/out/request when shop_ids are provided.
    multi_shop_modes = {"view", "in", "out", "request"}
    use_multi_shop = mode in multi_shop_modes and (
        bool(shop_ids) or (mode == "view" and not shop_id)
    )
    view_shop_ids = []
    if use_multi_shop:
        if shop_id and not shop_ids:
            view_shop_ids = [int(shop_id)]
        elif shop_ids:
            view_shop_ids = [int(sid) for sid in shop_ids if sid]
        else:
            view_shop_ids = list(
                Shop.objects.filter(is_hidden=False, is_suspended=False)
                .order_by("name")
                .values_list("pk", flat=True)
            )

    shop_qty_map = {}
    from_qty_map = {}
    multi_qty_map = {}
    if item_ids and shop_id and not use_multi_shop:
        shop_qty_map = {
            item_id: qty
            for item_id, qty in ShopStock.objects.filter(
                shop_id=shop_id, item_id__in=item_ids
            ).values_list("item_id", "quantity")
        }
    if item_ids and requested_from_shop_id:
        from_qty_map = {
            item_id: qty
            for item_id, qty in ShopStock.objects.filter(
                shop_id=requested_from_shop_id, item_id__in=item_ids
            ).values_list("item_id", "quantity")
        }
    if item_ids and view_shop_ids:
        for item_id, sid, qty in ShopStock.objects.filter(
            shop_id__in=view_shop_ids, item_id__in=item_ids
        ).values_list("item_id", "shop_id", "quantity"):
            multi_qty_map.setdefault(item_id, {})[sid] = int(qty)

    last_buying = {}
    if mode == "in" and item_ids:
        last_buying = last_buying_prices_for_items(
            item_ids, prefer_shop_id=shop_id
        )

    shops_meta = []
    if view_shop_ids:
        shops_by_id = {
            shop.pk: shop
            for shop in Shop.objects.filter(pk__in=view_shop_ids).only("id", "name")
        }
        for sid in view_shop_ids:
            shop = shops_by_id.get(sid)
            if shop:
                shops_meta.append({"id": shop.pk, "name": shop.name})

    rows = []
    for item in items:
        description = (item.description or "").strip()
        if len(description) > 120:
            description = description[:117].rstrip() + "..."
        price = last_buying.get(item.pk)
        row = {
            "id": item.pk,
            "name": item.name,
            "category": item.category,
            "description": description,
            "shop_qty": int(shop_qty_map.get(item.pk, 0)),
            "requested_from_qty": int(from_qty_map.get(item.pk, 0)),
            "track_serial": bool(item.track_serial_number),
            "is_suspended": bool(item.is_suspended),
            "last_buying_price": (
                format(price, "f") if price is not None else None
            ),
        }
        if use_multi_shop:
            quantities = [
                int(multi_qty_map.get(item.pk, {}).get(sid, 0)) for sid in view_shop_ids
            ]
            row["shop_quantities"] = quantities
            row["row_total"] = sum(quantities)
            if len(view_shop_ids) == 1:
                row["shop_qty"] = quantities[0] if quantities else 0
        rows.append(row)

    total_units = 0
    if include_totals:
        if shop_id and not use_multi_shop:
            total_units = (
                ShopStock.objects.filter(shop_id=shop_id).aggregate(
                    total=Sum("quantity")
                )["total"]
                or 0
            )
        elif view_shop_ids:
            total_units = (
                ShopStock.objects.filter(shop_id__in=view_shop_ids).aggregate(
                    total=Sum("quantity")
                )["total"]
                or 0
            )

    return {
        "ok": True,
        "mode": mode,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "next_page": next_page,
        "total_units": int(total_units),
        "items": rows,
        "q": query,
        "shop_id": shop_id,
        "requested_from_shop_id": requested_from_shop_id,
        "shops": shops_meta,
        "show_all_shops": use_multi_shop and len(view_shop_ids) > 1,
        "editable_matrix": mode in ("in", "out", "request") and use_multi_shop,
    }


def build_item_management_catalog_page(*, q: str = "", page=1, page_size=48, sort="category"):
    """Paginated item-management rows with shop price display fields."""
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size or 48)
    except (TypeError, ValueError):
        page_size = 48
    page_size = min(max(page_size, 12), 96)

    sort_key = (sort or "category").strip().lower()
    if sort_key not in {"category", "name"}:
        sort_key = "category"
    order_by = ("name", "category") if sort_key == "name" else ("category", "name")

    qs = Item.objects.only(
        "id",
        "category",
        "name",
        "description",
        "minimum_selling_price",
        "maximum_selling_price",
        "shop_price",
        "use_individual_shop_prices",
        "track_serial_number",
        "is_suspended",
        "image",
    ).order_by(*order_by)

    query = (q or "").strip()
    if query:
        qs = qs.filter(item_text_search_q(query))

    page_data = _paginate_queryset(qs, page=page, page_size=page_size)
    total = page_data["total"]
    items = page_data["items"]
    has_more = page_data["has_more"]
    next_page = page_data["next_page"]
    page = page_data["page"]
    item_ids = [item.pk for item in items]
    active_shops = _active_shops()

    prices_list_by_item = {}
    prices_map_by_item = {}
    if item_ids:
        for item_id, shop_id, price in ShopItemPrice.objects.filter(
            item_id__in=item_ids
        ).values_list("item_id", "shop_id", "price"):
            prices_list_by_item.setdefault(item_id, []).append(price)
            prices_map_by_item.setdefault(item_id, {})[shop_id] = price

    rows = []
    for item in items:
        description = (item.description or "").strip()
        prices = prices_list_by_item.get(item.pk) or []
        item_shop_map = prices_map_by_item.get(item.pk) or {}
        if not item.use_individual_shop_prices or not prices:
            shop_price_display = f"KSh {item.shop_price:.2f}"
        else:
            low = min(prices)
            high = max(prices)
            shop_price_display = (
                f"KSh {low:.2f}" if low == high else f"KSh {low:.2f} – {high:.2f}"
            )
        shop_price_rows = []
        for shop in active_shops:
            if item.use_individual_shop_prices:
                override = item_shop_map.get(shop.pk)
                resolved = item.resolve_list_price(override)
            else:
                resolved = item.resolve_list_price(None)
            shop_price_rows.append(
                {
                    "shop_id": shop.pk,
                    "shop_name": shop.name,
                    "price": f"{resolved:.2f}",
                }
            )
        shop_prices = {
            str(row["shop_id"]): row["price"] for row in shop_price_rows
        }
        image_url = ""
        try:
            if item.image:
                image_url = item.image.url
        except Exception:
            image_url = ""
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "category": item.category,
                "description": description,
                "minimum_selling_price": f"{item.minimum_selling_price:.2f}",
                "maximum_selling_price": f"{item.maximum_selling_price:.2f}",
                "shop_price": f"{item.shop_price:.2f}",
                "shop_price_display": shop_price_display,
                "pricing_mode": (
                    "individual" if item.use_individual_shop_prices else "single"
                ),
                "shop_prices": shop_prices,
                "shop_price_rows": shop_price_rows,
                "track_serial": bool(item.track_serial_number),
                "is_suspended": bool(item.is_suspended),
                "image_url": image_url,
            }
        )

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "next_page": next_page,
        "shops": [
            {"id": shop.pk, "name": shop.name} for shop in active_shops
        ],
        "items": rows,
        "q": query,
        "sort": sort_key,
    }


def search_available_serials(
    *,
    item_id,
    shop_id,
    query: str = "",
    exclude=None,
    limit: int = 10,
    match: str = "contains",
):
    from django.db.models import Case, IntegerField, Value, When
    from django.db.models.functions import Right, Upper

    try:
        item_pk = int(item_id)
        shop_pk = int(shop_id)
    except (TypeError, ValueError):
        return []

    qs = ItemSerial.objects.filter(
        item_id=item_pk,
        shop_id=shop_pk,
        is_available=True,
    )

    exclude = [str(value or "").strip().upper() for value in (exclude or []) if value]
    if exclude:
        qs = qs.exclude(serial_number__in=exclude)

    query = (query or "").strip().upper()
    match_mode = (match or "contains").strip().lower()
    if query:
        if match_mode in ("last4", "endswith", "suffix"):
            # Progressive last-4: match serials whose final 4 chars contain what was typed
            # (not only exact endswith), closest matches first.
            qs = qs.annotate(_suffix=Upper(Right("serial_number", 4))).filter(
                _suffix__contains=query
            ).annotate(
                _rank=Case(
                    When(_suffix=query, then=Value(0)),
                    When(_suffix__startswith=query, then=Value(1)),
                    When(serial_number__iendswith=query, then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                )
            ).order_by("_rank", "serial_number")
        else:
            qs = qs.filter(serial_number__icontains=query).annotate(
                _rank=Case(
                    When(serial_number__iexact=query, then=Value(0)),
                    When(serial_number__istartswith=query, then=Value(1)),
                    When(serial_number__iendswith=query, then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                )
            ).order_by("_rank", "serial_number")
    else:
        qs = qs.order_by("serial_number")

    return list(qs.values_list("serial_number", flat=True)[:limit])


def check_serials_already_in_stock(*, item_id, serials) -> dict[str, dict]:
    """Return map of SERIAL -> {shop_id, shop_name} for units already available."""
    try:
        item_pk = int(item_id)
    except (TypeError, ValueError):
        return {}

    wanted = []
    seen = set()
    for raw in serials or []:
        serial = str(raw or "").strip().upper()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        wanted.append(serial)
        # Keep the endpoint cheap for live typing / paste floods.
        if len(wanted) >= 12:
            break
    if not wanted:
        return {}

    rows = ItemSerial.objects.filter(
        item_id=item_pk,
        serial_number__in=wanted,
        is_available=True,
    ).values("serial_number", "shop_id", "shop__name")
    found = {}
    for row in rows:
        found[row["serial_number"]] = {
            "shop_id": row["shop_id"],
            "shop_name": (row["shop__name"] or "") if row["shop_id"] else "",
        }
    return found


def search_suppliers(
    *,
    query: str,
    by: str = "name",
    dial: str = "",
    limit: int = 8,
    match: str = "contains",
):
    query = (query or "").strip().upper()
    by = (by or "name").strip().lower()
    match_mode = (match or "contains").strip().lower()

    qs = Supplier.objects.all()
    if by == "phone":
        digits = _normalize_national_phone(query, dial)
        last4_mode = match_mode in ("last4", "endswith", "suffix")
        min_digits = 1 if last4_mode else 3
        if len(digits) < min_digits:
            return []
        if last4_mode:
            digits = digits[-4:]
        dial = (dial or "").strip()
        if dial:
            qs = qs.filter(phone_country_code=dial)
        matches = []
        for supplier in qs.order_by("name", "phone_number")[:120]:
            phone_digits = _normalize_phone_digits(supplier.phone_number)
            if last4_mode:
                if phone_digits.endswith(digits):
                    matches.append(supplier)
            elif digits in phone_digits:
                matches.append(supplier)
            if len(matches) >= limit:
                break
        return matches

    if len(query) < 2:
        return []
    return list(qs.filter(name__icontains=query).order_by("name", "phone_number")[:limit])


def upsert_supplier(
    *,
    name: str,
    dial: str,
    phone: str,
    iso: str = "",
    supplier_id=None,
):
    name = (name or "").strip().upper()
    dial = (dial or "").strip()
    phone = _normalize_national_phone(phone, dial)
    iso = ((iso or "").strip().upper() or ISO_BY_DIAL.get(dial, "KE"))[:2]
    if not name or not dial or not phone:
        return None

    try:
        supplier_pk = int(supplier_id) if supplier_id not in (None, "") else None
    except (TypeError, ValueError):
        supplier_pk = None

    if supplier_pk:
        supplier = Supplier.objects.filter(pk=supplier_pk).first()
        if supplier is not None:
            conflict = (
                Supplier.objects.filter(phone_country_code=dial, phone_number=phone)
                .exclude(pk=supplier.pk)
                .first()
            )
            if conflict is not None:
                conflict.name = name
                conflict.phone_country_iso = iso or "KE"
                conflict.save(update_fields=["name", "phone_country_iso", "updated_at"])
                return conflict
            supplier.name = name
            supplier.phone_country_code = dial
            supplier.phone_number = phone
            supplier.phone_country_iso = iso or "KE"
            supplier.save(
                update_fields=[
                    "name",
                    "phone_country_code",
                    "phone_number",
                    "phone_country_iso",
                    "updated_at",
                ]
            )
            return supplier

    supplier, _created = Supplier.objects.update_or_create(
        phone_country_code=dial,
        phone_number=phone,
        defaults={
            "name": name,
            "phone_country_iso": iso or "KE",
        },
    )
    return supplier


def upsert_suppliers_from_lines(lines):
    seen = set()
    for line in lines:
        name = (line.get("supplier_name") or "").strip().upper()
        dial = (line.get("supplier_phone_country_code") or "").strip()
        phone = _normalize_national_phone(line.get("supplier_phone_number") or "", dial)
        supplier_id = line.get("supplier_id") or ""
        key = (str(supplier_id or ""), dial, phone)
        if not name or not dial or not phone or key in seen:
            continue
        seen.add(key)
        upsert_supplier(
            name=name,
            dial=dial,
            phone=phone,
            supplier_id=supplier_id,
        )


def _parse_price(raw_value: str, label: str) -> Decimal:
    value = (raw_value or "").strip()
    if not value:
        raise ValidationError(f"{label} is required.")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValidationError(f"Enter a valid {label.lower()}.")
    if amount < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return amount.quantize(Decimal("0.01"))


def _clamp_price(price: Decimal, minimum_price, maximum_price) -> Decimal:
    """Keep a price inside the selling range (min/max inclusive)."""
    clamped = price
    if minimum_price is not None and clamped < minimum_price:
        clamped = minimum_price
    if maximum_price is not None and clamped > maximum_price:
        clamped = maximum_price
    return clamped.quantize(Decimal("0.01"))


def _existing_shop_prices(item: Item) -> dict:
    return {
        shop_id: price.quantize(Decimal("0.01"))
        for shop_id, price in ShopItemPrice.objects.filter(item=item).values_list(
            "shop_id", "price"
        )
    }


def _active_shops():
    from shops.models import Shop

    return list(Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name"))


def _pricing_mode_from_data(data) -> str:
    mode = (data.get("pricing_mode") or "single").strip().lower()
    if mode not in ("single", "individual"):
        return "single"
    return mode


def _parse_individual_shop_prices(
    data,
    shops,
    minimum_price,
    maximum_price,
    *,
    existing_item=None,
    existing_shop_prices=None,
    editable_shop_ids=None,
):
    """Parse per-shop prices from POST fields shop_price_<id>.

    On create, blank shop prices default to the maximum selling price.
    On update, blank or unchanged prices keep the current shop price and are
    only clamped when the min/max range changes. Shops outside editable_shop_ids
    always keep their stored price (clamped when the range changes).
    """
    errors = []
    prices_by_shop = {}

    if not shops:
        errors.append("No active shops are available to set individual prices.")
        return prices_by_shop, errors

    if existing_item is not None:
        existing_shop_prices = existing_shop_prices or _existing_shop_prices(existing_item)

    for shop in shops:
        raw = data.get(f"shop_price_{shop.pk}")
        raw_str = str(raw).strip() if raw is not None else ""
        existing = existing_shop_prices.get(shop.pk) if existing_item is not None else None

        if (
            existing_item is not None
            and editable_shop_ids is not None
            and shop.pk not in editable_shop_ids
        ):
            if existing is not None:
                prices_by_shop[shop.pk] = _clamp_price(
                    existing, minimum_price, maximum_price
                )
            continue

        if existing_item is not None and existing is not None:
            if not raw_str:
                prices_by_shop[shop.pk] = _clamp_price(
                    existing, minimum_price, maximum_price
                )
                continue
            try:
                submitted = _parse_price(raw_str, f"Shop price for {shop.name}")
            except ValidationError as exc:
                errors.append(exc.message)
                continue
            if submitted == existing:
                prices_by_shop[shop.pk] = _clamp_price(
                    existing, minimum_price, maximum_price
                )
                continue
            if minimum_price is not None and maximum_price is not None:
                if not (minimum_price <= submitted <= maximum_price):
                    errors.append(
                        f"Shop price for “{shop.name}” must be between the minimum and maximum selling prices."
                    )
                    continue
            prices_by_shop[shop.pk] = submitted
            continue

        if raw_str == "":
            if maximum_price is None:
                errors.append(
                    f"Enter a shop price for “{shop.name}”, or set a maximum selling price."
                )
                continue
            prices_by_shop[shop.pk] = maximum_price
            continue
        try:
            price = _parse_price(raw_str, f"Shop price for {shop.name}")
        except ValidationError as exc:
            errors.append(exc.message)
            continue
        if price <= 0:
            if maximum_price is None:
                errors.append(
                    f"Enter a shop price for “{shop.name}”, or set a maximum selling price."
                )
                continue
            prices_by_shop[shop.pk] = maximum_price
            continue
        if minimum_price is not None and maximum_price is not None:
            if not (minimum_price <= price <= maximum_price):
                errors.append(
                    f"Shop price for “{shop.name}” must be between the minimum and maximum selling prices."
                )
                continue
        prices_by_shop[shop.pk] = price

    return prices_by_shop, errors


def _sync_shop_item_prices(item: Item, prices_by_shop: dict) -> None:
    """Replace ShopItemPrice rows for an item with the given shop→price map."""
    ShopItemPrice.objects.filter(item=item).exclude(shop_id__in=prices_by_shop.keys()).delete()
    for shop_id, price in prices_by_shop.items():
        ShopItemPrice.objects.update_or_create(
            item=item,
            shop_id=shop_id,
            defaults={"price": price},
        )


def validate_item_payload(data, files, *, existing_item=None, editable_shop_ids=None) -> dict:
    category = (data.get("category") or "").strip()
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    image = files.get("image")
    remove_image = (data.get("remove_image") or "").strip() in ("1", "true", "on", "yes")
    pricing_mode = _pricing_mode_from_data(data)

    errors = []
    cleaned = {}

    if not category:
        errors.append("Item category is required.")
    else:
        cleaned["category"] = category.upper()

    if not name:
        errors.append("Item name is required.")
    else:
        cleaned["name"] = name.upper()

    cleaned["description"] = description
    cleaned["track_serial_number"] = (data.get("track_serial_number") or "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    cleaned["pricing_mode"] = pricing_mode
    cleaned["use_individual_shop_prices"] = pricing_mode == "individual"

    try:
        minimum_price = _parse_price(data.get("minimum_selling_price"), "Minimum selling price")
        maximum_price = _parse_price(data.get("maximum_selling_price"), "Maximum selling price")
    except ValidationError as exc:
        errors.append(exc.message)
        minimum_price = maximum_price = None

    shop_price = None
    shop_prices = {}

    if pricing_mode == "individual":
        shops = _active_shops()
        shop_prices, price_errors = _parse_individual_shop_prices(
            data,
            shops,
            minimum_price,
            maximum_price,
            existing_item=existing_item,
            editable_shop_ids=editable_shop_ids,
        )
        errors.extend(price_errors)
        if shop_prices:
            shop_price = min(shop_prices.values())
    else:
        raw_shop_price = data.get("shop_price")
        raw_shop_price_str = (
            str(raw_shop_price).strip() if raw_shop_price is not None else ""
        )
        existing_single_price = (
            existing_item.shop_price.quantize(Decimal("0.01"))
            if existing_item is not None
            else None
        )

        if existing_item is not None and existing_single_price is not None:
            if not raw_shop_price_str:
                shop_price = _clamp_price(
                    existing_single_price, minimum_price, maximum_price
                )
            else:
                try:
                    submitted = _parse_price(raw_shop_price_str, "Shop price")
                except ValidationError as exc:
                    errors.append(exc.message)
                    submitted = None
                if submitted is not None:
                    if submitted <= 0:
                        shop_price = _clamp_price(
                            maximum_price or existing_single_price,
                            minimum_price,
                            maximum_price,
                        )
                    elif submitted == existing_single_price:
                        shop_price = _clamp_price(
                            existing_single_price, minimum_price, maximum_price
                        )
                    elif (
                        minimum_price is not None
                        and maximum_price is not None
                        and not (minimum_price <= submitted <= maximum_price)
                    ):
                        errors.append(
                            "Shop price must be between the minimum and maximum selling prices."
                        )
                        shop_price = None
                    else:
                        shop_price = submitted
        elif raw_shop_price_str == "":
            if maximum_price is not None:
                shop_price = maximum_price
            else:
                errors.append("Shop price is required.")
        else:
            try:
                shop_price = _parse_price(raw_shop_price_str, "Shop price")
            except ValidationError as exc:
                errors.append(exc.message)
                shop_price = None

            if shop_price is not None and shop_price <= 0:
                if maximum_price is not None:
                    shop_price = maximum_price
                else:
                    errors.append("Shop price must be greater than zero.")
                    shop_price = None

            if (
                minimum_price is not None
                and maximum_price is not None
                and shop_price is not None
                and not (minimum_price <= shop_price <= maximum_price)
            ):
                errors.append(
                    "Shop price must be between the minimum and maximum selling prices."
                )

    if minimum_price is not None and maximum_price is not None:
        if minimum_price > maximum_price:
            errors.append("Minimum selling price cannot be greater than maximum selling price.")

    if image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Item image must be JPG, PNG, WEBP, or GIF.")
        elif image.size > MAX_IMAGE_BYTES:
            errors.append("Item image must be 5 MB or smaller.")
        else:
            cleaned["image"] = image
    elif remove_image and existing_item and existing_item.image:
        cleaned["remove_image"] = True

    if errors:
        raise ValidationError(errors)

    cleaned["minimum_selling_price"] = minimum_price
    cleaned["maximum_selling_price"] = maximum_price
    cleaned["shop_price"] = shop_price
    cleaned["shop_prices"] = shop_prices
    return cleaned


def create_item(profile, data, files) -> Item:
    cleaned = validate_item_payload(data, files)
    with transaction.atomic():
        item = Item.objects.create(
            category=cleaned["category"],
            name=cleaned["name"],
            description=cleaned["description"],
            minimum_selling_price=cleaned["minimum_selling_price"],
            maximum_selling_price=cleaned["maximum_selling_price"],
            shop_price=cleaned["shop_price"],
            use_individual_shop_prices=cleaned["use_individual_shop_prices"],
            image=cleaned.get("image"),
            track_serial_number=cleaned["track_serial_number"],
            created_by=profile,
        )
        if cleaned["use_individual_shop_prices"]:
            _sync_shop_item_prices(item, cleaned["shop_prices"])
        return item


def update_item(item: Item, data, files, *, editable_shop_ids=None) -> Item:
    cleaned = validate_item_payload(
        data,
        files,
        existing_item=item,
        editable_shop_ids=editable_shop_ids,
    )
    was_individual = item.use_individual_shop_prices
    with transaction.atomic():
        item.category = cleaned["category"]
        item.name = cleaned["name"]
        item.description = cleaned["description"]
        item.minimum_selling_price = cleaned["minimum_selling_price"]
        item.maximum_selling_price = cleaned["maximum_selling_price"]
        item.shop_price = cleaned["shop_price"]
        item.use_individual_shop_prices = cleaned["use_individual_shop_prices"]
        item.track_serial_number = cleaned["track_serial_number"]

        if cleaned.get("image"):
            if item.image:
                item.image.delete(save=False)
            item.image = cleaned["image"]
        elif cleaned.get("remove_image"):
            if item.image:
                item.image.delete(save=False)
            item.image = None

        item.save()

        if cleaned["use_individual_shop_prices"]:
            _sync_shop_item_prices(item, cleaned["shop_prices"])
        elif was_individual:
            ShopItemPrice.objects.filter(item=item).delete()

        return item


def toggle_item_suspended(item: Item) -> Item:
    item.is_suspended = not item.is_suspended
    item.save(update_fields=["is_suspended", "updated_at"])
    return item


def delete_item(item: Item) -> None:
    if item.image:
        item.image.delete(save=False)
    item.delete()


def _parse_serial_numbers(raw_value: str) -> list[str]:
    parts = []
    for chunk in (raw_value or "").replace(",", "\n").splitlines():
        serial = chunk.strip().upper()
        if serial:
            parts.append(serial)
    return parts


def _validate_supplier(
    *,
    name: str,
    dial: str,
    phone: str,
    line_label: str,
):
    name = (name or "").strip().upper()
    dial = (dial or "").strip()
    phone = _normalize_national_phone(phone, dial)
    if not name:
        return None, f"{line_label}: supplier name is required."
    if dial not in VALID_DIAL_CODES:
        return None, f"{line_label}: select a valid supplier country code."
    if not phone:
        return None, f"{line_label}: supplier phone number is required."
    if len(phone) != 9 or not phone.isdigit():
        return None, f"{line_label}: enter a valid 9-digit supplier phone number."
    return (
        {
            "supplier_name": name,
            "supplier_phone_country_code": dial,
            "supplier_phone_number": phone,
        },
        None,
    )


def _parse_movement_lines(data, movement_type: str):
    raw_ids = data.getlist("item_id") if hasattr(data, "getlist") else data.get("item_id") or []
    raw_qtys = data.getlist("quantity") if hasattr(data, "getlist") else data.get("quantity") or []
    raw_prices = (
        data.getlist("buying_price") if hasattr(data, "getlist") else data.get("buying_price") or []
    )
    raw_reasons = data.getlist("reason") if hasattr(data, "getlist") else data.get("reason") or []
    raw_payments = (
        data.getlist("payment_status")
        if hasattr(data, "getlist")
        else data.get("payment_status") or []
    )
    raw_notes = data.getlist("note") if hasattr(data, "getlist") else data.get("note") or []
    raw_serials = (
        data.getlist("serial_numbers")
        if hasattr(data, "getlist")
        else data.get("serial_numbers") or []
    )
    raw_supplier_names = (
        data.getlist("supplier_name")
        if hasattr(data, "getlist")
        else data.get("supplier_name") or []
    )
    raw_supplier_dials = (
        data.getlist("supplier_phone_country_code")
        if hasattr(data, "getlist")
        else data.get("supplier_phone_country_code") or []
    )
    raw_supplier_phones = (
        data.getlist("supplier_phone_number")
        if hasattr(data, "getlist")
        else data.get("supplier_phone_number") or []
    )
    raw_supplier_ids = (
        data.getlist("supplier_id") if hasattr(data, "getlist") else data.get("supplier_id") or []
    )
    raw_refunds = data.getlist("refund") if hasattr(data, "getlist") else data.get("refund") or []
    raw_refund_amounts = (
        data.getlist("refund_amount")
        if hasattr(data, "getlist")
        else data.get("refund_amount") or []
    )
    raw_line_shops = (
        data.getlist("line_shop_id")
        if hasattr(data, "getlist")
        else data.get("line_shop_id") or []
    )

    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if isinstance(raw_qtys, str):
        raw_qtys = [raw_qtys]
    if isinstance(raw_prices, str):
        raw_prices = [raw_prices]
    if isinstance(raw_reasons, str):
        raw_reasons = [raw_reasons]
    if isinstance(raw_payments, str):
        raw_payments = [raw_payments]
    if isinstance(raw_notes, str):
        raw_notes = [raw_notes]
    if isinstance(raw_serials, str):
        raw_serials = [raw_serials]
    if isinstance(raw_supplier_names, str):
        raw_supplier_names = [raw_supplier_names]
    if isinstance(raw_supplier_dials, str):
        raw_supplier_dials = [raw_supplier_dials]
    if isinstance(raw_supplier_phones, str):
        raw_supplier_phones = [raw_supplier_phones]
    if isinstance(raw_supplier_ids, str):
        raw_supplier_ids = [raw_supplier_ids]
    if isinstance(raw_refunds, str):
        raw_refunds = [raw_refunds]
    if isinstance(raw_refund_amounts, str):
        raw_refund_amounts = [raw_refund_amounts]
    if isinstance(raw_line_shops, str):
        raw_line_shops = [raw_line_shops]

    if not raw_ids:
        raise ValidationError("Enter quantity on at least one item.")

    track_by_id = {
        str(pk): track
        for pk, track in Item.objects.filter(pk__in=raw_ids).values_list(
            "pk", "track_serial_number"
        )
    }

    from shops.services import get_company_stock_settings

    stock_req = get_company_stock_settings()

    lines = []
    errors = []
    for index, item_id in enumerate(raw_ids):
        item_id = str(item_id).strip()
        tracks_serial = bool(track_by_id.get(item_id))
        # Stock requests are quantity-only; serials are chosen when fulfilling later.
        if movement_type == StockMovementType.REQUEST:
            tracks_serial = False
        serials = _parse_serial_numbers(
            raw_serials[index] if index < len(raw_serials) else ""
        )
        note = (raw_notes[index] if index < len(raw_notes) else "").strip()
        line_label = f"Item line {index + 1}"

        if tracks_serial:
            if not serials:
                continue
            if len(serials) != len(set(serials)):
                errors.append(f"{line_label}: duplicate serial numbers are not allowed.")
                continue
            quantity = len(serials)
        else:
            qty_raw = (raw_qtys[index] if index < len(raw_qtys) else "").strip()
            if not qty_raw:
                continue
            try:
                quantity = int(qty_raw)
            except (TypeError, ValueError):
                errors.append(f"{line_label}: enter a valid quantity.")
                continue
            if quantity <= 0:
                continue
            serials = []

        payment_status = ""
        reason = ""
        refund = ""
        refund_amount = None
        buying_price = None
        supplier = None

        if movement_type == StockMovementType.IN:
            payment_status = (
                raw_payments[index] if index < len(raw_payments) else ""
            ).strip().lower()
            valid_payments = {choice.value for choice in StockPaymentStatus}
            if stock_req.require_payment_status_on_in:
                if payment_status not in valid_payments:
                    errors.append(
                        f"{line_label}: choose a payment status (unpaid, paid, or partial)."
                    )
                    continue
            elif payment_status not in valid_payments:
                payment_status = ""

            price_raw = raw_prices[index] if index < len(raw_prices) else ""
            if stock_req.require_buying_price_on_in or str(price_raw).strip():
                try:
                    buying_price = _parse_price(price_raw, "Buying price")
                except ValidationError as exc:
                    if stock_req.require_buying_price_on_in or str(price_raw).strip():
                        message = (
                            exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                        )
                        errors.append(f"{line_label}: {message}")
                        continue
            else:
                buying_price = None

            name_raw = raw_supplier_names[index] if index < len(raw_supplier_names) else ""
            dial_raw = raw_supplier_dials[index] if index < len(raw_supplier_dials) else ""
            phone_raw = (
                raw_supplier_phones[index] if index < len(raw_supplier_phones) else ""
            )
            # Dial defaults to +254 in the UI; only treat name/phone as "provided".
            supplier_any = bool(str(name_raw).strip() or str(phone_raw).strip())
            if stock_req.require_supplier_on_in or supplier_any:
                supplier, supplier_error = _validate_supplier(
                    name=name_raw,
                    dial=dial_raw,
                    phone=phone_raw,
                    line_label=line_label,
                )
                if supplier_error:
                    errors.append(supplier_error)
                    continue
            else:
                supplier = {
                    "supplier_name": "",
                    "supplier_phone_country_code": "",
                    "supplier_phone_number": "",
                }

        if movement_type == StockMovementType.OUT:
            reason = (raw_reasons[index] if index < len(raw_reasons) else "").strip().lower()
            valid_reasons = {choice.value for choice in StockOutReason}
            if stock_req.require_reason_on_out:
                if reason not in valid_reasons:
                    errors.append(
                        f"{line_label}: choose a reason (waste, transfer, display, or return)."
                    )
                    continue
            elif reason not in valid_reasons:
                reason = ""

            refund = (raw_refunds[index] if index < len(raw_refunds) else "").strip().lower()
            refund_amount = None
            if stock_req.require_refund_on_out:
                if refund not in ("yes", "no"):
                    errors.append(
                        f"{line_label}: choose whether a refund applies (yes or no)."
                    )
                    continue
            elif refund not in ("yes", "no"):
                refund = ""

            if refund == "yes":
                amount_raw = (
                    raw_refund_amounts[index] if index < len(raw_refund_amounts) else ""
                )
                try:
                    refund_amount = _parse_price(amount_raw, "Refund amount")
                except ValidationError as exc:
                    message = (
                        exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    )
                    errors.append(f"{line_label}: {message}")
                    continue
                if refund_amount <= 0:
                    errors.append(f"{line_label}: refund amount must be greater than zero.")
                    continue

        if movement_type == StockMovementType.REQUEST and stock_req.require_note_on_request:
            if not note:
                errors.append(f"{line_label}: enter a note for this request.")
                continue

        line = {
            "item_id": item_id,
            "quantity": quantity,
            "note": note,
            "serial_numbers": serials,
            "track_serial_number": tracks_serial,
        }
        line_shop = (
            str(raw_line_shops[index]).strip()
            if index < len(raw_line_shops)
            else ""
        )
        if line_shop:
            line["shop_id"] = line_shop
        if movement_type == StockMovementType.IN:
            line["buying_price"] = buying_price
            line["payment_status"] = payment_status
            line.update(supplier or {})
            supplier_id_raw = (
                raw_supplier_ids[index] if index < len(raw_supplier_ids) else ""
            )
            line["supplier_id"] = str(supplier_id_raw or "").strip()
        if movement_type == StockMovementType.OUT:
            line["reason"] = reason
            line["refund"] = refund
            line["refund_amount"] = refund_amount
        lines.append(line)

    if errors:
        raise ValidationError(errors)
    if not lines:
        raise ValidationError("Enter quantity or serial numbers on at least one item.")
    return lines


def _get_active_shop(shop_id: str, *, label: str = "shop"):
    from shops.models import Shop

    shop_id = (shop_id or "").strip()
    if not shop_id:
        raise ValidationError(f"Select a {label}.")
    shop = Shop.objects.filter(pk=shop_id, is_hidden=False).first()
    if shop is None:
        raise ValidationError(f"Selected {label} was not found.")
    return shop


def actionable_shops_for_profile(profile):
    """Shops the employee may stock in/out/request for (assigned shops when role is shop-scoped)."""
    from employees.models import SHOP_ASSIGNABLE_ROLES
    from shops.models import Shop

    base = Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name")
    if profile.role in SHOP_ASSIGNABLE_ROLES:
        return list(base.filter(assigned_employees=profile))
    return list(base)


def _assert_shop_allowed(profile, shop, *, label: str = "shop"):
    from employees.models import SHOP_ASSIGNABLE_ROLES

    if profile.role not in SHOP_ASSIGNABLE_ROLES:
        return
    if not profile.assigned_shops.filter(pk=shop.pk).exists():
        raise ValidationError(f"You are not allocated to the selected {label}.")


def apply_stock_movement(profile, movement_type: str, data) -> StockMovement:
    if movement_type not in {
        StockMovementType.IN,
        StockMovementType.OUT,
        StockMovementType.REQUEST,
    }:
        raise ValidationError("Unknown stock action.")

    line_payloads = _parse_movement_lines(data, movement_type)

    if movement_type == StockMovementType.REQUEST:
        # shop_id = requesting (destination). Per-line shop_id = from shop.
        requesting_shop = _get_active_shop(
            data.get("shop_id"), label="requesting shop"
        )
        _assert_shop_allowed(profile, requesting_shop, label="requesting shop")
        fallback_from = str(data.get("requested_from_shop_id") or "").strip()
        grouped_from: dict[str, list] = {}
        for line in line_payloads:
            from_sid = str(line.get("shop_id") or fallback_from).strip()
            if not from_sid:
                raise ValidationError(
                    "Select a shop to request from for each stock line."
                )
            if str(from_sid) == str(requesting_shop.pk):
                raise ValidationError(
                    "Requesting shop and requested shop must be different."
                )
            grouped_from.setdefault(from_sid, []).append(line)

        shop_groups = []
        for from_sid, lines in grouped_from.items():
            from_shop = _get_active_shop(from_sid, label="shop to request from")
            # Requester must be allowed on the destination shop only; the supply
            # shop is the peer being asked, not a shop they must be assigned to.
            if from_shop.is_suspended:
                raise ValidationError(f"Shop “{from_shop.name}” is suspended.")
            shop_groups.append((requesting_shop, lines, from_shop))
    else:
        # Group lines by shop (per-line shop_id from multi-shop matrix, else form shop_id).
        fallback_shop_id = str(data.get("shop_id") or "").strip()
        grouped: dict[str, list] = {}
        for line in line_payloads:
            sid = str(line.get("shop_id") or fallback_shop_id).strip()
            if not sid:
                raise ValidationError("Select a shop for each stock line.")
            line["shop_id"] = sid
            grouped.setdefault(sid, []).append(line)

        shop_groups = []
        for sid, lines in grouped.items():
            shop = _get_active_shop(sid, label="shop")
            _assert_shop_allowed(profile, shop)
            if shop.is_suspended:
                raise ValidationError(f"Shop “{shop.name}” is suspended.")
            shop_groups.append((shop, lines, None))

    last_movement = None
    with transaction.atomic():
        all_item_ids = [
            line["item_id"] for _shop, lines, _from in shop_groups for line in lines
        ]
        items = {
            str(item.pk): item
            for item in Item.objects.select_for_update().filter(pk__in=all_item_ids)
        }

        for shop, group_lines, requested_from in shop_groups:
            prepared = []
            errors = []
            for line in group_lines:
                item = items.get(line["item_id"])
                if item is None:
                    errors.append("One of the selected items could not be found.")
                    continue
                if item.is_suspended and movement_type != StockMovementType.REQUEST:
                    errors.append(f"“{item.name}” is suspended and cannot be moved.")
                    continue

                shop_stock, _ = ShopStock.objects.select_for_update().get_or_create(
                    shop=shop,
                    item=item,
                    defaults={"quantity": 0},
                )
                line["shop_stock"] = shop_stock

                if movement_type == StockMovementType.REQUEST:
                    from_stock, _ = ShopStock.objects.select_for_update().get_or_create(
                        shop=requested_from,
                        item=item,
                        defaults={"quantity": 0},
                    )
                    line["requested_from_stock"] = from_stock

                serials = line.get("serial_numbers") or []
                if item.track_serial_number and movement_type != StockMovementType.REQUEST:
                    if not serials:
                        errors.append(
                            f"“{item.name}” at {shop.name} requires serial numbers."
                        )
                        continue
                    if len(serials) != line["quantity"]:
                        errors.append(
                            f"“{item.name}” at {shop.name}: quantity must match serial count."
                        )
                        continue

                    if movement_type == StockMovementType.IN:
                        existing_rows = list(
                            ItemSerial.objects.select_for_update().filter(
                                item=item, serial_number__in=serials
                            )
                        )
                        available_dupes = [
                            row.serial_number
                            for row in existing_rows
                            if row.is_available
                        ]
                        if available_dupes:
                            errors.append(
                                f"“{item.name}”: serial already in stock "
                                f"({', '.join(sorted(available_dupes)[:5])}"
                                f"{'…' if len(available_dupes) > 5 else ''})."
                            )
                            continue
                        line["reactivate_serials"] = {
                            row.serial_number: row
                            for row in existing_rows
                            if not row.is_available
                        }

                    if movement_type == StockMovementType.OUT:
                        available = {
                            serial.serial_number: serial
                            for serial in ItemSerial.objects.select_for_update().filter(
                                item=item,
                                shop=shop,
                                serial_number__in=serials,
                                is_available=True,
                            )
                        }
                        missing = [s for s in serials if s not in available]
                        if missing:
                            errors.append(
                                f"“{item.name}”: serial not in stock at {shop.name} "
                                f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})."
                            )
                            continue
                        line["serial_objects"] = available

                if movement_type == StockMovementType.OUT:
                    if shop_stock.quantity < line["quantity"]:
                        errors.append(
                            f"Insufficient stock for “{item.name}” at {shop.name} "
                            f"(available {shop_stock.quantity}, requested {line['quantity']})."
                        )
                        continue
                prepared.append((item, line))

            if errors:
                raise ValidationError(errors)
            if not prepared:
                raise ValidationError("Select at least one valid item.")

            out_fallback_costs = {}
            if movement_type == StockMovementType.OUT:
                out_fallback_costs = last_buying_prices_for_items(
                    [item.pk for item, _line in prepared],
                    prefer_shop_id=shop.pk,
                )

            movement = StockMovement.objects.create(
                movement_type=movement_type,
                shop=shop,
                requested_from_shop=requested_from,
                request_status=(
                    StockRequestStatus.PENDING
                    if movement_type == StockMovementType.REQUEST
                    else ""
                ),
                supplier_notified=(
                    False if movement_type == StockMovementType.REQUEST else True
                ),
                created_by=profile,
            )
            last_movement = movement

            paid_total = Decimal("0.00")
            for item, line in prepared:
                out_unit_cost = None
                if movement_type == StockMovementType.OUT:
                    out_unit_cost = resolve_sale_unit_cost(
                        line["shop_stock"],
                        fallback=out_fallback_costs.get(item.pk),
                    )
                    line["unit_cost"] = out_unit_cost
                StockMovementLine.objects.create(
                    movement=movement,
                    item=item,
                    quantity=line["quantity"],
                    buying_price=line.get("buying_price"),
                    unit_cost=out_unit_cost or Decimal("0.00"),
                    payment_status=line.get("payment_status", ""),
                    reason=line.get("reason", ""),
                    refund=line.get("refund", ""),
                    refund_amount=line.get("refund_amount"),
                    note=line.get("note", ""),
                    serial_numbers=line.get("serial_numbers") or [],
                    supplier_name=line.get("supplier_name", ""),
                    supplier_phone_country_code=line.get(
                        "supplier_phone_country_code", ""
                    ),
                    supplier_phone_number=line.get("supplier_phone_number", ""),
                )
                if movement_type == StockMovementType.IN:
                    status = (line.get("payment_status") or "").strip()
                    if status == StockPaymentStatus.PAID:
                        qty = int(line["quantity"] or 0)
                        unit = Decimal(line.get("buying_price") or 0)
                        paid_total += (unit * qty).quantize(Decimal("0.01"))

                shop_stock = line["shop_stock"]

                if movement_type == StockMovementType.IN:
                    if item.track_serial_number:
                        reactivate = line.get("reactivate_serials") or {}
                        create_serials = []
                        for serial in line["serial_numbers"]:
                            existing = reactivate.get(serial)
                            if existing:
                                existing.is_available = True
                                existing.shop = shop
                                existing.save(
                                    update_fields=[
                                        "is_available",
                                        "shop",
                                        "updated_at",
                                    ]
                                )
                            else:
                                create_serials.append(
                                    ItemSerial(
                                        item=item,
                                        shop=shop,
                                        serial_number=serial,
                                        is_available=True,
                                    )
                                )
                        if create_serials:
                            ItemSerial.objects.bulk_create(create_serials)
                    apply_stock_in_average_cost(
                        shop_stock,
                        qty=line["quantity"],
                        unit_cost=line.get("buying_price") or 0,
                    )
                    shop_stock.quantity += line["quantity"]
                    shop_stock.save(
                        update_fields=["quantity", "average_cost", "updated_at"]
                    )
                    item.stock += line["quantity"]
                    item.save(update_fields=["stock", "updated_at"])

                elif movement_type == StockMovementType.OUT:
                    if item.track_serial_number:
                        serial_objects = line.get("serial_objects") or {}
                        for serial in line["serial_numbers"]:
                            obj = serial_objects[serial]
                            obj.is_available = False
                            obj.save(update_fields=["is_available", "updated_at"])
                    shop_stock.quantity -= line["quantity"]
                    shop_stock.save(update_fields=["quantity", "updated_at"])
                    item.stock = max(0, item.stock - line["quantity"])
                    item.save(update_fields=["stock", "updated_at"])
                    # average_cost is unchanged on outbound; unit_cost was stamped on the line.

            if movement_type == StockMovementType.IN:
                upsert_suppliers_from_lines([line for _item, line in prepared])
                if paid_total > 0:
                    line_statuses = {
                        (line.get("payment_status") or "").strip()
                        for _item, line in prepared
                    }
                    if line_statuses == {StockPaymentStatus.PAID}:
                        movement_status = StockPaymentStatus.PAID
                    else:
                        movement_status = StockPaymentStatus.PARTIAL
                    movement.amount_paid = paid_total
                    movement.payment_status = movement_status
                    movement.save(update_fields=["amount_paid", "payment_status"])

    return last_movement


def _normalize_serial_list(values) -> list[str]:
    seen = set()
    serials = []
    for value in values or []:
        serial = str(value or "").strip().upper()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        serials.append(serial)
    return serials


@transaction.atomic
def respond_to_stock_request(
    *,
    movement: StockMovement,
    profile,
    decision: str,
    login_code: str,
    serials_by_line: dict | None = None,
    quantities_by_line: dict | None = None,
):
    """Accept or decline a pending stock request from the supplying shop."""
    from django.utils import timezone

    from employees.services import verify_active_employee_code

    decision = (decision or "").strip().lower()
    if decision not in ("accept", "decline"):
        raise ValidationError("Choose accept or decline.")

    locked = (
        StockMovement.objects.select_for_update()
        .select_related("shop", "requested_from_shop")
        .filter(pk=movement.pk)
        .first()
    )
    if locked is None:
        raise ValidationError("This stock request could not be found.")
    movement = locked

    if (
        movement.movement_type != StockMovementType.REQUEST
        or movement.request_status != StockRequestStatus.PENDING
    ):
        raise ValidationError("This stock request is no longer pending.")

    supplier = movement.requested_from_shop
    requester = movement.shop
    if supplier is None or requester is None:
        raise ValidationError("This stock request is missing shop details.")

    authorising = verify_active_employee_code(login_code)
    if authorising is None:
        raise ValidationError("Enter a valid active staff 6-digit ID.")

    from employees.module_permissions import ensure_employee_may

    ensure_employee_may(
        authorising,
        "my-shop",
        "respond_stock_request",
        message="You do not have permission to accept or decline stock requests.",
    )

    # Prefer the authorising employee as responder when available.
    if authorising is not None:
        profile = authorising

    if decision == "decline":
        movement.request_status = StockRequestStatus.DECLINED
        movement.requester_notified = False
        movement.supplier_notified = True
        movement.responded_by = profile
        movement.responded_at = timezone.now()
        movement.save(
            update_fields=[
                "request_status",
                "requester_notified",
                "supplier_notified",
                "responded_by",
                "responded_at",
            ]
        )
        return movement

    serials_by_line = serials_by_line or {}
    quantities_by_line = quantities_by_line or {}
    lines = list(
        movement.lines.select_related("item").select_for_update().order_by("pk")
    )
    if not lines:
        raise ValidationError("This stock request has no items.")

    prepared = []
    errors = []
    for line in lines:
        item = line.item
        requested_qty = line.quantity
        raw_qty = quantities_by_line.get(str(line.pk))
        if raw_qty in (None, ""):
            transfer_qty = requested_qty
        else:
            try:
                transfer_qty = int(str(raw_qty).strip())
            except (TypeError, ValueError):
                errors.append(f"“{item.name}”: enter a valid transfer quantity.")
                continue

        if transfer_qty < 0:
            errors.append(f"“{item.name}”: transfer quantity cannot be negative.")
            continue
        if transfer_qty > requested_qty:
            errors.append(
                f"“{item.name}”: cannot transfer more than requested ({requested_qty})."
            )
            continue

        supplier_stock, _ = ShopStock.objects.select_for_update().get_or_create(
            shop=supplier,
            item=item,
            defaults={"quantity": 0},
        )
        requester_stock, _ = ShopStock.objects.select_for_update().get_or_create(
            shop=requester,
            item=item,
            defaults={"quantity": 0},
        )

        if transfer_qty == 0:
            prepared.append(
                {
                    "line": line,
                    "item": item,
                    "requested_qty": requested_qty,
                    "transfer_qty": 0,
                    "supplier_stock": supplier_stock,
                    "requester_stock": requester_stock,
                    "serials": [],
                    "serial_objects": {},
                }
            )
            continue

        if supplier_stock.quantity < transfer_qty:
            errors.append(
                f"Insufficient stock for “{item.name}” at {supplier.name} "
                f"(available {supplier_stock.quantity}, transfer {transfer_qty})."
            )
            continue

        serial_objects = {}
        serials = []
        if item.track_serial_number:
            serials = _normalize_serial_list(serials_by_line.get(str(line.pk), []))
            if len(serials) != transfer_qty:
                errors.append(
                    f"“{item.name}”: select exactly {transfer_qty} serial number"
                    f"{'s' if transfer_qty != 1 else ''}."
                )
                continue
            available = {
                serial.serial_number: serial
                for serial in ItemSerial.objects.select_for_update().filter(
                    item=item,
                    shop=supplier,
                    serial_number__in=serials,
                    is_available=True,
                )
            }
            missing = [s for s in serials if s not in available]
            if missing:
                errors.append(
                    f"“{item.name}”: serial not in stock at {supplier.name} "
                    f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})."
                )
                continue
            serial_objects = available

        prepared.append(
            {
                "line": line,
                "item": item,
                "requested_qty": requested_qty,
                "transfer_qty": transfer_qty,
                "supplier_stock": supplier_stock,
                "requester_stock": requester_stock,
                "serials": serials,
                "serial_objects": serial_objects,
            }
        )

    if errors:
        raise ValidationError(errors)

    if not any(row["transfer_qty"] > 0 for row in prepared):
        raise ValidationError("Enter at least one quantity to transfer before accepting.")

    for row in prepared:
        line = row["line"]
        item = row["item"]
        qty = row["transfer_qty"]
        requested_qty = row["requested_qty"]
        supplier_stock = row["supplier_stock"]
        requester_stock = row["requester_stock"]

        if qty > 0 and item.track_serial_number:
            for serial in row["serials"]:
                obj = row["serial_objects"][serial]
                obj.shop = requester
                obj.is_available = True
                obj.save(update_fields=["shop", "is_available", "updated_at"])
            line.serial_numbers = row["serials"]

        if qty != requested_qty:
            note = f"Requested {requested_qty}; transferred {qty}."
            line.note = f"{note} {line.note}".strip() if line.note else note

        line.quantity = qty
        line.save(update_fields=["quantity", "serial_numbers", "note"])

        if qty <= 0:
            continue

        transfer_unit_cost = _money_cost(getattr(supplier_stock, "average_cost", 0))
        apply_stock_in_average_cost(
            requester_stock,
            qty=qty,
            unit_cost=transfer_unit_cost,
        )
        supplier_stock.quantity -= qty
        supplier_stock.save(update_fields=["quantity", "updated_at"])
        requester_stock.quantity += qty
        requester_stock.save(
            update_fields=["quantity", "average_cost", "updated_at"]
        )

    movement.request_status = StockRequestStatus.FULFILLED
    movement.requester_notified = False
    movement.supplier_notified = True
    movement.responded_by = profile
    movement.responded_at = timezone.now()
    movement.save(
        update_fields=[
            "request_status",
            "requester_notified",
            "supplier_notified",
            "responded_by",
            "responded_at",
        ]
    )
    return movement


STOCK_PRINT_LAYOUTS = ("items", "prices", "stock")

# A4 printable height with @page margins 8mm / 9mm (297 - 17).
_STOCK_PRINT_A4_PAGE_MM = 280.0
_STOCK_PRINT_A4_HEADER_MM = 20.0
_STOCK_PRINT_A4_FOOTNOTE_MM = 6.0
_STOCK_PRINT_A4_CATEGORY_OVERHEAD_MM = 7.2
_STOCK_PRINT_A4_ROW_MM = 5.0
_STOCK_PRINT_A4_TABLE_GAP_MM = 1.5


def estimate_stock_print_a4_pages(document: dict | None) -> int:
    """Estimate how many A4 sheets the stock print document will use."""
    import math

    if not document:
        return 1
    categories = document.get("categories") or []
    item_count = int(document.get("item_count") or 0)
    if item_count <= 0 and not categories:
        return 1

    height = _STOCK_PRINT_A4_HEADER_MM + _STOCK_PRINT_A4_FOOTNOTE_MM
    for group in categories:
        rows = len(group.get("rows") or [])
        height += (
            _STOCK_PRINT_A4_CATEGORY_OVERHEAD_MM
            + rows * _STOCK_PRINT_A4_ROW_MM
            + _STOCK_PRINT_A4_TABLE_GAP_MM
        )
    return max(1, math.ceil(height / _STOCK_PRINT_A4_PAGE_MM))


def _format_print_money(value) -> str:
    try:
        amount = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"{amount.quantize(Decimal('0.01')):,.2f}"


def build_stock_print_document(*, layout: str, shops) -> dict:
    """Build printable stock list rows for items / prices / stock layouts."""
    layout = (layout or "items").strip().lower()
    if layout not in STOCK_PRINT_LAYOUTS:
        layout = "items"

    shops = list(shops or [])
    shop_ids = [shop.pk for shop in shops]

    items = list(
        Item.objects.order_by("category", "name").only(
            "id",
            "name",
            "category",
            "shop_price",
            "maximum_selling_price",
            "use_individual_shop_prices",
        )
    )

    stock_map: dict[tuple[int, int], int] = {}
    if layout == "stock" and shop_ids and items:
        item_ids = [item.pk for item in items]
        for item_id, shop_id, qty in ShopStock.objects.filter(
            item_id__in=item_ids, shop_id__in=shop_ids
        ).values_list("item_id", "shop_id", "quantity"):
            stock_map[(item_id, shop_id)] = int(qty or 0)

    price_overrides: dict[tuple[int, int], Decimal] = {}
    if layout == "prices" and shop_ids and items:
        item_ids = [item.pk for item in items]
        for item_id, shop_id, price in ShopItemPrice.objects.filter(
            item_id__in=item_ids, shop_id__in=shop_ids
        ).values_list("item_id", "shop_id", "price"):
            if price is not None:
                price_overrides[(item_id, shop_id)] = Decimal(price)

    categories: list[dict] = []
    current_category = None
    current_rows: list[dict] = []

    def flush_category():
        nonlocal current_category, current_rows
        if current_category is None:
            return
        categories.append({"name": current_category, "rows": current_rows})
        current_rows = []

    for item in items:
        if item.category != current_category:
            flush_category()
            current_category = item.category or "Uncategorised"

        row: dict = {
            "name": item.name,
            "category": item.category or "Uncategorised",
        }

        if layout == "prices":
            prices = []
            for shop in shops:
                override = None
                if item.use_individual_shop_prices:
                    override = price_overrides.get((item.pk, shop.pk))
                amount = item.resolve_list_price(override)
                prices.append(
                    {
                        "shop_id": shop.pk,
                        "shop_name": shop.name,
                        "price": _format_print_money(amount),
                    }
                )
            row["prices"] = prices
            if len(shops) == 1:
                row["price"] = prices[0]["price"] if prices else "0.00"
        elif layout == "stock":
            quantities = []
            row_total = 0
            for shop in shops:
                qty = stock_map.get((item.pk, shop.pk), 0)
                row_total += qty
                quantities.append(
                    {
                        "shop_id": shop.pk,
                        "shop_name": shop.name,
                        "qty": qty,
                    }
                )
            row["quantities"] = quantities
            row["total"] = row_total

        current_rows.append(row)

    flush_category()

    titles = {
        "items": "Items only",
        "prices": "Items and price",
        "stock": "Items with current stock",
    }
    shop_label = ""
    if layout in ("prices", "stock"):
        if len(shops) == 1:
            shop_label = shops[0].name
        elif shops:
            shop_label = f"{len(shops)} shops"

    item_count = sum(len(group["rows"]) for group in categories)
    document = {
        "layout": layout,
        "title": titles.get(layout, "Stock list"),
        "shop_label": shop_label,
        "shops": [{"id": shop.pk, "name": shop.name} for shop in shops],
        "categories": categories,
        "item_count": item_count,
    }
    document["a4_page_estimate"] = estimate_stock_print_a4_pages(document)
    return document


def build_stock_print_pdf(
    *,
    document: dict,
    company_name: str,
    printed_at,
) -> bytes:
    """Render the stock print document as an A4 PDF."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from core.pdf_fonts import MANROPE_PDF, MANROPE_PDF_BOLD, register_manrope_pdf_fonts

    register_manrope_pdf_fonts()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=9 * mm,
        title=f"{company_name} — {document.get('title') or 'Stock list'}",
        author=company_name or "MY-SHOP",
    )

    ink = colors.HexColor("#000000")
    muted = colors.HexColor("#1a1a1a")
    line = colors.HexColor("#000000")
    line_soft = colors.HexColor("#333333")
    head_fill = colors.HexColor("#d0d0d0")
    category_fill = colors.HexColor("#b8b8b8")
    row_alt = colors.HexColor("#ececec")
    accent = colors.HexColor("#000000")

    styles = getSampleStyleSheet()
    kicker = ParagraphStyle(
        "StockKicker",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=accent,
        leading=9,
        spaceAfter=1,
    )
    title_style = ParagraphStyle(
        "StockTitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=14,
        textColor=ink,
        leading=17,
        spaceAfter=1,
    )
    subtitle_style = ParagraphStyle(
        "StockSubtitle",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=9,
        textColor=ink,
        leading=11,
    )
    meta_style = ParagraphStyle(
        "StockMeta",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=8.5,
        textColor=ink,
        leading=11,
        alignment=TA_RIGHT,
    )
    meta_sub = ParagraphStyle(
        "StockMetaSub",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7.5,
        textColor=ink,
        leading=10,
        alignment=TA_RIGHT,
    )
    cell_style = ParagraphStyle(
        "StockCell",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=9,
        textColor=ink,
        leading=11,
    )
    head_cell = ParagraphStyle(
        "StockHeadCell",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7.5,
        textColor=ink,
        leading=9,
    )
    foot_style = ParagraphStyle(
        "StockFoot",
        parent=styles["Normal"],
        fontName=MANROPE_PDF_BOLD,
        fontSize=7,
        textColor=ink,
        leading=9,
    )

    layout = (document.get("layout") or "items").strip().lower()
    shops = list(document.get("shops") or [])
    item_count = int(document.get("item_count") or 0)
    page_estimate = int(document.get("a4_page_estimate") or 1)
    shop_label = (document.get("shop_label") or "").strip()
    subtitle = document.get("title") or "Stock list"
    if shop_label:
        subtitle = f"{subtitle} · {shop_label}"

    stamp = printed_at.strftime("%d %b %Y · %H:%M") if printed_at else ""
    page_word = "page" if page_estimate == 1 else "pages"
    item_word = "item" if item_count == 1 else "items"
    meta_bits = f"{item_count} {item_word} · A4 · ≈ {page_estimate} {page_word}"

    def _esc(text: str) -> str:
        return (
            str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    header = Table(
        [
            [
                [
                    Paragraph("STOCK MANAGEMENT", kicker),
                    Paragraph(_esc(company_name or "MY-SHOP"), title_style),
                    Paragraph(_esc(subtitle), subtitle_style),
                ],
                [
                    Paragraph(_esc(stamp), meta_style),
                    Paragraph(_esc(meta_bits), meta_sub),
                ],
            ]
        ],
        colWidths=[doc.width * 0.62, doc.width * 0.38],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 1.25, line),
            ]
        )
    )

    story: list = [header, Spacer(1, 4 * mm)]

    def _col_count() -> int:
        if layout == "items":
            return 4
        if layout == "prices":
            return 2 if len(shops) <= 1 else 1 + len(shops)
        if layout == "stock":
            return (len(shops) + 1) if len(shops) > 1 else max(2, 1 + len(shops))
        return 2

    def _header_labels() -> list[str]:
        labels = ["Item"]
        if layout == "items":
            labels.extend(["Qty", "Notes", ""])
        elif layout == "prices":
            if len(shops) <= 1:
                labels.append("Price")
            else:
                labels.extend([shop.get("name") or "Shop" for shop in shops])
        elif layout == "stock":
            labels.extend([shop.get("name") or "Shop" for shop in shops])
            if len(shops) > 1:
                labels.append("Total")
        return labels

    def _row_values(row: dict) -> list:
        values = [Paragraph(_esc(row.get("name") or ""), cell_style)]
        if layout == "items":
            values.extend(["", "", ""])
        elif layout == "prices":
            if len(shops) <= 1:
                values.append(_esc(row.get("price") or "0.00"))
            else:
                for price in row.get("prices") or []:
                    values.append(_esc(price.get("price") or "0.00"))
        elif layout == "stock":
            for qty in row.get("quantities") or []:
                values.append(str(qty.get("qty") if qty.get("qty") is not None else 0))
            if len(shops) > 1:
                values.append(str(row.get("total") if row.get("total") is not None else 0))
        return values

    labels = _header_labels()
    ncols = max(len(labels), _col_count())
    usable = doc.width

    if layout == "items":
        col_widths = [
            usable * 0.42,
            usable * 0.12,
            usable * 0.23,
            usable * 0.23,
        ]
    elif ncols <= 2:
        col_widths = [usable * 0.62, usable * 0.38]
    else:
        item_w = usable * (0.28 if ncols > 4 else 0.36)
        rest = (usable - item_w) / max(1, ncols - 1)
        col_widths = [item_w] + [rest] * (ncols - 1)

    categories = document.get("categories") or []
    if not categories:
        story.append(Paragraph("No items to print.", foot_style))
    else:
        for group in categories:
            cat_name = _esc(str(group.get("name") or "Uncategorised").upper())
            cat_row = [Paragraph(cat_name, head_cell)] + [""] * (ncols - 1)
            head_row = []
            for label in labels[:ncols]:
                head_row.append(Paragraph(_esc(label), head_cell) if label else "")
            while len(head_row) < ncols:
                head_row.append("")

            data = [cat_row, head_row]
            for row in group.get("rows") or []:
                values = _row_values(row)
                while len(values) < ncols:
                    values.append("")
                data.append(values[:ncols])

            table = Table(data, colWidths=col_widths[:ncols], repeatRows=2)
            style_cmds = [
                ("FONTNAME", (0, 0), (-1, -1), MANROPE_PDF),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), ink),
                ("GRID", (0, 0), (-1, -1), 0.4, line_soft),
                ("BOX", (0, 0), (-1, -1), 0.8, line),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2.2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
                ("BACKGROUND", (0, 0), (-1, 0), category_fill),
                ("SPAN", (0, 0), (-1, 0)),
                ("BACKGROUND", (0, 1), (-1, 1), head_fill),
                ("FONTNAME", (0, 1), (-1, 1), MANROPE_PDF_BOLD),
                ("FONTSIZE", (0, 1), (-1, 1), 7.5),
                ("ALIGN", (1, 1), (-1, 1), "CENTER"),
                ("ALIGN", (1, 2), (-1, -1), "RIGHT"),
                ("LINEBEFORE", (1, 0), (1, -1), 0.7, line),
            ]
            for r in range(2, len(data)):
                if (r - 2) % 2 == 1:
                    style_cmds.append(("BACKGROUND", (0, r), (-1, r), row_alt))
            if layout == "items":
                # Blank write-in columns stay left-aligned / empty.
                style_cmds.append(("ALIGN", (1, 2), (-1, -1), "LEFT"))

            table.setStyle(TableStyle(style_cmds))
            story.append(table)
            story.append(Spacer(1, 2.2 * mm))

    foot = Table(
        [
            [
                Paragraph(
                    _esc(
                        f"Generated from Stock Management · {document.get('title') or 'Stock list'}"
                    ),
                    foot_style,
                ),
                Paragraph(
                    _esc(company_name or "MY-SHOP"),
                    ParagraphStyle(
                        "StockFootRight",
                        parent=foot_style,
                        alignment=TA_RIGHT,
                    ),
                ),
            ]
        ],
        colWidths=[doc.width * 0.7, doc.width * 0.3],
    )
    foot.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, -1), 0.5, line_soft),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(foot)

    doc.build(story)
    return buffer.getvalue()
