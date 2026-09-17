"""Compulsory shop day open/close gates selling."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import Shop, ShopDaySession, ShopPaymentMethod, ShopReceiptKind
from shops.services import (
    close_shop_day,
    complete_shop_checkout,
    open_shop_day,
    require_shop_day_for_sale,
    shop_day_floor_state,
)


class CompulsoryShopDayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="200001",
            password="pass",
            email="day@test.local",
            first_name="Day",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200001",
            phone_country_code="+254",
            phone_number="700000201",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Day Shop",
            location="Nairobi",
            email="day-shop@test.local",
            phone_number="0700000201",
            login_code="200001",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Day Item",
            category="Parts",
            description="Day fixture",
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

    def _open_payload(self):
        return {
            "cash_amount": "1000",
            "mpesa_amount": "500",
            "credit_amount": "0",
            "stock_confirmed": True,
            "login_code": "200001",
        }

    def _sale_payload(self):
        return {
            "kind": ShopReceiptKind.SALE,
            "payment_method": ShopPaymentMethod.CASH,
            "cash_amount": "100",
            "mpesa_amount": "0",
            "login_code": "200001",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }

    def test_sale_blocked_when_shop_closed(self):
        state = shop_day_floor_state(shop=self.shop)
        self.assertFalse(state["can_trade"])
        self.assertEqual(state["mode"], "open")
        with self.assertRaises(ValidationError) as ctx:
            require_shop_day_for_sale(shop=self.shop)
        self.assertIn("Open the shop day", str(ctx.exception))
        with self.assertRaises(ValidationError):
            complete_shop_checkout(
                shop=self.shop, profile=self.profile, payload=self._sale_payload()
            )

    def test_sale_allowed_after_open(self):
        open_shop_day(shop=self.shop, payload=self._open_payload())
        state = shop_day_floor_state(shop=self.shop)
        self.assertTrue(state["can_trade"])
        result = complete_shop_checkout(
            shop=self.shop, profile=self.profile, payload=self._sale_payload()
        )
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)

    def test_stale_open_blocks_sale_until_close_then_open(self):
        open_shop_day(shop=self.shop, payload=self._open_payload())
        session = ShopDaySession.objects.get(shop=self.shop, closed_at__isnull=True)
        ShopDaySession.objects.filter(pk=session.pk).update(
            opened_at=timezone.now() - timedelta(days=1)
        )

        state = shop_day_floor_state(shop=self.shop)
        self.assertTrue(state["stale_open"])
        self.assertFalse(state["can_trade"])
        self.assertEqual(state["mode"], "close")
        with self.assertRaises(ValidationError) as ctx:
            complete_shop_checkout(
                shop=self.shop, profile=self.profile, payload=self._sale_payload()
            )
        self.assertIn("yesterday", str(ctx.exception).lower())

        close_shop_day(shop=self.shop, payload=self._open_payload())
        state = shop_day_floor_state(shop=self.shop)
        self.assertFalse(state["is_open"])
        self.assertEqual(state["mode"], "open")

        open_shop_day(shop=self.shop, payload=self._open_payload())
        state = shop_day_floor_state(shop=self.shop)
        self.assertTrue(state["can_trade"])
        result = complete_shop_checkout(
            shop=self.shop, profile=self.profile, payload=self._sale_payload()
        )
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)

    def test_quotation_allowed_when_closed(self):
        payload = self._sale_payload()
        payload["kind"] = ShopReceiptKind.QUOTATION
        payload["client_name"] = "QUOTE CLIENT"
        result = complete_shop_checkout(
            shop=self.shop, profile=self.profile, payload=payload
        )
        self.assertEqual(result["kind"], ShopReceiptKind.QUOTATION)
