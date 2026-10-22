"""One account = one live session: a newer sign-in supersedes older devices."""

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
class SingleSessionTests(TestCase):
    def setUp(self):
        self.password = "single-session-pass"
        self.user = User.objects.create_user(
            username="800077",
            password=self.password,
            email="single-session@test.local",
            first_name="SINGLE",
            last_name="SESSION",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800077",
            phone_country_code="+254",
            phone_number="700000877",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SINGLE SESSION SHOP",
            location="NAIROBI",
            email="single-session-shop@test.local",
            phone_number="0700000877",
            login_code="800277",
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
        return client

    def _login_employee(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:login"),
            {"username": self.profile.employee_id, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _ping(self, client):
        response = client.get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_login_stamps_session_key(self):
        client = self._login_employee()
        self.profile.refresh_from_db()
        self.assertEqual(
            self.profile.active_session_key, client.session.session_key
        )

        client = self._login_shop()
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.active_session_key, client.session.session_key)

    def test_second_employee_login_supersedes_first_device(self):
        client_a = self._login_employee()
        self.assertTrue(self._ping(client_a).get("employee_auth"))

        self._login_employee(Client())

        data = self._ping(client_a)
        self.assertFalse(data.get("employee_auth"))
        self.assertEqual(data.get("auth"), "none")

        # The superseded device is also logged out of gated pages.
        response = client_a.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("employees:login")))

    def test_second_shop_login_supersedes_first_device(self):
        client_a = self._login_shop()
        self.assertTrue(self._ping(client_a).get("shop_auth"))

        self._login_shop(Client())

        data = self._ping(client_a)
        self.assertFalse(data.get("shop_auth"))
        self.assertEqual(data.get("auth"), "none")

        response = client_a.get(
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("employees:shop_login")))

    def test_other_shop_sessions_unaffected(self):
        shop_b = Shop.objects.create(
            name="SINGLE SESSION SHOP B",
            location="MOMBASA",
            email="single-session-shop-b@test.local",
            phone_number="0700000878",
            login_code="800278",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        client_a = self._login_shop()
        client_b = Client()
        client_b.post(
            reverse("employees:shop_login"),
            {"login_code": shop_b.login_code, "password": self.password},
        )
        self.assertTrue(self._ping(client_a).get("shop_auth"))
        self.assertTrue(self._ping(client_b).get("shop_auth"))

    def test_coexisting_portals_survive_second_login(self):
        """Shop+employee in one browser: each login must migrate the other's stamp."""
        client = self._login_employee()
        client = self._login_shop(client)
        data = self._ping(client)
        self.assertTrue(data.get("shop_auth"))
        self.assertTrue(data.get("employee_auth"))

    def test_coexisting_portals_survive_employee_second(self):
        client = self._login_shop()
        client = self._login_employee(client)
        data = self._ping(client)
        self.assertTrue(data.get("shop_auth"))
        self.assertTrue(data.get("employee_auth"))

    def test_superseded_logout_keeps_newer_stamp(self):
        client_a = self._login_shop()
        client_b = self._login_shop(Client())
        new_key = client_b.session.session_key

        # Superseded device signing out must not clear the live stamp.
        client_a.get(reverse("employees:shop_logout"))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.active_session_key, new_key)
        self.assertTrue(self._ping(client_b).get("shop_auth"))

        # The owning device's logout releases the stamp.
        client_b.get(reverse("employees:shop_logout"))
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.active_session_key, "")

    def test_employee_logout_releases_stamp(self):
        client = self._login_employee()
        client.get(reverse("employees:logout"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.active_session_key, "")

    def test_superseded_employee_logout_keeps_newer_stamp(self):
        client_a = self._login_employee()
        client_b = self._login_employee(Client())
        new_key = client_b.session.session_key

        client_a.get(reverse("employees:logout"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.active_session_key, new_key)
        self.assertTrue(self._ping(client_b).get("employee_auth"))

    def test_portal_keys_cleared_on_superseded_session(self):
        client_a = self._login_shop()
        self._login_shop(Client())
        self._ping(client_a)
        self.assertFalse(client_a.session.get("shop_portal_auth"))
        self.assertFalse(client_a.session.get(SESSION_SHOP_PORTAL_SHOP_KEY))
