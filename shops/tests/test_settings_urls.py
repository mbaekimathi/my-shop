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
