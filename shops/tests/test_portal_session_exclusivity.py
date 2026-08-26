"""Shop and employee portal sessions are mutually exclusive."""

from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class PortalSessionExclusivityTests(TestCase):
    def setUp(self):
        self.password = "portal-exclusive-pass"
        self.user = User.objects.create_user(
            username="800022",
            password=self.password,
            email="portal-exclusive@test.local",
            first_name="PORTAL",
            last_name="EXCLUSIVE",
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
            name="PORTAL EXCLUSIVE SHOP",
            location="NAIROBI",
            email="portal-exclusive-shop@test.local",
            phone_number="0700000822",
            login_code="800222",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)

    def _login_shop(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(client.session.get("active_shop_id"), str(self.shop.pk))
        return client

    def _login_employee(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:login"),
            {"username": self.profile.employee_id, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get("_auth_user_id"))
        self.assertFalse(client.session.get("shop_portal_auth"))
        return client

    def test_shop_login_clears_employee_session(self):
        client = self._login_employee()
        self.assertTrue(client.session.get("_auth_user_id"))

        client = self._login_shop(client)
        self.assertFalse(client.session.get("_auth_user_id"))
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(client.session.get("active_shop_id"), str(self.shop.pk))

    def test_employee_login_clears_shop_session(self):
        client = self._login_shop()
        self.assertTrue(client.session.get("shop_portal_auth"))

        client = self._login_employee(client)
        self.assertTrue(client.session.get("_auth_user_id"))
        self.assertFalse(client.session.get("shop_portal_auth"))
        self.assertFalse(client.session.get("active_shop_id"))

    def test_opening_employee_login_ends_shop_session(self):
        client = self._login_shop()
        response = client.get(reverse("employees:login"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(client.session.get("shop_portal_auth"))
        self.assertFalse(client.session.get("active_shop_id"))

    def test_opening_shop_login_ends_employee_session(self):
        client = self._login_employee()
        response = client.get(reverse("employees:shop_login"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(client.session.get("_auth_user_id"))

    def test_switch_to_employee_login_ends_shop_session(self):
        client = self._login_shop()
        response = client.get(reverse("employees:to_employee_login"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:login"))
        self.assertFalse(client.session.get("shop_portal_auth"))
        self.assertFalse(client.session.get("active_shop_id"))
        self.assertFalse(client.session.get("_auth_user_id"))

    def test_my_shop_entry_ends_employee_session(self):
        client = self._login_employee()
        response = client.get(reverse("employees:my_shop"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:shop_login"))
        self.assertFalse(client.session.get("_auth_user_id"))
        self.assertFalse(client.session.get("shop_portal_auth"))
