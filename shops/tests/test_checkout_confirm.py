"""Confirm-at-checkout: the cart checkbox that confirms a receipt on complete."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

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
    ShopReceiptStatus,
)
from shops.services import (
    _invalidate_pos_settings_cache,
    complete_shop_checkout,
    open_shop_day,
    set_company_pos_setting,
)


class CheckoutConfirmTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="200301",
            password="pass",
            email="confirm@test.local",
            first_name="Confirm",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200301",
            phone_country_code="+254",
            phone_number="700000301",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Confirm Shop",
            location="Nairobi",
            email="confirm-shop@test.local",
            phone_number="0700000301",
            login_code="200301",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Confirm Item",
            category="Parts",
            description="Confirm fixture",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "200301",
            },
        )

    def tearDown(self):
        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=False
        )

    def _sale_payload(self, **extra):
        payload = {
            "kind": ShopReceiptKind.SALE,
            "payment_method": ShopPaymentMethod.CASH,
            "cash_amount": "100",
            "mpesa_amount": "0",
            "login_code": "200301",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }
        payload.update(extra)
        return payload

    def test_sale_without_confirm_stays_pending(self):
        result = complete_shop_checkout(
            shop=self.shop, profile=self.profile, payload=self._sale_payload()
        )
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["receipt_status"], ShopReceiptStatus.ACTIVE)
        self.assertEqual(result["receipt"].status, ShopReceiptStatus.ACTIVE)

    def test_sale_with_confirm_is_confirmed(self):
        result = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(
                confirm=True,
                confirm_mpesa_receipt_number="SII7ABC123",
                confirm_payment_at="2026-11-14T10:30",
            ),
        )
        receipt = result["receipt"]
        self.assertTrue(result["confirmed"])
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(receipt.mpesa_receipt_number, "SII7ABC123")
        self.assertIsNotNone(receipt.confirmed_payment_at)

    def test_confirm_string_flag_works(self):
        result = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(confirm="on"),
        )
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["receipt"].status, ShopReceiptStatus.CONFIRMED)

    def test_confirm_rejected_for_quotation(self):
        payload = self._sale_payload(
            kind=ShopReceiptKind.QUOTATION,
            client_name="QUOTE CLIENT",
            confirm=True,
        )
        with self.assertRaises(ValidationError) as ctx:
            complete_shop_checkout(
                shop=self.shop, profile=self.profile, payload=payload
            )
        self.assertIn("Only sales and credits", str(ctx.exception))

    def test_confirm_requires_details_when_pos_setting_on(self):
        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        before = ShopReceipt.objects.count()
        with self.assertRaises(ValidationError) as ctx:
            complete_shop_checkout(
                shop=self.shop,
                profile=self.profile,
                payload=self._sale_payload(confirm=True),
            )
        self.assertIn("reference number", str(ctx.exception))
        # Checkout rolls back — no half-confirmed receipt left behind.
        self.assertEqual(ShopReceipt.objects.count(), before)

    def test_confirm_denied_without_receipts_permission(self):
        EmployeeModulePermission.objects.create(
            employee=self.profile,
            module_slug="my-shop",
            submodule_slug="receipts",
            allowed=False,
        )
        before = ShopReceipt.objects.count()
        with self.assertRaises(ValidationError) as ctx:
            complete_shop_checkout(
                shop=self.shop,
                profile=self.profile,
                payload=self._sale_payload(confirm=True),
            )
        self.assertIn("permission to confirm receipts", str(ctx.exception))
        self.assertEqual(ShopReceipt.objects.count(), before)

    def test_credit_confirm_full_amount_clears_to_sale(self):
        result = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.CREDIT,
                "client_name": "CREDIT CLIENT",
                "client_phone": "0712345678",
                "login_code": "200301",
                "confirm": True,
                "confirm_amount": "100",
                "confirm_payment_method": "cash",
                "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
            },
        )
        receipt = result["receipt"]
        self.assertTrue(result["confirmed"])
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertTrue(receipt.settled_from_credit)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
