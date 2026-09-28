from django.test import TestCase, override_settings

from shops.deploy_profile import hosted_daraja_credentials_locked
from shops.models import CompanyDarajaSettings, DarajaDeployProfile
from shops.services import (
    _invalidate_daraja_settings_cache,
    get_daraja_settings,
    get_daraja_settings_db,
    update_daraja_settings,
)


class DeployProfileDarajaTests(TestCase):
    def setUp(self):
        CompanyDarajaSettings.objects.update_or_create(
            deploy_profile=DarajaDeployProfile.LOCAL,
            defaults={"consumer_key": "local-key", "shortcode": "174379"},
        )
        CompanyDarajaSettings.objects.update_or_create(
            deploy_profile=DarajaDeployProfile.HOSTED,
            defaults={"consumer_key": "hosted-db-key", "shortcode": "600000"},
        )
        _invalidate_daraja_settings_cache()

    def test_local_uses_local_row(self):
        row = get_daraja_settings()
        self.assertEqual(row.deploy_profile, DarajaDeployProfile.LOCAL)
        self.assertEqual(row.consumer_key, "local-key")

    @override_settings(IS_HOSTED=True)
    def test_hosted_uses_hosted_row_without_env(self):
        _invalidate_daraja_settings_cache()
        row = get_daraja_settings()
        self.assertEqual(row.deploy_profile, DarajaDeployProfile.HOSTED)
        self.assertEqual(row.consumer_key, "hosted-db-key")

    @override_settings(IS_HOSTED=True)
    def test_hosted_env_overlays_credentials(self):
        env = {
            "DARAJA_CONSUMER_KEY": "env-key",
            "DARAJA_CONSUMER_SECRET": "env-secret",
            "DARAJA_PASSKEY": "env-pass",
            "DARAJA_SHORTCODE": "522522",
        }
        import os

        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            _invalidate_daraja_settings_cache()
            self.assertTrue(hosted_daraja_credentials_locked())
            row = get_daraja_settings()
            self.assertEqual(row.consumer_key, "env-key")
            self.assertEqual(row.shortcode, "522522")
            self.assertTrue(row.credentials_valid)
            db = get_daraja_settings_db()
            self.assertEqual(db.consumer_key, "hosted-db-key")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _invalidate_daraja_settings_cache()

    @override_settings(IS_HOSTED=True)
    def test_hosted_env_blocks_credential_save(self):
        import os

        env = {
            "DARAJA_CONSUMER_KEY": "env-key",
            "DARAJA_CONSUMER_SECRET": "env-secret",
            "DARAJA_PASSKEY": "env-pass",
            "DARAJA_SHORTCODE": "522522",
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            _invalidate_daraja_settings_cache()
            with self.assertRaises(Exception):
                update_daraja_settings(
                    environment="production",
                    shortcode="522522",
                    consumer_key="other",
                    consumer_secret="other",
                    passkey="other",
                )
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _invalidate_daraja_settings_cache()
