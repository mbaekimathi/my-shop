from django.core.exceptions import ValidationError
from django.test import TestCase

from shops.models import (
    CompanyDarajaSettings,
    CompanyPosSettings,
    DarajaEnvironment,
    StkProvider,
)
from shops.services import (
    _invalidate_daraja_settings_cache,
    _invalidate_pos_settings_cache,
    set_shop_stk_push_enabled,
    stk_shop_activation_status,
)


class PosStkToggleTests(TestCase):
    def setUp(self):
        CompanyPosSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enable_mpesa": True,
                "enable_cash_mpesa": False,
            },
        )
        CompanyDarajaSettings.objects.update_or_create(
            pk=1,
            defaults={
                "environment": DarajaEnvironment.PRODUCTION,
                "stk_provider": StkProvider.NEXUS,
                "nexus_api_key": "cm_test_key_12345",
                "nexus_key_verified": True,
                "enable_stk_push": False,
            },
        )
        _invalidate_pos_settings_cache()
        _invalidate_daraja_settings_cache()

    def test_activation_status_when_ready(self):
        status = stk_shop_activation_status()
        self.assertTrue(status["mpesa_checkout_enabled"])
        self.assertTrue(status["stk_credentials_ready"])
        self.assertTrue(status["stk_can_enable_on_pos"])
        self.assertFalse(status["stk_ready_for_shop"])

    def test_enable_stk_for_shop(self):
        row = set_shop_stk_push_enabled(enabled=True)
        self.assertTrue(row.enable_stk_push)
        status = stk_shop_activation_status()
        self.assertTrue(status["stk_ready_for_shop"])

    def test_cannot_enable_without_mpesa(self):
        pos = CompanyPosSettings.objects.get(pk=1)
        pos.enable_mpesa = False
        pos.enable_cash_mpesa = False
        pos.save()
        _invalidate_pos_settings_cache()
        with self.assertRaises(ValidationError):
            set_shop_stk_push_enabled(enabled=True)
