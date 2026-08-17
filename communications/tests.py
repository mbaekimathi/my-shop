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

    def test_page_shows_what_and_who(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/whatsapp/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What to send")
        self.assertContains(response, "Who to share with")
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
        self.assertContains(response, "JANE DOE")
        self.assertContains(response, "254712345678")
        self.assertContains(response, "Sent &amp; viewed")
        self.assertContains(response, "Cancel a send")
        self.assertContains(response, "data-wa-pick")
        self.assertContains(response, "Select all")
        self.assertContains(response, "Share items")
        self.assertContains(response, "/it-support/whatsapp/catalogue/")

    def test_save_audience(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/it-support/whatsapp/",
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
            "/it-support/whatsapp/",
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
                "/it-support/whatsapp/",
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
        home = self.client.get("/it-support/whatsapp/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Share items")
        self.assertContains(home, 'href="/it-support/whatsapp/catalogue/"')
        response = self.client.get("/it-support/whatsapp/catalogue/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pick items")
        self.assertContains(response, "PIXEL 9")
        self.assertContains(response, "KSh 85,000")
        self.assertContains(response, "New items")
        self.assertContains(response, "Send selected items now")
        self.assertContains(response, "data-wa-item-pick")
        self.assertContains(response, "JANE DOE")

    def test_caption_includes_name_and_price(self):
        from communications.automations import build_item_catalogue_caption

        text = build_item_catalogue_caption(self.item)
        self.assertIn("PIXEL 9", text)
        self.assertIn("PHONES", text)
        self.assertIn("KSh 85,000", text)
        self.assertIn("{first_name}", text)

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
        self.assertIn("PIXEL 9", message.body)
        self.assertIn("KSh 85,000", message.body)
        self.assertIn("Hi JANE", message.body)

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
        self.assertIn("PIXEL 9", body)
        self.assertIn("KSh 85,000", body)

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
            "/it-support/whatsapp/",
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
            "/it-support/whatsapp/",
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
                "/it-support/whatsapp/",
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

