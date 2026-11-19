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


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class EmployeeShopSwitchTests(TestCase):
    """Employees acting in a shop-scoped role re-pick their session *view*
    shop (``employee_acting_shop``) via the same endpoint — no floor session
    is touched and no shop password is asked."""

    def setUp(self):
        self.password = "switch-pass-1"
        self.other_password = "switch-pass-2"
        self.user = User.objects.create_user(
            username="800033",
            password=self.password,
            email="emp-switch@test.local",
            first_name="SWITCH",
            last_name="MANAGER",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800033",
            phone_country_code="+254",
            phone_number="700000833",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="EMP SWITCH SHOP ONE",
            location="NAIROBI",
            email="emp-switch-shop-1@test.local",
            phone_number="0700000833",
            login_code="800331",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.other_shop = Shop.objects.create(
            name="EMP SWITCH SHOP TWO",
            location="MOMBASA",
            email="emp-switch-shop-2@test.local",
            phone_number="0700000834",
            login_code="800332",
            password_hash=make_password(self.other_password),
            created_by=self.profile,
        )
        self.third_shop = Shop.objects.create(
            name="EMP SWITCH SHOP THREE",
            location="KISUMU",
            email="emp-switch-shop-3@test.local",
            phone_number="0700000835",
            login_code="800333",
            password_hash=make_password("switch-pass-3"),
            created_by=self.profile,
        )
        self.url = reverse("employees:my_shop_switch")
        self.client.force_login(self.user)

    def _acting(self):
        return self.client.session.get("employee_acting_shop")

    def test_get_lists_only_allocated_shops(self):
        self.profile.assigned_shops.add(self.shop, self.other_shop)
        response = self.client.get(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("mode"), "view")
        self.assertTrue(data.get("passwordless"))
        self.assertEqual(data.get("current_shop_id"), "")
        ids = {row["id"] for row in data.get("shops", [])}
        self.assertEqual(
            ids, {str(self.shop.pk), str(self.other_shop.pk)}
        )
        self.assertEqual((data.get("all_option") or {}).get("id"), "")

    def test_get_lists_all_active_shops_when_unallocated(self):
        response = self.client.get(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = {row["id"] for row in data.get("shops", [])}
        self.assertIn(str(self.third_shop.pk), ids)
        self.assertIsNone(data.get("all_option"))

    def test_post_allocated_shop_sets_view_pick(self):
        self.profile.assigned_shops.add(self.shop, self.other_shop)
        response = self.client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk)},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(
            data.get("redirect"), reverse("employees:role_shop_manager")
        )
        self.assertEqual(self._acting(), str(self.other_shop.pk))
        # The floor session is untouched — this is view-only.
        self.assertIsNone(self.client.session.get("active_shop_id"))

    def test_post_unallocated_shop_denied_when_allocated(self):
        self.profile.assigned_shops.add(self.shop)
        response = self.client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk)},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._acting())

    def test_post_any_shop_when_unallocated(self):
        response = self.client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk)},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._acting(), str(self.other_shop.pk))

    def test_post_empty_shop_clears_view_pick(self):
        session = self.client.session
        session["employee_acting_shop"] = str(self.shop.pk)
        session.save()
        response = self.client.post(
            self.url,
            {"shop_id": ""},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._acting())

    def test_view_pick_narrows_actionable_shops(self):
        from items.services import actionable_shops_for_profile

        self.profile.assigned_shops.add(self.shop, self.other_shop)
        self.profile.acting_shop_id = str(self.other_shop.pk)
        shops = actionable_shops_for_profile(self.profile)
        self.assertEqual([shop.pk for shop in shops], [self.other_shop.pk])
        # A pick outside the allocation is ignored entirely.
        self.profile.acting_shop_id = str(self.third_shop.pk)
        shops = actionable_shops_for_profile(self.profile)
        self.assertEqual(
            {shop.pk for shop in shops},
            {self.shop.pk, self.other_shop.pk},
        )

    def test_view_pick_survives_requests_for_single_role(self):
        """Single-role staff get acting_shop_id applied too (no role picker)."""
        self.profile.assigned_shops.add(self.shop, self.other_shop)
        self.client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk)},
            HTTP_ACCEPT="application/json",
        )
        from employees.access import get_profile_for_request
        from django.test import RequestFactory

        request = RequestFactory().get("/shop-manager/")
        request.session = self.client.session
        request.user = self.user
        profile = get_profile_for_request(request)
        self.assertEqual(
            getattr(profile, "acting_shop_id", None), str(self.other_shop.pk)
        )

    def test_get_denied_for_non_shop_scoped_role(self):
        rider_user = User.objects.create_user(
            username="800044",
            password=self.password,
            email="emp-switch-rider@test.local",
            is_active=True,
        )
        rider = EmployeeProfile.objects.create(
            user=rider_user,
            employee_id="800044",
            phone_country_code="+254",
            phone_number="700000844",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.RIDER,
        )
        self.client.force_login(rider.user)
        response = self.client.get(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            self.url,
            {"shop_id": str(self.other_shop.pk)},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
