"""Fully paid credit receipts convert to sales."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from employees.analytics_services import (
    apply_account_payment,
    apply_credit_receipt_payment,
)
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.credit_settlement import (
    convert_settled_credit_to_sale,
    record_credit_collection,
)
from shops.models import (
    Client,
    Shop,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptStatus,
)


class DummyReceipt(SimpleNamespace):
    def save(self, update_fields=None):
        self.saved_fields = list(update_fields or [])


class CreditSettlementHelperTests(SimpleTestCase):
    def _receipt(self, **kwargs):
        values = dict(
            kind=ShopReceiptKind.CREDIT,
            status=ShopReceiptStatus.ACTIVE,
            total=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            cash_amount=Decimal("0.00"),
            mpesa_amount=Decimal("0.00"),
            payment_method=ShopPaymentMethod.NONE,
            mpesa_receipt_number="",
            credit_due_date=date(2026, 9, 1),
            settled_from_credit=False,
        )
        values.update(kwargs)
        return DummyReceipt(**values)

    def test_partial_collection_stays_credit(self):
        receipt = self._receipt()
        converted = record_credit_collection(
            receipt, amount=Decimal("40.00"), payment_method="cash"
        )
        self.assertFalse(converted)
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)
        self.assertEqual(receipt.amount_paid, Decimal("40.00"))
        self.assertEqual(receipt.cash_amount, Decimal("40.00"))
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.NONE)
        self.assertNotIn("kind", receipt.saved_fields)

    def test_full_cash_collection_converts_to_sale(self):
        receipt = self._receipt()
        converted = record_credit_collection(
            receipt, amount=Decimal("100.00"), payment_method="cash"
        )
        self.assertTrue(converted)
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.CASH)
        self.assertEqual(receipt.cash_amount, Decimal("100.00"))
        self.assertTrue(receipt.settled_from_credit)
        self.assertIsNone(receipt.credit_due_date)
        self.assertIn("kind", receipt.saved_fields)

    def test_split_collections_become_both(self):
        receipt = self._receipt()
        record_credit_collection(
            receipt, amount=Decimal("40.00"), payment_method="cash"
        )
        converted = record_credit_collection(
            receipt,
            amount=Decimal("60.00"),
            payment_method="mpesa",
            mpesa_receipt_number="ABC123",
        )
        self.assertTrue(converted)
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.BOTH)
        self.assertEqual(receipt.cash_amount, Decimal("40.00"))
        self.assertEqual(receipt.mpesa_amount, Decimal("60.00"))
        self.assertEqual(receipt.mpesa_receipt_number, "ABC123")

    def test_cancelled_credit_is_not_converted(self):
        receipt = self._receipt(
            amount_paid=Decimal("100.00"),
            status=ShopReceiptStatus.CANCELLED,
        )
        self.assertFalse(convert_settled_credit_to_sale(receipt))
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)



class CreditPaymentConversionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="850012",
            password="credit-pay",
            email="credit-pay@test.local",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="850012",
            phone_country_code="+254",
            phone_number="700000952",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="PAY SHOP",
            location="NAIROBI",
            email="pay-shop@test.local",
            phone_number="0700000952",
            login_code="850112",
            password_hash="x",
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        self.client = Client.objects.create(
            full_name="PAY CLIENT",
            phone_number="0700003002",
            phone_normalized="254700003002",
            created_by=self.cashier,
        )
        self._n = 0

    def _receipt(self, **kwargs):
        self._n += 1
        defaults = {
            "shop": self.shop,
            "receipt_number": f"CR-PAY-{self._n}",
            "kind": ShopReceiptKind.CREDIT,
            "total": Decimal("80.00"),
            "amount_paid": Decimal("0.00"),
            "created_by": self.cashier,
            "client": self.client,
            "client_name": self.client.full_name,
            "client_phone": self.client.phone_number,
            "status": ShopReceiptStatus.ACTIVE,
            "payment_method": ShopPaymentMethod.NONE,
        }
        defaults.update(kwargs)
        return ShopReceipt.objects.create(**defaults)

    def test_single_receipt_payment_converts_when_cleared(self):
        receipt = self._receipt(total=Decimal("80.00"))
        result = apply_credit_receipt_payment(
            profile=self.cashier,
            receipt_id=receipt.pk,
            amount="80",
        )
        receipt.refresh_from_db()
        self.assertTrue(result["converted"])
        self.assertEqual(result["receipt_kind"], ShopReceiptKind.SALE)
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.CASH)
        self.assertTrue(receipt.settled_from_credit)
        self.assertIn("recorded as a sale", result["message"])

    def test_fifo_converts_only_fully_paid_receipts(self):
        first = self._receipt(total=Decimal("50.00"))
        second = self._receipt(total=Decimal("70.00"))
        result = apply_account_payment(
            profile=self.cashier,
            kind="credit",
            account_id=self.client.pk,
            amount="50",
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(result["converted"], 1)
        self.assertEqual(first.kind, ShopReceiptKind.SALE)
        self.assertTrue(first.settled_from_credit)
        self.assertEqual(second.kind, ShopReceiptKind.CREDIT)
        self.assertFalse(second.settled_from_credit)
        self.assertEqual(second.amount_paid, Decimal("0.00"))


class SalesAnalyticsPaidCreditsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="850022",
            password="sales-credit",
            email="sales-credit@test.local",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="850022",
            phone_country_code="+254",
            phone_number="700000962",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SALES CREDIT SHOP",
            location="NAIROBI",
            email="sales-credit-shop@test.local",
            phone_number="0700000962",
            login_code="850122",
            password_hash="x",
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        self.client = Client.objects.create(
            full_name="SALES CREDIT CLIENT",
            phone_number="0700003012",
            phone_normalized="254700003012",
            created_by=self.cashier,
        )

    def test_paid_credits_split_from_cash_and_mpesa(self):
        from employees.analytics_services import _build_sales

        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="SALE-CASH-1",
            kind=ShopReceiptKind.SALE,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            cash_amount=Decimal("100.00"),
            mpesa_amount=Decimal("0.00"),
            payment_method=ShopPaymentMethod.CASH,
            created_by=self.cashier,
            settled_from_credit=False,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="SALE-MPESA-1",
            kind=ShopReceiptKind.SALE,
            total=Decimal("50.00"),
            amount_paid=Decimal("50.00"),
            cash_amount=Decimal("0.00"),
            mpesa_amount=Decimal("50.00"),
            payment_method=ShopPaymentMethod.MPESA,
            created_by=self.cashier,
            settled_from_credit=False,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="SALE-CREDIT-1",
            kind=ShopReceiptKind.SALE,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            cash_amount=Decimal("30.00"),
            mpesa_amount=Decimal("50.00"),
            payment_method=ShopPaymentMethod.BOTH,
            created_by=self.cashier,
            client=self.client,
            client_name=self.client.full_name,
            settled_from_credit=True,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="CREDIT-OPEN-1",
            kind=ShopReceiptKind.CREDIT,
            total=Decimal("120.00"),
            amount_paid=Decimal("20.00"),
            cash_amount=Decimal("20.00"),
            mpesa_amount=Decimal("0.00"),
            payment_method=ShopPaymentMethod.NONE,
            created_by=self.cashier,
            client=self.client,
            client_name=self.client.full_name,
        )

        page = _build_sales(
            {
                "active_shop_ids": [self.shop.pk],
                "filter_shops": [self.shop],
                "start": None,
                "end": None,
                "prev_start": None,
                "prev_end": None,
                "role": self.cashier.role,
                "query": "",
            }
        )
        board = {tile["label"]: tile["value"] for tile in page["summary_board"]["tiles"]}
        self.assertEqual(board["Cash"], "KSh 100.00")
        self.assertEqual(board["M-Pesa"], "KSh 50.00")
        self.assertEqual(board["Paid credits"], "KSh 80.00")
        self.assertEqual(board["Unpaid credits"], "KSh 100.00")
        self.assertEqual(page["summary_board"]["hero"]["value"], "KSh 230.00")

        columns = [
            col["label"] if isinstance(col, dict) else col
            for col in page["tables"][0]["columns"]
        ]
        self.assertEqual(
            columns[:6],
            ["Shop", "Cash", "M-Pesa", "Paid credits", "Unpaid credits", "Total"],
        )
        shop_row = page["tables"][0]["rows"][0]
        self.assertEqual(shop_row[1]["amount"], "100")
        self.assertEqual(shop_row[2]["amount"], "50")
        self.assertEqual(shop_row[3]["amount"], "80")
        self.assertEqual(shop_row[3]["qty"], "1")
        self.assertEqual(shop_row[4]["amount"], "100")
        self.assertEqual(shop_row[4]["qty"], "1")
        self.assertEqual(shop_row[5]["amount"], "230")
