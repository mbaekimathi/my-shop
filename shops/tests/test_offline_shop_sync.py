"""Shop portal can replay queued floor sales via /employees/api/sync/."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ItemSerial, ShopStock
from shops.models import Shop, ShopReceipt


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ShopPortalOfflineSyncTests(TestCase):
    def setUp(self):
        self.password = "portal-sync-pass"
        self.user = User.objects.create_user(
            username="800001",
            password=self.password,
            email="portal-sync@test.local",
            first_name="PORTAL",
            last_name="CASHIER",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800001",
            phone_country_code="+254",
            phone_number="700000801",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="PORTAL SYNC SHOP",
            location="NAIROBI",
            email="portal-sync-shop@test.local",
            phone_number="0700000801",
            login_code="800101",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="PORTAL SYNC ITEM",
            category="SYNC",
            description="Offline portal checkout fixture",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("25.00"),
            stock=50,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.shop, item=self.item, quantity=50)
        self.serial_item = Item.objects.create(
            name="PORTAL SYNC PHONE",
            category="PHONES",
            description="Serial tracked offline fixture",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            stock=3,
            track_serial_number=True,
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.shop, item=self.serial_item, quantity=3)
        self.serials = ["SN800001AAA", "SN800001BBB", "SN800001CCC"]
        ItemSerial.objects.bulk_create(
            [
                ItemSerial(
                    item=self.serial_item,
                    shop=self.shop,
                    serial_number=serial,
                    is_available=True,
                )
                for serial in self.serials
            ]
        )

    def _checkout_payload(self, *, client_id=None, shop_id=None):
        return {
            "client_id": client_id or str(uuid.uuid4()),
            "shop_id": shop_id or self.shop.pk,
            "checkout": {
                "kind": "sale",
                "payment_method": "cash",
                "client_name": "PORTAL CLIENT",
                "client_phone": "0712345678",
                "login_code": self.profile.employee_id,
                "share_whatsapp": False,
                "lines": [
                    {
                        "id": self.item.pk,
                        "qty": 1,
                        "price": str(self.item.shop_price),
                        "serials": [],
                    }
                ],
            },
        }

    def _portal_client(self):
        client = Client()
        session = client.session
        session["active_shop_id"] = str(self.shop.pk)
        session["shop_portal_auth"] = True
        session.save()
        return client

    def _post_sync(self, client, operations):
        return client.post(
            "/employees/api/sync/",
            data=json.dumps({"operations": operations}),
            content_type="application/json",
        )

    def test_anonymous_sync_returns_json_403(self):
        response = self._post_sync(
            Client(),
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "complete_shop_checkout",
                    "payload": self._checkout_payload(),
                }
            ],
        )
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error"), "auth_required")

    def test_shop_portal_syncs_queued_checkout(self):
        client = self._portal_client()
        client_id = str(uuid.uuid4())
        before = ShopReceipt.objects.filter(shop=self.shop).count()
        response = self._post_sync(
            client,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "complete_shop_checkout",
                    "payload": self._checkout_payload(client_id=client_id),
                }
            ],
        )
        self.assertIn(response.status_code, (200, 207))
        data = response.json()
        self.assertEqual(data.get("failed"), 0)
        self.assertTrue(data.get("results", [{}])[0].get("ok"))
        self.assertEqual(ShopReceipt.objects.filter(shop=self.shop).count(), before + 1)

        replay = self._post_sync(
            client,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "complete_shop_checkout",
                    "payload": self._checkout_payload(client_id=client_id),
                }
            ],
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json().get("failed"), 0)
        self.assertEqual(ShopReceipt.objects.filter(shop=self.shop).count(), before + 1)

    def test_shop_portal_rejects_non_checkout_ops(self):
        response = self._post_sync(
            self._portal_client(),
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "create_sale",
                    "payload": {"client_id": str(uuid.uuid4())},
                }
            ],
        )
        self.assertEqual(response.status_code, 207)
        data = response.json()
        self.assertEqual(data.get("failed"), 1)
        self.assertEqual(data["results"][0].get("error"), "unsupported_type")

    def test_verify_login_code_accepts_active_cashier(self):
        response = self._portal_client().post(
            reverse("employees:my_shop_verify_login_code", kwargs={"shop_id": self.shop.pk}),
            {"login_code": self.profile.employee_id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("employee_id"), self.profile.employee_id)
        self.assertIn("PORTAL", data.get("name") or "")

    def test_verify_login_code_rejects_invalid_and_inactive(self):
        client = self._portal_client()
        url = reverse(
            "employees:my_shop_verify_login_code", kwargs={"shop_id": self.shop.pk}
        )
        bad = client.post(url, {"login_code": "000000"})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json().get("ok"))

        self.profile.status = EmployeeStatus.SUSPENDED
        self.profile.save(update_fields=["status", "updated_at"])
        suspended = client.post(url, {"login_code": self.profile.employee_id})
        self.assertEqual(suspended.status_code, 400)
        self.assertFalse(suspended.json().get("ok"))

    def test_portal_online_checkout_verifies_and_completes_sale(self):
        client = self._portal_client()
        response = client.post(
            reverse("employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}),
            data=json.dumps(
                {
                    "kind": "sale",
                    "payment_method": "cash",
                    "client_name": "WALK IN",
                    "client_phone": "0712345678",
                    "login_code": self.profile.employee_id,
                    "share_whatsapp": False,
                    "lines": [
                        {
                            "id": self.item.pk,
                            "qty": 2,
                            "price": str(self.item.shop_price),
                            "serials": [],
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("receipt_number"))
        self.assertIn("PORTAL", data.get("authorised_by") or "")
        receipt = ShopReceipt.objects.get(receipt_number=data["receipt_number"])
        self.assertEqual(receipt.shop_id, self.shop.pk)
        self.assertEqual(receipt.created_by_id, self.profile.pk)
        self.assertEqual(receipt.total, Decimal("50.00"))
        stock = ShopStock.objects.get(shop=self.shop, item=self.item)
        self.assertEqual(stock.quantity, 48)

    def test_portal_online_checkout_rejects_bad_staff_code(self):
        response = self._portal_client().post(
            reverse("employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}),
            data=json.dumps(
                {
                    "kind": "sale",
                    "payment_method": "cash",
                    "login_code": "000000",
                    "lines": [
                        {
                            "id": self.item.pk,
                            "qty": 1,
                            "price": str(self.item.shop_price),
                            "serials": [],
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(response.json().get("ok"))
        self.assertEqual(ShopReceipt.objects.filter(shop=self.shop).count(), 0)

    def test_employee_session_can_sync_queued_checkout(self):
        client = Client()
        client.force_login(self.user)
        before = ShopReceipt.objects.filter(shop=self.shop).count()
        response = self._post_sync(
            client,
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "complete_shop_checkout",
                    "payload": self._checkout_payload(),
                }
            ],
        )
        self.assertIn(response.status_code, (200, 207))
        self.assertEqual(response.json().get("failed"), 0)
        self.assertEqual(ShopReceipt.objects.filter(shop=self.shop).count(), before + 1)

    def test_sync_rejects_invalid_employee_code(self):
        payload = self._checkout_payload()
        payload["checkout"]["login_code"] = "000000"
        response = self._post_sync(
            self._portal_client(),
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "complete_shop_checkout",
                    "payload": payload,
                }
            ],
        )
        self.assertEqual(response.status_code, 207)
        self.assertEqual(response.json().get("failed"), 1)
        self.assertIn("staff", (response.json()["results"][0].get("message") or "").lower())
        self.assertEqual(ShopReceipt.objects.filter(shop=self.shop).count(), 0)

    def test_portal_catalog_and_serial_search(self):
        client = self._portal_client()
        catalog = client.get(
            reverse("employees:my_shop_catalog", kwargs={"shop_id": self.shop.pk}),
            {"page": 1, "page_size": 24},
        )
        self.assertEqual(catalog.status_code, 200)
        catalog_data = catalog.json()
        self.assertTrue(catalog_data.get("ok"))
        names = {row.get("name") for row in catalog_data.get("items") or []}
        self.assertIn(self.item.name, names)

        search = client.get(
            reverse("employees:my_shop_serial_search", kwargs={"shop_id": self.shop.pk}),
            {
                "item_id": self.serial_item.pk,
                "q": "",
                "limit": 500,
            },
        )
        self.assertEqual(search.status_code, 200)
        serials = search.json().get("results") or []
        self.assertEqual(set(serials), set(self.serials))

        last4 = client.get(
            reverse("employees:my_shop_serial_search", kwargs={"shop_id": self.shop.pk}),
            {
                "item_id": self.serial_item.pk,
                "q": "AAA",
                "match": "last4",
            },
        )
        self.assertEqual(last4.status_code, 200)
        self.assertIn("SN800001AAA", last4.json().get("results") or [])

    def test_shop_portal_cannot_sync_another_shop(self):
        other = Shop.objects.create(
            name="OTHER SHOP",
            location="MOMBASA",
            email="other-sync@test.local",
            phone_number="0700000802",
            login_code="800102",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        response = self._post_sync(
            self._portal_client(),
            [
                {
                    "id": str(uuid.uuid4()),
                    "type": "complete_shop_checkout",
                    "payload": self._checkout_payload(shop_id=other.pk),
                }
            ],
        )
        self.assertEqual(response.status_code, 207)
        self.assertEqual(response.json()["results"][0].get("error"), "forbidden")
        self.assertEqual(ShopReceipt.objects.filter(shop=other).count(), 0)
