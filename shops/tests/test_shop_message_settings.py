from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from communications.constants import MESSAGE_STATUS_ACTIVE, TRIGGER_SALE
from communications.models import TriggeredMessage
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop, ShopPosSettings
from shops.session import SESSION_SHOP_PORTAL_SHOP_KEY


class ShopMessageSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="800077",
            password="pw",
            email="shop-msg@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800077",
            phone_country_code="+254",
            phone_number="700000877",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="MSG SHOP",
            location="NAIROBI",
            email="msg-shop@test.local",
            phone_number="0700000877",
            login_code="800177",
            password_hash=make_password("shop-pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)

    def _portal_client(self):
        client = Client()
        session = client.session
        session[SESSION_SHOP_PORTAL_SHOP_KEY] = str(self.shop.pk)
        session["shop_portal_auth"] = True
        session.save()
        return client

    def _hub_url(self):
        return reverse(
            "employees:my_shop_settings", kwargs={"shop_id": self.shop.pk}
        )

    def _page_url(self):
        return reverse(
            "employees:my_shop_settings_messages",
            kwargs={"shop_id": self.shop.pk},
        )

    def test_settings_hub_links_to_messages_page(self):
        response = self._portal_client().get(self._hub_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._page_url())

    def test_messages_page_renders_company_drafts_when_override_off(self):
        TriggeredMessage.objects.create(
            trigger=TRIGGER_SALE,
            body="COMPANY body",
            status=MESSAGE_STATUS_ACTIVE,
        )
        response = self._portal_client().get(self._page_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Company messages")
        self.assertContains(response, "COMPANY body")
        self.assertFalse(
            ShopPosSettings.objects.filter(
                shop=self.shop, override_messages=True
            ).exists()
        )

    def test_enable_override_seeds_and_unlocks_shop_drafts(self):
        TriggeredMessage.objects.create(
            trigger=TRIGGER_SALE,
            body="COMPANY body",
            status=MESSAGE_STATUS_ACTIVE,
        )
        client = self._portal_client()
        response = client.post(
            self._page_url(),
            {"action": "set_shop_override", "enabled": "1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("override_enabled"))
        row = ShopPosSettings.objects.get(shop=self.shop)
        self.assertTrue(row.override_messages)
        self.assertTrue(
            TriggeredMessage.objects.filter(
                shop=self.shop, body="COMPANY body"
            ).exists()
        )

    def test_save_draft_blocked_while_override_off(self):
        response = self._portal_client().post(
            self._page_url(),
            {
                "action": "save_draft",
                "trigger": TRIGGER_SALE,
                "send_timing": "now",
                "body": "SHOP body",
                "status": MESSAGE_STATUS_ACTIVE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TriggeredMessage.objects.filter(shop=self.shop).exists()
        )

    def test_save_draft_creates_shop_scoped_message_when_override_on(self):
        ShopPosSettings.objects.create(
            shop=self.shop, override_messages=True
        )
        response = self._portal_client().post(
            self._page_url(),
            {
                "action": "save_draft",
                "trigger": TRIGGER_SALE,
                "send_timing": "now",
                "body": "SHOP body",
                "status": MESSAGE_STATUS_ACTIVE,
            },
        )
        self.assertEqual(response.status_code, 302)
        draft = TriggeredMessage.objects.get(shop=self.shop)
        self.assertEqual(draft.body, "SHOP body")
        # Company page scope stays untouched.
        self.assertFalse(
            TriggeredMessage.objects.filter(shop__isnull=True).exists()
        )

    def test_messages_page_requires_portal_session(self):
        response = Client().get(self._page_url())
        self.assertNotEqual(response.status_code, 200)
