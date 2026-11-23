"""Per-item sale stock tracking (deduct_stock_on_sale) on the shop page."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock, StoreStock, StoreStockMovement
from shops.models import Shop, ShopPaymentMethod, ShopReceiptKind
from shops.services import (
    _invalidate_pos_settings_cache,
    cancel_pending_shop_receipt,
    complete_shop_checkout,
    get_company_pos_settings,
    open_shop_day,
    reopen_cancelled_shop_receipt,
    return_shop_receipt_items,
)
from shops.views import build_shop_catalog_page


class SaleStockTrackingTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="770021",
            password="pass",
            email="sale-track@test.local",
            first_name="Sale",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="770021",
            phone_country_code="+254",
            phone_number="700007721",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Sale Track Shop",
            location="Nairobi",
            email="sale-track@test.local",
            phone_number="0700007721",
            login_code="770021",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        pos = get_company_pos_settings()
        if not pos.enable_stock_tracking:
            pos.enable_stock_tracking = True
            pos.save(update_fields=["enable_stock_tracking", "updated_at"])
        self.item = Item.objects.create(
            name="Tracked Bread",
            category="Bakery",
            minimum_selling_price=Decimal("40.00"),
            shop_price=Decimal("60.00"),
            stock=Decimal("10.000"),
            track_serial_number=False,
            created_by=self.profile,
        )
        self.untracked = Item.objects.create(
            name="Untracked Airtime",
            category="Services",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            stock=Decimal("5.000"),
            track_serial_number=False,
            deduct_stock_on_sale=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=Decimal("10.000"),
            average_cost=Decimal("30.00"),
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.untracked,
            quantity=Decimal("5.000"),
            average_cost=Decimal("8.00"),
        )
        StoreStock.objects.create(item=self.item, quantity=Decimal("10.000"))
        StoreStock.objects.create(item=self.untracked, quantity=Decimal("5.000"))
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "770021",
            },
        )

    def _checkout(self, item, qty="3", price="60.00", kind=ShopReceiptKind.SALE):
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": kind,
                "payment_method": ShopPaymentMethod.CASH,
                "login_code": "770021",
                "lines": [{"id": item.pk, "qty": qty, "price": price}],
            },
        )

    def _shop_qty(self, item):
        return ShopStock.objects.get(shop=self.shop, item=item).quantity

    def test_sale_deducts_tracked_item(self):
        self._checkout(self.item, qty="3")
        self.assertEqual(self._shop_qty(self.item), Decimal("7.000"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("7.000"))

    def test_sale_on_untracked_item_does_not_deduct(self):
        self._checkout(self.untracked, qty="2", price="20.00")
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("5.000"))
        self.assertFalse(
            StoreStockMovement.objects.filter(item=self.untracked).exists()
        )

    def test_untracked_item_sells_past_stock_on_hand(self):
        # Only 5 on hand — an untracked item still sells 8 with no block.
        result = self._checkout(self.untracked, qty="8", price="20.00")
        self.assertEqual(result["receipt"].lines.get().quantity, Decimal("8.000"))
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))

    def test_tracked_item_still_blocks_insufficient_stock(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self._checkout(self.item, qty="11")

    def test_toggle_flag_controls_deduction(self):
        self.untracked.deduct_stock_on_sale = True
        self.untracked.save(update_fields=["deduct_stock_on_sale"])
        self._checkout(self.untracked, qty="2", price="20.00")
        self.assertEqual(self._shop_qty(self.untracked), Decimal("3.000"))

        self.untracked.deduct_stock_on_sale = False
        self.untracked.save(update_fields=["deduct_stock_on_sale"])
        self._checkout(self.untracked, qty="2", price="20.00")
        self.assertEqual(self._shop_qty(self.untracked), Decimal("3.000"))

    def _enable_tracking_and_backfill(self, item):
        from items.services import backfill_item_sale_stock

        item.deduct_stock_on_sale = True
        item.stock_count_enabled = True
        item.save(update_fields=["deduct_stock_on_sale", "stock_count_enabled"])
        return backfill_item_sale_stock(item, actor=self.profile)

    def test_backfill_deducts_untracked_credit_sale(self):
        # A tracked sale first — its POS mirror movement marks the point
        # after which un-mirrored receipt lines are known to be untracked.
        self._checkout(self.item, qty="1")
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.CREDIT,
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "770021",
                "lines": [{"id": self.untracked.pk, "qty": "2", "price": "20.00"}],
            },
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))

        summary = self._enable_tracking_and_backfill(self.untracked)
        self.assertEqual(summary["lines"], 1)
        self.assertEqual(summary["units_out"], Decimal("2"))
        self.assertEqual(self._shop_qty(self.untracked), Decimal("3.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("3.000"))
        movement = StoreStockMovement.objects.get(item=self.untracked)
        self.assertEqual(movement.movement_type, "out")
        self.assertEqual(movement.source, "pos")

    def test_backfill_skips_already_deducted_lines(self):
        self._checkout(self.item, qty="3")
        summary = self._enable_tracking_and_backfill(self.item)
        self.assertEqual(summary["lines"], 0)
        self.assertEqual(self._shop_qty(self.item), Decimal("7.000"))
        self.assertEqual(
            StoreStockMovement.objects.filter(item=self.item).count(), 1
        )

    def test_backfill_deducts_full_qty_when_return_restocked(self):
        # Untracked sale, then a tracked return: the restock added stock the
        # sale never removed, so the backfill deducts the full sold qty.
        self._checkout(self.item, qty="1")
        result = self._checkout(self.untracked, qty="4", price="20.00")
        line = result["receipt"].lines.get()
        self.untracked.deduct_stock_on_sale = True
        self.untracked.stock_count_enabled = True
        self.untracked.save(
            update_fields=["deduct_stock_on_sale", "stock_count_enabled"]
        )
        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=result["receipt"].pk,
            payload={
                "login_code": "770021",
                "reason": "Wrong item",
                "lines": [{"line_id": line.pk, "qty": "1"}],
            },
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("6.000"))

        from items.services import backfill_item_sale_stock

        summary = backfill_item_sale_stock(self.untracked, actor=self.profile)
        self.assertEqual(summary["lines"], 1)
        self.assertEqual(self._shop_qty(self.untracked), Decimal("2.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("2.000"))

    def test_backfill_adds_untracked_trade_in(self):
        self._checkout(self.item, qty="1")
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": "trade_in",
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "770021",
                "lines": [{"id": self.untracked.pk, "qty": 2, "price": "100.00"}],
            },
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))

        summary = self._enable_tracking_and_backfill(self.untracked)
        self.assertEqual(summary["lines"], 1)
        self.assertEqual(summary["units_in"], Decimal("2"))
        self.assertEqual(self._shop_qty(self.untracked), Decimal("7.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("7.000"))

    def test_return_of_untracked_item_does_not_restock(self):
        result = self._checkout(self.untracked, qty="2", price="20.00")
        line = result["receipt"].lines.get()
        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=result["receipt"].pk,
            payload={
                "login_code": "770021",
                "reason": "Wrong item",
                "lines": [{"line_id": line.pk, "qty": "1"}],
            },
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("5.000"))

    def test_trade_in_on_untracked_item_does_not_add_stock(self):
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": "trade_in",
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "770021",
                "lines": [{"id": self.untracked.pk, "qty": 2, "price": "100.00"}],
            },
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("5.000"))

    def test_trade_out_on_untracked_item_does_not_deduct(self):
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": "trade_out",
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "770021",
                "lines": [{"id": self.untracked.pk, "qty": 2, "price": "20.00"}],
            },
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("5.000"))

    def test_untracked_item_without_stock_row_sells(self):
        # An untracked item that was never stocked still sells at the till.
        ShopStock.objects.filter(shop=self.shop, item=self.untracked).delete()
        result = self._checkout(self.untracked, qty="2", price="20.00")
        self.assertEqual(result["receipt"].lines.get().quantity, Decimal("2.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("5.000"))

    def test_catalog_flags_untracked_item_and_lists_it_at_zero(self):
        # Untracked items stay on the floor even at zero recorded stock.
        ShopStock.objects.filter(shop=self.shop, item=self.untracked).update(
            quantity=Decimal("0")
        )
        data = build_shop_catalog_page(self.shop)
        rows = {row["id"]: row for row in data["items"]}
        self.assertIn(self.untracked.pk, rows)
        self.assertFalse(rows[self.untracked.pk]["sale_stock_tracked"])
        self.assertTrue(rows[self.item.pk]["sale_stock_tracked"])

    def test_new_item_defaults_sale_tracking_off(self):
        from items.services import create_item

        payload = {
            "category": "GROCERY",
            "name": "MODE DEFAULT ITEM",
            "description": "",
            "minimum_selling_price": "10",
            "shop_price": "20",
            "pricing_mode": "single",
        }
        item = create_item(self.profile, payload, {})
        self.assertFalse(item.deduct_stock_on_sale)
        self.assertTrue(item.sale_tracking_custom)

        pos = get_company_pos_settings()
        pos.enable_stock_tracking = False
        pos.save(update_fields=["enable_stock_tracking", "updated_at"])
        _invalidate_pos_settings_cache()
        try:
            off_item = create_item(
                self.profile, {**payload, "name": "MODE OFF ITEM"}, {}
            )
            self.assertFalse(off_item.deduct_stock_on_sale)
            self.assertTrue(off_item.sale_tracking_custom)
        finally:
            pos.enable_stock_tracking = True
            pos.save(update_fields=["enable_stock_tracking", "updated_at"])
            _invalidate_pos_settings_cache()

    def test_cancel_and_reopen_untracked_item_keeps_stock(self):
        result = self._checkout(self.untracked, qty="2", price="20.00")
        receipt = result["receipt"]
        cancel_pending_shop_receipt(
            shop=self.shop, receipt_id=receipt.pk, actor=self.profile
        )
        reopen_cancelled_shop_receipt(
            shop=self.shop, receipt_id=receipt.pk, actor=self.profile
        )
        self.assertEqual(self._shop_qty(self.untracked), Decimal("5.000"))
        self.untracked.refresh_from_db()
        self.assertEqual(self.untracked.stock, Decimal("5.000"))


class SaleStockTrackingPageTests(TestCase):
    def setUp(self):
        self.password = "pass"
        self.user = User.objects.create_user(
            username="770022",
            password=self.password,
            email="sale-track-it@test.local",
            first_name="IT",
            last_name="Support",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="770022",
            phone_country_code="+254",
            phone_number="700007722",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="Sale Track Page Shop",
            location="Nairobi",
            email="sale-track-page@test.local",
            phone_number="0700007722",
            login_code="770022",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Page Toggle Item",
            category="Grocery",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=Decimal("20.000"),
            created_by=self.profile,
        )
        self.url = "/it-support/stock-management/"

    def _login(self):
        self.client.login(username="770022", password=self.password)

    def test_settings_page_links_to_sale_tracking(self):
        self._login()
        response = self.client.get(
            self.url, {"mode": "settings", "stock": "item"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mode=sale-tracking")

    def test_shelf_settings_page_hides_sale_tracking_link(self):
        self._login()
        pos = get_company_pos_settings()
        if not pos.enable_shelf_mode:
            pos.enable_shelf_mode = True
            pos.save(update_fields=["enable_shelf_mode", "updated_at"])
            _invalidate_pos_settings_cache()
        response = self.client.get(
            self.url, {"mode": "settings", "stock": "shelf"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "sale-tracking")

    def test_sale_tracking_page_renders_items(self):
        self._login()
        response = self.client.get(self.url, {"mode": "sale-tracking"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page Toggle Item")
        self.assertContains(response, "data-sale-tracking-toggle")

    def test_toggle_endpoint_updates_flag(self):
        self._login()
        response = self.client.post(
            self.url,
            {
                "mode": "sale-tracking",
                "action": "toggle_sale_tracking",
                "item_id": self.item.pk,
                "enabled": "0",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["deduct_stock_on_sale"])
        self.item.refresh_from_db()
        self.assertFalse(self.item.deduct_stock_on_sale)
        # The item-stock page "Stock count" switch is the same setting.
        self.assertFalse(self.item.stock_count_enabled)

        response = self.client.post(
            self.url,
            {
                "mode": "sale-tracking",
                "action": "toggle_sale_tracking",
                "item_id": self.item.pk,
                "enabled": "1",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertTrue(self.item.deduct_stock_on_sale)
        self.assertTrue(self.item.stock_count_enabled)

    def test_track_all_endpoint(self):
        self._login()
        response = self.client.post(
            self.url,
            {
                "mode": "sale-tracking",
                "action": "track_all_sale_stock",
                "enabled": "0",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertFalse(self.item.deduct_stock_on_sale)
        self.assertFalse(self.item.stock_count_enabled)

    def test_toggle_rejects_unknown_item(self):
        self._login()
        response = self.client.post(
            self.url,
            {
                "mode": "sale-tracking",
                "action": "toggle_sale_tracking",
                "item_id": "999999",
                "enabled": "0",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)

    def test_page_auto_picks_default_when_stock_tracking_enabled(self):
        # Items never picked by hand follow the company default: default
        # stock tracking is on, so the page switches the item on too.
        self._login()
        Item.objects.filter(pk=self.item.pk).update(
            deduct_stock_on_sale=False, sale_tracking_custom=False
        )
        response = self.client.get(self.url, {"mode": "sale-tracking"})
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertTrue(self.item.deduct_stock_on_sale)

    def test_page_keeps_manual_pick_when_stock_tracking_enabled(self):
        self._login()
        Item.objects.filter(pk=self.item.pk).update(
            deduct_stock_on_sale=False, sale_tracking_custom=True
        )
        response = self.client.get(self.url, {"mode": "sale-tracking"})
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertFalse(self.item.deduct_stock_on_sale)

    def test_toggle_marks_item_custom(self):
        self._login()
        response = self.client.post(
            self.url,
            {
                "mode": "sale-tracking",
                "action": "toggle_sale_tracking",
                "item_id": self.item.pk,
                "enabled": "0",
                "ajax": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertFalse(self.item.deduct_stock_on_sale)
        self.assertTrue(self.item.sale_tracking_custom)

    def test_stock_mode_change_syncs_untouched_items(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.item.refresh_from_db()
        self.assertFalse(self.item.deduct_stock_on_sale)

        set_company_pos_setting(field="stock_mode_items", enabled=True)
        self.item.refresh_from_db()
        self.assertTrue(self.item.deduct_stock_on_sale)

    def test_stock_mode_change_keeps_manual_pick(self):
        from shops.services import set_company_pos_setting

        Item.objects.filter(pk=self.item.pk).update(
            deduct_stock_on_sale=False, sale_tracking_custom=True
        )
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        set_company_pos_setting(field="stock_mode_items", enabled=True)
        self.item.refresh_from_db()
        self.assertFalse(self.item.deduct_stock_on_sale)
