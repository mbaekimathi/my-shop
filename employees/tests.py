from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from employees.analytics_services import ANALYTICS_SECTIONS, _build_clients
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from employees.permissions_catalog import PERMISSION_MODULE_BY_SLUG
from employees.workspace import (
    DASHBOARD_MODULES,
    HR_SIDEBAR_SECTIONS,
    SETTINGS_NESTED_SECTIONS,
    SETTINGS_SECTIONS,
    sidebar_for_marketing_links,
    sidebar_for_my_shop,
    sidebar_for_role_dashboard,
    sidebar_for_settings,
    sidebar_for_super_admin,
    sidebar_for_suppliers,
)
from items.services import actionable_shops_for_profile
from shops.models import Client, Shop, ShopReceipt, ShopReceiptKind, ShopReceiptStatus


class SupplierSidebarTests(TestCase):
    def test_suppliers_sidebar_only_has_all_suppliers(self):
        sidebar = sidebar_for_suppliers(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(labels, ["Dashboard", "All suppliers"])
        self.assertTrue(sidebar["primary"][1]["active"])
        self.assertIn("/analytics/suppliers/", sidebar["primary"][1]["href"])
        self.assertEqual(sidebar["primary"][0]["href"], sidebar["dashboard_url"])


class DashboardSidebarLinkTests(SimpleTestCase):
    def test_role_dashboard_omits_dashboard_link(self):
        sidebar = sidebar_for_role_dashboard(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Dashboard", labels)
        self.assertIn("Marketing links", labels)
        self.assertIn("WhatsApp", labels)
        whatsapp = next(item for item in sidebar["primary"] if item["label"] == "WhatsApp")
        self.assertIn("/it-support/whatsapp/inbox/", whatsapp["href"])

    def test_reused_dashboard_nav_on_other_pages_includes_dashboard(self):
        sidebar = sidebar_for_role_dashboard(
            EmployeeRole.IT_SUPPORT,
            active_slug="clients",
            omit_dashboard_link=False,
        )
        self.assertEqual(sidebar["primary"][0]["label"], "Dashboard")
        self.assertEqual(sidebar["primary"][0]["href"], sidebar["dashboard_url"])

    def test_super_admin_dashboard_omits_dashboard_link(self):
        sidebar = sidebar_for_super_admin()
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Dashboard", labels)

    def test_other_pages_start_with_dashboard(self):
        cases = (
            sidebar_for_suppliers(EmployeeRole.IT_SUPPORT),
            sidebar_for_marketing_links(EmployeeRole.IT_SUPPORT),
            sidebar_for_settings(EmployeeRole.IT_SUPPORT),
            sidebar_for_my_shop(EmployeeRole.IT_SUPPORT),
        )
        for sidebar in cases:
            primary = sidebar["primary"]
            self.assertGreaterEqual(len(primary), 1, sidebar["page"])
            self.assertEqual(primary[0]["label"], "Dashboard", sidebar["page"])
            self.assertEqual(primary[0]["href"], sidebar["dashboard_url"], sidebar["page"])
            self.assertEqual(
                [item["label"] for item in primary].count("Dashboard"),
                1,
                sidebar["page"],
            )


class MyShopDashboardSidebarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="860011",
            password="shop-pass",
            email="it-shop-nav@test.local",
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
            name="NAV SHOP",
            location="NAIROBI",
            email="nav-shop@test.local",
            phone_number="0700000961",
            login_code="860111",
            password_hash="x",
            created_by=self.it,
        )

    def test_employee_shop_floor_starts_with_dashboard(self):
        sidebar = sidebar_for_my_shop(
            EmployeeRole.IT_SUPPORT,
            shop=self.shop,
            shops=[self.shop],
            profile=self.it,
        )
        self.assertEqual(sidebar["primary"][0]["label"], "Dashboard")
        self.assertEqual(sidebar["primary"][0]["href"], sidebar["dashboard_url"])
        self.assertIn("/it-support/", sidebar["dashboard_url"])

    def test_portal_shop_floor_omits_dashboard(self):
        sidebar = sidebar_for_my_shop(
            EmployeeRole.SHOP_CASHIER,
            shop=self.shop,
            shops=[self.shop],
            portal=True,
            active="workspace",
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Dashboard", labels)

    def test_portal_receipts_start_with_dashboard(self):
        sidebar = sidebar_for_my_shop(
            EmployeeRole.SHOP_CASHIER,
            shop=self.shop,
            shops=[self.shop],
            portal=True,
            active="receipts",
        )
        self.assertEqual(sidebar["primary"][0]["label"], "Dashboard")
        self.assertEqual(sidebar["primary"][0]["href"], sidebar["dashboard_url"])
        self.assertIn(f"/my-shop/{self.shop.pk}/", sidebar["dashboard_url"])


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


class ClientCreditAccountTableTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="830011",
            password="credit-pass",
            email="credit-cashier@test.local",
            first_name="CREDIT",
            last_name="CASHIER",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="830011",
            phone_country_code="+254",
            phone_number="700000931",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="CREDIT SHOP",
            location="NAIROBI",
            email="credit-shop@test.local",
            phone_number="0700000931",
            login_code="830111",
            password_hash="x",
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        self.client = Client.objects.create(
            full_name="CREDIT CLIENT",
            phone_number="0700002001",
            phone_normalized="254700002001",
            created_by=self.cashier,
        )
        self._receipt_n = 0

    def _filters(self, **extra):
        return {
            "active_shop_ids": [self.shop.pk],
            "filter_shops": [self.shop],
            "role": self.cashier.role,
            "query": "",
            **extra,
        }

    def _make_client(self, name, phone_suffix):
        return Client.objects.create(
            full_name=name,
            phone_number=f"0700002{phone_suffix}",
            phone_normalized=f"254700002{phone_suffix}",
            created_by=self.cashier,
        )

    def _make_receipt(self, client, **kwargs):
        self._receipt_n += 1
        defaults = {
            "shop": self.shop,
            "receipt_number": f"CR-{self._receipt_n}",
            "kind": ShopReceiptKind.CREDIT,
            "total": 100,
            "amount_paid": 0,
            "created_by": self.cashier,
            "client": client,
            "client_name": client.full_name,
            "client_phone": client.phone_number,
            "status": ShopReceiptStatus.ACTIVE,
        }
        defaults.update(kwargs)
        return ShopReceipt.objects.create(**defaults)

    def _row_for(self, page, name):
        for row in page["tables"][0]["rows"]:
            first = row[0]
            if isinstance(first, dict) and name in (first.get("label") or ""):
                return row
        return None

    def test_fully_paid_credit_is_converted_to_sale(self):
        from employees.analytics_services import apply_credit_receipt_payment

        receipt = self._make_receipt(self.client, total=100, amount_paid=0)
        result = apply_credit_receipt_payment(
            profile=self.cashier,
            receipt_id=receipt.pk,
            amount="100",
        )
        receipt.refresh_from_db()
        self.assertTrue(result["converted"])
        self.assertEqual(receipt.kind, ShopReceiptKind.SALE)
        self.assertEqual(str(receipt.amount_paid), "100.00")
        page = _build_clients(self._filters())
        self.assertIsNone(self._row_for(page, "CREDIT CLIENT"))

    def test_partial_credit_stays_credit_with_balance_due(self):
        from employees.analytics_services import apply_credit_receipt_payment

        receipt = self._make_receipt(self.client, total=100, amount_paid=0)
        result = apply_credit_receipt_payment(
            profile=self.cashier,
            receipt_id=receipt.pk,
            amount="40",
        )
        receipt.refresh_from_db()
        self.assertFalse(result["converted"])
        self.assertEqual(receipt.kind, ShopReceiptKind.CREDIT)
        page = _build_clients(self._filters())
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-1]["qty"], "1")
        self.assertEqual(row[-1]["amount"], "60")

    def test_cancelled_credit_is_excluded(self):
        self._make_receipt(
            self.client,
            total=200,
            amount_paid=0,
            status=ShopReceiptStatus.CANCELLED,
        )
        page = _build_clients(self._filters())
        self.assertIsNone(self._row_for(page, "CREDIT CLIENT"))

    def test_sale_only_client_is_excluded(self):
        sale_client = self._make_client("SALE CLIENT", "002")
        self._make_receipt(
            sale_client,
            kind=ShopReceiptKind.SALE,
            total=80,
            amount_paid=80,
        )
        page = _build_clients(self._filters())
        self.assertIsNone(self._row_for(page, "SALE CLIENT"))

    def test_overpayment_does_not_reduce_other_receipt_due(self):
        self._make_receipt(self.client, total=100, amount_paid=150)
        self._make_receipt(self.client, total=80, amount_paid=0)
        page = _build_clients(self._filters())
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-1]["qty"], "2")
        self.assertEqual(row[-1]["amount"], "80")

    def test_credits_page_links_use_credits_account_url(self):
        self._make_receipt(self.client)
        page = _build_clients(self._filters(from_credits=True))
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIn("/analytics/credits/clients/", row[0]["href"])
        self.assertIn("all-time", page["tables"][0]["footnote"])


class CreditsShopScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840011",
            password="credit-pass",
            email="it-credits@test.local",
            first_name="IT",
            last_name="SUPPORT",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840011",
            phone_country_code="+254",
            phone_number="700000941",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="CREDITS SCOPE A",
            location="NAIROBI",
            email="scope-a@test.local",
            phone_number="0700000941",
            login_code="840111",
            password_hash="x",
            created_by=self.it,
        )
        self.shop_b = Shop.objects.create(
            name="CREDITS SCOPE B",
            location="MOMBASA",
            email="scope-b@test.local",
            phone_number="0700000942",
            login_code="840112",
            password_hash="x",
            created_by=self.it,
        )

    def _request(self, **get):
        from django.test import RequestFactory

        request = RequestFactory().get("/it-support/analytics/credits/", get)
        request.user = self.user
        request.session = {}
        return request

    def test_it_support_credits_default_to_all_shops(self):
        from employees.analytics_services import _filters_context

        filters = _filters_context(self.it, self._request())
        self.assertEqual(filters["active_shop_ids"], [self.shop_a.pk, self.shop_b.pk])
        self.assertEqual(filters["selected_shop_ids"], [])
        self.assertFalse(filters["credits_require_shop_pick"])

    def test_it_support_credits_ignore_allocated_shops_on_load(self):
        from employees.analytics_services import _filters_context

        self.it.assigned_shops.add(self.shop_a)
        filters = _filters_context(self.it, self._request())
        self.assertEqual(filters["active_shop_ids"], [self.shop_a.pk, self.shop_b.pk])
        self.assertEqual(filters["selected_shop_ids"], [])

    def test_it_support_credits_ignore_session_shop_on_load(self):
        from employees.analytics_services import _filters_context
        from shops.session import SESSION_SHOP_KEY

        request = self._request()
        request.session[SESSION_SHOP_KEY] = str(self.shop_b.pk)
        filters = _filters_context(self.it, request)
        self.assertEqual(filters["active_shop_ids"], [self.shop_a.pk, self.shop_b.pk])
        self.assertEqual(filters["selected_shop_ids"], [])

    def test_shop_manager_credits_keep_allocated_shops(self):
        from django.test import RequestFactory

        from employees.analytics_services import _filters_context

        manager_user = User.objects.create_user(
            username="840012",
            password="credit-pass",
            email="mgr-credits@test.local",
            is_active=True,
        )
        manager = EmployeeProfile.objects.create(
            user=manager_user,
            employee_id="840012",
            phone_country_code="+254",
            phone_number="700000942",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        manager.assigned_shops.add(self.shop_a)
        request = RequestFactory().get("/shop-manager/analytics/credits/")
        request.user = manager_user
        request.session = {}
        filters = _filters_context(manager, request)
        self.assertEqual(filters["active_shop_ids"], [self.shop_a.pk])
        self.assertFalse(filters["credits_require_shop_pick"])

    def test_it_support_credits_honour_explicit_shop_filter(self):
        from employees.analytics_services import _filters_context

        filters = _filters_context(
            self.it, self._request(shop_id=str(self.shop_b.pk))
        )
        self.assertEqual(filters["active_shop_ids"], [self.shop_b.pk])
        self.assertEqual(filters["selected_shop_ids"], [self.shop_b.pk])

    def test_credits_page_renders_all_shops_filter_on_load(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/credits/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(">All shops</option>", html)
        self.assertNotIn(">Select a shop</option>", html)
        self.assertEqual(response.context["selected_shop_ids"], [])
        self.assertCountEqual(
            response.context["active_shop_ids"],
            [self.shop_a.pk, self.shop_b.pk],
        )


class CreditAuditsEventFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="840021",
            password="audit-pass",
            email="it-audits@test.local",
            first_name="IT",
            last_name="AUDITS",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840021",
            phone_country_code="+254",
            phone_number="700000961",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="AUDIT SHOP",
            location="NAIROBI",
            email="audit-shop@test.local",
            phone_number="0700000961",
            login_code="840121",
            password_hash="x",
            created_by=self.it,
        )
        self.client_row = Client.objects.create(
            full_name="AUDIT CLIENT",
            phone_number="0700004001",
            phone_normalized="254700004001",
            created_by=self.it,
        )
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="CR-AUDIT-1",
            kind=ShopReceiptKind.CREDIT,
            total=120,
            amount_paid=0,
            created_by=self.it,
            client=self.client_row,
            client_name=self.client_row.full_name,
            client_phone=self.client_row.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )

    def _log(self, kind, *, amount=None, detail="", occurred_at=None):
        from shops.credit_audit import log_client_credit_event

        return log_client_credit_event(
            client_id=self.client_row.pk,
            kind=kind,
            shop=self.shop,
            receipt=self.receipt,
            amount=amount,
            detail=detail,
            actor=self.it,
            occurred_at=occurred_at,
        )

    def test_all_events_excludes_credit_issued(self):
        from shops.credit_audit import build_credit_audits
        from shops.models import ClientCreditAccountEventKind

        self._log(ClientCreditAccountEventKind.PAYMENT_CASH, amount=40)
        self._log(ClientCreditAccountEventKind.PAYMENT_MPESA, amount=20)
        self._log(ClientCreditAccountEventKind.ITEMS_RETURNED)
        page = build_credit_audits(profile=self.it)
        kinds = [row["kind"] for row in page["rows"]]
        self.assertNotIn(ClientCreditAccountEventKind.CREDIT_ISSUED, kinds)
        self.assertCountEqual(
            kinds,
            [
                ClientCreditAccountEventKind.PAYMENT_CASH,
                ClientCreditAccountEventKind.PAYMENT_MPESA,
                ClientCreditAccountEventKind.ITEMS_RETURNED,
            ],
        )
        self.assertEqual(page["event_filter"], "")
        labels = [option["label"] for option in page["event_options"]]
        self.assertEqual(labels[0], "All events")
        self.assertIn("Cash payment", labels)
        self.assertIn("M-Pesa payment", labels)

    def test_event_filter_returns_matching_kind_only(self):
        from shops.credit_audit import build_credit_audits
        from shops.models import ClientCreditAccountEventKind

        self._log(ClientCreditAccountEventKind.PAYMENT_CASH, amount=40)
        self._log(ClientCreditAccountEventKind.PAYMENT_MPESA, amount=20)
        self._log(ClientCreditAccountEventKind.ITEMS_RETURNED)
        page = build_credit_audits(
            profile=self.it, event_kind=ClientCreditAccountEventKind.PAYMENT_CASH
        )
        self.assertEqual([row["kind"] for row in page["rows"]], ["payment_cash"])
        self.assertEqual(page["event_filter"], "payment_cash")
        self.assertEqual(page["event_filter_label"], "Cash payment")
        self.assertEqual(page["payment_count"], 1)
        self.assertEqual(page["change_count"], 0)

    def test_unknown_event_filter_falls_back_to_all(self):
        from shops.credit_audit import build_credit_audits
        from shops.models import ClientCreditAccountEventKind

        self._log(ClientCreditAccountEventKind.PAYMENT_CASH, amount=40)
        self._log(ClientCreditAccountEventKind.ITEMS_RETURNED)
        page = build_credit_audits(profile=self.it, event_kind="not-a-kind")
        self.assertEqual(page["event_filter"], "")
        self.assertEqual(len(page["rows"]), 2)

    def test_audits_page_renders_event_filter(self):
        from shops.models import ClientCreditAccountEventKind

        self._log(ClientCreditAccountEventKind.PAYMENT_CASH, amount=40)
        self._log(ClientCreditAccountEventKind.ITEMS_RETURNED)
        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/credits/audits/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="event"', html)
        self.assertIn(">All events</option>", html)
        self.assertIn(">Cash payment</option>", html)
        self.assertIn(">Items returned</option>", html)
        self.assertEqual(response.context["event_filter"], "")
        self.assertEqual(len(response.context["rows"]), 2)
        self.assertIn('name="range"', html)
        self.assertIn(">Day</option>", html)
        self.assertIn(">Period</option>", html)
        self.assertIn(">Month</option>", html)
        self.assertIn(">Year</option>", html)
        self.assertEqual(response.context["report_range"], "day")

    def test_audits_page_honours_event_query(self):
        from shops.models import ClientCreditAccountEventKind

        self._log(ClientCreditAccountEventKind.PAYMENT_CASH, amount=40)
        self._log(ClientCreditAccountEventKind.ITEMS_RETURNED)
        self.client.force_login(self.user)
        response = self.client.get(
            "/it-support/analytics/credits/audits/", {"event": "items_returned"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event_filter"], "items_returned")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["kind"], "items_returned")
        html = response.content.decode()
        self.assertIn('value="items_returned" selected', html)

    def test_day_filter_excludes_other_days(self):
        from datetime import datetime, time, timedelta

        from django.test import RequestFactory
        from django.utils import timezone

        from shops.credit_audit import build_credit_audits
        from shops.models import ClientCreditAccountEventKind

        today = timezone.localdate()
        older = today - timedelta(days=3)
        tz = timezone.get_current_timezone()
        self._log(
            ClientCreditAccountEventKind.PAYMENT_CASH,
            amount=40,
            occurred_at=timezone.make_aware(datetime.combine(older, time(12, 0)), tz),
        )
        self._log(
            ClientCreditAccountEventKind.ITEMS_RETURNED,
            occurred_at=timezone.make_aware(datetime.combine(today, time(12, 0)), tz),
        )
        request = RequestFactory().get(
            "/it-support/analytics/credits/audits/",
            {"range": "day", "date": today.isoformat()},
        )
        page = build_credit_audits(profile=self.it, request=request)
        self.assertEqual([row["kind"] for row in page["rows"]], ["items_returned"])
        self.assertEqual(page["report_range"], "day")

    def test_year_filter_keeps_matching_events(self):
        from datetime import datetime

        from django.test import RequestFactory
        from django.utils import timezone

        from shops.credit_audit import build_credit_audits
        from shops.models import ClientCreditAccountEventKind

        tz = timezone.get_current_timezone()
        self._log(
            ClientCreditAccountEventKind.PAYMENT_CASH,
            amount=40,
            occurred_at=timezone.make_aware(datetime(2024, 6, 15, 12, 0), tz),
        )
        self._log(
            ClientCreditAccountEventKind.ITEMS_RETURNED,
            occurred_at=timezone.make_aware(datetime(2026, 3, 1, 12, 0), tz),
        )
        request = RequestFactory().get(
            "/it-support/analytics/credits/audits/",
            {"range": "year", "year": "2024"},
        )
        page = build_credit_audits(profile=self.it, request=request)
        self.assertEqual([row["kind"] for row in page["rows"]], ["payment_cash"])
        self.assertEqual(page["report_range"], "year")
        self.assertEqual(page["report_period_label"], "2024")

    def test_audits_page_honours_date_query(self):
        from datetime import datetime, time, timedelta

        from django.utils import timezone

        from shops.models import ClientCreditAccountEventKind

        today = timezone.localdate()
        older = today - timedelta(days=5)
        tz = timezone.get_current_timezone()
        self._log(
            ClientCreditAccountEventKind.PAYMENT_CASH,
            amount=40,
            occurred_at=timezone.make_aware(datetime.combine(older, time(10, 0)), tz),
        )
        self._log(
            ClientCreditAccountEventKind.ITEMS_RETURNED,
            occurred_at=timezone.make_aware(datetime.combine(today, time(10, 0)), tz),
        )
        self.client.force_login(self.user)
        response = self.client.get(
            "/it-support/analytics/credits/audits/",
            {"range": "day", "date": older.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["report_range"], "day")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["kind"], "payment_cash")


class MarketingLinksPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="850011",
            password="mkt-pass",
            email="it-marketing@test.local",
            first_name="IT",
            last_name="LINKS",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="850011",
            phone_country_code="+254",
            phone_number="700000951",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.live = Shop.objects.create(
            name="LIVE MARKET SHOP",
            location="NAIROBI",
            email="live-mkt@test.local",
            phone_number="0700000951",
            login_code="850111",
            password_hash="x",
            created_by=self.it,
        )
        Shop.objects.create(
            name="HIDDEN MARKET SHOP",
            location="KISUMU",
            email="hidden-mkt@test.local",
            phone_number="0700000952",
            login_code="850112",
            password_hash="x",
            created_by=self.it,
            is_hidden=True,
        )

    def test_it_support_home_lists_public_shop_website(self):
        self.client.force_login(self.user)
        home = self.client.get("/it-support/")
        self.assertEqual(home.status_code, 200)
        home_labels = [item["label"] for item in home.context["page_sidebar"]["primary"]]
        self.assertEqual(
            home_labels,
            [
                "Stock Management",
                "Analytics",
                "Credits",
                "Suppliers",
                "Clients",
                "Item Management",
                "HR Management",
                "Shop Management",
                "Marketing links",
                "WhatsApp",
            ],
        )
        self.assertIn("/it-support/marketing/", home.context["page_sidebar"]["primary"][-2]["href"])
        self.assertIn("/it-support/whatsapp/inbox/", home.context["page_sidebar"]["primary"][-1]["href"])

        response = self.client.get("/it-support/marketing/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marketing links")
        self.assertContains(response, "LIVE MARKET SHOP")
        self.assertContains(response, f"http://localhost:8000/shop/{self.live.pk}/")
        self.assertContains(response, "data:image/png;base64,")
        self.assertContains(response, "Local")
        self.assertNotContains(response, "Hosted")
        self.assertNotContains(response, "HIDDEN MARKET SHOP")
        labels = [item["label"] for item in response.context["page_sidebar"]["primary"]]
        self.assertEqual(
            labels,
            ["Dashboard", "Communication settings", "Share items", "Contacts", "Inbox", "Activities"],
        )
        self.assertIn(
            "/settings/communication-settings/",
            response.context["page_sidebar"]["primary"][1]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/catalogue/",
            response.context["page_sidebar"]["primary"][2]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/contacts/",
            response.context["page_sidebar"]["primary"][3]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/inbox/",
            response.context["page_sidebar"]["primary"][4]["href"],
        )
        self.assertIn(
            "/it-support/marketing/activities/",
            response.context["page_sidebar"]["primary"][5]["href"],
        )
        shop = response.context["marketing_links"][0]
        self.assertEqual(len(shop["variants"]), 1)
        self.assertEqual(shop["variants"][0]["key"], "local")
        self.assertTrue(shop["variants"][0]["qr"].startswith("data:image/png;base64,"))

    @override_settings(IS_HOSTED=True, DARAJA_CALLBACK_BASE_URL="https://shops.example.com")
    def test_marketing_links_include_hosted_qr(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/marketing/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f"http://localhost:8000/shop/{self.live.pk}/")
        self.assertContains(response, f"https://shops.example.com/shop/{self.live.pk}/")
        self.assertContains(response, "Hosted")
        self.assertNotContains(response, "Local")
        shop = response.context["marketing_links"][0]
        self.assertEqual(shop["local_url"], "")
        self.assertEqual(shop["hosted_url"], f"https://shops.example.com/shop/{self.live.pk}/")
        self.assertEqual(len(shop["variants"]), 1)
        self.assertEqual(shop["variants"][0]["key"], "hosted")
        self.assertTrue(shop["variants"][0]["qr"].startswith("data:image/png;base64,"))
