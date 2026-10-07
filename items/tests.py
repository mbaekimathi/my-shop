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
            [self.shop_b.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stock_transfer_in"], 2)
        self.assertEqual(row["stock_transfer_out"], 0)
        self.assertEqual(row["starting_stock"], 6)
        self.assertEqual(row["closing_stock"], 8)

    def test_source_shop_counts_transfer_out(self):
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
        self.assertEqual(row["stock_transfer_in"], 0)
        self.assertEqual(row["stock_transfer_out"], 2)
        self.assertEqual(row["starting_stock"], 4)
        self.assertEqual(row["closing_stock"], 2)

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
        self.assertEqual(by_shop[self.shop_a.name]["stock_transfer_in"], 0)
        self.assertEqual(by_shop[self.shop_a.name]["stock_transfer_out"], 2)
        self.assertEqual(by_shop[self.shop_b.name]["stock_transfer_in"], 2)
        self.assertEqual(by_shop[self.shop_b.name]["stock_transfer_out"], 0)

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

    def test_report_row_net_sale_subtracts_returns(self):
        from decimal import Decimal

        from shops.models import (
            ShopPaymentMethod,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptLine,
            ShopReceiptStatus,
        )
        from items.views import _build_item_report_rows

        receipt = ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="REP-SALE-1",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("500.00"),
            total=Decimal("500.00"),
            amount_paid=Decimal("500.00"),
            cash_amount=Decimal("500.00"),
            created_by=self.profile,
            status=ShopReceiptStatus.PARTIAL_RETURN,
            last_returned_at=self.now,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=5,
            returned_quantity=2,
            unit_price=Decimal("100.00"),
            line_total=Decimal("300.00"),
            return_batches=[
                {
                    "qty": 2,
                    "at": self.now.isoformat(),
                    "by_id": self.profile.pk,
                    "serials": [],
                }
            ],
        )
        rows = _build_item_report_rows(
            [self.item],
            [self.shop_a.pk],
            self.day_start,
            self.day_end,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stock_sale"], 5)
        self.assertEqual(row["stock_return"], 2)
        self.assertEqual(row["net_sale"], 3)

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

    def _stock_in(self, *, shop, qty, at):
        from items.models import StockMovement, StockMovementLine, StockMovementType

        movement = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            shop=shop,
            created_by=self.profile,
        )
        StockMovement.objects.filter(pk=movement.pk).update(created_at=at)
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            quantity=qty,
        )
        return movement

    def test_daily_rows_chain_start_close_and_skip_quiet_days(self):
        from datetime import datetime, time, timedelta

        from django.utils import timezone
        from items.models import Item, ShopStock
        from items.views import _build_item_report_daily_rows

        tz = timezone.get_current_timezone()
        day1 = timezone.localdate() - timedelta(days=4)
        day2 = day1 + timedelta(days=2)  # quiet day in between
        period_start = timezone.make_aware(datetime.combine(day1, time.min), tz)
        period_end = timezone.make_aware(
            datetime.combine(day2 + timedelta(days=1), time.min), tz
        )

        # Reset known stock so starting is deterministic after movements.
        ShopStock.objects.filter(item=self.item, shop=self.shop_a).update(quantity=10)
        self._stock_in(
            shop=self.shop_a,
            qty=3,
            at=timezone.make_aware(datetime.combine(day1, time(10, 0)), tz),
        )
        self._stock_in(
            shop=self.shop_a,
            qty=2,
            at=timezone.make_aware(datetime.combine(day2, time(11, 0)), tz),
        )
        # Current stock already includes both ins in live DB? ShopStock is manual
        # in tests — bump it to match "after" both receipts for closing math.
        ShopStock.objects.filter(item=self.item, shop=self.shop_a).update(quantity=15)

        rows = _build_item_report_daily_rows(
            [self.item],
            [self.shop_a.pk],
            period_start,
            period_end,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["report_date"], day1)
        self.assertEqual(rows[1]["report_date"], day2)
        self.assertNotIn(day1 + timedelta(days=1), [row["report_date"] for row in rows])
        self.assertEqual(rows[0]["stock_in"], 3)
        self.assertEqual(rows[1]["stock_in"], 2)
        self.assertEqual(rows[0]["starting_stock"], 10)
        self.assertEqual(rows[0]["closing_stock"], 13)
        self.assertEqual(rows[1]["starting_stock"], 13)
        self.assertEqual(rows[1]["closing_stock"], 15)
        self.assertTrue(rows[0]["is_daily_row"])

        other = Item.objects.create(
            category="ROUTERS",
            name="TENDA F6",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.profile,
        )
        from items.views import _filter_items_by_search

        matched = _filter_items_by_search([self.item, other], "tenda")
        self.assertEqual([item.pk for item in matched], [other.pk])
        empty = _build_item_report_daily_rows(
            matched,
            [self.shop_a.pk],
            period_start,
            period_end,
        )
        self.assertEqual(empty, [])

    def test_parse_report_view_by_forces_item_for_single_day(self):
        from items.views import _parse_report_view_by

        self.assertEqual(_parse_report_view_by("day", range_type="period"), "day")
        self.assertEqual(_parse_report_view_by("day", range_type="day"), "item")
        self.assertEqual(_parse_report_view_by("bogus", range_type="month"), "item")

    def test_movement_event_filters_support_multi_select(self):
        from items.views import (
            _event_filters_query_value,
            _filter_movement_events,
            _movement_event_filter_label,
            _parse_movement_event_filters,
        )

        self.assertEqual(_parse_movement_event_filters([]), frozenset({"all"}))
        self.assertEqual(
            _parse_movement_event_filters(["out", "sale"]),
            frozenset({"out", "sale"}),
        )
        self.assertEqual(
            _parse_movement_event_filters(["out,sale"]),
            frozenset({"out", "sale"}),
        )
        self.assertEqual(
            _parse_movement_event_filters(
                ["in", "out", "sale", "transfer", "return"]
            ),
            frozenset({"all"}),
        )
        events = [
            {"event_type": "out", "quantity": 1, "receipt_number": ""},
            {"event_type": "sale", "quantity": 2, "receipt_number": "R1"},
            {"event_type": "return", "quantity": 1, "receipt_number": "R1"},
            {"event_type": "in", "quantity": 3, "receipt_number": ""},
        ]
        filtered = _filter_movement_events(events, frozenset({"out", "sale"}))
        self.assertEqual(
            [event["event_type"] for event in filtered],
            ["out", "sale", "return"],
        )
        self.assertEqual(
            _movement_event_filter_label(frozenset({"out", "sale"})),
            "Stock out + Sale",
        )
        self.assertEqual(
            _event_filters_query_value(frozenset({"out", "sale"})),
            ["out", "sale"],
        )

    def test_audit_pdf_summary_then_transaction_ledger(self):
        from django.utils import timezone
        from items.services import build_stock_report_pdf
        from items.views import _build_audit_detail_rows, _build_audit_summary_rows

        now = timezone.now()
        events = [
            {
                "happened_at": now,
                "event_type": "out",
                "event_label": "Stock out",
                "item_id": self.item.pk,
                "item_name": self.item.name,
                "item_category": self.item.category,
                "quantity": 3,
                "note": "Damaged",
            },
            {
                "happened_at": now,
                "event_type": "out",
                "event_label": "Stock out",
                "item_id": self.item.pk,
                "item_name": self.item.name,
                "item_category": self.item.category,
                "quantity": 2,
                "reason": "Expired",
            },
        ]
        summary_headers, summary_rows = _build_audit_summary_rows(
            events, event_filter="out", qty_label="Stocked out"
        )
        self.assertEqual(
            summary_headers,
            [
                "Item",
                "Category",
                "Transactions",
                "Stocked out",
                "Actual qty",
                "Missing",
                "Excess",
                "Note",
            ],
        )
        self.assertEqual(summary_rows[0][:4], [self.item.name, self.item.category, 2, 5])
        self.assertEqual(summary_rows[0][4:], ["", "", "", ""])
        self.assertEqual(summary_rows[-1][:4], ["Total", "", 2, 5])

        detail_headers, detail_rows = _build_audit_detail_rows(events)
        self.assertEqual(detail_headers, ["When", "Type", "Item", "Reason", "Qty"])
        self.assertEqual(len(detail_rows), 2)
        self.assertEqual(len(detail_rows[0]), 5)

        pdf = build_stock_report_pdf(
            company_name="TEST SHOP",
            page_mode="movements",
            period_label="2026",
            event_filter="out",
            event_filter_label="Stock out",
            view_by="timeline",
            view_label="Timeline",
            shop_label=self.shop_a.name,
            report_kind="audit",
            generated_at=now,
            summary_rows=summary_rows,
            summary_headers=summary_headers,
            summary_qty_label="Stocked out",
            detail_rows=detail_rows,
            detail_headers=detail_headers,
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 500)

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
                # Sender B → receiver A (same shape as timeline events).
                "shop_id": self.shop_b.pk,
                "source_shop_id": self.shop_b.pk,
                "destination_shop_id": self.shop_a.pk,
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

    def test_movements_item_view_does_not_double_count_same_day_transfer(self):
        from items.views import (
            _build_movement_timeline,
            _filter_item_summary_movement_events,
            _group_movement_events_by_item,
            _transfer_qty_by_item_shop,
        )

        qty = 2
        self._fulfill_transfer(qty=qty)
        shop_ids = [self.shop_a.pk, self.shop_b.pk]
        events, *_ = _build_movement_timeline(
            shop_ids=shop_ids,
            day_start=self.day_start,
            day_end=self.day_end,
            item_mode="all",
            selected_categories=[],
            selected_item_ids=[],
            report_items=[],
        )
        self.assertGreaterEqual(
            sum(
                1
                for event in events
                if event.get("event_type") == "transfer_fulfilled"
                and event.get("movement_id")
            ),
            1,
        )
        filtered = _filter_item_summary_movement_events(events)
        rows = _group_movement_events_by_item(
            filtered,
            shop_ids,
            shops_by_id={self.shop_a.pk: self.shop_a, self.shop_b.pk: self.shop_b},
        )
        by_shop = {
            row["shop_name"]: row
            for row in rows
            if not row.get("is_item_total")
        }
        truth = _transfer_qty_by_item_shop(
            [self.item.pk],
            shop_ids,
            self.day_start,
            self.day_end,
        )
        # shop_a sends, shop_b receives (_fulfill_transfer).
        self.assertEqual(
            by_shop[self.shop_a.name]["units_transfer_out"],
            truth[(self.item.pk, self.shop_a.pk)]["out"],
        )
        self.assertEqual(
            by_shop[self.shop_b.name]["units_transfer_in"],
            truth[(self.item.pk, self.shop_b.pk)]["in"],
        )
        self.assertEqual(by_shop[self.shop_a.name]["units_transfer_out"], qty)
        self.assertEqual(by_shop[self.shop_b.name]["units_transfer_in"], qty)
        self.assertEqual(by_shop[self.shop_a.name]["units_transfer_in"], 0)
        self.assertEqual(by_shop[self.shop_b.name]["units_transfer_out"], 0)

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

    def test_movements_item_view_require_events_skips_idle_stock(self):
        from items.models import Item, ShopStock
        from items.views import _group_movement_events_by_item

        idle = Item.objects.create(
            category="CABLES",
            name="FILTER IDLE CABLE",
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
            require_events=True,
        )
        self.assertEqual(rows, [])

    def test_movements_item_summary_all_type_includes_idle_stock(self):
        from items.models import Item, ShopStock

        idle = Item.objects.create(
            category="CABLES",
            name="ALL FILTER IDLE",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.shop_a, item=idle, quantity=4)

        self.client.force_login(self.user)
        all_types = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "movements",
                "range": "day",
                "item_mode": "all",
                "view_by": "item",
                "date": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(all_types.status_code, 200)
        idle_names = {
            row["item_name"]
            for row in all_types.context["movement_item_rows"]
            if not row.get("is_item_total")
        }
        self.assertIn("ALL FILTER IDLE", idle_names)

        stock_in_only = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "movements",
                "range": "day",
                "item_mode": "all",
                "view_by": "item",
                "date": timezone.localdate().isoformat(),
                "event_type": "in",
            },
        )
        self.assertEqual(stock_in_only.status_code, 200)
        filtered_names = {
            row["item_name"]
            for row in stock_in_only.context["movement_item_rows"]
            if not row.get("is_item_total")
        }
        self.assertNotIn("ALL FILTER IDLE", filtered_names)

    def test_all_event_filter_includes_stock_in_and_out(self):
        from items.models import StockMovement, StockMovementLine, StockMovementType
        from items.views import _build_movement_timeline

        StockMovementLine.objects.create(
            movement=StockMovement.objects.create(
                movement_type=StockMovementType.IN,
                shop=self.shop_a,
                created_by=self.profile,
            ),
            item=self.item,
            quantity=3,
        )
        StockMovementLine.objects.create(
            movement=StockMovement.objects.create(
                movement_type=StockMovementType.OUT,
                shop=self.shop_a,
                created_by=self.profile,
            ),
            item=self.item,
            quantity=2,
        )
        events, units_in, units_out, *_ = _build_movement_timeline(
            shop_ids=[self.shop_a.pk, self.shop_b.pk],
            day_start=self.day_start,
            day_end=self.day_end,
            item_mode="all",
            selected_categories=[],
            selected_item_ids=[],
            report_items=[self.item],
            event_filter="all",
        )
        types = {event["event_type"] for event in events}
        self.assertIn("in", types)
        self.assertIn("out", types)
        self.assertEqual(units_in, 3)
        self.assertEqual(units_out, 2)

    def test_transfer_event_filter_matches_fulfilled_only(self):
        from items.views import MOVEMENT_EVENT_FILTER_TYPES, _filter_movement_events

        events = [
            {"event_type": "request", "quantity": 2},
            {"event_type": "transfer_fulfilled", "quantity": 3},
        ]
        filtered = _filter_movement_events(events, "transfer")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["event_type"], "transfer_fulfilled")
        self.assertEqual(
            MOVEMENT_EVENT_FILTER_TYPES["transfer"],
            frozenset({"transfer_fulfilled"}),
        )

    def test_timeline_display_keeps_transfer_out(self):
        from items.views import _filter_timeline_display_events

        events = [
            {"event_type": "request", "quantity": 1},
            {
                "event_type": "transfer_fulfilled",
                "quantity": 2,
                "transfer_direction": "out",
            },
            {
                "event_type": "transfer_fulfilled",
                "quantity": 3,
                "transfer_direction": "in",
            },
        ]
        kept = _filter_timeline_display_events(events)
        self.assertEqual(len(kept), 2)
        self.assertEqual(
            {row["transfer_direction"] for row in kept},
            {"in", "out"},
        )

    def test_timeline_transfer_shows_requester_and_receiver(self):
        from items.views import _build_movement_timeline, _filter_timeline_display_events

        receiver = User.objects.create_user(
            username="840012",
            password="report-pass",
            email="stock-receiver@test.local",
            first_name="STOCK",
            last_name="RECEIVER",
            is_active=True,
        )
        receiver_profile = EmployeeProfile.objects.create(
            user=receiver,
            employee_id="840012",
            phone_country_code="+254",
            phone_number="700000942",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
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
            responded_by=receiver_profile,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            quantity=2,
        )
        events, *_ = _build_movement_timeline(
            shop_ids=[self.shop_a.pk],
            day_start=self.day_start,
            day_end=self.day_end,
            item_mode="all",
            selected_categories=[],
            selected_item_ids=[],
            report_items=[self.item],
        )
        transfers = [
            event
            for event in _filter_timeline_display_events(events)
            if event.get("event_type") == "transfer_fulfilled"
        ]
        self.assertEqual(len(transfers), 1)
        event = transfers[0]
        self.assertEqual(event["requested_by"], "STOCK REPORT")
        self.assertEqual(event["received_by"], "STOCK RECEIVER")
        self.assertIn("Requested: STOCK REPORT", event["by"])
        self.assertIn("Received: STOCK RECEIVER", event["by"])

    def test_timeline_sale_keeps_item_after_rename(self):
        from shops.models import (
            ShopPaymentMethod,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptLine,
            ShopReceiptStatus,
        )
        from items.views import (
            _build_movement_timeline,
            _filter_movement_events_by_search,
            _item_qty_summary_by_type_rows,
        )

        receipt = ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="REP-RENAME-1",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("300.00"),
            total=Decimal("300.00"),
            amount_paid=Decimal("300.00"),
            cash_amount=Decimal("300.00"),
            created_by=self.profile,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name="OLD CABLE NAME",
            quantity=2,
            unit_price=Decimal("150.00"),
            line_total=Decimal("300.00"),
        )
        self.item.name = "NEW CABLE NAME"
        self.item.save(update_fields=["name", "updated_at"])

        events, *_ = _build_movement_timeline(
            shop_ids=[self.shop_a.pk],
            day_start=self.day_start,
            day_end=self.day_end,
            item_mode="all",
            selected_categories=[],
            selected_item_ids=[],
            report_items=[self.item],
        )
        sales = [event for event in events if event.get("event_type") == "sale"]
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["item_id"], self.item.pk)
        self.assertEqual(sales[0]["item_name"], "NEW CABLE NAME")
        self.assertEqual(sales[0]["item_name_snapshot"], "OLD CABLE NAME")

        searched = _filter_movement_events_by_search(events, "NEW CABLE")
        self.assertEqual(
            [event["event_type"] for event in searched if event.get("event_type") == "sale"],
            ["sale"],
        )
        old_searched = _filter_movement_events_by_search(events, "OLD CABLE")
        self.assertEqual(
            [event["event_type"] for event in old_searched if event.get("event_type") == "sale"],
            ["sale"],
        )

        summary = _item_qty_summary_by_type_rows(
            [
                {
                    "event_type": "in",
                    "item_id": self.item.pk,
                    "item_name": "NEW CABLE NAME",
                    "item_category": self.item.category,
                    "quantity": 5,
                },
                {
                    "event_type": "sale",
                    "item_id": self.item.pk,
                    "item_name": "OLD CABLE NAME",
                    "item_category": self.item.category,
                    "quantity": 2,
                },
            ]
        )
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["in"], 5)
        self.assertEqual(summary[0]["sale"], 2)

    def test_update_item_renames_receipt_and_pos_snapshots(self):
        from pos.models import Product, Sale, SaleLine, SaleSource
        from shops.models import (
            ShopPaymentMethod,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptLine,
            ShopReceiptStatus,
        )
        from items.services import update_item

        receipt = ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="REP-RENAME-2",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("150.00"),
            total=Decimal("150.00"),
            amount_paid=Decimal("150.00"),
            cash_amount=Decimal("150.00"),
            created_by=self.profile,
            status=ShopReceiptStatus.ACTIVE,
        )
        line = ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=1,
            unit_price=Decimal("150.00"),
            line_total=Decimal("150.00"),
        )
        product = Product.objects.create(
            sku="POS-RENAME-1",
            name=self.item.name,
            price=Decimal("150.00"),
            stock=1,
        )
        sale = Sale.objects.create(
            client_id="rename-sale-1",
            employee=self.profile,
            total=Decimal("150.00"),
            source=SaleSource.ONLINE,
            sold_at=self.now,
        )
        pos_line = SaleLine.objects.create(
            sale=sale,
            product=product,
            product_sku=product.sku,
            product_name=self.item.name,
            quantity=1,
            unit_price=Decimal("150.00"),
            line_total=Decimal("150.00"),
        )

        update_item(
            self.item,
            {
                "category": self.item.category,
                "name": "RENAMED REPORT CABLE",
                "description": "",
                "minimum_selling_price": "100.00",
                "shop_price": "150.00",
                "pricing_mode": "single",
            },
            {},
        )
        self.item.refresh_from_db()
        line.refresh_from_db()
        pos_line.refresh_from_db()
        self.assertEqual(self.item.name, "RENAMED REPORT CABLE")
        self.assertEqual(line.item_name, "RENAMED REPORT CABLE")
        self.assertEqual(pos_line.product_name, "RENAMED REPORT CABLE")

    def test_timeline_groups_sale_and_return_by_receipt(self):
        from datetime import timedelta

        from django.utils import timezone
        from items.views import (
            _filter_movement_events,
            _group_timeline_events_by_receipt,
        )

        now = timezone.now()
        sale = {
            "happened_at": now,
            "event_type": "sale",
            "event_label": "Stock sale",
            "item_name": "Item A",
            "item_category": "Phones",
            "from_label": "Shop",
            "to_label": "Client",
            "by": "Cashier",
            "quantity": 2,
            "receipt_number": "RCP-100",
            "receipt_status": "partial_return",
            "serial_numbers": ["SN1"],
            "note": "",
        }
        other_sale = {
            "happened_at": now + timedelta(minutes=5),
            "event_type": "sale",
            "event_label": "Stock sale",
            "item_name": "Item B",
            "item_category": "Phones",
            "from_label": "Shop",
            "to_label": "Client",
            "by": "Cashier",
            "quantity": 1,
            "receipt_number": "RCP-100",
            "receipt_status": "partial_return",
            "serial_numbers": [],
            "note": "",
        }
        returned = {
            "happened_at": now + timedelta(days=2),
            "event_type": "return",
            "event_label": "Return",
            "item_name": "Item A",
            "item_category": "Phones",
            "from_label": "Client",
            "to_label": "Shop",
            "by": "Cashier",
            "quantity": 1,
            "receipt_number": "RCP-100",
            "receipt_status": "partial_return",
            "serial_numbers": ["SN1"],
            "note": "Return on RCP-100",
        }
        stock_out = {
            "happened_at": now + timedelta(hours=1),
            "event_type": "out",
            "event_label": "Stock out",
            "item_name": "Item C",
            "item_category": "Accessories",
            "from_label": "Shop",
            "to_label": "Damage",
            "by": "Manager",
            "quantity": 1,
            "receipt_number": "",
            "receipt_status": "",
            "serial_numbers": [],
            "note": "",
        }

        groups = _group_timeline_events_by_receipt(
            [sale, stock_out, other_sale, returned]
        )
        self.assertEqual(len(groups), 2)
        receipt_group = groups[0]
        self.assertTrue(receipt_group["is_receipt_group"])
        self.assertTrue(receipt_group["is_multi"])
        self.assertEqual(receipt_group["receipt_number"], "RCP-100")
        self.assertEqual(
            [row["event_type"] for row in receipt_group["activities"]],
            ["sale", "sale", "return"],
        )
        self.assertEqual(groups[1]["activities"][0]["event_type"], "out")

        sale_filtered = _filter_movement_events(
            [sale, other_sale, returned, stock_out], "sale"
        )
        self.assertEqual(
            {row["event_type"] for row in sale_filtered},
            {"sale", "return"},
        )
        self.assertNotIn("out", {row["event_type"] for row in sale_filtered})

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

    def test_public_image_url_uses_app_route_when_file_exists(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from urllib.parse import quote

        upload = SimpleUploadedFile(
            "photo.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg"
        )
        self.item.image = upload
        self.item.save(update_fields=["image"])
        token = quote(self.item.image.name.rsplit("/", 1)[-1], safe="")
        self.assertEqual(
            self.item.public_image_url(),
            f"{reverse('core:item_photo', kwargs={'item_id': self.item.pk})}?v={token}",
        )

    def test_update_item_replaces_image_and_busts_url(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from items.services import update_item

        first = SimpleUploadedFile(
            "first.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg"
        )
        self.item.image = first
        self.item.save(update_fields=["image"])
        old_name = self.item.image.name
        old_url = self.item.public_image_url()

        second = SimpleUploadedFile(
            "second.jpg", b"\xff\xd8\xff\xd9\x00\x01", content_type="image/jpeg"
        )
        update_item(
            self.item,
            {
                "category": self.item.category,
                "name": self.item.name,
                "description": "",
                "minimum_selling_price": "100.00",
                "shop_price": "150.00",
                "pricing_mode": "single",
            },
            {"image": second},
        )
        self.item.refresh_from_db()
        self.assertTrue(self.item.image)
        self.assertNotEqual(self.item.image.name, old_name)
        self.assertNotEqual(self.item.public_image_url(), old_url)
        self.assertFalse(self.item.image.storage.exists(old_name))

    def test_item_photo_view_serves_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        upload = SimpleUploadedFile(
            "photo.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg"
        )
        self.item.image = upload
        self.item.save(update_fields=["image"])
        url = reverse("core:item_photo", kwargs={"item_id": self.item.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")

    def test_item_management_catalog_omits_missing_file(self):
        from items.services import build_item_management_catalog_page

        self.item.image = "items/images/30.jpeg"
        self.item.save(update_fields=["image"])
        payload = build_item_management_catalog_page(q="MISSING PHOTO")
        rows = [row for row in payload["items"] if row["id"] == self.item.pk]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_url"], "")


class ShopPriceIsolationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840031",
            password="price-pass",
            email="price-isolation@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840031",
            phone_country_code="+254",
            phone_number="700000961",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="PRICE SHOP A",
            location="NAIROBI",
            email="price-a@test.local",
            phone_number="0700000961",
            login_code="840131",
            password_hash="x",
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="PRICE SHOP B",
            location="MOMBASA",
            email="price-b@test.local",
            phone_number="0700000962",
            login_code="840132",
            password_hash="x",
            created_by=self.profile,
        )

    def test_create_single_mode_writes_per_shop_prices(self):
        from items.models import ShopItemPrice
        from items.services import create_item

        item = create_item(
            self.profile,
            {
                "category": "DRINKS",
                "name": "SODA 500ML",
                "description": "",
                "minimum_selling_price": "100",
                "shop_price": "150",
                "pricing_mode": "single",
            },
            {},
        )
        self.assertTrue(item.use_individual_shop_prices)
        prices = {
            row.shop_id: row.price
            for row in ShopItemPrice.objects.filter(item=item)
        }
        self.assertEqual(prices[self.shop_a.pk], Decimal("150.00"))
        self.assertEqual(prices[self.shop_b.pk], Decimal("150.00"))
        self.assertEqual(item.price_for_shop(self.shop_a), Decimal("150.00"))
        self.assertEqual(item.price_for_shop(self.shop_b), Decimal("150.00"))

    def test_limited_single_edit_does_not_change_other_shop(self):
        from items.models import Item, ShopItemPrice
        from items.services import update_item

        item = Item.objects.create(
            category="DRINKS",
            name="JUICE 1L",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            use_individual_shop_prices=False,
            created_by=self.profile,
        )
        ShopItemPrice.objects.create(shop=self.shop_a, item=item, price=Decimal("150.00"))
        ShopItemPrice.objects.create(shop=self.shop_b, item=item, price=Decimal("180.00"))

        update_item(
            item,
            {
                "category": "DRINKS",
                "name": "JUICE 1L",
                "description": "",
                "minimum_selling_price": "100",
                "shop_price": "200",
                "pricing_mode": "single",
            },
            {},
            editable_shop_ids={self.shop_a.pk},
        )
        item.refresh_from_db()
        self.assertTrue(item.use_individual_shop_prices)
        self.assertEqual(item.price_for_shop(self.shop_a), Decimal("200.00"))
        self.assertEqual(item.price_for_shop(self.shop_b), Decimal("180.00"))
        self.assertEqual(
            ShopItemPrice.objects.get(item=item, shop=self.shop_b).price,
            Decimal("180.00"),
        )

    def test_limited_individual_edit_preserves_other_shop_override(self):
        from items.models import Item, ShopItemPrice
        from items.services import update_item

        item = Item.objects.create(
            category="DRINKS",
            name="WATER 1L",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("80.00"),
            use_individual_shop_prices=True,
            created_by=self.profile,
        )
        ShopItemPrice.objects.create(shop=self.shop_a, item=item, price=Decimal("80.00"))
        ShopItemPrice.objects.create(shop=self.shop_b, item=item, price=Decimal("95.00"))

        update_item(
            item,
            {
                "category": "DRINKS",
                "name": "WATER 1L",
                "description": "",
                "minimum_selling_price": "50",
                "pricing_mode": "individual",
                f"shop_price_{self.shop_a.pk}": "120",
            },
            {},
            editable_shop_ids={self.shop_a.pk},
        )
        item.refresh_from_db()
        self.assertEqual(item.price_for_shop(self.shop_a), Decimal("120.00"))
        self.assertEqual(item.price_for_shop(self.shop_b), Decimal("95.00"))
        self.assertEqual(ShopItemPrice.objects.filter(item=item).count(), 2)

    def test_single_edit_with_all_shops_keeps_other_shop_override(self):
        from items.models import Item, ShopItemPrice
        from items.services import update_item

        item = Item.objects.create(
            category="DRINKS",
            name="TEA 500ML",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            use_individual_shop_prices=True,
            created_by=self.profile,
        )
        ShopItemPrice.objects.create(shop=self.shop_a, item=item, price=Decimal("100.00"))
        ShopItemPrice.objects.create(shop=self.shop_b, item=item, price=Decimal("130.00"))

        update_item(
            item,
            {
                "category": "DRINKS",
                "name": "TEA 500ML",
                "description": "",
                "minimum_selling_price": "50",
                "shop_price": "115",
                "pricing_mode": "single",
            },
            {},
            editable_shop_ids={self.shop_a.pk, self.shop_b.pk},
        )
        item.refresh_from_db()
        self.assertEqual(item.price_for_shop(self.shop_a), Decimal("115.00"))
        self.assertEqual(item.price_for_shop(self.shop_b), Decimal("130.00"))


class ItemPriceListTests(TestCase):
    def setUp(self):
        from items.models import Item, ShopItemPrice

        self.password = "price-list-pass"
        self.user = User.objects.create_user(
            username="840033",
            password=self.password,
            email="price-list@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840033",
            phone_country_code="+254",
            phone_number="700000963",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="PRICE LIST SHOP",
            location="NAIROBI",
            email="price-list@test.local",
            phone_number="0700000963",
            login_code="840133",
            password_hash="x",
            created_by=self.profile,
        )
        self.item = Item.objects.create(
            category="DRINKS",
            name="JUICE 1L",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            use_individual_shop_prices=True,
            created_by=self.profile,
        )
        ShopItemPrice.objects.create(
            shop=self.shop,
            item=self.item,
            price=Decimal("110.00"),
        )

    def test_build_item_price_list_includes_min_and_shop_prices(self):
        from items.services import build_item_price_list_document

        document = build_item_price_list_document(shops=[self.shop])
        rows = [
            row
            for group in document["categories"]
            for row in group["rows"]
            if row["name"] == "JUICE 1L"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["min_price"], "80.00")
        self.assertEqual(rows[0]["shop_price"], "110.00")

    def test_price_list_page_and_pdf_download(self):
        from django.urls import reverse

        self.client.login(username="840033", password=self.password)
        page_url = reverse(
            "employees:item_management_price_list",
            kwargs={"role_segment": "it-support"},
        )
        page = self.client.get(page_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "JUICE 1L")
        self.assertContains(page, "Min price")
        self.assertContains(page, "Shop price")

        pdf = self.client.get(page_url, {"download": "1"})
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertIn("item-price-list-", pdf["Content-Disposition"])
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_item_management_shows_download_price_list(self):
        self.client.login(username="840033", password=self.password)
        response = self.client.get("/it-support/item-management/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download price list")
        self.assertContains(response, "/it-support/item-management/price-list/")


class ItemActivityAuditTests(TestCase):
    def setUp(self):
        self.password = "activity-pass"
        self.user = User.objects.create_user(
            username="840041",
            password=self.password,
            email="item-activity@test.local",
            first_name="Audit",
            last_name="User",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840041",
            phone_country_code="+254",
            phone_number="700000971",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="AUDIT SHOP A",
            location="NAIROBI",
            email="audit-a@test.local",
            phone_number="0700000971",
            login_code="840141",
            password_hash="x",
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="AUDIT SHOP B",
            location="KISUMU",
            email="audit-b@test.local",
            phone_number="0700000972",
            login_code="840142",
            password_hash="x",
            created_by=self.profile,
        )

    def test_create_edit_suspend_delete_write_activity_events(self):
        from items.models import ItemActivityEvent, ItemActivityKind
        from items.services import create_item, delete_item, toggle_item_suspended, update_item

        item = create_item(
            self.profile,
            {
                "category": "DRINKS",
                "name": "SODA AUDIT",
                "description": "",
                "minimum_selling_price": "100",
                "shop_price": "150",
                "pricing_mode": "single",
            },
            {},
        )
        self.assertTrue(
            ItemActivityEvent.objects.filter(
                item=item, kind=ItemActivityKind.REGISTERED, actor=self.profile
            ).exists()
        )

        update_item(
            item,
            {
                "category": "DRINKS",
                "name": "SODA AUDIT XL",
                "description": "",
                "minimum_selling_price": "100",
                "shop_price": "160",
                "pricing_mode": "single",
            },
            {},
            actor=self.profile,
            editable_shop_ids={self.shop_a.pk},
        )
        edited = ItemActivityEvent.objects.filter(
            item=item, kind=ItemActivityKind.EDITED, actor=self.profile
        ).latest("pk")
        self.assertIn("name", edited.detail.lower())

        toggle_item_suspended(item, actor=self.profile)
        self.assertTrue(
            ItemActivityEvent.objects.filter(
                item=item, kind=ItemActivityKind.SUSPENDED, actor=self.profile
            ).exists()
        )

        delete_item(item, actor=self.profile)
        deleted = ItemActivityEvent.objects.filter(
            kind=ItemActivityKind.DELETED, actor=self.profile, item_name="SODA AUDIT XL"
        ).latest("pk")
        self.assertIsNone(deleted.item_id)

    def test_activity_audits_page_filters_by_employee_and_period(self):
        from django.utils import timezone

        from items.activity_audit import log_item_activity
        from items.models import ItemActivityKind

        now = timezone.now()
        log_item_activity(
            kind=ItemActivityKind.REGISTERED,
            actor=self.profile,
            item_name="FILTER ITEM",
            item_category="TOOLS",
            detail="Registered item",
            shop_ids=[self.shop_a.pk],
            occurred_at=now,
        )
        other_user = User.objects.create_user(
            username="840042",
            password="x",
            email="other-activity@test.local",
            is_active=True,
        )
        other = EmployeeProfile.objects.create(
            user=other_user,
            employee_id="840042",
            phone_country_code="+254",
            phone_number="700000973",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        log_item_activity(
            kind=ItemActivityKind.EDITED,
            actor=other,
            item_name="OTHER ITEM",
            item_category="TOOLS",
            detail="Updated name",
            shop_ids=[self.shop_b.pk],
            occurred_at=now,
        )

        self.client.login(username="840041", password=self.password)
        response = self.client.get(
            "/it-support/item-management/",
            {
                "mode": "activity-audits",
                "range": "day",
                "date": timezone.localdate().isoformat(),
                "employee_id": self.profile.pk,
                "shop_id": self.shop_a.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity analytics")
        self.assertContains(response, "FILTER ITEM")
        self.assertNotContains(response, "OTHER ITEM")
        self.assertContains(response, "Activity analytics")


class StockActivityAnalyticsTests(TestCase):
    def setUp(self):
        from items.models import Item

        self.password = "stock-act-pass"
        self.user = User.objects.create_user(
            username="840051",
            password=self.password,
            email="stock-activity@test.local",
            first_name="Stock",
            last_name="Audit",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840051",
            phone_country_code="+254",
            phone_number="700000981",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="STOCK ACT SHOP",
            location="NAIROBI",
            email="stock-act@test.local",
            phone_number="0700000981",
            login_code="840151",
            password_hash="x",
            created_by=self.profile,
        )
        self.item = Item.objects.create(
            category="TOOLS",
            name="STOCK ACT ITEM",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            created_by=self.profile,
        )

    def test_stock_activity_page_lists_movements_and_filters(self):
        from django.utils import timezone

        from items.models import StockMovement, StockMovementLine, StockMovementType
        from items.stock_activity_audit import build_stock_activity_audits

        movement = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            shop=self.shop,
            created_by=self.profile,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            quantity=3,
        )
        other_user = User.objects.create_user(
            username="840052",
            password="x",
            email="stock-other@test.local",
            is_active=True,
        )
        other = EmployeeProfile.objects.create(
            user=other_user,
            employee_id="840052",
            phone_country_code="+254",
            phone_number="700000982",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        StockMovement.objects.create(
            movement_type=StockMovementType.OUT,
            shop=self.shop,
            created_by=other,
        )

        page = build_stock_activity_audits(
            profile=self.profile,
            shops=[self.shop],
            employee_id=self.profile.pk,
            kind="in",
        )
        self.assertEqual(page["event_count"], 1)
        self.assertIn("Stock In", page["rows"][0]["kind_label"])

        self.client.login(username="840051", password=self.password)
        response = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "activity-audits",
                "range": "day",
                "date": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity analytics")
        self.assertContains(response, "STOCK ACT SHOP")


class StockSerialMovementsShopFilterTests(TestCase):
    def setUp(self):
        self.password = "serial-mov-pass"
        self.user = User.objects.create_user(
            username="840031",
            password=self.password,
            email="serial-mov@test.local",
            first_name="SERIAL",
            last_name="MOV",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840031",
            phone_country_code="+254",
            phone_number="700000943",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="SERIAL SHOP A",
            location="NAIROBI",
            email="serial-a@test.local",
            phone_number="0700000943",
            login_code="840131",
            password_hash="x",
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="SERIAL SHOP B",
            location="MOMBASA",
            email="serial-b@test.local",
            phone_number="0700000944",
            login_code="840132",
            password_hash="x",
            created_by=self.profile,
        )
        from items.models import Item, StockMovement, StockMovementLine, StockMovementType

        self.item = Item.objects.create(
            category="PHONES",
            name="SERIAL HANDSET",
            minimum_selling_price=Decimal("1000.00"),
            shop_price=Decimal("1500.00"),
            track_serial_number=True,
            created_by=self.profile,
        )
        for shop, serial in ((self.shop_a, "SNA-001"), (self.shop_b, "SNB-001")):
            movement = StockMovement.objects.create(
                movement_type=StockMovementType.IN,
                shop=shop,
                created_by=self.profile,
            )
            StockMovementLine.objects.create(
                movement=movement,
                item=self.item,
                quantity=1,
                serial_numbers=[serial],
            )

    def test_shop_dropdown_and_filter(self):
        self.client.login(username="840031", password=self.password)
        year = timezone.localdate().year
        all_resp = self.client.get(
            "/it-support/stock-management/",
            {"mode": "serial-movements", "range": "year", "year": year},
        )
        self.assertEqual(all_resp.status_code, 200)
        self.assertContains(all_resp, 'name="shop_id"')
        self.assertContains(all_resp, "All shops")
        self.assertContains(all_resp, "SERIAL SHOP A")
        self.assertContains(all_resp, "SERIAL SHOP B")
        self.assertContains(all_resp, "SNA-001")
        self.assertContains(all_resp, "SNB-001")
        self.assertEqual(all_resp.context["selected_shop_ids"], set())

        filtered = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "serial-movements",
                "range": "year",
                "year": year,
                "shop_id": self.shop_a.pk,
            },
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.context["selected_shop_ids"], {self.shop_a.pk})
        self.assertContains(filtered, "SNA-001")
        self.assertNotContains(filtered, "SNB-001")
        serials = [row["serial_number"] for row in filtered.context["rows"]]
        self.assertEqual(serials, ["SNA-001"])

    def test_status_filter(self):
        from items.models import StockMovement, StockMovementLine, StockMovementType

        out_movement = StockMovement.objects.create(
            movement_type=StockMovementType.OUT,
            shop=self.shop_a,
            created_by=self.profile,
        )
        StockMovementLine.objects.create(
            movement=out_movement,
            item=self.item,
            quantity=1,
            serial_numbers=["SNA-OUT-1"],
        )

        self.client.login(username="840031", password=self.password)
        year = timezone.localdate().year
        page = self.client.get(
            "/it-support/stock-management/",
            {"mode": "serial-movements", "range": "year", "year": year},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="event_type"')
        self.assertContains(page, "Status")
        self.assertEqual(page.context["event_filter"], "all")
        self.assertContains(page, "SNA-001")
        self.assertContains(page, "SNA-OUT-1")

        stock_in = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "serial-movements",
                "range": "year",
                "year": year,
                "event_type": "in",
            },
        )
        self.assertEqual(stock_in.status_code, 200)
        self.assertEqual(stock_in.context["event_filter"], "in")
        self.assertContains(stock_in, "SNA-001")
        self.assertNotContains(stock_in, "SNA-OUT-1")

        stock_out = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "serial-movements",
                "range": "year",
                "year": year,
                "event_type": "out",
            },
        )
        self.assertEqual(stock_out.status_code, 200)
        self.assertEqual(stock_out.context["event_filter"], "out")
        self.assertContains(stock_out, "SNA-OUT-1")
        self.assertNotContains(stock_out, "SNA-001")


class StockSerialDetailShopFilterTests(TestCase):
    def setUp(self):
        self.password = "serial-detail-pass"
        self.user = User.objects.create_user(
            username="840032",
            password=self.password,
            email="serial-detail@test.local",
            first_name="SERIAL",
            last_name="DETAIL",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840032",
            phone_country_code="+254",
            phone_number="700000945",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="DETAIL SHOP A",
            location="NAIROBI",
            email="detail-a@test.local",
            phone_number="0700000945",
            login_code="840145",
            password_hash="x",
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="DETAIL SHOP B",
            location="MOMBASA",
            email="detail-b@test.local",
            phone_number="0700000946",
            login_code="840146",
            password_hash="x",
            created_by=self.profile,
        )
        from items.models import Item, ItemSerial, ItemSerialStatus

        self.item = Item.objects.create(
            category="PHONES",
            name="DETAIL HANDSET",
            minimum_selling_price=Decimal("1000.00"),
            shop_price=Decimal("1500.00"),
            track_serial_number=True,
            created_by=self.profile,
        )
        ItemSerial.objects.create(
            item=self.item,
            shop=self.shop_a,
            serial_number="SDA-IN-1",
            is_available=True,
        )
        ItemSerial.objects.create(
            item=self.item,
            shop=self.shop_b,
            serial_number="SDB-IN-1",
            is_available=True,
        )
        ItemSerial.objects.create(
            item=self.item,
            shop=self.shop_a,
            serial_number="SDA-OUT-1",
            is_available=False,
            status_override=ItemSerialStatus.OUT,
        )
        ItemSerial.objects.create(
            item=self.item,
            shop=self.shop_b,
            serial_number="SDB-OUT-1",
            is_available=False,
            status_override=ItemSerialStatus.OUT,
        )
        self.detail_url = f"/it-support/stock-management/serials/{self.item.pk}/"

    def test_shop_dropdown_and_filter(self):
        self.client.login(username="840032", password=self.password)
        all_resp = self.client.get(self.detail_url, {"status": "out", "q": ""})
        self.assertEqual(all_resp.status_code, 200)
        self.assertContains(all_resp, 'name="shop_id"')
        self.assertContains(all_resp, "All shops")
        self.assertContains(all_resp, "DETAIL SHOP A")
        self.assertContains(all_resp, "DETAIL SHOP B")
        self.assertEqual(all_resp.context["selected_shop_ids"], set())
        self.assertContains(all_resp, "SDA-OUT-1")
        self.assertContains(all_resp, "SDB-OUT-1")
        self.assertNotContains(all_resp, "SDA-IN-1")
        self.assertNotContains(all_resp, "SDB-IN-1")
        self.assertEqual(all_resp.context["out_count"], 2)

        filtered = self.client.get(
            self.detail_url,
            {"status": "out", "q": "", "shop_id": self.shop_a.pk},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.context["selected_shop_ids"], {self.shop_a.pk})
        self.assertContains(filtered, "SDA-OUT-1")
        self.assertNotContains(filtered, "SDB-OUT-1")
        self.assertEqual(filtered.context["out_count"], 1)
        self.assertEqual(filtered.context["in_stock_count"], 1)
        serials = [row["serial_number"] for row in filtered.context["rows"]]
        self.assertEqual(serials, ["SDA-OUT-1"])


class DecimalStockQuantityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840099",
            password="decimal-pass",
            email="decimal-stock@test.local",
            first_name="DECIMAL",
            last_name="STOCK",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840099",
            phone_country_code="+254",
            phone_number="700000999",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="DECIMAL SHOP",
            location="NAIROBI",
            email="decimal-shop@test.local",
            phone_number="0700000999",
            login_code="840999",
            password_hash="x",
            created_by=self.profile,
        )
        from items.models import Item
        from shops.models import CompanyStockSettings

        settings_row, _ = CompanyStockSettings.objects.get_or_create(pk=1)
        settings_row.require_buying_price_on_in = True
        settings_row.require_supplier_on_in = False
        settings_row.require_payment_status_on_in = False
        settings_row.require_reason_on_out = False
        settings_row.require_refund_on_out = False
        settings_row.save()

        self.item = Item.objects.create(
            category="BULK",
            name="DECIMAL RICE",
            minimum_selling_price=Decimal("20.00"),
            shop_price=Decimal("30.00"),
            created_by=self.profile,
        )
        self.serial_item = Item.objects.create(
            category="PHONES",
            name="SERIAL PHONE",
            minimum_selling_price=Decimal("1000.00"),
            shop_price=Decimal("1200.00"),
            track_serial_number=True,
            created_by=self.profile,
        )

    def test_decimal_stock_in_updates_qty_and_average_cost(self):
        from django.http import QueryDict

        from items.models import ShopStock, StockMovementType
        from items.services import apply_stock_movement

        data = QueryDict(mutable=True)
        data.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.item.pk),
                "quantity": "1.5",
                "buying_price": "12.50",
            }
        )
        apply_stock_movement(self.profile, StockMovementType.IN, data)

        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.item.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("1.500"))
        self.assertEqual(self.item.stock, Decimal("1.500"))
        self.assertEqual(stock.average_cost, Decimal("12.50"))

    def test_decimal_stock_out_after_decimal_in(self):
        from django.http import QueryDict

        from items.models import ShopStock, StockMovementType
        from items.services import apply_stock_movement

        inbound = QueryDict(mutable=True)
        inbound.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.item.pk),
                "quantity": "1.5",
                "buying_price": "12.50",
            }
        )
        apply_stock_movement(self.profile, StockMovementType.IN, inbound)

        outbound = QueryDict(mutable=True)
        outbound.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.item.pk),
                "quantity": "0.5",
            }
        )
        apply_stock_movement(self.profile, StockMovementType.OUT, outbound)

        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.item.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("1.000"))
        self.assertEqual(self.item.stock, Decimal("1.000"))

    def test_serial_stock_in_stays_whole_units_from_serial_count(self):
        from django.core.exceptions import ValidationError
        from django.http import QueryDict

        from items.models import ShopStock, StockMovementType
        from items.services import apply_stock_movement

        missing_serials = QueryDict(mutable=True)
        missing_serials.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.serial_item.pk),
                "quantity": "1.5",
                "buying_price": "900.00",
            }
        )
        with self.assertRaises(ValidationError):
            apply_stock_movement(self.profile, StockMovementType.IN, missing_serials)

        too_precise = QueryDict(mutable=True)
        too_precise.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.item.pk),
                "quantity": "1.2345",
                "buying_price": "12.50",
            }
        )
        with self.assertRaises(ValidationError):
            apply_stock_movement(self.profile, StockMovementType.IN, too_precise)

        good = QueryDict(mutable=True)
        good.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.serial_item.pk),
                "quantity": "1",
                "serial_numbers": "SN-A1",
                "buying_price": "900.00",
            }
        )
        apply_stock_movement(self.profile, StockMovementType.IN, good)
        stock = ShopStock.objects.get(shop=self.shop, item=self.serial_item)
        self.assertEqual(stock.quantity, Decimal("1.000"))

    def test_serial_transfer_requires_selected_serials(self):
        from django.core.exceptions import ValidationError
        from django.http import QueryDict

        from items.models import (
            ItemSerial,
            ShopStock,
            StockMovementType,
            StockRequestStatus,
        )
        from items.services import apply_stock_movement, respond_to_stock_request

        destination = Shop.objects.create(
            name="DECIMAL DEST",
            location="MOMBASA",
            email="decimal-dest@test.local",
            phone_number="0700000998",
            login_code="840998",
            password_hash="x",
            created_by=self.profile,
        )
        inbound = QueryDict(mutable=True)
        inbound.update(
            {
                "shop_id": str(self.shop.pk),
                "item_id": str(self.serial_item.pk),
                "serial_numbers": "SN-TX-1\nSN-TX-2",
                "buying_price": "900.00",
            }
        )
        apply_stock_movement(self.profile, StockMovementType.IN, inbound)
        self.assertEqual(
            ShopStock.objects.get(shop=self.shop, item=self.serial_item).quantity,
            Decimal("2.000"),
        )

        missing = QueryDict(mutable=True)
        missing.update(
            {
                "shop_id": str(self.shop.pk),
                "requested_from_shop_id": str(destination.pk),
                "item_id": str(self.serial_item.pk),
                "quantity": "2",
            }
        )
        with self.assertRaises(ValidationError):
            apply_stock_movement(self.profile, StockMovementType.REQUEST, missing)

        transfer = QueryDict(mutable=True)
        transfer.update(
            {
                "shop_id": str(self.shop.pk),
                "requested_from_shop_id": str(destination.pk),
                "item_id": str(self.serial_item.pk),
                "serial_numbers": "SN-TX-1\nSN-TX-2",
            }
        )
        movement = apply_stock_movement(
            self.profile, StockMovementType.REQUEST, transfer
        )
        line = movement.lines.get()
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(line.serial_numbers, ["SN-TX-1", "SN-TX-2"])
        self.assertEqual(movement.request_status, StockRequestStatus.PENDING)
        # Pending transfer does not move stock yet.
        self.assertEqual(
            ShopStock.objects.get(shop=self.shop, item=self.serial_item).quantity,
            Decimal("2.000"),
        )

        respond_to_stock_request(
            movement=movement,
            profile=self.profile,
            decision="accept",
            login_code=self.profile.employee_id,
            quantities_by_line={str(line.pk): "2"},
        )
        self.assertEqual(
            ShopStock.objects.get(shop=self.shop, item=self.serial_item).quantity,
            Decimal("0.000"),
        )
        self.assertEqual(
            ShopStock.objects.get(shop=destination, item=self.serial_item).quantity,
            Decimal("2.000"),
        )
        self.assertEqual(
            set(
                ItemSerial.objects.filter(
                    item=self.serial_item, shop=destination, is_available=True
                ).values_list("serial_number", flat=True)
            ),
            {"SN-TX-1", "SN-TX-2"},
        )


class RequestItemAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840088",
            password="audit-pass",
            email="request-audit@test.local",
            first_name="REQUEST",
            last_name="AUDIT",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840088",
            phone_country_code="+254",
            phone_number="700000888",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="AUDIT SHOP A",
            location="NAIROBI",
            email="audit-a@test.local",
            phone_number="0700000888",
            login_code="840888",
            password_hash="x",
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="AUDIT SHOP B",
            location="MOMBASA",
            email="audit-b@test.local",
            phone_number="0700000889",
            login_code="840889",
            password_hash="x",
            created_by=self.profile,
        )
        from items.models import Item, StockMovement, StockMovementLine, StockMovementType, StockRequestStatus

        self.item = Item.objects.create(
            category="BULK",
            name="AUDIT CABLE",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            created_by=self.profile,
        )
        self.other = Item.objects.create(
            category="BULK",
            name="OTHER CABLE",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            created_by=self.profile,
        )
        self.movement = StockMovement.objects.create(
            movement_type=StockMovementType.REQUEST,
            shop=self.shop_a,
            requested_from_shop=self.shop_b,
            request_status=StockRequestStatus.FULFILLED,
            created_by=self.profile,
            responded_by=self.profile,
            responded_at=timezone.now(),
        )
        StockMovementLine.objects.create(
            movement=self.movement,
            item=self.item,
            quantity=Decimal("2.500"),
        )
        StockMovementLine.objects.create(
            movement=self.movement,
            item=self.other,
            quantity=Decimal("1"),
        )

    def test_list_requests_filters_by_item(self):
        from items.services import list_stock_requests_for_profile

        rows = list_stock_requests_for_profile(self.profile, item_id=self.item.pk)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pk, self.movement.pk)

        empty = list_stock_requests_for_profile(self.profile, item_id=999999)
        self.assertEqual(empty, [])

    def test_build_request_item_audit_rows(self):
        from items.services import (
            build_request_item_audit_rows,
            list_stock_requests_for_profile,
        )

        requests = list_stock_requests_for_profile(self.profile, item_id=self.item.pk)
        rows, item, summary = build_request_item_audit_rows(requests, self.item.pk)
        self.assertEqual(item.pk, self.item.pk)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("2.500"))
        self.assertEqual(summary["fulfilled"], Decimal("2.500"))
        self.assertEqual(summary["count"], 1)
