from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from employees.access import store_profile_session
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.daraja_stk import handle_stk_callback
from shops.models import (
    CompanyDarajaSettings,
    CompanyDeveloperPaymentSettings,
    MpesaStkPayment,
    MpesaStkPurpose,
    MpesaStkStatus,
)
from shops.services import (
    developer_payment_is_due,
    developer_payment_prompt_for_request,
    get_daraja_settings,
    get_developer_payment_settings,
    mark_developer_subscription_paid,
    update_developer_payment_settings,
)


class DeveloperPaymentIsolationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.password = "dev-pay-pass"
        self.user = User.objects.create_user(
            username="920011",
            password=self.password,
            email="dev-pay@test.local",
            first_name="DEV",
            last_name="PAY",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="920011",
            phone_country_code="+254",
            phone_number="711000011",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        CompanyDeveloperPaymentSettings.objects.filter(pk=1).delete()
        CompanyDarajaSettings.objects.get_or_create(pk=1)

    def _login(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["employee_profile_meta"] = {
            "user_id": self.user.pk,
            "employee_id": self.profile.employee_id,
            "role": self.profile.role,
            "status": self.profile.status,
        }
        session.save()

    def test_defaults_disabled_and_not_due(self):
        row = get_developer_payment_settings()
        self.assertFalse(row.prompts_enabled)
        self.assertEqual(row.total_amount(), Decimal("0"))
        self.assertFalse(developer_payment_is_due(row))

    def test_amounts_alone_do_not_make_due_until_enabled(self):
        row = update_developer_payment_settings(
            prompts_enabled=False,
            system_subscription_amount="100",
            whatsapp_subscription_amount="50",
            hosting_subscription_amount="25",
        )
        self.assertEqual(row.total_amount(), Decimal("175.00"))
        self.assertFalse(developer_payment_is_due(row))

    def test_enabled_with_zero_amounts_not_due(self):
        row = update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="0",
            whatsapp_subscription_amount="0",
            hosting_subscription_amount="0",
        )
        self.assertFalse(developer_payment_is_due(row))

    def test_enabled_with_amounts_is_due(self):
        row = update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="100",
            whatsapp_subscription_amount="0",
            hosting_subscription_amount="0",
        )
        self.assertTrue(developer_payment_is_due(row))

    def test_prompt_hidden_when_disabled_even_for_active_employee(self):
        update_developer_payment_settings(
            prompts_enabled=False,
            system_subscription_amount="100",
        )
        request = self.factory.get("/it-support/")
        request.user = self.user
        request.session = self.client.session
        store_profile_session(request, self.profile)
        self.assertIsNone(developer_payment_prompt_for_request(request))

    def test_prompt_hidden_on_settings_pages_even_when_due(self):
        update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="100",
        )
        request = self.factory.get("/employees/settings/developer-payments/")
        request.user = self.user
        request.session = self.client.session
        store_profile_session(request, self.profile)
        self.assertIsNone(developer_payment_prompt_for_request(request))

    def test_prompt_appears_when_enabled_and_due(self):
        update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="100",
            whatsapp_subscription_amount="40",
            hosting_subscription_amount="10",
            popup_location="employee",
        )
        request = self.factory.get("/it-support/")
        request.user = self.user
        request.session = {}
        store_profile_session(request, self.profile)
        prompt = developer_payment_prompt_for_request(request)
        self.assertIsNotNone(prompt)
        self.assertTrue(prompt["auto_open"])
        self.assertEqual(prompt["total_amount"], "150.00")
        self.assertEqual(len(prompt["line_items"]), 3)

    def test_saving_subscription_settings_does_not_change_company_daraja(self):
        daraja = get_daraja_settings()
        daraja.enable_stk_push = True
        daraja.shortcode = "174379"
        daraja.consumer_key = "company-key"
        daraja.consumer_secret = "company-secret"
        daraja.passkey = "company-pass"
        daraja.credentials_valid = True
        daraja.save()

        update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="250",
            whatsapp_subscription_amount="100",
            hosting_subscription_amount="50",
            prompt_cadence="quarterly",
            popup_location="shop",
            allow_dismiss=False,
        )

        daraja.refresh_from_db()
        self.assertTrue(daraja.enable_stk_push)
        self.assertEqual(daraja.shortcode, "174379")
        self.assertEqual(daraja.consumer_key, "company-key")
        self.assertEqual(daraja.consumer_secret, "company-secret")
        self.assertEqual(daraja.passkey, "company-pass")
        self.assertTrue(daraja.credentials_valid)

    def test_company_daraja_page_omits_developer_subscription_form(self):
        self._login()
        response = self.client.get("/employees/settings/company-daraja/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Developer subscription prompts")
        self.assertNotContains(response, 'data-developer-form')

    def test_developer_page_includes_subscription_form(self):
        self._login()
        response = self.client.get("/employees/settings/developer-payments/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Developer subscription prompts")
        self.assertContains(response, 'data-developer-form')

    def test_sale_stk_status_not_accepted_by_developer_endpoint(self):
        self._login()
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.SUCCESS,
            amount=Decimal("200.00"),
            phone="254711000011",
            mpesa_receipt_number="SALE123",
            completed_at=timezone.now(),
        )
        url = reverse(
            "employees:developer_payment_stk_status",
            kwargs={"payment_id": payment.public_id},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        row = get_developer_payment_settings()
        self.assertIsNone(row.last_paid_at)
        self.assertEqual(row.last_mpesa_receipt, "")

    def test_developer_callback_marks_subscription_not_sale_payments(self):
        update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="100",
        )
        payment = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.DEVELOPER,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("100.00"),
            phone="254711000011",
            checkout_request_id="ws_CO_DEV_1",
            merchant_request_id="mr_DEV_1",
        )
        sale = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.SALE,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("500.00"),
            phone="254711000011",
            checkout_request_id="ws_CO_SALE_1",
            merchant_request_id="mr_SALE_1",
        )

        handle_stk_callback(
            {
                "Body": {
                    "stkCallback": {
                        "MerchantRequestID": "mr_DEV_1",
                        "CheckoutRequestID": "ws_CO_DEV_1",
                        "ResultCode": 0,
                        "ResultDesc": "The service request is processed successfully.",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": 100},
                                {"Name": "MpesaReceiptNumber", "Value": "DEV999"},
                                {"Name": "PhoneNumber", "Value": 254711000011},
                            ]
                        },
                    }
                }
            }
        )

        payment.refresh_from_db()
        sale.refresh_from_db()
        row = get_developer_payment_settings()
        self.assertEqual(payment.status, MpesaStkStatus.SUCCESS)
        self.assertTrue(payment.applied)
        self.assertEqual(row.last_mpesa_receipt, "DEV999")
        self.assertIsNotNone(row.last_paid_at)
        self.assertEqual(sale.status, MpesaStkStatus.PENDING)
        self.assertFalse(sale.applied)

    def test_paid_subscription_is_not_due_until_next_period(self):
        row = update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="100",
            prompt_cadence="monthly",
        )
        mark_developer_subscription_paid(mpesa_receipt="ABC123")
        row = get_developer_payment_settings()
        self.assertFalse(developer_payment_is_due(row))

        # Force due by backdating last payment beyond cadence.
        row.last_paid_at = timezone.now() - timedelta(days=40)
        row.save(update_fields=["last_paid_at", "updated_at"])
        self.assertTrue(developer_payment_is_due(get_developer_payment_settings()))

    def test_workspace_page_has_no_popup_when_disabled(self):
        update_developer_payment_settings(
            prompts_enabled=False,
            system_subscription_amount="500",
        )
        self._login()
        response = self.client.get("/it-support/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-developer-payment-modal')

    def test_workspace_page_shows_popup_only_when_enabled(self):
        update_developer_payment_settings(
            prompts_enabled=True,
            system_subscription_amount="500",
            popup_location="employee",
        )
        self._login()
        response = self.client.get("/it-support/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-developer-payment-modal')
        self.assertContains(response, "Pay securely with M-Pesa")
