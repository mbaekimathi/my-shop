"""Compulsory clock in (Settings → POS → "Compulsory clock in"):

When the company flag is on, only an employee with an open clock entry can
sell — the floor checkout (``complete_shop_checkout``) and the standalone
POS API (``/pos/api/sales/``) both refuse the sale until they clock in.
"""

import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employees.attendance import clock_in, clock_out, open_entry_for
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from pos.models import Product
from shops.models import Shop, ShopPaymentMethod, ShopReceiptKind
from shops.services import (
    complete_shop_checkout,
    open_shop_day,
    set_company_pos_setting,
)


class ClockInEnforcementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="210777",
            password="clock-pass",
            email="clock@test.local",
            first_name="Clock",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="210777",
            phone_country_code="+254",
            phone_number="700001777",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Clock Shop",
            location="Nairobi",
            email="clock-shop@test.local",
            phone_number="0700001777",
            login_code="210777",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Clock Item",
            category="Parts",
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
                "login_code": "210777",
            },
        )
        set_company_pos_setting(field="require_clock_in", enabled=True)
        self.addCleanup(
            set_company_pos_setting, field="require_clock_in", enabled=False
        )

    def _checkout(self, kind=ShopReceiptKind.SALE):
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": kind,
                "payment_method": ShopPaymentMethod.CASH,
                "login_code": "210777",
                "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
            },
        )

    def test_sale_blocked_until_employee_clocks_in(self):
        self.assertIsNone(open_entry_for(self.profile))
        with self.assertRaises(ValidationError) as ctx:
            self._checkout()
        self.assertIn("Clock in", str(ctx.exception))

        clock_in(self.profile)
        result = self._checkout()
        self.assertTrue(result["receipt_number"])

    def test_clocked_out_entry_does_not_count(self):
        entry = clock_in(self.profile)
        clock_out(entry)
        self.assertIsNone(open_entry_for(self.profile))
        with self.assertRaises(ValidationError):
            self._checkout()

    def test_flag_off_allows_sale_without_clock_in(self):
        set_company_pos_setting(field="require_clock_in", enabled=False)
        result = self._checkout()
        self.assertTrue(result["receipt_number"])


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
        "pos_sale": {"max": 10_000, "window": 60},
    },
)
class PosApiClockInEnforcementTests(TestCase):
    """The standalone POS sale API honours the same compulsory clock-in."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="210778",
            password="pos-clock-pass",
            email="pos-clock@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="210778",
            phone_country_code="+254",
            phone_number="700001778",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        Product.objects.create(
            sku="CLK-1", name="Clock Product", price=Decimal("100.00"), stock=5
        )
        set_company_pos_setting(field="require_clock_in", enabled=True)
        self.addCleanup(
            set_company_pos_setting, field="require_clock_in", enabled=False
        )
        self.client = Client()
        self.client.post(
            reverse("employees:login"),
            {"username": "210778", "password": "pos-clock-pass"},
        )

    def _payload(self, client_id):
        return {
            "client_id": client_id,
            "sold_at": timezone.now().isoformat(),
            "total": "100.00",
            "lines": [
                {
                    "product_sku": "CLK-1",
                    "quantity": 1,
                    "unit_price": "100.00",
                    "line_total": "100.00",
                }
            ],
        }

    def test_sale_create_blocked_until_clocked_in(self):
        url = reverse("pos:sale_create")
        response = self.client.post(
            url,
            data=json.dumps(self._payload("clk-a")),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "clock_in_required")

        clock_in(self.profile)
        response = self.client.post(
            url,
            data=json.dumps(self._payload("clk-b")),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
