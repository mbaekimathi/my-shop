"""Health column on the company-store-manager and stock-management pages."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShelfItem, ShopStock
from shops.models import CompanyPosSettings, Shop, ShopPosSettings
from shops.services import _invalidate_pos_settings_cache


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class HealthColumnTests(TestCase):
    def setUp(self):
        self.password = "health-pass"
        self.user = User.objects.create_user(
            username="830091",
            password=self.password,
            is_active=True,
        )
        self.manager = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="830091",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.COMPANY_STORE_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="HEALTH SHOP",
            location="NAIROBI",
            login_code="830092",
            password_hash="x",
            created_by=self.manager,
        )

    def test_item_stock_health_column(self):
        low_item = Item.objects.create(
            category="PARTS",
            name="LOW WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            stock=Decimal("5.000"),
            low_stock_notify=True,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=low_item,
            quantity=Decimal("2.000"),
            low_stock_threshold=5,
            low_stock_manual=True,
        )
        ok_item = Item.objects.create(
            category="PARTS",
            name="OK WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            stock=Decimal("50.000"),
            low_stock_notify=True,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=ok_item,
            quantity=Decimal("50.000"),
            low_stock_threshold=5,
            low_stock_manual=True,
        )
        off_item = Item.objects.create(
            category="PARTS",
            name="OFF WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            stock=Decimal("1.000"),
        )
        ShopStock.objects.create(
            shop=self.shop, item=off_item, quantity=Decimal("0")
        )

        self.client.login(username="830091", password=self.password)
        page = self.client.get("/company-store-manager/item-stock/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, ">Health<")
        self.assertContains(page, 'data-label="Health"')
        html = page.content.decode()
        self.assertIn('low-stock-badge is-low', html)
        self.assertIn('low-stock-badge is-ok', html)
        self.assertIn('low-stock-badge is-off', html)
        self.assertIn("Low at HEALTH SHOP", html)

    def test_shelf_stock_health_column(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        ShelfItem.objects.create(
            shop=self.shop,
            name="KETTLE",
            category="KIT",
            unit="pcs",
            quantity=Decimal("1.000"),
            low_stock_threshold=Decimal("5"),
        )
        ShelfItem.objects.create(
            shop=self.shop,
            name="POT",
            category="KIT",
            unit="pcs",
            quantity=Decimal("9.000"),
            low_stock_threshold=Decimal("5"),
        )
        ShelfItem.objects.create(
            shop=self.shop,
            name="PAN",
            category="KIT",
            unit="pcs",
            quantity=Decimal("0"),
        )
        self.client.login(username="830091", password=self.password)
        page = self.client.get(
            f"/company-store-manager/shelf-stock/?shop={self.shop.pk}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, ">Health<")
        self.assertContains(page, 'data-label="Health"')
        html = page.content.decode()
        self.assertIn('low-stock-badge is-low', html)
        self.assertIn('low-stock-badge is-ok', html)
        self.assertIn('low-stock-badge is-off', html)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class StockManagementHealthTests(TestCase):
    """Health on /shop-store-manager/stock-management/ — the item stock
    catalog JSON (view mode renders via stock-catalog.js) and the shelf
    matrix (view tab + the in/out panel)."""

    def setUp(self):
        self.password = "w-pass"
        self.user = User.objects.create_user(
            username="860091",
            password=self.password,
            is_active=True,
        )
        self.mgr = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860091",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_STORE_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="ALPHA SHOP",
            location="NAIROBI",
            login_code="860092",
            password_hash="x",
            created_by=self.mgr,
        )
        self.shop_b = Shop.objects.create(
            name="BETA SHOP",
            location="NAIROBI",
            login_code="860093",
            password_hash="x",
            created_by=self.mgr,
        )
        self.mgr.assigned_shops.add(self.shop, self.shop_b)
        self.client.login(username="860091", password=self.password)

    def _stock_modes(self, *, items, shelf):
        CompanyPosSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enable_stock_tracking": items,
                "enable_shelf_mode": shelf,
            },
        )
        ShopPosSettings.objects.filter(
            shop__in=[self.shop, self.shop_b]
        ).delete()
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)

    def _make_items(self):
        low = Item.objects.create(
            category="PARTS",
            name="LOW WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            low_stock_notify=True,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=low,
            quantity=Decimal("2.000"),
            low_stock_threshold=5,
            low_stock_manual=True,
        )
        ShopStock.objects.create(
            shop=self.shop_b,
            item=low,
            quantity=Decimal("50.000"),
            low_stock_threshold=5,
            low_stock_manual=True,
        )
        ok = Item.objects.create(
            category="PARTS",
            name="OK WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            low_stock_notify=True,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=ok,
            quantity=Decimal("50.000"),
            low_stock_threshold=5,
            low_stock_manual=True,
        )
        off = Item.objects.create(
            category="PARTS",
            name="OFF WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
        )
        ShopStock.objects.create(shop=self.shop, item=off, quantity=0)
        return low, ok, off

    def _catalog(self, **params):
        response = self.client.get(
            "/shop-store-manager/stock-management/catalog/",
            {"mode": "view", **params},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        return payload

    def test_view_catalog_single_shop_health(self):
        self._stock_modes(items=True, shelf=False)
        self._make_items()
        payload = self._catalog(shop_id=self.shop.pk)
        rows = {row["name"]: row for row in payload["items"]}
        self.assertEqual(rows["LOW WIDGET"]["health"], "low")
        self.assertEqual(rows["LOW WIDGET"]["health_label"], "Low now")
        self.assertEqual(rows["LOW WIDGET"]["shop_health"], ["low"])
        self.assertEqual(rows["OK WIDGET"]["health"], "ok")
        self.assertEqual(rows["OK WIDGET"]["health_label"], "Watching")
        self.assertEqual(rows["OK WIDGET"]["shop_health"], ["ok"])
        self.assertEqual(rows["OFF WIDGET"]["health"], "off")
        self.assertEqual(rows["OFF WIDGET"]["health_label"], "Off")
        self.assertEqual(rows["OFF WIDGET"]["shop_health"], ["off"])

    def test_view_catalog_multi_shop_health(self):
        self._stock_modes(items=True, shelf=False)
        self._make_items()
        payload = self._catalog()  # no shop pick — every tracked shop
        rows = {row["name"]: row for row in payload["items"]}
        shops = payload["shops"]
        alpha_idx = next(
            i for i, s in enumerate(shops) if s["id"] == self.shop.pk
        )
        beta_idx = next(
            i for i, s in enumerate(shops) if s["id"] == self.shop_b.pk
        )
        low = rows["LOW WIDGET"]
        self.assertEqual(low["health"], "low")
        self.assertEqual(low["shop_health"][alpha_idx], "low")
        self.assertEqual(low["shop_health"][beta_idx], "ok")
        self.assertIn("ALPHA SHOP", low["health_title"])
        ok = rows["OK WIDGET"]
        self.assertEqual(ok["health"], "ok")
        off = rows["OFF WIDGET"]
        self.assertEqual(off["health"], "off")
        self.assertEqual(off["shop_health"], ["off"] * len(shops))

    def test_action_modes_have_no_health(self):
        self._stock_modes(items=True, shelf=False)
        self._make_items()
        response = self.client.get(
            "/shop-store-manager/stock-management/catalog/",
            {"mode": "in", "shop_id": self.shop.pk},
        )
        self.assertEqual(response.status_code, 200)
        for row in response.json()["items"]:
            self.assertNotIn("health", row)
            self.assertNotIn("shop_health", row)

    def test_shelf_view_tab_health_column(self):
        self._stock_modes(items=True, shelf=True)
        ShelfItem.objects.create(
            shop=self.shop,
            name="KETTLE",
            category="KIT",
            quantity=Decimal("1.000"),
            low_stock_threshold=Decimal("5"),
        )
        ShelfItem.objects.create(
            shop=self.shop,
            name="POT",
            category="KIT",
            quantity=Decimal("9.000"),
            low_stock_threshold=Decimal("5"),
        )
        ShelfItem.objects.create(
            shop=self.shop_b,
            name="PAN",
            category="KIT",
            quantity=Decimal("3.000"),
        )
        page = self.client.get(
            "/shop-store-manager/stock-management/",
            {"mode": "view", "tab": "shelf"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, ">Health<")
        html = page.content.decode()
        self.assertIn('low-stock-badge is-low', html)
        self.assertIn('low-stock-badge is-ok', html)
        self.assertIn('low-stock-badge is-off', html)
        self.assertIn("Low at ALPHA SHOP", html)

    def test_shelf_in_panel_health_column(self):
        self._stock_modes(items=True, shelf=True)
        ShelfItem.objects.create(
            shop=self.shop,
            name="KETTLE",
            category="KIT",
            quantity=Decimal("1.000"),
            low_stock_threshold=Decimal("5"),
        )
        page = self.client.get(
            "/shop-store-manager/stock-management/",
            {"mode": "in", "shop_id": self.shop.pk},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, ">Health<")
        self.assertContains(page, 'low-stock-badge is-low')

    def test_shelf_only_shop_no_item_matrix(self):
        self._stock_modes(items=False, shelf=True)
        ShelfItem.objects.create(
            shop=self.shop,
            name="KETTLE",
            category="KIT",
            quantity=Decimal("9.000"),
            low_stock_threshold=Decimal("5"),
        )
        page = self.client.get(
            "/shop-store-manager/stock-management/",
            {"mode": "view", "shop_id": self.shop.pk},
        )
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        # Shelf panel renders with health; the item catalog stays off.
        self.assertIn("stock-shelf-panel", html)
        self.assertIn('low-stock-badge is-ok', html)
        self.assertNotIn("data-stock-catalog-root", html)

    def test_item_only_shop_no_shelf_panel(self):
        self._stock_modes(items=True, shelf=False)
        self._make_items()
        page = self.client.get(
            "/shop-store-manager/stock-management/",
            {"mode": "view", "shop_id": self.shop.pk},
        )
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("data-stock-catalog-root", html)
        self.assertNotIn("stock-shelf-panel", html)
