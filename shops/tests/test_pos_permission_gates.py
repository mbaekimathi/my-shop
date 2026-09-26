from django.test import TestCase

from employees.module_permissions import employee_may
from employees.models import EmployeeProfile, EmployeeRole
from employees.permissions_catalog import permission_modules_for_display
from shops.models import CompanyPosSettings
from shops.services import (
    _invalidate_pos_settings_cache,
    company_pos_analytics_section_visible,
    company_pos_permission_visible,
)


class PosPermissionGateTests(TestCase):
    def setUp(self):
        CompanyPosSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enable_sale": True,
                "enable_credit": True,
                "enable_quotation": True,
                "enable_trade_out": True,
            },
        )
        _invalidate_pos_settings_cache()

    def _set_pos(self, **fields):
        pos = CompanyPosSettings.objects.get(pk=1)
        for key, value in fields.items():
            setattr(pos, key, value)
        pos.save()
        _invalidate_pos_settings_cache()

    def test_credit_off_hides_my_shop_and_analytics_permissions(self):
        self._set_pos(enable_credit=False)
        self.assertFalse(company_pos_permission_visible("my-shop", "credit"))
        self.assertFalse(company_pos_permission_visible("analytics", "credits"))
        self.assertTrue(company_pos_permission_visible("my-shop", "sale"))

    def test_analytics_tradings_follows_trade_out_toggle(self):
        self._set_pos(enable_trade_out=False)
        self.assertFalse(company_pos_analytics_section_visible("tradings"))
        self.assertTrue(company_pos_analytics_section_visible("revenue"))

    def test_permission_matrix_omits_disabled_submodules(self):
        self._set_pos(enable_quotation=False)
        my_shop = next(m for m in permission_modules_for_display() if m["slug"] == "my-shop")
        slugs = {row["slug"] for row in my_shop["submodules"]}
        self.assertNotIn("quotation", slugs)
        self.assertIn("sale", slugs)

    def test_employee_may_denies_when_company_pos_off(self):
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="cashier1", password="test-pass-123")
        profile = EmployeeProfile.objects.create(
            user=user,
            employee_id="100001",
            role=EmployeeRole.SHOP_CASHIER,
        )
        self._set_pos(enable_credit=False)
        self.assertFalse(employee_may(profile, "my-shop", "credit"))
