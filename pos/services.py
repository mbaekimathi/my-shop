from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from employees.models import EmployeeProfile

from .models import Product, Sale, SaleLine, SaleSource


class SaleValidationError(Exception):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code
        self.message = message


def _parse_decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        raise SaleValidationError(f"Invalid {field}.", "invalid_field")


def create_sale_from_payload(
    employee: EmployeeProfile,
    payload: dict,
    *,
    source: str = SaleSource.ONLINE,
) -> Sale:
    client_id = (payload.get("client_id") or "").strip()
    if not client_id:
        raise SaleValidationError("client_id is required.", "missing_client_id")

    existing = Sale.objects.filter(client_id=client_id).first()
    if existing:
        return existing

    lines = payload.get("lines") or []
    if not lines:
        raise SaleValidationError("At least one line item is required.", "empty_lines")

    sold_at = parse_datetime(payload.get("sold_at") or "")
    if sold_at is None:
        raise SaleValidationError("sold_at is required (ISO datetime).", "invalid_sold_at")

    draft_lines = []
    for index, line in enumerate(lines):
        sku = (line.get("product_sku") or "").strip()
        if not sku:
            raise SaleValidationError(f"Line {index + 1}: product_sku required.", "invalid_line")
        quantity = int(line.get("quantity") or 0)
        if quantity <= 0:
            raise SaleValidationError(f"Line {index + 1}: invalid quantity.", "invalid_line")

        unit_price = _parse_decimal(line.get("unit_price"), "unit_price")
        line_total = _parse_decimal(line.get("line_total"), "line_total")
        product_name = (line.get("product_name") or sku).strip()
        draft_lines.append(
            {
                "sku": sku,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "product_name": product_name,
            }
        )

    skus = [draft["sku"] for draft in draft_lines]
    total_hint = payload.get("total")

    with transaction.atomic():
        # Re-check idempotency under the transaction.
        existing = Sale.objects.filter(client_id=client_id).first()
        if existing:
            return existing

        products_by_sku = {
            product.sku: product
            for product in Product.objects.select_for_update().filter(
                sku__in=skus, is_active=True
            )
        }

        parsed_lines = []
        computed_total = Decimal("0")
        for draft in draft_lines:
            product = products_by_sku.get(draft["sku"])
            if product is None:
                raise SaleValidationError(
                    f"Product {draft['sku']} not found.", "product_not_found"
                )
            if product.stock < draft["quantity"]:
                raise SaleValidationError(
                    f"Insufficient stock for {draft['sku']}.", "insufficient_stock"
                )
            computed_total += draft["line_total"]
            parsed_lines.append(
                {
                    "product": product,
                    "product_sku": draft["sku"],
                    "product_name": draft["product_name"] or product.name,
                    "quantity": draft["quantity"],
                    "unit_price": draft["unit_price"],
                    "line_total": draft["line_total"],
                }
            )

        total = (
            _parse_decimal(total_hint, "total")
            if total_hint is not None
            else computed_total
        )

        sale = Sale.objects.create(
            client_id=client_id,
            employee=employee,
            total=total,
            source=source,
            sold_at=sold_at,
        )
        products_to_update = []
        now = timezone.now()
        for row in parsed_lines:
            product = row["product"]
            product.stock -= row["quantity"]
            product.updated_at = now
            products_to_update.append(product)
        if products_to_update:
            Product.objects.bulk_update(products_to_update, ["stock", "updated_at"])
        SaleLine.objects.bulk_create(
            [
                SaleLine(
                    sale=sale,
                    product=row["product"],
                    product_sku=row["product_sku"],
                    product_name=row["product_name"],
                    quantity=row["quantity"],
                    unit_price=row["unit_price"],
                    line_total=row["line_total"],
                )
                for row in parsed_lines
            ]
        )

    return sale
