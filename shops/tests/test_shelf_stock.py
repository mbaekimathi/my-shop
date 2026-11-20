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
from items.models import (
    Item,
    ShelfItem,
    ShelfStockMovement,
    ShopStock,
    StoreStock,
)
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
        from shops.services import _invalidate_stock_settings_cache

        _invalidate_stock_settings_cache()
        self.addCleanup(_invalidate_stock_settings_cache)
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

        client.post(self.url, {"action": "in", "shelf_item_id": shelf.pk, "qty": "3", "price": "7.50", **self.staff})
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

    def test_stock_in_by_name_via_stock_management(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        admin_user = User.objects.create_user(
            username="800992", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800992",
            phone_country_code="+254",
            phone_number="700000992",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        # A second shop keeps the single-shop filter from collapsing to
        # "all shops" (which would show no shelf panel).
        Shop.objects.create(
            name="OTHER SHELF SHOP",
            location="NAIROBI",
            email="other-shelf@test.local",
            phone_number="0700000956",
            login_code="830956",
            password_hash="x",
            created_by=admin,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        url = (
            f"{stock_management_url(admin.role, 'in', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-modal-open="shelf-stock"')

        response = self.client.post(
            url,
            {
                "shelf_action": "in_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "maize flour 2kg",
                "shelf_new_unit": "PACKETS",
                "shelf_new_qty": "4",
                "shelf_new_price": "120.50",
                "shelf_new_note": "first batch",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=shelf", response["Location"])
        shelf = ShelfItem.objects.get(shop=self.shop)
        self.assertEqual(shelf.name, "MAIZE FLOUR 2KG")
        self.assertEqual(shelf.unit, "PACKETS")
        self.assertEqual(shelf.quantity, Decimal("4"))
        self.assertEqual(
            list(
                ShelfStockMovement.objects.filter(shelf_item=shelf)
                .order_by("id")
                .values_list("movement_type", flat=True)
            ),
            ["register", "in"],
        )
        last = ShelfStockMovement.objects.filter(shelf_item=shelf).latest("id")
        self.assertEqual(last.buying_price, Decimal("120.50"))
        self.assertEqual(last.note, "first batch")

        # Buying price is compulsory on shelf stock-in by default — a post
        # without it is rejected and moves nothing.
        self.client.post(
            url,
            {
                "shelf_action": "in_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "MAIZE FLOUR 2KG",
                "shelf_new_qty": "2",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("4"))

        # An existing name stocks into the same item — no duplicate row.
        response = self.client.post(
            url,
            {
                "shelf_action": "in_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "MAIZE FLOUR 2KG",
                "shelf_new_qty": "2",
                "shelf_new_price": "115",
            },
        )
        self.assertEqual(response.status_code, 302)
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("6"))
        self.assertEqual(ShelfItem.objects.filter(shop=self.shop).count(), 1)

        # Missing name / quantity are rejected without creating anything.
        self.client.post(
            url,
            {
                "shelf_action": "in_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_qty": "2",
                "shelf_new_price": "50",
            },
        )
        self.client.post(
            url,
            {
                "shelf_action": "in_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "rice 1kg",
                "shelf_new_price": "50",
            },
        )
        self.assertEqual(
            ShelfItem.objects.filter(shop=self.shop).count(), 1
        )

    def test_shelf_tab_single_shop_still_shows_panel(self):
        """One shelf-mode shop: shop_id collapses to all-shops scope, but the
        shelf panel must still render on the stock-in tab."""
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        ShelfItem.objects.create(
            shop=self.shop, name="MAIZE FLOUR 2KG", unit="PACKETS",
            quantity=Decimal("5"), created_by=self.profile,
        )
        admin_user = User.objects.create_user(
            username="800993", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800993",
            phone_country_code="+254",
            phone_number="700000993",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        url = (
            f"{stock_management_url(admin.role, 'in', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-modal-open="shelf-stock"')
        self.assertNotContains(page, "data-search-gated")
        self.assertNotContains(page, "data-item-search-idle")
        self.assertNotContains(page, "Pick a shelf-mode shop above")

        # Rows follow the item stock-in editable matrix: one row per item,
        # one editable cell per shelf shop column, with the floating submit
        # card collecting the lines — no per-row Add/Remove buttons.
        self.assertContains(page, "data-item-category-group")
        self.assertContains(page, "stock-matrix--editable")
        self.assertContains(page, "data-shelf-cell")
        self.assertContains(page, "stock-list-input")
        self.assertContains(page, "data-shelf-float-submit")
        self.assertContains(page, "data-shelf-float-lines")
        self.assertNotContains(page, 'value="in:')
        self.assertNotContains(page, 'value="out:')

        # The stock-out tab lists items the same way; the current-stock
        # view still lists every shelf item.
        out_page = self.client.get(
            f"{stock_management_url(admin.role, 'out', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        self.assertContains(out_page, "data-shelf-cell")
        self.assertContains(out_page, "data-shelf-balance")
        self.assertContains(out_page, 'value="out_bulk"')
        view_page = self.client.get(
            f"{stock_management_url(admin.role, 'view', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        self.assertContains(view_page, "MAIZE FLOUR 2KG")

    def test_stock_out_by_name_via_stock_management(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        ShelfItem.objects.create(
            shop=self.shop, name="MAIZE FLOUR 2KG", unit="PACKETS",
            quantity=Decimal("5"), created_by=self.profile,
        )
        admin_user = User.objects.create_user(
            username="800994", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800994",
            phone_country_code="+254",
            phone_number="700000994",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        url = (
            f"{stock_management_url(admin.role, 'out', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-modal-open="shelf-stock"')

        shelf = ShelfItem.objects.get(shop=self.shop)
        response = self.client.post(
            url,
            {
                "shelf_action": "out_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "maize flour 2kg",
                "shelf_new_qty": "2",
                "shelf_new_note": "damaged",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=shelf", response["Location"])
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("3"))
        move = ShelfStockMovement.objects.filter(shelf_item=shelf).latest("id")
        self.assertEqual(move.movement_type, "out")
        self.assertEqual(move.note, "damaged")

        # Over-removing and unknown names are rejected without moving stock.
        self.client.post(
            url,
            {
                "shelf_action": "out_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "MAIZE FLOUR 2KG",
                "shelf_new_qty": "99",
            },
        )
        self.client.post(
            url,
            {
                "shelf_action": "out_new",
                "shelf_shop_id": str(self.shop.pk),
                "shelf_new_name": "rice 1kg",
                "shelf_new_qty": "1",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("3"))

    def test_bulk_stock_in_via_stock_management(self):
        """The Stock in entered button posts every filled row (and the
        quick-add strip) as one in_bulk action."""
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        item_a = ShelfItem.objects.create(
            shop=self.shop, name="MAIZE FLOUR 2KG", unit="PACKETS",
            quantity=Decimal("5"), created_by=self.profile,
        )
        item_b = ShelfItem.objects.create(
            shop=self.shop, name="RICE 1KG", unit="PACKETS",
            quantity=Decimal("3"), created_by=self.profile,
        )
        admin_user = User.objects.create_user(
            username="800996", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800996",
            phone_country_code="+254",
            phone_number="700000996",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        url = (
            f"{stock_management_url(admin.role, 'in', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        page = self.client.get(url)
        self.assertContains(page, 'value="in_bulk"')

        response = self.client.post(
            url,
            {
                "shelf_action": "in_bulk",
                "shelf_shop_id": str(self.shop.pk),
                f"shelf_qty_{item_a.pk}": "4",
                f"shelf_price_{item_a.pk}": "120",
                f"shelf_qty_{item_b.pk}": "2",
                f"shelf_price_{item_b.pk}": "80",
                "shelf_new_name": "sugar 500g",
                "shelf_new_unit": "PACKETS",
                "shelf_new_qty": "6",
                "shelf_new_price": "95",
            },
        )
        self.assertEqual(response.status_code, 302)
        item_a.refresh_from_db()
        item_b.refresh_from_db()
        self.assertEqual(item_a.quantity, Decimal("9"))
        self.assertEqual(item_b.quantity, Decimal("5"))
        new_item = ShelfItem.objects.get(shop=self.shop, name="SUGAR 500G")
        self.assertEqual(new_item.quantity, Decimal("6"))

        # A bad line fails on its own — the good lines still post.
        response = self.client.post(
            url,
            {
                "shelf_action": "in_bulk",
                "shelf_shop_id": str(self.shop.pk),
                f"shelf_qty_{item_a.pk}": "abc",
                f"shelf_price_{item_a.pk}": "120",
                f"shelf_qty_{item_b.pk}": "1",
                f"shelf_price_{item_b.pk}": "80",
            },
        )
        self.assertEqual(response.status_code, 302)
        item_a.refresh_from_db()
        item_b.refresh_from_db()
        self.assertEqual(item_a.quantity, Decimal("9"))
        self.assertEqual(item_b.quantity, Decimal("6"))

        # Nothing filled in is rejected without moving anything.
        self.client.post(
            url,
            {
                "shelf_action": "in_bulk",
                "shelf_shop_id": str(self.shop.pk),
            },
        )
        item_b.refresh_from_db()
        self.assertEqual(item_b.quantity, Decimal("6"))

    def test_bulk_stock_out_via_stock_management(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        item_a = ShelfItem.objects.create(
            shop=self.shop, name="MAIZE FLOUR 2KG", unit="PACKETS",
            quantity=Decimal("5"), created_by=self.profile,
        )
        item_b = ShelfItem.objects.create(
            shop=self.shop, name="RICE 1KG", unit="PACKETS",
            quantity=Decimal("3"), created_by=self.profile,
        )
        admin_user = User.objects.create_user(
            username="800997", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800997",
            phone_country_code="+254",
            phone_number="700000997",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        url = (
            f"{stock_management_url(admin.role, 'out', shop_id=self.shop.pk)}"
            "&tab=shelf"
        )
        page = self.client.get(url)
        self.assertContains(page, 'value="out_bulk"')

        response = self.client.post(
            url,
            {
                "shelf_action": "out_bulk",
                "shelf_shop_id": str(self.shop.pk),
                f"shelf_qty_{item_a.pk}": "2",
                f"shelf_qty_{item_b.pk}": "9",
            },
        )
        self.assertEqual(response.status_code, 302)
        item_a.refresh_from_db()
        item_b.refresh_from_db()
        # item_b over-removes — its line errors, item_a still applies.
        self.assertEqual(item_a.quantity, Decimal("3"))
        self.assertEqual(item_b.quantity, Decimal("3"))

    def test_shelf_tab_all_shops_lists_and_posts_across_shops(self):
        """With All shops selected, the shelf in tab lists every shelf-mode
        shop's items — each row carries its own shop and bulk posts apply
        per line."""
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        admin_user = User.objects.create_user(
            username="800998", password="admin-pass", is_active=True
        )
        admin = EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="800998",
            phone_country_code="+254",
            phone_number="700000998",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        other = Shop.objects.create(
            name="SECOND SHELF SHOP",
            location="NAIROBI",
            email="second-shelf2@test.local",
            phone_number="0700000958",
            login_code="830958",
            password_hash="x",
            created_by=admin,
        )
        item_a = ShelfItem.objects.create(
            shop=self.shop, name="SALT 1KG", unit="PACKETS",
            quantity=Decimal("5"), created_by=self.profile,
        )
        item_b = ShelfItem.objects.create(
            shop=other, name="SUGAR 2KG", unit="PACKETS",
            quantity=Decimal("4"), created_by=self.profile,
        )
        self.client.force_login(admin_user)
        from employees.workspace import stock_management_url

        url = f"{stock_management_url(admin.role, 'in')}&tab=shelf"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        # No shop pick needed — every shelf-mode shop's items are listed.
        self.assertContains(page, "SALT 1KG")
        self.assertContains(page, "SUGAR 2KG")
        self.assertContains(page, 'data-modal-open="shelf-stock"')
        self.assertContains(page, "data-shelf-float-submit")
        self.assertNotContains(page, "Pick a shelf-mode shop above")

        # One bulk post stocks into rows from different shops, and a posted
        # strip line still carries its own shop pick.
        response = self.client.post(
            url,
            {
                "shelf_action": "in_bulk",
                f"shelf_qty_{item_a.pk}": "2",
                f"shelf_price_{item_a.pk}": "40",
                f"shelf_qty_{item_b.pk}": "3",
                f"shelf_price_{item_b.pk}": "80",
                "shelf_new_shop_id": str(other.pk),
                "shelf_new_name": "honey 500g",
                "shelf_new_qty": "1",
                "shelf_new_price": "150",
            },
        )
        self.assertEqual(response.status_code, 302)
        item_a.refresh_from_db()
        item_b.refresh_from_db()
        self.assertEqual(item_a.quantity, Decimal("7"))
        self.assertEqual(item_b.quantity, Decimal("7"))
        self.assertTrue(
            ShelfItem.objects.filter(shop=other, name="HONEY 500G").exists()
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
                "price": "4.00",
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
            role=EmployeeRole.COMPANY_STORE_MANAGER,
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
        from shops.services import _invalidate_stock_settings_cache

        _invalidate_stock_settings_cache()
        self.addCleanup(_invalidate_stock_settings_cache)
        self.client.login(username="830041", password="store-pass")
        self.url = reverse("employees:store_shelf_stock")

    def assertRedirectsWithPrint(self, response, expected):
        """Like assertRedirects, but the target may carry a ?print= marker."""
        from urllib.parse import parse_qsl, urlparse

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        loc, exp = urlparse(location), urlparse(expected)
        self.assertEqual(loc.path, exp.path)
        loc_q = dict(parse_qsl(loc.query))
        exp_q = dict(parse_qsl(exp.query))
        self.assertLessEqual(
            exp_q.items(),
            loc_q.items(),
            f"{location} missing expected query params {exp_q}",
        )
        self.assertTrue(
            loc_q.get("print", "").startswith(("shelf:", "store:", "movement:")),
            f"{location} has no movement print marker",
        )
        self.assertEqual(self.client.get(location).status_code, 200)

    def test_store_manager_registers_shelf_items_for_a_shop(self):
        response = self.client.get("/company-store-manager/")
        self.assertNotContains(response, self.url)
        self.assertContains(self.client.get(self.url), "Shelf mode is off")

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.assertContains(self.client.get("/company-store-manager/"), self.url)
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

    def test_register_form_lives_in_popup_not_page_body(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.client.post(
            self.url,
            {"action": "register", "name": "Gunny bags", "opening_qty": "20", "shop": self.shop.pk},
        )
        page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        # The register trigger lives in the sidebar, not the listing panel.
        self.assertContains(page, 'data-modal-open="shelf-stock"')
        self.assertNotContains(page, "stock-shelf-register-btn")
        self.assertContains(page, 'data-modal="shelf-stock"')
        self.assertContains(page, 'data-shelf-register-form')
        self.assertContains(page, "GUNNY BAGS")
        # Registering is name/category/unit + optional image — no qty fields.
        body = page.content.decode()
        self.assertNotIn('name="opening_qty"', body)
        self.assertIn('name="image"', body)
        # The Manage column edits details (incl. the low-stock alert) and
        # suspends items from the same page.
        self.assertIn('data-modal="shelf-edit"', body)
        self.assertIn('data-shelf-edit-form', body)
        self.assertIn('name="low_stock_threshold"', body)

    def test_register_shelf_item_accepts_optional_image(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        photo = SimpleUploadedFile(
            "rolls.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg"
        )
        self.client.post(
            self.url,
            {
                "action": "register",
                "name": "Thermal rolls",
                "shop": self.shop.pk,
                "image": photo,
            },
        )
        shelf = ShelfItem.objects.get(shop=self.shop)
        self.assertEqual(shelf.quantity, Decimal("0"))
        self.assertTrue(shelf.image)

        page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        self.assertContains(page, shelf.public_image_url())
        photo_url = reverse("core:shelf_photo", kwargs={"shelf_item_id": shelf.pk})
        self.assertEqual(self.client.get(photo_url).status_code, 200)

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

    def test_shelf_only_mode_hides_item_store_pages(self):
        items_url = reverse("employees:store_stock_items")
        in_url = reverse("employees:store_stock_in")
        out_url = reverse("employees:store_stock_out")
        trade_url = reverse("employees:store_stock_trade")
        movement_url = reverse("employees:store_stock_movement")
        report_url = reverse("employees:store_stock_report")
        item_urls = (
            items_url,
            in_url,
            out_url,
            trade_url,
            movement_url,
            report_url,
        )

        page = self.client.get("/company-store-manager/")
        self.assertContains(page, items_url)
        for url in item_urls:
            self.assertEqual(self.client.get(url).status_code, 200)

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        page = self.client.get("/company-store-manager/")
        self.assertNotContains(page, items_url)
        for url in item_urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/company-store-manager/shelf-stock/", response["Location"])
        self.assertContains(page, self.url)
        payload = self.client.get(
            reverse("employees:store_stock_item_search")
        ).json()
        self.assertEqual(payload["results"], [])

        # Both mode: items are tracked again — item links and pages return.
        set_company_pos_setting(field="stock_mode_both", enabled=True)
        page = self.client.get("/company-store-manager/")
        self.assertContains(page, items_url)
        for url in item_urls:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(page, self.url)

    def test_dashboard_links_item_and_shelf_stock_per_mode(self):
        items_url = reverse("employees:store_stock_items")

        page = self.client.get("/company-store-manager/")
        self.assertContains(page, "Item stock")
        self.assertContains(page, items_url)
        self.assertNotContains(page, self.url)

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.client.post(
            self.url,
            {"action": "register", "name": "Gunny bags", "opening_qty": "20", "shop": self.shop.pk},
        )
        page = self.client.get("/company-store-manager/")
        self.assertContains(page, items_url)
        self.assertContains(page, self.url)
        self.assertContains(page, "Shelf stock")
        # Registering happens from the stock pages' sidebar, not the dashboard.
        self.assertNotContains(page, 'data-modal-open="shelf-stock"')

        # Shelf-only: the item stock link disappears, shelf stays.
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        page = self.client.get("/company-store-manager/")
        self.assertContains(page, self.url)
        self.assertNotContains(page, items_url)
        self.assertNotContains(page, "Item stock")

    def test_edit_and_suspend_shelf_items(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        shelf = ShelfItem.objects.create(
            shop=self.shop,
            name="GUNNY BAGS",
            category="PACKAGING",
            unit="BAGS",
            quantity=Decimal("9"),
            low_stock_threshold=Decimal("2"),
            created_by=self.manager,
        )
        page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        self.assertContains(page, "GUNNY BAGS")
        self.assertContains(page, 'data-shelf-edit')
        self.assertContains(page, 'data-modal="shelf-edit"')
        self.assertContains(page, 'value="suspend"')
        # The sidebar carries a Suspended items link for the shop.
        self.assertContains(page, "Suspended items")
        self.assertContains(page, "mode=suspended")

        # Editing updates the details — the quantity never moves by hand.
        response = self.client.post(
            self.url,
            {
                "shop": self.shop.pk,
                "action": "edit",
                "shelf_item_id": shelf.pk,
                "name": "gunny bags small",
                "category": "packaging supplies",
                "unit": "pcs",
                "low_stock_threshold": "4",
            },
        )
        self.assertEqual(response.status_code, 302)
        shelf.refresh_from_db()
        self.assertEqual(shelf.name, "GUNNY BAGS SMALL")
        self.assertEqual(shelf.category, "PACKAGING SUPPLIES")
        self.assertEqual(shelf.unit, "PCS")
        self.assertEqual(shelf.low_stock_threshold, Decimal("4"))
        self.assertEqual(shelf.quantity, Decimal("9"))

        # Renaming onto an existing item is rejected.
        ShelfItem.objects.create(
            shop=self.shop, name="SISAL ROPE", created_by=self.manager
        )
        self.client.post(
            self.url,
            {
                "shop": self.shop.pk,
                "action": "edit",
                "shelf_item_id": shelf.pk,
                "name": "sisal rope",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.name, "GUNNY BAGS SMALL")

        # Suspending drops the item off the shelf list and blocks moves.
        self.client.post(
            self.url,
            {
                "shop": self.shop.pk,
                "action": "suspend",
                "shelf_item_id": shelf.pk,
            },
        )
        shelf.refresh_from_db()
        self.assertTrue(shelf.is_suspended)
        page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        # Off the item rows — its name still shows in the movement log.
        self.assertNotContains(page, f'data-shelf-row="{shelf.pk}"')
        self.assertNotContains(page, f'data-item-id="{shelf.pk}"')
        self.assertContains(page, "SISAL ROPE")
        self.client.post(
            self.url,
            {
                "shop": self.shop.pk,
                "action": "in",
                "shelf_item_id": shelf.pk,
                "qty": "2",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("9"))

        # The suspended page lists it; unsuspending puts it back.
        page = self.client.get(
            f"{self.url}?mode=suspended&shop={self.shop.pk}"
        )
        self.assertContains(page, "Suspended shelf items")
        self.assertContains(page, "GUNNY BAGS SMALL")
        self.assertContains(page, 'value="unsuspend"')
        self.client.post(
            self.url,
            {
                "mode": "suspended",
                "shop": self.shop.pk,
                "action": "unsuspend",
                "shelf_item_id": shelf.pk,
            },
        )
        shelf.refresh_from_db()
        self.assertFalse(shelf.is_suspended)
        self.assertContains(
            self.client.get(f"{self.url}?shop={self.shop.pk}"),
            "GUNNY BAGS SMALL",
        )

    def test_stock_page_audits_list_all_transactions_for_the_period(self):
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.client.post(
            self.url,
            {
                "action": "register",
                "name": "Gunny bags",
                "opening_qty": "9",
                "shop": self.shop.pk,
            },
        )
        shelf = ShelfItem.objects.get(shop=self.shop)
        self.client.post(
            self.url,
            {
                "action": "in",
                "shop": self.shop.pk,
                "shelf_item_id": shelf.pk,
                "qty": "2",
                "price": "40",
                "note": "audit-top-up",
            },
        )
        self.client.post(
            self.url,
            {
                "action": "out",
                "shop": self.shop.pk,
                "shelf_item_id": shelf.pk,
                "qty": "1",
                "note": "audit-removal",
            },
        )

        # The audits live on their own page (sidebar link ?mode=audits); the
        # stock page shows only the On the shelf list — no Recent changes.
        page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        self.assertContains(page, f"{self.url}?mode=audits")
        self.assertContains(page, "On the shelf")
        self.assertNotContains(page, "Recent changes")
        self.assertNotContains(page, "data-stock-report-filters")

        # The audits page lists today's register + in + out transactions by
        # default.
        page = self.client.get(
            f"{self.url}?shop={self.shop.pk}&mode=audits"
        )
        self.assertContains(page, "Shelf stock audits")
        self.assertNotContains(page, "Recent changes")
        self.assertContains(page, 'name="range"')
        self.assertContains(page, "audit-top-up")
        self.assertContains(page, "audit-removal")

        # Backdate one movement: today's filter drops it, the matching
        # period brings it back, and the year filter shows everything.
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        moved = ShelfStockMovement.objects.get(
            shelf_item=shelf, note="audit-top-up"
        )
        past = dj_timezone.now() - timedelta(days=40)
        ShelfStockMovement.objects.filter(pk=moved.pk).update(
            created_at=past
        )

        page = self.client.get(
            f"{self.url}?shop={self.shop.pk}&mode=audits"
        )
        self.assertNotContains(page, "audit-top-up")
        self.assertContains(page, "audit-removal")

        page = self.client.get(
            f"{self.url}?shop={self.shop.pk}&mode=audits&range=period"
            f"&date_from={past.date():%Y-%m-%d}&date_to={past.date():%Y-%m-%d}"
        )
        self.assertContains(page, "audit-top-up")
        self.assertNotContains(page, "audit-removal")

        page = self.client.get(
            f"{self.url}?shop={self.shop.pk}&mode=audits&range=year&year={past.year}"
        )
        self.assertContains(page, "audit-top-up")
        self.assertContains(page, "audit-removal")

    def test_item_stock_page_lists_central_store_qty_and_tabs(self):
        item = Item.objects.create(
            category="GEN",
            name="Rice 5kg",
            minimum_selling_price=Decimal("10"),
            shop_price=Decimal("20"),
            created_by=self.manager,
        )
        StoreStock.objects.create(item=item, quantity=Decimal("150"))

        page = self.client.get(reverse("employees:store_stock_items"))
        self.assertContains(page, "Rice 5kg")
        self.assertContains(page, "150.000")
        # The five item-stock links live in the page sidebar.
        for name in (
            "store_stock_in",
            "store_stock_out",
            "store_stock_movement",
            "store_stock_report",
        ):
            self.assertContains(page, reverse(f"employees:{name}"))
        self.assertContains(page, "Item stock in")
        self.assertContains(page, "Item stock movement")
        # Item pages never carry shelf links.
        self.assertNotContains(page, self.url)

    def test_item_stock_movement_page_lists_in_out_ledger(self):
        item = Item.objects.create(
            category="GEN",
            name="Rice 5kg",
            minimum_selling_price=Decimal("10"),
            shop_price=Decimal("20"),
            created_by=self.manager,
        )
        self.client.post(
            reverse("employees:store_stock_in"),
            {
                f"qty_{item.pk}": "25",
                f"price_{item.pk}": "18.00",
                "received_by_id": self.manager.pk,
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "paid",
                "note": "Delivery",
            },
        )
        page = self.client.get(reverse("employees:store_stock_movement"))
        self.assertContains(page, "Rice 5kg")
        self.assertContains(page, "Stock in")
        self.assertContains(page, "Delivery")

    def test_shelf_stock_modes_cover_in_out_movement_and_report(self):
        set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.client.post(
            self.url,
            {"action": "register", "name": "Gunny bags", "opening_qty": "20", "shop": self.shop.pk},
        )
        shelf = ShelfItem.objects.get(shop=self.shop)

        # The five shelf-stock links live in the page sidebar; item links
        # never appear on shelf pages.
        stock_page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        self.assertContains(stock_page, "Shelf stock in")
        self.assertContains(stock_page, f"{self.url}?mode=out")
        self.assertContains(stock_page, f"{self.url}?mode=movements")
        self.assertContains(stock_page, f"{self.url}?mode=report")
        self.assertContains(stock_page, f"{self.url}?mode=audits")
        self.assertNotContains(stock_page, reverse("employees:store_stock_items"))

        # Stock in: batch form posts qty_<id> lines like the item pages.
        in_page = self.client.get(f"{self.url}?shop={self.shop.pk}&mode=in")
        self.assertContains(in_page, f'name="qty_{shelf.pk}"')
        self.client.post(
            self.url,
            {
                "mode": "in",
                "shop": self.shop.pk,
                f"qty_{shelf.pk}": "7",
                f"price_{shelf.pk}": "12.00",
                "supplier_name": "Kenafric",
                "supplier_phone": "0733111222",
                "payment_status": "unpaid",
                "note": "Top up",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("27"))

        # Stock out: same batch form with reason + refund, qty capped at
        # what is on the shelf.
        self.client.post(
            self.url,
            {
                "mode": "out",
                "shop": self.shop.pk,
                f"qty_{shelf.pk}": "2",
                "reason": "waste",
                "refund": "no",
            },
        )
        self.client.post(
            self.url,
            {
                "mode": "out",
                "shop": self.shop.pk,
                f"qty_{shelf.pk}": "999",
                "reason": "waste",
                "refund": "no",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("25"))
        out_move = shelf.movements.get(movement_type="out")
        self.assertEqual(out_move.reason, "waste")
        self.assertEqual(out_move.refund, "no")

        # Movement + report tabs render the ledger and the summary.
        movements = self.client.get(f"{self.url}?shop={self.shop.pk}&mode=movements")
        self.assertContains(movements, "Top up")
        self.assertContains(movements, "GUNNY BAGS")
        report = self.client.get(f"{self.url}?shop={self.shop.pk}&mode=report")
        self.assertContains(report, "GUNNY BAGS")
        self.assertContains(report, "Stock report")

    def test_shelf_trade_is_one_way_with_a_person(self):
        set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.client.post(
            self.url,
            {"action": "register", "name": "Gunny bags", "opening_qty": "20", "shop": self.shop.pk},
        )
        shelf = ShelfItem.objects.get(shop=self.shop)

        # Sidebar action + popup; the trade page shows the direction toggle.
        stock_page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        self.assertContains(stock_page, "Shelf stock trade")
        self.assertContains(stock_page, 'data-modal-open="shelf-stock-trade"')
        self.assertContains(stock_page, 'data-modal="shelf-stock-trade"')
        trade_page = self.client.get(f"{self.url}?shop={self.shop.pk}&mode=trade")
        self.assertContains(trade_page, 'name="trade_kind"')
        self.assertContains(trade_page, f'name="qty_{shelf.pk}"')

        # Trade out: the person takes items off the shelf.
        resp = self.client.post(
            self.url,
            {
                "mode": "trade",
                "shop": self.shop.pk,
                "trade_kind": "trade_out",
                f"qty_{shelf.pk}": "4",
                "person_name": "Jane Trader",
                "person_phone": "0722000000",
                "note": "swap deal",
            },
        )
        self.assertRedirectsWithPrint(resp, f"{self.url}?shop={self.shop.pk}")
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("16"))
        trade_out = ShelfStockMovement.objects.get(
            shelf_item=shelf, movement_type="out", reason="trade_out"
        )
        self.assertEqual(trade_out.supplier_name, "JANE TRADER")
        self.assertEqual(trade_out.supplier_phone, "0722000000")
        self.assertEqual(trade_out.note, "swap deal")

        # Trade in: the person brings items onto the shelf (unit value is
        # required while shelf_require_buying_price_on_in is on).
        self.client.post(
            self.url,
            {
                "mode": "trade",
                "shop": self.shop.pk,
                "trade_kind": "trade_in",
                f"qty_{shelf.pk}": "3",
                f"price_{shelf.pk}": "15.00",
                "person_name": "Jane Trader",
                "person_phone": "0722000000",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("19"))
        trade_in = ShelfStockMovement.objects.get(
            shelf_item=shelf, movement_type="in", reason="trade_in"
        )
        self.assertEqual(trade_in.supplier_name, "JANE TRADER")

        # No direction, no lines, and over-quantity trades are rejected.
        self.client.post(
            self.url,
            {"mode": "trade", "shop": self.shop.pk, f"qty_{shelf.pk}": "1"},
        )
        self.client.post(
            self.url,
            {"mode": "trade", "shop": self.shop.pk, "trade_kind": "trade_out"},
        )
        self.client.post(
            self.url,
            {
                "mode": "trade",
                "shop": self.shop.pk,
                "trade_kind": "trade_out",
                f"qty_{shelf.pk}": "999",
            },
        )
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("19"))
        self.assertEqual(
            ShelfStockMovement.objects.filter(
                shelf_item=shelf, reason__in=("trade_out", "trade_in")
            ).count(),
            2,
        )

    def test_both_mode_adds_item_and_shelf_tabs_to_item_management(self):
        page_url = "/company-store-manager/item-management/"

        page = self.client.get(page_url)
        self.assertNotContains(page, 'href="?tab=shelf"')

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.client.post(
            self.url,
            {
                "action": "register",
                "name": "Gunny bags",
                "opening_qty": "4",
                "shop": self.shop.pk,
            },
        )

        page = self.client.get(page_url)
        self.assertContains(page, 'href="?tab=shelf"')
        self.assertContains(page, 'href="?tab=items"')
        self.assertContains(page, "data-item-catalog-api")

        shelf_page = self.client.get(f"{page_url}?tab=shelf")
        self.assertContains(shelf_page, "Shelf stock")
        self.assertContains(shelf_page, "STORE SHELF SHOP")
        self.assertContains(shelf_page, "GUNNY BAGS")
        self.assertNotContains(shelf_page, "data-item-catalog-root")

        # Shelf-only mode lands on the shelf list by default.
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        page = self.client.get(page_url)
        self.assertContains(page, "GUNNY BAGS")
        self.assertNotContains(page, "data-item-catalog-root")

    def test_stock_in_and_out_live_in_popups(self):
        item = Item.objects.create(
            category="GEN",
            name="Rice 5kg",
            minimum_selling_price=Decimal("10"),
            shop_price=Decimal("20"),
            created_by=self.manager,
        )

        # Item stock: sidebar triggers open modals on every item-stock page.
        for url in (
            reverse("employees:store_stock_items"),
            reverse("employees:store_stock_movement"),
            reverse("employees:store_stock_report"),
        ):
            page = self.client.get(url)
            self.assertContains(page, 'data-modal-open="store-stock-in"')
            self.assertContains(page, 'data-modal-open="store-stock-out"')
            self.assertContains(page, 'data-modal="store-stock-in"')
            self.assertContains(page, 'data-modal="store-stock-out"')
            self.assertContains(
                page, f'action="{reverse("employees:store_stock_in")}"'
            )
            self.assertContains(
                page, f'action="{reverse("employees:store_stock_out")}"'
            )
            self.assertContains(page, f'name="qty_{item.pk}"')

        # Stock-in popups capture supplier details + payment; stock-out
        # popups do not.
        page = self.client.get(reverse("employees:store_stock_items"))
        body = page.content.decode()
        in_modal = body.split('data-modal="store-stock-in"', 1)[1].split(
            'data-modal="store-stock-out"', 1
        )[0]
        self.assertIn('name="supplier_name"', in_modal)
        self.assertIn('name="supplier_phone"', in_modal)
        self.assertIn('name="payment_status"', in_modal)
        self.assertIn(f'name="price_{item.pk}"', in_modal)
        # Pick-and-add flow: rows offer an Add button into a selected list.
        self.assertIn('data-store-row-add', in_modal)
        self.assertIn('data-store-row-remove', in_modal)
        self.assertIn('data-store-selected-list', in_modal)
        out_modal = body.split('data-modal="store-stock-out"', 1)[1].split(
            'data-modal="store-stock-trade"', 1
        )[0]
        self.assertNotIn(f'name="price_{item.pk}"', out_modal)
        self.assertNotIn('name="supplier_name"', out_modal)
        for field in ("reason", "refund", "refund_amount"):
            self.assertIn(f'name="{field}"', out_modal)

        # A popup POST lands on Current stock, not the standalone form page.
        resp = self.client.post(
            reverse("employees:store_stock_in"),
            {
                f"qty_{item.pk}": "5",
                f"price_{item.pk}": "32.50",
                "received_by_id": self.manager.pk,
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "partial",
                "note": "Pop up",
            },
        )
        self.assertRedirectsWithPrint(
            resp, reverse("employees:store_stock_items")
        )
        movement = item.store_stock_movements.get()
        self.assertEqual(movement.buying_price, Decimal("32.50"))
        self.assertEqual(movement.supplier_name, "BROOKSIDE")
        self.assertEqual(movement.supplier_phone, "0722000000")
        self.assertEqual(movement.payment_status, "partial")

        # Supplier + payment are required on stock in by default.
        denied = self.client.post(
            reverse("employees:store_stock_in"),
            {
                f"qty_{item.pk}": "1",
                f"price_{item.pk}": "10.00",
                "received_by_id": self.manager.pk,
            },
        )
        self.assertContains(denied, "supplier name", status_code=200)
        self.assertEqual(item.store_stock_movements.count(), 1)

        # Stock in needs no counterpart — it records the acting manager.
        self.assertNotIn('name="received_by_id"', in_modal)
        resp = self.client.post(
            reverse("employees:store_stock_in"),
            {
                f"qty_{item.pk}": "3",
                f"price_{item.pk}": "30.00",
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "paid",
            },
        )
        self.assertRedirectsWithPrint(resp, reverse("employees:store_stock_items"))
        no_counterpart = item.store_stock_movements.latest("id")
        self.assertEqual(no_counterpart.received_by, self.manager)

        # Stock out needs no receiving employee either — and the popup
        # defaults the reason to Custom with the custom-reason field open.
        self.assertNotIn('name="received_by_id"', out_modal)
        self.assertIn('value="custom" selected', out_modal)
        resp = self.client.post(
            reverse("employees:store_stock_out"),
            {
                f"qty_{item.pk}": "1",
                "reason": "waste",
                "refund": "no",
            },
        )
        self.assertRedirectsWithPrint(resp, reverse("employees:store_stock_items"))
        no_emp_out = item.store_stock_movements.get(movement_type="out")
        self.assertEqual(no_emp_out.received_by, self.manager)

        # Stock out carries reason + refund like the shop-level stock-out
        # panel: custom reason uses the note field, refund=yes needs an amount.
        out = self.client.post(
            reverse("employees:store_stock_out"),
            {
                f"qty_{item.pk}": "2",
                "received_by_id": self.manager.pk,
                "reason": "custom",
                "note": "Damaged in transit",
                "refund": "yes",
                "refund_amount": "45.50",
            },
        )
        self.assertRedirectsWithPrint(out, reverse("employees:store_stock_items"))
        out_move = item.store_stock_movements.filter(
            movement_type="out"
        ).latest("id")
        self.assertEqual(out_move.reason, "custom")
        self.assertEqual(out_move.note, "Damaged in transit")
        self.assertEqual(out_move.refund, "yes")
        self.assertEqual(out_move.refund_amount, Decimal("45.50"))

        # Reason and refund choice are required by default on stock out.
        denied_out = self.client.post(
            reverse("employees:store_stock_out"),
            {f"qty_{item.pk}": "1", "received_by_id": self.manager.pk},
        )
        self.assertContains(denied_out, "Choose a reason", status_code=200)
        self.assertEqual(item.store_stock_movements.count(), 4)

        # Shelf stock: the same popup pattern posts mode + shop + qty_<id>.
        set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.client.post(
            self.url,
            {"action": "register", "name": "Gunny bags", "shop": self.shop.pk},
        )
        shelf = ShelfItem.objects.get(shop=self.shop)

        shelf_page = self.client.get(f"{self.url}?shop={self.shop.pk}")
        self.assertContains(shelf_page, 'data-modal-open="shelf-stock-in"')
        self.assertContains(shelf_page, 'data-modal-open="shelf-stock-out"')
        self.assertContains(shelf_page, 'data-modal="shelf-stock-in"')
        self.assertContains(shelf_page, 'data-modal="shelf-stock-out"')
        self.assertContains(shelf_page, 'name="mode" value="in"')
        self.assertContains(shelf_page, 'name="mode" value="out"')
        self.assertContains(shelf_page, f'name="qty_{shelf.pk}"')
        self.assertContains(shelf_page, 'name="supplier_name"')
        self.assertContains(shelf_page, 'name="payment_status"')
        shelf_body = shelf_page.content.decode()
        shelf_out = shelf_body.split('data-modal="shelf-stock-out"', 1)[1]
        for field in ("reason", "refund", "refund_amount"):
            self.assertIn(f'name="{field}"', shelf_out)
        self.assertNotIn('name="supplier_name"', shelf_out)

        resp = self.client.post(
            self.url,
            {
                "mode": "in",
                "shop": self.shop.pk,
                f"qty_{shelf.pk}": "7",
                f"price_{shelf.pk}": "14.00",
                "supplier_name": "Kenafric",
                "supplier_phone": "0733111222",
                "payment_status": "unpaid",
                "note": "Pop up",
            },
        )
        self.assertRedirectsWithPrint(resp, f"{self.url}?shop={self.shop.pk}")
        shelf.refresh_from_db()
        self.assertEqual(shelf.quantity, Decimal("7"))
        in_move = shelf.movements.get(movement_type="in")
        self.assertEqual(in_move.supplier_name, "KENAFRIC")
        self.assertEqual(in_move.supplier_phone, "0733111222")
        self.assertEqual(in_move.payment_status, "unpaid")

    def test_buying_price_toggle_applies_to_store_manager(self):
        """Stock Settings → Unit buying price also gates the store pages."""
        from shops.services import set_company_stock_setting

        item = Item.objects.create(
            category="GEN",
            name="Rice 5kg",
            minimum_selling_price=Decimal("10"),
            shop_price=Decimal("20"),
            created_by=self.manager,
        )
        in_url = reverse("employees:store_stock_in")

        # On by default: a filled line with no buying price is rejected.
        denied = self.client.post(
            in_url,
            {
                f"qty_{item.pk}": "5",
                "received_by_id": self.manager.pk,
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "paid",
            },
        )
        self.assertContains(denied, "buying price", status_code=200)
        self.assertFalse(StoreStock.objects.exists())
        page = self.client.get(in_url)
        self.assertContains(page, "Buying price <em>required</em>")

        # With a price the line records.
        ok = self.client.post(
            in_url,
            {
                f"qty_{item.pk}": "5",
                f"price_{item.pk}": "18.00",
                "received_by_id": self.manager.pk,
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "paid",
            },
        )
        self.assertRedirectsWithPrint(
            ok, reverse("employees:store_stock_items")
        )
        movement = item.store_stock_movements.get()
        self.assertEqual(movement.buying_price, Decimal("18.00"))

        # Trade in applies the same rule to the unit value column; trade
        # out never collects a value.
        denied_trade = self.client.post(
            reverse("employees:store_stock_trade"),
            {"trade_kind": "trade_in", f"qty_{item.pk}": "2"},
        )
        self.assertContains(denied_trade, "buying price", status_code=200)
        self.assertEqual(item.store_stock_movements.count(), 1)

        # Turning the toggle off makes the price optional again.
        set_company_stock_setting(
            field="require_buying_price_on_in", enabled=False
        )
        ok_free = self.client.post(
            in_url,
            {
                f"qty_{item.pk}": "1",
                "received_by_id": self.manager.pk,
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "paid",
            },
        )
        self.assertRedirectsWithPrint(ok_free, reverse("employees:store_stock_items"))
        self.assertEqual(item.store_stock_movements.count(), 2)

    def test_shelf_settings_are_independent_of_item_settings(self):
        """Shelf stock follows the shelf_require_* flags, not the item ones."""
        from shops.services import set_company_stock_setting

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        self.client.post(
            self.url,
            {
                "action": "register",
                "name": "Gunny bags",
                "opening_qty": "20",
                "shop": self.shop.pk,
            },
        )
        shelf = ShelfItem.objects.get(shop=self.shop)
        payload = {
            "mode": "in",
            "shop": self.shop.pk,
            f"qty_{shelf.pk}": "5",
            "supplier_name": "Kenafric",
            "supplier_phone": "0733111222",
            "payment_status": "unpaid",
        }

        # The item flag being off does not relax the shelf rule.
        set_company_stock_setting(
            field="require_buying_price_on_in", enabled=False
        )
        denied = self.client.post(self.url, payload, follow=True)
        self.assertContains(denied, "buying price", status_code=200)
        self.assertFalse(
            ShelfStockMovement.objects.filter(movement_type="in").exists()
        )

        # The shelf flag is what unlocks the price-free stock in.
        set_company_stock_setting(
            field="shelf_require_buying_price_on_in", enabled=False
        )
        ok = self.client.post(self.url, payload)
        self.assertRedirectsWithPrint(ok, f"{self.url}?shop={self.shop.pk}")
        self.assertEqual(
            ShelfStockMovement.objects.filter(movement_type="in").count(), 1
        )

        # Supplier rules are independent too: item supplier off, shelf
        # supplier on → shelf stock in still needs the supplier.
        set_company_stock_setting(field="require_supplier_on_in", enabled=False)
        denied_supplier = self.client.post(
            self.url,
            {
                "mode": "in",
                "shop": self.shop.pk,
                f"qty_{shelf.pk}": "1",
                "payment_status": "unpaid",
            },
            follow=True,
        )
        self.assertContains(denied_supplier, "supplier name", status_code=200)
        set_company_stock_setting(
            field="shelf_require_supplier_on_in", enabled=False
        )
        ok_supplier = self.client.post(
            self.url,
            {
                "mode": "in",
                "shop": self.shop.pk,
                f"qty_{shelf.pk}": "1",
                "payment_status": "unpaid",
            },
        )
        self.assertRedirectsWithPrint(ok_supplier, f"{self.url}?shop={self.shop.pk}")
        self.assertEqual(
            ShelfStockMovement.objects.filter(movement_type="in").count(), 2
        )
