"""Multi-role employees: extra_roles grants and the session role picker."""

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.access import SESSION_ACTIVE_ROLE_KEY, SESSION_PROFILE_KEY
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
                ["shop_cashier", "store_manager", "employee"],
                exclude="employee",
            ),
            "store_manager,shop_cashier",
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
            extra_roles="shop_cashier,store_manager,shop_manager",
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
                EmployeeRole.STORE_MANAGER,
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
            EmployeeRole.STORE_MANAGER,
            extra_roles="shop_manager",
        )
        client = Client()
        self._login(client, "820006")
        client.post(
            reverse("employees:select_role"),
            {"role": EmployeeRole.SHOP_MANAGER},
        )
        # The store-manager home is now denied and redirects to shop-manager.
        response = client.get(reverse("employees:role_store_manager"))
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
                EmployeeRole.STORE_MANAGER,
                EmployeeRole.EMPLOYEE,  # duplicate of primary — stripped
            ],
        }
        update_employee(profile, form_data)
        profile.refresh_from_db()
        self.assertEqual(profile.role, EmployeeRole.EMPLOYEE)
        self.assertEqual(
            profile.extra_role_list(),
            [EmployeeRole.STORE_MANAGER, EmployeeRole.SHOP_CASHIER],
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
