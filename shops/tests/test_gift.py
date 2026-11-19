"""Gift checkout: payment-free stock deduction."""

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
    ShopReceiptKind,
    ShopReceiptStatus,
)
from shops.services import (
    _invalidate_pos_settings_cache,
    complete_shop_checkout,
    get_company_pos_settings,
    open_shop_day,
    return_shop_receipt_items,
    set_company_pos_setting,
)


class GiftCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="100001",
            password="pass",
            email="gift@test.local",
            first_name="Gift",
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
            name="Gift Shop",
            location="Nairobi",
            email="gift-shop@test.local",
            phone_number="0700000001",
            login_code="900001",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        pos = get_company_pos_settings()
        if not pos.enable_gift:
            pos.enable_gift = True
            pos.save(update_fields=["enable_gift", "updated_at"])
        self.item = Item.objects.create(
            name="Gift Mug",
            category="Branded",
            description="Gift fixture",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("500.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("200.00"),
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

    def _checkout_gift(self, **overrides):
        payload = {
            "kind": "gift",
            "login_code": "100001",
            "lines": [{"id": self.item.pk, "qty": 2, "price": "500.00"}],
        }
        payload.update(overrides)
        return complete_shop_checkout(
            shop=self.shop, profile=self.profile, payload=payload
        )

    def test_gift_reduces_stock_and_confirms(self):
        result = self._checkout_gift()
        receipt = result["receipt"]
        self.assertEqual(receipt.kind, ShopReceiptKind.GIFT)
        self.assertEqual(receipt.payment_method, ShopPaymentMethod.NONE)
        self.assertEqual(receipt.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(receipt.total, Decimal("1000.00"))
        self.assertTrue(receipt.receipt_number.startswith("GF"))

        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(stock.quantity, 8)
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, 8)

    def test_gift_client_is_optional(self):
        result = self._checkout_gift()
        receipt = result["receipt"]
        self.assertEqual(receipt.client_name, "")
        self.assertEqual(receipt.client_phone, "")

        with_client = self._checkout_gift(
            client_name="JANE DOE", client_phone="0712345678"
        )["receipt"]
        self.assertEqual(with_client.client_name, "JANE DOE")
        self.assertIsNotNone(with_client.client)

    def test_gift_disabled_by_pos_toggle(self):
        set_company_pos_setting(field="enable_gift", enabled=False)
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        with self.assertRaises(ValidationError):
            self._checkout_gift()

    def test_gift_records_client_without_client_data_toggle(self):
        # Like the trade kinds, a gift always collects who received it.
        set_company_pos_setting(field="enable_client_data", enabled=False)
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        receipt = self._checkout_gift(
            client_name="JANE DOE", client_phone="0712345678"
        )["receipt"]
        self.assertEqual(receipt.client_name, "JANE DOE")
        self.assertIsNotNone(receipt.client)

    def test_gift_rejects_delivery(self):
        with self.assertRaises(ValidationError):
            self._checkout_gift(
                delivery=True,
                client_name="JANE DOE",
                client_phone="0712345678",
                delivery_location="Westlands",
            )

    def test_gift_return_restocks(self):
        receipt = self._checkout_gift()["receipt"]
        line = receipt.lines.get()
        return_shop_receipt_items(
            shop=self.shop,
            receipt_id=receipt.pk,
            payload={
                "reason": "Issued in error",
                "lines": [{"line_id": line.pk, "qty": 2}],
            },
            actor=self.profile,
        )
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(stock.quantity, 10)
