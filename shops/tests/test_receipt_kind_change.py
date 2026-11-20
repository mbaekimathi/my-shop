"""Receipt type changes on the confirm-receipts modal.

Covered by the ``analytics/change-receipt-type`` permission toggle: a pending
sale/credit can be converted into a sale, credit, gift, or quotation. Stock
follows the new kind (quotations never moved stock; converting to one
restocks the lines, converting from one deducts them).
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from employees.models import (
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)
from items.models import Item, ShopStock
from shops.models import (
    Shop,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)
from shops.services import change_shop_receipt_kind


def _profile(*, employee_id, role):
    user = User.objects.create_user(
        username=f"kind-{employee_id}",
        password="pass",
        is_active=True,
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=employee_id,
        phone_country_code="+254",
        phone_number="700000000",
        status=EmployeeStatus.ACTIVE,
        role=role,
    )


def _shop(employee_id, profile):
    return Shop.objects.create(
        name=f"Kind Shop {employee_id}",
        location="Nairobi",
        email=f"kind-shop-{employee_id}@test.local",
        phone_number="0700000000",
        login_code=employee_id,
        password_hash=make_password("pass"),
        created_by=profile,
    )


class ReceiptKindChangeServiceTests(TestCase):
    def setUp(self):
        self.profile = _profile(
            employee_id="420001", role=EmployeeRole.SHOP_CASHIER
        )
        self.shop = _shop("420001", self.profile)
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Kind Item",
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

    def _receipt(self, kind, *, status=ShopReceiptStatus.ACTIVE, **fields):
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number=fields.pop("receipt_number", "KIND-1"),
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

    def _change(self, receipt, target, **kwargs):
        return change_shop_receipt_kind(
            shop=self.shop,
            receipt_id=receipt.pk,
            actor=self.profile,
            target=target,
            **kwargs,
        )

    def test_sale_to_credit_stays_pending_and_keeps_stock_deducted(self):
        # Stock already left for the sale — it stays deducted on the credit.
        self.stock.quantity = 8
        self.stock.save(update_fields=["quantity"])
        self.item.stock = 8
        self.item.save(update_fields=["stock"])
        receipt = self._receipt(
            ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            cash_amount=Decimal("200.00"),
        )
        result = self._change(
            receipt, "credit", credit_due_date="2026-12-01"
        )
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)
        self.assertEqual(receipt.status, ShopReceiptStatus.ACTIVE)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.NONE)
        self.assertEqual(receipt.cash_amount, Decimal("0.00"))
        self.assertEqual(receipt.amount_paid, Decimal("0.00"))
        self.assertEqual(str(receipt.credit_due_date), "2026-12-01")
        self.stock.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.stock.quantity, 8)
        self.assertEqual(self.item.stock, 8)

    def test_sale_to_gift_confirms_as_free_issue(self):
        receipt = self._receipt(
            ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.MPESA,
            mpesa_amount=Decimal("200.00"),
        )
        result = self._change(receipt, "gift")
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.GIFT)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.NONE)
        self.assertEqual(receipt.mpesa_amount, Decimal("0.00"))

    def test_sale_to_quotation_restocks_lines(self):
        self.stock.quantity = 8
        self.stock.save(update_fields=["quantity"])
        self.item.stock = 8
        self.item.save(update_fields=["stock"])
        receipt = self._receipt(ShopReceiptKind.SALE)
        result = self._change(receipt, "quotation")
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.QUOTATION)
        self.assertEqual(receipt.status, ShopReceiptStatus.ACTIVE)
        self.stock.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.stock.quantity, 10)
        self.assertEqual(self.item.stock, 10)

    def test_credit_to_sale_collects_balance_and_confirms(self):
        receipt = self._receipt(ShopReceiptKind.CREDIT)
        result = self._change(
            receipt,
            "sale",
            payment_method="mpesa",
            mpesa_receipt_number="TKL4X2ABCD",
            payment_at="2026-11-20T09:30",
        )
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertTrue(receipt.settled_from_credit)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(receipt.mpesa_amount, Decimal("200.00"))
        self.assertEqual(receipt.mpesa_receipt_number, "TKL4X2ABCD")
        self.assertIsNotNone(receipt.confirmed_payment_at)

    def test_credit_with_payments_cannot_leave_credit_except_sale(self):
        receipt = self._receipt(
            ShopReceiptKind.CREDIT, amount_paid=Decimal("50.00")
        )
        with self.assertRaises(ValidationError):
            self._change(receipt, "gift")
        with self.assertRaises(ValidationError):
            self._change(receipt, "quotation")

    def test_quotation_to_sale_deducts_stock_and_confirms(self):
        receipt = self._receipt(ShopReceiptKind.QUOTATION)
        result = self._change(
            receipt, "sale", payment_method="cash"
        )
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(receipt.cash_amount, Decimal("200.00"))
        self.stock.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.stock.quantity, 8)
        self.assertEqual(self.item.stock, 8)

    def test_quotation_to_credit_deducts_stock(self):
        receipt = self._receipt(ShopReceiptKind.QUOTATION)
        result = self._change(receipt, "credit")
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 8)

    def test_non_pending_receipt_rejected(self):
        receipt = self._receipt(
            ShopReceiptKind.SALE, status=ShopReceiptStatus.CONFIRMED
        )
        with self.assertRaises(ValidationError) as ctx:
            self._change(receipt, "credit")
        self.assertIn("pending", str(ctx.exception))

    def test_trade_receipt_rejected(self):
        receipt = self._receipt(ShopReceiptKind.TRADE_OUT)
        with self.assertRaises(ValidationError):
            self._change(receipt, "sale")

    def test_invalid_target_rejected(self):
        receipt = self._receipt(ShopReceiptKind.SALE)
        with self.assertRaises(ValidationError):
            self._change(receipt, "trade_out")

    def test_same_kind_is_a_noop(self):
        receipt = self._receipt(ShopReceiptKind.SALE)
        result = self._change(receipt, "sale")
        self.assertTrue(result["ok"])
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(receipt.status, ShopReceiptStatus.ACTIVE)


class ReceiptKindChangePermissionTests(TestCase):
    """``analytics/change-receipt-type`` off ⇒ the confirm endpoint refuses
    any posted ``receipt_kind`` that differs from the stored kind."""

    def setUp(self):
        self.profile = _profile(
            employee_id="420002", role=EmployeeRole.IT_SUPPORT
        )
        self.shop = _shop("420002", self.profile)
        self.client.force_login(self.profile.user)
        self.item = Item.objects.create(
            name="Kind Perm Item",
            category="Parts",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            deduct_stock_on_sale=True,
            stock_count_enabled=True,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )

    def _receipt(self, number):
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number=number,
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("200.00"),
            total=Decimal("200.00"),
            cash_amount=Decimal("200.00"),
            created_by=self.profile,
            status=ShopReceiptStatus.ACTIVE,
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

    def _url(self, name, receipt):
        return reverse(
            f"employees:{name}",
            kwargs={
                "role_segment": "it-support",
                "shop_id": self.shop.pk,
                "receipt_id": receipt.pk,
            },
        )

    def _deny(self, module, submodule):
        return EmployeeModulePermission.objects.create(
            employee=self.profile,
            module_slug=module,
            submodule_slug=submodule,
            allowed=False,
        )

    def test_kind_change_denied_when_toggled_off(self):
        self._deny("analytics", "change-receipt-type")
        receipt = self._receipt("KIND-DENY-1")
        resp = self.client.post(
            self._url("analytics_receipt_confirm", receipt),
            data=json.dumps(
                {
                    "login_code": self.profile.employee_id,
                    "receipt_kind": "gift",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)

    def test_kind_change_allowed_by_default(self):
        receipt = self._receipt("KIND-ALLOW-1")
        resp = self.client.post(
            self._url("analytics_receipt_confirm", receipt),
            data=json.dumps(
                {
                    "login_code": self.profile.employee_id,
                    "receipt_kind": "gift",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        receipt.refresh_from_db()
        self.assertEqual(receipt.kind, ShopReceiptKind.GIFT)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)

    def test_normal_confirm_unaffected_by_missing_kind_perm(self):
        self._deny("analytics", "change-receipt-type")
        receipt = self._receipt("KIND-SAME-1")
        resp = self.client.post(
            self._url("analytics_receipt_confirm", receipt),
            data=json.dumps(
                {
                    "login_code": self.profile.employee_id,
                    "receipt_kind": "sale",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)

    def test_detail_payload_reports_capability(self):
        receipt = self._receipt("KIND-FLAG-1")
        url = f"{self._url('analytics_receipt_detail', receipt)}?source=pos"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["receipt"]["can_change_kind"])
        self._deny("analytics", "change-receipt-type")
        resp = self.client.get(url)
        self.assertFalse(resp.json()["receipt"]["can_change_kind"])
