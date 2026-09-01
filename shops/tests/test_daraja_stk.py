from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from shops.daraja_stk import (
    _callback_url,
    _resolve_stk_config,
    callback_secret_matches,
    detect_ngrok_public_base_url,
    ensure_callback_secret,
    handle_stk_callback,
    persist_public_callback_base,
    query_stk_push_status,
    refresh_stk_payment_if_pending,
    require_successful_stk,
    resolve_callback_base_url,
    sync_callback_base_from_request,
)
from shops.models import (
    CompanyDarajaSettings,
    CompanyPosSettings,
    MpesaStkPayment,
    MpesaStkPurpose,
    MpesaStkStatus,
)
from shops.services import (
    _invalidate_daraja_settings_cache,
    _invalidate_pos_settings_cache,
    get_company_pos_settings,
    get_daraja_settings,
)


class DarajaStkConfigTests(TestCase):
    def setUp(self):
        CompanyDarajaSettings.objects.get_or_create(pk=1)
        CompanyPosSettings.objects.get_or_create(pk=1)

    def test_resolve_paybill_uses_business_number(self):
        row = get_daraja_settings()
        row.shortcode = "600000"
        pos = get_company_pos_settings()
        pos.mpesa_collection_type = "paybill"
        pos.mpesa_business_number = "522522"
        config = _resolve_stk_config(row, pos)
        self.assertEqual(config["transaction_type"], "CustomerPayBillOnline")
        self.assertEqual(config["business_shortcode"], "522522")
        self.assertEqual(config["party_b"], "522522")

    def test_resolve_buy_goods_uses_till_for_party_b(self):
        row = get_daraja_settings()
        row.shortcode = "174379"
        pos = get_company_pos_settings()
        pos.mpesa_collection_type = "buy_goods"
        pos.mpesa_till_number = "123456"
        config = _resolve_stk_config(row, pos)
        self.assertEqual(config["transaction_type"], "CustomerBuyGoodsOnline")
        self.assertEqual(config["business_shortcode"], "174379")
        self.assertEqual(config["party_b"], "123456")


class DarajaCallbackAutoDetectTests(TestCase):
    def setUp(self):
        CompanyDarajaSettings.objects.update_or_create(
            pk=1,
            defaults={"callback_base_url": "https://old-domain.example.com"},
        )
        _invalidate_daraja_settings_cache()
        self.factory = RequestFactory()

    def test_request_domain_overrides_stale_saved_callback(self):
        request = self.factory.get(
            "/employees/settings/company-daraja/",
            HTTP_HOST="shop.example.com",
            HTTP_X_FORWARDED_PROTO="https",
        )
        resolved = sync_callback_base_from_request(request, persist=True)
        self.assertEqual(resolved, "https://shop.example.com")
        row = get_daraja_settings()
        self.assertEqual(row.callback_base_url, "https://shop.example.com")

    def test_resolve_callback_uses_request_domain_first(self):
        request = self.factory.get(
            "/my-shop/1/",
            HTTP_HOST="live.myshop.co.ke",
            HTTP_X_FORWARDED_PROTO="https",
        )
        base = resolve_callback_base_url(request=request, persist=False)
        self.assertEqual(base, "https://live.myshop.co.ke")

    def test_persist_public_callback_base_updates_db(self):
        saved = persist_public_callback_base("https://new-domain.example.com")
        self.assertEqual(saved, "https://new-domain.example.com")
        row = get_daraja_settings()
        self.assertEqual(row.callback_base_url, "https://new-domain.example.com")


class DarajaStkQueryTests(TestCase):
    def setUp(self):
        CompanyDarajaSettings.objects.update_or_create(
            pk=1,
            defaults={
                "shortcode": "174379",
                "passkey": "test-passkey",
                "consumer_key": "key",
                "consumer_secret": "secret",
                "environment": "sandbox",
                "credentials_valid": True,
            },
        )
        CompanyPosSettings.objects.update_or_create(
            pk=1,
            defaults={
                "mpesa_collection_type": "paybill",
                "mpesa_business_number": "174379",
            },
        )
        _invalidate_daraja_settings_cache()
        _invalidate_pos_settings_cache()

    def test_query_marks_payment_success_when_safaricom_confirms(self):
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("150.00"),
            phone="254711000011",
            checkout_request_id="ws_CO_QUERY_1",
            merchant_request_id="mr_QUERY_1",
            created_at=timezone.now() - timezone.timedelta(seconds=10),
        )
        query_payload = {
            "ResponseCode": "0",
            "ResponseDescription": "Accept",
            "MerchantRequestID": "mr_QUERY_1",
            "CheckoutRequestID": "ws_CO_QUERY_1",
            "ResultCode": 0,
            "ResultDesc": "The service request is processed successfully.",
        }
        with patch(
            "shops.daraja_stk.get_daraja_access_token",
            return_value="token",
        ), patch(
            "shops.daraja_stk._daraja_json_request",
            return_value=query_payload,
        ):
            updated = query_stk_push_status(payment)

        self.assertEqual(updated.status, MpesaStkStatus.SUCCESS)
        self.assertEqual(updated.result_code, "0")

    def test_refresh_skips_recent_pending_payment(self):
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("50.00"),
            phone="254711000011",
            checkout_request_id="ws_CO_NEW",
        )
        with patch("shops.daraja_stk.query_stk_push_status") as mock_query:
            result = refresh_stk_payment_if_pending(payment)
        mock_query.assert_not_called()
        self.assertEqual(result.status, MpesaStkStatus.PENDING)

    def test_refresh_throttles_safaricom_query(self):
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("50.00"),
            phone="254711000011",
            checkout_request_id="ws_CO_THROTTLE",
            created_at=timezone.now() - timezone.timedelta(seconds=10),
            last_status_query_at=timezone.now(),
        )
        with patch("shops.daraja_stk.query_stk_push_status") as mock_query:
            result = refresh_stk_payment_if_pending(payment)
        mock_query.assert_not_called()
        self.assertEqual(result.status, MpesaStkStatus.PENDING)

    def test_callback_still_updates_receipt_number(self):
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("80.00"),
            phone="254711000011",
            checkout_request_id="ws_CO_CB_1",
            merchant_request_id="mr_CB_1",
        )
        handle_stk_callback(
            {
                "Body": {
                    "stkCallback": {
                        "MerchantRequestID": "mr_CB_1",
                        "CheckoutRequestID": "ws_CO_CB_1",
                        "ResultCode": 0,
                        "ResultDesc": "OK",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "MpesaReceiptNumber", "Value": "ABC123"},
                            ]
                        },
                    }
                }
            }
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaStkStatus.SUCCESS)
        self.assertEqual(payment.mpesa_receipt_number, "ABC123")


class DarajaStkHardeningTests(TestCase):
    def setUp(self):
        CompanyDarajaSettings.objects.update_or_create(
            pk=1,
            defaults={
                "callback_secret": "test-callback-secret",
                "callback_base_url": "https://shop.example.com",
                "credentials_valid": True,
                "enable_stk_push": True,
                "shortcode": "174379",
                "consumer_key": "key",
                "consumer_secret": "secret",
                "passkey": "pass",
            },
        )
        _invalidate_daraja_settings_cache()

    def test_callback_url_includes_secret(self):
        with patch(
            "shops.daraja_stk.resolve_callback_base_url",
            return_value="https://shop.example.com",
        ):
            url = _callback_url()
        self.assertIn("/mpesa/daraja/callback/test-callback-secret/", url)

    def test_callback_secret_matches(self):
        self.assertTrue(callback_secret_matches("test-callback-secret"))
        self.assertFalse(callback_secret_matches("wrong-secret"))

    def test_require_successful_stk_rejects_replay(self):
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.SUCCESS,
            amount=Decimal("100.00"),
            phone="254711000011",
            applied=True,
            completed_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as ctx:
            require_successful_stk(
                public_id=payment.public_id,
                expected_amount=Decimal("100.00"),
                purpose="sale",
            )
        self.assertIn("already applied", str(ctx.exception).lower())

    @override_settings(IS_HOSTED=True)
    def test_ngrok_skipped_on_hosted(self):
        with patch("urllib.request.urlopen") as mock_open:
            self.assertEqual(detect_ngrok_public_base_url(), "")
            mock_open.assert_not_called()

    def test_ensure_callback_secret_generates_when_missing(self):
        row = get_daraja_settings()
        row.callback_secret = ""
        row.save(update_fields=["callback_secret"])
        _invalidate_daraja_settings_cache()
        secret = ensure_callback_secret()
        self.assertTrue(len(secret) >= 20)
        row.refresh_from_db()
        self.assertEqual(row.callback_secret, secret)
