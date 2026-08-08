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


def last_buying_prices_for_items(item_ids, *, prefer_shop_id=None) -> dict:
    """
    Latest stock-in buying price per item (one subquery each), avoiding full
    history scans. Prefer prices from prefer_shop_id when provided, then fall
    back to any shop.
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

    result = {}
    remaining = set(ids)

    if prefer_shop_id is not None:
        for item_id, price in (
            Item.objects.filter(pk__in=remaining)
            .annotate(last_buy=Subquery(_latest_subquery(shop_id=prefer_shop_id)))
            .filter(last_buy__isnull=False)
            .values_list("pk", "last_buy")
        ):
            result[item_id] = price
            remaining.discard(item_id)

    if remaining:
        for item_id, price in (
            Item.objects.filter(pk__in=remaining)
            .annotate(last_buy=Subquery(_latest_subquery()))
            .filter(last_buy__isnull=False)
            .values_list("pk", "last_buy")
        ):
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

    view_shop_ids = []
    if mode == "view":
        if shop_id:
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
    if item_ids and shop_id and mode != "view":
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
    if mode == "view" and view_shop_ids:
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
        if mode == "view":
            quantities = [
                int(multi_qty_map.get(item.pk, {}).get(sid, 0)) for sid in view_shop_ids
            ]
            row["shop_quantities"] = quantities
            row["row_total"] = sum(quantities)
            if len(view_shop_ids) == 1:
                row["shop_qty"] = quantities[0] if quantities else 0
        rows.append(row)

    total_units = 0
    if shop_id:
        total_units = (
            ShopStock.objects.filter(shop_id=shop_id).aggregate(total=Sum("quantity"))[
                "total"
            ]
            or 0
        )
    elif mode == "view" and view_shop_ids:
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
        "show_all_shops": mode == "view" and len(view_shop_ids) > 1,
    }


def build_item_management_catalog_page(*, q: str = "", page=1, page_size=48):
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
    ).order_by("category", "name")

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
        shop_prices = {
            str(shop_id): f"{price:.2f}" for shop_id, price in item_shop_map.items()
        }
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
    }


def search_available_serials(
    *,
    item_id,
    shop_id,
    query: str = "",
    exclude=None,
    limit: int = 10,
):
    try:
        item_pk = int(item_id)
        shop_pk = int(shop_id)
    except (TypeError, ValueError):
        return []

    qs = ItemSerial.objects.filter(
        item_id=item_pk,
        shop_id=shop_pk,
        is_available=True,
    ).order_by("serial_number")

    exclude = [str(value or "").strip().upper() for value in (exclude or []) if value]
    if exclude:
        qs = qs.exclude(serial_number__in=exclude)

    query = (query or "").strip().upper()
    if query:
        qs = qs.filter(serial_number__icontains=query)

    return list(qs.values_list("serial_number", flat=True)[:limit])


def search_suppliers(*, query: str, by: str = "name", dial: str = "", limit: int = 8):
    query = (query or "").strip().upper()
    if len(query) < 2:
        return []

    qs = Supplier.objects.all()
    by = (by or "name").strip().lower()
    if by == "phone":
        digits = _normalize_national_phone(query, dial)
        if len(digits) < 3:
            return []
        dial = (dial or "").strip()
        if dial:
            qs = qs.filter(phone_country_code=dial)
        matches = []
        for supplier in qs.order_by("name", "phone_number")[:80]:
            if digits in _normalize_phone_digits(supplier.phone_number):
                matches.append(supplier)
            if len(matches) >= limit:
                break
        return matches

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


def _active_shops():
    from shops.models import Shop

    return list(Shop.objects.filter(is_hidden=False, is_suspended=False).order_by("name"))


def _pricing_mode_from_data(data) -> str:
    mode = (data.get("pricing_mode") or "single").strip().lower()
    if mode not in ("single", "individual"):
        return "single"
    return mode


def _parse_individual_shop_prices(data, shops, minimum_price, maximum_price):
    """Parse per-shop prices from POST fields shop_price_<id>.

    Blank shop prices default to the maximum selling price.
    """
    errors = []
    prices_by_shop = {}

    if not shops:
        errors.append("No active shops are available to set individual prices.")
        return prices_by_shop, errors

    for shop in shops:
        raw = data.get(f"shop_price_{shop.pk}")
        if raw is None or str(raw).strip() == "":
            if maximum_price is None:
                errors.append(
                    f"Enter a shop price for “{shop.name}”, or set a maximum selling price."
                )
                continue
            prices_by_shop[shop.pk] = maximum_price
            continue
        try:
            price = _parse_price(str(raw), f"Shop price for {shop.name}")
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


def validate_item_payload(data, files, *, existing_item=None) -> dict:
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
            data, shops, minimum_price, maximum_price
        )
        errors.extend(price_errors)
        if shop_prices:
            shop_price = min(shop_prices.values())
    else:
        raw_shop_price = data.get("shop_price")
        if raw_shop_price is None or str(raw_shop_price).strip() == "":
            if maximum_price is not None:
                shop_price = maximum_price
            else:
                errors.append("Shop price is required.")
        else:
            try:
                shop_price = _parse_price(raw_shop_price, "Shop price")
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
            errors.append("Shop price must be between the minimum and maximum selling prices.")

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


def update_item(item: Item, data, files) -> Item:
    cleaned = validate_item_payload(data, files, existing_item=item)
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
        else:
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

    if not raw_ids:
        raise ValidationError("Enter quantity on at least one item.")

    track_by_id = {
        str(pk): track
        for pk, track in Item.objects.filter(pk__in=raw_ids).values_list(
            "pk", "track_serial_number"
        )
    }

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
            if payment_status not in {choice.value for choice in StockPaymentStatus}:
                errors.append(
                    f"{line_label}: choose a payment status (unpaid, paid, or partial)."
                )
                continue

            price_raw = raw_prices[index] if index < len(raw_prices) else ""
            try:
                buying_price = _parse_price(price_raw, "Buying price")
            except ValidationError as exc:
                message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                errors.append(f"{line_label}: {message}")
                continue

            supplier, supplier_error = _validate_supplier(
                name=raw_supplier_names[index] if index < len(raw_supplier_names) else "",
                dial=raw_supplier_dials[index] if index < len(raw_supplier_dials) else "",
                phone=raw_supplier_phones[index] if index < len(raw_supplier_phones) else "",
                line_label=line_label,
            )
            if supplier_error:
                errors.append(supplier_error)
                continue

        if movement_type == StockMovementType.OUT:
            reason = (raw_reasons[index] if index < len(raw_reasons) else "").strip().lower()
            if reason not in {choice.value for choice in StockOutReason}:
                errors.append(
                    f"{line_label}: choose a reason (waste, transfer, display, or return)."
                )
                continue
            refund = (raw_refunds[index] if index < len(raw_refunds) else "").strip().lower()
            if refund not in ("yes", "no"):
                errors.append(f"{line_label}: choose whether a refund applies (yes or no).")
                continue
            refund_amount = None
            if refund == "yes":
                amount_raw = (
                    raw_refund_amounts[index] if index < len(raw_refund_amounts) else ""
                )
                try:
                    refund_amount = _parse_price(amount_raw, "Refund amount")
                except ValidationError as exc:
                    message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    errors.append(f"{line_label}: {message}")
                    continue
                if refund_amount <= 0:
                    errors.append(f"{line_label}: refund amount must be greater than zero.")
                    continue

        line = {
            "item_id": item_id,
            "quantity": quantity,
            "note": note,
            "serial_numbers": serials,
            "track_serial_number": tracks_serial,
        }
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

    if movement_type == StockMovementType.REQUEST:
        shop = _get_active_shop(data.get("shop_id"), label="requesting shop")
        _assert_shop_allowed(profile, shop, label="requesting shop")
        requested_from = _get_active_shop(
            data.get("requested_from_shop_id"),
            label="shop to request from",
        )
        if str(shop.pk) == str(requested_from.pk):
            raise ValidationError("Requesting shop and requested shop must be different.")
    else:
        shop = _get_active_shop(data.get("shop_id"), label="shop")
        _assert_shop_allowed(profile, shop)
        requested_from = None
        if shop.is_suspended:
            raise ValidationError(f"Shop “{shop.name}” is suspended.")

    line_payloads = _parse_movement_lines(data, movement_type)

    with transaction.atomic():
        items = {
            str(item.pk): item
            for item in Item.objects.select_for_update().filter(
                pk__in=[line["item_id"] for line in line_payloads]
            )
        }

        prepared = []
        errors = []
        for line in line_payloads:
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
                    errors.append(f"“{item.name}” requires serial numbers.")
                    continue
                if len(serials) != line["quantity"]:
                    errors.append(f"“{item.name}”: quantity must match serial count.")
                    continue

                if movement_type == StockMovementType.IN:
                    existing_rows = list(
                        ItemSerial.objects.select_for_update().filter(
                            item=item, serial_number__in=serials
                        )
                    )
                    available_dupes = [
                        row.serial_number for row in existing_rows if row.is_available
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

        movement = StockMovement.objects.create(
            movement_type=movement_type,
            shop=shop,
            requested_from_shop=requested_from,
            request_status=(
                StockRequestStatus.PENDING
                if movement_type == StockMovementType.REQUEST
                else ""
            ),
            created_by=profile,
        )

        paid_total = Decimal("0.00")
        for item, line in prepared:
            StockMovementLine.objects.create(
                movement=movement,
                item=item,
                quantity=line["quantity"],
                buying_price=line.get("buying_price"),
                payment_status=line.get("payment_status", ""),
                reason=line.get("reason", ""),
                refund=line.get("refund", ""),
                refund_amount=line.get("refund_amount"),
                note=line.get("note", ""),
                serial_numbers=line.get("serial_numbers") or [],
                supplier_name=line.get("supplier_name", ""),
                supplier_phone_country_code=line.get("supplier_phone_country_code", ""),
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
                                update_fields=["is_available", "shop", "updated_at"]
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
                shop_stock.quantity += line["quantity"]
                shop_stock.save(update_fields=["quantity", "updated_at"])
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

            # REQUEST only records the ask — no stock change until fulfilled.

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

    return movement


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
        movement.responded_by = profile
        movement.responded_at = timezone.now()
        movement.save(
            update_fields=[
                "request_status",
                "requester_notified",
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

        supplier_stock.quantity -= qty
        supplier_stock.save(update_fields=["quantity", "updated_at"])
        requester_stock.quantity += qty
        requester_stock.save(update_fields=["quantity", "updated_at"])

    movement.request_status = StockRequestStatus.FULFILLED
    movement.requester_notified = False
    movement.responded_by = profile
    movement.responded_at = timezone.now()
    movement.save(
        update_fields=[
            "request_status",
            "requester_notified",
            "responded_by",
            "responded_at",
        ]
    )
    return movement
