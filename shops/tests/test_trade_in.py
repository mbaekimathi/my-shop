"""Trade-in checkout: goods arrive from another shop and can be paid later."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock, StockEntrySource, StockMovement, StockMovementType
from shops.models import Shop, ShopPaymentMethod, ShopReceiptKind, ShopReceiptStatus
from shops.services import complete_shop_checkout, get_company_pos_settings, open_shop_day
from shops.trade_settlement import record_trade_payment, trade_balance_due


class TradeInCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="100002",
            password="pass",
            email="trade-in@test.local",
            first_name="Trade",
            last_name="Receiver",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="100002",
            phone_country_code="+254",
            phone_number="700000002",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Receiving Shop",
            location="Nairobi",
            email="recv@test.local",
            phone_number="0700000002",
            login_code="900002",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        pos = get_company_pos_settings()
        if not pos.enable_trade_in:
            pos.enable_trade_in = True
            pos.save(update_fields=["enable_trade_in", "updated_at"])
        self.item = Item.objects.create(
            name="Inbound Phone",
            category="Phones",
            description="Trade in fixture",
            minimum_selling_price=Decimal("500.00"),
            shop_price=Decimal("1000.00"),
            stock=4,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
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
                "login_code": "100002",
            },
        )

    def _checkout(self, price="350.00"):
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": "trade_in",
                "client_name": "JANE DOE",
                "client_phone": "0712345678",
                "login_code": "100002",
                "lines": [{"id": self.item.pk, "qty": 2, "price": price}],
            },
        )

    def test_trade_in_moves_stock_from_source_shop(self):
        result = self._checkout()
        receipt = result["receipt"]
        self.assertEqual(receipt.kind, ShopReceiptKind.TRADE_IN)
        self.assertIsNone(receipt.source_shop_id)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.NONE)
        self.assertEqual(receipt.total, Decimal("700.00"))
        self.assertEqual(receipt.client_name, "JANE DOE")
        self.assertEqual(receipt.client_phone, "+254712345678")
        self.assertEqual(trade_balance_due(receipt), Decimal("700.00"))

        dest_stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(dest_stock.quantity, 2)
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, 6)

        inbound = StockMovement.objects.get(
            shop=self.shop, entry_source=StockEntrySource.TRADE_IN
        )
        self.assertEqual(inbound.movement_type, StockMovementType.IN)
        self.assertEqual(inbound.lines.get().quantity, 2)

    def test_payment_clears_trade_in_without_converting_to_sale(self):
        receipt = self._checkout()["receipt"]
        result = record_trade_payment(
            receipt,
            amount=Decimal("700.00"),
            payment_method="cash",
            actor=self.profile,
        )
        receipt.refresh_from_db()
        self.assertFalse(result["converted"])
        self.assertTrue(result["confirmed"])
        self.assertEqual(receipt.kind, ShopReceiptKind.TRADE_IN)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(trade_balance_due(receipt), Decimal("0.00"))
        self.assertEqual(receipt.amount_paid, Decimal("700.00"))
