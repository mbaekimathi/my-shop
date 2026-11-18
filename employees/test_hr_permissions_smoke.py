from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import (
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)


class HrPermissionsPagesSmokeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="870099", password="pass", is_active=True
        )
        self.admin_profile = EmployeeProfile.objects.create(
            user=self.admin,
            employee_id="870099",
            role=EmployeeRole.IT_SUPPORT,
            status=EmployeeStatus.ACTIVE,
        )
        self.staff = User.objects.create_user(
            username="870011", password="pass", is_active=True,
            first_name="Jane", last_name="Doe",
        )
        self.staff_profile = EmployeeProfile.objects.create(
            user=self.staff,
            employee_id="870011",
            role=EmployeeRole.SHOP_CASHIER,
            status=EmployeeStatus.ACTIVE,
        )
        self.client.force_login(self.admin)

    def test_permissions_list_page_renders_employee_cards(self):
        response = self.client.get("/it-support/hr-management/permissions/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "870011")
        self.assertContains(
            response, "/it-support/hr-management/permissions/870011/"
        )

    def test_employee_permissions_page_renders_module_rows(self):
        response = self.client.get(
            "/it-support/hr-management/permissions/870011/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-permission-toggle")
        self.assertContains(response, "Jane Doe")
        self.assertContains(response, 'id="module-hr-management"')

    def test_toggle_permission_post_on_detail_page(self):
        response = self.client.post(
            "/it-support/hr-management/permissions/870011/",
            {
                "action": "toggle_permission",
                "employee_id": "870011",
                "module_slug": "my-shop",
                "submodule_slug": "sale",
                "allowed": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        perm = EmployeeModulePermission.objects.get(
            employee=self.staff_profile,
            module_slug="my-shop",
            submodule_slug="sale",
        )
        self.assertFalse(perm.allowed)

        # Denied count shows on the list page.
        response = self.client.get("/it-support/hr-management/permissions/")
        self.assertContains(response, "1 denied")

    def test_search_filters_list(self):
        response = self.client.get(
            "/it-support/hr-management/permissions/?q=jane"
        )
        self.assertContains(response, "870011")
        response = self.client.get(
            "/it-support/hr-management/permissions/?q=nonexistentzzz"
        )
        self.assertContains(response, "No employees match")

    def test_unknown_employee_404s(self):
        response = self.client.get(
            "/it-support/hr-management/permissions/999999/"
        )
        self.assertEqual(response.status_code, 404)
