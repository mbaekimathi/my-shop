"""Cashier dashboard Sell popup: link, modal context, and checkout access.

The /shop-cashier/ dashboard shows a "Sell" quick action that opens a cart
popup — live catalog search, sale/credit/quotation, client lookup — and posts
to the same ``my_shop_checkout`` endpoint as the shop floor. The endpoint
allows allocation-level access (no floor unlock): the staff ID in the payload
authorises the write, same as the buy-stock / register-expense popups.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import (
    Shop,
    ShopReceipt,
    ShopReceiptKind,
)
from shops.services import (
    _invalidate_pos_settings_cache,
    open_shop_day,
    set_company_pos_setting,
    upsert_client,
)


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class CashierDashboardSellTests(TestCase):
    PASSWORD = "cashier-sell-pass"

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="840001",
            password=self.PASSWORD,
            email="840001@test.local",
            first_name="Sell",
            last_name="Cashier",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840001",
            phone_country_code="+254",
            phone_number="700000840",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SELL SHOP",
            location="NAIROBI",
            email="sell-shop@test.local",
            phone_number="0700008401",
            login_code="870840",
            password_hash=make_password("pass"),
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Sell Item",
            category="Parts",
            description="Sell fixture",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.cashier,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )
        self.client = Client()
        self.client.post(
            reverse("employees:login"),
            {"username": "840001", "password": self.PASSWORD},
        )

    def _open_day(self):
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "840001",
            },
        )

    def _checkout_url(self):
        return reverse("employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk})

    def test_dashboard_shows_sell_action_and_modal(self):
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        actions = [
            item["action"]
            for item in response.context["page_sidebar"]["primary"]
            if item.get("type") == "action"
        ]
        self.assertIn("sell", actions)
        self.assertTrue(response.context["sell_modal"])
        self.assertContains(response, 'data-modal="sell"')
        self.assertContains(response, 'data-modal-open="sell"')
        self.assertContains(response, "cashier-sell.js")

    def test_sell_action_hidden_by_sidebar_pref(self):
        self.cashier.sidebar_prefs = {"sell": False}
        self.cashier.save(update_fields=["sidebar_prefs"])
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        actions = [
            item["action"]
            for item in response.context["page_sidebar"]["primary"]
            if item.get("type") == "action"
        ]
        self.assertNotIn("sell", actions)

    def test_sell_modal_absent_for_multi_shop_cashier(self):
        other = Shop.objects.create(
            name="SELL SHOP TWO",
            location="NAKURU",
            email="sell-shop-two@test.local",
            phone_number="0700008402",
            login_code="870842",
            password_hash=make_password("pass"),
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(other)
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sell_modal", response.context)
        # The quick action still renders — it falls back to its data-href.
        actions = [
            item["action"]
            for item in response.context["page_sidebar"]["primary"]
            if item.get("type") == "action"
        ]
        self.assertIn("sell", actions)

    def test_checkout_without_floor_unlock(self):
        """Allocated cashier can check out from the dashboard — no floor session."""
        self._open_day()
        response = self.client.post(
            self._checkout_url(),
            data=json.dumps(
                {
                    "kind": ShopReceiptKind.SALE,
                    "payment_method": "cash",
                    "login_code": "840001",
                    "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        receipt = ShopReceipt.objects.get(receipt_number=data["receipt_number"])
        self.assertEqual(receipt.shop_id, self.shop.pk)
        self.assertEqual(
            ShopStock.objects.get(shop=self.shop, item=self.item).quantity,
            Decimal("9"),
        )

    def test_checkout_requires_valid_staff_code(self):
        self._open_day()
        response = self.client.post(
            self._checkout_url(),
            data=json.dumps(
                {
                    "kind": ShopReceiptKind.SALE,
                    "payment_method": "cash",
                    "login_code": "999999",
                    "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_client_lookup_without_floor_unlock(self):
        upsert_client(full_name="JANE DOE", phone="0712345678")
        url = reverse(
            "employees:my_shop_client_lookup", kwargs={"shop_id": self.shop.pk}
        )
        by_phone = self.client.get(url, {"phone": "0712345678"})
        self.assertEqual(by_phone.status_code, 200)
        self.assertTrue(by_phone.json()["found"])
        by_name = self.client.get(url, {"name": "JANE"})
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(
            by_name.json()["results"][0]["full_name"], "JANE DOE"
        )

    def test_catalog_search_without_floor_unlock(self):
        url = reverse("employees:my_shop_catalog", kwargs={"shop_id": self.shop.pk})
        response = self.client.get(url, {"q": "Sell Item"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["items"][0]["id"], self.item.pk)


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class CashierSellPosToggleTests(TestCase):
    """The dashboard Sell popup is exempt from company/shop POS toggles."""

    PASSWORD = "cashier-toggle-pass"

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="840002",
            password=self.PASSWORD,
            email="840002@test.local",
            first_name="Toggle",
            last_name="Cashier",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840002",
            phone_country_code="+254",
            phone_number="700000842",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="TOGGLE SHOP",
            location="NAIROBI",
            email="toggle-shop@test.local",
            phone_number="0700008403",
            login_code="870843",
            password_hash=make_password("pass"),
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Toggle Item",
            category="Parts",
            description="Toggle fixture",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.cashier,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )
        self.client = Client()
        self.client.post(
            reverse("employees:login"),
            {"username": "840002", "password": self.PASSWORD},
        )
        self.checkout_url = reverse(
            "employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}
        )

    def _open_day(self):
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "840002",
            },
        )

    def _payload(self, kind, **extra):
        body = {
            "kind": kind,
            "dashboard_popup": True,
            "payment_method": "cash",
            "client_name": "JANE DOE",
            "login_code": "840002",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }
        body.update(extra)
        return body

    def _disable_all_toggles(self):
        for field in (
            "enable_credit",
            "enable_quotation",
            "enable_mpesa",
            "enable_card",
            "enable_client_data",
            "enable_discount",
        ):
            set_company_pos_setting(field=field, enabled=False)
        _invalidate_pos_settings_cache()

    def test_modal_shows_all_kinds_and_payments_when_toggles_off(self):
        self._disable_all_toggles()
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["sell_modal"])
        self.assertContains(response, 'value="credit" data-sell-kind')
        self.assertContains(response, 'value="quotation" data-sell-kind')
        self.assertContains(response, 'value="mpesa" data-sell-pay')
        self.assertContains(response, "data-sell-client-phone")

    def test_dashboard_credit_checkout_with_kind_disabled(self):
        set_company_pos_setting(field="enable_credit", enabled=False)
        self._open_day()
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(self._payload(ShopReceiptKind.CREDIT)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        receipt = ShopReceipt.objects.get(receipt_number=data["receipt_number"])
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)

    def test_dashboard_mpesa_sale_with_payment_disabled(self):
        set_company_pos_setting(field="enable_mpesa", enabled=False)
        self._open_day()
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(
                self._payload(ShopReceiptKind.SALE, payment_method="mpesa")
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_sell_pref_hides_kind_and_blocks_checkout(self):
        """Cashier settings Sell toggles gate the popup and the endpoint."""
        self.cashier.sidebar_prefs = {"sell_credit": False}
        self.cashier.save(update_fields=["sidebar_prefs"])
        self._open_day()
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="credit" data-sell-kind')
        self.assertContains(response, 'value="sale" data-sell-kind')
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(self._payload(ShopReceiptKind.CREDIT)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_sell_pref_hides_payment_and_blocks_it(self):
        self.cashier.sidebar_prefs = {"sell_pay_mpesa": False}
        self.cashier.save(update_fields=["sidebar_prefs"])
        self._open_day()
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertNotContains(response, 'value="mpesa" data-sell-pay')
        self.assertContains(response, 'value="cash" data-sell-pay')
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(
                self._payload(ShopReceiptKind.SALE, payment_method="mpesa")
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_edit_prices_pref_off_locks_prices(self):
        self.cashier.sidebar_prefs = {"sell_edit_prices": False}
        self.cashier.save(update_fields=["sidebar_prefs"])
        self._open_day()
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertContains(response, 'data-pos-discount="0"')
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(
                self._payload(ShopReceiptKind.SALE)
                | {"lines": [{"id": self.item.pk, "qty": 1, "price": "60"}]}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        receipt = ShopReceipt.objects.get(receipt_number=data["receipt_number"])
        self.assertEqual(receipt.lines.first().unit_price, Decimal("100.00"))

    def test_flag_ignored_with_unlocked_floor_session(self):
        """A floor session must not smuggle the bypass flag past the toggles."""
        set_company_pos_setting(field="enable_credit", enabled=False)
        session = self.client.session
        session["active_shop_id"] = str(self.shop.pk)
        session.save()
        self._open_day()
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(self._payload(ShopReceiptKind.CREDIT)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(
            ShopReceipt.objects.filter(
                shop=self.shop, kind=ShopReceiptKind.CREDIT
            ).exists()
        )
