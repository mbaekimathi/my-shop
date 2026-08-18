"""
Storefront paired-items suggestion loop.

Verifies:
  1) /shop/<id>/ renders with suggestion URL + pair modal markup
  2) Suggestions API returns co-purchased items from receipt history
  3) Closing the cart after add would have data to show (API contract)

Usage:
  python scripts/test_storefront_pair_loop.py
  python scripts/test_storefront_pair_loop.py --shop-id 1 --iterations 20
  python scripts/test_storefront_pair_loop.py --seed
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def django_ready() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
    import django

    django.setup()


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}")
    return ok


def seed_paired_receipts(shop, item_a, item_b, profile) -> None:
    from shops.models import (
        ShopPaymentMethod,
        ShopReceipt,
        ShopReceiptKind,
        ShopReceiptLine,
        ShopReceiptStatus,
    )

    receipt = ShopReceipt.objects.create(
        shop=shop,
        receipt_number=f"PAIR-LOOP-{int(time.time())}",
        kind=ShopReceiptKind.SALE,
        payment_method=ShopPaymentMethod.CASH,
        status=ShopReceiptStatus.ACTIVE,
        subtotal=Decimal("100.00"),
        total=Decimal("100.00"),
        amount_paid=Decimal("100.00"),
        cash_amount=Decimal("100.00"),
        created_by=profile,
    )
    ShopReceiptLine.objects.create(
        receipt=receipt,
        item=item_a,
        item_name=item_a.name,
        quantity=1,
        unit_price=item_a.shop_price or Decimal("50.00"),
        line_total=item_a.shop_price or Decimal("50.00"),
    )
    ShopReceiptLine.objects.create(
        receipt=receipt,
        item=item_b,
        item_name=item_b.name,
        quantity=1,
        unit_price=item_b.shop_price or Decimal("50.00"),
        line_total=item_b.shop_price or Decimal("50.00"),
    )


def find_pair_seed_items(shop):
    from collections import Counter

    from items.models import Item
    from shops.models import ShopReceipt, ShopReceiptKind, ShopReceiptStatus

    pairs = Counter()
    qs = (
        ShopReceipt.objects.filter(
            shop=shop,
            kind__in=(ShopReceiptKind.SALE, ShopReceiptKind.CREDIT),
            status__in=(ShopReceiptStatus.ACTIVE, ShopReceiptStatus.PARTIAL_RETURN),
        )
        .prefetch_related("lines")
        .order_by("-created_at")[:300]
    )
    for receipt in qs:
        ids = sorted({line.item_id for line in receipt.lines.all() if line.item_id})
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                pairs[(left, right)] += 1

    if pairs:
        (left_id, right_id), _ = pairs.most_common(1)[0]
        left = Item.objects.filter(pk=left_id, is_suspended=False).first()
        right = Item.objects.filter(pk=right_id, is_suspended=False).first()
        if left and right:
            return left, right, "existing"

    items = list(Item.objects.filter(is_suspended=False).order_by("id")[:2])
    if len(items) < 2:
        return None, None, "missing"
    return items[0], items[1], "seed-needed"


def run_once(client, shop_id: int, item_a, item_b) -> dict:
    from django.urls import reverse

    started = time.perf_counter()
    page = client.get(f"/shop/{shop_id}/")
    page_ms = (time.perf_counter() - started) * 1000
    html = page.content.decode("utf-8", errors="replace")

    template_match = re.search(
        r'data-suggestions-url-template="([^"]+)"',
        html,
    )
    template = template_match.group(1) if template_match else ""
    url = reverse(
        "employees:shop_website_suggestions",
        kwargs={"shop_id": shop_id, "item_id": item_a.pk},
    )

    started = time.perf_counter()
    response = client.get(url)
    suggest_ms = (time.perf_counter() - started) * 1000
    payload = response.json() if response.status_code == 200 else {}
    names = [row.get("name") for row in payload.get("items", [])]

    return {
        "page_status": page.status_code,
        "page_ms": page_ms,
        "has_template": bool(template),
        "template": template,
        "has_pair_modal": 'data-storefront-pair-modal' in html,
        "has_product_modal": 'data-storefront-product-modal' in html,
        "has_product_preview": 'data-storefront-preview' in html,
        "has_js": "shop-website.js" in html,
        "suggest_status": response.status_code,
        "suggest_ms": suggest_ms,
        "suggest_count": len(payload.get("items", [])),
        "suggest_names": names,
        "includes_pair": item_b.name in names,
        "url": url,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Storefront pair-suggestion loop")
    parser.add_argument("--shop-id", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Create a multi-item receipt so suggestions have data",
    )
    args = parser.parse_args()

    django_ready()
    from django.test import Client
    from employees.models import EmployeeProfile, EmployeeRole
    from shops.models import Shop

    shop = Shop.objects.filter(pk=args.shop_id, is_hidden=False, is_suspended=False).first()
    if shop is None:
        print(f"[FAIL] shop {args.shop_id} not found / unavailable")
        return 1

    item_a, item_b, mode = find_pair_seed_items(shop)
    if item_a is None:
        print("[FAIL] need at least 2 active items to test pair suggestions")
        return 1

    if args.seed or mode == "seed-needed":
        profile = (
            EmployeeProfile.objects.filter(role=EmployeeRole.SUPER_ADMIN)
            .order_by("id")
            .first()
            or EmployeeProfile.objects.order_by("id").first()
        )
        if profile is None:
            print("[FAIL] no employee profile available to seed receipts")
            return 1
        seed_paired_receipts(shop, item_a, item_b, profile)
        print(f"[SEED] linked {item_a.name!r} + {item_b.name!r} on a sale receipt")
    else:
        print(f"[INFO] using existing pair {item_a.name!r} + {item_b.name!r}")

    client = Client()
    results = []
    ok_all = True
    for i in range(1, args.iterations + 1):
        row = run_once(client, shop.pk, item_a, item_b)
        results.append(row)
        ok = (
            row["page_status"] == 200
            and row["has_template"]
            and row["has_pair_modal"]
            and row["has_product_modal"]
            and row["has_product_preview"]
            and row["has_js"]
            and row["suggest_status"] == 200
            and row["suggest_count"] > 0
            and row["includes_pair"]
        )
        ok_all = ok_all and ok
        print(
            f"[{'PASS' if ok else 'FAIL'}] iter={i} "
            f"page={row['page_status']} suggest={row['suggest_status']} "
            f"n={row['suggest_count']} includes_pair={row['includes_pair']} "
            f"page_ms={row['page_ms']:.1f} suggest_ms={row['suggest_ms']:.1f}"
        )
        if not ok and i == 1:
            print(f"  template={row['template']!r}")
            print(f"  url={row['url']}")
            print(f"  names={row['suggest_names']}")

    page_samples = [r["page_ms"] for r in results]
    suggest_samples = [r["suggest_ms"] for r in results]
    print(
        f"[STATS] page median={statistics.median(page_samples):.1f}ms  "
        f"suggest median={statistics.median(suggest_samples):.1f}ms"
    )
    if ok_all:
        print("[PASS] storefront pair loop")
        return 0
    print("[FAIL] storefront pair loop")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
