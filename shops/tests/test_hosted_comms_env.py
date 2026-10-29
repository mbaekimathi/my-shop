import os

from django.test import TestCase, override_settings

from shops.deploy_profile import (
    HOSTED_MESSAGE_ENV,
    HOSTED_SMS_ENV,
    HOSTED_TWILIO_ENV,
)
from shops.models import CompanyCommunicationsSettings, SmsProvider
from shops.services import (
    _invalidate_communications_settings_cache,
    communications_settings_as_dict,
    get_communications_settings,
    get_communications_settings_db,
    update_sms_settings,
    update_twilio_settings,
)

COMMS_ENV_NAMES = (
    list(HOSTED_TWILIO_ENV.values())
    + list(HOSTED_SMS_ENV.values())
    + list(HOSTED_MESSAGE_ENV.values())
)


class HostedCommsEnvTests(TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in COMMS_ENV_NAMES}
        for k in COMMS_ENV_NAMES:
            os.environ.pop(k, None)
        CompanyCommunicationsSettings.objects.update_or_create(
            pk=1,
            defaults={
                "twilio_account_sid": "ACdb" + "b" * 32,
                "twilio_auth_token": "db-token",
                "twilio_whatsapp_from": "whatsapp:+14155238886",
                "sms_api_key": "db-sms-key",
                "sms_sender_id": "DBSENDER",
                "message_from_name": "DB Support",
            },
        )
        _invalidate_communications_settings_cache()

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _invalidate_communications_settings_cache()

    @override_settings(IS_HOSTED=True)
    def test_hosted_without_env_clears_twilio_but_keeps_db_sms(self):
        row = get_communications_settings()
        self.assertEqual(row.twilio_account_sid, "")
        self.assertFalse(row.has_twilio_credentials())
        self.assertEqual(row.sms_sender_id, "DBSENDER")
        self.assertTrue(row.has_sms_credentials())
        db = get_communications_settings_db()
        self.assertTrue(db.has_twilio_credentials())

    @override_settings(IS_HOSTED=True)
    def test_hosted_env_overlays_twilio_and_sms(self):
        env = {
            "TWILIO_ACCOUNT_SID": "AC" + "c" * 32,
            "TWILIO_AUTH_TOKEN": "env-token",
            "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
            "SMS_API_KEY": "env-sms",
            "SMS_SENDER_ID": "RICHCOM",
            "MESSAGE_FROM_NAME": "RICHCOM Support",
            "MESSAGE_REPLY_TO": "support@richcom.co.ke",
        }
        os.environ.update(env)
        _invalidate_communications_settings_cache()
        row = get_communications_settings()
        self.assertEqual(row.twilio_auth_token, "env-token")
        self.assertEqual(row.sms_sender_id, "RICHCOM")
        self.assertEqual(row.message_reply_to, "support@richcom.co.ke")
        self.assertTrue(row.has_twilio_credentials())
        self.assertTrue(row.has_sms_credentials())

    @override_settings(IS_HOSTED=True)
    def test_hosted_blocks_twilio_save(self):
        with self.assertRaises(Exception):
            update_twilio_settings(
                account_sid="AC" + "d" * 32,
                auth_token="nope",
                whatsapp_from="whatsapp:+14155238886",
            )

    @override_settings(IS_HOSTED=True)
    def test_hosted_allows_sms_save_without_env(self):
        row = update_sms_settings(
            provider=SmsProvider.BLESSED_TEXTS,
            api_key="new-key",
            sender_id="23107",
        )
        self.assertEqual(row.sms_provider, SmsProvider.BLESSED_TEXTS)
        self.assertEqual(row.sms_sender_id, "23107")
        db = get_communications_settings_db()
        self.assertEqual(db.sms_api_key, "new-key")
        self.assertFalse(communications_settings_as_dict()["sms_credentials_from_env"])

    @override_settings(IS_HOSTED=True)
    def test_hosted_blocks_sms_save_when_env_set(self):
        os.environ["SMS_API_KEY"] = "env-sms"
        _invalidate_communications_settings_cache()
        with self.assertRaises(Exception):
            update_sms_settings(
                provider=SmsProvider.AFRICAS_TALKING,
                api_key="key",
                sender_id="SENDER",
            )
        self.assertTrue(communications_settings_as_dict()["sms_credentials_from_env"])
