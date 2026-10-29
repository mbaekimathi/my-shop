"""Session meta mirrors the DB profile — role/status changes apply at once."""

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.access import SESSION_PROFILE_KEY
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
class SessionMetaSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="810011", password="meta-sync-pass", email="ms@test.local",
            first_name="META", last_name="SYNC", is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user, employee_id="810011", phone_country_code="+254",
            phone_number="700000001", status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.EMPLOYEE,
        )

    def _login(self):
        c = Client()
        r = c.post(reverse("employees:login"), {"username": "810011", "password": "meta-sync-pass"})
        self.assertEqual(r.status_code, 302)
        return c

    def test_role_change_in_db_updates_session_meta(self):
        c = self._login()
        self.assertEqual(c.session.get(SESSION_PROFILE_KEY)["role"], "employee")

        # promote to super_admin in DB mid-session
        EmployeeProfile.objects.filter(pk=self.profile.pk).update(role=EmployeeRole.SUPER_ADMIN)

        # legacy settings URL redirects to the promoted role's segment
        r = c.get("/employees/settings/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/super-admin/settings/")
        # session mirror refreshed
        self.assertEqual(c.session.get(SESSION_PROFILE_KEY)["role"], "super_admin")
        # role-gated view now reachable (would previously redirect to /employee/)
        r = c.get(reverse("employees:role_super_admin"))
        self.assertEqual(r.status_code, 200)

    def test_demoted_role_applies_immediately(self):
        self.profile.role = EmployeeRole.SUPER_ADMIN
        self.profile.save()
        c = self._login()
        self.assertEqual(c.session.get(SESSION_PROFILE_KEY)["role"], "super_admin")

        EmployeeProfile.objects.filter(pk=self.profile.pk).update(role=EmployeeRole.EMPLOYEE)
        r = c.get(reverse("employees:role_super_admin"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/employee/", r.url)

    def test_suspension_applies_immediately(self):
        c = self._login()
        EmployeeProfile.objects.filter(pk=self.profile.pk).update(status=EmployeeStatus.SUSPENDED)
        r = c.get("/employees/settings/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.url.startswith(reverse("employees:login")))

    def test_settings_shows_real_role(self):
        c = self._login()
        r = c.get("/employee/settings/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["role_label"], "Employee")
