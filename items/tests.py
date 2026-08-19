from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop


class ItemStockReportRowsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840011",
            password="report-pass",
            email="stock-report@test.local",
            first_name="STOCK",
            last_name="REPORT",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840011",
            phone_country_code="+254",
            phone_number="700000941",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="REPORT SHOP A",
            location="NAIROBI",
            email="report-a@test.local",
            phone_number="0700000941",
            login_code="840111",
            password_hash="x",
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="REPORT SHOP B",
            location="MOMBASA",
            email="report-b@test.local",
            phone_number="0700000942",
            login_code="840112",
            password_hash="x",
            created_by=self.profile,
        )
        from items.models import Item, ShopStock

        self.item = Item.objects.create(
            category="CABLES",
            name="REPORT CABLE",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.shop_a, item=self.item, quantity=2)
        ShopStock.objects.create(shop=self.shop_b, item=self.item, quantity=8)
        self.now = timezone.now()
        self.day_start = self.now - timedelta(hours=1)
        self.day_end = self.now + timedelta(hours=1)

    def _fulfill_transfer(self, *, qty=2):
        from items.models import (
            StockMovement,
            StockMovementLine,
            StockMovementType,
            StockRequestStatus,
        )

        movement = StockMovement.objects.create(
            movement_type=StockMovementType.REQUEST,
            shop=self.shop_a,
            requested_from_shop=self.shop_b,
            request_status=StockRequestStatus.FULFILLED,
            responded_at=self.now,
            created_by=self.profile,
            responded_by=self.profile,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            quantity=qty,
        )
        return movement

    def test_destination_shop_counts_transfer_in(self):
        from items.views import _build_item_report_rows

        self._fulfill_transfer(qty=2)
        rows = _build_item_report_rows(
            [self.item],
            [self.shop_a.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stock_transfer_in"], 2)
        self.assertEqual(row["stock_transfer_out"], 0)
        self.assertEqual(row["starting_stock"], 0)
        self.assertEqual(row["closing_stock"], 2)

    def test_source_shop_counts_transfer_out(self):
        from items.views import _build_item_report_rows

        self._fulfill_transfer(qty=2)
        rows = _build_item_report_rows(
            [self.item],
            [self.shop_b.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stock_transfer_in"], 0)
        self.assertEqual(row["stock_transfer_out"], 2)
        self.assertEqual(row["starting_stock"], 10)
        self.assertEqual(row["closing_stock"], 8)

    def test_all_shops_show_each_shop_transfer_side(self):
        from items.views import _build_item_report_rows

        self._fulfill_transfer(qty=2)
        rows = _build_item_report_rows(
            [self.item],
            [self.shop_a.pk, self.shop_b.pk],
            self.day_start,
            self.day_end,
        )
        by_shop = {row["shop_name"]: row for row in rows}
        self.assertEqual(set(by_shop), {self.shop_a.name, self.shop_b.name, "Total"})
        self.assertEqual(rows[0]["shop_name"], self.shop_a.name)
        self.assertTrue(rows[0]["is_item_start"])
        self.assertEqual(rows[1]["shop_name"], self.shop_b.name)
        self.assertFalse(rows[1]["is_item_start"])
        self.assertTrue(rows[-1]["is_item_total"])
        self.assertEqual(rows[-1]["stock_transfer_in"], 2)
        self.assertEqual(rows[-1]["stock_transfer_out"], 2)
        self.assertEqual(by_shop[self.shop_a.name]["stock_transfer_in"], 2)
        self.assertEqual(by_shop[self.shop_a.name]["stock_transfer_out"], 0)
        self.assertEqual(by_shop[self.shop_b.name]["stock_transfer_in"], 0)
        self.assertEqual(by_shop[self.shop_b.name]["stock_transfer_out"], 2)

    def test_all_shops_list_every_shop_under_the_item(self):
        from items.models import ShopStock
        from items.views import _build_item_report_rows

        shop_c = Shop.objects.create(
            name="REPORT SHOP C",
            location="KISUMU",
            email="report-c@test.local",
            phone_number="0700000943",
            login_code="840113",
            password_hash="x",
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=shop_c, item=self.item, quantity=0)
        rows = _build_item_report_rows(
            [self.item],
            [self.shop_a.pk, self.shop_b.pk, shop_c.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(
            [row["shop_name"] for row in rows],
            [self.shop_a.name, self.shop_b.name, shop_c.name, "Total"],
        )
        self.assertTrue(rows[0]["is_item_start"])
        self.assertTrue(rows[-1]["is_item_total"])
        self.assertEqual(rows[2]["starting_stock"], 0)
        self.assertEqual(rows[2]["closing_stock"], 0)
        self.assertEqual(rows[-1]["starting_stock"], 10)
        self.assertEqual(rows[-1]["closing_stock"], 10)

    def test_idle_stock_still_listed(self):
        from items.views import _build_item_report_rows

        rows = _build_item_report_rows(
            [self.item],
            [self.shop_b.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["starting_stock"], 8)
        self.assertEqual(rows[0]["closing_stock"], 8)
        self.assertEqual(rows[0]["stock_transfer_in"], 0)
        self.assertEqual(rows[0]["stock_transfer_out"], 0)

    def test_pending_request_is_not_a_transfer(self):
        from items.models import (
            StockMovement,
            StockMovementLine,
            StockMovementType,
            StockRequestStatus,
        )
        from items.views import _build_item_report_rows

        movement = StockMovement.objects.create(
            movement_type=StockMovementType.REQUEST,
            shop=self.shop_a,
            requested_from_shop=self.shop_b,
            request_status=StockRequestStatus.PENDING,
            created_by=self.profile,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            quantity=3,
        )
        dest_rows = _build_item_report_rows(
            [self.item],
            [self.shop_a.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(dest_rows[0]["stock_transfer_in"], 0)
        self.assertEqual(dest_rows[0]["stock_transfer_out"], 0)
        self.assertEqual(dest_rows[0]["closing_stock"], 2)

    def test_movements_item_view_splits_shops_and_totals(self):
        from django.utils import timezone
        from items.views import _group_movement_events_by_item

        now = timezone.now()
        events = [
            {
                "happened_at": now,
                "event_type": "in",
                "item_id": self.item.pk,
                "item_name": self.item.name,
                "item_category": self.item.category,
                "shop_id": self.shop_a.pk,
                "source_shop_id": None,
                "quantity": 5,
                "transfer_direction": "",
            },
            {
                "happened_at": now,
                "event_type": "transfer_fulfilled",
                "item_id": self.item.pk,
                "item_name": self.item.name,
                "item_category": self.item.category,
                "shop_id": self.shop_a.pk,
                "source_shop_id": self.shop_b.pk,
                "quantity": 2,
                "transfer_direction": "both",
            },
        ]
        rows = _group_movement_events_by_item(
            events,
            [self.shop_a.pk, self.shop_b.pk],
            shops_by_id={self.shop_a.pk: self.shop_a, self.shop_b.pk: self.shop_b},
        )
        self.assertEqual(
            [row["shop_name"] for row in rows],
            [self.shop_a.name, self.shop_b.name, "Total"],
        )
        self.assertEqual(rows[0]["units_in"], 5)
        self.assertEqual(rows[0]["units_transfer_in"], 2)
        self.assertEqual(rows[1]["units_transfer_out"], 2)
        self.assertEqual(rows[-1]["units_in"], 5)
        self.assertEqual(rows[-1]["units_transfer_in"], 2)
        self.assertEqual(rows[-1]["units_transfer_out"], 2)
        self.assertTrue(rows[-1]["is_item_total"])

    def test_movements_item_view_lists_idle_stock_for_all_shops(self):
        from items.models import Item, ShopStock
        from items.views import _group_movement_events_by_item

        idle = Item.objects.create(
            category="CABLES",
            name="IDLE CABLE",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.shop_a, item=idle, quantity=4)

        rows = _group_movement_events_by_item(
            [],
            [self.shop_a.pk, self.shop_b.pk],
            shops_by_id={self.shop_a.pk: self.shop_a, self.shop_b.pk: self.shop_b},
            extra_items=[idle],
        )
        self.assertEqual(
            [row["shop_name"] for row in rows],
            [self.shop_a.name, self.shop_b.name, "Total"],
        )
        self.assertEqual(rows[0]["current_stock"], 4)
        self.assertEqual(rows[1]["current_stock"], 0)
        self.assertEqual(rows[-1]["current_stock"], 4)
        self.assertTrue(rows[-1]["is_item_total"])

    def test_low_stock_rows_are_per_shop_not_company_total(self):
        from items.views import _build_low_stock_rows

        self.item.low_stock_notify = True
        self.item.low_stock_threshold = 5
        self.item.save(update_fields=["low_stock_notify", "low_stock_threshold"])
        from items.models import ShopStock

        ShopStock.objects.filter(item=self.item).update(
            low_stock_threshold=5, low_stock_manual=True
        )

        rows, notify_count, group_by_shop = _build_low_stock_rows(
            [self.item],
            [self.shop_a, self.shop_b],
        )
        self.assertTrue(group_by_shop)
        self.assertEqual(notify_count, 1)
        self.assertEqual(
            [row["shop_name"] for row in rows],
            [self.shop_a.name, self.shop_b.name, "Total"],
        )
        self.assertEqual(rows[0]["total_units"], 2)
        self.assertTrue(rows[0]["is_low"])
        self.assertEqual(rows[1]["total_units"], 8)
        self.assertFalse(rows[1]["is_low"])
        self.assertEqual(rows[-1]["total_units"], 10)
        self.assertTrue(rows[-1]["is_low"])
        self.assertTrue(rows[0]["show_notify"])
        self.assertTrue(rows[0]["show_threshold"])
        self.assertTrue(rows[1]["show_threshold"])
        self.assertFalse(rows[1]["show_notify"])

    def test_shop_selling_alerts_use_per_shop_threshold(self):
        from items.models import ShopStock
        from items.services import list_shop_low_stock_alerts

        self.item.low_stock_notify = True
        self.item.save(update_fields=["low_stock_notify"])
        ShopStock.objects.filter(item=self.item, shop=self.shop_a).update(
            low_stock_threshold=5, low_stock_manual=True
        )
        ShopStock.objects.filter(item=self.item, shop=self.shop_b).update(
            low_stock_threshold=5, low_stock_manual=True
        )

        alerts_a = list_shop_low_stock_alerts(self.shop_a)
        alerts_b = list_shop_low_stock_alerts(self.shop_b)
        self.assertEqual([row["item_id"] for row in alerts_a], [self.item.pk])
        self.assertEqual(alerts_a[0]["quantity"], 2)
        self.assertEqual(alerts_a[0]["threshold"], 5)
        self.assertEqual(alerts_b, [])

        from items.services import shop_has_low_stock_alerts

        self.assertTrue(shop_has_low_stock_alerts(self.shop_a))
        self.assertFalse(shop_has_low_stock_alerts(self.shop_b))

    def test_notify_all_turns_every_item_on(self):
        from items.models import Item

        other = Item.objects.create(
            category="CABLES",
            name="OTHER CABLE",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.profile,
        )
        self.item.low_stock_notify = False
        self.item.save(update_fields=["low_stock_notify"])
        other.low_stock_notify = False
        other.save(update_fields=["low_stock_notify"])

        Item.objects.update(low_stock_notify=True)
        self.item.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(self.item.low_stock_notify)
        self.assertTrue(other.low_stock_notify)

    def test_low_stock_sync_copies_average_into_shop_alert(self):
        from items.models import ShopStock
        from items.views import (
            _build_low_stock_rows,
            _sync_shop_thresholds_from_usage,
            _threshold_from_weekly_avg,
        )

        self.assertEqual(_threshold_from_weekly_avg(0.1), 1)
        self.assertEqual(_threshold_from_weekly_avg(0.4), 1)
        self.assertEqual(_threshold_from_weekly_avg(0), 0)

        for week in range(13):
            self._sale(self.shop_a, 2, weeks_ago=week, number="STEADY")
        self._sale(self.shop_b, 4, weeks_ago=1, number="B-ONLY")

        _sync_shop_thresholds_from_usage(
            [self.item], [self.shop_a, self.shop_b]
        )
        self.assertEqual(
            ShopStock.objects.get(item=self.item, shop=self.shop_a).low_stock_threshold,
            _threshold_from_weekly_avg(2.0),
        )
        self.assertTrue(
            ShopStock.objects.get(item=self.item, shop=self.shop_a).low_stock_manual
        )
        self.assertEqual(
            ShopStock.objects.get(item=self.item, shop=self.shop_b).low_stock_threshold,
            _threshold_from_weekly_avg(0.3),
        )

        rows, _notify_count, _group = _build_low_stock_rows(
            [self.item],
            [self.shop_a, self.shop_b],
        )
        shop_a = next(row for row in rows if row["shop_id"] == self.shop_a.pk)
        shop_b = next(row for row in rows if row["shop_id"] == self.shop_b.pk)
        self.assertEqual(shop_a["threshold"], 2)
        self.assertTrue(shop_a["threshold_manual"])
        self.assertEqual(shop_b["threshold"], 1)
        self.assertTrue(shop_b["threshold_manual"])

        from items.views import _low_stock_payloads_from_rows

        refresh_rows, _, _ = _build_low_stock_rows(
            [self.item],
            [self.shop_a, self.shop_b],
            include_usage=False,
        )
        payload = _low_stock_payloads_from_rows(refresh_rows)[0]
        self.assertEqual(payload["item_id"], self.item.pk)
        self.assertEqual(len(payload["shops"]), 2)
        by_shop = {row["shop_id"]: row for row in payload["shops"]}
        self.assertEqual(by_shop[self.shop_a.pk]["threshold"], 2)
        self.assertEqual(by_shop[self.shop_b.pk]["threshold"], 1)

    def _sale(self, shop, qty, *, weeks_ago=0, returned=0, number="R"):
        from shops.models import ShopReceipt, ShopReceiptKind, ShopReceiptLine, ShopReceiptStatus

        when = timezone.now() - timedelta(days=weeks_ago * 7 + 1)
        receipt = ShopReceipt.objects.create(
            shop=shop,
            receipt_number=f"{number}-{shop.pk}-{weeks_ago}-{qty}",
            kind=ShopReceiptKind.SALE,
            total=Decimal("150.00") * qty,
            amount_paid=Decimal("150.00") * qty,
            created_by=self.profile,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceipt.objects.filter(pk=receipt.pk).update(created_at=when)
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=qty,
            returned_quantity=returned,
            unit_price=Decimal("150.00"),
            line_total=Decimal("150.00") * qty,
        )

    def test_low_stock_usage_is_long_run_weekly_average(self):
        from items.views import LOW_STOCK_USAGE_WEEKS, _build_low_stock_rows

        for week in range(LOW_STOCK_USAGE_WEEKS):
            self._sale(self.shop_a, 2, weeks_ago=week, number="STEADY")
        self._sale(self.shop_a, 20, weeks_ago=0, number="SPIKE")
        self._sale(self.shop_b, 4, weeks_ago=1, number="B-ONLY")

        rows, _notify_count, _group = _build_low_stock_rows(
            [self.item],
            [self.shop_a, self.shop_b],
        )
        shop_a = next(row for row in rows if row["shop_id"] == self.shop_a.pk)
        shop_b = next(row for row in rows if row["shop_id"] == self.shop_b.pk)
        total = next(row for row in rows if row["is_item_total"])

        # 13 weeks of 2, plus one extra 20: (26+20)/13 ≈ 3.5 → 4 whole units
        self.assertEqual(shop_a["avg_week"], 4)
        self.assertLess(shop_a["avg_week"], 10)
        self.assertEqual(shop_b["avg_week"], 1)
        self.assertEqual(total["avg_week"], 5)

    def test_blank_alert_uses_average_until_manually_set(self):
        from items.models import ShopStock
        from items.services import list_shop_low_stock_alerts
        from items.views import (
            _build_low_stock_rows,
            _set_shop_low_stock_threshold,
            _sync_shop_thresholds_from_usage,
        )

        self.item.low_stock_notify = True
        self.item.save(update_fields=["low_stock_notify"])
        for week in range(13):
            self._sale(self.shop_a, 2, weeks_ago=week, number="STEADY")

        rows, _notify_count, _group = _build_low_stock_rows(
            [self.item],
            [self.shop_a, self.shop_b],
        )
        shop_a = next(row for row in rows if row["shop_id"] == self.shop_a.pk)
        shop_b = next(row for row in rows if row["shop_id"] == self.shop_b.pk)
        self.assertFalse(shop_a["threshold_manual"])
        self.assertEqual(shop_a["threshold"], 2)
        self.assertTrue(shop_a["is_low"])
        self.assertFalse(shop_b["threshold_manual"])
        self.assertEqual(shop_b["threshold"], 0)

        alerts = list_shop_low_stock_alerts(self.shop_a)
        self.assertEqual([row["item_id"] for row in alerts], [self.item.pk])
        self.assertEqual(alerts[0]["threshold"], 2)

        _set_shop_low_stock_threshold(self.item, self.shop_a, 1, manual=True)
        _sync_shop_thresholds_from_usage(
            [self.item], [self.shop_a, self.shop_b]
        )
        stock_a = ShopStock.objects.get(item=self.item, shop=self.shop_a)
        self.assertEqual(stock_a.low_stock_threshold, 2)
        self.assertTrue(stock_a.low_stock_manual)

        rows, _notify_count, _group = _build_low_stock_rows(
            [self.item],
            [self.shop_a, self.shop_b],
        )
        shop_a = next(row for row in rows if row["shop_id"] == self.shop_a.pk)
        self.assertTrue(shop_a["threshold_manual"])
        self.assertEqual(shop_a["threshold"], 2)
        self.assertTrue(shop_a["is_low"])

        alerts = list_shop_low_stock_alerts(self.shop_a)
        self.assertEqual(alerts[0]["threshold"], 2)


class ItemImageUrlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840021",
            password="img-pass",
            email="item-image@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840021",
            phone_country_code="+254",
            phone_number="700000951",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        from items.models import Item

        self.item = Item.objects.create(
            category="PHONES",
            name="MISSING PHOTO",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.profile,
        )

    def test_public_image_url_skips_missing_file(self):
        self.item.image = "items/images/25.jpeg"
        self.item.save(update_fields=["image"])
        self.assertEqual(self.item.public_image_url(), "")

    def test_item_management_catalog_omits_missing_file(self):
        from items.services import build_item_management_catalog_page

        self.item.image = "items/images/30.jpeg"
        self.item.save(update_fields=["image"])
        payload = build_item_management_catalog_page(q="MISSING PHOTO")
        rows = [row for row in payload["items"] if row["id"] == self.item.pk]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_url"], "")
