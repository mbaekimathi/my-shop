"""Shelf mode: separate shelf stock registered and adjusted by hand."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import (
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)
from items.models import Item, ShelfItem, ShelfStockMovement, ShopStock
from shops.models import CompanyPosSettings, Shop, ShopPosSettings
from shops.services import (
    _invalidate_pos_settings_cache,
    get_effective_pos_settings,
    pos_settings_as_dict,
    set_company_pos_setting,
)


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class ShelfStockTests(TestCase):
    def setUp(self):
        self.password = "shelf-pass"
        user = User.objects.create_user(
            username="800031", password=self.password, is_active=True
        )
        self.profile = EmployeeProfile.objects.create(
            user=user,
            employee_id="800031",
            phone_country_code="+254",
            phone_number="700000831",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="SHELF SHOP",
            location="NAIROBI",
            email="shelf-shop@test.local",
            phone_number="0700000831",
            login_code="800331",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        CompanyPosSettings.objects.update_or_create(
            pk=1, defaults={"enable_shelf_mode": False}
        )
        ShopPosSettings.objects.filter(shop=self.shop).delete()
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        self.staff = {"login_code": self.profile.employee_id}
        self.url = reverse(
            "employees:my_shop_shelf_stock", kwargs={"shop_id": self.shop.pk}
        )

    def _login(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_shelf_mode_defaults_off_and_toggles(self):
        self.assertFalse(pos_settings_as_dict()["enable_shelf_mode"])
        set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        self.assertTrue(get_effective_pos_settings(self.shop).enable_shelf_mode)
        self.assertTrue(pos_settings_as_dict()["enable_shelf_mode"])

    def test_stock_mode_toggles_switch_each_other_off(self):
        from django.core.exceptions import ValidationError

        from shops.services import pos_toggle_enabled, stock_mode_of

        row = set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.assertFalse(row.enable_stock_tracking)
        self.assertTrue(row.enable_shelf_mode)
        self.assertEqual(stock_mode_of(row), "stock_mode_shelf")
        self.assertFalse(pos_toggle_enabled(row, "stock_mode_items"))

        row = set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.assertTrue(row.enable_stock_tracking and row.enable_shelf_mode)

        row = set_company_pos_setting(field="stock_mode_items", enabled=True)
        self.assertTrue(row.enable_stock_tracking)
        self.assertFalse(row.enable_shelf_mode)

        with self.assertRaises(ValidationError):
            set_company_pos_setting(field="stock_mode_items", enabled=False)
        row = set_company_pos_setting(field="stock_mode_shelf", enabled=False)
        self.assertEqual(stock_mode_of(row), "stock_mode_items")

    def test_page_redirects_when_shelf_mode_off(self):
        client = self._login()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("shelf-stock", response["Location"])

    def test_register_add_remove_and_archive(self):
        set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        client = self._login()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Register shelf stock")

        client.post(
            self.url,
            {"action": "register", "name": "Sugar bag", "unit": "bags", "opening_qty": "5", **self.staff},
        )
        shelf = ShelfItem.objects.get(shop=self.shop, name="SUGAR BAG")
        self.assertEqual(shelf.quantity, Decimal("5"))

        client.post(self.url, {"action": "in", "shelf_item_id": shelf.pk, "qty": "3", **self.staff})
        client.post(self.url, {"action": "out", "shelf_item_id": shelf.pk, "qty": "2", **self.staff})
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("6"))

        client.post(self.url, {"action": "out", "shelf_item_id": shelf.pk, "qty": "100", **self.staff})
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("6"))
        self.assertEqual(
            list(
                ShelfStockMovement.objects.filter(shelf_item=shelf)
                .order_by("id")
                .values_list("movement_type", flat=True)
            ),
            ["register", "in", "out"],
        )

        response = client.post(
            self.url, {"action": "register", "name": "sugar BAG", **self.staff}, follow=True
        )
        self.assertContains(response, "already on the shelf list")

        client.post(self.url, {"action": "archive", "shelf_item_id": shelf.pk, **self.staff})
        shelf.refresh_from_db()
        self.assertTrue(shelf.is_archived)
        response = client.get(self.url)
        self.assertContains(response, "No shelf stock yet")

    def test_register_stores_uppercase_name_category_and_unit(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        client = self._login()
        client.post(
            self.url,
            {
                "action": "register",
                "name": "sugar bag",
                "category": "groceries",
                "unit": "bags",
                "opening_qty": "5",
                **self.staff,
            },
        )
        shelf = ShelfItem.objects.get(shop=self.shop)
        self.assertEqual(shelf.name, "SUGAR BAG")
        self.assertEqual(shelf.category, "GROCERIES")
        self.assertEqual(shelf.unit, "BAGS")

    def test_register_via_stock_management_post_covers_multiple_shops(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        admin_user = User.objects.create_user(
            username="800991", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800991",
            phone_country_code="+254",
            phone_number="700000991",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        other = Shop.objects.create(
            name="SECOND SHELF SHOP",
            location="NAIROBI",
            email="second-shelf@test.local",
            phone_number="0700000955",
            login_code="830955",
            password_hash="x",
            created_by=admin,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        response = self.client.post(
            stock_management_url(admin.role, "view"),
            {
                "shelf_action": "register",
                "shelf_shop_id": [str(self.shop.pk), str(other.pk)],
                "name": "gunny bags",
                "category": "packaging",
                "unit": "BAGS",
                "opening_qty": "7",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ShelfItem.objects.filter(
                name="GUNNY BAGS", category="PACKAGING", quantity=Decimal("7")
            ).count(),
            2,
        )

    def test_portal_changes_require_permitted_staff_id(self):
        set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        client = self._login()
        client.post(self.url, {"action": "register", "name": "No code"})
        client.post(
            self.url, {"action": "register", "name": "Bad code", "login_code": "999999"}
        )
        EmployeeModulePermission.objects.create(
            employee=self.profile,
            module_slug="my-shop",
            submodule_slug="shelf_stock",
            allowed=False,
        )
        client.post(self.url, {"action": "register", "name": "Denied", **self.staff})
        self.assertFalse(ShelfItem.objects.filter(shop=self.shop).exists())

        EmployeeModulePermission.objects.filter(employee=self.profile).update(allowed=True)
        client.post(self.url, {"action": "register", "name": "Allowed", **self.staff})
        shelf = ShelfItem.objects.get(shop=self.shop)
        self.assertEqual(shelf.name, "ALLOWED")
        self.assertEqual(shelf.created_by, self.profile)

    def test_shelf_items_do_not_touch_item_stock(self):
        set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        item = Item.objects.create(
            category="GEN",
            name="Sugar bag",
            minimum_selling_price=Decimal("10"),
            shop_price=Decimal("20"),
            created_by=self.profile,
        )
        ShopStock.objects.create(shop=self.shop, item=item, quantity=Decimal("4"))
        client = self._login()
        client.post(self.url, {"action": "register", "name": "Sugar bag", "opening_qty": "9", **self.staff})
        client.post(
            self.url,
            {
                "action": "in",
                "shelf_item_id": ShelfItem.objects.get(shop=self.shop).pk,
                "qty": "1",
                **self.staff,
            },
        )
        self.assertEqual(
            ShopStock.objects.get(shop=self.shop, item=item).quantity, Decimal("4")
        )

    def test_shop_override_cannot_change_stock_mode(self):
        from django.core.exceptions import ValidationError

        from shops.services import set_shop_pos_setting

        ShopPosSettings.objects.update_or_create(
            shop=self.shop,
            defaults={
                "override_pos": True,
                "enable_shelf_mode": True,
                "enable_open_close": False,
            },
        )
        _invalidate_pos_settings_cache()
        self.assertFalse(get_effective_pos_settings(self.shop).enable_shelf_mode)
        client = self._login()
        workspace = client.get(
            reverse("employees:my_shop_workspace", kwargs={"shop_id": self.shop.pk})
        )
        self.assertNotContains(workspace, self.url)
        self.assertNotEqual(client.get(self.url).status_code, 200)
        for field in ("stock_mode_shelf", "enable_shelf_mode", "enable_stock_tracking"):
            with self.assertRaises(ValidationError):
                set_shop_pos_setting(shop=self.shop, field=field, enabled=True)

    def test_floor_catalog_matches_stock_mode(self):
        catalog_url = reverse(
            "employees:my_shop_catalog", kwargs={"shop_id": self.shop.pk}
        )
        Item.objects.create(
            category="GEN",
            name="Rice 5kg",
            minimum_selling_price=Decimal("10"),
            shop_price=Decimal("20"),
            created_by=self.profile,
        )
        client = self._login()

        # Items mode (default): catalog lists items, no shelf rows.
        payload = client.get(catalog_url).json()
        self.assertTrue(payload["ok"])
        self.assertEqual([row["name"] for row in payload["items"]], ["Rice 5kg"])

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        client.post(
            self.url,
            {
                "action": "register",
                "name": "Sugar bag",
                "opening_qty": "5",
                **self.staff,
            },
        )

        # Shelf mode: catalog items still sell on the floor — only the
        # registered shelf item is excluded (it is never sold).
        payload = client.get(catalog_url).json()
        self.assertEqual(
            [row["name"] for row in payload["items"]], ["Rice 5kg"]
        )
        self.assertEqual(payload["total"], 1)

        # Both mode: only the sellable catalog items show on the floor.
        set_company_pos_setting(field="stock_mode_both", enabled=True)
        payload = client.get(catalog_url).json()
        self.assertEqual([row["name"] for row in payload["items"]], ["Rice 5kg"])
        self.assertEqual(payload["total"], 1)

        # Search hits catalog items only — shelf items are never returned.
        payload = client.get(catalog_url, {"q": "sugar"}).json()
        self.assertEqual(payload["items"], [])
        payload = client.get(catalog_url, {"q": "rice"}).json()
        self.assertEqual([row["name"] for row in payload["items"]], ["Rice 5kg"])


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class StoreManagerShelfStockTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            username="830041", password="store-pass", is_active=True
        )
        self.manager = EmployeeProfile.objects.create(
            user=user,
            employee_id="830041",
            phone_country_code="+254",
            phone_number="700000941",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.STORE_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="STORE SHELF SHOP",
            location="NAIROBI",
            email="store-shelf@test.local",
            phone_number="0700000941",
            login_code="830141",
            password_hash="x",
            created_by=self.manager,
        )
        CompanyPosSettings.objects.update_or_create(
            pk=1, defaults={"enable_shelf_mode": False, "enable_stock_tracking": True}
        )
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        self.client.login(username="830041", password="store-pass")
        self.url = reverse("employees:store_shelf_stock")

    def test_store_manager_registers_shelf_items_for_a_shop(self):
        response = self.client.get("/store-manager/")
        self.assertNotContains(response, self.url)
        self.assertContains(self.client.get(self.url), "Shelf mode is off")

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.assertContains(self.client.get("/store-manager/"), self.url)
        page = self.client.get(self.url)
        self.assertContains(page, "STORE SHELF SHOP")

        self.client.post(
            f"{self.url}?shop={self.shop.pk}",
            {"action": "register", "name": "Gunny bags", "opening_qty": "20"},
        )
        shelf = ShelfItem.objects.get(shop=self.shop)
        self.assertEqual(shelf.quantity, Decimal("20"))
        self.assertEqual(shelf.created_by, self.manager)
        self.assertFalse(Item.objects.filter(name__iexact="Gunny bags").exists())

        self.client.post(
            self.url,
            {"action": "out", "shelf_item_id": shelf.pk, "qty": "5", "shop": self.shop.pk},
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("15"))

    def test_post_rejected_when_company_shelf_mode_off(self):
        other = Shop.objects.create(
            name="OTHER SHELF SHOP",
            location="NAIROBI",
            email="other-shelf@test.local",
            phone_number="0700000942",
            login_code="830142",
            password_hash="x",
            created_by=self.manager,
        )
        self.client.post(
            self.url, {"action": "register", "name": "Wrong", "shop": self.shop.pk}
        )
        self.assertFalse(ShelfItem.objects.exists())
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.client.post(self.url, {"action": "register", "name": "Right", "shop": other.pk})
        self.assertEqual(ShelfItem.objects.get().shop, other)
