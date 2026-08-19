from django.test import SimpleTestCase, TestCase, override_settings

from shops.services import update_twilio_settings


class TwilioNumberTests(SimpleTestCase):
    def test_e164_kenya_and_international(self):
        from communications.twilio import _e164

        self.assertEqual(_e164("0712345678"), "+254712345678")
        self.assertEqual(_e164("+14155552671"), "+14155552671")
        self.assertEqual(_e164("whatsapp:+14155238886"), "+14155238886")

    def test_whatsapp_lid_is_detected(self):
        from communications.twilio import _looks_like_wa_lid, _strip_whatsapp_prefix

        self.assertTrue(_looks_like_wa_lid("KE.1033669479277157"))
        self.assertTrue(_looks_like_wa_lid("whatsapp:KE.1033669479277157"))
        self.assertFalse(_looks_like_wa_lid("+254795606115"))
        self.assertEqual(
            _strip_whatsapp_prefix("254795606115@c.us"),
            "254795606115",
        )

    def test_fake_message_sids_are_not_twilio_sids(self):
        from communications.twilio import is_twilio_message_sid

        self.assertTrue(is_twilio_message_sid("SM" + "a" * 32))
        self.assertTrue(is_twilio_message_sid("MM" + "b" * 32))
        self.assertFalse(is_twilio_message_sid("SM_FAKE_142570218000000"))
        self.assertFalse(is_twilio_message_sid("SMTESTREAD1"))
        self.assertFalse(is_twilio_message_sid(""))

    def test_fetch_skips_fake_message_sids(self):
        from unittest.mock import patch

        from communications.twilio import fetch_twilio_message

        with patch("communications.twilio.urlopen") as mocked:
            self.assertIsNone(
                fetch_twilio_message(
                    "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "token",
                    "SM_FAKE_142570218000000",
                )
            )
            mocked.assert_not_called()


class TwilioDeliverySyncTests(TestCase):
    def test_sync_skips_fake_sids_and_updates_real_ones(self):
        from unittest.mock import patch

        from communications.constants import CAMPAIGN_QUEUED, MSG_SENT
        from communications.models import BroadcastCampaign, OutboundMessage
        from communications.twilio import sync_outbound_delivery_status
        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from django.contrib.auth.models import User

        user = User.objects.create_user(
            username="860099",
            password="wa-sync",
            email="wa-sync@test.local",
            is_active=True,
        )
        it = EmployeeProfile.objects.create(
            user=user,
            employee_id="860099",
            phone_country_code="+254",
            phone_number="700000999",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        campaign = BroadcastCampaign.objects.create(
            created_by=it,
            body_template="Hi",
            status=CAMPAIGN_QUEUED,
            recipient_count=2,
        )
        fake = OutboundMessage.objects.create(
            campaign=campaign,
            client_name="FAKE",
            phone="254700000001",
            body="Hi",
            status=MSG_SENT,
            wa_message_id="SM_FAKE_142570218000000",
        )
        real_sid = "SM" + "c" * 32
        real = OutboundMessage.objects.create(
            campaign=campaign,
            client_name="REAL",
            phone="254700000002",
            body="Hi",
            status=MSG_SENT,
            wa_message_id=real_sid,
        )

        class DeliveredResponse:
            def read(self):
                return b'{"status":"delivered"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("communications.twilio.urlopen", return_value=DeliveredResponse()):
            updated = sync_outbound_delivery_status(force=True)
        self.assertEqual(updated, 1)
        fake.refresh_from_db()
        real.refresh_from_db()
        self.assertIsNone(fake.delivered_at)
        self.assertIsNotNone(real.delivered_at)
        self.assertEqual(fake.provider_status, "local")

    def test_sync_marks_twilio_404_and_does_not_retry(self):
        from io import BytesIO
        from unittest.mock import patch
        from urllib.error import HTTPError

        from django.contrib.auth.models import User
        from django.core.cache import cache

        from communications.constants import CAMPAIGN_QUEUED, MSG_SENT
        from communications.models import BroadcastCampaign, OutboundMessage
        from communications.twilio import sync_outbound_delivery_status
        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

        cache.clear()
        user = User.objects.create_user(
            username="860098",
            password="wa-404",
            email="wa-404@test.local",
            is_active=True,
        )
        it = EmployeeProfile.objects.create(
            user=user,
            employee_id="860098",
            phone_country_code="+254",
            phone_number="700000998",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        campaign = BroadcastCampaign.objects.create(
            created_by=it,
            body_template="Hi",
            status=CAMPAIGN_QUEUED,
            recipient_count=1,
        )
        sid = "SM" + "d" * 32
        row = OutboundMessage.objects.create(
            campaign=campaign,
            client_name="GONE",
            phone="254700000003",
            body="Hi",
            status=MSG_SENT,
            wa_message_id=sid,
        )

        def fake_urlopen(request, timeout=15):
            raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=BytesIO())

        with patch("communications.twilio.urlopen", side_effect=fake_urlopen) as mocked:
            self.assertEqual(sync_outbound_delivery_status(force=True), 0)
            self.assertEqual(mocked.call_count, 1)
            row.refresh_from_db()
            self.assertEqual(row.provider_status, "missing")
            self.assertEqual(sync_outbound_delivery_status(force=True), 0)
            self.assertEqual(mocked.call_count, 1)

    def test_sync_cooldown_skips_repeat_fetch(self):
        from unittest.mock import patch

        from django.contrib.auth.models import User
        from django.core.cache import cache

        from communications.constants import CAMPAIGN_QUEUED, MSG_SENT
        from communications.models import BroadcastCampaign, OutboundMessage
        from communications.twilio import sync_outbound_delivery_status
        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

        cache.clear()
        user = User.objects.create_user(
            username="860097",
            password="wa-cool",
            email="wa-cool@test.local",
            is_active=True,
        )
        it = EmployeeProfile.objects.create(
            user=user,
            employee_id="860097",
            phone_country_code="+254",
            phone_number="700000997",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        campaign = BroadcastCampaign.objects.create(
            created_by=it,
            body_template="Hi",
            status=CAMPAIGN_QUEUED,
            recipient_count=1,
        )
        OutboundMessage.objects.create(
            campaign=campaign,
            client_name="REAL",
            phone="254700000004",
            body="Hi",
            status=MSG_SENT,
            wa_message_id="SM" + "e" * 32,
        )
        with patch("communications.twilio.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = b'{"status":"queued"}'
            sync_outbound_delivery_status()
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(sync_outbound_delivery_status(), 0)
            self.assertEqual(mocked.call_count, 1)


class TwilioSendChannelTests(TestCase):
    def test_geo_permission_error_is_explained(self):
        from communications.twilio import friendly_send_error, is_retryable_error

        raw = (
            "Permission to send an SMS has not been enabled for the region "
            "indicated by the 'To' number: +254795606115 (21408)"
        )
        self.assertFalse(is_retryable_error(raw))
        text = friendly_send_error(raw)
        self.assertIn("WhatsApp", text)
        self.assertIn("21408", text)

    def test_send_uses_whatsapp_not_sms(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs

        from communications.twilio import send_whatsapp_message

        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="",
        )

        class FakeResponse:
            def read(self):
                return b'{"sid":"SMWHATSAPP1","status":"queued"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        captured = {}

        def fake_urlopen(request, timeout=30):
            url = request.full_url
            if "/Messages/" in url and not url.endswith("Messages.json"):
                class DeliveredResponse:
                    def read(self):
                        return b'{"sid":"SMWHATSAPP1","status":"delivered"}'

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                return DeliveredResponse()
            captured["body"] = parse_qs(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("communications.twilio.urlopen", fake_urlopen), patch(
            "communications.twilio.time.sleep"
        ):
            result = send_whatsapp_message(phone="0795606115", text="Hi Kim")
        self.assertTrue(result["ok"])
        self.assertEqual(captured["body"]["To"], ["whatsapp:+254795606115"])
        self.assertEqual(captured["body"]["From"], ["whatsapp:+14155552671"])

    def test_localhost_media_url_is_not_sent(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs

        from communications.twilio import is_twilio_fetchable_url, send_whatsapp_message

        self.assertFalse(is_twilio_fetchable_url("http://localhost:8000/media/a.jpg"))
        self.assertFalse(is_twilio_fetchable_url("http://127.0.0.1/media/a.jpg"))
        self.assertTrue(is_twilio_fetchable_url("https://cdn.example.com/a.jpg"))

        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="",
        )

        class FakeResponse:
            def read(self):
                return b'{"sid":"SMTEXT1","status":"queued"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        captured = {}

        def fake_urlopen(request, timeout=30):
            captured["body"] = parse_qs(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("communications.twilio.urlopen", fake_urlopen), patch(
            "communications.twilio.time.sleep"
        ):
            result = send_whatsapp_message(
                phone="0795606115",
                text="PIXEL 9",
                media_path="http://localhost:8000/media/items/pixel.jpg",
            )
        self.assertTrue(result["ok"])
        self.assertNotIn("MediaUrl", captured["body"])

    def test_invalid_media_retries_text_only(self):
        from io import BytesIO
        from unittest.mock import patch
        from urllib.error import HTTPError
        from urllib.parse import parse_qs

        from communications.twilio import (
            friendly_send_error,
            is_retryable_error,
            send_whatsapp_message,
        )

        self.assertIn("photo", friendly_send_error("Invalid media URL(s) (21620)").lower())
        self.assertFalse(is_retryable_error("Invalid media URL(s) (21620)"))

        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="",
        )

        class FakeResponse:
            def read(self):
                return b'{"sid":"SMTEXT2","status":"queued"}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        calls = []

        def fake_urlopen(request, timeout=30):
            body = parse_qs(request.data.decode("utf-8"))
            calls.append(body)
            if body.get("MediaUrl"):
                raise HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=BytesIO(b'{"code":21620,"message":"Invalid media URL(s)"}'),
                )
            return FakeResponse()

        with patch("communications.twilio.urlopen", fake_urlopen), patch(
            "communications.twilio.time.sleep"
        ):
            result = send_whatsapp_message(
                phone="0795606115",
                text="PIXEL 9",
                media_path="https://example.com/missing.jpg",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2)
        self.assertIn("MediaUrl", calls[0])
        self.assertNotIn("MediaUrl", calls[1])

    def test_sandbox_join_info_builds_phrase(self):
        from communications.twilio import friendly_send_error, sandbox_join_info

        row = update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            whatsapp_from="whatsapp:+14155238886",
            from_number="+14155552671",
            join_code="happy-tiger",
        )
        info = sandbox_join_info(row)
        self.assertEqual(info["phrase"], "join happy-tiger")
        self.assertIn("join%20happy-tiger", info["wa_link"])
        self.assertIn("join happy-tiger", friendly_send_error("63015"))

    def test_send_fails_when_sandbox_not_joined(self):
        from unittest.mock import patch

        from communications.twilio import send_whatsapp_message

        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            whatsapp_from="whatsapp:+14155238886",
            from_number="+14155552671",
        )

        class FakeResponse:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=30):
            url = request.full_url
            if url.endswith("Messages.json"):
                return FakeResponse(b'{"sid":"SMFAIL15","status":"queued"}')
            return FakeResponse(
                b'{"sid":"SMFAIL15","status":"failed","error_code":63015,"error_message":null}'
            )

        with patch("communications.twilio.urlopen", fake_urlopen), patch(
            "communications.twilio.time.sleep"
        ):
            result = send_whatsapp_message(phone="0795606115", text="Hi Kim")
        self.assertFalse(result["ok"])
        self.assertIn("sandbox", result["error"].lower())


class TwilioSettingsTests(TestCase):
    def test_save_twilio_credentials(self):
        row = update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="",
        )
        self.assertTrue(row.has_twilio_credentials())
        self.assertEqual(row.twilio_account_sid, "ACtest")
        self.assertEqual(row.twilio_from_number, "+14155552671")


class WhatsAppAutomationsPageTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from shops.models import (
            Client,
            Shop,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptStatus,
        )

        self.user = User.objects.create_user(
            username="860011",
            password="wa-auto",
            email="wa-auto@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860011",
            phone_country_code="+254",
            phone_number="700000961",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="WA AUTO SHOP",
            location="NAIROBI",
            email="wa-shop@test.local",
            phone_number="0700000961",
            login_code="860111",
            password_hash="x",
            created_by=self.it,
        )
        self.customer = Client.objects.create(
            full_name="JANE DOE",
            phone_number="0712345678",
            phone_normalized="254712345678",
            created_by=self.it,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="WA-1",
            kind=ShopReceiptKind.SALE,
            total=500,
            amount_paid=500,
            created_by=self.it,
            client=self.customer,
            client_name=self.customer.full_name,
            client_phone=self.customer.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )

    def test_page_shows_what_to_send(self):
        self.client.force_login(self.user)
        response = self.client.get("/employees/settings/whatsapp/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WhatsApp settings")
        self.assertContains(response, "What to send")
        self.assertContains(response, "Cash sale receipts")
        self.assertContains(response, "Credit sales")
        self.assertContains(response, "Buy stock")
        self.assertContains(response, "Register expense")
        self.assertContains(
            response,
            "Send a WhatsApp notice to the supplier after stock is bought from the shop popup.",
        )
        self.assertContains(
            response,
            "Send a credit sale notice to the customer phone when credit is taken at the cart.",
        )
        self.assertNotContains(response, "Who to share with")
        self.assertNotContains(response, "Join Twilio sandbox")
        self.assertNotContains(response, "Sent &amp; viewed")
        self.assertNotContains(response, "Share items")
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertNotIn("Marketing links", labels)
        self.assertNotIn("Communication settings", labels)
        self.assertIn("Twilio settings", labels)
        self.assertIn("WhatsApp settings", labels)

    def test_workspace_page_redirects_to_communications_settings(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/whatsapp/")
        self.assertRedirects(response, "/employees/settings/communication-settings/")

    def test_communications_hub_links_to_twilio_and_whatsapp(self):
        self.client.force_login(self.user)
        response = self.client.get("/employees/settings/communication-settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Twilio settings")
        self.assertContains(response, "WhatsApp settings")
        self.assertContains(response, 'href="/employees/settings/twilio/"')
        self.assertContains(response, 'href="/employees/settings/whatsapp/"')
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertNotIn("Marketing links", labels)
        self.assertNotIn("Communication settings", labels)
        self.assertIn("Twilio settings", labels)
        self.assertIn("WhatsApp settings", labels)
        self.assertNotIn("Share items", labels)

    def test_old_communications_url_redirects(self):
        self.client.force_login(self.user)
        response = self.client.get("/employees/settings/communications/")
        self.assertRedirects(response, "/employees/settings/communication-settings/")

    def test_twilio_settings_page(self):
        self.client.force_login(self.user)
        response = self.client.get("/employees/settings/twilio/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account SID")
        self.assertContains(response, "Save Twilio")
        self.assertContains(response, "Join Twilio sandbox on the customer phone")
        self.assertContains(response, "Open WhatsApp on this phone")
        self.assertContains(response, "When a message comes in")
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertNotIn("Marketing links", labels)
        self.assertNotIn("Communication settings", labels)
        self.assertIn("Twilio settings", labels)
        self.assertIn("WhatsApp settings", labels)

    def test_save_audience(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/employees/settings/whatsapp/",
            {
                "action": "save_audience",
                "audience_type": "sale",
                "last_purchase_days": "30",
                "shop_id": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["automation_audience_type"], "sale")
        self.assertEqual(data["automation_last_purchase_days"], "30")
        self.assertGreaterEqual(data["recipient_count"], 1)
        names = [row["full_name"] for row in data["recipients"]]
        phones = [row["phone"] for row in data["recipients"]]
        self.assertIn("JANE DOE", names)
        self.assertTrue(any("712345678" in phone for phone in phones))
        self.assertTrue(any(row.get("client_id") for row in data["recipients"]))

    def test_send_requires_selected_people(self):
        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            "/employees/settings/whatsapp/",
            {"action": "send_website"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Select at least one", response.json()["error"])

    def test_send_only_selected_person(self):
        from django.test import override_settings

        from communications.models import BroadcastCampaign
        from shops.models import Client, ShopReceipt, ShopReceiptKind, ShopReceiptStatus

        other = Client.objects.create(
            full_name="JOHN DOE",
            phone_number="0700000002",
            phone_normalized="254700000002",
            created_by=self.it,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="WA-2",
            kind=ShopReceiptKind.SALE,
            total=200,
            amount_paid=200,
            created_by=self.it,
            client=other,
            client_name=other.full_name,
            client_phone=other.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )
        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with override_settings(COMMS_SEND_MODE="cron"):
            response = self.client.post(
                "/employees/settings/whatsapp/",
                {
                    "action": "send_website",
                    "client_ids": [str(self.customer.pk)],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        campaign = BroadcastCampaign.objects.latest("id")
        self.assertEqual(campaign.recipient_count, 1)
        self.assertEqual(
            list(campaign.messages.values_list("client_id", flat=True)),
            [self.customer.pk],
        )


class WhatsAppCataloguePageTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from items.models import Item
        from shops.models import (
            Client,
            Shop,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptStatus,
        )

        self.user = User.objects.create_user(
            username="860021",
            password="wa-cat",
            email="wa-cat@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860021",
            phone_country_code="+254",
            phone_number="700000971",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="WA CAT SHOP",
            location="NAIROBI",
            email="wa-cat@test.local",
            phone_number="0700000971",
            login_code="860121",
            password_hash="x",
            created_by=self.it,
        )
        self.customer = Client.objects.create(
            full_name="JANE DOE",
            phone_number="0712345678",
            phone_normalized="254712345678",
            created_by=self.it,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="WAC-1",
            kind=ShopReceiptKind.SALE,
            total=500,
            amount_paid=500,
            created_by=self.it,
            client=self.customer,
            client_name=self.customer.full_name,
            client_phone=self.customer.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )
        self.item = Item.objects.create(
            category="PHONES",
            name="PIXEL 9",
            minimum_selling_price=80000,
            shop_price=85000,
            created_by=self.it,
        )

    def test_page_and_sidebar(self):
        self.client.force_login(self.user)
        home = self.client.get("/it-support/marketing/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Share items")
        self.assertContains(home, 'href="/it-support/whatsapp/catalogue/"')
        response = self.client.get("/it-support/whatsapp/catalogue/")
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertEqual(labels, ["Dashboard", "Share items", "Contacts", "Inbox", "Activities"])
        self.assertTrue(response.context["page_sidebar"]["primary"][1].get("active"))
        self.assertFalse(response.context["page_sidebar"]["primary"][2].get("active"))
        self.assertIn(
            "/it-support/whatsapp/contacts/",
            response.context["page_sidebar"]["primary"][2]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/inbox/",
            response.context["page_sidebar"]["primary"][3]["href"],
        )
        self.assertIn(
            "/it-support/marketing/activities/",
            response.context["page_sidebar"]["primary"][4]["href"],
        )
        self.assertContains(response, "Who to share with")
        self.assertContains(response, "Pick items")
        self.assertContains(response, "When to send")
        self.assertContains(response, "Edit message")
        self.assertContains(response, "card image")
        self.assertContains(response, "data-wa-message-body")
        self.assertContains(response, "{items}")
        self.assertContains(response, "PIXEL 9")
        self.assertContains(response, "KSh 85,000")
        self.assertNotContains(response, "New items")
        self.assertNotContains(response, "Sent &amp; viewed")
        self.assertContains(response, "Send selected items now")
        self.assertContains(response, "data-wa-item-pick")
        self.assertContains(response, "JANE DOE")
        self.assertContains(response, "filter_item_id")
        self.assertContains(response, "Add matching items")

    def test_missing_item_image_file_is_not_rendered(self):
        self.item.image = "items/images/25.jpeg"
        self.item.save(update_fields=["image"])
        self.client.force_login(self.user)
        response = self.client.get("/it-support/whatsapp/catalogue/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/media/items/images/25.jpeg")
        self.assertContains(response, "PIXEL 9")

    def test_catalogue_get_does_not_poll_twilio(self):
        from unittest.mock import patch

        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with patch("communications.views.sync_outbound_delivery_status") as mocked:
            response = self.client.get("/it-support/whatsapp/catalogue/")
        self.assertEqual(response.status_code, 200)
        mocked.assert_not_called()

    def test_activities_page_and_sidebar(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/marketing/activities/")
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertEqual(labels, ["Dashboard", "Communication settings", "Share items", "Contacts", "Inbox", "Activities"])
        self.assertTrue(response.context["page_sidebar"]["primary"][5].get("active"))
        self.assertContains(response, "Waiting to send")
        self.assertContains(response, "Sent history")
        self.assertContains(response, "Nothing waiting. Scheduled item shares and queued messages will show up here.")
        self.assertContains(response, 'href="/it-support/whatsapp/catalogue/"')
        self.assertContains(response, 'href="/it-support/whatsapp/contacts/"')
        self.assertIn("pending", response.context["activities"])
        self.assertIn("history", response.context["activities"])
        self.assertContains(response, "Open a send to see the message")

    def test_caption_includes_name_and_price(self):
        from communications.automations import build_item_catalogue_caption

        text = build_item_catalogue_caption(self.item)
        self.assertIn("PIXEL 9", text)
        self.assertIn("PHONES", text)
        self.assertIn("KSh 85,000", text)
        self.assertIn("{first_name}", text)

    def test_combined_caption_lists_every_item(self):
        from communications.automations import build_catalogue_share_caption
        from items.models import Item

        cable = Item.objects.create(
            category="CABLES",
            name="USB-C CABLE",
            minimum_selling_price=200,
            shop_price=350,
            created_by=self.it,
        )
        text = build_catalogue_share_caption([self.item, cable])
        self.assertEqual(text, "Hi {first_name},")

    def test_custom_template_keeps_intro_and_items(self):
        from communications.automations import apply_catalogue_message_template
        from items.models import Item

        cable = Item.objects.create(
            category="CABLES",
            name="USB-C CABLE",
            minimum_selling_price=200,
            shop_price=350,
            created_by=self.it,
        )
        text = apply_catalogue_message_template(
            "Hello {first_name},\n\nNew stock:\n{items}\n\nReply YES.",
            [self.item, cable],
        )
        self.assertIn("Hello {first_name}", text)
        self.assertIn("New stock:", text)
        self.assertIn("PIXEL 9", text)
        self.assertIn("USB-C CABLE", text)
        self.assertIn("Reply YES.", text)
        self.assertNotIn("{items}", text)

        card_caption = apply_catalogue_message_template(
            "Hello {first_name},\n\nNew stock:\n{items}\n\nReply YES.",
            [self.item, cable],
            card=True,
        )
        self.assertEqual(card_caption, "Hello {first_name},\n\nNew stock:\n\nReply YES.")
        self.assertNotIn("PIXEL 9", card_caption)

    def test_catalogue_card_builds_jpeg(self):
        from communications.catalogue_card import compose_catalogue_card
        from items.models import Item

        cable = Item.objects.create(
            category="CABLES",
            name="USB-C CABLE",
            minimum_selling_price=200,
            shop_price=350,
            created_by=self.it,
        )
        card = compose_catalogue_card([self.item, cable])
        self.assertIsNotNone(card)
        data = card.read()
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
        from PIL import Image as PILImage
        from io import BytesIO

        image = PILImage.open(BytesIO(data))
        self.assertEqual(image.size[0], 1080)
        self.assertGreater(image.size[1], 900)

    def test_localhost_item_image_is_omitted(self):
        from unittest.mock import patch

        from django.test import RequestFactory, override_settings

        from communications.campaigns import item_whatsapp_media_url

        class FakeImage:
            url = "/media/items/pixel.jpg"

        self.item.image = FakeImage()
        request = RequestFactory().get("/it-support/whatsapp/catalogue/")
        request.META["HTTP_HOST"] = "localhost:8000"
        with (
            patch("shops.daraja_stk.detect_ngrok_public_base_url", return_value=""),
            patch("shops.daraja_stk.resolve_callback_base_url", return_value=""),
        ):
            self.assertEqual(item_whatsapp_media_url(self.item, request=request), "")
            with override_settings(DARAJA_CALLBACK_BASE_URL="https://shop.example.com"):
                url = item_whatsapp_media_url(self.item, request=request)
        self.assertEqual(url, "https://shop.example.com/media/items/pixel.jpg")

    def test_send_requires_items_and_people(self):
        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        missing_items = self.client.post(
            "/it-support/whatsapp/catalogue/",
            {"action": "send_catalogue", "client_ids": [str(self.customer.pk)]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(missing_items.status_code, 400)
        self.assertIn("item", missing_items.json()["error"].lower())
        missing_people = self.client.post(
            "/it-support/whatsapp/catalogue/",
            {"action": "send_catalogue", "item_ids": [str(self.item.pk)]},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(missing_people.status_code, 400)
        self.assertIn("person", missing_people.json()["error"].lower())

    def test_send_selected_item_to_selected_person(self):
        from django.test import override_settings

        from communications.models import BroadcastCampaign, OutboundMessage

        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with override_settings(COMMS_SEND_MODE="cron"):
            response = self.client.post(
                "/it-support/whatsapp/catalogue/",
                {
                    "action": "send_catalogue",
                    "item_ids": [str(self.item.pk)],
                    "client_ids": [str(self.customer.pk)],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        campaign = BroadcastCampaign.objects.latest("id")
        self.assertEqual(campaign.recipient_count, 1)
        message = OutboundMessage.objects.get(campaign=campaign)
        self.assertEqual(message.client_id, self.customer.pk)
        self.assertIn("Hi JANE", message.body)
        self.assertTrue((campaign.image.name or "").lower().endswith(".jpg"))
        self.assertTrue(campaign.image.read().startswith(b"\xff\xd8\xff"))
        campaign.image.seek(0)

    def test_send_multiple_items_as_one_message(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from PIL import Image as PILImage

        from communications.models import BroadcastCampaign, OutboundMessage
        from items.models import Item

        def jpeg(name, color):
            buf = BytesIO()
            PILImage.new("RGB", (48, 48), color).save(buf, format="JPEG")
            return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")

        cable = Item.objects.create(
            category="CABLES",
            name="USB-C CABLE",
            minimum_selling_price=200,
            shop_price=350,
            created_by=self.it,
            image=jpeg("cable.jpg", (20, 80, 160)),
        )
        self.item.image = jpeg("pixel.jpg", (180, 40, 40))
        self.item.save(update_fields=["image"])
        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with override_settings(
            COMMS_SEND_MODE="cron",
            DARAJA_CALLBACK_BASE_URL="https://shop.example.com",
        ):
            response = self.client.post(
                "/it-support/whatsapp/catalogue/",
                {
                    "action": "send_catalogue",
                    "item_ids": [str(self.item.pk), str(cable.pk)],
                    "client_ids": [str(self.customer.pk)],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        campaign = BroadcastCampaign.objects.latest("id")
        self.assertEqual(campaign.recipient_count, 1)
        self.assertTrue(campaign.image)
        self.assertTrue((campaign.image.name or "").lower().endswith(".jpg"))
        messages = list(OutboundMessage.objects.filter(campaign=campaign))
        self.assertEqual(len(messages), 1)
        body = messages[0].body
        self.assertIn("Hi JANE", body)
        self.assertNotIn("USB-C CABLE", body)
        self.assertNotIn("PIXEL 9", body)
        self.assertTrue(messages[0].image_path.startswith("https://shop.example.com/"))
        self.assertTrue(messages[0].image_path.lower().endswith(".jpg"))
        card = campaign.image.read()
        self.assertTrue(card.startswith(b"\xff\xd8\xff"))

    def test_send_uses_custom_message_template(self):
        from django.test import override_settings

        from communications.models import BroadcastCampaign, OutboundMessage

        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with override_settings(COMMS_SEND_MODE="cron"):
            response = self.client.post(
                "/it-support/whatsapp/catalogue/",
                {
                    "action": "send_catalogue",
                    "item_ids": [str(self.item.pk)],
                    "client_ids": [str(self.customer.pk)],
                    "message_body": "Hello {first_name},\n\nHot deal:\n{items}\n\nReply YES.",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        body = OutboundMessage.objects.get(
            campaign=BroadcastCampaign.objects.latest("id")
        ).body
        self.assertIn("Hello JANE", body)
        self.assertIn("Hot deal:", body)
        self.assertIn("Reply YES.", body)
        self.assertNotIn("{items}", body)
        self.assertNotIn("{first_name}", body)
        self.assertNotIn("PIXEL 9", body)

    def test_preview_includes_share_items(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/it-support/whatsapp/catalogue/",
            {
                "action": "preview_audience",
                "audience_type": "sale",
                "last_purchase_days": "",
                "shop_id": "",
                "filter_item_id": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        names = [row["name"] for row in data.get("share_items") or []]
        self.assertIn("PIXEL 9", names)

    def test_schedule_creates_later_waves(self):
        from django.test import override_settings

        from communications.constants import CAMPAIGN_DRAFT, CAMPAIGN_QUEUED
        from communications.models import BroadcastCampaign

        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with override_settings(COMMS_SEND_MODE="cron"):
            response = self.client.post(
                "/it-support/whatsapp/catalogue/",
                {
                    "action": "send_catalogue",
                    "item_ids": [str(self.item.pk)],
                    "client_ids": [str(self.customer.pk)],
                    "schedule_period": "7",
                    "schedule_times": "3",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload.get("scheduled_sends"), 2)
        self.assertEqual(BroadcastCampaign.objects.count(), 3)
        self.assertEqual(
            BroadcastCampaign.objects.filter(status=CAMPAIGN_QUEUED).count(), 1
        )
        self.assertEqual(
            BroadcastCampaign.objects.filter(status=CAMPAIGN_DRAFT).count(), 2
        )

    def test_activities_lists_unsent_scheduled_sends(self):
        from django.test import override_settings

        from communications.campaigns import activities_payload, campaign_as_dict
        from communications.constants import CAMPAIGN_DRAFT
        from communications.models import BroadcastCampaign

        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        self.client.force_login(self.user)
        with override_settings(COMMS_SEND_MODE="cron"):
            response = self.client.post(
                "/it-support/whatsapp/catalogue/",
                {
                    "action": "send_catalogue",
                    "item_ids": [str(self.item.pk)],
                    "client_ids": [str(self.customer.pk)],
                    "schedule_period": "7",
                    "schedule_times": "3",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("ok"))

        payload = activities_payload()
        self.assertEqual(len(payload["pending"]), 3)
        self.assertEqual(payload["summary"]["waiting_messages"], 3)
        self.assertGreaterEqual(payload["summary"]["scheduled_batches"], 2)
        draft = BroadcastCampaign.objects.filter(status=CAMPAIGN_DRAFT).first()
        self.assertIsNotNone(draft)
        row = campaign_as_dict(draft)
        self.assertTrue(row["is_pending"])
        self.assertTrue(row["is_scheduled"])
        self.assertTrue(row["can_cancel"])
        self.assertIn("Item share", row["kind_label"])
        self.assertTrue(row["timing_label"].startswith("Sends "))

        page = self.client.get("/it-support/marketing/activities/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Waiting to send")
        self.assertContains(page, "Item share")
        cancel = self.client.post(
            "/it-support/marketing/activities/",
            {"action": "cancel_campaign", "campaign_id": str(draft.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(cancel.status_code, 200)
        self.assertTrue(cancel.json().get("ok"))
        self.assertIn("activities", cancel.json())
        draft.refresh_from_db()
        from communications.constants import CAMPAIGN_CANCELLED

        self.assertEqual(draft.status, CAMPAIGN_CANCELLED)

    def test_auto_skip_when_toggle_off(self):
        from communications.automations import maybe_send_new_item_catalogue
        from communications.models import BroadcastCampaign

        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        maybe_send_new_item_catalogue(self.item)
        self.assertFalse(BroadcastCampaign.objects.exists())

    def test_auto_sends_new_item_when_toggle_on(self):
        from django.test import override_settings

        from communications.automations import maybe_send_new_item_catalogue
        from communications.models import BroadcastCampaign, OutboundMessage
        from shops.services import set_communications_setting

        update_twilio_settings(
            account_sid="ACtest",
            auth_token="secret-token",
            from_number="+14155552671",
        )
        set_communications_setting(field="enable_automations", enabled=True)
        set_communications_setting(field="auto_item_catalogue", enabled=True)
        with override_settings(COMMS_SEND_MODE="cron"):
            maybe_send_new_item_catalogue(self.item)
        campaign = BroadcastCampaign.objects.latest("id")
        self.assertEqual(campaign.recipient_count, 1)
        body = OutboundMessage.objects.get(campaign=campaign).body
        self.assertIn("Hi", body)
        self.assertTrue((campaign.image.name or "").lower().endswith(".jpg"))
        card = campaign.image.read()
        self.assertTrue(card.startswith(b"\xff\xd8\xff"))

    def test_create_item_calls_catalogue_share(self):
        from unittest.mock import patch

        from items.services import create_item

        with patch(
            "communications.automations.maybe_send_new_item_catalogue"
        ) as share:
            item = create_item(
                self.it,
                {
                    "category": "CABLES",
                    "name": "USB-C CABLE",
                    "minimum_selling_price": "200",
                    "shop_price": "350",
                },
                {},
            )
        share.assert_called_once()
        self.assertEqual(share.call_args.args[0].pk, item.pk)
        self.assertEqual(item.name, "USB-C CABLE")


class WhatsAppContactsPageTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from shops.models import Client

        self.user = User.objects.create_user(
            username="860031",
            password="wa-contacts",
            email="wa-contacts@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860031",
            phone_country_code="+254",
            phone_number="700000981",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.customer = Client.objects.create(
            full_name="JANE DOE",
            phone_number="0712345678",
            phone_normalized="254712345678",
            created_by=self.it,
        )
        self.lead = Client.objects.create(
            full_name="LEAD ONLY",
            phone_number="0798765432",
            phone_normalized="254798765432",
            created_by=self.it,
        )

    def test_page_lists_every_contact_and_sidebar(self):
        self.client.force_login(self.user)
        catalogue = self.client.get("/it-support/whatsapp/catalogue/")
        self.assertContains(catalogue, 'href="/it-support/whatsapp/contacts/"')
        response = self.client.get("/it-support/whatsapp/contacts/")
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertEqual(
            labels, ["Dashboard", "Communication settings", "Share items", "Contacts", "Inbox", "Activities"]
        )
        self.assertTrue(response.context["page_sidebar"]["primary"][3].get("active"))
        names = [row["full_name"] for row in response.context["contacts"]]
        self.assertEqual(response.context["contact_count"], 2)
        self.assertIn("JANE DOE", names)
        self.assertIn("LEAD ONLY", names)
        self.assertContains(response, "LEAD ONLY")
        self.assertContains(response, "Add contact")
        self.assertContains(response, "Create group")
        self.assertContains(response, "Join group")

    def test_add_contact(self):
        from shops.models import Client

        self.client.force_login(self.user)
        response = self.client.post(
            "/it-support/whatsapp/contacts/",
            {
                "action": "add_contact",
                "full_name": "Sam Otieno",
                "phone": "0711002200",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        saved = Client.objects.get(phone_normalized="254711002200")
        self.assertEqual(saved.full_name, "SAM OTIENO")
        self.assertContains(response, "SAM OTIENO")

    def test_create_and_join_group(self):
        from communications.models import WhatsAppGroup

        self.client.force_login(self.user)
        created = self.client.post(
            "/it-support/whatsapp/contacts/",
            {
                "action": "create_group",
                "name": "Sales team",
                "member_ids": [str(self.customer.pk), str(self.lead.pk)],
            },
            follow=True,
        )
        self.assertEqual(created.status_code, 200)
        group = WhatsAppGroup.objects.get(name="Sales team")
        self.assertEqual(group.members.count(), 2)
        self.assertContains(created, "Sales team")

        joined = self.client.post(
            "/it-support/whatsapp/contacts/",
            {
                "action": "join_group",
                "name": "Promo blast",
                "invite_link": "https://chat.whatsapp.com/AbCdEfGhIjK",
                "open_whatsapp": "1",
            },
        )
        self.assertEqual(joined.status_code, 302)
        self.assertEqual(joined["Location"], "https://chat.whatsapp.com/AbCdEfGhIjK")
        promo = WhatsAppGroup.objects.get(name="Promo blast")
        self.assertEqual(promo.source, "joined")
        self.assertEqual(promo.invite_link, "https://chat.whatsapp.com/AbCdEfGhIjK")

        bad = self.client.post(
            "/it-support/whatsapp/contacts/",
            {
                "action": "join_group",
                "invite_link": "https://example.com/not-whatsapp",
            },
            follow=True,
        )
        self.assertContains(bad, "Paste a WhatsApp group invite link")


class WhatsAppSendLogTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from communications.constants import CAMPAIGN_QUEUED, MSG_PENDING, MSG_SENT
        from communications.models import BroadcastCampaign, OutboundMessage
        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

        self.user = User.objects.create_user(
            username="860012",
            password="wa-send",
            email="wa-send@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860012",
            phone_country_code="+254",
            phone_number="700000962",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.campaign = BroadcastCampaign.objects.create(
            created_by=self.it,
            body_template="Hi {first_name}, browse our shop.",
            status=CAMPAIGN_QUEUED,
            recipient_count=2,
        )
        self.pending = OutboundMessage.objects.create(
            campaign=self.campaign,
            client_name="JANE DOE",
            phone="254712345678",
            body="Hi Jane",
            status=MSG_PENDING,
        )
        self.sent = OutboundMessage.objects.create(
            campaign=self.campaign,
            client_name="JOHN DOE",
            phone="254700000001",
            body="Hi John",
            status=MSG_SENT,
            wa_message_id="SMTESTREAD1",
        )

    def test_cancel_stops_pending_and_keeps_sent(self):
        from communications.constants import CAMPAIGN_CANCELLED, MSG_CANCELLED, MSG_SENT

        self.client.force_login(self.user)
        response = self.client.post(
            "/employees/settings/whatsapp/",
            {"action": "cancel_campaign", "campaign_id": str(self.campaign.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.pending.refresh_from_db()
        self.sent.refresh_from_db()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, CAMPAIGN_CANCELLED)
        self.assertEqual(self.pending.status, MSG_CANCELLED)
        self.assertEqual(self.sent.status, MSG_SENT)

    def test_twilio_read_marks_viewed(self):
        from communications.campaigns import outbound_display_status
        from communications.twilio import apply_message_status

        self.assertTrue(
            apply_message_status(message_sid="SMTESTREAD1", status="read")
        )
        self.sent.refresh_from_db()
        self.assertIsNotNone(self.sent.read_at)
        self.assertIsNotNone(self.sent.delivered_at)
        self.assertEqual(
            outbound_display_status(
                status=self.sent.status,
                read_at=self.sent.read_at,
                delivered_at=self.sent.delivered_at,
            ),
            "viewed",
        )

    def test_activity_detail_shows_message_recipients_and_status(self):
        from communications.campaigns import campaign_as_dict

        payload = campaign_as_dict(self.campaign)
        self.assertEqual(payload["messages"][0]["body"], "Hi Jane")
        self.assertEqual(payload["messages"][0]["status_label"], "Waiting")
        self.assertEqual(payload["messages"][1]["body"], "Hi John")
        self.assertEqual(payload["messages"][1]["status_label"], "Sent")

        self.client.force_login(self.user)
        url = f"/it-support/marketing/activities/{self.campaign.pk}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hi {first_name}, browse our shop.")
        self.assertContains(response, "JANE DOE")
        self.assertContains(response, "JOHN DOE")
        self.assertContains(response, "Hi Jane")
        self.assertContains(response, "Hi John")
        self.assertContains(response, "254712345678")
        self.assertContains(response, "Waiting")
        self.assertContains(response, "Sent")
        self.assertContains(response, "Back to activities")
        self.assertContains(response, 'href="/it-support/marketing/activities/"')
        self.assertTrue(response.context["page_sidebar"]["primary"][4].get("active"))

        json_page = self.client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(json_page.status_code, 200)
        body = json_page.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["campaign"]["id"], self.campaign.pk)
        self.assertEqual(len(body["campaign"]["messages"]), 2)

        missing = self.client.get("/it-support/marketing/activities/999999/")
        self.assertEqual(missing.status_code, 404)

    def test_twilio_sandbox_failure_marks_failed(self):
        from communications.constants import MSG_FAILED
        from communications.twilio import apply_message_status

        self.assertTrue(
            apply_message_status(
                message_sid="SMTESTREAD1",
                status="failed",
                error_code="63015",
                error_message="",
            )
        )
        self.sent.refresh_from_db()
        self.assertEqual(self.sent.status, MSG_FAILED)
        self.assertIn("sandbox", self.sent.error.lower())

    def test_cannot_cancel_finished_send(self):
        from communications.constants import CAMPAIGN_DONE

        self.campaign.status = CAMPAIGN_DONE
        self.campaign.save(update_fields=["status"])
        self.client.force_login(self.user)
        response = self.client.post(
            "/employees/settings/whatsapp/",
            {"action": "cancel_campaign", "campaign_id": str(self.campaign.pk)},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)


class WhatsAppRetryTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from communications.constants import CAMPAIGN_DONE, MSG_FAILED
        from communications.models import BroadcastCampaign, OutboundMessage
        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus

        self.user = User.objects.create_user(
            username="860013",
            password="wa-retry",
            email="wa-retry@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860013",
            phone_country_code="+254",
            phone_number="700000963",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.campaign = BroadcastCampaign.objects.create(
            created_by=self.it,
            body_template="Hi {first_name}, browse our shop.",
            status=CAMPAIGN_DONE,
            recipient_count=1,
            failed_count=1,
        )
        self.failed = OutboundMessage.objects.create(
            campaign=self.campaign,
            client_name="KIM",
            phone="254795606115",
            body="",
            status=MSG_FAILED,
            error="Authenticate",
        )

    def test_auth_error_is_detected(self):
        from communications.twilio import is_auth_error, is_retryable_error

        self.assertTrue(is_auth_error("Authenticate"))
        self.assertTrue(is_auth_error("Twilio rejected the Account SID or Auth Token."))
        self.assertFalse(is_retryable_error("Authenticate"))
        self.assertFalse(is_retryable_error("63015"))
        self.assertFalse(
            is_retryable_error("Twilio still does not see this customer in your sandbox.")
        )
        self.assertTrue(is_retryable_error("Could not reach Twilio: timed out"))

    def test_retry_failed_requeues_and_restores_body(self):
        from django.test import override_settings

        from communications.constants import CAMPAIGN_QUEUED, MSG_PENDING

        self.client.force_login(self.user)
        with override_settings(COMMS_SEND_MODE="cron"):
            response = self.client.post(
                "/employees/settings/whatsapp/",
                {"action": "retry_failed", "campaign_id": str(self.campaign.pk)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.failed.refresh_from_db()
        self.campaign.refresh_from_db()
        self.assertEqual(self.failed.status, MSG_PENDING)
        self.assertEqual(self.failed.error, "")
        self.assertEqual(self.failed.body, "Hi {first_name}, browse our shop.")
        self.assertEqual(self.campaign.status, CAMPAIGN_QUEUED)
        self.assertTrue(data["campaign"]["can_retry"] is False)
        self.assertEqual(data["campaign"]["pending_count"], 1)

    def test_auth_failure_does_not_loop(self):
        from unittest.mock import patch

        from communications.constants import MSG_FAILED, MSG_PENDING
        from communications.tasks import _send_one

        self.failed.status = MSG_PENDING
        self.failed.error = ""
        self.failed.body = "Hi Kim"
        self.failed.save(update_fields=["status", "error", "body"])
        with (
            patch("communications.tasks.time.sleep") as sleep,
            patch("communications.tasks.send_whatsapp_message") as send,
        ):
            send.return_value = {
                "ok": False,
                "retryable": False,
                "error": "Twilio rejected the Account SID or Auth Token.",
            }
            _send_one(self.failed)
        self.assertEqual(send.call_count, 1)
        sleep.assert_not_called()
        self.failed.refresh_from_db()
        self.assertEqual(self.failed.status, MSG_FAILED)
        self.assertIn("Auth Token", self.failed.error)

    def test_retryable_failure_loops_until_success(self):
        from unittest.mock import patch

        from communications.constants import MSG_PENDING, MSG_SENT
        from communications.tasks import _send_one

        self.failed.status = MSG_PENDING
        self.failed.error = ""
        self.failed.body = "Hi Kim"
        self.failed.save(update_fields=["status", "error", "body"])
        with (
            patch("communications.tasks.time.sleep"),
            patch("communications.tasks.send_whatsapp_message") as send,
        ):
            send.side_effect = [
                {"ok": False, "retryable": True, "error": "Could not reach Twilio: timed out"},
                {"ok": False, "retryable": True, "error": "HTTP 429"},
                {"ok": True, "messageId": "SMRETRY1", "chatId": "whatsapp:+254795606115"},
            ]
            _send_one(self.failed)
        self.assertEqual(send.call_count, 3)
        self.failed.refresh_from_db()
        self.assertEqual(self.failed.status, MSG_SENT)
        self.assertEqual(self.failed.wa_message_id, "SMRETRY1")


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


@override_settings(DARAJA_CALLBACK_BASE_URL="https://pay.myshop.test")
class CreditSaleNotificationTests(TestCase):
    def setUp(self):
        from datetime import date
        from decimal import Decimal

        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from shops.models import (
            Client,
            Shop,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptLine,
            ShopReceiptStatus,
        )
        from shops.services import set_communications_setting, update_twilio_settings

        self.user = User.objects.create_user(
            username="860021",
            password="wa-credit",
            email="wa-credit@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860021",
            phone_country_code="+254",
            phone_number="700000971",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="CREDIT WA SHOP",
            location="NAIROBI",
            email="credit-wa@test.local",
            phone_number="0700000971",
            login_code="860121",
            password_hash="x",
            created_by=self.it,
        )
        self.customer = Client.objects.create(
            full_name="KIM OTIENO",
            phone_number="+254795606115",
            phone_normalized="254795606115",
            created_by=self.it,
        )
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="CR-WA-1",
            kind=ShopReceiptKind.CREDIT,
            total=Decimal("250.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.it,
            client=self.customer,
            client_name="KIM OTIENO",
            client_phone="+254795606115",
            credit_due_date=date(2026, 9, 1),
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceiptLine.objects.create(
            receipt=self.receipt,
            item_name="USB CABLE",
            quantity=2,
            unit_price=Decimal("125.00"),
            line_total=Decimal("250.00"),
        )
        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="whatsapp:+14155238886",
        )
        set_communications_setting(field="enable_automations", enabled=True)
        set_communications_setting(field="auto_payment_reminder", enabled=True)

    def tearDown(self):
        from shops.services import _invalidate_communications_settings_cache

        _invalidate_communications_settings_cache()
        super().tearDown()

    def test_credit_notice_text(self):
        from communications.automations import build_credit_sale_notification
        from shops.credit_note import client_credit_note_url, unsign_client_credit_token

        body = build_credit_sale_notification(self.receipt)
        self.assertIn("Hi Kim,", body)
        self.assertIn("Credit sale at CREDIT WA SHOP.", body)
        self.assertIn("USB CABLE x2", body)
        self.assertIn("Total due: KSh 250", body)
        self.assertIn("Pay by: 01 Sep 2026", body)
        self.assertIn("Receipt CR-WA-1", body)
        pay_url = client_credit_note_url(self.customer.pk)
        self.assertTrue(pay_url.startswith("https://pay.myshop.test/credit-note/"))
        self.assertIn("You can view your credit account and pay with M-Pesa using this link:", body)
        self.assertIn(pay_url, body)
        self.assertIn("https://", body)
        token = pay_url.rstrip("/").rsplit("/", 1)[-1]
        self.assertEqual(unsign_client_credit_token(token), self.customer.pk)

    def test_credit_notice_omits_pay_link_without_client(self):
        from communications.automations import build_credit_sale_notification

        self.receipt.client = None
        self.receipt.save(update_fields=["client"])
        body = build_credit_sale_notification(self.receipt)
        self.assertNotIn("/credit-note/", body)
        self.assertNotIn("view your credit account", body)

    def test_credit_checkout_sends_whatsapp(self):
        from unittest.mock import patch

        from communications.automations import maybe_send_receipt_share

        with (
            patch("communications.automations.threading.Thread", ImmediateThread),
            patch("communications.twilio.send_whatsapp_message") as send,
        ):
            send.return_value = {"ok": True, "messageId": "SMCREDIT1"}
            maybe_send_receipt_share(self.receipt)

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["phone"], "+254795606115")
        self.assertIn("Credit sale at CREDIT WA SHOP.", kwargs["text"])
        self.assertIn("USB CABLE x2", kwargs["text"])
        self.assertIn("https://pay.myshop.test/credit-note/", kwargs["text"])
        self.assertIn("You can view your credit account and pay with M-Pesa using this link:", kwargs["text"])

    def test_credit_send_skipped_when_toggle_off(self):
        from unittest.mock import patch

        from communications.automations import maybe_send_receipt_share
        from shops.services import set_communications_setting

        set_communications_setting(field="auto_payment_reminder", enabled=False)
        with (
            patch("communications.automations.threading.Thread", ImmediateThread),
            patch("communications.twilio.send_whatsapp_message") as send,
        ):
            maybe_send_receipt_share(self.receipt)
        send.assert_not_called()

    def test_credit_whatsapp_required_follows_toggles(self):
        from communications.automations import credit_whatsapp_required
        from shops.services import set_communications_setting

        self.assertTrue(credit_whatsapp_required())
        set_communications_setting(field="auto_payment_reminder", enabled=False)
        self.assertFalse(credit_whatsapp_required())


@override_settings(DARAJA_CALLBACK_BASE_URL="https://pay.myshop.test")
class CreditNotePublicPayTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from shops.models import (
            Client,
            Shop,
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptStatus,
        )

        self.user = User.objects.create_user(
            username="860031",
            password="cn-pay",
            email="cn-pay@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860031",
            phone_country_code="+254",
            phone_number="700000981",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="CREDIT NOTE SHOP",
            location="NAIROBI",
            email="cn-shop@test.local",
            phone_number="0700000981",
            login_code="860131",
            password_hash="x",
            created_by=self.it,
        )
        self.customer = Client.objects.create(
            full_name="KIM OTIENO",
            phone_number="+254795606115",
            phone_normalized="254795606115",
            created_by=self.it,
        )
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="CN-PAY-1",
            kind=ShopReceiptKind.CREDIT,
            total=Decimal("250.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.it,
            client=self.customer,
            client_name="KIM OTIENO",
            client_phone="+254795606115",
            status=ShopReceiptStatus.ACTIVE,
        )
        self._enable_stk()

    def tearDown(self):
        from shops.services import _invalidate_daraja_settings_cache

        _invalidate_daraja_settings_cache()
        super().tearDown()

    def _enable_stk(self):
        from shops.services import _invalidate_daraja_settings_cache, get_daraja_settings

        row = get_daraja_settings()
        row.enable_stk_push = True
        row.credentials_valid = True
        row.consumer_key = "test-key"
        row.consumer_secret = "test-secret"
        row.passkey = "test-pass"
        row.shortcode = "174379"
        row.callback_base_url = "https://pay.myshop.test"
        row.save()
        _invalidate_daraja_settings_cache()

    def test_signed_link_opens_client_credit_account(self):
        from shops.credit_note import client_credit_note_path, client_credit_note_url

        url = client_credit_note_url(self.customer.pk)
        self.assertTrue(url.startswith("https://pay.myshop.test/credit-note/"))
        response = self.client.get(client_credit_note_path(self.customer.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your credit account")
        self.assertContains(response, "KIM OTIENO")
        self.assertContains(response, "data-stk-initiate-url")
        self.assertContains(response, "Pay")

    def test_pay_without_stk_does_not_update_balance(self):
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from shops.credit_note import apply_client_credit_note_payment

        with self.assertRaises(ValidationError):
            apply_client_credit_note_payment(
                client_id=self.customer.pk,
                amount="250",
                phone="+254795606115",
            )
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.amount_paid, Decimal("0.00"))

    def test_pending_stk_does_not_update_balance(self):
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from shops.credit_note import apply_client_credit_note_payment
        from shops.models import MpesaStkPayment, MpesaStkPurpose, MpesaStkStatus

        pending = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.CREDIT,
            status=MpesaStkStatus.PENDING,
            amount=Decimal("250.00"),
            phone="254795606115",
            account_kind="credit",
            account_id=self.customer.pk,
        )
        with self.assertRaises(ValidationError):
            apply_client_credit_note_payment(
                client_id=self.customer.pk,
                amount="250",
                phone="+254795606115",
                stk_payment_id=pending.public_id,
            )
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.amount_paid, Decimal("0.00"))

    def test_successful_stk_updates_balance(self):
        from decimal import Decimal

        from shops.credit_note import apply_client_credit_note_payment
        from shops.models import MpesaStkPayment, MpesaStkPurpose, MpesaStkStatus

        success = MpesaStkPayment.objects.create(
            purpose=MpesaStkPurpose.CREDIT,
            status=MpesaStkStatus.SUCCESS,
            amount=Decimal("250.00"),
            phone="254795606115",
            account_kind="credit",
            account_id=self.customer.pk,
            mpesa_receipt_number="RHLTEST1",
        )
        result = apply_client_credit_note_payment(
            client_id=self.customer.pk,
            amount="250",
            phone="+254795606115",
            stk_payment_id=success.public_id,
        )
        self.assertTrue(result["ok"])
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.amount_paid, Decimal("250.00"))
        success.refresh_from_db()
        self.assertTrue(success.applied)


class SupplierAutomationTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from items.models import (
            Item,
            StockEntrySource,
            StockMovement,
            StockMovementLine,
            StockMovementType,
            StockPaymentStatus,
        )
        from shops.models import (
            Expense,
            ExpenseCategory,
            ExpensePaymentStatus,
            Shop,
        )
        from shops.services import set_communications_setting, update_twilio_settings

        self.user = User.objects.create_user(
            username="860041",
            password="wa-sup",
            email="wa-sup@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860041",
            phone_country_code="+254",
            phone_number="700000991",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="SUPPLIER WA SHOP",
            location="NAIROBI",
            email="sup-wa@test.local",
            phone_number="0700000991",
            login_code="860141",
            password_hash="x",
            created_by=self.it,
        )
        self.item = Item.objects.create(
            name="RICE 5KG",
            category="GROCERY",
            description="Supplier automation fixture",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("25.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.it,
        )
        self.movement = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            entry_source=StockEntrySource.BUY_ITEMS,
            shop=self.shop,
            created_by=self.it,
        )
        StockMovementLine.objects.create(
            movement=self.movement,
            item=self.item,
            quantity=2,
            buying_price=Decimal("400.00"),
            payment_status=StockPaymentStatus.PAID,
            supplier_name="KIM SUPPLIES",
            supplier_phone_country_code="+254",
            supplier_phone_number="795606115",
        )
        self.expense = Expense.objects.create(
            shop=self.shop,
            category=ExpenseCategory.RENT,
            name="SHOP RENT",
            amount=Decimal("5000.00"),
            amount_paid=Decimal("0.00"),
            payment_status=ExpensePaymentStatus.UNPAID,
            supplier_name="KIM LANDLORD",
            supplier_phone_country_code="+254",
            supplier_phone_number="712345678",
            created_by=self.it,
        )
        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="whatsapp:+14155238886",
        )
        set_communications_setting(field="enable_automations", enabled=True)
        set_communications_setting(field="auto_stock_supplier", enabled=True)
        set_communications_setting(field="auto_expense_supplier", enabled=True)

    def tearDown(self):
        from shops.services import _invalidate_communications_settings_cache

        _invalidate_communications_settings_cache()
        super().tearDown()

    def test_stock_supplier_notice_text(self):
        from communications.automations import build_stock_supplier_notification

        body = build_stock_supplier_notification(self.movement)
        self.assertIn("Hi Kim,", body)
        self.assertIn("We received stock at SUPPLIER WA SHOP.", body)
        self.assertIn("RICE 5KG x2", body)
        self.assertIn("Total: KSh 800", body)
        self.assertIn("Payment: Paid", body)
        self.assertIn("Ref I", body)

    def test_expense_supplier_notice_text(self):
        from communications.automations import build_expense_supplier_notification

        body = build_expense_supplier_notification([self.expense])
        self.assertIn("Hi Kim,", body)
        self.assertIn("We recorded an expense at SUPPLIER WA SHOP.", body)
        self.assertIn("SHOP RENT", body)
        self.assertIn("Total: KSh 5,000", body)
        self.assertIn("Payment: Unpaid", body)
        self.assertIn("Ref E", body)

    def test_buy_stock_sends_whatsapp(self):
        from unittest.mock import patch

        from communications.automations import maybe_send_stock_supplier_notice

        with (
            patch("communications.automations.threading.Thread", ImmediateThread),
            patch("communications.twilio.send_whatsapp_message") as send,
        ):
            send.return_value = {"ok": True, "messageId": "SMSTOCK1"}
            maybe_send_stock_supplier_notice(self.movement)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["phone"], "+254795606115")
        self.assertIn("We received stock at SUPPLIER WA SHOP.", send.call_args.kwargs["text"])

    def test_register_expense_sends_whatsapp(self):
        from unittest.mock import patch

        from communications.automations import maybe_send_expense_supplier_notice

        with (
            patch("communications.automations.threading.Thread", ImmediateThread),
            patch("communications.twilio.send_whatsapp_message") as send,
        ):
            send.return_value = {"ok": True, "messageId": "SMEXP1"}
            maybe_send_expense_supplier_notice(expense=self.expense)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["phone"], "+254712345678")
        self.assertIn("We recorded an expense at SUPPLIER WA SHOP.", send.call_args.kwargs["text"])

    def test_stock_send_skipped_when_toggle_off(self):
        from unittest.mock import patch

        from communications.automations import maybe_send_stock_supplier_notice
        from shops.services import set_communications_setting

        set_communications_setting(field="auto_stock_supplier", enabled=False)
        with (
            patch("communications.automations.threading.Thread", ImmediateThread),
            patch("communications.twilio.send_whatsapp_message") as send,
        ):
            maybe_send_stock_supplier_notice(self.movement)
        send.assert_not_called()

    def test_warehouse_stock_in_does_not_send(self):
        from unittest.mock import patch

        from communications.automations import maybe_send_stock_supplier_notice
        from items.models import StockEntrySource

        self.movement.entry_source = StockEntrySource.STOCK_MANAGEMENT
        self.movement.save(update_fields=["entry_source"])
        with (
            patch("communications.automations.threading.Thread", ImmediateThread),
            patch("communications.twilio.send_whatsapp_message") as send,
        ):
            maybe_send_stock_supplier_notice(self.movement)
        send.assert_not_called()


class WhatsAppInboxTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
        from shops.models import Client

        self.user = User.objects.create_user(
            username="860088",
            password="wa-inbox",
            email="wa-inbox@test.local",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860088",
            phone_country_code="+254",
            phone_number="700000888",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.customer = Client.objects.create(
            full_name="JANE DOE",
            phone_number="0712345678",
            phone_normalized="254712345678",
            created_by=self.it,
        )

    @override_settings(DEBUG=True)
    def test_webhook_stores_reply_and_inbox_shows_it(self):
        from communications.models import InboundReply

        response = self.client.post(
            "/twilio/incoming/",
            {
                "MessageSid": "SM" + "d" * 32,
                "From": "whatsapp:+254712345678",
                "WaId": "254712345678",
                "Body": "I want the Pixel 9",
                "ProfileName": "Jane",
            },
        )
        self.assertEqual(response.status_code, 200)
        row = InboundReply.objects.get(wa_message_id="SM" + "d" * 32)
        self.assertEqual(row.phone, "254712345678")
        self.assertEqual(row.body, "I want the Pixel 9")
        self.assertEqual(row.client_id, self.customer.pk)

        self.client.force_login(self.user)
        page = self.client.get("/it-support/whatsapp/inbox/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "I want the Pixel 9")
        self.assertContains(page, "JANE DOE")
        self.assertContains(page, "Type a message")
        self.assertContains(page, "New chat")
        self.assertContains(page, 'data-wa-app')
        labels = [item["label"] for item in page.context["page_sidebar"]["primary"]]
        self.assertEqual(
            labels,
            ["Dashboard", "Communication settings", "Share items", "Contacts", "Inbox", "Activities"],
        )
        self.assertTrue(page.context["page_sidebar"]["primary"][4].get("active"))
        self.assertEqual(page.context["inbox"]["count"], 1)

    def test_join_messages_are_not_stored(self):
        from communications.models import InboundReply
        from communications.replies import record_inbound_reply

        self.assertIsNone(
            record_inbound_reply(
                message_sid="SM" + "e" * 32,
                from_value="whatsapp:+254712345678",
                body="join control-did",
            )
        )
        self.assertEqual(InboundReply.objects.count(), 0)

    def test_sync_pulls_inbound_from_twilio_list(self):
        from unittest.mock import patch

        from communications.models import InboundReply
        from communications.twilio import sync_inbound_replies

        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
        )

        class ListResponse:
            def read(self):
                return (
                    b'{"messages":[{"sid":"SM'
                    + b"f" * 32
                    + b'","direction":"inbound","from":"whatsapp:+254712345678",'
                    + b'"body":"Is this in stock?","date_sent":"Wed, 19 Aug 2026 08:00:00 +0000"}]}'
                )

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("communications.twilio.urlopen", return_value=ListResponse()):
            saved = sync_inbound_replies(force=True)
        self.assertEqual(saved, 1)
        row = InboundReply.objects.get(wa_message_id="SM" + "f" * 32)
        self.assertEqual(row.body, "Is this in stock?")
        self.assertEqual(row.client_id, self.customer.pk)

    def test_send_reply_joins_the_same_chat(self):
        from unittest.mock import patch

        from communications.models import InboundReply, OutboundMessage
        from communications.replies import record_inbound_reply
        from django.utils import timezone

        record_inbound_reply(
            message_sid="SM" + "d" * 32,
            from_value="whatsapp:+254712345678",
            wa_id="254712345678",
            body="Do you have stock?",
            created_at=timezone.now(),
        )
        update_twilio_settings(
            account_sid="ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            auth_token="secret-token",
            from_number="+14155552671",
            whatsapp_from="whatsapp:+14155238886",
        )
        self.client.force_login(self.user)
        with patch("communications.twilio.send_whatsapp_message") as send:
            send.return_value = {
                "ok": True,
                "messageId": "SM" + "a" * 32,
                "chatId": "whatsapp:+254712345678",
                "status": "queued",
            }
            response = self.client.post(
                "/it-support/whatsapp/api/inbox/",
                data='{"action":"send","phone":"254712345678","body":"Yes, it is in stock"}',
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content.decode()[:500])
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        bodies = [msg["body"] for msg in payload["threads"][0]["messages"]]
        self.assertIn("Do you have stock?", bodies)
        self.assertIn("Yes, it is in stock", bodies)
        self.assertTrue(
            OutboundMessage.objects.filter(body="Yes, it is in stock").exists()
        )
        self.assertEqual(InboundReply.objects.count(), 1)

