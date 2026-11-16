"""Day-summary money figures on the cashier dashboard."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import (
    Expense,
    ExpenseCategory,
    ExpensePaymentStatus,
    Shop,
    ShopDaySession,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptStatus,
)
from shops.services import (
    _invalidate_pos_settings_cache,
    complete_shop_checkout,
    day_sales_status_summary,
    get_open_shop_day,
    open_shop_day,
)


class DaySalesStatusSummaryTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="200401",
            password="pass",
            email="summary@test.local",
            first_name="Summary",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200401",
            phone_country_code="+254",
            phone_number="700000401",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Summary Shop",
            location="Nairobi",
            email="summary-shop@test.local",
            phone_number="0700000401",
            login_code="200401",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Summary Item",
            category="Parts",
            description="Summary fixture",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "200401",
            },
        )
        self.session = get_open_shop_day(self.shop)

    def _sale_payload(self, **extra):
        payload = {
            "kind": ShopReceiptKind.SALE,
            "payment_method": ShopPaymentMethod.CASH,
            "cash_amount": "100",
            "mpesa_amount": "0",
            "login_code": "200401",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }
        payload.update(extra)
        return payload

    def _method(self, summary, key):
        return next(row for row in summary["methods"] if row["key"] == key)

    def test_pending_and_confirmed_totals_with_method_split(self):
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(),
        )
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(
                payment_method=ShopPaymentMethod.BOTH,
                cash_amount="50",
                mpesa_amount="150",
                lines=[{"id": self.item.pk, "qty": 2, "price": "100"}],
                confirm=True,
            ),
        )

        summary = day_sales_status_summary(self.session)
        self.assertEqual(summary["pending_total"], Decimal("100"))
        self.assertEqual(summary["confirmed_total"], Decimal("200"))
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["confirmed_count"], 1)
        self.assertEqual(summary["sales_total"], Decimal("300"))

        cash = self._method(summary, "cash")
        self.assertEqual(cash["pending"], Decimal("100"))
        self.assertEqual(cash["confirmed"], Decimal("50"))
        self.assertEqual(cash["total"], Decimal("150"))
        mpesa = self._method(summary, "mpesa")
        self.assertEqual(mpesa["pending"], Decimal("0"))
        self.assertEqual(mpesa["confirmed"], Decimal("150"))
        self.assertEqual(mpesa["total"], Decimal("150"))
        card = self._method(summary, "card")
        self.assertEqual(card["total"], Decimal("0"))

    def test_expenses_reduce_revenue_drawings_excluded(self):
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(confirm=True),
        )
        Expense.objects.create(
            shop=self.shop,
            category=ExpenseCategory.TRANSPORT,
            name="Fuel",
            amount=Decimal("40"),
            amount_paid=Decimal("40"),
            payment_status=ExpensePaymentStatus.PAID,
            created_by=self.profile,
        )
        Expense.objects.create(
            shop=self.shop,
            category=ExpenseCategory.MISC,
            name="Supplies on credit",
            amount=Decimal("70"),
            amount_paid=Decimal("0"),
            payment_status=ExpensePaymentStatus.UNPAID,
            created_by=self.profile,
        )
        Expense.objects.create(
            shop=self.shop,
            category=ExpenseCategory.OWNER_DRAWINGS,
            name="Owner take",
            amount=Decimal("999"),
            amount_paid=Decimal("999"),
            payment_status=ExpensePaymentStatus.PAID,
            created_by=self.profile,
        )

        summary = day_sales_status_summary(self.session)
        self.assertEqual(summary["sales_total"], Decimal("100"))
        self.assertEqual(summary["expenses_paid"], Decimal("40"))
        self.assertEqual(summary["revenue"], Decimal("60"))

    def test_out_of_window_and_cancelled_sales_excluded(self):
        result = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(),
        )
        ShopReceipt.objects.filter(pk=result["receipt"].pk).update(
            created_at=self.session.opened_at - timedelta(hours=2)
        )
        cancelled = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(),
        )["receipt"]
        ShopReceipt.objects.filter(pk=cancelled.pk).update(
            status=ShopReceiptStatus.CANCELLED
        )

        summary = day_sales_status_summary(self.session)
        self.assertEqual(summary["pending_total"], Decimal("0"))
        self.assertEqual(summary["sales_total"], Decimal("0"))
        self.assertEqual(summary["revenue"], Decimal("0"))

    def test_no_session_returns_empty(self):
        self.assertEqual(day_sales_status_summary(None), {})


class CashierDaySummaryContextTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="200402",
            password="pass",
            email="summary2@test.local",
            first_name="Dash",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200402",
            phone_country_code="+254",
            phone_number="700000402",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Dash Shop",
            location="Nairobi",
            email="dash-shop@test.local",
            phone_number="0700000402",
            login_code="200402",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.other_shop = Shop.objects.create(
            name="Other Shop",
            location="Nakuru",
            email="other-shop@test.local",
            phone_number="0700000403",
            login_code="200403",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop, self.other_shop)
        self.item = Item.objects.create(
            name="Dash Item",
            category="Parts",
            description="Dash fixture",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )

    def _open_day_and_sell(self):
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "200402",
            },
        )
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.SALE,
                "payment_method": ShopPaymentMethod.CASH,
                "cash_amount": "100",
                "mpesa_amount": "0",
                "login_code": "200402",
                "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
            },
        )
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.SALE,
                "payment_method": ShopPaymentMethod.MPESA,
                "cash_amount": "0",
                "mpesa_amount": "200",
                "login_code": "200402",
                "lines": [{"id": self.item.pk, "qty": 2, "price": "100"}],
                "confirm": True,
            },
        )

    def test_context_lists_only_assigned_shops(self):
        from django.test import RequestFactory

        from employees.workspace import cashier_day_summaries_context

        third = Shop.objects.create(
            name="Unassigned Shop",
            location="Eldoret",
            email="unassigned-shop@test.local",
            phone_number="0700000404",
            login_code="200404",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        request = RequestFactory().get("/shop-cashier/")
        context = cashier_day_summaries_context(request, self.profile)
        rows = context["cashier_day_summaries"]
        shop_ids = {row["shop"].pk for row in rows}
        self.assertEqual(shop_ids, {self.shop.pk, self.other_shop.pk})
        self.assertNotIn(third.pk, shop_ids)
        self.assertIsNone(rows[0]["session"])
        self.assertEqual(rows[0]["summary"], {})

    def test_status_filter_picks_bucket(self):
        from django.test import RequestFactory

        from employees.workspace import cashier_day_summaries_context

        self._open_day_and_sell()
        factory = RequestFactory()

        context = cashier_day_summaries_context(
            factory.get("/shop-cashier/", {"status": "pending"}), self.profile
        )
        row = next(
            r for r in context["cashier_day_summaries"]
            if r["shop"].pk == self.shop.pk
        )
        self.assertEqual(context["cashier_sales_status"], "pending")
        self.assertEqual(row["display"]["sales_total"], Decimal("100"))
        self.assertEqual(row["display"]["sales_count"], 1)
        amounts = {m["key"]: m["amount"] for m in row["display"]["methods"]}
        self.assertEqual(amounts, {"cash": 100, "mpesa": 0, "card": 0})

        context = cashier_day_summaries_context(
            factory.get("/shop-cashier/", {"status": "confirmed"}), self.profile
        )
        row = next(
            r for r in context["cashier_day_summaries"]
            if r["shop"].pk == self.shop.pk
        )
        self.assertEqual(row["display"]["sales_total"], Decimal("200"))
        amounts = {m["key"]: m["amount"] for m in row["display"]["methods"]}
        self.assertEqual(amounts["mpesa"], Decimal("200"))

        context = cashier_day_summaries_context(
            factory.get("/shop-cashier/", {"status": "bogus"}), self.profile
        )
        self.assertEqual(context["cashier_sales_status"], "all")
        row = next(
            r for r in context["cashier_day_summaries"]
            if r["shop"].pk == self.shop.pk
        )
        self.assertEqual(row["display"]["sales_total"], Decimal("300"))
        self.assertEqual(row["display"]["sales_count"], 2)

    def test_confirm_receipts_sidebar_has_return_receipt_link(self):
        from employees.models import EmployeeModulePermission
        from employees.workspace import (
            analytics_return_receipts_url,
            sidebar_for_analytics_section,
        )

        sidebar = sidebar_for_analytics_section(
            self.profile.role,
            active_view="confirm-receipts",
            profile=self.profile,
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertIn("Return receipt", labels)
        link = next(
            item for item in sidebar["primary"]
            if item["label"] == "Return receipt"
        )
        self.assertEqual(
            link["href"], analytics_return_receipts_url(self.profile.role)
        )
        self.assertFalse(link["active"])

        EmployeeModulePermission.objects.create(
            employee=self.profile,
            module_slug="analytics",
            submodule_slug="return-receipts",
            allowed=False,
        )
        # Permission rows are cached on the profile — drop the cached lookup.
        delattr(self.profile, "_employee_module_perm_cache")
        sidebar = sidebar_for_analytics_section(
            self.profile.role,
            active_view="confirm-receipts",
            profile=self.profile,
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Return receipt", labels)

    def test_dashboard_page_renders_day_summary_and_filter(self):
        from django.urls import reverse

        self._open_day_and_sell()

        self.client.force_login(self.user)
        url = reverse("employees:role_shop_cashier")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Day summary")
        self.assertContains(response, "Dash Shop")
        self.assertContains(response, "KSh 300")
        self.assertContains(response, "?status=pending")

        response = self.client.get(url, {"status": "confirmed"})
        self.assertContains(response, "Confirmed sales")
        self.assertContains(response, "KSh 200")

        response = self.client.get(url, {"status": "pending"})
        self.assertContains(response, "Pending sales")
        self.assertContains(response, "KSh 100")
