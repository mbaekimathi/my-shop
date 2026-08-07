"""
Efficiency test loops for MY-SHOP MySQL (PyMySQL).

Re-runs create/migrate/CRUD paths and asserts they stay within time budgets.
Usage:
  python scripts/test_mysql_efficiency.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time
import uuid
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ITERATIONS = 25
# Soft budgets (seconds) — fail if median exceeds these on a healthy local MySQL
BUDGET_CONNECT = 0.25
BUDGET_EXISTS_CHECK = 0.05
BUDGET_PROFILE_LOOKUP = 0.08
BUDGET_BULK_STATUS = 0.15


def _python():
    return sys.executable


def ensure_setup() -> None:
    load_dotenv(ROOT / ".env", override=True)
    os.environ["MYSQL_ENABLED"] = "True"
    # Run setup once so DB + migrations exist
    import subprocess

    result = subprocess.run(
        [_python(), str(ROOT / "scripts" / "setup_mysql.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit("setup_mysql.py failed — cannot run efficiency loops.")


def timed(fn, n: int = ITERATIONS) -> list[float]:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return samples


def report(label: str, samples: list[float], budget: float) -> bool:
    med = statistics.median(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    ok = med <= budget
    status = "PASS" if ok else "FAIL"
    print(
        f"[{status}] {label}: median={med*1000:.2f}ms  "
        f"p95={p95*1000:.2f}ms  budget={budget*1000:.0f}ms  n={len(samples)}"
    )
    return ok


def loop_connect(cfg: dict) -> list[float]:
    def once():
        conn = pymysql.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            charset="utf8mb4",
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()

    return timed(once)


def loop_create_if_not_exists(cfg: dict) -> list[float]:
    name = cfg["database"]

    def once():
        conn = pymysql.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            conn.close()

    return timed(once, n=10)


def django_ready():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
    os.environ["MYSQL_ENABLED"] = "True"
    import django

    django.setup()


def loop_employee_id_check():
    from django.contrib.auth.models import User

    from employees.models import EmployeeProfile

    code = "000000"

    def once():
        from django.db.models import Q

        User.objects.filter(
            Q(username=code) | Q(employee_profile__employee_id=code)
        ).exists()

    return timed(once)


def loop_profile_lookup():
    from django.contrib.auth.models import User

    from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

    suffix = uuid.uuid4().hex[:6]
    emp_id = f"9{suffix[:5]}"
    user = User.objects.create_user(
        username=emp_id,
        email=f"bench_{suffix}@example.com",
        password="bench-pass-1",
        first_name="Bench",
        last_name="User",
    )
    EmployeeProfile.objects.create(
        user=user,
        employee_id=emp_id,
        phone_country_code="+254",
        phone_number="700000000",
        status=EmployeeStatus.ACTIVE,
        role=EmployeeRole.EMPLOYEE,
    )

    def once():
        EmployeeProfile.objects.select_related("user").get(employee_id=emp_id)

    samples = timed(once)

    # cleanup
    user.delete()
    return samples


def loop_bulk_status_update():
    from django.contrib.auth.models import User
    from django.db import transaction

    from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

    created_users = []
    with transaction.atomic():
        for i in range(20):
            emp_id = f"8{i:05d}"
            # wipe prior bench rows if any
            User.objects.filter(username=emp_id).delete()
            user = User.objects.create_user(
                username=emp_id,
                email=f"bulk_{emp_id}@example.com",
                password="bench-pass-1",
            )
            EmployeeProfile.objects.create(
                user=user,
                employee_id=emp_id,
                phone_country_code="+254",
                phone_number="711111111",
                status=EmployeeStatus.PENDING_APPROVAL,
                role=EmployeeRole.EMPLOYEE,
            )
            created_users.append(user)

    qs = EmployeeProfile.objects.filter(employee_id__startswith="8")

    def once():
        qs.update(status=EmployeeStatus.ACTIVE)

    samples = timed(once, n=10)

    for user in created_users:
        user.delete()
    return samples


def loop_idempotent_setup_script() -> list[float]:
    import subprocess

    def once():
        result = subprocess.run(
            [_python(), str(ROOT / "scripts" / "setup_mysql.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)

    # Fewer iterations — migrate is heavier but should stay fast when already applied
    return timed(once, n=3)


def main() -> int:
    print("=== MY-SHOP MySQL efficiency loops ===\n")
    ensure_setup()
    load_dotenv(ROOT / ".env", override=True)

    cfg = {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "myshop"),
    }

    django_ready()

    results = []
    results.append(report("connect+SELECT 1", loop_connect(cfg), BUDGET_CONNECT))
    results.append(
        report(
            "CREATE DATABASE IF NOT EXISTS (idempotent)",
            loop_create_if_not_exists(cfg),
            BUDGET_CONNECT,
        )
    )
    results.append(
        report("employee_id exists checks", loop_employee_id_check(), BUDGET_EXISTS_CHECK)
    )
    results.append(
        report(
            "select_related profile lookup",
            loop_profile_lookup(),
            BUDGET_PROFILE_LOOKUP,
        )
    )
    results.append(
        report(
            "bulk status QuerySet.update (20 rows)",
            loop_bulk_status_update(),
            BUDGET_BULK_STATUS,
        )
    )
    results.append(
        report(
            "setup_mysql.py re-run (already migrated)",
            loop_idempotent_setup_script(),
            5.0,
        )
    )

    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    print(f"\n=== Summary: {passed}/{len(results)} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
