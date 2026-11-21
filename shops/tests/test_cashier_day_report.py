"""Cashier day report/history is scoped to the sessions that cashier opened.

/my-shop/<id>/day/report/ shows the day the acting cashier opened → closed —
a colleague's session never fills the report, and ?session=<id> only picks
one of the cashier's own days. Portal sessions and non-cashier roles keep the
shop-wide view (open session, else last closed).
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from decimal import Decimal

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import (
    Expense,
    ExpenseCategory,
    ExpensePaymentStatus,
    Shop,
)
from shops.services import close_shop_day, open_shop_day


def _employee(username, *, role, first_name, last_name, password):
    user = User.objects.create_user(
        username=username,
        password=password,
        email=f"{username}@test.local",
        first_name=first_name,
        last_name=last_name,
        is_active=True,
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=username,
        phone_country_code="+254",
        phone_number="700001001",
        status=EmployeeStatus.ACTIVE,
        role=role,
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
class CashierDayReportScopeTests(TestCase):
    PASSWORD = "day-report-pass"

    def setUp(self):
        self.previous = _employee(
            "870001",
            role=EmployeeRole.SHOP_CASHIER,
            first_name="PREVIOUS",
            last_name="CASHIER",
            password=self.PASSWORD,
        )
        self.next = _employee(
            "870002",
            role=EmployeeRole.SHOP_CASHIER,
            first_name="NEXT",
            last_name="CASHIER",
            password=self.PASSWORD,
        )
        self.manager = _employee(
            "870003",
            role=EmployeeRole.SHOP_MANAGER,
            first_name="SHOP",
            last_name="MANAGER",
            password=self.PASSWORD,
        )
        self.shop = Shop.objects.create(
            name="REPORT SHOP",
            location="NAIROBI",
            email="report-shop@test.local",
            phone_number="0700008701",
            login_code="880870",
            password_hash=make_password(self.PASSWORD),
            created_by=self.previous,
        )
        for profile in (self.previous, self.next, self.manager):
            profile.assigned_shops.add(self.shop)

    def _login(self, profile, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:login"),
            {"username": profile.employee_id, "password": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _login_shop(self, client=None):
        client = client or Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _open_day(self, profile):
        return open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": profile.employee_id,
            },
        )["session"]

    def _close_day(self, profile):
        close_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "card_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": profile.employee_id,
            },
        )

    def _report_url(self, session=None):
        url = reverse(
            "employees:my_shop_day_report", kwargs={"shop_id": self.shop.pk}
        )
        if session is not None:
            url = f"{url}?session={session.pk}"
        return url

    def _report_session(self, client, url):
        """GET the report PDF with the builder mocked; return its session."""
        sep = "&" if "?" in url else "?"
        with patch(
            "shops.views.build_shop_day_report_pdf",
            return_value=b"%PDF-fake",
        ) as pdf:
            response = client.get(f"{url}{sep}download=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(pdf.called)
        return pdf.call_args.kwargs["session"]

    def test_report_uses_cashier_own_session(self):
        """Shop-latest session is a colleague's — report still picks mine."""
        own = self._open_day(self.next)
        self._close_day(self.next)
        colleague = self._open_day(self.previous)
        self._close_day(self.previous)

        client = self._login(self.next)
        session = self._report_session(client, self._report_url())
        self.assertEqual(session.pk, own.pk)
        self.assertNotEqual(session.pk, colleague.pk)

    def test_report_live_preview_of_own_open_session(self):
        own = self._open_day(self.next)
        client = self._login(self.next)
        session = self._report_session(client, self._report_url())
        self.assertEqual(session.pk, own.pk)

    def test_report_without_own_session_redirects_to_open(self):
        self._open_day(self.previous)
        self._close_day(self.previous)
        client = self._login(self.next)
        response = client.get(self._report_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "employees:my_shop_day_toggle",
                kwargs={"shop_id": self.shop.pk},
            ),
        )

    def test_session_param_scoped_to_cashier(self):
        colleague = self._open_day(self.previous)
        self._close_day(self.previous)
        client = self._login(self.next)
        response = client.get(self._report_url(session=colleague))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "employees:my_shop_day_history",
                kwargs={"shop_id": self.shop.pk},
            ),
        )

    def test_session_param_accepts_own_session(self):
        own = self._open_day(self.next)
        self._close_day(self.next)
        self._open_day(self.previous)
        self._close_day(self.previous)
        client = self._login(self.next)
        session = self._report_session(client, self._report_url(session=own))
        self.assertEqual(session.pk, own.pk)

    def test_default_get_pins_session_then_renders_page(self):
        """No ?session= → redirect pins the resolved day, then the page renders."""
        own = self._open_day(self.next)
        client = self._login(self.next)

        response = client.get(self._report_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f"{self._report_url()}?session={own.pk}"
        )

        response = client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "text/html; charset=utf-8"
        )
        page = response.context["page"]
        self.assertTrue(page["receipts_print_modal"])
        self.assertEqual(
            page["receipts_print_url"],
            reverse(
                "employees:my_shop_day_report",
                kwargs={"shop_id": self.shop.pk},
            ),
        )

    def test_paper_param_renders_print_sheet(self):
        own = self._open_day(self.next)
        client = self._login(self.next)
        response = client.get(f"{self._report_url(session=own)}&paper=58")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="paper-58"')

    def test_report_lists_credit_and_gift_not_in_till(self):
        """Credit sales and gift issues show as not-in-till lines."""
        from decimal import Decimal

        from shops.models import (
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptStatus,
        )

        session = self._open_day(self.next)
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="CR-TEST-1",
            kind=ShopReceiptKind.CREDIT,
            status=ShopReceiptStatus.CONFIRMED,
            total=Decimal("5000.00"),
            amount_paid=Decimal("1200.00"),
            created_by=self.next,
        )
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="GF-TEST-1",
            kind=ShopReceiptKind.GIFT,
            status=ShopReceiptStatus.CONFIRMED,
            total=Decimal("800.00"),
            created_by=self.next,
        )

        client = self._login(self.next)
        response = client.get(self._report_url(session=session))
        self.assertEqual(response.status_code, 200)
        tables = response.context["page"]["tables"]
        not_till = next(
            t for t in tables if "not in the till" in t["title"]
        )
        credit_row, gift_row = not_till["rows"]
        self.assertEqual(credit_row[0], "Credit sales")
        self.assertEqual(credit_row[1], "1")
        self.assertIn("5,000", credit_row[2])
        self.assertIn("1,200", credit_row[3])
        self.assertIn("3,800", credit_row[4])
        self.assertEqual(gift_row[0], "Gift items")
        self.assertEqual(gift_row[1], "1")
        self.assertIn("800", gift_row[2])
        self.assertEqual(gift_row[3], "—")
        self.assertEqual(gift_row[4], "—")
        self.assertIn("Expected in till", not_till["footnote"])
        self.assertIn("Credit slips counted", not_till["footnote"])

    def test_money_flow_shows_shop_activated_mpesa(self):
        """A shop-level M-Pesa override survives the company flag strip."""
        from shops.services import (
            _invalidate_pos_settings_cache,
            get_company_pos_settings,
            get_shop_pos_settings,
        )

        company = get_company_pos_settings()
        company.enable_mpesa = False
        company.enable_cash_mpesa = False
        company.enable_mpesa_card = False
        company.enable_cash_mpesa_card = False
        company.save(
            update_fields=[
                "enable_mpesa",
                "enable_cash_mpesa",
                "enable_mpesa_card",
                "enable_cash_mpesa_card",
            ]
        )
        shop_row = get_shop_pos_settings(self.shop)
        shop_row.override_pos = True
        shop_row.enable_mpesa = True
        shop_row.custom_pos_fields = ["enable_mpesa"]
        shop_row.save(
            update_fields=[
                "override_pos",
                "enable_mpesa",
                "custom_pos_fields",
            ]
        )
        _invalidate_pos_settings_cache(shop_id=self.shop.pk)
        self.addCleanup(
            _invalidate_pos_settings_cache, shop_id=self.shop.pk
        )

        session = self._open_day(self.next)
        client = self._login(self.next)
        response = client.get(self._report_url(session=session))
        self.assertEqual(response.status_code, 200)
        tables = response.context["page"]["tables"]
        money = next(t for t in tables if t["title"] == "Money flow")
        labels = [
            (c.get("label") if isinstance(c, dict) else c)
            for c in money["columns"]
        ]
        self.assertEqual(labels, ["Flow", "Cash", "M-Pesa", "Total"])

    def test_money_flow_shows_disabled_channel_with_money(self):
        """A channel with actual figures stays visible even when off."""
        from decimal import Decimal

        from shops.models import (
            ShopReceipt,
            ShopReceiptKind,
            ShopReceiptStatus,
        )
        from shops.services import (
            _invalidate_pos_settings_cache,
            get_company_pos_settings,
        )

        company = get_company_pos_settings()
        company.enable_mpesa = False
        company.enable_cash_mpesa = False
        company.enable_mpesa_card = False
        company.enable_cash_mpesa_card = False
        company.save(
            update_fields=[
                "enable_mpesa",
                "enable_cash_mpesa",
                "enable_mpesa_card",
                "enable_cash_mpesa_card",
            ]
        )
        _invalidate_pos_settings_cache(shop_id=self.shop.pk)
        self.addCleanup(
            _invalidate_pos_settings_cache, shop_id=self.shop.pk
        )

        session = self._open_day(self.next)
        ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="SALE-MPESA-1",
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.CONFIRMED,
            total=Decimal("300.00"),
            amount_paid=Decimal("300.00"),
            mpesa_amount=Decimal("300.00"),
            created_by=self.next,
        )

        client = self._login(self.next)
        response = client.get(self._report_url(session=session))
        self.assertEqual(response.status_code, 200)
        tables = response.context["page"]["tables"]
        money = next(t for t in tables if t["title"] == "Money flow")
        labels = [
            (c.get("label") if isinstance(c, dict) else c)
            for c in money["columns"]
        ]
        self.assertIn("M-Pesa", labels)

    def test_foreign_open_session_still_blocks(self):
        self._open_day(self.previous)
        client = self._login(self.next)
        response = client.get(self._report_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("employees:role_shop_cashier"))

    def test_manager_sees_shop_latest_session(self):
        own = self._open_day(self.next)
        self._close_day(self.next)
        colleague = self._open_day(self.previous)
        self._close_day(self.previous)
        client = self._login(self.manager)
        session = self._report_session(client, self._report_url())
        self.assertEqual(session.pk, colleague.pk)
        self.assertNotEqual(session.pk, own.pk)

    def test_portal_sees_shop_latest_session(self):
        own = self._open_day(self.next)
        self._close_day(self.next)
        colleague = self._open_day(self.previous)
        self._close_day(self.previous)
        client = self._login_shop()
        session = self._report_session(client, self._report_url())
        self.assertEqual(session.pk, colleague.pk)
        self.assertNotEqual(session.pk, own.pk)

    def test_employee_section_prefs_apply_with_portal_session(self):
        """Employee + portal session in one browser: the portal branch of
        _require_allocated_shop_access resolves profile=None, but the
        employee's day-report section picks must still hide tables."""
        session = self._open_day(self.next)
        self.next.sidebar_prefs = {"day_report_sec_item_stock": False}
        self.next.save(update_fields=["sidebar_prefs"])

        client = self._login(self.next)
        portal = client.session
        portal["shop_portal_auth"] = True
        portal["shop_portal_shop_id"] = str(self.shop.pk)
        portal.save()

        response = client.get(self._report_url(session=session))
        self.assertEqual(response.status_code, 200)
        titles = [t["title"] for t in response.context["page"]["tables"]]
        self.assertNotIn("Stocked in", titles)
        self.assertNotIn("Stocked out", titles)
        self.assertIn("Money flow", titles)

    def test_portal_view_shows_all_sections(self):
        """A portal-only visitor has no employee prefs — every section stays."""
        session = self._open_day(self.next)
        client = self._login_shop()
        response = client.get(self._report_url(session=session))
        self.assertEqual(response.status_code, 200)
        titles = [t["title"] for t in response.context["page"]["tables"]]
        self.assertIn("Stocked in", titles)
        self.assertIn("Stocked out", titles)

    def test_history_lists_only_cashier_own_days(self):
        own = self._open_day(self.next)
        self._close_day(self.next)
        colleague = self._open_day(self.previous)
        self._close_day(self.previous)

        url = reverse(
            "employees:my_shop_day_history", kwargs={"shop_id": self.shop.pk}
        )
        client = self._login(self.next)
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        session_ids = {
            row["session"].pk for row in response.context["day_sessions"]
        }
        self.assertEqual(session_ids, {own.pk})

        manager = self._login(self.manager)
        response = manager.get(url)
        self.assertEqual(response.status_code, 200)
        session_ids = {
            row["session"].pk for row in response.context["day_sessions"]
        }
        self.assertEqual(session_ids, {own.pk, colleague.pk})

    def _expense_in_session(self):
        return Expense.objects.create(
            shop=self.shop,
            category=ExpenseCategory.TRANSPORT,
            name="Fuel",
            amount=Decimal("40"),
            amount_paid=Decimal("40"),
            payment_status=ExpensePaymentStatus.PAID,
            created_by=self.next,
        )

    def _expense_time_cell(self, client, session):
        response = client.get(self._report_url(session=session))
        self.assertEqual(response.status_code, 200)
        table = next(
            t
            for t in response.context["page"]["tables"]
            if t["title"] == "Expenses incurred"
        )
        return table["columns"][0], table["rows"][0][0]

    def test_dates_toggle_adds_date_to_row_times(self):
        """The opt-in "Dates" pick renders row times as date · time."""
        session = self._open_day(self.next)
        self._expense_in_session()
        self.next.sidebar_prefs = {"day_report_sec_dates": True}
        self.next.save(update_fields=["sidebar_prefs"])

        client = self._login(self.next)
        column, cell = self._expense_time_cell(client, session)
        self.assertEqual(column, "Date · time")
        self.assertRegex(cell, r"^\d{2} \w{3} · \d{2}:\d{2}$")

    def test_dates_default_off_keeps_time_only(self):
        """No pref stored — rows keep the plain HH:MM time."""
        session = self._open_day(self.next)
        self._expense_in_session()

        client = self._login(self.next)
        column, cell = self._expense_time_cell(client, session)
        self.assertEqual(column, "Time")
        self.assertRegex(cell, r"^\d{2}:\d{2}$")
