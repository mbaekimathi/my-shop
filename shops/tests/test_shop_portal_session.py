"""Shop portal sessions stay signed in until explicit logout."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop
from shops.session import SHOP_PORTAL_SESSION_AGE, SESSION_SHOP_PORTAL_SHOP_KEY


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class ShopPortalSessionLifetimeTests(TestCase):
    def setUp(self):
        self.password = "portal-session-pass"
        self.user = User.objects.create_user(
            username="800011",
            password=self.password,
            email="portal-session@test.local",
            first_name="PORTAL",
            last_name="CASHIER",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800011",
            phone_country_code="+254",
            phone_number="700000811",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="PORTAL SESSION SHOP",
            location="NAIROBI",
            email="portal-session-shop@test.local",
            phone_number="0700000811",
            login_code="800111",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)

    def _login(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_login_sets_long_lived_shop_session(self):
        client = self._login()
        self.assertTrue(client.session.get("shop_portal_auth"))
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )
        self.assertEqual(client.session.get_expiry_age(), SHOP_PORTAL_SESSION_AGE)
        expiry = client.session.get_expiry_date()
        self.assertGreater(expiry, timezone.now() + timedelta(days=365 * 9))

    def test_shop_floor_use_keeps_session_from_expiring(self):
        client = Client()
        session = client.session
        session[SESSION_SHOP_PORTAL_SHOP_KEY] = str(self.shop.pk)
        session["shop_portal_auth"] = True
        session.set_expiry(60)
        session.save()

        response = client.post(
            reverse(
                "employees:my_shop_verify_login_code",
                kwargs={"shop_id": self.shop.pk},
            ),
            {"login_code": self.profile.employee_id},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("ok"))
        self.assertEqual(client.session.get_expiry_age(), SHOP_PORTAL_SESSION_AGE)
        self.assertGreater(
            client.session.get_expiry_date(),
            timezone.now() + timedelta(days=365 * 9),
        )

    def test_logout_ends_shop_session(self):
        client = self._login()
        response = client.get(reverse("employees:shop_logout"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(client.session.get("shop_portal_auth"))
        self.assertFalse(client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY))

    def test_ping_keeps_shop_portal_session_from_expiring(self):
        client = Client()
        session = client.session
        session[SESSION_SHOP_PORTAL_SHOP_KEY] = str(self.shop.pk)
        session["shop_portal_auth"] = True
        session.set_expiry(60)
        session.save()

        response = client.get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.session.get_expiry_age(), SHOP_PORTAL_SESSION_AGE)

    def test_ping_sets_csrf_cookie(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
        self.assertTrue(response.cookies["csrftoken"].value)

    def test_ping_reports_shop_auth(self):
        client = self._login()
        response = client.get(reverse("employees:ping"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("auth"), "shop")
        self.assertEqual(data.get("login_url"), reverse("employees:shop_login"))

    def test_expired_shop_floor_json_logs_out_to_login(self):
        url = reverse(
            "employees:my_shop_verify_login_code",
            kwargs={"shop_id": self.shop.pk},
        )
        response = Client().post(
            url,
            {"login_code": self.profile.employee_id},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data.get("error"), "session_expired")
        self.assertEqual(data.get("login_url"), reverse("employees:shop_login"))

    def test_expired_shop_floor_html_redirects_to_login(self):
        url = reverse(
            "employees:my_shop_workspace",
            kwargs={"shop_id": self.shop.pk},
        )
        response = Client().get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:shop_login"))


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class ShopPortalLoginSelectTests(TestCase):
    def setUp(self):
        self.password = "portal-select-pass"
        self.user = User.objects.create_user(
            username="800311",
            password=self.password,
            email="portal-select@test.local",
            first_name="PORTAL",
            last_name="SELECT",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800311",
            phone_country_code="+254",
            phone_number="700000831",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SELECTABLE WESTLANDS",
            location="WESTLANDS",
            email="selectable-westlands@test.local",
            phone_number="0700000831",
            login_code="800311",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.other = Shop.objects.create(
            name="SELECTABLE CBD",
            location="CBD",
            email="selectable-cbd@test.local",
            phone_number="0700000832",
            login_code="800312",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.hidden = Shop.objects.create(
            name="HIDDEN BRANCH",
            location="HIDDEN",
            email="hidden-branch@test.local",
            phone_number="0700000833",
            login_code="800313",
            password_hash=make_password(self.password),
            is_hidden=True,
            created_by=self.profile,
        )
        self.suspended = Shop.objects.create(
            name="SUSPENDED BRANCH",
            location="CLOSED",
            email="suspended-branch@test.local",
            phone_number="0700000834",
            login_code="800314",
            password_hash=make_password(self.password),
            is_suspended=True,
            created_by=self.profile,
        )

    def test_login_page_offers_active_shops(self):
        response = Client().get(reverse("employees:login"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="shop_id"', html)
        self.assertIn("SELECTABLE WESTLANDS", html)
        self.assertIn("SELECTABLE CBD", html)
        self.assertNotIn("HIDDEN BRANCH", html)
        self.assertNotIn("SUSPENDED BRANCH", html)
        self.assertNotIn('name="login_code"', html)

    def test_selected_shop_and_password_opens_floor(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"shop_id": str(self.shop.pk), "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop.pk}),
        )
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )

    def test_wrong_password_keeps_the_selected_shop(self):
        response = Client().post(
            reverse("employees:shop_login"),
            {"shop_id": str(self.other.pk), "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Invalid shop or password.", html)
        self.assertRegex(html, rf'value="{self.other.pk}"\s+selected')

    def test_hidden_shop_cannot_be_opened_by_id(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"shop_id": str(self.hidden.pk), "password": self.password},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid shop or password.", response.content.decode())
        self.assertFalse(client.session.get("shop_portal_auth"))

    def test_shop_code_sign_in_still_works(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            client.session.get(SESSION_SHOP_PORTAL_SHOP_KEY), str(self.shop.pk)
        )
