from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from employees.analytics_services import ANALYTICS_SECTIONS, _build_clients
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from employees.permissions_catalog import PERMISSION_MODULE_BY_SLUG
from employees.workspace import (
    DASHBOARD_MODULES,
    HR_SIDEBAR_SECTIONS,
    SETTINGS_NESTED_SECTIONS,
    SETTINGS_SECTIONS,
    sidebar_for_suppliers,
)
from items.services import actionable_shops_for_profile
from shops.models import Client, Shop, ShopReceipt, ShopReceiptKind, ShopReceiptStatus


class SupplierSidebarTests(TestCase):
    def test_suppliers_sidebar_only_has_all_suppliers(self):
        sidebar = sidebar_for_suppliers(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(labels, ["All suppliers"])
        self.assertTrue(sidebar["primary"][0]["active"])
        self.assertIn("/analytics/suppliers/", sidebar["primary"][0]["href"])


class PermissionCatalogTests(SimpleTestCase):
    def test_dashboard_modules_are_in_catalog(self):
        catalog = set(PERMISSION_MODULE_BY_SLUG)
        for module in DASHBOARD_MODULES:
            self.assertIn(module["slug"], catalog)

    def test_analytics_sections_are_permission_keys(self):
        slugs = {row["slug"] for row in PERMISSION_MODULE_BY_SLUG["analytics"]["submodules"]}
        for section in ANALYTICS_SECTIONS:
            expected = "view" if section["slug"] == "overview" else section["slug"]
            self.assertIn(expected, slugs)
        self.assertIn("account_pay", slugs)

    def test_settings_sections_are_permission_keys(self):
        slugs = {row["slug"] for row in PERMISSION_MODULE_BY_SLUG["settings"]["submodules"]}
        self.assertIn("home", slugs)
        for section in (*SETTINGS_SECTIONS, *SETTINGS_NESTED_SECTIONS):
            self.assertIn(section["slug"], slugs)

    def test_hr_sidebar_sections_are_permission_keys(self):
        slugs = {
            row["slug"] for row in PERMISSION_MODULE_BY_SLUG["hr-management"]["submodules"]
        }
        for section in HR_SIDEBAR_SECTIONS:
            self.assertIn(section["slug"], slugs)

    def test_stock_sidebar_modes_are_permission_keys(self):
        slugs = {
            row["slug"] for row in PERMISSION_MODULE_BY_SLUG["stock-management"]["submodules"]
        }
        for mode in (
            "view",
            "in",
            "out",
            "request",
            "serials",
            "movements",
            "report",
            "settings",
            "low-stock",
            "print",
        ):
            self.assertIn(mode, slugs)

    def test_whatsapp_features_are_permission_keys(self):
        slugs = {row["slug"] for row in PERMISSION_MODULE_BY_SLUG["whatsapp"]["submodules"]}
        for key in ("view", "send", "inbox", "analytics", "connect"):
            self.assertIn(key, slugs)

    def test_my_shop_capabilities_are_permission_keys(self):
        slugs = {row["slug"] for row in PERMISSION_MODULE_BY_SLUG["my-shop"]["submodules"]}
        for key in (
            "workspace",
            "sale",
            "credit",
            "quotation",
            "buy_stock",
            "stock_requests",
            "respond_stock_request",
            "register_expense",
            "receipts",
            "return_receipt",
            "open_close",
            "print",
        ):
            self.assertIn(key, slugs)


class AllocatedShopDataScopeTests(TestCase):
    def setUp(self):
        self.password = "scope-pass"
        self.user = User.objects.create_user(
            username="810011",
            password=self.password,
            email="scope-cashier@test.local",
            first_name="SCOPE",
            last_name="CASHIER",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="810011",
            phone_country_code="+254",
            phone_number="700000911",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop_a = Shop.objects.create(
            name="SCOPE SHOP A",
            location="NAIROBI",
            email="scope-a@test.local",
            phone_number="0700000911",
            login_code="810111",
            password_hash="x",
            created_by=self.cashier,
        )
        self.shop_b = Shop.objects.create(
            name="SCOPE SHOP B",
            location="MOMBASA",
            email="scope-b@test.local",
            phone_number="0700000912",
            login_code="810112",
            password_hash="x",
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop_a)
        self.client_a = Client.objects.create(
            full_name="CLIENT A",
            phone_number="0700001001",
            phone_normalized="254700001001",
            created_by=self.cashier,
        )
        self.client_b = Client.objects.create(
            full_name="CLIENT B",
            phone_number="0700001002",
            phone_normalized="254700001002",
            created_by=self.cashier,
        )
        ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="SA-1",
            kind=ShopReceiptKind.CREDIT,
            total=100,
            amount_paid=0,
            created_by=self.cashier,
            client=self.client_a,
            client_name=self.client_a.full_name,
            client_phone=self.client_a.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceipt.objects.create(
            shop=self.shop_b,
            receipt_number="SB-1",
            kind=ShopReceiptKind.CREDIT,
            total=200,
            amount_paid=0,
            created_by=self.cashier,
            client=self.client_b,
            client_name=self.client_b.full_name,
            client_phone=self.client_b.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )

    def test_actionable_shops_are_allocated_only(self):
        shops = actionable_shops_for_profile(self.cashier)
        self.assertEqual([shop.pk for shop in shops], [self.shop_a.pk])

    def test_whatsapp_filters_are_constrained_to_allocated_shops(self):
        from communications.services import constrain_filters_to_profile

        scoped = constrain_filters_to_profile({}, self.cashier)
        self.assertTrue(scoped["shop_scoped"])
        self.assertEqual(scoped["shop_ids"], [self.shop_a.pk])

    def test_shop_management_lists_allocated_shops_only(self):
        from shops.views import _shops_for_shop_management

        shops = _shops_for_shop_management(self.cashier)
        self.assertEqual([shop.pk for shop in shops], [self.shop_a.pk])

    def test_analytics_clients_hide_other_shops(self):
        page = _build_clients(
            {
                "active_shop_ids": [self.shop_a.pk],
                "filter_shops": [self.shop_a],
                "role": self.cashier.role,
                "query": "",
            }
        )
        labels = " ".join(
            str(cell.get("label") if isinstance(cell, dict) else cell)
            for row in page["tables"][0]["rows"]
            for cell in row
        )
        self.assertIn("CLIENT A", labels)
        self.assertNotIn("CLIENT B", labels)

    def test_client_credit_account_rejects_other_shop_client(self):
        from django.http import Http404

        from employees.analytics_services import build_client_credit_account

        with self.assertRaises(Http404):
            build_client_credit_account(profile=self.cashier, client_id=self.client_b.pk)

        account = build_client_credit_account(
            profile=self.cashier, client_id=self.client_a.pk
        )
        self.assertEqual(account["client"].pk, self.client_a.pk)


class StockRequestFromAnyShopTests(TestCase):
    """Stock requests: destination allocated; supply shop may be any active shop."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="820011",
            password="scope-pass",
            email="req-mgr@test.local",
            first_name="REQ",
            last_name="MANAGER",
            is_active=True,
        )
        self.manager = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="820011",
            phone_country_code="+254",
            phone_number="700000921",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop_a = Shop.objects.create(
            name="REQ SHOP A",
            location="NAIROBI",
            email="req-a@test.local",
            phone_number="0700000921",
            login_code="820111",
            password_hash="x",
            created_by=self.manager,
        )
        self.shop_b = Shop.objects.create(
            name="REQ SHOP B",
            location="MOMBASA",
            email="req-b@test.local",
            phone_number="0700000922",
            login_code="820112",
            password_hash="x",
            created_by=self.manager,
        )
        self.manager.assigned_shops.add(self.shop_a)
        from decimal import Decimal

        from items.models import Item, ShopStock, StockMovementType

        self.item = Item.objects.create(
            category="CABLES",
            name="REQ CABLE",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.manager,
        )
        ShopStock.objects.create(shop=self.shop_b, item=self.item, quantity=10)
        self.StockMovementType = StockMovementType

    def test_request_from_unallocated_shop_succeeds(self):
        from django.http import QueryDict

        from items.models import StockMovement
        from items.services import apply_stock_movement

        data = QueryDict(mutable=True)
        data.update(
            {
                "shop_id": str(self.shop_a.pk),
                "requested_from_shop_id": str(self.shop_b.pk),
                "item_id": str(self.item.pk),
                "quantity": "2",
                "note": "Need stock",
            }
        )
        movement = apply_stock_movement(
            self.manager, self.StockMovementType.REQUEST, data
        )
        self.assertEqual(movement.shop_id, self.shop_a.pk)
        self.assertEqual(movement.requested_from_shop_id, self.shop_b.pk)
        self.assertEqual(
            StockMovement.objects.filter(
                shop=self.shop_a, requested_from_shop=self.shop_b
            ).count(),
            1,
        )

    def test_request_to_unallocated_shop_is_rejected(self):
        from django.core.exceptions import ValidationError
        from django.http import QueryDict

        from items.services import apply_stock_movement

        data = QueryDict(mutable=True)
        data.update(
            {
                "shop_id": str(self.shop_b.pk),
                "requested_from_shop_id": str(self.shop_a.pk),
                "item_id": str(self.item.pk),
                "quantity": "1",
                "note": "Should fail",
            }
        )
        with self.assertRaises(ValidationError):
            apply_stock_movement(self.manager, self.StockMovementType.REQUEST, data)
