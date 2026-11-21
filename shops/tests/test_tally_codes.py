"""Tally document codes — per-type prefixes and dual codes on conversion.

Plain receipts show their stored number (S0005); a converted receipt shows
both kind letters plus the original sequence — CS0005 (credit → sale),
SC0007 (sale → credit), SG0003 (sale → gift). Customer prepayments
("advancements") get their own A-prefixed doc code, and the receipt lists /
filters expose both the code and the dual kind identity.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from employees.models import EmployeeRole
from items.models import Item, ShopStock
from shops.credit_settlement import convert_settled_credit_to_sale
from shops.models import (
    CustomerPrepayment,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)
from shops.services import (
    change_shop_receipt_kind,
    list_shop_receipts,
    prepayment_doc_code,
    receipt_tally_code,
)

from .test_receipt_kind_change import _profile, _shop


class ReceiptTallyCodeTests(TestCase):
    def setUp(self):
        self.profile = _profile(
            employee_id="430001", role=EmployeeRole.SHOP_CASHIER
        )
        self.shop = _shop("430001", self.profile)
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Tally Item",
            category="Parts",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            deduct_stock_on_sale=True,
            stock_count_enabled=True,
            created_by=self.profile,
        )
        self.stock = ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )

    def _receipt(self, kind, number, *, status=ShopReceiptStatus.ACTIVE, **fields):
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number=number,
            kind=kind,
            payment_method=fields.pop(
                "payment_method", ShopPaymentMethod.NONE
            ),
            subtotal=Decimal("200.00"),
            total=Decimal("200.00"),
            created_by=self.profile,
            status=status,
            **fields,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=2,
            unit_price=Decimal("100.00"),
            unit_cost=Decimal("60.00"),
            line_total=Decimal("200.00"),
            line_cogs=Decimal("120.00"),
        )
        return receipt

    def test_plain_sale_keeps_stored_number(self):
        receipt = self._receipt(ShopReceiptKind.SALE, "S0007")
        self.assertEqual(receipt_tally_code(receipt), "S0007")

    def test_sale_to_credit_shows_sc_code(self):
        receipt = self._receipt(
            ShopReceiptKind.SALE,
            "S0007",
            payment_method=ShopPaymentMethod.CASH,
            cash_amount=Decimal("200.00"),
        )
        change_shop_receipt_kind(
            shop=self.shop,
            receipt_id=receipt.pk,
            actor=self.profile,
            target="credit",
            credit_due_date="2026-12-01",
            client_name="Test Client",
            client_phone="0712000000",
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)
        self.assertEqual(receipt.converted_from_kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt_tally_code(receipt), "SC0007")

    def test_sale_to_gift_shows_sg_code(self):
        receipt = self._receipt(
            ShopReceiptKind.SALE,
            "S0009",
            payment_method=ShopPaymentMethod.CASH,
            cash_amount=Decimal("200.00"),
        )
        change_shop_receipt_kind(
            shop=self.shop,
            receipt_id=receipt.pk,
            actor=self.profile,
            target="gift",
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.converted_from_kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt_tally_code(receipt), "SG0009")

    def test_settled_credit_shows_cs_code(self):
        receipt = self._receipt(
            ShopReceiptKind.CREDIT,
            "C0003",
            status=ShopReceiptStatus.CONFIRMED,
            payment_method=ShopPaymentMethod.NONE,
            amount_paid=Decimal("200.00"),
        )
        self.assertTrue(convert_settled_credit_to_sale(receipt))
        receipt.save()
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertTrue(receipt.settled_from_credit)
        self.assertEqual(receipt.converted_from_kind, ShopReceiptKind.CREDIT)
        self.assertEqual(receipt_tally_code(receipt), "CS0003")

    def test_settled_credit_code_works_without_converted_field(self):
        # Older rows predate converted_from_kind — settled_from_credit
        # still yields the dual code.
        receipt = self._receipt(
            ShopReceiptKind.SALE,
            "C0011",
            status=ShopReceiptStatus.CONFIRMED,
            settled_from_credit=True,
        )
        self.assertEqual(receipt_tally_code(receipt), "CS0011")

    def test_converted_receipt_matches_both_kind_filters(self):
        receipt = self._receipt(
            ShopReceiptKind.SALE,
            "S0021",
            payment_method=ShopPaymentMethod.CASH,
            cash_amount=Decimal("200.00"),
        )
        change_shop_receipt_kind(
            shop=self.shop,
            receipt_id=receipt.pk,
            actor=self.profile,
            target="credit",
            credit_due_date="2026-12-01",
            client_name="Test Client",
            client_phone="0712000000",
        )
        credit_rows = list_shop_receipts(shop=self.shop, kind="credit")["receipts"]
        sale_rows = list_shop_receipts(shop=self.shop, kind="sale")["receipts"]
        credit_ids = {row["id"] for row in credit_rows}
        sale_ids = {row["id"] for row in sale_rows}
        self.assertIn(receipt.pk, credit_ids)
        self.assertIn(receipt.pk, sale_ids)
        row = next(row for row in credit_rows if row["id"] == receipt.pk)
        self.assertEqual(row["tally_code"], "SC0021")

    def test_advancement_doc_code_and_filter(self):
        prepay = CustomerPrepayment.objects.create(
            shop=self.shop,
            customer_name="Jane Kamau",
            customer_phone="0712345678",
            purpose="Deposit for order",
            amount=Decimal("500.00"),
            payment_method=ShopPaymentMethod.CASH,
            cash_amount=Decimal("500.00"),
            created_by=self.profile,
        )
        expected = f"A{prepay.pk:04d}"
        self.assertEqual(prepayment_doc_code(prepay), expected)
        self.assertEqual(prepay.doc_code, expected)

        rows = list_shop_receipts(shop=self.shop, kind="advancement")["receipts"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["tally_code"], expected)
        self.assertEqual(row["receipt_number"], expected)
        self.assertEqual(row["kind_label"], "Advancement")
        self.assertEqual(row["client_name"], "Jane Kamau")

        all_rows = list_shop_receipts(shop=self.shop, kind="all")["receipts"]
        adv_rows = [r for r in all_rows if r["source"] == "prepayment"]
        self.assertEqual(len(adv_rows), 1)

    def test_all_filter_lists_advancement_once_with_pos_rows(self):
        self._receipt(ShopReceiptKind.SALE, "S0030")
        CustomerPrepayment.objects.create(
            shop=self.shop,
            customer_name="Mose Otieno",
            customer_phone="0700000000",
            purpose="Advance",
            amount=Decimal("100.00"),
            payment_method=ShopPaymentMethod.CASH,
            cash_amount=Decimal("100.00"),
            created_by=self.profile,
        )
        rows = list_shop_receipts(shop=self.shop, kind="all")["receipts"]
        sources = sorted(row["source"] for row in rows)
        self.assertEqual(sources.count("prepayment"), 1)
        self.assertEqual(sources.count("pos"), 1)
