"""Shared ledger movements page: store stock, shelf stock, shelf tab."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import (
    Item,
    ShelfItem,
    ShelfStockMovement,
    ShelfStockMovementType,
    StoreStock,
    StoreStockMovement,
    StoreStockMovementType,
)
from shops.models import CompanyPosSettings, Shop, ShopPosSettings
from shops.services import _invalidate_pos_settings_cache

_RATE_LIMITS = {
    "login": {"max": 10_000, "window": 60},
    "check_employee_id": {"max": 10_000, "window": 60},
    "register": {"max": 10_000, "window": 60},
    "sync": {"max": 10_000, "window": 60},
}


def _make_profile(employee_id, role, password):
    user = User.objects.create_user(
        username=employee_id, password=password, is_active=True
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=employee_id,
        phone_country_code="+254",
        phone_number="700000123",
        status=EmployeeStatus.ACTIVE,
        role=role,
    )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"], RATE_LIMITS=_RATE_LIMITS)
class LedgerMovementPageTests(TestCase):
    def setUp(self):
        self.manager = _make_profile(
            "830055", EmployeeRole.STORE_MANAGER, "mgr-pass"
        )
        self.support = _make_profile(
            "840055", EmployeeRole.IT_SUPPORT, "it-pass"
        )
        self.shop = Shop.objects.create(
            name="LEDGER SHOP",
            location="NAIROBI",
            email="ledger@test.local",
            phone_number="0700000555",
            login_code="830155",
            password_hash="x",
            created_by=self.manager,
        )
        # "Both" stock mode: item stock tracked AND every shop is a shelf shop.
        CompanyPosSettings.objects.update_or_create(
            pk=1,
            defaults={"enable_stock_tracking": True, "enable_shelf_mode": True},
        )
        ShopPosSettings.objects.filter(shop=self.shop).delete()
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        from shops.services import _invalidate_stock_settings_cache

        _invalidate_stock_settings_cache()
        self.addCleanup(_invalidate_stock_settings_cache)

        self.item = Item.objects.create(
            category="CABLES",
            name="HDMI CABLE",
            minimum_selling_price=Decimal("100.00"),
            shop_price=Decimal("150.00"),
            created_by=self.manager,
        )
        StoreStock.objects.create(item=self.item, quantity=Decimal("7"))
        StoreStockMovement.objects.create(
            item=self.item,
            movement_type=StoreStockMovementType.IN,
            quantity=Decimal("10"),
            balance_after=Decimal("10"),
            created_by=self.manager,
            received_by=self.manager,
            supplier_name="ACME",
            payment_status="paid",
            buying_price=Decimal("50"),
        )
        StoreStockMovement.objects.create(
            item=self.item,
            movement_type=StoreStockMovementType.OUT,
            quantity=Decimal("3"),
            balance_after=Decimal("7"),
            created_by=self.manager,
            received_by=self.manager,
            source="pos",
            note="RCP-9001 · Sale · LEDGER SHOP",
        )

        self.shelf_item = ShelfItem.objects.create(
            shop=self.shop,
            name="SUGAR BAGS",
            unit="bags",
            category="DRY GOODS",
            quantity=Decimal("5"),
        )
        ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.REGISTER,
            quantity=Decimal("10"),
            balance_after=Decimal("10"),
            created_by=self.manager,
        )
        ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("2"),
            balance_after=Decimal("12"),
            created_by=self.manager,
            supplier_name="ACME",
            payment_status="paid",
            buying_price=Decimal("30"),
        )
        ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.OUT,
            quantity=Decimal("7"),
            balance_after=Decimal("5"),
            created_by=self.manager,
        )

    # -- store manager · central store ----------------------------------

    def test_store_movement_timeline(self):
        self.client.login(username="830055", password="mgr-pass")
        url = reverse("employees:store_stock_movement")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-stock-report-filters")
        self.assertContains(response, "HDMI CABLE")
        self.assertContains(response, "RCP-9001")
        self.assertContains(response, "Sale")
        events = [
            event
            for group in response.context["movement_event_groups"]
            for event in group["activities"]
        ]
        self.assertEqual(len(events), 2)
        sale = next(e for e in events if e["receipt_number"] == "RCP-9001")
        self.assertEqual(sale["event_type"], "sale")
        self.assertEqual(sale["event_label"], "Sale")
        self.assertEqual(sale["to_label"], "LEDGER SHOP")

    def test_store_movement_summary_and_detail(self):
        self.client.login(username="830055", password="mgr-pass")
        url = reverse("employees:store_stock_movement")
        response = self.client.get(url, {"view_by": "item"})
        self.assertEqual(response.status_code, 200)
        rows = [
            row
            for row in response.context["movement_item_rows"]
            if not row.get("is_item_total")
        ]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["item_name"], "HDMI CABLE")
        self.assertEqual(row["units_in"], 10)
        self.assertEqual(row["units_sale"], 3)
        self.assertEqual(row["current_stock"], Decimal("7"))
        self.assertTrue(row["detail_url"])

        detail = self.client.get(row["detail_url"])
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.context["is_item_movement_detail"])
        self.assertEqual(detail.context["detail_item"].pk, self.item.pk)

    def test_store_movement_type_and_picker(self):
        self.client.login(username="830055", password="mgr-pass")
        url = reverse("employees:store_stock_movement")
        ins_only = self.client.get(url, {"event_type": "in"})
        self.assertEqual(ins_only.status_code, 200)
        events = [
            e
            for g in ins_only.context["movement_event_groups"]
            for e in g["activities"]
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "in")

        picker = self.client.get(url, {"item_picker": "1"})
        self.assertEqual(picker.status_code, 200)
        payload = picker.json()
        self.assertIn(
            "HDMI CABLE", [row["name"] for row in payload["items"]]
        )

    # -- store manager · shelf -------------------------------------------

    def test_store_shelf_movements_page(self):
        self.client.login(username="830055", password="mgr-pass")
        url = reverse("employees:store_shelf_stock")
        response = self.client.get(url, {"mode": "movements"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-stock-report-filters")
        self.assertContains(response, "SUGAR BAGS")
        self.assertContains(response, "Registered")
        types = {opt["value"] for opt in response.context["event_filter_options"]}
        self.assertEqual(types, {"register", "in", "out"})

    def test_store_shelf_movement_summary_counts_register_as_in(self):
        self.client.login(username="830055", password="mgr-pass")
        url = reverse("employees:store_shelf_stock")
        response = self.client.get(
            url, {"mode": "movements", "view_by": "item"}
        )
        self.assertEqual(response.status_code, 200)
        rows = [
            row
            for row in response.context["movement_item_rows"]
            if not row.get("is_item_total")
        ]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["item_name"], "SUGAR BAGS")
        self.assertEqual(row["units_in"], 12)  # 10 register + 2 in
        self.assertEqual(row["units_out"], 7)
        self.assertEqual(row["current_stock"], Decimal("5"))

        registered_only = self.client.get(
            url,
            {"mode": "movements", "event_type": "register"},
        )
        events = [
            e
            for g in registered_only.context["movement_event_groups"]
            for e in g["activities"]
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "register")

    # -- it support · shelf tab ------------------------------------------

    def test_it_support_shelf_tab_movements(self):
        self.client.force_login(self.support.user)
        response = self.client.get(
            "/it-support/stock-management/",
            {
                "mode": "movements",
                "tab": "shelf",
                "range": "day",
                "item_mode": "all",
                "view_by": "item",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-stock-report-filters")
        rows = [
            row
            for row in response.context["movement_item_rows"]
            if not row.get("is_item_total")
        ]
        names = {row["item_name"] for row in rows}
        self.assertIn("SUGAR BAGS", names)
        row = next(r for r in rows if r["item_name"] == "SUGAR BAGS")
        self.assertEqual(row["units_in"], 12)
        self.assertTrue(row["detail_url"])

    def test_it_support_shelf_tab_timeline_keeps_tab(self):
        self.client.force_login(self.support.user)
        response = self.client.get(
            "/it-support/stock-management/",
            {"mode": "movements", "tab": "shelf", "range": "day"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SUGAR BAGS")
        self.assertContains(response, 'name="tab" value="shelf"')

    # -- downloads --------------------------------------------------------

    def test_movement_pdf_download(self):
        self.client.login(username="830055", password="mgr-pass")
        for url, params in (
            (reverse("employees:store_stock_movement"), {}),
            (reverse("employees:store_shelf_stock"), {"mode": "movements"}),
        ):
            response = self.client.get(url, {**params, "download": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(
                response["Content-Disposition"].startswith("attachment")
            )
