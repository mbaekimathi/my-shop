"""
Online + offline capability loop for MY-SHOP.

Usage:
  python scripts/test_online_offline_loop.py
  python scripts/test_online_offline_loop.py --iterations 50
  python scripts/test_online_offline_loop.py --continuous --interval 10
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUDGET_PING = 0.08
BUDGET_PRODUCTS = 0.20
BUDGET_ONLINE_SALE = 0.35
BUDGET_OFFLINE_SYNC_HANDLER = 0.40
BUDGET_SYNC_API_BATCH = 0.50
BUDGET_EMP_ID_CHECK = 0.08
BUDGET_EMP_ID_CACHE = 0.02
BUDGET_IDEMPOTENCY = 0.20

CASHIER_ID = "700001"
CASHIER_PASSWORD = "loop-test-pass"
SUPER_ADMIN_ID = "700002"


def django_ready() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("RATE_LIMIT_CHECK_ID_MAX", "100000")
    os.environ.setdefault("RATE_LIMIT_SYNC_MAX", "100000")
    os.environ.setdefault("RATE_LIMIT_POS_SALE_MAX", "100000")
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
        raise SystemExit("migrate failed — cannot run online/offline loop.")


def report(label: str, samples: list[float], budget: float) -> bool:
    if not samples:
        print(f"[SKIP] {label}")
        return True
    med = statistics.median(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    ok = med <= budget
    print(
        f"[{'PASS' if ok else 'FAIL'}] {label}: median={med*1000:.2f}ms  "
        f"p95={p95*1000:.2f}ms  budget={budget*1000:.0f}ms  n={len(samples)}"
    )
    return ok


def ensure_users():
    from django.contrib.auth.models import User

    from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

    cashier_user, _ = User.objects.get_or_create(
        username=CASHIER_ID,
        defaults={"email": "cashier_loop@test.local", "is_active": True},
    )
    cashier_user.set_password(CASHIER_PASSWORD)
    cashier_user.save()

    profile, _ = EmployeeProfile.objects.get_or_create(
        user=cashier_user,
        defaults={
            "employee_id": CASHIER_ID,
            "phone_country_code": "+254",
            "phone_number": "700000001",
            "status": EmployeeStatus.ACTIVE,
            "role": EmployeeRole.SHOP_CASHIER,
        },
    )
    profile.role = EmployeeRole.SHOP_CASHIER
    profile.status = EmployeeStatus.ACTIVE
    profile.save(update_fields=["role", "status", "updated_at"])

    admin_user, _ = User.objects.get_or_create(
        username=SUPER_ADMIN_ID,
        defaults={"email": "admin_loop@test.local", "is_active": True},
    )
    admin_user.set_password(CASHIER_PASSWORD)
    admin_user.save()

    admin_profile, _ = EmployeeProfile.objects.get_or_create(
        user=admin_user,
        defaults={
            "employee_id": SUPER_ADMIN_ID,
            "phone_country_code": "+254",
            "phone_number": "700000002",
            "status": EmployeeStatus.ACTIVE,
            "role": EmployeeRole.SUPER_ADMIN,
        },
    )
    admin_profile.role = EmployeeRole.SUPER_ADMIN
    admin_profile.status = EmployeeStatus.ACTIVE
    admin_profile.save(update_fields=["role", "status", "updated_at"])

    return cashier_user, admin_user, profile, admin_profile


def cleanup_users(cashier_user, admin_user, cashier_profile, admin_profile) -> None:
    """Remove online/offline loop users and their sales from the live DB."""
    from django.contrib.auth.models import User
    from django.db import transaction

    from employees.models import EmployeeProfile
    from pos.models import Sale, SaleLine

    profile_ids = [
        pk
        for pk in (
            getattr(cashier_profile, "pk", None),
            getattr(admin_profile, "pk", None),
        )
        if pk
    ]
    user_ids = [
        pk
        for pk in (getattr(cashier_user, "pk", None), getattr(admin_user, "pk", None))
        if pk
    ]

    with transaction.atomic():
        if profile_ids:
            sale_ids = list(
                Sale.objects.filter(employee_id__in=profile_ids).values_list("id", flat=True)
            )
            SaleLine.objects.filter(sale_id__in=sale_ids).delete()
            Sale.objects.filter(id__in=sale_ids).delete()
            EmployeeProfile.objects.filter(id__in=profile_ids).delete()
        EmployeeProfile.objects.filter(employee_id__in=[CASHIER_ID, SUPER_ADMIN_ID]).delete()
        if user_ids:
            User.objects.filter(id__in=user_ids).delete()
        User.objects.filter(username__in=[CASHIER_ID, SUPER_ADMIN_ID]).delete()


def seed_products_if_empty():
    from decimal import Decimal

    from pos.models import Product

    if Product.objects.exists():
        Product.objects.filter(sku__startswith="SKU").update(stock=500)
        return

    samples = [
        ("SKU001", "Water", "15.00", 200),
        ("SKU002", "Bread", "55.00", 80),
        ("SKU003", "Milk", "120.00", 60),
        ("SKU004", "Sugar", "180.00", 45),
        ("SKU005", "Oil", "250.00", 35),
        ("SKU006", "Rice", "320.00", 50),
        ("SKU007", "Tea", "95.00", 70),
        ("SKU008", "Soap", "40.00", 100),
    ]
    for sku, name, price, stock in samples:
        Product.objects.get_or_create(
            sku=sku,
            defaults={"name": name, "price": Decimal(price), "stock": stock, "is_active": True},
        )


def build_sale_payload(sku: str, client_id: str | None = None) -> dict:
    from pos.models import Product

    product = Product.objects.get(sku=sku, is_active=True)
    price = float(product.price)
    return {
        "client_id": client_id or str(uuid.uuid4()),
        "sold_at": datetime.now(timezone.utc).isoformat(),
        "total": price,
        "lines": [
            {
                "product_sku": sku,
                "product_name": product.name,
                "quantity": 1,
                "unit_price": price,
                "line_total": price,
            }
        ],
    }


def run_loop(iterations: int) -> tuple[int, int]:
    from django.conf import settings
    from django.test import Client, override_settings

    from employees.services import employee_id_is_taken, mark_employee_id_taken
    from employees.sync_handlers import process_sync_operations
    from pos.models import Product, Sale

    seed_products_if_empty()
    cashier_user, admin_user, cashier_profile, admin_profile = ensure_users()
    try:
        client = Client()
        client.force_login(cashier_user)

        skus = list(
            Product.objects.filter(is_active=True, sku__startswith="SKU")
            .order_by("sku")
            .values_list("sku", flat=True)
        )

        ping_s, product_s, sale_s, offline_s, sync_s, emp_s, cache_s, idem_s = [], [], [], [], [], [], [], []
        errors = []

        allowed = list(settings.ALLOWED_HOSTS)
        if "testserver" not in allowed:
            allowed.append("testserver")

        with override_settings(ALLOWED_HOSTS=allowed):
            for i in range(iterations):
                sku = skus[i % len(skus)]
                code = f"9{i:05d}"

                t0 = time.perf_counter()
                r = client.get("/pos/api/ping/")
                ping_s.append(time.perf_counter() - t0)
                if r.status_code != 200:
                    errors.append(f"ping {i}")

                t0 = time.perf_counter()
                r = client.get("/pos/api/products/")
                product_s.append(time.perf_counter() - t0)
                if r.status_code != 200:
                    errors.append(f"products {i}")

                payload = build_sale_payload(sku)
                t0 = time.perf_counter()
                r = client.post("/pos/api/sales/", data=json.dumps(payload), content_type="application/json")
                sale_s.append(time.perf_counter() - t0)
                if r.status_code not in (200, 201):
                    errors.append(f"online sale {i}")

                op = {"id": str(uuid.uuid4()), "type": "create_sale", "payload": build_sale_payload(skus[(i+1)%len(skus)])}
                t0 = time.perf_counter()
                res = process_sync_operations(cashier_profile, [op])
                offline_s.append(time.perf_counter() - t0)
                if res["failed"]:
                    errors.append(f"offline handler {i}")

                batch = [
                    {"id": str(uuid.uuid4()), "type": "create_sale", "payload": build_sale_payload(skus[(i+j+2)%len(skus)])}
                    for j in range(3)
                ]
                t0 = time.perf_counter()
                r = client.post("/employees/api/sync/", data=json.dumps({"operations": batch}), content_type="application/json")
                sync_s.append(time.perf_counter() - t0)
                if r.status_code not in (200, 207) or r.json().get("failed"):
                    errors.append(f"sync api {i}")

                dup_id = str(uuid.uuid4())
                dup = build_sale_payload(sku, client_id=dup_id)
                t0 = time.perf_counter()
                process_sync_operations(cashier_profile, [{"id": "a", "type": "create_sale", "payload": dup}])
                process_sync_operations(cashier_profile, [{"id": "b", "type": "create_sale", "payload": dup}])
                idem_s.append(time.perf_counter() - t0)
                if Sale.objects.filter(client_id=dup_id).count() != 1:
                    errors.append(f"idempotency {i}")

                t0 = time.perf_counter()
                r = Client().get(f"/employees/api/check-employee-id/?code={code}")
                emp_s.append(time.perf_counter() - t0)
                if r.status_code != 200:
                    errors.append(f"emp check {i}")

                mark_employee_id_taken(code)
                t0 = time.perf_counter()
                if not employee_id_is_taken(code):
                    errors.append(f"cache {i}")
                cache_s.append(time.perf_counter() - t0)

        results = [
            report("ONLINE ping", ping_s, BUDGET_PING),
            report("ONLINE catalog", product_s, BUDGET_PRODUCTS),
            report("ONLINE sale", sale_s, BUDGET_ONLINE_SALE),
            report("OFFLINE sync handler", offline_s, BUDGET_OFFLINE_SYNC_HANDLER),
            report("OFFLINE sync API batch", sync_s, BUDGET_SYNC_API_BATCH),
            report("OFFLINE idempotency", idem_s, BUDGET_IDEMPOTENCY),
            report("ONLINE emp ID check", emp_s, BUDGET_EMP_ID_CHECK),
            report("CACHE emp ID", cache_s, BUDGET_EMP_ID_CACHE),
        ]
        passed = sum(1 for r in results if r)
        failed = len(results) - passed + (1 if errors else 0)
        if errors:
            print(f"\nLogic errors ({len(errors)}):", errors[:5])
        return passed, failed
    finally:
        cleanup_users(cashier_user, admin_user, cashier_profile, admin_profile)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    print("=== MY-SHOP online/offline capability loop ===\n")
    django_ready()
    ensure_migrations()

    total_fail = 0
    pass_num = 0
    try:
        while True:
            pass_num += 1
            print(f"--- Pass {pass_num} (n={args.iterations}) ---")
            _, failed = run_loop(args.iterations)
            total_fail += failed
            if not args.continuous:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.")

    print(f"\n=== Finished: {pass_num} pass(es), {total_fail} failure groups ===")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
