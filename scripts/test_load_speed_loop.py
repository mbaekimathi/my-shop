"""
Page / submit / fetch load-speed correction loops for MY-SHOP.

Measures the hot paths that make the system feel slow:
  - catalog build (page-to-page floor load)
  - last-buying price lookup (stock-in page)
  - POS settings fetch (cached)
  - checkout submit (batched queries)
  - product list fetch
  - POS sale submit

Fails when medians exceed budgets so regressions are caught early.

Usage:
  python scripts/test_load_speed_loop.py
  python scripts/test_load_speed_loop.py --iterations 30
  python scripts/test_load_speed_loop.py --continuous --interval 15
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Soft budgets (seconds) on a healthy local MySQL/SQLite
BUDGET_CATALOG = 0.25
BUDGET_LAST_BUYING = 0.20
BUDGET_POS_SETTINGS = 0.05
BUDGET_CHECKOUT = 0.45
BUDGET_PRODUCT_LIST = 0.15
BUDGET_POS_SALE = 0.35
BUDGET_PAGE_HOP = 0.40

FIX_HINTS = {
    "catalog build (50 items)": (
        "Catalog builder should batch stock/prices and defer description. "
        "Check shops.views._catalog_items_by_category."
    ),
    "last buying prices (50 items)": (
        "Use items.services.last_buying_prices_for_items (subquery), "
        "not a full StockMovementLine history scan."
    ),
    "POS settings get (cached)": (
        "get_company_pos_settings should hit cache after the first load."
    ),
    "checkout submit (5 lines)": (
        "complete_shop_checkout must batch Item/ShopStock/price loads and "
        "bulk_create/bulk_update writes — no per-line get_or_create."
    ),
    "product list fetch": (
        "product_list_api should not call .count() after iterating the queryset."
    ),
    "POS sale submit (3 lines)": (
        "create_sale_from_payload should lock products once and bulk_update stock."
    ),
    "page-hop catalog+settings": (
        "Workspace path should reuse cached POS settings and a lean catalog query."
    ),
}


def django_ready() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
    import django

    django.setup()


def ensure_migrations() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "manage.py"), "migrate", "--noinput"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit("migrate failed — cannot run load-speed loops.")


def timed(fn, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return samples


def report(label: str, samples: list[float], budget: float) -> bool:
    if not samples:
        print(f"[SKIP] {label}")
        return True
    med = statistics.median(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    ok = med <= budget
    status = "PASS" if ok else "FAIL"
    print(
        f"[{status}] {label}: median={med*1000:.2f}ms  "
        f"p95={p95*1000:.2f}ms  budget={budget*1000:.0f}ms  n={len(samples)}"
    )
    if not ok:
        hint = FIX_HINTS.get(label)
        if hint:
            print(f"       -> correct: {hint}")
    return ok


def seed_fixture():
    from django.contrib.auth.models import User
    from django.contrib.auth.hashers import make_password

    from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
    from items.models import Item, ShopStock, StockMovement, StockMovementLine, StockMovementType
    from pos.models import Product
    from shops.models import Shop
    from shops.services import _invalidate_pos_settings_cache, get_company_pos_settings

    suffix = uuid.uuid4().hex[:8]
    password = make_password("loop-load-pass")

    user, _ = User.objects.get_or_create(
        username="810001",
        defaults={
            "email": "load_loop@test.local",
            "is_active": True,
            "first_name": "Load",
            "last_name": "Loop",
        },
    )
    if not user.has_usable_password():
        user.password = password
        user.save(update_fields=["password"])

    profile, _ = EmployeeProfile.objects.get_or_create(
        user=user,
        defaults={
            "employee_id": "810001",
            "phone_country_code": "+254",
            "phone_number": "711000001",
            "status": EmployeeStatus.ACTIVE,
            "role": EmployeeRole.SHOP_MANAGER,
        },
    )
    profile.status = EmployeeStatus.ACTIVE
    profile.role = EmployeeRole.SHOP_MANAGER
    profile.save(update_fields=["status", "role", "updated_at"])

    shop, _ = Shop.objects.get_or_create(
        login_code="819001",
        defaults={
            "name": f"LOAD LOOP SHOP {suffix[:4]}",
            "location": "NAIROBI",
            "email": "load_loop@test.local",
            "phone_number": "0711000001",
            "password_hash": password,
            "created_by": profile,
        },
    )

    items = []
    for i in range(50):
        item, created = Item.objects.get_or_create(
            name=f"Load Loop Item {i:03d}",
            category="LOOP",
            defaults={
                "description": "x" * 200,
                "minimum_selling_price": Decimal("50.00"),
                "shop_price": Decimal("100.00"),
                "stock": 100,
                "created_by": profile,
            },
        )
        if created or item.stock < 100:
            item.stock = 100
            item.save(update_fields=["stock", "updated_at"])
        ShopStock.objects.update_or_create(
            shop=shop, item=item, defaults={"quantity": 100}
        )
        items.append(item)

    # Seed a few stock-in rows so last-buying lookups have data.
    if not StockMovementLine.objects.filter(
        item_id__in=[item.pk for item in items[:5]],
        buying_price__isnull=False,
    ).exists():
        movement = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            shop=shop,
            created_by=profile,
            notes="load-speed seed",
        )
        StockMovementLine.objects.bulk_create(
            [
                StockMovementLine(
                    movement=movement,
                    item=item,
                    quantity=10,
                    buying_price=Decimal("80.00"),
                )
                for item in items[:20]
            ]
        )

    products = []
    for i in range(10):
        sku = f"LOOP-{suffix}-{i:02d}"
        product, _ = Product.objects.get_or_create(
            sku=sku,
            defaults={
                "name": f"Loop Product {i}",
                "price": Decimal("150.00"),
                "stock": 500,
                "is_active": True,
            },
        )
        product.stock = 500
        product.is_active = True
        product.save(update_fields=["stock", "is_active", "updated_at"])
        products.append(product)

    _invalidate_pos_settings_cache()
    get_company_pos_settings()

    return {
        "profile": profile,
        "shop": shop,
        "items": items,
        "products": products,
        "suffix": suffix,
    }


def cleanup_fixture(fixture: dict) -> None:
    """Remove load-speed loop seed data so it never lingers in the real DB."""
    from django.contrib.auth.models import User
    from django.db import transaction

    from employees.models import EmployeeProfile
    from items.models import Item, ShopStock, StockMovement, StockMovementLine
    from pos.models import Product, Sale, SaleLine
    from shops.models import Shop, ShopReceipt, ShopReceiptLine

    shop = fixture.get("shop")
    profile = fixture.get("profile")
    items = fixture.get("items") or []
    products = fixture.get("products") or []
    item_ids = [item.pk for item in items if getattr(item, "pk", None)]
    product_ids = [product.pk for product in products if getattr(product, "pk", None)]
    shop_id = getattr(shop, "pk", None)
    profile_id = getattr(profile, "pk", None)
    user_id = getattr(getattr(profile, "user", None), "pk", None)

    with transaction.atomic():
        receipt_qs = ShopReceipt.objects.none()
        if shop_id:
            receipt_qs = receipt_qs | ShopReceipt.objects.filter(shop_id=shop_id)
        receipt_qs = (
            receipt_qs
            | ShopReceipt.objects.filter(client_name__icontains="LOAD LOOP")
        ).distinct()
        receipt_ids = list(receipt_qs.values_list("id", flat=True))
        ShopReceiptLine.objects.filter(receipt_id__in=receipt_ids).delete()
        receipt_qs.delete()

        if profile_id:
            sale_ids = list(
                Sale.objects.filter(employee_id=profile_id).values_list("id", flat=True)
            )
            SaleLine.objects.filter(sale_id__in=sale_ids).delete()
            Sale.objects.filter(id__in=sale_ids).delete()

        mov_qs = StockMovement.objects.filter(notes="load-speed seed")
        if shop_id:
            mov_qs = mov_qs | StockMovement.objects.filter(shop_id=shop_id)
        mov_ids = list(mov_qs.distinct().values_list("id", flat=True))
        StockMovementLine.objects.filter(movement_id__in=mov_ids).delete()
        if item_ids:
            StockMovementLine.objects.filter(item_id__in=item_ids).delete()
        StockMovement.objects.filter(id__in=mov_ids).delete()

        if shop_id:
            ShopStock.objects.filter(shop_id=shop_id).delete()
        if item_ids:
            ShopStock.objects.filter(item_id__in=item_ids).delete()
            Item.objects.filter(id__in=item_ids).delete()
        Item.objects.filter(category="LOOP", name__startswith="Load Loop").delete()

        if product_ids:
            Product.objects.filter(id__in=product_ids).delete()
        Product.objects.filter(sku__startswith="LOOP-").delete()

        if profile is not None:
            profile.assigned_shops.clear()
        if shop_id:
            Shop.objects.filter(id=shop_id).delete()
        Shop.objects.filter(login_code="819001").delete()
        Shop.objects.filter(name__startswith="LOAD LOOP SHOP").delete()

        if profile_id:
            EmployeeProfile.objects.filter(id=profile_id).delete()
        EmployeeProfile.objects.filter(employee_id="810001").delete()
        if user_id:
            User.objects.filter(id=user_id).delete()
        User.objects.filter(username="810001").delete()


def loop_catalog(shop, iterations: int) -> list[float]:
    from shops.views import _catalog_items_by_category

    def once():
        rows, count = _catalog_items_by_category(shop)
        assert count >= 1
        assert rows

    return timed(once, iterations)


def loop_last_buying(items, shop, iterations: int) -> list[float]:
    from items.services import last_buying_prices_for_items

    ids = [item.pk for item in items]

    def once():
        last_buying_prices_for_items(ids, prefer_shop_id=shop.pk)

    return timed(once, iterations)


def loop_pos_settings(iterations: int) -> list[float]:
    from shops.services import get_company_pos_settings

    # Warm cache once outside the timed loop.
    get_company_pos_settings()

    def once():
        get_company_pos_settings()

    return timed(once, iterations)


def loop_checkout(shop, profile, items, iterations: int) -> list[float]:
    from shops.services import complete_shop_checkout

    cart_items = items[:5]

    def once():
        payload = {
            "kind": "quotation",
            "login_code": profile.employee_id,
            "client_name": "LOAD LOOP CLIENT",
            "client_phone": "0711000001",
            "lines": [
                {
                    "id": item.pk,
                    "qty": 1,
                    "price": str(item.shop_price),
                }
                for item in cart_items
            ],
        }
        complete_shop_checkout(shop=shop, profile=profile, payload=payload)

    return timed(once, iterations)


def loop_product_list(iterations: int) -> list[float]:
    from pos.models import Product

    def once():
        products = list(Product.objects.filter(is_active=True).order_by("name"))
        _ = len(products)

    return timed(once, iterations)


def loop_pos_sale(profile, products, iterations: int) -> list[float]:
    from django.utils import timezone

    from pos.services import create_sale_from_payload

    sale_products = products[:3]

    def once():
        client_id = f"ld{uuid.uuid4().hex[:14]}"
        lines = []
        total = Decimal("0.00")
        for product in sale_products:
            qty = 1
            line_total = (product.price * qty).quantize(Decimal("0.01"))
            total += line_total
            lines.append(
                {
                    "product_sku": product.sku,
                    "product_name": product.name,
                    "quantity": qty,
                    "unit_price": str(product.price),
                    "line_total": str(line_total),
                }
            )
        create_sale_from_payload(
            profile,
            {
                "client_id": client_id,
                "sold_at": timezone.now().isoformat(),
                "total": str(total),
                "lines": lines,
            },
        )

    return timed(once, max(5, iterations // 2))


def loop_page_hop(shop, iterations: int) -> list[float]:
    from shops.services import get_company_pos_settings, pos_settings_as_dict
    from shops.views import _catalog_items_by_category

    def once():
        _catalog_items_by_category(shop)
        pos_settings_as_dict(get_company_pos_settings())

    return timed(once, iterations)


def run_loop(iterations: int) -> tuple[int, int]:
    fixture = seed_fixture()
    try:
        shop = fixture["shop"]
        profile = fixture["profile"]
        items = fixture["items"]
        products = fixture["products"]

        results = [
            report(
                "catalog build (50 items)",
                loop_catalog(shop, iterations),
                BUDGET_CATALOG,
            ),
            report(
                "last buying prices (50 items)",
                loop_last_buying(items, shop, iterations),
                BUDGET_LAST_BUYING,
            ),
            report(
                "POS settings get (cached)",
                loop_pos_settings(iterations),
                BUDGET_POS_SETTINGS,
            ),
            report(
                "checkout submit (5 lines)",
                loop_checkout(shop, profile, items, max(5, iterations // 2)),
                BUDGET_CHECKOUT,
            ),
            report(
                "product list fetch",
                loop_product_list(iterations),
                BUDGET_PRODUCT_LIST,
            ),
            report(
                "POS sale submit (3 lines)",
                loop_pos_sale(profile, products, iterations),
                BUDGET_POS_SALE,
            ),
            report(
                "page-hop catalog+settings",
                loop_page_hop(shop, iterations),
                BUDGET_PAGE_HOP,
            ),
        ]
        passed = sum(1 for ok in results if ok)
        failed = len(results) - passed
        return passed, failed
    finally:
        cleanup_fixture(fixture)


def main() -> int:
    parser = argparse.ArgumentParser(description="MY-SHOP load-speed correction loops")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()

    print("=== MY-SHOP load-speed correction loops ===\n")
    django_ready()
    ensure_migrations()

    if args.continuous:
        round_no = 0
        while True:
            round_no += 1
            print(f"--- round {round_no} ---")
            passed, failed = run_loop(args.iterations)
            print(f"Summary: {passed} passed, {failed} failed\n")
            time.sleep(max(1.0, args.interval))
    else:
        passed, failed = run_loop(args.iterations)
        print(f"\n=== Summary: {passed} passed, {failed} failed ===")
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
