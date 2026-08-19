"""
WhatsApp catalogue / missing-image / Twilio-sync correction loops.

Guards the Share items page so leftover test SIDs and missing item photos
cannot 404 on every load, and so catalogue GET never blocks on Twilio.

Usage:
  python scripts/test_whatsapp_catalogue_loop.py
  python scripts/test_whatsapp_catalogue_loop.py --iterations 12
  python scripts/test_whatsapp_catalogue_loop.py --continuous --interval 20
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent.parent

BUDGET_SHARE_ROWS = 0.35
BUDGET_CATALOGUE_GET = 1.20
BUDGET_ITEM_CATALOG = 0.40

FIX_HINTS = {
    "sid helper": "communications.twilio must export is_twilio_message_sid.",
    "skip fake sid": "fetch_twilio_message must reject SM_FAKE_* before urlopen.",
    "404 no traceback": "HTTP 404 must return _not_found without exc_info traceback.",
    "sync skip statuses": "sync must exclude local/missing SIDs from Twilio fetches.",
    "catalogue skips sync": "whatsapp_catalogue GET must not call sync_outbound_delivery_status.",
    "item public url": "Item.public_image_url must skip missing media files.",
    "share row omits missing": "_item_share_row must not emit a 404 media URL.",
    "item catalog omits missing": "build_item_management_catalog_page must omit missing photos.",
    "shop catalog omits missing": "POS/storefront catalog rows must omit missing photos.",
    "fake sid marked local": "Invalid SIDs must be parked as provider_status=local.",
    "twilio 404 marked missing": "A Twilio 404 must park the SID so it is not retried.",
    "sync cooldown": "Repeat sync within the cooldown must not hit Twilio again.",
    "share rows timed": "Building catalogue share rows should stay under budget.",
    "item catalog timed": "Item-management catalog page should stay under budget.",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def django_ready() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
    import django

    django.setup()
    from django.conf import settings

    hosts = list(settings.ALLOWED_HOSTS or [])
    for host in ("testserver", "localhost", "127.0.0.1"):
        if host not in hosts:
            hosts.append(host)
    settings.ALLOWED_HOSTS = hosts


def check_source_invariants() -> list[tuple[str, bool, str]]:
    twilio = _read(ROOT / "communications" / "twilio.py")
    views = _read(ROOT / "communications" / "views.py")
    models = _read(ROOT / "items" / "models.py")
    results: list[tuple[str, bool, str]] = []

    results.append(
        (
            "sid helper",
            "def is_twilio_message_sid(" in twilio,
            "is_twilio_message_sid defined",
        )
    )
    skip_ok = (
        "is_twilio_message_sid(msid)" in twilio
        and "urlopen" in twilio.split("def fetch_twilio_message", 1)[-1]
    )
    results.append(("skip fake sid", skip_ok, "fetch guards SID before urlopen"))
    results.append(
        (
            "404 no traceback",
            'exc.code == 404' in twilio and '"_not_found"' in twilio,
            "404 returns _not_found",
        )
    )
    results.append(
        (
            "sync skip statuses",
            "TWILIO_SYNC_SKIP_STATUSES" in twilio and '"local"' in twilio,
            "local/missing SIDs are excluded",
        )
    )
    cat_fn = views.split("def whatsapp_catalogue", 1)[-1].split("def whatsapp_contacts", 1)[0]
    results.append(
        (
            "catalogue skips sync",
            "sync_outbound_delivery_status" not in cat_fn,
            "Share items GET does not poll Twilio",
        )
    )
    results.append(
        (
            "item public url",
            "def public_image_url" in models and "storage.exists" in models,
            "Item.public_image_url checks the file exists",
        )
    )
    return results


def _seed():
    from django.contrib.auth.models import User

    from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
    from items.models import Item
    from shops.models import Shop

    suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        username=f"81{suffix[:4]}",
        password="wa-loop",
        email=f"wa-loop-{suffix}@test.local",
        is_active=True,
    )
    profile = EmployeeProfile.objects.create(
        user=user,
        employee_id=f"81{suffix[:4]}",
        phone_country_code="+254",
        phone_number=f"711{suffix[:6]}",
        status=EmployeeStatus.ACTIVE,
        role=EmployeeRole.IT_SUPPORT,
    )
    shop = Shop.objects.create(
        name=f"WA LOOP {suffix}",
        location="NAIROBI",
        email=f"wa-loop-{suffix}@test.local",
        phone_number="0700000811",
        login_code=f"81{suffix[:4]}",
        password_hash="x",
        created_by=profile,
    )
    item = Item.objects.create(
        category="PHONES",
        name=f"LOOP PIXEL {suffix}",
        minimum_selling_price=Decimal("100.00"),
        shop_price=Decimal("150.00"),
        created_by=profile,
        image="items/images/25.jpeg",
    )
    return {
        "suffix": suffix,
        "user": user,
        "profile": profile,
        "shop": shop,
        "item": item,
    }


def _cleanup(fixture: dict) -> None:
    from communications.models import BroadcastCampaign
    from items.models import Item
    from shops.models import Shop

    item = fixture.get("item")
    shop = fixture.get("shop")
    profile = fixture.get("profile")
    user = fixture.get("user")
    if item:
        BroadcastCampaign.objects.filter(created_by=profile).delete()
        Item.objects.filter(pk=item.pk).delete()
    if shop:
        Shop.objects.filter(pk=shop.pk).delete()
    if profile:
        profile.delete()
    if user:
        user.delete()


def check_runtime(iterations: int) -> list[tuple[str, bool, str]]:
    from django.core.cache import cache
    from django.test import Client

    from communications.constants import CAMPAIGN_QUEUED, MSG_SENT
    from communications.models import BroadcastCampaign, OutboundMessage
    from communications.twilio import (
        fetch_twilio_message,
        is_twilio_message_sid,
        sync_outbound_delivery_status,
    )
    from communications.views import _item_share_row
    from items.services import build_item_management_catalog_page
    from shops.views import _catalog_rows_for_items

    class FakeTwilioSettings:
        twilio_account_sid = "AC" + "a" * 32
        twilio_auth_token = "secret-token"

        def has_twilio_credentials(self):
            return True

    results: list[tuple[str, bool, str]] = []
    fixture = _seed()
    item = fixture["item"]
    profile = fixture["profile"]
    shop = fixture["shop"]
    user = fixture["user"]
    try:
        results.append(
            (
                "item public url",
                item.public_image_url() == "",
                "missing 25.jpeg -> empty URL",
            )
        )
        row = _item_share_row(item)
        results.append(
            (
                "share row omits missing",
                row.get("image_url") == "",
                "catalogue thumb is a placeholder",
            )
        )
        catalog = build_item_management_catalog_page(q=item.name)
        match = next((r for r in catalog["items"] if r["id"] == item.pk), None)
        results.append(
            (
                "item catalog omits missing",
                bool(match) and match["image_url"] == "",
                "item management API omits 404 photo",
            )
        )
        shop_rows = _catalog_rows_for_items(shop, [item])
        results.append(
            (
                "shop catalog omits missing",
                shop_rows and shop_rows[0]["image_url"] == "",
                "POS/storefront omits 404 photo",
            )
        )

        fake_sid = "SM_FAKE_142570218000000"
        real_sid = "SM" + "a" * 32
        results.append(
            (
                "skip fake sid",
                (not is_twilio_message_sid(fake_sid)) and is_twilio_message_sid(real_sid),
                "SM_FAKE rejected; real SID accepted",
            )
        )
        with patch("communications.twilio.urlopen") as mocked:
            fetch_twilio_message(
                "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "token",
                fake_sid,
            )
            results.append(
                (
                    "skip fake sid",
                    mocked.call_count == 0,
                    "urlopen not called for SM_FAKE",
                )
            )

        with patch(
            "communications.twilio.get_communications_settings",
            return_value=FakeTwilioSettings(),
        ):
            cache.clear()
            campaign = BroadcastCampaign.objects.create(
                created_by=profile,
                body_template="Hi",
                status=CAMPAIGN_QUEUED,
                recipient_count=2,
            )
            fake = OutboundMessage.objects.create(
                campaign=campaign,
                client_name="FAKE",
                phone="254700000001",
                body="Hi",
                status=MSG_SENT,
                wa_message_id=fake_sid,
            )
            gone = OutboundMessage.objects.create(
                campaign=campaign,
                client_name="GONE",
                phone="254700000002",
                body="Hi",
                status=MSG_SENT,
                wa_message_id=real_sid,
            )

            def fake_urlopen(request, timeout=15):
                raise HTTPError(
                    request.full_url, 404, "Not Found", hdrs=None, fp=BytesIO()
                )

            with patch(
                "communications.twilio.urlopen", side_effect=fake_urlopen
            ) as mocked:
                sync_outbound_delivery_status(force=True)
                fake.refresh_from_db()
                gone.refresh_from_db()
                results.append(
                    (
                        "fake sid marked local",
                        fake.provider_status == "local",
                        f"provider_status={fake.provider_status}",
                    )
                )
                results.append(
                    (
                        "twilio 404 marked missing",
                        gone.provider_status == "missing" and mocked.call_count == 1,
                        f"status={gone.provider_status} fetches={mocked.call_count}",
                    )
                )
                sync_outbound_delivery_status(force=True)
                results.append(
                    (
                        "twilio 404 marked missing",
                        mocked.call_count == 1,
                        "parked SID is not fetched again",
                    )
                )

            cache.clear()
            live = OutboundMessage.objects.create(
                campaign=campaign,
                client_name="LIVE",
                phone="254700000003",
                body="Hi",
                status=MSG_SENT,
                wa_message_id="SM" + "b" * 32,
            )
            with patch("communications.twilio.urlopen") as mocked:
                mocked.return_value.__enter__.return_value.read.return_value = (
                    b'{"status":"queued"}'
                )
                sync_outbound_delivery_status()
                first = mocked.call_count
                sync_outbound_delivery_status()
                results.append(
                    (
                        "sync cooldown",
                        first == 1 and mocked.call_count == 1,
                        f"fetches={mocked.call_count} after two syncs",
                    )
                )
            live.delete()

        client = Client()
        client.force_login(user)
        with patch("communications.views.sync_outbound_delivery_status") as mocked:
            response = client.get("/it-support/whatsapp/catalogue/")
        results.append(
            (
                "catalogue skips sync",
                response.status_code == 200 and mocked.call_count == 0,
                f"GET {response.status_code}, sync calls={mocked.call_count}",
            )
        )

        share_samples = []
        for _ in range(max(3, iterations)):
            t0 = time.perf_counter()
            _item_share_row(item)
            share_samples.append(time.perf_counter() - t0)
        share_med = statistics.median(share_samples)
        results.append(
            (
                "share rows timed",
                share_med <= BUDGET_SHARE_ROWS,
                f"median={share_med * 1000:.1f}ms budget={BUDGET_SHARE_ROWS * 1000:.0f}ms",
            )
        )
        catalog_samples = []
        for _ in range(max(3, min(iterations, 8))):
            t0 = time.perf_counter()
            build_item_management_catalog_page(q=item.name, page_size=24)
            catalog_samples.append(time.perf_counter() - t0)
        catalog_med = statistics.median(catalog_samples)
        results.append(
            (
                "item catalog timed",
                catalog_med <= BUDGET_ITEM_CATALOG,
                f"median={catalog_med * 1000:.1f}ms budget={BUDGET_ITEM_CATALOG * 1000:.0f}ms",
            )
        )
        cat_samples = []
        for _ in range(max(2, min(iterations, 6))):
            t0 = time.perf_counter()
            client.get("/it-support/whatsapp/catalogue/")
            cat_samples.append(time.perf_counter() - t0)
        cat_med = statistics.median(cat_samples)
        results.append(
            (
                "catalogue skips sync",
                cat_med <= BUDGET_CATALOGUE_GET,
                f"GET median={cat_med * 1000:.0f}ms budget={BUDGET_CATALOGUE_GET * 1000:.0f}ms",
            )
        )
    finally:
        _cleanup(fixture)
    return results


def run_once(iterations: int) -> bool:
    print("=== MY-SHOP WhatsApp catalogue / image / Twilio sync loops ===\n")
    checks: list[tuple[str, bool, str]] = []
    checks.extend(check_source_invariants())
    checks.extend(check_runtime(iterations))

    passed = 0
    failed = 0
    seen_fail_keys: set[str] = set()
    for label, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label}: {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
            if label not in seen_fail_keys:
                hint = FIX_HINTS.get(label)
                if hint:
                    print(f"       fix -> {hint}")
                seen_fail_keys.add(label)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    return failed == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WhatsApp catalogue / missing-image / Twilio-sync correction loops"
    )
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=float, default=20.0)
    args = parser.parse_args()

    django_ready()
    if not args.continuous:
        return 0 if run_once(args.iterations) else 1

    round_no = 0
    while True:
        round_no += 1
        print(f"--- round {round_no} ---")
        ok = run_once(args.iterations)
        if not ok:
            return 1
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
