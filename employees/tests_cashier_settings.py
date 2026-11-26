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
from shops.session import SESSION_SHOP_KEY
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
                "sidebar_credits",
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

    def test_company_default_applies_until_cashier_picks_own(self):
        from employees.workspace import set_cashier_default_pref

        # No own pick → the company default drives the dashboard link.
        set_cashier_default_pref("expenses", False)
        self.assertNotIn("Expenses", self._sidebar_labels())

        response = self.client.get("/shop-cashier/settings/")
        self.assertEqual(response.status_code, 200)
        toggles = {
            toggle["field"]: toggle["enabled"]
            for toggle in response.context["sidebar_toggles"]
        }
        self.assertFalse(toggles["sidebar_expenses"])

        # The cashier's own pick wins over the company default.
        self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_expenses",
                "enabled": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
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
                "sell_gift",
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

    def test_credits_link_opens_credits_workspace(self):
        response = self.client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(response.status_code, 200)
        links = {
            item["label"]: item
            for item in response.context["page_sidebar"]["primary"]
            if item.get("type") == "link"
        }
        self.assertIn("Credits", links)
        self.assertEqual(
            links["Credits"]["href"], "/shop-cashier/analytics/credits/"
        )
        for url in (
            "/shop-cashier/analytics/credits/",
            "/shop-cashier/analytics/credits/clients/",
            "/shop-cashier/analytics/credits/follow-ups/",
            "/shop-cashier/analytics/credits/report/",
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_credits_pref_toggle_hides_link(self):
        self.assertIn("Credits", self._sidebar_labels())
        self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "sidebar_credits",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertNotIn("Credits", self._sidebar_labels())

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

    def test_day_report_section_toggle_persists(self):
        from employees.workspace import (
            DAY_REPORT_SECTION_TOGGLES,
            DAY_REPORT_SECTIONS_OFF_BY_DEFAULT,
        )

        response = self.client.get("/shop-cashier/settings/")
        toggles = response.context["day_report_section_toggles"]
        self.assertEqual(
            {toggle["field"] for toggle in toggles},
            {key for key, _label in DAY_REPORT_SECTION_TOGGLES},
        )
        # Section toggles default on; opt-in display picks default off.
        self.assertTrue(
            all(
                toggle["enabled"]
                for toggle in toggles
                if toggle["field"] not in DAY_REPORT_SECTIONS_OFF_BY_DEFAULT
            )
        )
        self.assertTrue(
            all(
                not toggle["enabled"]
                for toggle in toggles
                if toggle["field"] in DAY_REPORT_SECTIONS_OFF_BY_DEFAULT
            )
        )

        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "day_report_sec_expenses",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content),
            {
                "ok": True,
                "field": "day_report_sec_expenses",
                "enabled": False,
            },
        )
        self.cashier.refresh_from_db()
        self.assertEqual(
            self.cashier.sidebar_prefs.get("day_report_sec_expenses"), False
        )
        response = self.client.get("/shop-cashier/settings/")
        enabled = {
            toggle["field"]
            for toggle in response.context["day_report_section_toggles"]
            if toggle["enabled"]
        }
        self.assertNotIn("day_report_sec_expenses", enabled)

    def test_day_report_dates_toggle_opts_in(self):
        """The opt-in "Dates" pick turns on via the same toggle POST."""
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "day_report_sec_dates",
                "enabled": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content),
            {
                "ok": True,
                "field": "day_report_sec_dates",
                "enabled": True,
            },
        )
        self.cashier.refresh_from_db()
        self.assertEqual(
            self.cashier.sidebar_prefs.get("day_report_sec_dates"), True
        )
        response = self.client.get("/shop-cashier/settings/")
        enabled = {
            toggle["field"]
            for toggle in response.context["day_report_section_toggles"]
            if toggle["enabled"]
        }
        self.assertIn("day_report_sec_dates", enabled)

    def test_day_report_section_rejects_unknown_field(self):
        response = self.client.post(
            "/shop-cashier/settings/",
            {
                "action": "toggle_pos_setting",
                "field": "day_report_sec_bogus",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.cashier.refresh_from_db()
        self.assertIsNone(
            self.cashier.sidebar_prefs.get("day_report_sec_bogus")
        )

    def _unlock_floor(self):
        session = self.client.session
        session[SESSION_SHOP_KEY] = str(self.shop.pk)
        session.save()

    def test_floor_footer_shows_cashier_settings_not_shop_settings(self):
        self._unlock_floor()
        response = self.client.get(
            reverse(
                "employees:my_shop_workspace",
                kwargs={"shop_id": self.shop.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        footer = {
            item["label"]: item
            for item in response.context["page_sidebar"]["footer"]
            if item.get("type") == "link"
        }
        self.assertIn("Cashier settings", footer)
        self.assertNotIn("Shop settings", footer)
        self.assertEqual(
            footer["Cashier settings"]["href"], "/shop-cashier/settings/"
        )

    def test_shop_settings_pages_redirect_to_cashier_settings(self):
        self._unlock_floor()
        for name in (
            "employees:my_shop_settings",
            "employees:my_shop_settings_theme",
            "employees:my_shop_settings_pos",
            "employees:my_shop_settings_receipt",
            "employees:my_shop_settings_messages",
        ):
            response = self.client.get(
                reverse(name, kwargs={"shop_id": self.shop.pk})
            )
            self.assertEqual(response.status_code, 302, name)
            self.assertEqual(
                response["Location"], "/shop-cashier/settings/"
            )

    def test_shop_portal_keeps_shop_settings(self):
        from shops.session import SESSION_SHOP_PORTAL_SHOP_KEY

        client = Client()
        session = client.session
        session[SESSION_SHOP_PORTAL_SHOP_KEY] = str(self.shop.pk)
        session["shop_portal_auth"] = True
        session.save()
        response = client.get(
            reverse("employees:my_shop_settings", kwargs={"shop_id": self.shop.pk})
        )
        self.assertEqual(response.status_code, 200)

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


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class BalancesCashierSettingsTests(TestCase):
    """?mode=settings on /…/analytics/balances/ — company cashier session
    options (open & close, shop switching, idle sign-out) plus company
    defaults for the per-cashier picks (dashboard sidebar, Sell popup, day
    report status/sections), editable from the Cashiers analytics page
    under the settings/company-pos permission. A cashier's own
    sidebar_prefs entry still wins over the company default."""

    PASSWORD = "balances-settings-pass"
    BALANCES_URL = "/it-support/analytics/balances/"

    def setUp(self):
        self.user = User.objects.create_user(
            username="830100",
            password=self.PASSWORD,
            email="830100@test.local",
            first_name="IT",
            last_name="SUPPORT",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="830100",
            phone_country_code="+254",
            phone_number="700000100",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="BALANCES SHOP",
            location="NAIROBI",
            email="balances-shop@test.local",
            phone_number="0700003100",
            login_code="870310",
            password_hash="x",
            created_by=self.staff,
        )
        set_working_hours_enabled(enabled=False)
        self.client = Client()
        self.client.post(
            reverse("employees:login"),
            {"username": "830100", "password": self.PASSWORD},
        )

    def test_cashiers_page_links_to_settings_mode(self):
        response = self.client.get(self.BALANCES_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cashier settings")
        self.assertContains(response, f"{self.BALANCES_URL}?mode=settings")

    def test_settings_mode_lists_cashier_groups(self):
        response = self.client.get(f"{self.BALANCES_URL}?mode=settings")
        self.assertEqual(response.status_code, 200)
        page = response.context["page"]
        self.assertTrue(page["cashier_settings_mode"])
        self.assertEqual(page["back_label"], "Sessions")
        self.assertIn(self.BALANCES_URL, page["back_href"])
        groups = {group["title"]: group for group in page["settings_groups"]}
        self.assertEqual(
            set(groups),
            {
                "Dashboard sidebar",
                "Sell popup",
                "Day report",
                "Day report sections",
                "Open & close",
                "Shop switching",
                "Session timeout",
            },
        )
        fields = {
            toggle["field"]
            for group in groups.values()
            for toggle in group["toggles"]
        }
        self.assertEqual(
            fields,
            {
                "enable_open_close",
                "require_clock_in",
                "enable_passwordless_shop_switch",
                "enable_employee_session_timeout",
                "enable_shop_session_timeout",
                "sidebar_sell",
                "sidebar_open_close",
                "sidebar_drawings",
                "sidebar_day_report",
                "sidebar_day_history",
                "sidebar_expenses",
                "sidebar_receipts",
                "sidebar_credits",
                "sidebar_analytics",
                "sidebar_buy_stock",
                "sidebar_suppliers",
                "sell_sale",
                "sell_credit",
                "sell_quotation",
                "sell_gift",
                "sell_pay_cash",
                "sell_pay_mpesa",
                "sell_pay_card",
                "sell_edit_prices",
                "day_report_status_all",
                "day_report_status_pending",
                "day_report_status_confirmed",
                "day_report_sec_sales_profit",
                "day_report_sec_money_flow",
                "day_report_sec_prepayments",
                "day_report_sec_cleared",
                "day_report_sec_credit_payments",
                "day_report_sec_not_till",
                "day_report_sec_gift",
                "day_report_sec_items_sold",
                "day_report_sec_sellers",
                "day_report_sec_flagged",
                "day_report_sec_drawings",
                "day_report_sec_expenses",
                "day_report_sec_item_stock",
                "day_report_sec_shelf_stock",
                "day_report_sec_dates",
            },
        )
        self.assertContains(response, "Compulsory open")
        self.assertContains(response, "Switch shops without password")
        self.assertContains(response, "Employee session")
        self.assertContains(response, "Dashboard sidebar")
        self.assertContains(response, "Sell popup")
        self.assertContains(response, "Pending receipts only")

    def test_toggle_updates_company_pos_row(self):
        response = self.client.post(
            self.BALANCES_URL,
            {
                "action": "toggle_pos_setting",
                "field": "enable_passwordless_shop_switch",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["ok"])
        self.assertFalse(data["enabled"])
        self.assertFalse(
            get_company_pos_settings().enable_passwordless_shop_switch
        )

    def test_toggle_rejects_fields_off_the_panel(self):
        response = self.client.post(
            self.BALANCES_URL,
            {
                "action": "toggle_pos_setting",
                "field": "enable_tax",
                "enabled": "1",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)

    def test_session_timeout_minutes_save(self):
        response = self.client.post(
            self.BALANCES_URL,
            {
                "action": "set_employee_session_timeout",
                "employee_timeout_minutes": "45",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            get_company_pos_settings().employee_session_timeout_minutes,
            45,
        )

    def test_open_close_mode_and_times_save(self):
        response = self.client.post(
            self.BALANCES_URL,
            {"action": "set_open_close_mode", "mode": "set"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_company_working_hours_settings().enabled)

        response = self.client.post(
            self.BALANCES_URL,
            {
                "action": "set_open_close_times",
                f"shop_{self.shop.pk}_start_time": "08:15",
                f"shop_{self.shop.pk}_end_time": "19:30",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        from shops.models import ShopWorkingHoursSettings

        hours = ShopWorkingHoursSettings.objects.get(shop=self.shop)
        self.assertEqual(str(hours.start_time)[:5], "08:15")
        self.assertEqual(str(hours.end_time)[:5], "19:30")

    def test_settings_denied_without_company_pos_permission(self):
        from employees.models import EmployeeModulePermission

        EmployeeModulePermission.objects.create(
            employee=self.staff,
            module_slug="settings",
            submodule_slug="company-pos",
            allowed=False,
        )
        response = self.client.post(
            self.BALANCES_URL,
            {
                "action": "toggle_pos_setting",
                "field": "enable_open_close",
                "enabled": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.get(f"{self.BALANCES_URL}?mode=settings")
        self.assertEqual(response.status_code, 302)

        # …and the header shortcut drops off the sessions list.
        response = self.client.get(self.BALANCES_URL)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Cashier settings")

    def test_post_on_other_sections_returns_405(self):
        response = self.client.post(
            "/it-support/analytics/sales/",
            {"action": "toggle_pos_setting"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 405)

    def _post_toggle(self, field, enabled="1"):
        return self.client.post(
            self.BALANCES_URL,
            {
                "action": "toggle_pos_setting",
                "field": field,
                "enabled": enabled,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_sidebar_default_toggle_writes_company_pref(self):
        from employees.workspace import (
            cashier_sidebar_pref_enabled,
            set_cashier_sidebar_pref,
        )

        response = self._post_toggle("sidebar_suppliers", "0")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.content)["enabled"])
        self.assertFalse(
            get_company_pos_settings().cashier_default_prefs["suppliers"]
        )
        # An employee without their own pick follows the company default.
        self.assertFalse(
            cashier_sidebar_pref_enabled(self.staff, "suppliers")
        )
        # …but an employee's own pick still wins.
        set_cashier_sidebar_pref(self.staff, "suppliers", True)
        self.assertTrue(cashier_sidebar_pref_enabled(self.staff, "suppliers"))

    def test_sell_default_toggle_writes_company_pref(self):
        from employees.workspace import (
            cashier_sell_pref_enabled,
            sell_payment_methods,
        )

        response = self._post_toggle("sell_pay_card", "0")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            get_company_pos_settings().cashier_default_prefs["sell_pay_card"]
        )
        self.assertFalse(
            cashier_sell_pref_enabled(self.staff, "sell_pay_card")
        )
        methods = sell_payment_methods(self.staff)
        self.assertNotIn("card", methods)
        self.assertNotIn("cash_card", methods)

    def test_day_report_default_status_pick(self):
        from employees.workspace import (
            cashier_default_day_report_status,
            day_report_status_mode,
        )

        response = self._post_toggle("day_report_status_pending")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["stock_mode"], "day_report_status_pending")
        self.assertEqual(cashier_default_day_report_status(), "pending")
        self.assertEqual(day_report_status_mode(self.staff), "pending")
        # Turning a one-of pick off is rejected — pick another instead.
        response = self._post_toggle("day_report_status_pending", "0")
        self.assertEqual(response.status_code, 400)

    def test_day_report_section_default_toggle(self):
        from employees.workspace import day_report_section_enabled

        response = self._post_toggle("day_report_sec_drawings", "0")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            day_report_section_enabled(
                self.staff, "day_report_sec_drawings"
            )
        )
        # Opt-in sections can be turned on company-wide too.
        response = self._post_toggle("day_report_sec_dates")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            day_report_section_enabled(self.staff, "day_report_sec_dates")
        )

    def test_pref_fields_reject_unknown(self):
        for field in (
            "sidebar_nope",
            "sell_nope",
            "day_report_sec_nope",
            "day_report_status_nope",
        ):
            response = self._post_toggle(field)
            self.assertEqual(response.status_code, 400, field)
