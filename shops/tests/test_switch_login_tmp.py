"""?switch=1 forces the portal login form even when a session exists."""

from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop
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
class SwitchLoginTests(TestCase):
    def setUp(self):
        self.password = "switch-login-pass"
        self.user = User.objects.create_user(
            username="800022",
            password=self.password,
            email="switch-a@test.local",
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
        self.user_b = User.objects.create_user(
            username="800033",
            password=self.password,
            email="switch-b@test.local",
            is_active=True,
        )
        self.profile_b = EmployeeProfile.objects.create(
            user=self.user_b,
            employee_id="800033",
            phone_country_code="+254",
            phone_number="700000833",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SWITCH LOGIN SHOP",
            location="NAIROBI",
            email="switch-login-shop@test.local",
            phone_number="0700000822",
            login_code="800222",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.profile_b.assigned_shops.add(self.shop)

    def _login_shop(self, client):
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _login_employee(self, client):
        response = client.post(
            reverse("employees:login"),
            {"username": self.profile.employee_id, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_shop_login_switch_shows_form_with_shop_session(self):
        client = self._login_shop(Client())
        response = client.get(reverse("employees:shop_login") + "?switch=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["login_mode"], "shop")
        self.assertTrue(response.context["switch"])
        # Existing shop session stays intact while just viewing the form.
        self.assertTrue(client.session.get("shop_portal_auth"))

    def test_shop_login_without_switch_still_redirects(self):
        client = self._login_shop(Client())
        response = client.get(reverse("employees:shop_login"))
        self.assertEqual(response.status_code, 302)

    def test_employee_login_switch_shows_form_with_employee_session(self):
        client = self._login_employee(Client())
        response = client.get(reverse("employees:login") + "?switch=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["login_mode"], "employee")
        self.assertTrue(response.context["switch"])
        self.assertTrue(client.session.get("_auth_user_id"))

    def test_employee_login_without_switch_still_redirects(self):
        client = self._login_employee(Client())
        response = client.get(reverse("employees:login"))
        self.assertEqual(response.status_code, 302)

    def test_employee_switch_post_signs_in_new_user(self):
        client = self._login_employee(Client())
        response = client.post(
            reverse("employees:login"),
            {
                "username": self.profile_b.employee_id,
                "password": self.password,
                "switch": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            client.session.get("_auth_user_id"), str(self.user_b.pk)
        )

    def test_switch_post_keeps_shop_session(self):
        client = self._login_shop(Client())
        client = self._login_employee(client)
        response = client.post(
            reverse("employees:login"),
            {
                "username": self.profile_b.employee_id,
                "password": self.password,
                "switch": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )
