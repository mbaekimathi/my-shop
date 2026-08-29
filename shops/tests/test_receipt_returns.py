"""Verify return_shop_receipt_items restocks inventory and adjusts sales/credits."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock, StockEntrySource, StockMovement
from shops.models import (
    Client,
    ClientCreditAccountEvent,
    ClientCreditAccountEventKind,
    Shop,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)
from shops.services import return_shop_receipt_items


class ReceiptReturnScenarioTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="860011",
            password="return-pass",
            email="return@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860011",
            phone_country_code="+254",
            phone_number="700001001",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="RETURN SHOP",
            location="NAIROBI",
            email="return-shop@test.local",
            phone_number="0700001001",
            login_code="860111",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        self.client = Client.objects.create(
            full_name="RETURN CLIENT",
            phone_number="0700004001",
            phone_normalized="254700004001",
            created_by=self.staff,
        )
        self.item = Item.objects.create(
            category="PHONES",
            name="RETURN PHONE",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=3,
            created_by=self.staff,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=3,
            average_cost=Decimal("60.00"),
        )
        self._n = 0

    def _return_payload(self, lines):
        return {
            "login_code": self.staff.employee_id,
            "lines": lines,
        }

    def _sale_receipt(self, *, qty=2, unit_price=Decimal("100.00")):
        self._n += 1
        subtotal = unit_price * qty
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number=f"SALE-RET-{self._n}",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=subtotal,
            tax_percent=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total=subtotal,
            amount_paid=subtotal,
            cash_amount=subtotal,
            mpesa_amount=Decimal("0.00"),
            created_by=self.staff,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=qty,
            unit_price=unit_price,
            unit_cost=Decimal("60.00"),
            line_total=subtotal,
            line_cogs=Decimal("60.00") * qty,
        )
        return receipt

    def _credit_receipt(
        self,
        *,
        qty=2,
        unit_price=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
    ):
        self._n += 1
        subtotal = unit_price * qty
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number=f"CR-RET-{self._n}",
            kind=ShopReceiptKind.CREDIT,
            payment_method=ShopPaymentMethod.NONE,
            client=self.client,
            client_name=self.client.full_name,
            client_phone=self.client.phone_number,
            subtotal=subtotal,
            tax_percent=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total=subtotal,
            amount_paid=amount_paid,
            cash_amount=amount_paid if amount_paid > 0 else Decimal("0.00"),
            mpesa_amount=Decimal("0.00"),
            created_by=self.staff,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=qty,
            unit_price=unit_price,
            unit_cost=Decimal("60.00"),
            line_total=subtotal,
            line_cogs=Decimal("60.00") * qty,
        )
        return receipt

    def test_cash_sale_partial_return_restock_and_refund(self):
        """2 × 100 sale, return 1 → stock +1, total 100, till refund recorded."""
        receipt = self._sale_receipt(qty=2)
        line = receipt.lines.get()

        result = return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload=self._return_payload([{"line_id": line.pk, "qty": 1}]),
        )

        receipt.refresh_from_db()
        line.refresh_from_db()
        self.item.refresh_from_db()
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)

        self.assertEqual(stock.quantity, 4)
        self.assertEqual(self.item.stock, 4)
        self.assertEqual(line.returned_quantity, 1)
        self.assertEqual(line.remaining_quantity, 1)
        self.assertEqual(line.line_total, Decimal("100.00"))
        self.assertEqual(receipt.total, Decimal("100.00"))
        self.assertEqual(receipt.cash_amount, Decimal("100.00"))
        self.assertEqual(receipt.status, ShopReceiptStatus.PARTIAL_RETURN)
        self.assertEqual(len(receipt.return_payment_events), 1)
        self.assertEqual(
            Decimal(receipt.return_payment_events[0]["cash"]),
            Decimal("100.00"),
        )
        self.assertTrue(
            StockMovement.objects.filter(
                shop=self.shop,
                entry_source=StockEntrySource.CUSTOMER_RETURN,
            ).exists()
        )
        self.assertEqual(result["total"], "100.00")

    def test_credit_partial_return_after_partial_payment(self):
        """Credit 200, paid 80, return 1 × 100 → total 100, due 20, no till refund."""
        receipt = self._credit_receipt(amount_paid=Decimal("80.00"))
        line = receipt.lines.get()

        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload=self._return_payload([{"line_id": line.pk, "qty": 1}]),
        )

        receipt.refresh_from_db()
        line.refresh_from_db()
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)

        self.assertEqual(stock.quantity, 4)
        self.assertEqual(receipt.total, Decimal("100.00"))
        self.assertEqual(receipt.amount_paid, Decimal("80.00"))
        self.assertEqual(receipt.total - receipt.amount_paid, Decimal("20.00"))
        self.assertEqual(receipt.status, ShopReceiptStatus.PARTIAL_RETURN)
        self.assertEqual(receipt.return_payment_events, [])
        self.assertEqual(line.returned_quantity, 1)
        event = ClientCreditAccountEvent.objects.filter(
            receipt=receipt,
            kind=ClientCreditAccountEventKind.ITEMS_RETURNED,
        ).latest("occurred_at")
        self.assertEqual(event.amount, Decimal("100.00"))

    def test_credit_return_caps_overpayment(self):
        """Credit 200, paid 150, return 1 → total 100, amount_paid capped to 100."""
        receipt = self._credit_receipt(amount_paid=Decimal("150.00"))
        line = receipt.lines.get()

        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload=self._return_payload([{"line_id": line.pk, "qty": 1}]),
        )

        receipt.refresh_from_db()
        self.assertEqual(receipt.total, Decimal("100.00"))
        self.assertEqual(receipt.amount_paid, Decimal("100.00"))
        self.assertEqual(receipt.total - receipt.amount_paid, Decimal("0.00"))

    def test_full_return_cancels_receipt_and_restock_all(self):
        receipt = self._sale_receipt(qty=2)
        line = receipt.lines.get()

        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload=self._return_payload([{"line_id": line.pk, "qty": 2}]),
        )

        receipt.refresh_from_db()
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)

        self.assertEqual(stock.quantity, 5)
        self.assertEqual(receipt.status, ShopReceiptStatus.CANCELLED)
        self.assertEqual(receipt.total, Decimal("0.00"))
        self.assertEqual(receipt.cash_amount, Decimal("0.00"))

    def test_manual_line_without_item_does_not_restock(self):
        self._n += 1
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number=f"MAN-RET-{self._n}",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("50.00"),
            total=Decimal("50.00"),
            amount_paid=Decimal("50.00"),
            cash_amount=Decimal("50.00"),
            created_by=self.staff,
        )
        line = ShopReceiptLine.objects.create(
            receipt=receipt,
            item=None,
            item_name="Manual service",
            quantity=1,
            unit_price=Decimal("50.00"),
            line_total=Decimal("50.00"),
        )
        stock_before = ShopStock.objects.get(shop=self.shop, item=self.item).quantity

        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload=self._return_payload([{"line_id": line.pk, "qty": 1}]),
        )

        receipt.refresh_from_db()
        stock_after = ShopStock.objects.get(shop=self.shop, item=self.item).quantity

        self.assertEqual(stock_before, stock_after)
        self.assertEqual(receipt.status, ShopReceiptStatus.CANCELLED)
        self.assertEqual(receipt.total, Decimal("0.00"))
