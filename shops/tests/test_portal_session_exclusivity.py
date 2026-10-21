"""Shop and employee portal sessions coexist in one browser."""

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
class PortalSessionCoexistenceTests(TestCase):
    def setUp(self):
        self.password = "portal-coexist-pass"
        self.user = User.objects.create_user(
            username="800022",
            password=self.password,
            email="portal-coexist@test.local",
            first_name="PORTAL",
            last_name="COEXIST",
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
            name="PORTAL COEXIST SHOP",
            location="NAIROBI",
            email="portal-coexist-shop@test.local",
            phone_number="0700000822",
            login_code="800222",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.shop_b = Shop.objects.create(
            name="PORTAL COEXIST SHOP B",
            location="MOMBASA",
            email="portal-coexist-shop-b@test.local",
            phone_number="0700000823",
            login_code="800223",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop, self.shop_b)

    def _login_shop(self, client=None, shop=None):
        client = client or Client()
        shop = shop or self.shop
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(shop.pk)
        )
        return client

    def _login_employee(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:login"),
            {"username": self.profile.employee_id, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get("_auth_user_id"))
        return client

    def test_shop_login_keeps_employee_session(self):
        client = self._login_employee()
        self.assertTrue(client.session.get("_auth_user_id"))

        client = self._login_shop(client)
        self.assertTrue(client.session.get("_auth_user_id"))
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_employee_login_keeps_shop_session(self):
        client = self._login_shop()
        self.assertTrue(client.session.get("shop_portal_auth"))

        client = self._login_employee(client)
        self.assertTrue(client.session.get("_auth_user_id"))
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_shop_logout_keeps_employee_session(self):
        client = self._login_employee()
        client = self._login_shop(client)

        response = client.get(reverse("employees:shop_logout"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(client.session.get("shop_portal_auth"))
        self.assertFalse(client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY))
        self.assertTrue(client.session.get("_auth_user_id"))

    def test_employee_logout_keeps_shop_session(self):
        client = self._login_shop()
        client = self._login_employee(client)

        response = client.get(reverse("employees:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(client.session.get("_auth_user_id"))
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_opening_employee_login_keeps_shop_session(self):
        client = self._login_shop()
        response = client.get(reverse("employees:login"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_opening_shop_login_keeps_employee_session(self):
        client = self._login_employee()
        response = client.get(reverse("employees:shop_login"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(client.session.get("_auth_user_id"))

    def test_switch_to_employee_login_keeps_shop_session(self):
        client = self._login_shop()
        response = client.get(reverse("employees:to_employee_login"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:login"))
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_my_shop_entry_keeps_employee_session(self):
        client = self._login_employee()
        response = client.get(reverse("employees:my_shop"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:shop_login"))
        self.assertTrue(client.session.get("_auth_user_id"))

    def test_employee_unlock_other_shop_does_not_clobber_portal(self):
        client = self._login_shop(shop=self.shop)
        client = self._login_employee(client)

        response = client.post(
            reverse("employees:my_shop_select"),
            {"shop_id": self.shop_b.pk, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop_b.pk}),
        )
        self.assertEqual(client.session.get("active_shop_id"), str(self.shop_b.pk))
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

        portal_workspace = client.get(
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop.pk})
        )
        self.assertEqual(portal_workspace.status_code, 200)

        employee_workspace = client.get(
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop_b.pk})
        )
        self.assertEqual(employee_workspace.status_code, 200)

    def test_ping_reports_both_auths(self):
        client = self._login_shop()
        client = self._login_employee(client)
        response = client.get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("shop_auth"))
        self.assertTrue(data.get("employee_auth"))
        self.assertIn(data.get("auth"), {"shop", "employee"})

    def _stale_client(self, session_key):
        """A client still holding a retired sessionid cookie (in-flight tab)."""
        from django.conf import settings

        stale = Client()
        stale.cookies[settings.SESSION_COOKIE_NAME] = session_key
        return stale

    def test_stale_cookie_after_employee_login_still_resolves(self):
        """A second tab's in-flight request survives the login key rotation."""
        client = self._login_shop()
        old_key = client.session.session_key
        client = self._login_employee(client)
        self.assertNotEqual(old_key, client.session.session_key)

        response = self._stale_client(old_key).get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("shop_auth"))
        self.assertTrue(data.get("employee_auth"))

    def test_stale_cookie_after_shop_login_still_resolves(self):
        client = self._login_employee()
        old_key = client.session.session_key
        client = self._login_shop(client)
        self.assertNotEqual(old_key, client.session.session_key)

        response = self._stale_client(old_key).get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("shop_auth"))
        self.assertTrue(data.get("employee_auth"))

    def test_stale_cookie_can_open_shop_workspace(self):
        client = self._login_shop()
        old_key = client.session.session_key
        client = self._login_employee(client)

        response = self._stale_client(old_key).get(
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_stale_cookie_after_employee_logout_resolves_to_current(self):
        """Logout flushes the key; a lagging tab still sees the surviving shop session."""
        client = self._login_shop()
        client = self._login_employee(client)
        old_key = client.session.session_key
        client.get(reverse("employees:logout"))

        response = self._stale_client(old_key).get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("shop_auth"))
        self.assertFalse(data.get("employee_auth"))

    def test_employee_login_over_other_user_session_keeps_shop_keys(self):
        """login() flushes a different user's session — shop keys must survive."""
        from django.contrib.auth import (
            BACKEND_SESSION_KEY,
            HASH_SESSION_KEY,
            SESSION_KEY,
        )
        from django.test import RequestFactory

        from employees.portal_auth import begin_employee_session

        other = User.objects.create_user(
            username="900044",
            password="other-pass",
            email="portal-other@test.local",
            is_active=True,
        )
        client = self._login_shop()

        request = RequestFactory().post(reverse("employees:login"))
        request.session = client.session
        request.session[SESSION_KEY] = str(other.pk)
        request.session[BACKEND_SESSION_KEY] = (
            "django.contrib.auth.backends.ModelBackend"
        )
        request.session[HASH_SESSION_KEY] = other.get_session_auth_hash()

        begin_employee_session(request, self.user, self.profile)
        self.assertTrue(request.session.get("shop_portal_auth"))
        self.assertEqual(
            request.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )
