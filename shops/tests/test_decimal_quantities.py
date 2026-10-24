"""Decimal price/quantity support for shop cart, receipts, and returns."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import (
    Shop,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptStatus,
)
from shops.services import (
    _parse_qty,
    complete_shop_checkout,
    open_shop_day,
    return_shop_receipt_items,
)


class DecimalQuantityTests(TestCase):
    def setUp(self):
        from shops.services import _invalidate_pos_settings_cache

        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="220001",
            password="pass",
            email="decimal@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="220001",
            phone_country_code="+254",
            phone_number="700002201",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Decimal Shop",
            location="Nairobi",
            email="decimal-shop@test.local",
            phone_number="0700002201",
            login_code="220001",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Weighed Flour",
            category="Grocery",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=Decimal("10.000"),
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=Decimal("10.000"),
            average_cost=Decimal("60.00"),
        )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "220001",
            },
        )

    def _checkout(self, qty, price="100.00", cash="250.00"):
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.SALE,
                "payment_method": ShopPaymentMethod.CASH,
                "cash_amount": cash,
                "mpesa_amount": "0",
                "login_code": "220001",
                "lines": [{"id": self.item.pk, "qty": qty, "price": price}],
            },
        )

    def test_decimal_qty_checkout_persists_and_restock(self):
        result = self._checkout("2.5")
        self.assertTrue(result.get("ok") if "ok" in result else True)
        receipt = ShopReceipt.objects.get(shop=self.shop)
        line = receipt.lines.get()
        self.assertEqual(line.quantity, Decimal("2.500"))
        self.assertEqual(line.line_total, Decimal("250.00"))
        self.assertEqual(receipt.total, Decimal("250.00"))
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(stock.quantity, Decimal("7.500"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("7.500"))

    def test_decimal_qty_partial_return(self):
        self._checkout("2.5")
        receipt = ShopReceipt.objects.get(shop=self.shop)
        line = receipt.lines.get()

        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload={
                "login_code": self.profile.employee_id,
                "reason": "Weighed wrong",
                "lines": [{"line_id": line.pk, "qty": "1.25"}],
            },
        )

        line.refresh_from_db()
        receipt.refresh_from_db()
        self.assertEqual(line.returned_quantity, Decimal("1.250"))
        self.assertEqual(line.remaining_quantity, Decimal("1.250"))
        self.assertEqual(line.line_total, Decimal("125.00"))
        self.assertEqual(receipt.total, Decimal("125.00"))
        self.assertEqual(receipt.status, ShopReceiptStatus.PARTIAL_RETURN)
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(stock.quantity, Decimal("8.750"))

    def test_decimal_price_checkout(self):
        self._checkout("1.5", price="99.95", cash="149.92")
        receipt = ShopReceipt.objects.get(shop=self.shop)
        line = receipt.lines.get()
        self.assertEqual(line.unit_price, Decimal("99.95"))
        self.assertEqual(line.line_total, Decimal("149.92"))
        self.assertEqual(receipt.total, Decimal("149.92"))

    def test_parse_qty_rejects_over_three_decimals(self):
        with self.assertRaises(ValidationError):
            _parse_qty("1.2345")
        with self.assertRaises(ValidationError):
            _parse_qty("0")
        with self.assertRaises(ValidationError):
            _parse_qty("abc")
        self.assertEqual(_parse_qty("0.001"), Decimal("0.001"))
        self.assertEqual(_parse_qty("2.5"), Decimal("2.500"))

    def test_decimal_qty_rejected_when_over_stock(self):
        result = None
        with self.assertRaises(ValidationError) as ctx:
            self._checkout("10.5", cash="1050")
        self.assertIn("Insufficient stock", str(ctx.exception))
        self.assertIsNone(result)
