"""Wrong-shop sale warning and unallocated shop floor access."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import Shop
from shops.session import SESSION_SHOP_KEY, profile_allocated_to_shop


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class WrongShopSaleTests(TestCase):
    def setUp(self):
        self.password = "wrong-shop-pass"
        self.user = User.objects.create_user(
            username="810001",
            password=self.password,
            email="wrong-shop@test.local",
            first_name="WRONG",
            last_name="CASHIER",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="810001",
            phone_country_code="+254",
            phone_number="700000901",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.allocated_shop = Shop.objects.create(
            name="ALLOCATED SHOP",
            location="NAIROBI",
            email="allocated@test.local",
            phone_number="0700000901",
            login_code="810101",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.other_shop = Shop.objects.create(
            name="OTHER SHOP",
            location="MOMBASA",
            email="other@test.local",
            phone_number="0700000902",
            login_code="810102",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.allocated_shop)
        self.item = Item.objects.create(
            name="WRONG SHOP ITEM",
            category="TEST",
            description="Fixture",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            stock=10,
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.other_shop, item=self.item, quantity=10)

    def _login(self, client: Client) -> None:
        client.login(username="810001", password=self.password)

    def _unlock_shop(self, client: Client, shop: Shop) -> None:
        session = client.session
        session[SESSION_SHOP_KEY] = str(shop.pk)
        session.save()

    def test_profile_allocated_to_shop_helper(self):
        self.assertTrue(
            profile_allocated_to_shop(self.profile, self.allocated_shop)
        )
        self.assertFalse(profile_allocated_to_shop(self.profile, self.other_shop))

    def test_unallocated_shop_workspace_access_after_unlock(self):
        client = Client()
        self._login(client)
        self._unlock_shop(client, self.other_shop)
        response = client.get(
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.other_shop.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-wrong-shop-modal")
        self.assertContains(response, "OTHER SHOP")

    def test_verify_login_code_reports_unallocated_staff(self):
        client = Client()
        self._login(client)
        self._unlock_shop(client, self.other_shop)
        response = client.post(
            reverse(
                "employees:my_shop_verify_login_code",
                kwargs={"shop_id": self.other_shop.pk},
            ),
            {"login_code": self.profile.employee_id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertFalse(data.get("allocated_to_shop"))
        self.assertEqual(data.get("shop_name"), "OTHER SHOP")
        self.assertEqual(data.get("allocated_shop_names"), ["ALLOCATED SHOP"])

    def test_verify_login_code_reports_allocated_staff(self):
        client = Client()
        self._login(client)
        self._unlock_shop(client, self.allocated_shop)
        response = client.post(
            reverse(
                "employees:my_shop_verify_login_code",
                kwargs={"shop_id": self.allocated_shop.pk},
            ),
            {"login_code": self.profile.employee_id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("allocated_to_shop"))
        self.assertEqual(data.get("allocated_shop_names"), [])

    def test_checkout_succeeds_at_unallocated_shop(self):
        client = Client()
        self._login(client)
        self._unlock_shop(client, self.other_shop)
        response = client.post(
            reverse(
                "employees:my_shop_checkout", kwargs={"shop_id": self.other_shop.pk}
            ),
            data={
                "kind": "sale",
                "payment_method": "cash",
                "client_name": "WALK IN",
                "login_code": self.profile.employee_id,
                "lines": [
                    {
                        "id": self.item.pk,
                        "qty": 1,
                        "price": str(self.item.shop_price),
                        "serials": [],
                    }
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json().get("ok"))
