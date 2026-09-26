from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from shops.models import CompanyDarajaSettings, DarajaEnvironment, StkProvider
from shops.services import (
    _invalidate_daraja_settings_cache,
    get_daraja_settings,
    set_daraja_environment,
    set_daraja_stk_enabled,
    set_stk_provider,
    update_nexus_stk_settings,
)


class NexusStkSettingsTests(TestCase):
    def setUp(self):
        CompanyDarajaSettings.objects.update_or_create(
            pk=1,
            defaults={
                "stk_provider": StkProvider.DARAJA,
                "enable_stk_push": False,
                "credentials_valid": False,
                "nexus_key_verified": False,
                "nexus_api_key": "",
            },
        )
        _invalidate_daraja_settings_cache()

    @patch("shops.nexus_stk.verify_nexus_collection_api_key")
    def test_update_nexus_settings_verifies_and_enables_provider(self, verify_mock):
        verify_mock.return_value = {"ok": True, "collection_id": "C9DCFF5A9A3"}
        daraja = get_daraja_settings()
        daraja.environment = DarajaEnvironment.PRODUCTION
        daraja.save(update_fields=["environment"])
        _invalidate_daraja_settings_cache()
        row = update_nexus_stk_settings(
            nexus_api_key="cm_test_key_123",
            nexus_collection_id="",
        )
        self.assertEqual(row.stk_provider, StkProvider.NEXUS)
        self.assertTrue(row.nexus_key_verified)
        self.assertEqual(row.nexus_collection_id, "C9DCFF5A9A3")
        self.assertTrue(row.is_ready_for_stk())

    def test_enable_stk_without_nexus_verification_fails(self):
        row = get_daraja_settings()
        row.stk_provider = StkProvider.NEXUS
        row.nexus_api_key = "cm_test_key_123"
        row.nexus_key_verified = False
        row.enable_stk_push = False
        row.save()
        _invalidate_daraja_settings_cache()
        with self.assertRaises(ValidationError):
            set_daraja_stk_enabled(enabled=True)

    def test_nexus_not_allowed_in_sandbox(self):
        row = get_daraja_settings()
        row.environment = DarajaEnvironment.SANDBOX
        row.save(update_fields=["environment"])
        _invalidate_daraja_settings_cache()
        with self.assertRaises(ValidationError):
            set_stk_provider(provider=StkProvider.NEXUS)
        with self.assertRaises(ValidationError):
            update_nexus_stk_settings(nexus_api_key="cm_test_key_12345")

    def test_switching_to_sandbox_resets_nexus_provider(self):
        row = get_daraja_settings()
        row.environment = DarajaEnvironment.PRODUCTION
        row.stk_provider = StkProvider.NEXUS
        row.save()
        _invalidate_daraja_settings_cache()
        row = set_daraja_environment(environment=DarajaEnvironment.SANDBOX)
        self.assertEqual(row.stk_provider, StkProvider.DARAJA)
        self.assertFalse(row.uses_nexus_stk())

    def test_company_daraja_page_shows_provider_picker(self):
        from employees.models import EmployeeProfile, EmployeeRole

        profile = EmployeeProfile.objects.filter(role=EmployeeRole.IT_SUPPORT).first()
        if profile is None:
            self.skipTest("No IT support profile for UI test")
        self.client.force_login(profile.user)
        response = self.client.get("/employees/settings/company-daraja/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Setup")
        self.assertContains(response, "Enable STK Push")
        self.assertContains(response, "Nexus collections")
        self.assertContains(response, "data-nexus-form")
