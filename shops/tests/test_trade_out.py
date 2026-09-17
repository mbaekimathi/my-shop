"""Trade-out checkout and settlement."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock, StockEntrySource, StockMovement, StockOutReason
from shops.models import (
    Shop,
    ShopPaymentMethod,
    ShopReceiptKind,
    ShopReceiptStatus,
)
from shops.services import complete_shop_checkout, get_company_pos_settings, open_shop_day
from shops.trade_settlement import record_trade_exchange, record_trade_payment, trade_balance_due


class TradeOutCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="100001",
            password="pass",
            email="trade@test.local",
            first_name="Trade",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="100001",
            phone_country_code="+254",
            phone_number="700000001",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Trade Shop",
            location="Nairobi",
            email="trade-shop@test.local",
            phone_number="0700000001",
            login_code="900001",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        pos = get_company_pos_settings()
        if not pos.enable_trade_out:
            pos.enable_trade_out = True
            pos.save(update_fields=["enable_trade_out", "updated_at"])
        self.item = Item.objects.create(
            name="Trade Phone",
            category="Phones",
            description="Trade fixture",
            minimum_selling_price=Decimal("500.00"),
            shop_price=Decimal("1000.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )
        self.exchange_item = Item.objects.create(
            name="Used Phone",
            category="Phones",
            description="Exchange fixture",
            minimum_selling_price=Decimal("200.00"),
            shop_price=Decimal("800.00"),
            stock=0,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("400.00"),
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.exchange_item,
            quantity=0,
            average_cost=Decimal("0.00"),
        )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "0",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "100001",
            },
        )

    def _checkout_trade(self):
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": "trade_out",
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "100001",
                "lines": [{"id": self.item.pk, "qty": 2, "price": "1000.00"}],
            },
        )

    def test_trade_out_reduces_stock_and_creates_movement(self):
        result = self._checkout_trade()
        receipt = result["receipt"]
        self.assertEqual(receipt.kind, ShopReceiptKind.TRADE_OUT)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.NONE)
        self.assertEqual(receipt.total, Decimal("2000.00"))
        self.assertEqual(receipt.client_name, "JANE DOE")

        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(stock.quantity, 8)
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, 8)

        movement = StockMovement.objects.filter(
            shop=self.shop, entry_source=StockEntrySource.TRADE_OUT
        ).first()
        self.assertIsNotNone(movement)
        line = movement.lines.get()
        self.assertEqual(line.reason, StockOutReason.TRADE_OUT)
        self.assertEqual(line.quantity, 2)

    def test_payment_converts_trade_to_sale(self):
        receipt = self._checkout_trade()["receipt"]
        result = record_trade_payment(
            receipt,
            amount=Decimal("2000.00"),
            payment_method="cash",
            actor=self.profile,
        )
        receipt.refresh_from_db()
        self.assertTrue(result["converted"])
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertTrue(receipt.settled_from_trade)
        self.assertEqual(trade_balance_due(receipt), Decimal("0.00"))

    def test_exchange_deducts_balance_and_stocks_in(self):
        receipt = self._checkout_trade()["receipt"]
        result = record_trade_exchange(
            receipt,
            item_id=self.exchange_item.pk,
            quantity=1,
            buying_price=Decimal("600.00"),
            actor=self.profile,
        )
        receipt.refresh_from_db()
        self.assertFalse(result["converted"])
        self.assertEqual(receipt.trade_exchange_value, Decimal("600.00"))
        self.assertEqual(trade_balance_due(receipt), Decimal("1400.00"))

        stock = ShopStock.objects.get(shop=self.shop, item=self.exchange_item)
        self.assertEqual(stock.quantity, 1)
        movement = StockMovement.objects.filter(
            shop=self.shop, entry_source=StockEntrySource.TRADE_EXCHANGE
        ).first()
        self.assertIsNotNone(movement)

        result = record_trade_payment(
            receipt,
            amount=Decimal("1400.00"),
            payment_method="mpesa",
            mpesa_receipt_number="TRD123",
            actor=self.profile,
        )
        receipt.refresh_from_db()
        self.assertTrue(result["converted"])
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.MPESA)

    def test_full_exchange_confirms_without_sale(self):
        receipt = self._checkout_trade()["receipt"]
        result = record_trade_exchange(
            receipt,
            item_id=self.exchange_item.pk,
            quantity=1,
            buying_price=Decimal("2000.00"),
            actor=self.profile,
        )
        receipt.refresh_from_db()
        self.assertFalse(result["converted"])
        self.assertTrue(result["confirmed"])
        self.assertEqual(receipt.kind, ShopReceiptKind.TRADE_OUT)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(trade_balance_due(receipt), Decimal("0.00"))
