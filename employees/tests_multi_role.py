"""Multi-role employees: extra_roles grants and the session role picker."""

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.access import (
    SESSION_ACTIVE_ROLE_KEY,
    SESSION_ACTING_SHOP_KEY,
    SESSION_PROFILE_KEY,
)
from employees.models import (
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
    join_extra_roles,
    split_extra_roles,
)
from employees.services import update_employee


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class MultiRoleHelpersTests(TestCase):
    def test_split_extra_roles_orders_and_dedupes(self):
        raw = "shop_cashier,employee,shop_cashier,bogus"
        self.assertEqual(
            split_extra_roles(raw),
            [EmployeeRole.EMPLOYEE, EmployeeRole.SHOP_CASHIER],
        )

    def test_join_extra_roles_excludes_primary(self):
        self.assertEqual(
            join_extra_roles(
                ["shop_cashier", "company_store_manager", "employee"],
                exclude="employee",
            ),
            "company_store_manager,shop_cashier",
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
class MultiRoleLoginTests(TestCase):
    PASSWORD = "multi-role-pass"

    def _make_employee(self, employee_id, role, *, extra_roles=""):
        user = User.objects.create_user(
            username=employee_id,
            password=self.PASSWORD,
            email=f"{employee_id}@test.local",
            first_name="MULTI",
            last_name=employee_id,
            is_active=True,
        )
        profile = EmployeeProfile.objects.create(
            user=user,
            employee_id=employee_id,
            phone_country_code="+254",
            phone_number="700000002",
            status=EmployeeStatus.ACTIVE,
            role=role,
            extra_roles=extra_roles,
        )
        return user, profile

    def _login(self, client, employee_id):
        return client.post(
            reverse("employees:login"),
            {"username": employee_id, "password": self.PASSWORD},
        )

    def test_single_role_login_goes_straight_to_role_home(self):
        self._make_employee("820001", EmployeeRole.SHOP_CASHIER)
        response = self._login(Client(), "820001")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("employees:role_shop_cashier"))

    def test_multi_role_login_shows_role_picker(self):
        self._make_employee(
            "820002",
            EmployeeRole.EMPLOYEE,
            extra_roles="shop_cashier,company_store_manager,shop_manager",
        )
        client = Client()
        response = self._login(client, "820002")
        picker_url = reverse("employees:select_role")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(picker_url))

        picker = client.get(picker_url)
        self.assertEqual(picker.status_code, 200)
        picked = {card["value"] for card in picker.context["role_cards"]}
        self.assertEqual(
            picked,
            {
                EmployeeRole.EMPLOYEE,
                EmployeeRole.SHOP_CASHIER,
                EmployeeRole.SHOP_MANAGER,
                EmployeeRole.COMPANY_STORE_MANAGER,
            },
        )

    def test_picking_a_role_sets_effective_role(self):
        self._make_employee(
            "820003",
            EmployeeRole.EMPLOYEE,
            extra_roles="shop_cashier,shop_manager",
        )
        client = Client()
        self._login(client, "820003")

        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("employees:role_shop_cashier"))
        self.assertEqual(
            client.session[SESSION_ACTIVE_ROLE_KEY], EmployeeRole.SHOP_CASHIER
        )
        self.assertEqual(
            client.session[SESSION_PROFILE_KEY]["role"], EmployeeRole.SHOP_CASHIER
        )

        # The picked role's dashboard loads; another granted role's home
        # redirects back to the picked role.
        self.assertEqual(
            client.get(reverse("employees:role_shop_cashier")).status_code, 200
        )
        denied = client.get(reverse("employees:role_shop_manager"))
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied["Location"], reverse("employees:role_shop_cashier"))

    def test_picker_rejects_ungranted_role(self):
        self._make_employee(
            "820004",
            EmployeeRole.EMPLOYEE,
            extra_roles="shop_cashier",
        )
        client = Client()
        self._login(client, "820004")
        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SUPER_ADMIN},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_ACTIVE_ROLE_KEY, client.session)

    def test_revoked_role_falls_back_to_primary(self):
        user, profile = self._make_employee(
            "820005",
            EmployeeRole.EMPLOYEE,
            extra_roles="shop_cashier",
        )
        client = Client()
        self._login(client, "820005")
        client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER},
        )
        # HR revokes the extra role mid-session.
        EmployeeProfile.objects.filter(pk=profile.pk).update(extra_roles="")

        response = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("employees:role_employee"))
        self.assertNotIn(SESSION_ACTIVE_ROLE_KEY, client.session)

    def test_switch_role_from_workspace_updates_session(self):
        self._make_employee(
            "820006",
            EmployeeRole.COMPANY_STORE_MANAGER,
            extra_roles="shop_manager",
        )
        client = Client()
        self._login(client, "820006")
        client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_MANAGER},
        )
        # The company-store-manager home is now denied and redirects to shop-manager.
        response = client.get(reverse("employees:role_company_store_manager"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("employees:role_shop_manager"))

    def test_picker_redirects_single_role_users(self):
        self._make_employee("820007", EmployeeRole.RIDER)
        client = Client()
        self._login(client, "820007")
        response = client.get(reverse("employees:select_role"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("employees:role_rider"))

    def test_picker_next_url_is_honoured(self):
        self._make_employee(
            "820008",
            EmployeeRole.EMPLOYEE,
            extra_roles="shop_cashier",
        )
        client = Client()
        self._login(client, "820008")
        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER, "next": "/my-shop/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/my-shop/")


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class ActingShopRolePickTests(TestCase):
    """Role picker shop step for multi-role staff with no shop allocation."""

    PASSWORD = "acting-shop-pass"

    def _make_employee(self, employee_id, role, *, extra_roles=""):
        user = User.objects.create_user(
            username=employee_id,
            password=self.PASSWORD,
            email=f"{employee_id}@test.local",
            first_name="ACTING",
            last_name=employee_id,
            is_active=True,
        )
        profile = EmployeeProfile.objects.create(
            user=user,
            employee_id=employee_id,
            phone_country_code="+254",
            phone_number="700000004",
            status=EmployeeStatus.ACTIVE,
            role=role,
            extra_roles=extra_roles,
        )
        return user, profile

    def _make_shop(self, name, login_code):
        from shops.models import Shop

        return Shop.objects.create(
            name=name,
            location="Testville",
            email=f"{login_code}@shop.test",
            phone_number="700000000",
            login_code=login_code,
            password_hash="x",
        )

    def _login(self, client, employee_id):
        return client.post(
            reverse("employees:login"),
            {"username": employee_id, "password": self.PASSWORD},
        )

    def test_shop_scoped_pick_without_allocation_asks_for_shop(self):
        self._make_employee(
            "830001", EmployeeRole.IT_SUPPORT, extra_roles="shop_cashier"
        )
        shop = self._make_shop("CBD", "830101")
        client = Client()
        self._login(client, "830001")

        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER},
        )
        self.assertEqual(response.status_code, 200)
        step = response.context["shop_step"]
        self.assertEqual(step["role"], EmployeeRole.SHOP_CASHIER)
        self.assertEqual([s.pk for s in step["shops"]], [shop.pk])
        self.assertNotIn(SESSION_ACTIVE_ROLE_KEY, client.session)

    def test_acting_shop_unlocks_shop_scoped_dashboard(self):
        self._make_employee(
            "830002", EmployeeRole.IT_SUPPORT, extra_roles="shop_cashier"
        )
        shop = self._make_shop("CBD", "830102")
        client = Client()
        self._login(client, "830002")

        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER, "shop": str(shop.pk)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("employees:role_shop_cashier")
        )
        self.assertEqual(
            client.session[SESSION_ACTING_SHOP_KEY], str(shop.pk)
        )

        home = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(home.status_code, 200)
        labels = {
            link.get("label")
            for link in home.context["page_sidebar"]["primary"]
        }
        # IT support acting as cashier gets a full support pass — every link
        # a real cashier sees stays, including mutating actions.
        self.assertIn("Sell", labels)
        self.assertIn("Open shop", labels)
        self.assertIn("Buy stock", labels)
        self.assertIn("Expenses", labels)
        self.assertIn("Receipts", labels)
        self.assertIn("Analytics", labels)

    def test_acting_shop_scopes_allocation_level_access(self):
        from items.services import actionable_shops_for_profile
        from shops.session import get_shop_for_profile, profile_allocated_to_shop

        _, profile = self._make_employee(
            "830003", EmployeeRole.IT_SUPPORT, extra_roles="shop_cashier"
        )
        shop = self._make_shop("CBD", "830103")
        other = self._make_shop("Other", "830104")
        profile.role = EmployeeRole.SHOP_CASHIER
        profile.acting_shop_id = str(shop.pk)

        self.assertEqual(actionable_shops_for_profile(profile), [shop])
        self.assertEqual(get_shop_for_profile(profile, shop.pk), shop)
        self.assertIsNone(get_shop_for_profile(profile, other.pk))
        self.assertTrue(profile_allocated_to_shop(profile, shop))
        self.assertFalse(profile_allocated_to_shop(profile, other))

    def test_invalid_shop_pick_rejected(self):
        self._make_employee(
            "830006", EmployeeRole.IT_SUPPORT, extra_roles="shop_cashier"
        )
        self._make_shop("CBD", "830106")
        client = Client()
        self._login(client, "830006")

        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER, "shop": "999999"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("shop_step", response.context)
        self.assertNotIn(SESSION_ACTIVE_ROLE_KEY, client.session)
        self.assertNotIn(SESSION_ACTING_SHOP_KEY, client.session)

    def test_non_shop_role_pick_clears_acting_shop(self):
        self._make_employee(
            "830004", EmployeeRole.IT_SUPPORT, extra_roles="shop_cashier"
        )
        shop = self._make_shop("CBD", "830105")
        client = Client()
        self._login(client, "830004")
        client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER, "shop": str(shop.pk)},
        )
        self.assertEqual(
            client.session[SESSION_ACTING_SHOP_KEY], str(shop.pk)
        )

        client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.IT_SUPPORT},
        )
        self.assertNotIn(SESSION_ACTING_SHOP_KEY, client.session)

    def test_allocated_employee_skips_shop_step(self):
        _, profile = self._make_employee(
            "830005", EmployeeRole.SHOP_MANAGER, extra_roles="shop_cashier"
        )
        shop = self._make_shop("CBD", "830107")
        profile.assigned_shops.add(shop)
        client = Client()
        self._login(client, "830005")

        response = client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(SESSION_ACTING_SHOP_KEY, client.session)


class ExtraRolesWriteTests(TestCase):
    """HR edit persists extra_roles and keeps the primary out of it."""

    def _make_employee(self, employee_id, role, *, extra_roles=""):
        user = User.objects.create_user(
            username=employee_id,
            password="x",
            is_active=True,
        )
        return EmployeeProfile.objects.create(
            user=user,
            employee_id=employee_id,
            phone_country_code="+254",
            phone_number="700000003",
            status=EmployeeStatus.ACTIVE,
            role=role,
            extra_roles=extra_roles,
        )

    def test_update_employee_saves_extra_roles(self):
        profile = self._make_employee("820010", EmployeeRole.EMPLOYEE)
        form_data = {
            "first_name": "A",
            "last_name": "B",
            "email": "a@b.co",
            "phone_country_code": "+254",
            "phone_country_iso": "KE",
            "phone_number": "700000010",
            "employee_id": "820010",
            "role": EmployeeRole.EMPLOYEE,
            "extra_roles": [
                EmployeeRole.SHOP_CASHIER,
                EmployeeRole.COMPANY_STORE_MANAGER,
                EmployeeRole.EMPLOYEE,  # duplicate of primary — stripped
            ],
        }
        update_employee(profile, form_data)
        profile.refresh_from_db()
        self.assertEqual(profile.role, EmployeeRole.EMPLOYEE)
        self.assertEqual(
            profile.extra_role_list(),
            [EmployeeRole.COMPANY_STORE_MANAGER, EmployeeRole.SHOP_CASHIER],
        )

    def test_update_employee_access_drops_new_primary_from_extras(self):
        from employees.services import employee_edit_form_data_from_profile

        profile = self._make_employee(
            "820011", EmployeeRole.EMPLOYEE, extra_roles="shop_cashier"
        )
        form_data = employee_edit_form_data_from_profile(profile)
        form_data["role"] = EmployeeRole.SHOP_CASHIER
        form_data["extra_roles"] = [EmployeeRole.SHOP_MANAGER]
        update_employee(profile, form_data)
        profile.refresh_from_db()
        self.assertEqual(profile.role, EmployeeRole.SHOP_CASHIER)
        self.assertEqual(profile.extra_role_list(), [EmployeeRole.SHOP_MANAGER])


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class SupportPassRoleTests(TestCase):
    """IT support acting in a different role gets a full writable pass.

    The picked workspace behaves like they are the ones in session — a
    cashier day opened by someone else still shows its till — and writes
    go through: support is never locked out of a live open day and can
    act on it.
    """

    PASSWORD = "support-pass"

    def _employee(self, employee_id, role, *, extra_roles=""):
        user = User.objects.create_user(
            username=employee_id,
            password=self.PASSWORD,
            email=f"{employee_id}@test.local",
            first_name="VIEW",
            last_name=employee_id,
            is_active=True,
        )
        return EmployeeProfile.objects.create(
            user=user,
            employee_id=employee_id,
            phone_country_code="+254",
            phone_number="700000006",
            status=EmployeeStatus.ACTIVE,
            role=role,
            extra_roles=extra_roles,
        )

    def setUp(self):
        from django.contrib.auth.hashers import make_password

        from shops.models import Shop
        from shops.services import open_shop_day

        self.cashier = self._employee("840001", EmployeeRole.SHOP_CASHIER)
        self.support = self._employee(
            "840002", EmployeeRole.IT_SUPPORT, extra_roles="shop_cashier"
        )
        self.shop = Shop.objects.create(
            name="SUPPORT PASS SHOP",
            location="NAIROBI",
            email="support-pass-shop@test.local",
            phone_number="0700008401",
            login_code="840101",
            password_hash=make_password(self.PASSWORD),
            created_by=self.cashier,
        )
        # A day already open under the real cashier — the support pass must
        # see it as "their" session, not the colleague-day lockout.
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": self.cashier.employee_id,
            },
        )
        self.client = Client()
        self.client.post(
            reverse("employees:login"),
            {"username": self.support.employee_id, "password": self.PASSWORD},
        )
        self.client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_CASHIER, "shop": str(self.shop.pk)},
        )

    def test_dashboard_shows_foreign_day_till_with_actions(self):
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        # The colleague-day lockout page is skipped and the till is visible.
        self.assertTemplateNotUsed(
            response, "employees/cashier_session_blocked.html"
        )
        self.assertContains(response, "Day summary")
        self.assertContains(response, "In till now")
        # …and the employee actually in session is named on the row.
        self.assertContains(response, "VIEW 840001")
        # …and the pass is writable — the open day stays actionable.
        self.assertNotContains(response, "View only")
        self.assertContains(response, "Close shop")
        self.assertContains(response, "Record drawing")

    def test_day_picker_shows_till_with_actions(self):
        response = self.client.get(
            reverse(
                "employees:role_shop_day",
                kwargs={"role_segment": "shop-cashier"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "In till now")
        self.assertNotContains(response, "View only")
        # The employee in session is named — the badge shows who owns the day.
        self.assertContains(response, "VIEW 840001")
        self.assertContains(response, "Close shop")

    def test_mutation_post_allowed_json(self):
        response = self.client.post(
            reverse("employees:role_shop_cashier_settings"),
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_sell",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_checkout_post_reaches_endpoint(self):
        response = self.client.post(
            reverse(
                "employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}
            ),
            {"kind": "sale", "login_code": self.cashier.employee_id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        # The write reaches checkout — it may still fail validation (empty
        # cart) but it is not blocked by the support pass.
        self.assertNotEqual(response.status_code, 403)
        self.assertNotIn("view_only", response.json())

    def test_catalog_read_allowed_on_foreign_day(self):
        response = self.client.get(
            reverse(
                "employees:my_shop_catalog", kwargs={"shop_id": self.shop.pk}
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_floor_unlock_allowed_and_writes_reach_checkout(self):
        # Floor unlock is session navigation — it stays allowed.
        response = self.client.post(
            reverse("employees:my_shop_select"),
            {"shop_id": str(self.shop.pk), "password": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.get(
            reverse(
                "employees:my_shop_workspace",
                kwargs={"shop_id": self.shop.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse(
                "employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}
            ),
            {"kind": "sale", "login_code": self.cashier.employee_id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        # Not the old view-only 403 — the real checkout endpoint answers.
        self.assertNotIn("view_only", response.json())

    def test_acting_shop_switch_allowed(self):
        response = self.client.post(
            reverse("employees:my_shop_switch"),
            {"shop_id": ""},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_role_picker_post_still_allowed(self):
        # While acting as cashier the support pass is set.
        from django.test import RequestFactory

        from employees.access import get_profile_for_request

        request = RequestFactory().get("/shop-cashier/")
        request.session = self.client.session
        request.user = self.support.user
        profile = get_profile_for_request(request)
        self.assertTrue(getattr(profile, "support_pass", False))

        response = self.client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.IT_SUPPORT},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.session[SESSION_ACTIVE_ROLE_KEY],
            EmployeeRole.IT_SUPPORT,
        )
        # Back on the primary role the pass is off again.
        request = RequestFactory().get("/it-support/")
        request.session = self.client.session
        request.user = self.support.user
        profile = get_profile_for_request(request)
        self.assertFalse(getattr(profile, "support_pass", False))

    def test_day_history_stays_shop_wide(self):
        response = self.client.get(
            reverse(
                "employees:my_shop_day_history",
                kwargs={"shop_id": self.shop.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        sessions = response.context["day_sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["opened_by"], self.cashier)
