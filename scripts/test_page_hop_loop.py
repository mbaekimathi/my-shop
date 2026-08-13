"""
HTTP page-hop + report + printer + size + concurrency correction loops.

Usage:
  python scripts/test_page_hop_loop.py
  python scripts/test_page_hop_loop.py --iterations 12
  python scripts/test_page_hop_loop.py --continuous --interval 20
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import statistics
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUDGET_LOGIN = 5.00  # PBKDF2 verify dominates on Windows; regression guard only
BUDGET_ROLE_HOME = 0.50
BUDGET_WORKSPACE = 0.80
BUDGET_BUY_STOCK = 0.80
BUDGET_PAGE_HOP_CHAIN = 2.00
BUDGET_STOCK_REPORT = 0.60
BUDGET_PRINTER_SCAN = 5.00
BUDGET_PRINTER_CACHE = 0.05
BUDGET_WORKSPACE_HTML = 250_000  # shell only — catalog loads via API
BUDGET_BUY_STOCK_HTML = 250_000  # shell only — stock catalog via API
BUDGET_CATALOG_API = 0.35
BUDGET_STOCK_CATALOG_API = 0.40
BUDGET_STOCK_CATALOG_SEARCH = 0.45
BUDGET_STOCK_MOVEMENT = 0.80
BUDGET_CONCURRENT_CHECKOUT = 1.20
BUDGET_PARALLEL_CATALOG = 1.80
BUDGET_STOCK_CONTENTION = 2.50
BUDGET_CONCURRENT_SALE = 2.50
BUDGET_CATALOG_STRESS = 0.70
BUDGET_MIXED_WORKLOAD = 2.50

FIX_HINTS = {
    "login POST": "Check auth queries and session write path.",
    "role home GET": "Role home should be a light profile render.",
    "workspace GET": "Workspace shell must not SSR the full catalog.",
    "buy-stock GET": "Buy-stock shell must not SSR the full item matrix.",
    "page-hop chain": "Login → home → workspace should stay under budget.",
    "stock report build": "Use ShopReceiptLine by item_id; bound POS SaleLine by name.",
    "printer scan (uncached)": "ARP-first probe + short deadlines in printer_discovery.",
    "printer scan (cached)": "discover_lan_printers cache should hit within 20s.",
    "workspace HTML size": "Catalog must load via /catalog/ API, not full SSR.",
    "buy-stock HTML size": "Stock matrix must load via /buy-stock/catalog/ API.",
    "catalog API page 1": "build_shop_catalog_page should batch stock/prices for one page.",
    "buy-stock catalog API": "build_stock_catalog_page should page items with shop qty.",
    "stock-mgmt catalog API": "Stock-management catalog must page in/out/request rows.",
    "stock catalog search": "Catalog ?q= must filter; empty q restores full total.",
    "stock catalog page 2": "Page 2 ids must not overlap page 1 when has_more.",
    "stock in/out smoke": "apply_stock_movement in then out must update ShopStock.",
    "concurrent checkout x4": "Checkout must batch locks/writes under contention.",
    "parallel catalog x8": "Catalog API must stay healthy under concurrent GETs.",
    "stock row contention": "Oversubscribed stock-out must reject extras; qty never negative.",
    "concurrent sale x8": "Parallel sales on one SKU must serialize locks correctly.",
    "catalog stress search": "page_size=96 + noisy q must stay under budget.",
    "mixed report+POS": "Report build must not wedge concurrent checkouts.",
    "gunicorn-mysql capacity": "workers×threads + reserve must fit under MySQL max_connections.",
}


def django_ready() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
    os.environ.setdefault("RATE_LIMIT_CHECK_ID_MAX", "100000")
    os.environ.setdefault("RATE_LIMIT_SYNC_MAX", "100000")
    os.environ.setdefault("RATE_LIMIT_POS_SALE_MAX", "100000")
    os.environ.setdefault("RATE_LIMIT_LOGIN_MAX", "100000")
    import django

    django.setup()
    from django.conf import settings

    # Re-apply after setup in case settings already read env defaults.
    settings.RATE_LIMITS["login"]["max"] = 100000
    hosts = list(settings.ALLOWED_HOSTS or [])
    for host in ("testserver", "localhost", "127.0.0.1"):
        if host not in hosts:
            hosts.append(host)
    settings.ALLOWED_HOSTS = hosts


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
        raise SystemExit("migrate failed — cannot run page-hop loops.")


def timed(fn, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return samples


def report(label: str, samples: list[float], budget: float, *, unit: str = "ms") -> bool:
    if not samples:
        print(f"[SKIP] {label}")
        return True
    med = statistics.median(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    ok = med <= budget
    if unit == "bytes":
        print(
            f"[{'PASS' if ok else 'FAIL'}] {label}: median={med:,.0f}B  "
            f"p95={p95:,.0f}B  budget={budget:,.0f}B  n={len(samples)}"
        )
    else:
        print(
            f"[{'PASS' if ok else 'FAIL'}] {label}: median={med*1000:.2f}ms  "
            f"p95={p95*1000:.2f}ms  budget={budget*1000:.0f}ms  n={len(samples)}"
        )
    if not ok:
        hint = FIX_HINTS.get(label)
        if hint:
            print(f"       -> correct: {hint}")
    return ok


def seed_fixture():
    from django.contrib.auth.hashers import make_password
    from django.contrib.auth.models import User
    from django.utils import timezone

    from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
    from items.models import Item, ShopStock
    from shops.models import Shop, ShopReceipt, ShopReceiptKind, ShopReceiptLine
    from shops.printer_discovery import clear_discovery_cache
    from shops.services import _invalidate_pos_settings_cache, get_company_pos_settings

    suffix = uuid.uuid4().hex[:6]
    password_hash = make_password("page-hop-pass")

    user, _ = User.objects.get_or_create(
        username="820001",
        defaults={
            "email": "pagehop@test.local",
            "is_active": True,
            "first_name": "Page",
            "last_name": "Hop",
        },
    )
    user.set_password("page-hop-pass")
    user.is_active = True
    user.save()

    profile, _ = EmployeeProfile.objects.get_or_create(
        user=user,
        defaults={
            "employee_id": "820001",
            "phone_country_code": "+254",
            "phone_number": "720000001",
            "status": EmployeeStatus.ACTIVE,
            "role": EmployeeRole.SHOP_MANAGER,
        },
    )
    profile.employee_id = "820001"
    profile.status = EmployeeStatus.ACTIVE
    profile.role = EmployeeRole.SHOP_MANAGER
    profile.save(update_fields=["employee_id", "status", "role", "updated_at"])

    shop, _ = Shop.objects.get_or_create(
        login_code="829001",
        defaults={
            "name": f"PAGE HOP SHOP {suffix}",
            "location": "NAIROBI",
            "email": "pagehop_shop@test.local",
            "phone_number": "0720000001",
            "password_hash": password_hash,
            "created_by": profile,
        },
    )
    profile.assigned_shops.add(shop)

    items = []
    for i in range(40):
        item, _ = Item.objects.get_or_create(
            name=f"Page Hop Item {i:03d}",
            category="PAGEHOP",
            defaults={
                "description": "y" * 120,
                "minimum_selling_price": Decimal("40.00"),
                "shop_price": Decimal("90.00"),
                "stock": 80,
                "created_by": profile,
            },
        )
        ShopStock.objects.update_or_create(
            shop=shop, item=item, defaults={"quantity": 80}
        )
        items.append(item)

    # Seed a few receipts so report loops have data.
    if not ShopReceipt.objects.filter(shop=shop, receipt_number__startswith="PH").exists():
        receipt = ShopReceipt.objects.create(
            shop=shop,
            receipt_number=f"PH{shop.pk}-{timezone.now().strftime('%y%m%d%H%M%S')}",
            kind=ShopReceiptKind.SALE,
            client_name="PAGE HOP CLIENT",
            client_phone="0720000001",
            subtotal=Decimal("90.00"),
            tax_percent=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total=Decimal("90.00"),
            cash_amount=Decimal("90.00"),
            created_by=profile,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=items[0],
            item_name=items[0].name,
            quantity=1,
            unit_price=Decimal("90.00"),
            line_total=Decimal("90.00"),
        )

    _invalidate_pos_settings_cache()
    get_company_pos_settings()
    clear_discovery_cache()

    return {"user": user, "profile": profile, "shop": shop, "items": items}


def cleanup_fixture(fixture: dict) -> None:
    """Remove page-hop loop seed data so it never stays in the live DB."""
    from django.contrib.auth.models import User
    from django.db import transaction

    from employees.models import EmployeeProfile
    from items.models import Item, ShopStock, StockMovement, StockMovementLine
    from shops.models import Shop, ShopReceipt, ShopReceiptLine

    shop = fixture.get("shop")
    profile = fixture.get("profile")
    user = fixture.get("user")
    items = fixture.get("items") or []
    item_ids = [item.pk for item in items if getattr(item, "pk", None)]
    shop_id = getattr(shop, "pk", None)
    profile_id = getattr(profile, "pk", None)
    user_id = getattr(user, "pk", None)

    with transaction.atomic():
        if shop_id:
            receipt_ids = list(
                ShopReceipt.objects.filter(shop_id=shop_id).values_list("id", flat=True)
            )
            ShopReceiptLine.objects.filter(receipt_id__in=receipt_ids).delete()
            ShopReceipt.objects.filter(id__in=receipt_ids).delete()
            ShopReceipt.objects.filter(client_name="PAGE HOP CLIENT").delete()
            ShopReceipt.objects.filter(client_name__startswith="CONCUR ").delete()
            ShopReceipt.objects.filter(client_name__startswith="SALEWAVE ").delete()
            ShopReceipt.objects.filter(client_name__startswith="MIXED ").delete()

            mov_ids = list(
                StockMovement.objects.filter(shop_id=shop_id).values_list("id", flat=True)
            )
            StockMovementLine.objects.filter(movement_id__in=mov_ids).delete()
            StockMovement.objects.filter(id__in=mov_ids).delete()
            ShopStock.objects.filter(shop_id=shop_id).delete()

        if item_ids:
            StockMovementLine.objects.filter(item_id__in=item_ids).delete()
            ShopStock.objects.filter(item_id__in=item_ids).delete()
            Item.objects.filter(id__in=item_ids).delete()
        Item.objects.filter(category="PAGEHOP", name__startswith="Page Hop").delete()

        if profile_id:
            try:
                profile.assigned_shops.clear()
            except Exception:
                pass
        if shop_id:
            Shop.objects.filter(id=shop_id).delete()
        Shop.objects.filter(login_code="829001").delete()
        Shop.objects.filter(name__startswith="PAGE HOP SHOP").delete()

        if profile_id:
            EmployeeProfile.objects.filter(id=profile_id).delete()
        EmployeeProfile.objects.filter(employee_id="820001").delete()
        if user_id:
            User.objects.filter(id=user_id).delete()
        User.objects.filter(username="820001").delete()


def _client_logged_in(user):
    from django.test import Client

    client = Client()
    client.force_login(user)
    return client


def loop_login(iterations: int) -> list[float]:
    from django.test import Client

    # Warm hasher/DB once outside the timed samples.
    warm = Client()
    warm.post("/employees/login/", {"username": "820001", "password": "page-hop-pass"})

    def once():
        client = Client()
        response = client.post(
            "/employees/login/",
            {"username": "820001", "password": "page-hop-pass"},
        )
        assert response.status_code in (200, 302), response.status_code

    return timed(once, iterations)


def loop_role_home(user, iterations: int) -> list[float]:
    client = _client_logged_in(user)

    def once():
        response = client.get("/shop-manager/")
        assert response.status_code == 200

    return timed(once, iterations)


def loop_workspace(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()

    def once():
        response = client.get(f"/my-shop/{shop.pk}/")
        assert response.status_code == 200

    return timed(once, iterations)


def loop_buy_stock(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()

    def once():
        response = client.get(f"/my-shop/{shop.pk}/buy-stock/")
        assert response.status_code == 200

    return timed(once, iterations)


def loop_page_hop_chain(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    def once():
        client = _client_logged_in(user)
        home = client.get("/shop-manager/")
        assert home.status_code == 200
        session = client.session
        session[SESSION_SHOP_KEY] = str(shop.pk)
        session.save()
        workspace = client.get(f"/my-shop/{shop.pk}/")
        assert workspace.status_code == 200
        buy = client.get(f"/my-shop/{shop.pk}/buy-stock/")
        assert buy.status_code == 200

    return timed(once, iterations)


def loop_stock_report(shop, items, iterations: int) -> list[float]:
    from datetime import timedelta

    from django.utils import timezone

    from items.views import _build_item_report_rows, _build_movement_timeline

    day_end = timezone.now() + timedelta(days=1)
    day_start = day_end - timedelta(days=7)

    def once():
        _build_item_report_rows(items, [shop.pk], day_start, day_end)
        _build_movement_timeline(
            shop_ids=[shop.pk],
            day_start=day_start,
            day_end=day_end,
            item_mode="all",
            selected_categories=[],
            selected_item_ids=[],
            report_items=items,
        )

    return timed(once, iterations)


def loop_printer_scan(iterations: int, *, use_cache: bool) -> list[float]:
    from shops.printer_discovery import clear_discovery_cache, discover_lan_printers
    from unittest import mock
    import ipaddress

    clear_discovery_cache()

    def once():
        with mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["192.168.88.237"],
        ):
            with mock.patch(
                "shops.printer_discovery._networks_from_local_ips",
                return_value=[ipaddress.ip_network("192.168.88.0/24")],
            ):
                with mock.patch(
                    "shops.printer_discovery._arp_live_hosts", return_value=set()
                ):
                    with mock.patch(
                        "shops.printer_discovery._ping_sweep", return_value=set()
                    ):
                        with mock.patch(
                            "shops.printer_discovery._probe_host", return_value=None
                        ):
                            with mock.patch(
                                "shops.printer_discovery._windows_tcp_printers",
                                return_value=[],
                            ):
                                with mock.patch(
                                    "shops.printer_discovery._windows_pos_usb_printers",
                                    return_value=[],
                                ):
                                    result = discover_lan_printers(
                                        thorough=False, use_cache=use_cache
                                    )
                                    assert result["ok"]

    # Warm cache for cached loop.
    if use_cache:
        once()
    return timed(once, iterations)


def loop_workspace_html_size(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    sizes = []
    for _ in range(iterations):
        client = _client_logged_in(user)
        session = client.session
        session[SESSION_SHOP_KEY] = str(shop.pk)
        session.save()
        response = client.get(f"/my-shop/{shop.pk}/")
        assert response.status_code == 200
        html = response.content.decode("utf-8", errors="ignore")
        assert 'data-catalog-api="' in html
        assert "data-catalog-root" in html
        # Shell must not SSR catalog product cards.
        assert 'data-cart-item="' not in html
        assert "data-item-row" not in html
        sizes.append(float(len(response.content)))
    return sizes


def loop_catalog_api(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()

    def once():
        response = client.get(f"/my-shop/{shop.pk}/catalog/?page=1&page_size=48")
        assert response.status_code == 200
        payload = response.json()
        assert payload.get("ok") is True
        assert "items" in payload
        assert payload.get("page") == 1

    return timed(once, iterations)


def loop_buy_stock_html_size(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    sizes = []
    for _ in range(iterations):
        client = _client_logged_in(user)
        session = client.session
        session[SESSION_SHOP_KEY] = str(shop.pk)
        session.save()
        response = client.get(f"/my-shop/{shop.pk}/buy-stock/")
        assert response.status_code == 200
        html = response.content.decode("utf-8", errors="ignore")
        assert 'data-stock-catalog-api="' in html
        assert "data-stock-catalog-root" in html
        assert "data-item-row" not in html
        sizes.append(float(len(response.content)))
    return sizes


def loop_buy_stock_catalog_api(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()

    def once():
        response = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/?page=1&page_size=48&mode=in"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload.get("ok") is True
        assert "items" in payload
        assert payload.get("page") == 1
        assert payload.get("mode") == "in"

    return timed(once, iterations)


def loop_stock_mgmt_catalog_api(user, shop, iterations: int) -> list[float]:
    client = _client_logged_in(user)

    def once():
        response = client.get(
            "/shop-manager/stock-management/catalog/"
            f"?mode=in&shop_id={shop.pk}&page=1&page_size=48"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload.get("ok") is True
        assert "items" in payload
        assert payload.get("page") == 1

    return timed(once, iterations)


def loop_stock_catalog_search(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()

    def once():
        all_resp = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/?page=1&page_size=48"
        )
        assert all_resp.status_code == 200
        all_payload = all_resp.json()
        assert all_payload.get("ok") is True
        total = int(all_payload.get("total") or 0)
        assert total >= 1

        needle = "Page Hop Item 001"
        filtered = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/"
            f"?page=1&page_size=48&q={needle.replace(' ', '+')}"
        )
        assert filtered.status_code == 200
        payload = filtered.json()
        assert payload.get("ok") is True
        assert int(payload.get("total") or 0) >= 1
        assert int(payload.get("total") or 0) <= total
        names = [str(row.get("name") or "") for row in payload.get("items") or []]
        assert any(needle in name for name in names)

        restored = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/?page=1&page_size=48&q="
        )
        assert restored.status_code == 200
        restored_payload = restored.json()
        assert int(restored_payload.get("total") or 0) == total

        out_resp = client.get(
            "/shop-manager/stock-management/catalog/"
            f"?mode=out&shop_id={shop.pk}&page=1&page_size=12"
        )
        assert out_resp.status_code == 200
        out_payload = out_resp.json()
        assert out_payload.get("ok") is True
        assert out_payload.get("mode") == "out"
        assert "items" in out_payload

    return timed(once, iterations)


def loop_stock_catalog_page2(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()

    def once():
        page1 = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/?page=1&page_size=12"
        )
        assert page1.status_code == 200
        p1 = page1.json()
        assert p1.get("ok") is True
        assert p1.get("has_more") is True
        ids1 = {row["id"] for row in p1.get("items") or []}
        assert ids1

        page2 = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/?page=2&page_size=12"
        )
        assert page2.status_code == 200
        p2 = page2.json()
        assert p2.get("ok") is True
        assert p2.get("page") == 2
        ids2 = {row["id"] for row in p2.get("items") or []}
        assert ids2
        assert ids1.isdisjoint(ids2)

    return timed(once, iterations)


def loop_stock_in_out_smoke(profile, shop, items, iterations: int) -> list[float]:
    from django.http import QueryDict

    from items.models import ShopStock
    from items.services import apply_stock_movement

    item = next(
        (row for row in items if not getattr(row, "track_serial_number", False)),
        items[0],
    )

    def once():
        stock, _ = ShopStock.objects.get_or_create(
            shop=shop, item=item, defaults={"quantity": 0}
        )
        before = int(stock.quantity)

        inbound = QueryDict(mutable=True)
        inbound["shop_id"] = str(shop.pk)
        inbound.setlist("item_id", [str(item.pk)])
        inbound.setlist("quantity", ["2"])
        inbound.setlist("buying_price", ["12.50"])
        inbound.setlist("payment_status", ["paid"])
        inbound.setlist("supplier_name", ["PAGE HOP SUPPLIER"])
        inbound.setlist("supplier_phone_country_code", ["+254"])
        inbound.setlist("supplier_phone_number", ["712345678"])
        inbound.setlist("serial_numbers", [""])
        apply_stock_movement(profile, "in", inbound)

        stock.refresh_from_db()
        assert int(stock.quantity) == before + 2

        outbound = QueryDict(mutable=True)
        outbound["shop_id"] = str(shop.pk)
        outbound.setlist("item_id", [str(item.pk)])
        outbound.setlist("quantity", ["1"])
        outbound.setlist("reason", ["waste"])
        outbound.setlist("refund", ["no"])
        outbound.setlist("refund_amount", [""])
        outbound.setlist("serial_numbers", [""])
        apply_stock_movement(profile, "out", outbound)

        stock.refresh_from_db()
        assert int(stock.quantity) == before + 1

    return timed(once, max(3, iterations // 2))


def loop_concurrent_checkout(shop, profile, items, iterations: int) -> list[float]:
    from django.db import close_old_connections

    from shops.services import complete_shop_checkout

    cart = items[:4]

    def one_checkout(nonce: str):
        close_old_connections()
        try:
            payload = {
                "kind": "quotation",
                "login_code": profile.employee_id,
                "client_name": f"CONCUR {nonce}",
                "client_phone": "0720000002",
                "lines": [
                    {"id": item.pk, "qty": 1, "price": str(item.shop_price)}
                    for item in cart
                ],
            }
            complete_shop_checkout(shop=shop, profile=profile, payload=payload)
        finally:
            close_old_connections()

    def once():
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(one_checkout, f"{uuid.uuid4().hex[:6]}-{i}")
                for i in range(4)
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()

    return timed(once, max(3, iterations // 3))


def _db_engine() -> str:
    from django.conf import settings

    return str(settings.DATABASES["default"]["ENGINE"])


def _is_mysql() -> bool:
    return "mysql" in _db_engine()


def _thread_call(fn, *args, **kwargs):
    from django.db import close_old_connections

    close_old_connections()
    try:
        return fn(*args, **kwargs)
    finally:
        close_old_connections()


def check_gunicorn_mysql_capacity() -> bool:
    """Ensure workers×threads leave headroom under MySQL max_connections."""
    import multiprocessing

    from django.conf import settings
    from django.db import connection

    workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
    threads = int(os.getenv("GUNICORN_THREADS", "2"))
    app_slots = max(1, workers * max(1, threads))
    reserve = int(os.getenv("MYSQL_CONN_RESERVE", "20"))
    label = "gunicorn-mysql capacity"

    if not _is_mysql():
        print(
            f"[PASS] {label}: SQLite/local engine — skip "
            f"(configured app slots={app_slots}; set MYSQL_ENABLED for production check)"
        )
        return True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
            row = cursor.fetchone()
        max_connections = int(row[1]) if row else 0
    except Exception as exc:  # noqa: BLE001 — capacity check must not crash the suite
        print(f"[FAIL] {label}: could not read MySQL max_connections ({exc})")
        hint = FIX_HINTS.get(label)
        if hint:
            print(f"       -> correct: {hint}")
        return False

    needed = app_slots + reserve
    conn_max_age = int(settings.DATABASES["default"].get("CONN_MAX_AGE") or 0)
    ok = needed <= max_connections
    detail = (
        f"workers={workers} threads={threads} slots={app_slots} "
        f"reserve={reserve} needed={needed} "
        f"mysql_max_connections={max_connections} CONN_MAX_AGE={conn_max_age}"
    )
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    if not ok:
        hint = FIX_HINTS.get(label)
        if hint:
            print(f"       -> correct: {hint}")
        print(
            "       -> tip: lower GUNICORN_WORKERS/GUNICORN_THREADS or raise "
            "MySQL max_connections; keep ~20 free for admin/migrations."
        )
    return ok


def loop_parallel_catalog_gets(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    clients = []
    for _ in range(8):
        client = _client_logged_in(user)
        session = client.session
        session[SESSION_SHOP_KEY] = str(shop.pk)
        session.save()
        clients.append(client)

    def once():
        def fetch(client):
            return _thread_call(
                client.get,
                f"/my-shop/{shop.pk}/buy-stock/catalog/?page=1&page_size=48",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch, client) for client in clients]
            for future in concurrent.futures.as_completed(futures):
                response = future.result()
                assert response.status_code == 200
                payload = response.json()
                assert payload.get("ok") is True
                assert "items" in payload

    return timed(once, max(3, iterations // 2))


def loop_stock_row_contention(profile, shop, items, iterations: int) -> list[float]:
    """8 parallel outs of 1 against qty=5 → exactly 5 ok, 3 rejects, never negative."""
    from django.core.exceptions import ValidationError
    from django.db import OperationalError
    from django.http import QueryDict

    from items.models import ShopStock
    from items.services import apply_stock_movement

    if not _is_mysql():
        # SQLite cannot safely exercise row locks under threads.
        def once_sqlite():
            item = items[0]
            ShopStock.objects.update_or_create(
                shop=shop, item=item, defaults={"quantity": 5}
            )
            ok = reject = 0
            for _ in range(8):
                data = QueryDict(mutable=True)
                data["shop_id"] = str(shop.pk)
                data.setlist("item_id", [str(item.pk)])
                data.setlist("quantity", ["1"])
                data.setlist("reason", ["waste"])
                data.setlist("refund", ["no"])
                data.setlist("refund_amount", [""])
                data.setlist("serial_numbers", [""])
                try:
                    apply_stock_movement(profile, "out", data)
                    ok += 1
                except ValidationError:
                    reject += 1
            stock = ShopStock.objects.get(shop=shop, item=item)
            assert ok == 5 and reject == 3
            assert int(stock.quantity) == 0

        return timed(once_sqlite, max(2, iterations // 3))

    item = items[0]
    workers = 8
    available = 5

    def once():
        ShopStock.objects.update_or_create(
            shop=shop, item=item, defaults={"quantity": available}
        )

        def one_out(_i: int):
            data = QueryDict(mutable=True)
            data["shop_id"] = str(shop.pk)
            data.setlist("item_id", [str(item.pk)])
            data.setlist("quantity", ["1"])
            data.setlist("reason", ["waste"])
            data.setlist("refund", ["no"])
            data.setlist("refund_amount", [""])
            data.setlist("serial_numbers", [""])
            try:
                apply_stock_movement(profile, "out", data)
                return "ok"
            except ValidationError:
                return "reject"
            except OperationalError:
                return "reject"

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda i: _thread_call(one_out, i), range(workers)))

        ok = results.count("ok")
        reject = results.count("reject")
        stock = ShopStock.objects.get(shop=shop, item=item)
        assert ok == available, f"expected {available} ok, got {ok}: {results}"
        assert reject == workers - available
        assert int(stock.quantity) == 0
        assert int(stock.quantity) >= 0

    return timed(once, max(2, iterations // 3))


def _sale_payload(profile, item, *, nonce: str) -> dict:
    from shops.services import get_company_pos_settings

    pos = get_company_pos_settings()
    payload = {
        "kind": "sale",
        "login_code": profile.employee_id,
        "client_name": f"SALEWAVE {nonce}",
        "client_phone": "0720000003",
        "payment_method": "cash",
        "lines": [{"id": item.pk, "qty": 1, "price": str(item.shop_price)}],
    }
    if pos.compulsory_print_on_sale:
        channels = pos.enabled_print_channels()
        assert channels, "compulsory print on sale but no channels enabled"
        payload["print_via"] = channels[0]
    return payload


def loop_concurrent_sale_x8(shop, profile, items, iterations: int) -> list[float]:
    from items.models import ShopStock
    from shops.services import complete_shop_checkout

    item = items[0]
    workers = 8

    def once():
        ShopStock.objects.update_or_create(
            shop=shop, item=item, defaults={"quantity": 100}
        )
        before = 100

        def one_sale(i: int):
            complete_shop_checkout(
                shop=shop,
                profile=profile,
                payload=_sale_payload(profile, item, nonce=f"{uuid.uuid4().hex[:6]}-{i}"),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(lambda i=i: _thread_call(one_sale, i)) for i in range(workers)
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()

        stock = ShopStock.objects.get(shop=shop, item=item)
        assert int(stock.quantity) == before - workers

    return timed(once, max(2, iterations // 3))


def loop_catalog_stress_search(user, shop, iterations: int) -> list[float]:
    from shops.session import SESSION_SHOP_KEY

    client = _client_logged_in(user)
    session = client.session
    session[SESSION_SHOP_KEY] = str(shop.pk)
    session.save()
    noisy = "page hop item zz unlikely " + ("x" * 40)

    def once():
        response = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/"
            f"?page=1&page_size=96&q={noisy.replace(' ', '+')}"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload.get("ok") is True
        assert payload.get("page_size") == 96
        assert isinstance(payload.get("items"), list)

        wide = client.get(
            f"/my-shop/{shop.pk}/buy-stock/catalog/?page=1&page_size=96&q=Page"
        )
        assert wide.status_code == 200
        wide_payload = wide.json()
        assert wide_payload.get("ok") is True
        assert int(wide_payload.get("total") or 0) >= 1

    return timed(once, iterations)


def loop_mixed_report_and_checkout(shop, profile, items, iterations: int) -> list[float]:
    from datetime import timedelta

    from django.utils import timezone

    from items.views import _build_item_report_rows, _build_movement_timeline
    from shops.services import complete_shop_checkout

    day_end = timezone.now() + timedelta(days=1)
    day_start = day_end - timedelta(days=7)
    cart = items[:3]

    def once():
        def build_report():
            _build_item_report_rows(items, [shop.pk], day_start, day_end)
            _build_movement_timeline(
                shop_ids=[shop.pk],
                day_start=day_start,
                day_end=day_end,
                item_mode="all",
                selected_categories=[],
                selected_item_ids=[],
                report_items=items,
            )

        def one_checkout(i: int):
            complete_shop_checkout(
                shop=shop,
                profile=profile,
                payload={
                    "kind": "quotation",
                    "login_code": profile.employee_id,
                    "client_name": f"MIXED {uuid.uuid4().hex[:6]}-{i}",
                    "client_phone": "0720000004",
                    "lines": [
                        {"id": item.pk, "qty": 1, "price": str(item.shop_price)}
                        for item in cart
                    ],
                },
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(lambda: _thread_call(build_report))]
            futures.extend(
                pool.submit(lambda i=i: _thread_call(one_checkout, i)) for i in range(4)
            )
            for future in concurrent.futures.as_completed(futures):
                future.result()

    return timed(once, max(2, iterations // 3))


def run_loop(iterations: int) -> tuple[int, int]:
    fixture = seed_fixture()
    try:
        user = fixture["user"]
        profile = fixture["profile"]
        shop = fixture["shop"]
        items = fixture["items"]

        results = [
            report("login POST", loop_login(iterations), BUDGET_LOGIN),
            report("role home GET", loop_role_home(user, iterations), BUDGET_ROLE_HOME),
            report(
                "workspace GET",
                loop_workspace(user, shop, iterations),
                BUDGET_WORKSPACE,
            ),
            report(
                "buy-stock GET",
                loop_buy_stock(user, shop, iterations),
                BUDGET_BUY_STOCK,
            ),
            report(
                "page-hop chain",
                loop_page_hop_chain(user, shop, max(5, iterations // 2)),
                BUDGET_PAGE_HOP_CHAIN,
            ),
            report(
                "stock report build",
                loop_stock_report(shop, items, iterations),
                BUDGET_STOCK_REPORT,
            ),
            report(
                "printer scan (uncached)",
                loop_printer_scan(max(3, iterations // 3), use_cache=False),
                BUDGET_PRINTER_SCAN,
            ),
            report(
                "printer scan (cached)",
                loop_printer_scan(iterations, use_cache=True),
                BUDGET_PRINTER_CACHE,
            ),
            report(
                "workspace HTML size",
                loop_workspace_html_size(user, shop, max(3, iterations // 3)),
                BUDGET_WORKSPACE_HTML,
                unit="bytes",
            ),
            report(
                "catalog API page 1",
                loop_catalog_api(user, shop, iterations),
                BUDGET_CATALOG_API,
            ),
            report(
                "buy-stock HTML size",
                loop_buy_stock_html_size(user, shop, max(3, iterations // 3)),
                BUDGET_BUY_STOCK_HTML,
                unit="bytes",
            ),
            report(
                "buy-stock catalog API",
                loop_buy_stock_catalog_api(user, shop, iterations),
                BUDGET_STOCK_CATALOG_API,
            ),
            report(
                "stock-mgmt catalog API",
                loop_stock_mgmt_catalog_api(user, shop, iterations),
                BUDGET_STOCK_CATALOG_API,
            ),
            report(
                "stock catalog search",
                loop_stock_catalog_search(user, shop, iterations),
                BUDGET_STOCK_CATALOG_SEARCH,
            ),
            report(
                "stock catalog page 2",
                loop_stock_catalog_page2(user, shop, iterations),
                BUDGET_STOCK_CATALOG_API,
            ),
            report(
                "stock in/out smoke",
                loop_stock_in_out_smoke(profile, shop, items, iterations),
                BUDGET_STOCK_MOVEMENT,
            ),
            report(
                "concurrent checkout x4",
                loop_concurrent_checkout(shop, profile, items, iterations),
                BUDGET_CONCURRENT_CHECKOUT,
            ),
            report(
                "parallel catalog x8",
                loop_parallel_catalog_gets(user, shop, iterations),
                BUDGET_PARALLEL_CATALOG,
            ),
            report(
                "stock row contention",
                loop_stock_row_contention(profile, shop, items, iterations),
                BUDGET_STOCK_CONTENTION,
            ),
            report(
                "concurrent sale x8",
                loop_concurrent_sale_x8(shop, profile, items, iterations),
                BUDGET_CONCURRENT_SALE,
            ),
            report(
                "catalog stress search",
                loop_catalog_stress_search(user, shop, iterations),
                BUDGET_CATALOG_STRESS,
            ),
            report(
                "mixed report+POS",
                loop_mixed_report_and_checkout(shop, profile, items, iterations),
                BUDGET_MIXED_WORKLOAD,
            ),
            check_gunicorn_mysql_capacity(),
        ]
        passed = sum(1 for ok in results if ok)
        failed = len(results) - passed
        return passed, failed
    finally:
        cleanup_fixture(fixture)


def main() -> int:
    parser = argparse.ArgumentParser(description="MY-SHOP page-hop correction loops")
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=float, default=20.0)
    args = parser.parse_args()

    print("=== MY-SHOP page-hop / report / printer / size loops ===\n")
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
