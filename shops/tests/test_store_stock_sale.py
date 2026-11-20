"""POS receipts mirror into the central store count (item-stock page)."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock, StoreStock, StoreStockMovement
from items.store_stock_services import set_item_stock_count_enabled
from shops.models import Shop, ShopPaymentMethod, ShopReceiptKind
from shops.services import (
    _invalidate_pos_settings_cache,
    complete_shop_checkout,
    get_company_pos_settings,
    open_shop_day,
    return_shop_receipt_items,
)


class StoreStockSaleMirrorTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="770011",
            password="pass",
            email="store-sale@test.local",
            first_name="Store",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="770011",
            phone_country_code="+254",
            phone_number="700007711",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Store Mirror Shop",
            location="Nairobi",
            email="store-mirror@test.local",
            phone_number="0700007711",
            login_code="770011",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        pos = get_company_pos_settings()
        if not pos.enable_stock_tracking:
            pos.enable_stock_tracking = True
            pos.save(update_fields=["enable_stock_tracking", "updated_at"])
        self.item = Item.objects.create(
            name="Mirror Sugar",
            category="Grocery",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=Decimal("20.000"),
            track_serial_number=False,
            created_by=self.profile,
        )
        self.opted_out = Item.objects.create(
            name="Manual Only Item",
            category="Grocery",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("70.00"),
            stock=Decimal("20.000"),
            track_serial_number=False,
            stock_count_enabled=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=Decimal("20.000"),
            average_cost=Decimal("60.00"),
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.opted_out,
            quantity=Decimal("20.000"),
            average_cost=Decimal("40.00"),
        )
        StoreStock.objects.create(item=self.item, quantity=Decimal("20.000"))
        StoreStock.objects.create(item=self.opted_out, quantity=Decimal("20.000"))
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "770011",
            },
        )

    def _checkout(self, item, qty="3", price="100.00"):
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.SALE,
                "payment_method": ShopPaymentMethod.CASH,
                "login_code": "770011",
                "lines": [{"id": item.pk, "qty": qty, "price": price}],
            },
        )

    def _store_qty(self, item):
        return StoreStock.objects.get(item=item).quantity

    def test_sale_deducts_store_stock(self):
        result = self._checkout(self.item, qty="3")
        self.assertEqual(self._store_qty(self.item), Decimal("17.000"))

        movement = StoreStockMovement.objects.get(item=self.item)
        self.assertEqual(movement.movement_type, "out")
        self.assertEqual(movement.source, "pos")
        self.assertEqual(movement.quantity, Decimal("3.000"))
        self.assertEqual(movement.balance_after, Decimal("17.000"))
        self.assertEqual(movement.created_by_id, self.profile.pk)
        self.assertEqual(movement.received_by_id, self.profile.pk)
        self.assertIn(result["receipt"].receipt_number, movement.note)
        self.assertIn(self.shop.name, movement.note)

    def test_sale_on_counted_out_item_leaves_store_stock(self):
        self._checkout(self.opted_out, qty="4", price="70.00")
        self.assertEqual(self._store_qty(self.opted_out), Decimal("20.000"))
        self.assertFalse(
            StoreStockMovement.objects.filter(item=self.opted_out).exists()
        )
        # Shop floor stock still deducts — only the store count is opted out.
        stock = ShopStock.objects.get(shop=self.shop, item=self.opted_out)
        self.assertEqual(stock.quantity, Decimal("16.000"))

    def test_toggle_flag_controls_deduction(self):
        set_item_stock_count_enabled(item_id=self.item.pk, enabled=False)
        self._checkout(self.item, qty="2")
        self.assertEqual(self._store_qty(self.item), Decimal("20.000"))

        set_item_stock_count_enabled(item_id=self.item.pk, enabled=True)
        self._checkout(self.item, qty="2")
        self.assertEqual(self._store_qty(self.item), Decimal("18.000"))

    def test_customer_return_restores_store_stock(self):
        result = self._checkout(self.item, qty="3")
        line = result["receipt"].lines.get()
        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=result["receipt"].pk,
            payload={
                "login_code": "770011",
                "reason": "Wrong item",
                "lines": [{"line_id": line.pk, "qty": "1"}],
            },
        )
        self.assertEqual(self._store_qty(self.item), Decimal("18.000"))
        inward = StoreStockMovement.objects.filter(
            item=self.item, movement_type="in"
        ).get()
        self.assertEqual(inward.quantity, Decimal("1.000"))

    def test_trade_in_adds_to_store_stock(self):
        result = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": "trade_in",
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "770011",
                "lines": [{"id": self.item.pk, "qty": 2, "price": "350.00"}],
            },
        )
        self.assertEqual(result["receipt"].kind, ShopReceiptKind.TRADE_IN)
        self.assertEqual(self._store_qty(self.item), Decimal("22.000"))
        movement = StoreStockMovement.objects.get(item=self.item)
        self.assertEqual(movement.movement_type, "in")
        self.assertEqual(movement.reason, "trade_in")

    def test_stock_tracking_off_skips_store_deduction(self):
        pos = get_company_pos_settings()
        pos.enable_stock_tracking = False
        pos.save(update_fields=["enable_stock_tracking", "updated_at"])
        _invalidate_pos_settings_cache()

        self._checkout(self.item, qty="3")
        self.assertEqual(self._store_qty(self.item), Decimal("20.000"))
        self.assertFalse(
            StoreStockMovement.objects.filter(item=self.item).exists()
        )


class StoreStockItemToggleViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="770012",
            password="pass",
            email="store-mgr@test.local",
            first_name="Store",
            last_name="Manager",
            is_active=True,
        )
        self.manager = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="770012",
            phone_country_code="+254",
            phone_number="700007712",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.COMPANY_STORE_MANAGER,
        )
        self.item = Item.objects.create(
            name="Toggle Flour",
            category="Grocery",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=Decimal("20.000"),
            created_by=self.manager,
        )

    def test_toggle_endpoint_updates_flag(self):
        self.client.login(username="770012", password="pass")
        response = self.client.post(
            "/company-store-manager/item-stock/toggle/",
            {"item_id": self.item.pk, "enabled": "0"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["stock_count_enabled"])
        self.item.refresh_from_db()
        self.assertFalse(self.item.stock_count_enabled)
        # The Sale stock tracking switch is the same setting — off here
        # means off there too, and the pick is marked custom.
        self.assertFalse(self.item.deduct_stock_on_sale)
        self.assertTrue(self.item.sale_tracking_custom)

        response = self.client.post(
            "/company-store-manager/item-stock/toggle/",
            {"item_id": self.item.pk, "enabled": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertTrue(self.item.stock_count_enabled)
        self.assertTrue(self.item.deduct_stock_on_sale)

    def test_toggle_endpoint_rejects_bad_item(self):
        self.client.login(username="770012", password="pass")
        response = self.client.post(
            "/company-store-manager/item-stock/toggle/",
            {"item_id": "999999", "enabled": "0"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
