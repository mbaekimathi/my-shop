"""Role-scoped settings URLs: /<role>/settings/ mirrors the session role."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class RoleScopedSettingsUrlTests(TestCase):
    def setUp(self):
        self.password = "settings-url-pass"
        self.user = User.objects.create_user(
            username="800088",
            password=self.password,
            email="settings-url@test.local",
            first_name="SETTINGS",
            last_name="URL",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800088",
            phone_country_code="+254",
            phone_number="700000888",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )

    def _login_employee(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:login"),
            {"username": self.profile.employee_id, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_role_scoped_settings_home_renders(self):
        client = self._login_employee()
        response = client.get("/it-support/settings/")
        self.assertEqual(response.status_code, 200)

    def test_role_scoped_section_renders(self):
        client = self._login_employee()
        response = client.get("/it-support/settings/company-profile/")
        self.assertEqual(response.status_code, 200)

    def test_company_pos_renders_grouped_list_view(self):
        client = self._login_employee()
        response = client.get("/it-support/settings/company-pos/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-pos-filter')
        self.assertContains(response, "pos-settings-groups--list")
        self.assertContains(response, 'id="pos-group-tax"')
        self.assertContains(response, 'id="pos-group-session-timeout"')
        self.assertContains(response, 'id="pos-group-shop-floor-sidebar"')
        self.assertContains(response, 'data-field="enable_employee_session_timeout"')
        self.assertContains(response, 'data-field="enable_shop_session_timeout"')
        self.assertContains(response, 'data-employee-timeout-input')
        self.assertContains(response, 'data-shop-timeout-input')
        self.assertContains(response, 'data-pos-enabled-count')
        self.assertContains(response, 'data-pos-group')

    def test_company_pos_renders_sidebar_tabs_and_linked_settings(self):
        client = self._login_employee()
        response = client.get("/it-support/settings/company-pos/")
        self.assertEqual(response.status_code, 200)
        # The POS settings sidebar link becomes an open group whose children
        # are the page tabs.
        self.assertContains(response, 'data-nav-group')
        for slug in ("all", "checkout", "stock", "staff", "floor"):
            self.assertContains(
                response,
                f'href="/it-support/settings/company-pos/#tab-{slug}"',
            )
            self.assertContains(response, f'data-nav-key="pos-tab-{slug}"')
        self.assertContains(response, 'data-pos-tab-section="checkout"')
        self.assertContains(response, 'data-pos-tab-section="stock"')
        self.assertContains(response, 'data-pos-tab-section="staff"')
        self.assertContains(response, 'data-pos-tab-section="floor"')
        self.assertContains(response, 'id="pos-group-stk-push"')
        self.assertContains(response, 'data-pos-requires="enable_trade_out|enable_trade_in"')
        self.assertContains(response, 'data-pos-requires="stock_mode_shelf|stock_mode_both"')

    def test_company_receipt_renders_sidebar_tabs_and_linked_settings(self):
        client = self._login_employee()
        response = client.get("/it-support/settings/company-receipt/")
        self.assertEqual(response.status_code, 200)
        # The Receipt settings sidebar link becomes an open group whose
        # children are the page tabs.
        self.assertContains(response, 'data-nav-group')
        for slug in ("all", "printing", "content", "confirm"):
            self.assertContains(
                response,
                f'href="/it-support/settings/company-receipt/#tab-{slug}"',
            )
            self.assertContains(response, f'data-nav-key="pos-tab-{slug}"')
        self.assertContains(response, 'data-pos-tab-section="printing"')
        self.assertContains(response, 'data-pos-tab-section="content"')
        self.assertContains(response, 'data-pos-tab-section="confirm"')
        self.assertContains(response, 'data-pos-filter')
        # Channel-linked settings only appear while a print channel is on.
        channels = "enable_print_bluetooth|enable_print_usb|enable_print_wifi"
        self.assertContains(response, f'data-pos-requires="{channels}"')
        self.assertContains(response, 'id="pos-group-paper-size"')
        self.assertContains(response, 'id="pos-group-copies-preview"')
        self.assertContains(
            response, 'data-pos-requires-all="enable_receipt_payment_methods"'
        )

    def test_wrong_role_segment_redirects_to_session_role(self):
        client = self._login_employee()
        response = client.get("/employee/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/it-support/settings/")

    def test_wrong_role_segment_redirects_section(self):
        client = self._login_employee()
        response = client.get("/employee/settings/sms/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/it-support/settings/sms/")

    def test_flat_settings_url_redirects_to_role_url(self):
        client = self._login_employee()
        response = client.get("/employees/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/it-support/settings/")

    def test_flat_section_url_redirects_to_role_url(self):
        client = self._login_employee()
        response = client.get("/employees/settings/sms/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/it-support/settings/sms/")

    def test_other_valid_role_segment_redirects(self):
        client = self._login_employee()
        response = client.get("/super-admin/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/it-support/settings/")

    def test_anonymous_role_settings_redirects_to_login(self):
        response = Client().get("/it-support/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/employees/login/", response.headers["Location"])

    def test_dashboard_sidebar_settings_link_is_role_scoped(self):
        client = self._login_employee()
        response = client.get(reverse("employees:role_it_support"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/it-support/settings/"')

    def test_settings_page_lists_role_scoped_section_links(self):
        client = self._login_employee()
        response = client.get("/it-support/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/it-support/settings/")
        self.assertNotContains(response, "/employees/settings/")
