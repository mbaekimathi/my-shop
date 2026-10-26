"""Shop portal quick-switch endpoint (/my-shop/switch/)."""

from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop
from shops.services import set_company_pos_setting
from shops.session import SESSION_SHOP_PORTAL_SHOP_KEY


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class ShopSwitchTests(TestCase):
    def setUp(self):
        self.password = "switch-pass-1"
        self.other_password = "switch-pass-2"
        self.user = User.objects.create_user(
            username="800022",
            password=self.password,
            email="shop-switch@test.local",
            first_name="SWITCH",
            last_name="CASHIER",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800022",
            phone_country_code="+254",
            phone_number="700000822",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SWITCH SHOP ONE",
            location="NAIROBI",
            email="switch-shop-1@test.local",
            phone_number="0700000822",
            login_code="800221",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.other_shop = Shop.objects.create(
            name="SWITCH SHOP TWO",
            location="MOMBASA",
            email="switch-shop-2@test.local",
            phone_number="0700000823",
            login_code="800222",
            password_hash=make_password(self.other_password),
            created_by=self.profile,
        )
        self.url = reverse("employees:my_shop_switch")
        set_company_pos_setting(
            field="enable_passwordless_shop_switch", enabled=False
        )

    def tearDown(self):
        set_company_pos_setting(
            field="enable_passwordless_shop_switch", enabled=False
        )

    def _login(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_get_requires_portal_session(self):
        response = Client().get(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("error"), "session_expired")

    def test_get_lists_active_shops(self):
        client = self._login()
        response = client.get(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertFalse(data.get("passwordless"))
        self.assertEqual(data.get("current_shop_id"), str(self.shop.pk))
        ids = {row["id"] for row in data.get("shops", [])}
        self.assertIn(str(self.shop.pk), ids)
        self.assertIn(str(self.other_shop.pk), ids)

    def test_post_requires_password_when_toggle_off(self):
        client = self._login()
        response = client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk), "password": "wrong"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_post_switches_with_correct_password(self):
        client = self._login()
        response = client.post(
            self.url,
            {
                "shop_id": str(self.other_shop.pk),
                "password": self.other_password,
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(
            data.get("redirect"),
            reverse(
                "employees:my_shop_workspace",
                kwargs={"shop_id": self.other_shop.pk},
            ),
        )
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY),
            str(self.other_shop.pk),
        )

    def test_post_switches_without_password_when_toggle_on(self):
        set_company_pos_setting(
            field="enable_passwordless_shop_switch", enabled=True
        )
        client = self._login()
        response = client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk)},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("ok"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY),
            str(self.other_shop.pk),
        )

    def test_post_rejects_unknown_shop(self):
        client = self._login()
        response = client.post(
            self.url,
            {"shop_id": "999999", "password": self.other_password},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
