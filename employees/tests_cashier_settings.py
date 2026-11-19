"""Cashier settings page (/shop-cashier/settings/):

- Footer shows "Cashier settings" (not "System settings") for cashiers.
- Sidebar link picks are stored per employee on EmployeeProfile.sidebar_prefs
  and hide the matching /shop-cashier/ dashboard links.
- Open & close schedule options reuse the company working-hours / POS rows.
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop
from shops.services import (
    get_company_pos_settings,
    get_company_working_hours_settings,
    set_company_pos_setting,
    set_working_hours_enabled,
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
class CashierSettingsTests(TestCase):
    PASSWORD = "cashier-settings-pass"

    def setUp(self):
        self.user = User.objects.create_user(
            username="830001",
            password=self.PASSWORD,
            email="830001@test.local",
            first_name="CASH",
            last_name="IER",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="830001",
            phone_country_code="+254",
            phone_number="700000003",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="CASHIER SHOP",
            location="NAIROBI",
            email="cashier-shop@test.local",
            phone_number="0700001102",
            login_code="870112",
            password_hash="x",
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        set_company_pos_setting(field="enable_open_close", enabled=True)
        set_working_hours_enabled(enabled=False)
        self.client = Client()
        self.client.post(
            reverse("employees:login"),
            {"username": "830001", "password": self.PASSWORD},
        )

    def _sidebar_labels(self):
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        return {
            item["label"]
            for item in response.context["page_sidebar"]["primary"]
            if item.get("type") == "link"
        }

    def _footer_labels(self, response):
        return {
            item["label"]
            for item in response.context["page_sidebar"]["footer"]
            if item.get("type") == "link"
        }

    def test_dashboard_footer_shows_cashier_settings_not_system(self):
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        labels = self._footer_labels(response)
        self.assertIn("Cashier settings", labels)
        self.assertNotIn("System settings", labels)

    def test_settings_page_lists_sidebar_toggles_and_open_close(self):
        response = self.client.get("/shop-cashier/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cashier settings")
        self.assertContains(response, "Dashboard sidebar")
        self.assertContains(response, "Compulsory open")
        labels = {
            toggle["label"] for toggle in response.context["sidebar_toggles"]
        }
        self.assertIn('Show "Open / Close shop"', labels)
        self.assertIn('Show "Suppliers"', labels)
        fields = {
            toggle["field"]
            for toggle in response.context["sidebar_toggles"]
        }
        self.assertEqual(
            fields,
            {
                "sidebar_sell",
                "sidebar_open_close",
                "sidebar_drawings",
                "sidebar_day_report",
                "sidebar_day_history",
                "sidebar_expenses",
                "sidebar_receipts",
                "sidebar_analytics",
                "sidebar_buy_stock",
                "sidebar_suppliers",
            },
        )
        self.assertTrue(all(
            toggle["enabled"] for toggle in response.context["sidebar_toggles"]
        ))

    def test_sidebar_pref_toggle_hides_dashboard_link(self):
        self.assertIn("Expenses", self._sidebar_labels())
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_expenses",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content),
            {"ok": True, "field": "sidebar_expenses", "enabled": False},
        )
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.sidebar_prefs.get("expenses"), False)
        self.assertNotIn("Expenses", self._sidebar_labels())
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_expenses",
                "enabled": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Expenses", self._sidebar_labels())

    def test_settings_page_lists_sell_popup_toggles(self):
        response = self.client.get("/shop-cashier/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sell popup")
        fields = {
            toggle["field"] for toggle in response.context["sell_toggles"]
        }
        self.assertEqual(
            fields,
            {
                "sell_sale",
                "sell_credit",
                "sell_quotation",
                "sell_pay_cash",
                "sell_pay_mpesa",
                "sell_pay_card",
                "sell_edit_prices",
            },
        )

    def test_sell_pref_toggle_persists(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sell_credit",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content),
            {"ok": True, "field": "sell_credit", "enabled": False},
        )
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.sidebar_prefs.get("sell_credit"), False)

    def test_open_close_pref_toggle_hides_day_links(self):
        self.assertIn("Open shop", self._sidebar_labels())
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_open_close",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Open shop", self._sidebar_labels())

    def test_unknown_sidebar_field_rejected(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_bogus",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)

    def test_enable_open_close_toggle_uses_company_pos_row(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "enable_open_close",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_company_pos_settings().enable_open_close)

    def test_open_close_mode_set_enables_working_hours(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {"action": "set_open_close_mode", "mode": "set"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_company_working_hours_settings().enabled)

    def test_open_close_times_save(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "set_open_close_times",
                f"shop_{self.shop.pk}_start_time": "08:30",
                f"shop_{self.shop.pk}_end_time": "18:45",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        from shops.models import ShopWorkingHoursSettings

        hours = ShopWorkingHoursSettings.objects.get(shop=self.shop)
        self.assertEqual(str(hours.start_time)[:5], "08:30")
        self.assertEqual(str(hours.end_time)[:5], "18:45")

    def test_shop_day_page_has_dashboard_link(self):
        for url in (
            "/shop-cashier/shop-day/",
            "/shop-cashier/shop-day/?mode=drawings",
            "/shop-cashier/shop-day/?mode=report",
            "/shop-cashier/shop-day/?mode=history",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            labels = {
                item["label"]
                for item in response.context["page_sidebar"]["primary"]
                if item.get("type") == "link"
            }
            self.assertIn("Dashboard", labels)
            self.assertEqual(
                response.context["page_sidebar"]["dashboard_url"],
                "/shop-cashier/",
            )

    def test_dashboard_sidebar_has_day_report_and_history_links(self):
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        links = {
            item["label"]: item
            for item in response.context["page_sidebar"]["primary"]
            if item.get("type") == "link"
        }
        self.assertIn("Day report", links)
        self.assertIn("Day history", links)
        self.assertEqual(
            links["Day report"]["href"],
            reverse(
                "employees:my_shop_day_report",
                kwargs={"shop_id": self.shop.pk},
            ),
        )
        self.assertTrue(links["Day report"].get("new_tab"))
        self.assertEqual(
            links["Day history"]["href"],
            reverse(
                "employees:my_shop_day_history",
                kwargs={"shop_id": self.shop.pk},
            ),
        )
        # Sidebar only — never rendered as quick-action cards on the page.
        quick_labels = {
            item["label"]
            for item in response.context["page_sidebar"]["quick_actions"]
        }
        self.assertNotIn("Day report", quick_labels)
        self.assertNotIn("Day history", quick_labels)
        self.assertNotContains(response, "Day report (PDF)")

    def test_day_report_pref_toggle_hides_link(self):
        self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_day_report",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertNotIn("Day report", self._sidebar_labels())
        self.assertIn("Day history", self._sidebar_labels())

    def test_settings_page_lists_day_report_status_toggles(self):
        response = self.client.get("/shop-cashier/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Day report")
        toggles = response.context["day_report_toggles"]
        self.assertEqual(
            {toggle["field"] for toggle in toggles},
            {
                "day_report_status_all",
                "day_report_status_pending",
                "day_report_status_confirmed",
            },
        )
        enabled = [toggle for toggle in toggles if toggle["enabled"]]
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["field"], "day_report_status_all")

    def test_day_report_status_pref_updates(self):
        from employees.workspace import day_report_counted_statuses
        from shops.models import ShopReceiptStatus

        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "day_report_status_confirmed",
                "enabled": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content),
            {
                "ok": True,
                "field": "day_report_status_confirmed",
                "enabled": True,
                "stock_mode": "day_report_status_confirmed",
            },
        )
        self.cashier.refresh_from_db()
        self.assertEqual(
            self.cashier.sidebar_prefs.get("day_report_status"), "confirmed"
        )
        self.assertEqual(
            set(day_report_counted_statuses(self.cashier)),
            {
                ShopReceiptStatus.CONFIRMED,
                ShopReceiptStatus.PARTIAL_RETURN,
            },
        )
        response = self.client.get("/shop-cashier/settings/")
        enabled = [
            toggle
            for toggle in response.context["day_report_toggles"]
            if toggle["enabled"]
        ]
        self.assertEqual(
            [toggle["field"] for toggle in enabled],
            ["day_report_status_confirmed"],
        )

    def test_day_report_status_rejects_unknown_and_off(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "day_report_status_bogus",
                "enabled": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "day_report_status_pending",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.cashier.refresh_from_db()
        self.assertIsNone(
            self.cashier.sidebar_prefs.get("day_report_status")
        )

    def test_non_cashier_cannot_open_page(self):
        other_user = User.objects.create_user(
            username="830002", password=self.PASSWORD, is_active=True
        )
        EmployeeProfile.objects.create(
            user=other_user,
            employee_id="830002",
            phone_country_code="+254",
            phone_number="700000004",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.EMPLOYEE,
        )
        client = Client()
        client.post(
            reverse("employees:login"),
            {"username": "830002", "password": self.PASSWORD},
        )
        response = client.get("/shop-cashier/settings/")
        self.assertEqual(response.status_code, 302)
