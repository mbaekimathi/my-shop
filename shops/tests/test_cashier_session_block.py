"""Next-cashier lockout: a cashier can't use the workspace while a
colleague's day session is still open on an allocated shop.

When the previous cashier never closed their ShopDaySession, the next
cashier sees a blocking notice naming who must close it — every
/shop-cashier/* page renders the notice, and the my-shop/* endpoints a
cashier can reach at allocation level (day toggle, checkout, catalog,
expenses, drawings) deny the request. Portal sessions and non-cashier
roles are unaffected so the day can always be closed as an escape hatch.
"""

from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop
from shops.services import close_shop_day, get_open_shop_day, open_shop_day


def _employee(username, *, role, first_name, last_name, password):
    user = User.objects.create_user(
        username=username,
        password=password,
        email=f"{username}@test.local",
        first_name=first_name,
        last_name=last_name,
        is_active=True,
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=username,
        phone_country_code="+254",
        phone_number="700001001",
        status=EmployeeStatus.ACTIVE,
        role=role,
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
class CashierSessionBlockTests(TestCase):
    PASSWORD = "session-block-pass"

    def setUp(self):
        self.previous = _employee(
            "860001",
            role=EmployeeRole.SHOP_CASHIER,
            first_name="PREVIOUS",
            last_name="CASHIER",
            password=self.PASSWORD,
        )
        self.next = _employee(
            "860002",
            role=EmployeeRole.SHOP_CASHIER,
            first_name="NEXT",
            last_name="CASHIER",
            password=self.PASSWORD,
        )
        self.manager = _employee(
            "860003",
            role=EmployeeRole.SHOP_MANAGER,
            first_name="SHOP",
            last_name="MANAGER",
            password=self.PASSWORD,
        )
        self.shop = Shop.objects.create(
            name="BLOCK SHOP",
            location="NAIROBI",
            email="block-shop@test.local",
            phone_number="0700008601",
            login_code="870860",
            password_hash=make_password(self.PASSWORD),
            created_by=self.previous,
        )
        for profile in (self.previous, self.next, self.manager):
            profile.assigned_shops.add(self.shop)

    def _login(self, profile, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:login"),
            {"username": profile.employee_id, "password": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _login_shop(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _open_day(self, profile=None):
        profile = profile or self.previous
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": profile.employee_id,
            },
        )

    def _day_toggle_url(self):
        return reverse("employees:my_shop_day_toggle", kwargs={"shop_id": self.shop.pk})

    def test_dashboard_blocked_for_next_cashier(self):
        self._open_day()
        client = self._login(self.next)
        response = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "employees/cashier_session_blocked.html")
        self.assertContains(response, "still open under PREVIOUS CASHIER")
        self.assertContains(response, "close their session")
        self.assertNotContains(response, "Quick actions")
        self.assertNotContains(response, "Day summary")

    def test_dashboard_ok_for_session_owner(self):
        self._open_day()
        client = self._login(self.previous)
        response = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Day summary")

    def test_dashboard_ok_when_day_closed(self):
        self._open_day()
        close_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "card_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": self.previous.employee_id,
            },
        )
        client = self._login(self.next)
        response = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick actions")

    def test_cashier_settings_and_day_picker_blocked(self):
        self._open_day()
        client = self._login(self.next)
        for url in (
            reverse("employees:role_shop_cashier_settings"),
            reverse("employees:role_shop_day", kwargs={"role_segment": "shop-cashier"}),
        ):
            response = client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertTemplateUsed(
                response, "employees/cashier_session_blocked.html"
            )

    def test_cashier_post_denied_with_json(self):
        self._open_day()
        client = self._login(self.next)
        response = client.post(
            reverse("employees:role_shop_cashier_settings"),
            {"action": "toggle_pos_setting", "field": "sidebar_sell", "enabled": "0"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])
        self.assertIn("PREVIOUS CASHIER", response.json()["error"])

    def test_day_toggle_denied_for_next_cashier(self):
        self._open_day()
        client = self._login(self.next)
        response = client.get(self._day_toggle_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:role_shop_cashier"))

        response = client.post(
            self._day_toggle_url(),
            {
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "card_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": "1",
                "login_code": self.next.employee_id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:role_shop_cashier"))
        self.assertIsNotNone(get_open_shop_day(self.shop))

    def test_checkout_denied_for_next_cashier(self):
        self._open_day()
        client = self._login(self.next)
        response = client.post(
            reverse("employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}),
            {"kind": "sale", "login_code": self.next.employee_id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("PREVIOUS CASHIER", response.json()["error"])

    def test_catalog_denied_for_next_cashier(self):
        self._open_day()
        client = self._login(self.next)
        response = client.get(
            reverse("employees:my_shop_catalog", kwargs={"shop_id": self.shop.pk})
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("PREVIOUS CASHIER", response.json()["error"])

    def test_manager_still_reaches_day_toggle(self):
        self._open_day()
        client = self._login(self.manager)
        response = client.get(self._day_toggle_url())
        self.assertEqual(response.status_code, 200)

    def test_shop_portal_still_reaches_day_toggle(self):
        self._open_day()
        client = self._login_shop()
        response = client.get(self._day_toggle_url())
        self.assertEqual(response.status_code, 200)

    def test_next_cashier_unblocked_once_owner_closes(self):
        self._open_day()
        client = self._login(self.next)
        self.assertEqual(
            client.get(self._day_toggle_url()).status_code, 302
        )
        close_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "card_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": self.previous.employee_id,
            },
        )
        response = client.get(self._day_toggle_url())
        self.assertEqual(response.status_code, 200)

    def test_second_shop_without_foreign_session_still_blocks(self):
        """One foreign session anywhere locks the whole cashier workspace."""
        other = Shop.objects.create(
            name="BLOCK SHOP TWO",
            location="NAKURU",
            email="block-shop-two@test.local",
            phone_number="0700008602",
            login_code="870862",
            password_hash=make_password(self.PASSWORD),
            created_by=self.previous,
        )
        self.next.assigned_shops.add(other)
        self._open_day()
        client = self._login(self.next)
        response = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "employees/cashier_session_blocked.html")
        self.assertContains(response, "BLOCK SHOP")
