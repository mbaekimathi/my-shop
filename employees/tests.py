import json

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from employees.analytics_services import ANALYTICS_SECTIONS, _build_clients
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from employees.permissions_catalog import PERMISSION_MODULE_BY_SLUG
from employees.module_permissions import (
    employee_may_any,
    profile_has_shop_allocation,
    require_module_permission,
)
from employees.workspace import (
    DASHBOARD_MODULES,
    HR_SIDEBAR_SECTIONS,
    SETTINGS_NESTED_SECTIONS,
    SETTINGS_SECTIONS,
    sidebar_for_analytics,
    sidebar_for_analytics_section,
    sidebar_for_marketing_links,
    sidebar_for_my_shop,
    sidebar_for_role_dashboard,
    sidebar_for_settings,
    sidebar_for_stock,
    sidebar_for_super_admin,
    sidebar_for_suppliers,
)
from items.services import actionable_shops_for_profile
from shops.models import (
    Client,
    CompanyPosSettings,
    Shop,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)


class SupplierSidebarTests(TestCase):
    def test_suppliers_sidebar_lists_view_tabs(self):
        sidebar = sidebar_for_suppliers(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(
            labels,
            ["Dashboard", "All suppliers", "Item suppliers", "Shelf suppliers"],
        )
        self.assertTrue(sidebar["primary"][1]["active"])
        self.assertIn("/analytics/suppliers/", sidebar["primary"][1]["href"])
        self.assertIn("view=items", sidebar["primary"][2]["href"])
        self.assertIn("view=shelf", sidebar["primary"][3]["href"])
        self.assertEqual(sidebar["primary"][0]["href"], sidebar["dashboard_url"])

    def test_suppliers_sidebar_view_tab_marks_active(self):
        sidebar = sidebar_for_suppliers(
            EmployeeRole.IT_SUPPORT, view="shelf"
        )
        self.assertFalse(sidebar["primary"][1]["active"])
        self.assertFalse(sidebar["primary"][2]["active"])
        self.assertTrue(sidebar["primary"][3]["active"])


class AnalyticsSidebarTests(TestCase):
    def test_overview_sidebar_lists_section_hub_links(self):
        sidebar = sidebar_for_analytics(EmployeeRole.IT_SUPPORT, active_view="overview")
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(labels[0], "Dashboard")
        self.assertEqual(labels[1], "Overview")
        self.assertTrue(sidebar["primary"][1]["active"])
        self.assertIn("Revenue", labels)
        self.assertIn("Receipts", labels)
        self.assertNotIn("Analytics settings", labels)
        self.assertIn("Items", labels)
        self.assertIn("Stock", labels)
        self.assertNotIn("Item report", labels)
        self.assertNotIn("Supply analytics", labels)
        self.assertNotIn("Clients", labels)
        self.assertNotIn("Suppliers", labels)

    def test_section_sidebar_is_focused_to_one_page(self):
        sidebar = sidebar_for_analytics_section(
            EmployeeRole.IT_SUPPORT, active_view="revenue"
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(labels, ["Dashboard", "Overview", "Revenue"])
        self.assertTrue(sidebar["primary"][2]["active"])
        self.assertFalse(sidebar["primary"][1]["active"])

    def test_items_section_sidebar_includes_item_report(self):
        sidebar = sidebar_for_analytics_section(
            EmployeeRole.IT_SUPPORT, active_view="items"
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(
            labels,
            ["Dashboard", "Overview", "Items", "Item report"],
        )
        self.assertTrue(sidebar["primary"][2]["active"])
        self.assertFalse(sidebar["primary"][3]["active"])
        self.assertIn("/analytics/items/report/", sidebar["primary"][3]["href"])

    def test_stock_sidebar_includes_supply_analytics(self):
        sidebar = sidebar_for_stock(EmployeeRole.IT_SUPPORT, active="stock")
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(
            labels,
            ["Dashboard", "Overview", "Stock", "Supply analytics"],
        )
        self.assertTrue(sidebar["primary"][2]["active"])
        self.assertFalse(sidebar["primary"][3]["active"])
        self.assertIn("/analytics/supply/", sidebar["primary"][3]["href"])

        supply = sidebar_for_stock(EmployeeRole.IT_SUPPORT, active="supply")
        supply_labels = [item["label"] for item in supply["primary"]]
        self.assertEqual(labels, supply_labels)
        self.assertTrue(supply["primary"][3]["active"])
        self.assertFalse(supply["primary"][2]["active"])

    def test_confirm_receipts_sidebar_is_focused(self):
        sidebar = sidebar_for_analytics_section(
            EmployeeRole.IT_SUPPORT, active_view="confirm-receipts"
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(
            labels,
            [
                "Dashboard",
                "Overview",
                "Receipts",
                "Confirm receipts",
                "Return receipt",
            ],
        )
        self.assertTrue(sidebar["primary"][3]["active"])


class AnalyticsReceiptStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="870099",
            password="analytics-pass",
            email="ax-settings@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870099",
            role=EmployeeRole.IT_SUPPORT,
            status=EmployeeStatus.ACTIVE,
        )

    def test_receipt_status_defaults_to_pending(self):
        from employees.analytics_services import resolve_analytics_receipt_status

        self.assertEqual(resolve_analytics_receipt_status(None), "pending")
        self.assertEqual(resolve_analytics_receipt_status("bogus"), "pending")
        self.assertEqual(
            resolve_analytics_receipt_status(None, default="all"), "all"
        )

    def test_receipt_status_pick_is_remembered_on_session(self):
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from employees.analytics_services import resolve_analytics_receipt_status

        request = RequestFactory().get("/it-support/analytics/revenue/")
        request.session = SessionStore()
        self.assertEqual(
            resolve_analytics_receipt_status("confirmed", request=request),
            "confirmed",
        )
        self.assertEqual(
            resolve_analytics_receipt_status(None, request=request), "confirmed"
        )
        self.assertEqual(
            resolve_analytics_receipt_status("pending", request=request),
            "pending",
        )
        self.assertEqual(
            resolve_analytics_receipt_status(None, request=request), "pending"
        )

    def test_receipt_status_filters_receipt_queryset(self):
        from employees.analytics_services import _receipts_qs

        shop = Shop.objects.create(
            name="AX STATUS SHOP",
            location="NAIROBI",
            email="ax-status@test.local",
            phone_number="0700004099",
            login_code="850199",
            password_hash="x",
            created_by=self.staff,
        )
        pending = ShopReceipt.objects.create(
            shop=shop,
            receipt_number="AX-PENDING-1",
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.ACTIVE,
            total=10,
            amount_paid=10,
            created_by=self.staff,
        )
        confirmed = ShopReceipt.objects.create(
            shop=shop,
            receipt_number="AX-CONFIRMED-1",
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.CONFIRMED,
            total=20,
            amount_paid=20,
            created_by=self.staff,
        )
        pending_ids = set(
            _receipts_qs(
                shop_ids=[shop.pk],
                start=None,
                end=None,
                kinds=[ShopReceiptKind.SALE],
                receipt_status="pending",
            ).values_list("pk", flat=True)
        )
        confirmed_ids = set(
            _receipts_qs(
                shop_ids=[shop.pk],
                start=None,
                end=None,
                kinds=[ShopReceiptKind.SALE],
                receipt_status="confirmed",
            ).values_list("pk", flat=True)
        )
        self.assertEqual(pending_ids, {pending.pk})
        self.assertEqual(confirmed_ids, {confirmed.pk})


class ConfirmSalesReceiptTests(TestCase):
    def setUp(self):
        from shops.services import set_company_pos_setting

        self.user = User.objects.create_user(
            username="870011",
            password="confirm-pass",
            email="confirm@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870011",
            phone_country_code="+254",
            phone_number="700001101",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="CONFIRM SHOP",
            location="NAIROBI",
            email="confirm-shop@test.local",
            phone_number="0700001101",
            login_code="870111",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        # Company POS settings are a singleton — reset between tests.
        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=False
        )

    def _receipt(self, *, kind, number):
        return ShopReceipt.objects.create(
            shop=self.shop,
            kind=kind,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number=number,
            client_name="Walk-in",
            subtotal=100,
            tax_percent=0,
            tax_amount=0,
            total=100,
            amount_paid=100,
            payment_method="cash",
            created_by=self.staff,
        )

    def test_confirm_rejects_quotation_and_requires_credit_amount(self):
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from shops.services import confirm_shop_receipt

        credit = self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-1")
        credit.amount_paid = Decimal("0.00")
        credit.cash_amount = Decimal("0.00")
        credit.save(update_fields=["amount_paid", "cash_amount"])
        quote = self._receipt(kind=ShopReceiptKind.QUOTATION, number="QT-1")
        with self.assertRaisesMessage(
            ValidationError,
            "Enter the amount to confirm for this credit.",
        ):
            confirm_shop_receipt(shop=self.shop, receipt_id=credit.pk, actor=self.staff)
        with self.assertRaisesMessage(
            ValidationError,
            "Only sales, credits, and gifts can be confirmed.",
        ):
            confirm_shop_receipt(shop=self.shop, receipt_id=quote.pk, actor=self.staff)

    def test_confirm_credit_partial_and_full(self):
        from decimal import Decimal

        from django.test import RequestFactory

        from employees.analytics_services import build_confirm_receipts_page
        from shops.services import confirm_shop_receipt

        open_credit = self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-OPEN")
        open_credit.amount_paid = Decimal("0.00")
        open_credit.cash_amount = Decimal("0.00")
        open_credit.save(update_fields=["amount_paid", "cash_amount"])

        partial = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=open_credit.pk,
            actor=self.staff,
            amount="40.00",
            payment_method="cash",
        )
        open_credit.refresh_from_db()
        self.assertTrue(partial["ok"])
        self.assertTrue(partial.get("partial_credit"))
        self.assertEqual(partial["status_label"], "Partial confirmation")
        self.assertEqual(open_credit.kind, ShopReceiptKind.CREDIT)
        self.assertEqual(open_credit.amount_paid, Decimal("40.00"))
        self.assertEqual(open_credit.status, ShopReceiptStatus.ACTIVE)

        request = RequestFactory().get(
            "/it-support/analytics/confirm-receipts/",
            {"kind": "credit", "range": "all"},
        )
        request.user = self.user
        page = build_confirm_receipts_page(profile=self.staff, request=request)
        partial_row = next(
            row for row in page["rows"] if row["number"] == "CR-OPEN"
        )
        self.assertEqual(partial_row["status_label"], "Partial confirmation")
        self.assertTrue(partial_row["is_partial_confirmation"])
        self.assertTrue(partial_row["can_confirm"])
        self.assertTrue(partial_row["credit_clear_on_confirm"])

        full = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=open_credit.pk,
            actor=self.staff,
            amount="60.00",
            payment_method="cash",
        )
        open_credit.refresh_from_db()
        self.assertTrue(full["ok"])
        self.assertTrue(full.get("cleared_credit"))
        self.assertEqual(open_credit.kind, ShopReceiptKind.SALE)
        self.assertTrue(open_credit.settled_from_credit)
        self.assertEqual(open_credit.status, ShopReceiptStatus.CONFIRMED)

    def test_confirm_accepts_pending_sale(self):
        from shops.services import confirm_shop_receipt

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-1")
        result = confirm_shop_receipt(
            shop=self.shop, receipt_id=sale.pk, actor=self.staff
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)

    def test_confirm_updates_payment_at_and_exposes_local_default(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_confirm_receipts_page
        from shops.services import confirm_shop_receipt, get_shop_receipt_detail

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-PAY-AT")
        printed = timezone.make_aware(
            datetime(2026, 11, 11, 13, 49), ZoneInfo("Africa/Nairobi")
        )
        type(sale).objects.filter(pk=sale.pk).update(created_at=printed)
        sale.refresh_from_db()

        detail = get_shop_receipt_detail(shop=self.shop, receipt_id=sale.pk)
        self.assertEqual(detail["receipt"]["created_at_local"], "2026-11-11T13:49")
        self.assertEqual(detail["receipt"]["confirmed_payment_at_local"], "")

        request = RequestFactory().get(
            "/it-support/analytics/confirm-receipts/",
            {"kind": "sale", "range": "all"},
        )
        request.user = self.user
        pending_page = build_confirm_receipts_page(
            profile=self.staff, request=request
        )
        pending_row = next(
            row for row in pending_page["rows"] if row["number"] == "SL-PAY-AT"
        )
        self.assertEqual(pending_row["when"], "11 Nov 2026 · 13:49")
        self.assertEqual(pending_row["paid_when"], "—")

        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            payment_at="2026-11-12T09:15",
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)
        # Print time stays on created_at; payment time is confirmed_payment_at only.
        self.assertEqual(
            timezone.localtime(sale.created_at).strftime("%Y-%m-%dT%H:%M"),
            "2026-11-11T13:49",
        )
        self.assertEqual(
            timezone.localtime(sale.confirmed_payment_at).strftime("%Y-%m-%dT%H:%M"),
            "2026-11-12T09:15",
        )
        confirmed_detail = get_shop_receipt_detail(
            shop=self.shop, receipt_id=sale.pk
        )
        self.assertEqual(
            confirmed_detail["receipt"]["confirmed_payment_at_local"],
            "2026-11-12T09:15",
        )
        confirmed_page = build_confirm_receipts_page(
            profile=self.staff, request=request
        )
        confirmed_row = next(
            row for row in confirmed_page["rows"] if row["number"] == "SL-PAY-AT"
        )
        self.assertEqual(confirmed_row["when"], "11 Nov 2026 · 13:49")
        self.assertEqual(confirmed_row["paid_when"], "12 Nov 2026 · 09:15")
        self.assertEqual(confirmed_row["paid_via"], "Cash")

    def test_confirm_without_payment_at_defaults_to_now(self):
        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_confirm_receipts_page
        from shops.services import confirm_shop_receipt

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-NOW")
        before = timezone.now()
        result = confirm_shop_receipt(
            shop=self.shop, receipt_id=sale.pk, actor=self.staff
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertIsNotNone(sale.confirmed_payment_at)
        self.assertGreaterEqual(sale.confirmed_payment_at, before)
        self.assertLessEqual(sale.confirmed_payment_at, timezone.now())

        request = RequestFactory().get(
            "/it-support/analytics/confirm-receipts/",
            {"kind": "sale", "range": "all"},
        )
        request.user = self.user
        page = build_confirm_receipts_page(profile=self.staff, request=request)
        row = next(r for r in page["rows"] if r["number"] == "SL-NOW")
        self.assertNotEqual(row["paid_when"], "—")
        self.assertEqual(row["paid_via"], "Cash")

    def test_set_status_pending_confirmed_cancelled_round_trip(self):
        from shops.services import set_confirm_receipt_status

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-STATUS")
        confirmed = set_confirm_receipt_status(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            status="confirmed",
        )
        sale.refresh_from_db()
        self.assertTrue(confirmed["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)

        pending = set_confirm_receipt_status(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            status="pending",
        )
        sale.refresh_from_db()
        self.assertTrue(pending["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.ACTIVE)

        cancelled = set_confirm_receipt_status(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            status="cancelled",
        )
        sale.refresh_from_db()
        self.assertTrue(cancelled["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CANCELLED)

        reopened = set_confirm_receipt_status(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            status="pending",
        )
        sale.refresh_from_db()
        self.assertTrue(reopened["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.ACTIVE)

    def test_confirm_saves_optional_mpesa_reference(self):
        from shops.services import confirm_shop_receipt

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-MPESA")
        sale.payment_method = ShopPaymentMethod.MPESA
        sale.save(update_fields=["payment_method"])

        blank = confirm_shop_receipt(
            shop=self.shop, receipt_id=sale.pk, actor=self.staff
        )
        sale.refresh_from_db()
        self.assertTrue(blank["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(sale.mpesa_receipt_number, "")

        sale.status = ShopReceiptStatus.ACTIVE
        sale.save(update_fields=["status"])
        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            mpesa_receipt_number="  TKL4X2ABCD  ",
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.mpesa_receipt_number, "TKL4X2ABCD")
        self.assertEqual(result.get("mpesa_receipt_number"), "TKL4X2ABCD")

    def test_company_require_reference_syncs_to_shop_receipt_overrides(self):
        from shops.models import ShopPosSettings
        from shops.services import (
            get_effective_pos_settings,
            set_company_pos_setting,
            set_shop_pos_override,
            set_shop_pos_setting,
        )

        set_shop_pos_override(shop=self.shop, section="receipt", enabled=True)
        set_shop_pos_setting(
            shop=self.shop,
            field="require_mpesa_reference_on_confirm",
            enabled=False,
        )
        self.assertFalse(
            get_effective_pos_settings(self.shop).require_mpesa_reference_on_confirm
        )

        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        shop_row = ShopPosSettings.objects.get(shop=self.shop)
        self.assertTrue(shop_row.require_mpesa_reference_on_confirm)
        self.assertTrue(
            get_effective_pos_settings(self.shop).require_mpesa_reference_on_confirm
        )

    def test_confirm_requires_mpesa_reference_when_setting_on(self):
        from django.core.exceptions import ValidationError

        from shops.services import (
            confirm_shop_receipt,
            get_shop_receipt_detail,
            set_company_pos_setting,
        )

        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-REQ-REF")
        sale.payment_method = ShopPaymentMethod.MPESA
        sale.save(update_fields=["payment_method"])

        detail = get_shop_receipt_detail(shop=self.shop, receipt_id=sale.pk)
        self.assertTrue(detail["receipt"]["require_mpesa_reference_on_confirm"])

        with self.assertRaisesMessage(
            ValidationError,
            "Enter the reference number before confirming this receipt.",
        ):
            confirm_shop_receipt(
                shop=self.shop, receipt_id=sale.pk, actor=self.staff
            )

        with self.assertRaisesMessage(
            ValidationError,
            "Enter the payment date and time before confirming this receipt.",
        ):
            confirm_shop_receipt(
                shop=self.shop,
                receipt_id=sale.pk,
                actor=self.staff,
                mpesa_receipt_number="REQREF1234",
            )

        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            mpesa_receipt_number="REQREF1234",
            payment_at="2026-11-12T09:15",
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.mpesa_receipt_number, "REQREF1234")
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)

    def test_confirm_accepts_existing_mpesa_reference_when_required(self):
        from shops.services import confirm_shop_receipt, set_company_pos_setting

        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-EXIST-REF")
        sale.payment_method = ShopPaymentMethod.MPESA
        sale.mpesa_receipt_number = "ALREADYHERE"
        sale.save(update_fields=["payment_method", "mpesa_receipt_number"])

        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            payment_at="2026-11-12T09:15",
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(sale.mpesa_receipt_number, "ALREADYHERE")

    def test_confirm_cash_skips_reference_when_setting_on(self):
        """Cash-only confirms carry no M-Pesa/Card reference."""
        from django.core.exceptions import ValidationError

        from shops.services import confirm_shop_receipt, set_company_pos_setting

        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-CASH-OK")
        # No reference needed for cash — but the payment time is still required.
        with self.assertRaisesMessage(
            ValidationError,
            "Enter the payment date and time before confirming this receipt.",
        ):
            confirm_shop_receipt(
                shop=self.shop, receipt_id=sale.pk, actor=self.staff
            )
        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            payment_at="2026-11-12T09:15",
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(sale.mpesa_receipt_number, "")

    def test_confirm_method_switch_to_cash_skips_reference(self):
        """Confirming an M-Pesa receipt as cash-only needs no reference."""
        from shops.services import confirm_shop_receipt, set_company_pos_setting

        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-SWITCH-CASH")
        sale.payment_method = ShopPaymentMethod.MPESA
        sale.save(update_fields=["payment_method"])
        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=sale.pk,
            actor=self.staff,
            payment_method="cash",
            payment_at="2026-11-12T09:15",
        )
        sale.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(sale.status, ShopReceiptStatus.CONFIRMED)
        self.assertEqual(sale.payment_method, "cash")
        self.assertEqual(sale.mpesa_receipt_number, "")

    def test_confirm_page_lists_sales_only(self):
        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_confirm_receipts_page

        self._receipt(kind=ShopReceiptKind.SALE, number="SL-2")
        self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-2")
        self._receipt(kind=ShopReceiptKind.QUOTATION, number="QT-2")
        request = RequestFactory().get("/it-support/analytics/confirm-receipts/")
        request.user = self.user
        context = build_confirm_receipts_page(profile=self.staff, request=request)
        kinds = {row["kind"] for row in context["rows"]}
        self.assertEqual(kinds, {"sale", "credit", "quotation"})
        self.assertEqual(context["kind_filter"], "all")
        self.assertEqual(context["status_filter"], "")
        # Bare page defaults to today (day range).
        self.assertEqual(context["report_range"], "day")
        self.assertEqual(
            context["report_date_value"],
            timezone.localdate().isoformat(),
        )
        self.assertIn("kind_options", context)
        self.assertIn(
            "/analytics/confirm-receipts/print/",
            context["page"].get("print_url") or "",
        )
        self.assertTrue(context["page"].get("rows_url"))
        sale_row = context["rows"][0]
        self.assertEqual(sale_row["client"], "Walk-in")
        self.assertIn("amount_label", sale_row)
        self.assertTrue(str(sale_row["amount_label"]).startswith("KSh"))

    def test_confirm_print_document_uses_filters(self):
        from django.test import RequestFactory

        from employees.analytics_services import build_confirm_receipts_print_document

        self._receipt(kind=ShopReceiptKind.SALE, number="SL-PRINT-1")
        request = RequestFactory().get(
            "/it-support/analytics/confirm-receipts/print/",
            {"kind": "sale", "range": "month", "month": "2026-11"},
        )
        request.user = self.user
        document = build_confirm_receipts_print_document(
            profile=self.staff, request=request
        )
        # ?kind=sale normalizes onto the combined Sales & credits filter.
        self.assertEqual(document["kind_label"], "Sales & credits")
        self.assertGreaterEqual(document["receipt_total"], 1)
        self.assertTrue(
            any(row["number"] == "SL-PRINT-1" for row in document["rows"])
        )

    def test_confirm_page_orders_pending_cancelled_confirmed(self):
        from django.test import RequestFactory

        from employees.analytics_services import (
            _confirm_receipts_collect_rows,
            build_confirm_receipts_page,
        )

        confirmed = self._receipt(kind=ShopReceiptKind.SALE, number="SL-CONF")
        confirmed.status = ShopReceiptStatus.CONFIRMED
        confirmed.save(update_fields=["status"])
        cancelled = self._receipt(kind=ShopReceiptKind.SALE, number="SL-CANCEL")
        cancelled.status = ShopReceiptStatus.CANCELLED
        cancelled.save(update_fields=["status"])
        for i in range(15):
            self._receipt(kind=ShopReceiptKind.SALE, number=f"SL-P{i:03d}")

        request = RequestFactory().get(
            "/it-support/analytics/confirm-receipts/",
            {"kind": "sale", "range": "all"},
        )
        request.user = self.user
        context = build_confirm_receipts_page(profile=self.staff, request=request)
        statuses = [row["status"] for row in context["rows"]]
        self.assertIn(ShopReceiptStatus.CANCELLED, statuses)
        self.assertIn(ShopReceiptStatus.CONFIRMED, statuses)
        self.assertLess(
            statuses.index(ShopReceiptStatus.ACTIVE),
            statuses.index(ShopReceiptStatus.CANCELLED),
        )
        self.assertLess(
            statuses.index(ShopReceiptStatus.CANCELLED),
            statuses.index(ShopReceiptStatus.CONFIRMED),
        )
        # Uncapped page lists every receipt in the filtered period.
        pending_count = sum(
            1
            for status in statuses
            if status
            in {ShopReceiptStatus.ACTIVE, ShopReceiptStatus.PARTIAL_RETURN}
        )
        self.assertEqual(pending_count, 15)
        self.assertEqual(
            statuses[-2:],
            [
                ShopReceiptStatus.CANCELLED,
                ShopReceiptStatus.CONFIRMED,
            ],
        )

        # Explicit limit still caps each status band independently.
        capped = _confirm_receipts_collect_rows(
            shop_ids=context["active_shop_ids"],
            start=context["start"],
            end=context["end"],
            status_key=context["status_filter"],
            search=context["search"],
            kind_key=context["kind_filter"],
            limit=10,
        )
        capped_statuses = [row["status"] for row in capped]
        capped_pending = sum(
            1
            for status in capped_statuses
            if status
            in {ShopReceiptStatus.ACTIVE, ShopReceiptStatus.PARTIAL_RETURN}
        )
        self.assertEqual(capped_pending, 10)
        self.assertIn(ShopReceiptStatus.CANCELLED, capped_statuses)
        self.assertIn(ShopReceiptStatus.CONFIRMED, capped_statuses)


class AnalyticsReceiptsBrowseTests(TestCase):
    def setUp(self):
        from django.utils import timezone

        self.user = User.objects.create_user(
            username="870012",
            password="receipts-pass",
            email="receipts@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870012",
            phone_country_code="+254",
            phone_number="700001102",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="RECEIPTS SHOP",
            location="NAIROBI",
            email="receipts-shop@test.local",
            phone_number="0700001102",
            login_code="870112",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        self.today = timezone.localdate()

    def _receipt(self, *, kind, number):
        return ShopReceipt.objects.create(
            shop=self.shop,
            kind=kind,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number=number,
            client_name="Walk-in",
            subtotal=100,
            tax_percent=0,
            tax_amount=0,
            total=100,
            amount_paid=100 if kind == ShopReceiptKind.SALE else 0,
            payment_method="cash",
            created_by=self.staff,
        )

    def test_receipts_section_lists_all_types_and_keeps_tools(self):
        from django.test import RequestFactory

        from employees.analytics_services import build_analytics_page

        self._receipt(kind=ShopReceiptKind.SALE, number="SL-R1")
        self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-R1")
        self._receipt(kind=ShopReceiptKind.QUOTATION, number="QT-R1")
        request = RequestFactory().get(
            "/shop-manager/analytics/receipts/",
            {"range": "day", "date": self.today.isoformat()},
        )
        request.user = self.user
        context = build_analytics_page(
            profile=self.staff, request=request, section_slug="receipts"
        )
        page = context["page"]
        self.assertFalse(page.get("hide_date_filters"))
        self.assertEqual(page.get("receipt_kind"), "all")
        # Receipt tools moved out of the page body into the sidebar.
        self.assertEqual(page.get("sections") or [], [])
        from employees.workspace import sidebar_for_analytics_section

        sidebar = sidebar_for_analytics_section(
            self.staff.role, active_view="receipts", profile=self.staff
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertIn("Confirm receipts", labels)
        self.assertIn("Return receipt", labels)
        list_table = page["tables"][0]
        numbers = {row["cells"][0]["label"] for row in list_table["rows"]}
        self.assertEqual(numbers, {"SL-R1", "CR-R1", "QT-R1"})

    def test_receipts_section_filters_by_kind(self):
        from django.test import RequestFactory

        from employees.analytics_services import build_analytics_page

        self._receipt(kind=ShopReceiptKind.SALE, number="SL-R2")
        self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-R2")
        request = RequestFactory().get(
            "/shop-manager/analytics/receipts/",
            {
                "range": "day",
                "date": self.today.isoformat(),
                "receipt_kind": "credits",
            },
        )
        request.user = self.user
        context = build_analytics_page(
            profile=self.staff, request=request, section_slug="receipts"
        )
        page = context["page"]
        self.assertEqual(page.get("receipt_kind"), "credits")
        numbers = {row["cells"][0]["label"] for row in page["tables"][0]["rows"]}
        self.assertEqual(numbers, {"CR-R2"})

    def test_receipts_list_includes_bought_items(self):
        from django.test import RequestFactory

        from employees.analytics_services import build_analytics_receipts_list

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-ITEMS")
        ShopReceiptLine.objects.create(
            receipt=sale,
            item_name="USB Cable",
            quantity=2,
            unit_price=50,
            line_total=100,
        )
        ShopReceiptLine.objects.create(
            receipt=sale,
            item_name="Mouse",
            quantity=1,
            unit_price=100,
            line_total=100,
        )
        request = RequestFactory().get(
            "/shop-manager/analytics/receipts/sales/",
            {
                "range": "day",
                "date": self.today.isoformat(),
                "receipt_status": "pending",
            },
        )
        request.user = self.user
        context = build_analytics_receipts_list(
            profile=self.staff, request=request, kind="sales"
        )
        self.assertEqual(len(context["rows"]), 1)
        self.assertEqual(context["rows"][0]["number"], "SL-ITEMS")
        self.assertEqual(context["rows"][0]["items"], "USB Cable ×2, Mouse ×1")

    def test_receipts_section_columns_and_print_popup(self):
        from django.test import RequestFactory

        from employees.analytics_services import (
            build_analytics_page,
            build_receipts_print_document,
            receipts_report_pdf_response,
        )

        sale = self._receipt(kind=ShopReceiptKind.SALE, number="SL-PRINT")
        sale.client_name = "Ada Client"
        sale.client_phone = "0700111222"
        sale.save(update_fields=["client_name", "client_phone"])
        ShopReceiptLine.objects.create(
            receipt=sale,
            item_name="Keyboard",
            quantity=1,
            unit_price=100,
            line_total=100,
        )
        confirmed = self._receipt(kind=ShopReceiptKind.SALE, number="SL-CONF")
        confirmed.status = ShopReceiptStatus.CONFIRMED
        confirmed.save(update_fields=["status"])
        request = RequestFactory().get(
            "/shop-manager/analytics/receipts/",
            {
                "range": "day",
                "date": self.today.isoformat(),
                "receipt_kind": "sales",
                "receipt_status": "pending",
            },
        )
        request.user = self.user
        context = build_analytics_page(
            profile=self.staff, request=request, section_slug="receipts"
        )
        page = context["page"]
        self.assertTrue(page.get("receipts_print_modal"))
        self.assertIn("/analytics/receipts/print/", page.get("receipts_print_url") or "")
        self.assertEqual(
            page["tables"][0]["columns"],
            [
                "Receipt",
                "Type",
                "Client",
                "Items",
                "Total",
                "Status",
                "Date & time",
            ],
        )
        rows = page["tables"][0]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cells"][0]["label"], "SL-PRINT")
        self.assertEqual(rows[0]["cells"][1]["label"], "Sale")
        self.assertEqual(rows[0]["cells"][1]["tone"], "good")
        self.assertEqual(rows[0]["cells"][2]["label"], "Ada Client")
        self.assertEqual(rows[0]["cells"][2]["sub"], "0700111222")
        items_cell = rows[0]["cells"][3]
        self.assertEqual(items_cell["kind"], "items")
        self.assertEqual(items_cell["label"], "Keyboard ×1")
        self.assertEqual(
            json.loads(items_cell["items_json"]),
            [
                {
                    "name": "Keyboard",
                    "qty": "1",
                    "unit": "KSh 100.00",
                    "total": "KSh 100.00",
                }
            ],
        )
        self.assertEqual(rows[0]["cells"][4]["kind"], "money")
        self.assertEqual(rows[0]["cells"][5]["label"], "Pending")
        self.assertEqual(len(rows[0]["cells"]), 7)

        document = build_receipts_print_document(filters=context)
        self.assertEqual(document["receipt_total"], 1)
        self.assertEqual(document["rows"][0]["client_name"], "Ada Client")
        self.assertEqual(document["rows"][0]["phone"], "0700111222")
        self.assertEqual(document["rows"][0]["items"], "Keyboard ×1")

        pdf_response = receipts_report_pdf_response(page=page, filters=context)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response["Content-Disposition"].startswith("attachment;"))
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

        self.client.force_login(self.user)
        print_params = {
            "range": "day",
            "date": self.today.isoformat(),
            "receipt_kind": "sales",
            "receipt_status": "pending",
            "paper": "a4",
        }
        print_response = self.client.get(
            "/shop-manager/analytics/receipts/print/", print_params
        )
        self.assertEqual(print_response.status_code, 200)
        self.assertContains(print_response, "SL-PRINT")
        self.assertContains(print_response, "Ada Client")
        self.assertContains(print_response, "0700111222")

        estimate_response = self.client.get(
            "/shop-manager/analytics/receipts/print/",
            {
                **print_params,
                "estimate": "1",
            },
        )
        self.assertEqual(estimate_response.status_code, 200)
        self.assertEqual(estimate_response["Content-Type"], "application/json")
        self.assertEqual(estimate_response.json()["receipt_count"], 1)


class AnalyticsPosFeatureVisibilityTests(TestCase):
    """Analytics columns/metrics/options track the company POS toggles."""

    def setUp(self):
        from django.utils import timezone

        from shops.services import _invalidate_pos_settings_cache

        CompanyPosSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enable_sale": True,
                "enable_credit": True,
                "enable_quotation": True,
                "enable_trade_out": True,
                "enable_trade_in": True,
                "enable_cash": True,
                "enable_mpesa": True,
                "enable_cash_mpesa": False,
                "enable_cart_delivery": True,
            },
        )
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        self.user = User.objects.create_user(
            username="870055",
            password="flags-pass",
            email="flags@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870055",
            phone_country_code="+254",
            phone_number="700001155",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="FLAGS SHOP",
            location="NAIROBI",
            email="flags-shop@test.local",
            phone_number="0700001155",
            login_code="870155",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        self.today = timezone.localdate()

    def _set_pos(self, **fields):
        from shops.services import _invalidate_pos_settings_cache

        pos = CompanyPosSettings.objects.get(pk=1)
        for key, value in fields.items():
            setattr(pos, key, value)
        pos.save()
        _invalidate_pos_settings_cache()

    def _receipt(self, *, kind, number, total=100, paid=0, cash=0, mpesa=0):
        return ShopReceipt.objects.create(
            shop=self.shop,
            kind=kind,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number=number,
            client_name="Walk-in",
            subtotal=total,
            tax_percent=0,
            tax_amount=0,
            total=total,
            amount_paid=paid,
            cash_amount=cash,
            mpesa_amount=mpesa,
            payment_method="cash",
            created_by=self.staff,
        )

    def _page(self, section, params=None):
        from django.test import RequestFactory

        from employees.analytics_services import build_analytics_page

        request = RequestFactory().get(
            f"/shop-manager/analytics/{section}/",
            {"range": "day", "date": self.today.isoformat(), **(params or {})},
        )
        request.user = self.user
        return build_analytics_page(
            profile=self.staff, request=request, section_slug=section
        )["page"]

    @staticmethod
    def _labels(columns):
        return [
            (col.get("label") if isinstance(col, dict) else col)
            for col in columns
        ]

    def _assert_rows_align(self, page):
        for table in page.get("tables") or []:
            for row in table.get("rows") or []:
                if isinstance(row, dict) and row.get("group_header"):
                    continue
                cells = row.get("cells") if isinstance(row, dict) else row
                if cells is None:
                    continue
                self.assertEqual(
                    len(cells),
                    len(table["columns"]),
                    f"row/column mismatch in {table.get('title')}",
                )

    def test_revenue_drops_credits_column_when_disabled(self):
        self._receipt(kind=ShopReceiptKind.SALE, number="SL-V", paid=100)
        self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-V")
        page = self._page("revenue")
        grid = next(t for t in page["tables"] if t.get("shop_grid"))
        self.assertIn("Credits", self._labels(grid["columns"]))

        self._set_pos(enable_credit=False)
        page = self._page("revenue")
        grid = next(t for t in page["tables"] if t.get("shop_grid"))
        labels = self._labels(grid["columns"])
        self.assertNotIn("Credits", labels)
        self.assertIn("Sales", labels)
        self.assertIn("Total", labels)
        self._assert_rows_align(page)

    def test_sales_drops_mpesa_and_credit_columns(self):
        self._receipt(
            kind=ShopReceiptKind.SALE,
            number="SL-M",
            paid=100,
            cash=100,
            mpesa=0,
        )
        self._set_pos(enable_credit=False, enable_mpesa=False)
        page = self._page("sales")
        grid = next(t for t in page["tables"] if t.get("shop_grid"))
        labels = self._labels(grid["columns"])
        for dropped in (
            "M-Pesa",
            "Paid cash",
            "Paid M-Pesa",
            "Paid total",
            "Unpaid",
        ):
            self.assertNotIn(dropped, labels)
        self.assertIn("Cash", labels)
        self.assertIn("General total", labels)
        tiles = (page.get("summary_board") or {}).get("tiles") or []
        tile_labels = [tile["label"] for tile in tiles]
        self.assertNotIn("M-Pesa", tile_labels)
        self.assertNotIn("Unpaid", tile_labels)
        self._assert_rows_align(page)

    def test_balances_drop_mpesa_channels(self):
        self._set_pos(enable_mpesa=False)
        page = self._page("balances")
        for table in page.get("tables") or []:
            labels = self._labels(table["columns"])
            self.assertFalse(
                any("m-pesa" in (label or "").lower() for label in labels),
                f"M-Pesa column left in {table.get('title')}",
            )
        for group in page.get("metric_groups") or []:
            for row in group.get("rows") or []:
                for metric in row.get("metrics") or []:
                    self.assertNotIn("M-Pesa", metric["label"])
        self._assert_rows_align(page)

    def test_receipts_options_and_rows_drop_disabled_kinds(self):
        self._receipt(kind=ShopReceiptKind.SALE, number="SL-O", paid=100)
        self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-O")
        self._set_pos(enable_credit=False, enable_quotation=False)
        page = self._page("receipts")
        option_values = {value for value, _label in page["receipt_kind_options"]}
        self.assertNotIn("credits", option_values)
        self.assertNotIn("quotations", option_values)
        self.assertIn("sales", option_values)
        numbers = {row["cells"][0]["label"] for row in page["tables"][0]["rows"]}
        self.assertIn("SL-O", numbers)
        self.assertNotIn("CR-O", numbers)
        # Forcing a disabled kind falls back to "all types".
        page = self._page("receipts", {"receipt_kind": "credits"})
        self.assertEqual(page["receipt_kind"], "all")

    def test_receipts_list_kind_404s_when_disabled(self):
        from django.http import Http404

        from employees.analytics_services import get_analytics_receipt_kind

        self._set_pos(enable_credit=False)
        with self.assertRaises(Http404):
            get_analytics_receipt_kind("credits")
        self.assertEqual(
            get_analytics_receipt_kind("sales")["slug"], "sales"
        )

    def test_client_account_options_and_tiles_drop_disabled_kinds(self):
        from django.test import RequestFactory

        from employees.analytics_services import build_client_transactions

        client = Client.objects.create(
            full_name="Flag Client",
            phone_number="0700999000",
            phone_normalized="0700999000",
        )
        self._receipt(kind=ShopReceiptKind.SALE, number="SL-C1", paid=100)
        self._receipt(kind=ShopReceiptKind.CREDIT, number="CR-C1")
        ShopReceipt.objects.filter(receipt_number="SL-C1").update(
            client_id=client.pk
        )
        ShopReceipt.objects.filter(receipt_number="CR-C1").update(
            client_id=client.pk
        )
        self._set_pos(enable_credit=False, enable_trade_in=False)
        request = RequestFactory().get(
            f"/shop-manager/analytics/clients/{client.pk}/",
            {"range": "all"},
        )
        request.user = self.user
        account = build_client_transactions(
            profile=self.staff, client_id=client.pk, request=request
        )
        option_values = {value for value, _label in account["receipt_kind_options"]}
        self.assertNotIn("credits", option_values)
        self.assertNotIn("trade_in", option_values)
        self.assertIn("sales", option_values)
        tile_labels = [
            tile["label"]
            for tile in account["summary_board"]["tiles"]
        ]
        self.assertNotIn("Credits", tile_labels)
        self.assertNotIn("Trade in", tile_labels)
        numbers = {row["number"] for row in account["rows"]}
        self.assertIn("SL-C1", numbers)
        self.assertNotIn("CR-C1", numbers)

    def test_confirm_receipts_options_follow_toggles(self):
        from django.test import RequestFactory

        from employees.analytics_services import build_confirm_receipts_page

        self._set_pos(enable_sale=False, enable_credit=True)
        request = RequestFactory().get("/shop-manager/analytics/confirm/")
        request.user = self.user
        page = build_confirm_receipts_page(
            profile=self.staff, request=request, include_rows=False
        )
        values = {value for value, _label in page["kind_options"]}
        # Sales & credits merges both — it stays while either flag is on.
        self.assertIn("sales_credits", values)
        self.assertEqual(page["kind_filter"], "all")

        self._set_pos(enable_sale=True, enable_credit=False)
        page = build_confirm_receipts_page(
            profile=self.staff, request=request, include_rows=False
        )
        values = {value for value, _label in page["kind_options"]}
        self.assertIn("sales_credits", values)

        self._set_pos(enable_sale=False, enable_credit=False)
        page = build_confirm_receipts_page(
            profile=self.staff, request=request, include_rows=False
        )
        values = {value for value, _label in page["kind_options"]}
        self.assertNotIn("sales_credits", values)

    def test_tradings_visible_with_trade_in_only(self):
        from shops.services import (
            company_pos_analytics_section_visible,
            company_pos_permission_visible,
        )

        self._set_pos(enable_trade_out=False, enable_trade_in=True)
        self.assertTrue(company_pos_analytics_section_visible("tradings"))
        self.assertTrue(company_pos_permission_visible("analytics", "tradings"))
        self._set_pos(enable_trade_in=False)
        self.assertFalse(company_pos_analytics_section_visible("tradings"))
        self.assertFalse(company_pos_permission_visible("analytics", "tradings"))

    def test_deliveries_hidden_when_cart_delivery_off(self):
        from shops.services import (
            company_pos_analytics_section_visible,
            company_pos_permission_visible,
        )

        self._set_pos(enable_cart_delivery=False)
        self.assertFalse(company_pos_analytics_section_visible("deliveries"))
        self.assertFalse(
            company_pos_permission_visible("analytics", "deliveries")
        )
        self._set_pos(enable_cart_delivery=True)
        self.assertTrue(company_pos_analytics_section_visible("deliveries"))

    def test_cash_mpesa_combo_keeps_both_columns(self):
        self._set_pos(
            enable_cash=False, enable_mpesa=False, enable_cash_mpesa=True
        )
        page = self._page("sales")
        grid = next(t for t in page["tables"] if t.get("shop_grid"))
        labels = self._labels(grid["columns"])
        # A combined cash+M-Pesa tender still feeds both columns.
        self.assertIn("Cash", labels)
        self.assertIn("M-Pesa", labels)
        self._assert_rows_align(page)


class AnalyticsDaySessionReportTests(TestCase):
    """Cashiers day sessions open a page with every report for that day."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="870066",
            password="day-pass",
            email="day@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870066",
            phone_country_code="+254",
            phone_number="700001166",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="DAY SHOP",
            location="NAIROBI",
            email="day-shop@test.local",
            phone_number="0700001166",
            login_code="870166",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)

    def _session(self):
        from shops.models import ShopDaySession

        return ShopDaySession.objects.create(
            shop=self.shop,
            opened_by=self.staff,
            opening_cash=1000,
            opening_mpesa=200,
            opening_card=0,
            opening_credit=0,
        )

    def _sale(self, number="SL-D1", total=250):
        return ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number=number,
            client_name="Walk-in",
            subtotal=total,
            tax_percent=0,
            tax_amount=0,
            total=total,
            amount_paid=total,
            cash_amount=total,
            payment_method="cash",
            created_by=self.staff,
        )

    def _close(self, session, *, cash=1250, mpesa=200):
        from datetime import timedelta

        from django.utils import timezone

        session.closing_cash = cash
        session.closing_mpesa = mpesa
        session.closing_card = 0
        session.closing_credit = 0
        session.closed_by = self.staff
        # Nudge past the wall-clock resolution — on Windows `timezone.now()`
        # can return the same tick as rows created just before close, and the
        # report window excludes `created_at == closed_at` (created_at__lt).
        session.closed_at = timezone.now() + timedelta(seconds=1)
        session.save()
        return session

    def _balances_page(self):
        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_analytics_page

        request = RequestFactory().get(
            "/shop-manager/analytics/balances/",
            {"range": "day", "date": timezone.localdate().isoformat()},
        )
        request.user = self.user
        return build_analytics_page(
            profile=self.staff, request=request, section_slug="balances"
        )["page"]

    @staticmethod
    def _labels(columns):
        return [
            (col.get("label") if isinstance(col, dict) else col)
            for col in columns
        ]

    def test_session_rows_link_to_day_report(self):
        session = self._session()
        page = self._balances_page()
        table = next(
            t for t in page["tables"] if t["title"] == "Sessions"
        )
        self.assertEqual(
            self._labels(table["columns"]),
            ["Session", "Cashier", "Shop", "Status", "Report"],
        )
        row = table["rows"][0]
        cells = row["cells"] if isinstance(row, dict) else row
        session_cell = cells[0]
        self.assertIn("Started", session_cell["sub"])
        self.assertIn(
            f"/shop-manager/analytics/balances/{session.pk}/",
            session_cell["href"],
        )
        report_cell = cells[-1]
        self.assertEqual(report_cell["label"], "Open")
        self.assertIn(
            f"/shop-manager/analytics/balances/{session.pk}/",
            report_cell["href"],
        )
        self.assertIn("back=", report_cell["href"])

    def test_day_session_page_lists_all_reports(self):
        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import (
            build_day_session_report_page,
        )

        session = self._session()
        receipt = self._sale()
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item_name="Charger",
            quantity=1,
            unit_price=250,
            line_total=250,
        )
        session = self._close(session)

        request = RequestFactory().get(
            f"/shop-manager/analytics/balances/{session.pk}/"
        )
        request.user = self.user
        context = build_day_session_report_page(
            profile=self.staff, request=request, session=session
        )
        page = context["page"]
        self.assertEqual(context["section_slug"], "balances")
        self.assertTrue(page["hide_date_filters"])
        self.assertIn("/shop-manager/analytics/balances/", page["back_href"])
        self.assertIn("download=1", page["download_href"])
        self.assertEqual(
            [t["title"] for t in page["tables"]],
            [
                "Money flow",
                "Customer prepayments",
                "Credit payments collected",
                "Credit — not in the till",
                "Gifts — not in the till",
                "Items sold",
                "Sales by employee",
                "Pending & cancelled sales",
                "Drawings done",
                "Expenses incurred",
                "Stocked in",
                "Stocked out",
            ],
        )
        flow_labels = [r[0] for r in page["tables"][0]["rows"]]
        self.assertIn("Counted closing", flow_labels)
        self.assertIn("Variance", flow_labels)
        items = page["tables"][5]
        self.assertEqual(items["rows"][0][0], "Charger")
        sellers = page["tables"][6]
        self.assertEqual(sellers["rows"][0][0], "870066")
        self.assertEqual(sellers["rows"][0][1], "1")
        self.assertEqual(sellers["rows"][0][3], "KSh 250.00")
        metric_labels = [m["label"] for m in page["metrics"]]
        self.assertIn("Cashier", metric_labels)
        self.assertIn("Session start", metric_labels)
        self.assertIn("Sales generated", metric_labels)
        end = next(
            m for m in page["metrics"] if m["label"] == "Session end"
        )
        self.assertNotEqual(end["value"], "Still open")

    def test_day_session_open_shows_live_preview(self):
        from django.test import RequestFactory

        from employees.analytics_services import (
            build_day_session_report_page,
        )

        session = self._session()
        request = RequestFactory().get(
            f"/shop-manager/analytics/balances/{session.pk}/"
        )
        request.user = self.user
        page = build_day_session_report_page(
            profile=self.staff, request=request, session=session
        )["page"]
        metric_labels = [m["label"] for m in page["metrics"]]
        self.assertIn("Cashier", metric_labels)
        self.assertIn("Sales generated", metric_labels)
        end = next(
            m for m in page["metrics"] if m["label"] == "Session end"
        )
        self.assertEqual(end["value"], "Still open")
        self.assertTrue(page["alerts"])

    def test_day_session_view_renders_and_streams_pdf(self):
        session = self._session()
        self._sale()
        self.client.force_login(self.user)
        response = self.client.get(
            f"/shop-manager/analytics/balances/{session.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Money flow")
        self.assertContains(response, "Items sold")

        pdf = self.client.get(
            f"/shop-manager/analytics/balances/{session.pk}/",
            {"download": "1"},
        )
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_day_session_404s_for_unallocated_shop(self):
        from shops.models import ShopDaySession

        other_shop = Shop.objects.create(
            name="OTHER SHOP",
            location="NAKURU",
            email="other-shop@test.local",
            phone_number="0700001167",
            login_code="870167",
            password_hash="x",
            created_by=self.staff,
        )
        session = ShopDaySession.objects.create(
            shop=other_shop, opened_by=self.staff
        )
        self.client.force_login(self.user)
        response = self.client.get(
            f"/shop-manager/analytics/balances/{session.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def _report_tables(self, session):
        from django.test import RequestFactory

        from employees.analytics_services import (
            build_day_session_report_page,
        )

        request = RequestFactory().get(
            f"/shop-manager/analytics/balances/{session.pk}/"
        )
        request.user = self.user
        page = build_day_session_report_page(
            profile=self.staff, request=request, session=session
        )["page"]
        return [t["title"] for t in page["tables"]]

    def test_day_report_hides_stock_modes_disabled_on_the_shop(self):
        from shops.models import ShopPosSettings
        from shops.services import (
            _invalidate_pos_settings_cache,
            set_company_pos_setting,
        )

        set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        self.addCleanup(
            set_company_pos_setting, field="enable_shelf_mode", enabled=False
        )
        session = self._session()

        # No shop row → the company mode alone decides.
        titles = self._report_tables(session)
        self.assertIn("Stocked in", titles)
        self.assertIn("Shelf stocked in", titles)

        # Custom shop settings that keep item stock off but shelf on show
        # only the shelf tables.
        ShopPosSettings.objects.create(
            shop=self.shop,
            override_pos=True,
            enable_stock_tracking=False,
            enable_shelf_mode=True,
        )
        # Direct writes bypass the service layer — drop the cached shop row.
        _invalidate_pos_settings_cache(shop_id=self.shop.pk)
        titles = self._report_tables(session)
        self.assertNotIn("Stocked in", titles)
        self.assertNotIn("Stocked out", titles)
        self.assertIn("Shelf stocked in", titles)
        self.assertIn("Shelf stocked out", titles)

        # Company-level off wins even when the shop row enables the mode.
        ShopPosSettings.objects.filter(shop=self.shop).update(
            enable_stock_tracking=True
        )
        _invalidate_pos_settings_cache(shop_id=self.shop.pk)
        set_company_pos_setting(field="enable_shelf_mode", enabled=False)
        titles = self._report_tables(session)
        self.assertIn("Stocked in", titles)
        self.assertNotIn("Shelf stocked in", titles)

    def test_day_report_section_prefs_hide_tables(self):
        session = self._session()

        titles = self._report_tables(session)
        self.assertIn("Expenses incurred", titles)
        self.assertIn("Items sold", titles)

        # Per-employee section picks drop the matching tables — item stock
        # covers both "Stocked in" and "Stocked out".
        self.staff.sidebar_prefs = {
            "day_report_sec_expenses": False,
            "day_report_sec_item_stock": False,
        }
        self.staff.save(update_fields=["sidebar_prefs"])
        titles = self._report_tables(session)
        self.assertNotIn("Expenses incurred", titles)
        self.assertNotIn("Stocked in", titles)
        self.assertNotIn("Stocked out", titles)
        self.assertIn("Items sold", titles)

    def test_edit_session_context_only_when_allowed(self):
        from django.test import RequestFactory

        from employees.analytics_services import (
            build_day_session_report_page,
        )
        from employees.models import EmployeeModulePermission

        session = self._close(self._session())
        request = RequestFactory().get(
            f"/shop-manager/analytics/balances/{session.pk}/"
        )
        request.user = self.user

        # Off by default — the shop floor day report has no edit POST.
        page = build_day_session_report_page(
            profile=self.staff, request=request, session=session
        )["page"]
        self.assertIsNone(page.get("edit_session"))

        page = build_day_session_report_page(
            profile=self.staff,
            request=request,
            session=session,
            allow_edit=True,
        )["page"]
        edit = page["edit_session"]
        self.assertFalse(edit["is_open"])
        self.assertTrue(edit["channels"]["cash"])
        self.assertEqual(edit["opening"]["cash"], "1000")
        self.assertEqual(edit["closing"]["cash"], "1250")

        # An explicit deny hides the action.
        EmployeeModulePermission.objects.create(
            employee=self.staff,
            module_slug="analytics",
            submodule_slug="edit-balances",
            allowed=False,
        )
        delattr(self.staff, "_employee_module_perm_cache")
        page = build_day_session_report_page(
            profile=self.staff,
            request=request,
            session=session,
            allow_edit=True,
        )["page"]
        self.assertIsNone(page.get("edit_session"))

    def test_edit_session_post_updates_opening_and_closing(self):
        from decimal import Decimal

        session = self._close(self._session())
        self.client.force_login(self.user)
        response = self.client.post(
            f"/shop-manager/analytics/balances/{session.pk}/",
            {
                "opening_cash": "900",
                "opening_mpesa": "150",
                "closing_cash": "1150",
                "closing_mpesa": "150",
            },
        )
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.opening_cash, Decimal("900"))
        self.assertEqual(session.opening_mpesa, Decimal("150"))
        self.assertEqual(session.closing_cash, Decimal("1150"))
        self.assertEqual(session.closing_mpesa, Decimal("150"))
        # The audit fields stay untouched.
        self.assertEqual(session.opened_by_id, self.staff.pk)
        self.assertEqual(session.closed_by_id, self.staff.pk)

    def test_edit_session_post_open_session_keeps_closing_null(self):
        session = self._session()
        self.client.force_login(self.user)
        response = self.client.post(
            f"/shop-manager/analytics/balances/{session.pk}/",
            {
                "opening_cash": "800",
                "opening_mpesa": "100",
                "closing_cash": "999",
                "closing_mpesa": "999",
            },
        )
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.opening_cash, 800)
        self.assertEqual(session.opening_mpesa, 100)
        self.assertIsNone(session.closing_cash)
        self.assertIsNone(session.closing_mpesa)

    def test_edit_session_post_requires_permission(self):
        from employees.models import EmployeeModulePermission

        session = self._close(self._session())
        EmployeeModulePermission.objects.create(
            employee=self.staff,
            module_slug="analytics",
            submodule_slug="edit-balances",
            allowed=False,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            f"/shop-manager/analytics/balances/{session.pk}/",
            {
                "opening_cash": "1",
                "opening_mpesa": "1",
                "closing_cash": "1",
                "closing_mpesa": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.opening_cash, 1000)
        self.assertEqual(session.closing_cash, 1250)


class DashboardSidebarLinkTests(TestCase):
    def test_role_dashboard_omits_dashboard_link(self):
        sidebar = sidebar_for_role_dashboard(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Dashboard", labels)
        self.assertIn("Marketing links", labels)
        self.assertIn("Chatbot", labels)
        chatbot = next(item for item in sidebar["primary"] if item["label"] == "Chatbot")
        self.assertEqual(chatbot["href"], reverse("chat:home"))
        self.assertNotIn("WhatsApp", labels)

    def test_it_support_dashboard_splits_stock_links(self):
        sidebar = sidebar_for_role_dashboard(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Stock Management", labels)
        self.assertIn("Item stock management", labels)
        self.assertNotIn("Shelf stock management", labels)
        item = next(
            item
            for item in sidebar["primary"]
            if item["label"] == "Item stock management"
        )
        self.assertIn("/it-support/stock-management/", item["href"])
        self.assertIn("mode=view", item["href"])

        from shops.models import Shop
        from shops.services import set_company_pos_setting

        shop = Shop.objects.create(
            name="SHELF LINK SHOP",
            location="NAIROBI",
            email="shelf-link@test.local",
            phone_number="0700000961",
            login_code="860161",
            password_hash="x",
        )
        set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        self.addCleanup(
            set_company_pos_setting, field="enable_shelf_mode", enabled=False
        )
        self.assertTrue(shop)

        sidebar = sidebar_for_role_dashboard(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertIn("Shelf stock management", labels)
        shelf = next(
            item
            for item in sidebar["primary"]
            if item["label"] == "Shelf stock management"
        )
        self.assertIn("tab=shelf", shelf["href"])

    def test_shop_manager_dashboard_matches_it_support_links(self):
        it_labels = [
            item["label"]
            for item in sidebar_for_role_dashboard(EmployeeRole.IT_SUPPORT)[
                "primary"
            ]
        ]
        manager_sidebar = sidebar_for_role_dashboard(EmployeeRole.SHOP_MANAGER)
        manager_labels = [item["label"] for item in manager_sidebar["primary"]]
        self.assertEqual(manager_labels, it_labels)
        self.assertNotIn("Stock Management", manager_labels)
        self.assertNotIn("Communications", manager_labels)
        self.assertIn("Item stock management", manager_labels)
        self.assertIn("Marketing links", manager_labels)
        self.assertIn("Chatbot", manager_labels)
        item = next(
            item
            for item in manager_sidebar["primary"]
            if item["label"] == "Item stock management"
        )
        self.assertIn("/shop-manager/stock-management/", item["href"])

    def test_it_support_dashboard_hides_item_link_in_shelf_only_mode(self):
        from shops.models import Shop
        from shops.services import set_company_pos_setting

        Shop.objects.create(
            name="SHELF ONLY SHOP",
            location="NAIROBI",
            email="shelf-only@test.local",
            phone_number="0700000962",
            login_code="860162",
            password_hash="x",
        )
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.addCleanup(
            set_company_pos_setting, field="stock_mode_items", enabled=True
        )

        sidebar = sidebar_for_role_dashboard(EmployeeRole.IT_SUPPORT)
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Item stock management", labels)
        self.assertIn("Shelf stock management", labels)

    def test_whatsapp_link_lives_in_marketing_sidebar(self):
        sidebar = sidebar_for_marketing_links(EmployeeRole.IT_SUPPORT)
        inbox = next(
            item for item in sidebar["primary"] if item["label"] == "Inbox"
        )
        self.assertIn("/it-support/whatsapp/inbox/", inbox["href"])

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

    def test_every_role_dashboard_has_clock_link(self):
        for role in EmployeeRole:
            sidebar = sidebar_for_role_dashboard(role)
            labels = [item["label"] for item in sidebar["primary"]]
            self.assertIn("Clock in / out", labels, role)

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
            "stock-take",
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
            "stock_count",
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
                "receipt_kind": "credits",
            }
        )
        labels = " ".join(
            str(cell.get("label") if isinstance(cell, dict) else cell)
            for row in page["tables"][0]["rows"]
            for cell in (row.get("cells") if isinstance(row, dict) else row)
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
    """Stock transfers: sending shop allocated; receiving shop may be any active shop."""

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
        ShopStock.objects.create(shop=self.shop_a, item=self.item, quantity=10)
        self.StockMovementType = StockMovementType

    def test_request_to_unallocated_shop_succeeds(self):
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

    def test_request_from_unallocated_shop_is_rejected(self):
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

    def _row_cells(self, row):
        if isinstance(row, dict):
            return row.get("cells") or []
        return row

    def _row_for(self, page, name):
        for row in page["tables"][0]["rows"]:
            cells = self._row_cells(row)
            if not cells:
                continue
            first = cells[0]
            if isinstance(first, dict) and name in (first.get("label") or ""):
                return cells
        return None

    def test_update_account_follow_up_date_notifies_client(self):
        from datetime import date
        from unittest.mock import patch

        from employees.analytics_services import update_client_credit_follow_up_date
        from shops.models import ClientCreditAccountEvent, ClientCreditAccountEventKind

        receipt = self._make_receipt(
            self.client,
            total=100,
            amount_paid=0,
            credit_due_date=date(2026, 11, 1),
        )
        self.client.credit_follow_up_date = date(2026, 11, 5)
        self.client.credit_follow_up_reminder_sent_on = date(2026, 11, 5)
        self.client.save(
            update_fields=[
                "credit_follow_up_date",
                "credit_follow_up_reminder_sent_on",
            ]
        )
        with patch(
            "communications.automations.maybe_send_credit_follow_up_updated"
        ) as notify:
            notify.return_value = {"sent": True, "reason": ""}
            result = update_client_credit_follow_up_date(
                profile=self.cashier,
                client_id=self.client.pk,
                credit_follow_up_date="2026-12-15",
            )
        self.client.refresh_from_db()
        receipt.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("notified"))
        self.assertEqual(receipt.credit_due_date.isoformat(), "2026-11-01")
        self.assertEqual(self.client.credit_follow_up_date.isoformat(), "2026-12-15")
        self.assertIsNone(self.client.credit_follow_up_reminder_sent_on)
        notify.assert_called_once()
        self.assertEqual(
            notify.call_args.kwargs.get("old_date"), date(2026, 11, 5)
        )
        self.assertTrue(
            ClientCreditAccountEvent.objects.filter(
                client=self.client,
                kind=ClientCreditAccountEventKind.FOLLOW_UP_CHANGED,
            ).exists()
        )

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
        self.assertTrue(receipt.settled_from_credit)
        self.assertEqual(str(receipt.amount_paid), "100.00")
        page = _build_clients(self._filters(receipt_kind="credits"))
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-2]["qty"], "1")
        self.assertEqual(row[-2]["amount"], "0")

    def test_cleared_clients_sort_after_open_balances(self):
        from employees.analytics_services import apply_credit_receipt_payment

        open_client = self._make_client("OPEN CLIENT", "011")
        cleared_client = self._make_client("CLEARED CLIENT", "012")
        self._make_receipt(open_client, total=80, amount_paid=0)
        cleared_receipt = self._make_receipt(cleared_client, total=50, amount_paid=0)
        apply_credit_receipt_payment(
            profile=self.cashier,
            receipt_id=cleared_receipt.pk,
            amount="50",
        )
        page = _build_clients(self._filters(from_credits=True))
        labels = [
            self._row_cells(row)[0]["label"]
            for row in page["tables"][0]["rows"]
            if self._row_cells(row) and isinstance(self._row_cells(row)[0], dict)
        ]
        open_idx = next(i for i, label in enumerate(labels) if "OPEN CLIENT" in label)
        cleared_idx = next(
            i for i, label in enumerate(labels) if "CLEARED CLIENT" in label
        )
        self.assertLess(open_idx, cleared_idx)
        cleared_row = self._row_for(page, "CLEARED CLIENT")
        self.assertEqual(cleared_row[-2]["amount"], "0")

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
        page = _build_clients(self._filters(receipt_kind="credits"))
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-2]["qty"], "1")
        self.assertEqual(row[-2]["amount"], "60")

    def test_cancelled_credit_is_excluded(self):
        self._make_receipt(
            self.client,
            total=200,
            amount_paid=0,
            status=ShopReceiptStatus.CANCELLED,
        )
        page = _build_clients(self._filters(receipt_kind="credits"))
        self.assertIsNone(self._row_for(page, "CREDIT CLIENT"))

    def test_sale_only_client_is_included_on_clients_page(self):
        sale_client = self._make_client("SALE CLIENT", "002")
        self._make_receipt(
            sale_client,
            kind=ShopReceiptKind.SALE,
            total=80,
            amount_paid=80,
        )
        page = _build_clients(self._filters())
        row = self._row_for(page, "SALE CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-1]["qty"], "1")
        self.assertEqual(row[-1]["amount"], "80")
        credits_only = _build_clients(self._filters(receipt_kind="credits"))
        self.assertIsNone(self._row_for(credits_only, "SALE CLIENT"))

    def test_clients_page_filters_by_sales_credits_quotations(self):
        sale_client = self._make_client("SALE ONLY", "021")
        credit_client = self._make_client("CREDIT ONLY", "022")
        quote_client = self._make_client("QUOTE ONLY", "023")
        self._make_receipt(
            sale_client, kind=ShopReceiptKind.SALE, total=50, amount_paid=50
        )
        self._make_receipt(credit_client, total=90, amount_paid=0)
        self._make_receipt(
            quote_client,
            kind=ShopReceiptKind.QUOTATION,
            total=40,
            amount_paid=0,
        )

        all_page = _build_clients(self._filters(receipt_kind="all"))
        self.assertIsNotNone(self._row_for(all_page, "SALE ONLY"))
        self.assertIsNotNone(self._row_for(all_page, "CREDIT ONLY"))
        self.assertIsNotNone(self._row_for(all_page, "QUOTE ONLY"))

        sales_page = _build_clients(self._filters(receipt_kind="sales"))
        self.assertIsNotNone(self._row_for(sales_page, "SALE ONLY"))
        self.assertIsNone(self._row_for(sales_page, "CREDIT ONLY"))
        self.assertIsNone(self._row_for(sales_page, "QUOTE ONLY"))

        credits_page = _build_clients(self._filters(receipt_kind="credits"))
        self.assertIsNone(self._row_for(credits_page, "SALE ONLY"))
        self.assertIsNotNone(self._row_for(credits_page, "CREDIT ONLY"))
        self.assertIsNone(self._row_for(credits_page, "QUOTE ONLY"))

        quotes_page = _build_clients(self._filters(receipt_kind="quotations"))
        self.assertIsNone(self._row_for(quotes_page, "SALE ONLY"))
        self.assertIsNone(self._row_for(quotes_page, "CREDIT ONLY"))
        self.assertIsNotNone(self._row_for(quotes_page, "QUOTE ONLY"))

    def test_clients_page_respects_timeline_filter(self):
        from datetime import datetime, time, timedelta

        from django.utils import timezone

        today = timezone.localdate()
        older = today - timedelta(days=3)
        tz = timezone.get_current_timezone()
        in_period = self._make_receipt(
            self.client, kind=ShopReceiptKind.SALE, total=70, amount_paid=70
        )
        out_period = self._make_receipt(
            self.client, kind=ShopReceiptKind.SALE, total=30, amount_paid=30
        )
        ShopReceipt.objects.filter(pk=in_period.pk).update(
            created_at=timezone.make_aware(datetime.combine(today, time(10, 0)), tz)
        )
        ShopReceipt.objects.filter(pk=out_period.pk).update(
            created_at=timezone.make_aware(datetime.combine(older, time(10, 0)), tz)
        )

        start = timezone.make_aware(datetime.combine(today, time.min), tz)
        end = start + timedelta(days=1)
        page = _build_clients(
            self._filters(
                receipt_kind="sales",
                start=start,
                end=end,
                report_period_label=today.strftime("%d %b %Y"),
            )
        )
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-1]["qty"], "1")
        self.assertEqual(row[-1]["amount"], "70")

    def test_overpayment_does_not_reduce_other_receipt_due(self):
        self._make_receipt(self.client, total=100, amount_paid=150)
        self._make_receipt(self.client, total=80, amount_paid=0)
        page = _build_clients(self._filters(receipt_kind="credits"))
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-2]["qty"], "2")
        self.assertEqual(row[-2]["amount"], "80")

    def test_credits_page_links_use_credits_account_url(self):
        self._make_receipt(self.client)
        page = _build_clients(self._filters(from_credits=True))
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIn("/analytics/credits/clients/", row[0]["href"])
        self.assertIn("all-time", page["tables"][0]["footnote"])
        self.assertIn("Cleared clients", page["tables"][0]["footnote"])

    def test_credit_client_rows_include_stock_value_and_totals(self):
        from shops.models import ShopReceiptLine

        receipt = self._make_receipt(self.client, total=100, amount_paid=0)
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item_name="HDMI CABLE",
            quantity=2,
            unit_price=50,
            unit_cost=30,
            line_total=100,
        )
        page = _build_clients(self._filters(from_credits=True))
        table = page["tables"][0]
        self.assertEqual(table["columns"][-1]["label"], "Stock")
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-2]["qty"], "1")
        self.assertEqual(row[-2]["amount"], "100")
        self.assertEqual(row[-1]["kind"], "money")
        self.assertEqual(row[-1]["label"], "60")
        totals = self._row_cells(table["rows"][-1])
        self.assertEqual(totals[0], "Total")
        self.assertEqual(totals[-2]["amount"], "100")
        self.assertEqual(totals[-1]["label"], "60")

    def test_credits_period_filter_scopes_client_rows_and_alerts(self):
        from datetime import datetime, time, timedelta

        from django.utils import timezone

        today = timezone.localdate()
        older = today - timedelta(days=4)
        tz = timezone.get_current_timezone()
        in_period = self._make_receipt(self.client, total=100, amount_paid=0)
        out_period = self._make_receipt(self.client, total=80, amount_paid=0)
        ShopReceipt.objects.filter(pk=in_period.pk).update(
            created_at=timezone.make_aware(datetime.combine(today, time(11, 0)), tz)
        )
        ShopReceipt.objects.filter(pk=out_period.pk).update(
            created_at=timezone.make_aware(datetime.combine(older, time(11, 0)), tz)
        )

        start = timezone.make_aware(datetime.combine(today, time.min), tz)
        end = start + timedelta(days=1)
        page = _build_clients(
            self._filters(
                from_credits=True,
                start=start,
                end=end,
                report_period_label=today.strftime("%d %b %Y"),
                query=f"shop_id={self.shop.pk}&range=day&date={today.isoformat()}",
            )
        )
        row = self._row_for(page, "CREDIT CLIENT")
        self.assertIsNotNone(row)
        self.assertEqual(row[-2]["qty"], "1")
        self.assertEqual(row[-2]["amount"], "100")
        self.assertIn("this period", page["tables"][0]["footnote"])
        self.assertEqual(len(page["alerts"]), 1)
        self.assertEqual(page["alerts"][0]["href_label"], "View other credits")
        self.assertIn("range=all", page["alerts"][0]["href"])
        self.assertIn(f"shop_id={self.shop.pk}", page["alerts"][0]["href"])

    def test_client_account_filters_by_period_and_notifies_other_credits(self):
        from datetime import datetime, time, timedelta

        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_client_credit_account

        today = timezone.localdate()
        older = today - timedelta(days=2)
        tz = timezone.get_current_timezone()
        in_period = self._make_receipt(self.client, total=120, amount_paid=20)
        out_period = self._make_receipt(self.client, total=50, amount_paid=0)
        ShopReceipt.objects.filter(pk=in_period.pk).update(
            created_at=timezone.make_aware(datetime.combine(today, time(9, 0)), tz)
        )
        ShopReceipt.objects.filter(pk=out_period.pk).update(
            created_at=timezone.make_aware(datetime.combine(older, time(9, 0)), tz)
        )

        request = RequestFactory().get(
            f"/shop-cashier/analytics/credits/clients/{self.client.pk}/",
            {
                "shop_id": str(self.shop.pk),
                "range": "day",
                "date": today.isoformat(),
            },
        )
        account = build_client_credit_account(
            profile=self.cashier, client_id=self.client.pk, request=request
        )
        self.assertEqual(account["receipt_count"], 1)
        self.assertEqual(account["balance_raw"], "100.00")
        self.assertEqual(account["other_credits_count"], 1)
        self.assertIsNotNone(account["other_credits_alert"])
        self.assertIn("range=all", account["all_credits_href"])
        self.assertIn("View other credits", account["other_credits_alert"]["href_label"])

        all_request = RequestFactory().get(
            f"/shop-cashier/analytics/credits/clients/{self.client.pk}/",
            {"shop_id": str(self.shop.pk), "range": "all"},
        )
        all_account = build_client_credit_account(
            profile=self.cashier, client_id=self.client.pk, request=all_request
        )
        self.assertEqual(all_account["receipt_count"], 2)
        self.assertEqual(all_account["other_credits_count"], 0)
        self.assertIsNone(all_account["other_credits_alert"])

    def test_client_transactions_page_filters_and_downloads(self):
        from django.test import RequestFactory

        from employees.analytics_services import (
            build_client_transactions,
            client_transactions_pdf_response,
        )

        from shops.models import ShopReceiptLine

        sale = self._make_receipt(
            self.client, kind=ShopReceiptKind.SALE, total=50, amount_paid=50
        )
        ShopReceiptLine.objects.create(
            receipt=sale,
            item_name="HDMI CABLE",
            quantity=2,
            unit_price=25,
            line_total=50,
        )
        self._make_receipt(self.client, total=80, amount_paid=0)
        self._make_receipt(
            self.client, kind=ShopReceiptKind.QUOTATION, total=30, amount_paid=0
        )

        request = RequestFactory().get(
            f"/shop-cashier/analytics/clients/{self.client.pk}/",
            {"shop_id": str(self.shop.pk), "range": "all"},
        )
        account = build_client_transactions(
            profile=self.cashier, client_id=self.client.pk, request=request
        )
        self.assertEqual(account["receipt_kind"], "all")
        self.assertEqual(account["receipt_count"], 3)
        by_kind = {row["kind_label"]: row for row in account["rows"]}
        self.assertEqual(set(by_kind), {"Sale", "Credit", "Quotation"})
        self.assertEqual(by_kind["Sale"]["status"], "Paid")
        self.assertEqual(by_kind["Credit"]["status"], "Unpaid")
        self.assertIn("download=pdf", account["download_href"])
        self.assertEqual(account["item_count"], 1)
        self.assertEqual(account["item_rows"][0]["item"], "HDMI CABLE")
        self.assertTrue(account["item_rows"][0]["date"])

        paid_credit = self._make_receipt(self.client, total=40, amount_paid=40)
        paid_request = RequestFactory().get(
            f"/shop-cashier/analytics/clients/{self.client.pk}/",
            {
                "shop_id": str(self.shop.pk),
                "range": "all",
                "receipt_kind": "credits",
            },
        )
        credits_account = build_client_transactions(
            profile=self.cashier, client_id=self.client.pk, request=paid_request
        )
        paid_row = next(
            row
            for row in credits_account["rows"]
            if row["pay_id"] == paid_credit.pk
        )
        self.assertEqual(paid_row["status"], "Paid")

        sales_request = RequestFactory().get(
            f"/shop-cashier/analytics/clients/{self.client.pk}/",
            {
                "shop_id": str(self.shop.pk),
                "range": "all",
                "receipt_kind": "sales",
            },
        )
        sales_account = build_client_transactions(
            profile=self.cashier, client_id=self.client.pk, request=sales_request
        )
        self.assertEqual(sales_account["receipt_kind"], "sales")
        self.assertEqual(sales_account["receipt_count"], 1)
        self.assertEqual(sales_account["rows"][0]["kind_label"], "Sale")
        self.assertEqual(sales_account["rows"][0]["status"], "Paid")

        pdf_response = client_transactions_pdf_response(account=account)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", pdf_response["Content-Disposition"])
        self.assertTrue(pdf_response["Content-Disposition"].endswith('.pdf"'))
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))
        self.assertGreater(len(pdf_response.content), 500)


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

    def test_credits_page_offers_report_download(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/credits/?range=all")
        self.assertEqual(response.status_code, 200)
        download_href = response.context["page"]["download_href"]
        self.assertIn("download=pdf", download_href)
        self.assertIn("range=all", download_href)

    def test_credit_items_pdf_download(self):
        self.client.force_login(self.user)
        response = self.client.get(
            "/it-support/analytics/credits/?range=all&download=pdf"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("credit-items-", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 500)

    def test_credits_page_links_to_credit_report(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/credits/?range=all")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "/it-support/analytics/credits/report/",
            response.context["credit_report_href"],
        )
        html = response.content.decode()
        self.assertIn("Credit reports", html)
        self.assertIn("Credit items by shop", html)
        self.assertNotIn("Client credit accounts by shop", html)

    def test_credits_page_due_today_followup_payload(self):
        from datetime import date
        from decimal import Decimal
        from unittest.mock import patch

        from django.utils import timezone

        customer = Client.objects.create(
            full_name="DUE TODAY CLIENT",
            phone_number="0700000949",
            phone_normalized="254700000949",
            created_by=self.it,
        )
        today = date(2026, 11, 11)
        customer.credit_follow_up_date = today
        customer.save(update_fields=["credit_follow_up_date"])
        ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="CR-TODAY-1",
            kind=ShopReceiptKind.CREDIT,
            total=Decimal("300.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.it,
            client=customer,
            client_name=customer.full_name,
            client_phone=customer.phone_number,
            credit_due_date=date(2026, 12, 1),
            status=ShopReceiptStatus.ACTIVE,
        )
        self.client.force_login(self.user)
        with patch.object(timezone, "localdate", return_value=today):
            response = self.client.get("/it-support/analytics/credits/?range=all")
        self.assertEqual(response.status_code, 200)
        followup = response.context["page"].get("credit_due_today") or {}
        self.assertEqual(followup.get("date"), today.isoformat())
        self.assertEqual(followup.get("count"), 1)
        self.assertEqual(len(followup.get("rows") or []), 1)
        row = followup["rows"][0]
        self.assertEqual(row["client_name"], "DUE TODAY CLIENT")
        self.assertEqual(row["open_count"], 1)
        self.assertIn("/analytics/credits/clients/", row["href"])
        html = response.content.decode()
        self.assertIn("Follow-ups due today", html)
        self.assertIn("data-ax-credit-due-modal", html)

    def test_credits_page_due_today_followup_empty_when_none(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/credits/?range=all")
        self.assertEqual(response.status_code, 200)
        followup = response.context["page"].get("credit_due_today") or {}
        self.assertEqual(followup.get("count"), 0)
        self.assertEqual(followup.get("rows") or [], [])
        self.assertNotIn("data-ax-credit-due-modal", response.content.decode())

    def test_credits_report_page_shows_shop_and_item_tables(self):
        self.client.force_login(self.user)
        response = self.client.get(
            "/it-support/analytics/credits/report/?range=all"
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Credits by shop", html)
        self.assertIn("Items picked on credit", html)
        self.assertNotIn("Client credit accounts by shop", html)
        self.assertIn("Back to credits", html)

    def test_credits_report_pdf_download(self):
        self.client.force_login(self.user)
        response = self.client.get(
            "/it-support/analytics/credits/report/?range=all&download=pdf"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("credits-report-", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 500)

    def test_tradings_report_pdf_download(self):
        self.client.force_login(self.user)
        page = self.client.get("/it-support/analytics/tradings/?range=all")
        self.assertEqual(page.status_code, 200)
        self.assertIn("download=pdf", page.context["page"]["download_href"])

        response = self.client.get(
            "/it-support/analytics/tradings/?range=all&download=pdf"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("tradings-report-", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 500)

    def test_client_tradings_report_pdf_download(self):
        self.client.force_login(self.user)
        client = Client.objects.create(
            full_name="TRADE CLIENT",
            phone_number="0700009101",
            phone_normalized="254700009101",
            created_by=self.it,
        )
        ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="TR-1",
            kind=ShopReceiptKind.TRADE_OUT,
            total=150,
            amount_paid=0,
            created_by=self.it,
            client=client,
            client_name=client.full_name,
            client_phone=client.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )
        detail_url = f"/it-support/analytics/tradings/{client.pk}/"
        page = self.client.get(f"{detail_url}?range=all")
        self.assertEqual(page.status_code, 200)
        self.assertIn("download=pdf", page.context["page"]["download_href"])

        response = self.client.get(f"{detail_url}?range=all&download=pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("tradings-trade-client-", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 500)

    def test_fully_returned_trade_stays_visible(self):
        from shops.models import ShopReceiptLine

        self.client.force_login(self.user)
        client = Client.objects.create(
            full_name="RETURN CLIENT",
            phone_number="0700009102",
            phone_normalized="254700009102",
            created_by=self.it,
        )
        receipt = ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="TR-RET-1",
            kind=ShopReceiptKind.TRADE_OUT,
            total=0,
            amount_paid=0,
            created_by=self.it,
            client=client,
            client_name=client.full_name,
            client_phone=client.phone_number,
            status=ShopReceiptStatus.CANCELLED,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item_name="RETURNED TV",
            quantity=1,
            returned_quantity=1,
            unit_price=100,
            line_total=100,
        )

        section = self.client.get("/it-support/analytics/tradings/?range=all")
        self.assertEqual(section.status_code, 200)
        self.assertIn("RETURN CLIENT", section.content.decode())

        page = self.client.get(
            f"/it-support/analytics/tradings/{client.pk}/?range=all"
        )
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("TR-RET-1", html)
        self.assertIn("RETURNED TV", html)
        self.assertIn("Returned", html)

    def test_client_tradings_items_view(self):
        from shops.models import ShopReceiptLine

        self.client.force_login(self.user)
        client = Client.objects.create(
            full_name="ITEM CLIENT",
            phone_number="0700009103",
            phone_normalized="254700009103",
            created_by=self.it,
        )
        trade_out = ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="TR-OUT-1",
            kind=ShopReceiptKind.TRADE_OUT,
            total=300,
            amount_paid=0,
            created_by=self.it,
            client=client,
            client_name=client.full_name,
            client_phone=client.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceiptLine.objects.create(
            receipt=trade_out,
            item_name="TRADED PHONE",
            quantity=2,
            unit_price=150,
            line_total=300,
        )
        trade_in = ShopReceipt.objects.create(
            shop=self.shop_a,
            receipt_number="TR-IN-1",
            kind=ShopReceiptKind.TRADE_IN,
            total=50,
            amount_paid=0,
            created_by=self.it,
            client=client,
            client_name=client.full_name,
            client_phone=client.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )
        ShopReceiptLine.objects.create(
            receipt=trade_in,
            item_name="TRADED CHARGER",
            quantity=1,
            unit_price=50,
            line_total=50,
        )

        detail_url = f"/it-support/analytics/tradings/{client.pk}/"
        page = self.client.get(f"{detail_url}?range=all")
        self.assertEqual(page.status_code, 200)
        tabs = {
            tab["label"]: tab for tab in page.context["page"]["view_tabs"]
        }
        self.assertTrue(tabs["Trades"]["active"])
        self.assertFalse(tabs["Items"]["active"])
        self.assertIn("view=items", tabs["Items"]["href"])
        self.assertIn("range=all", tabs["Items"]["href"])

        items = self.client.get(f"{detail_url}?range=all&view=items")
        self.assertEqual(items.status_code, 200)
        tabs = {
            tab["label"]: tab for tab in items.context["page"]["view_tabs"]
        }
        self.assertTrue(tabs["Items"]["active"])
        html = items.content.decode()
        self.assertIn("TRADED PHONE", html)
        self.assertIn("TRADED CHARGER", html)

        table = items.context["page"]["tables"][0]
        self.assertEqual(table["title"], "Items")
        rows = {
            row["cells"][0]: row["cells"]
            for row in table["rows"]
            if isinstance(row, dict)
        }
        self.assertEqual(rows["TRADED PHONE"][1]["qty"], "0")
        self.assertEqual(rows["TRADED PHONE"][2]["qty"], "2")
        self.assertEqual(rows["TRADED PHONE"][3]["qty"], "2")
        self.assertEqual(rows["TRADED CHARGER"][1]["qty"], "1")
        self.assertEqual(rows["TRADED CHARGER"][2]["qty"], "0")
        self.assertEqual(rows["TRADED CHARGER"][3]["qty"], "1")

        response = self.client.get(
            f"{detail_url}?range=all&view=items&download=pdf"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


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

    def test_audits_pdf_download(self):
        from shops.models import ClientCreditAccountEventKind

        self._log(ClientCreditAccountEventKind.PAYMENT_CASH, amount=40)
        self._log(ClientCreditAccountEventKind.ITEMS_RETURNED)
        self.client.force_login(self.user)
        response = self.client.get(
            "/it-support/analytics/credits/audits/?download=pdf"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("credit-activity-audit-", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 500)

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


class CreditFollowUpsPageTests(TestCase):
    def setUp(self):
        from datetime import date

        self.user = User.objects.create_user(
            username="840031",
            password="follow-pass",
            email="it-follow@test.local",
            first_name="IT",
            last_name="FOLLOW",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840031",
            phone_country_code="+254",
            phone_number="700000971",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop = Shop.objects.create(
            name="FOLLOW SHOP",
            location="NAIROBI",
            email="follow-shop@test.local",
            phone_number="0700000971",
            login_code="840131",
            password_hash="x",
            created_by=self.it,
        )
        self.client_row = Client.objects.create(
            full_name="FOLLOW CLIENT",
            phone_number="0700004011",
            phone_normalized="254700004011",
            created_by=self.it,
            credit_follow_up_date=date(2026, 12, 15),
        )
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="CR-FOLLOW-1",
            kind=ShopReceiptKind.CREDIT,
            total=200,
            amount_paid=0,
            created_by=self.it,
            client=self.client_row,
            client_name=self.client_row.full_name,
            client_phone=self.client_row.phone_number,
            status=ShopReceiptStatus.ACTIVE,
        )

    def test_sidebar_and_page_list_follow_up_and_cleared(self):
        from datetime import date

        from shops.credit_audit import (
            build_credit_follow_ups_page,
            log_credit_follow_up_change,
            log_credit_payment,
        )
        from shops.models import ClientCreditAccountEventKind

        log_credit_follow_up_change(
            client_id=self.client_row.pk,
            old_date=None,
            new_date=date(2026, 12, 15),
            actor=self.it,
            shop=self.shop,
        )
        self.receipt.amount_paid = self.receipt.total
        self.receipt.save(update_fields=["amount_paid"])
        log_credit_payment(
            client_id=self.client_row.pk,
            receipt=self.receipt,
            amount=self.receipt.total,
            payment_method="cash",
            actor=self.it,
        )

        from django.test import RequestFactory

        request = RequestFactory().get(
            "/it-support/analytics/credits/follow-ups/",
            {"range": "year", "year": "2026"},
        )
        page = build_credit_follow_ups_page(profile=self.it, request=request)
        self.assertEqual(page["client_count"], 1)
        self.assertEqual(page["follow_up_count"], 1)
        self.assertEqual(page["cleared_count"], 1)
        row = page["rows"][0]
        self.assertEqual(row["client"], "FOLLOW CLIENT")
        labels = [item["label"] for item in row["outcomes"]]
        self.assertIn("Follow-up set", labels)
        self.assertIn("Credit cleared", labels)

        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/credits/")
        self.assertEqual(response.status_code, 200)
        labels = [
            item["label"]
            for item in response.context["page_sidebar"]["primary"]
        ]
        self.assertIn("Credit follow-up", labels)

        response = self.client.get(
            "/it-support/analytics/credits/follow-ups/",
            {"range": "year", "year": "2026"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["headline"], "Credit follow-up")
        self.assertEqual(response.context["client_count"], 1)
        html = response.content.decode()
        self.assertIn('name="outcome"', html)
        self.assertIn(">Follow-up set</option>", html)
        self.assertIn(">Credit cleared</option>", html)
        self.assertIn('name="range"', html)
        self.assertIn("FOLLOW CLIENT", html)

        filtered = self.client.get(
            "/it-support/analytics/credits/follow-ups/",
            {"range": "year", "year": "2026", "outcome": "cleared"},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.context["outcome_filter"], "cleared")
        self.assertEqual(filtered.context["client_count"], 1)
        self.assertTrue(filtered.context["rows"][0]["cleared"])


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
                "Item stock management",
                "Analytics",
                "Credits",
                "Suppliers",
                "Clients",
                "Cashiers",
                "Item Management",
                "HR Management",
                "Shop Management",
                "Marketing links",
                "Chatbot",
            ],
        )
        marketing_item = next(
            item
            for item in home.context["page_sidebar"]["primary"]
            if item["label"] == "Marketing links"
        )
        self.assertIn("/it-support/marketing/", marketing_item["href"])

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
            ["Dashboard", "Communication settings", "Message drafts", "Share items", "Contacts", "Inbox", "Activities"],
        )
        self.assertIn(
            "/settings/communication-settings/",
            response.context["page_sidebar"]["primary"][1]["href"],
        )
        self.assertIn(
            "/settings/message-drafts/",
            response.context["page_sidebar"]["primary"][2]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/catalogue/",
            response.context["page_sidebar"]["primary"][3]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/contacts/",
            response.context["page_sidebar"]["primary"][4]["href"],
        )
        self.assertIn(
            "/it-support/whatsapp/inbox/",
            response.context["page_sidebar"]["primary"][5]["href"],
        )
        self.assertIn(
            "/it-support/marketing/activities/",
            response.context["page_sidebar"]["primary"][6]["href"],
        )
        shop = response.context["marketing_links"][0]
        self.assertEqual(len(shop["variants"]), 1)
        self.assertEqual(shop["variants"][0]["key"], "local")
        self.assertTrue(shop["variants"][0]["qr"].startswith("data:image/png;base64,"))

    def test_shop_manager_can_open_marketing_links(self):
        user = User.objects.create_user(
            username="850012",
            password="mkt-mgr-pass",
            email="mgr-marketing@test.local",
            first_name="SHOP",
            last_name="MANAGER",
            is_active=True,
        )
        EmployeeProfile.objects.create(
            user=user,
            employee_id="850012",
            phone_country_code="+254",
            phone_number="700000952",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.client.force_login(user)
        response = self.client.get("/it-support/marketing/")
        self.assertEqual(response.status_code, 200)

    @override_settings(IS_HOSTED=True, DARAJA_CALLBACK_BASE_URL="https://shops.example.com")
    def test_marketing_links_include_hosted_qr(self):
        from shops.services import get_company_profile

        # Keep prior main-website fixtures from affecting hosted URL assertions.
        company = get_company_profile()
        company.main_website_shop = None
        company.main_website_domain = ""
        company.save(
            update_fields=["main_website_shop", "main_website_domain", "updated_at"]
        )

        self.client.force_login(self.user)
        response = self.client.get("/it-support/marketing/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["marketing_is_hosted"])
        shop = next(
            link
            for link in response.context["marketing_links"]
            if link["id"] == self.live.pk
        )
        self.assertEqual(shop["local_url"], "")
        self.assertEqual(shop["hosted_url"], f"https://shops.example.com/shop/{self.live.pk}/")
        self.assertEqual(len(shop["variants"]), 1)
        self.assertEqual(shop["variants"][0]["key"], "hosted")
        self.assertEqual(
            shop["variants"][0]["url"],
            f"https://shops.example.com/shop/{self.live.pk}/",
        )
        self.assertTrue(shop["variants"][0]["qr"].startswith("data:image/png;base64,"))
        self.assertContains(response, f"https://shops.example.com/shop/{self.live.pk}/")
        self.assertContains(response, "Hosted")
        self.assertNotContains(response, ">Local<")

    def test_marketing_links_can_set_main_website(self):
        from shops.services import get_company_profile

        self.client.force_login(self.user)
        page = self.client.get("/it-support/marketing/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Main website")
        self.assertContains(page, "How the landing page works")

        # Off → home redirects to login
        off_home = self.client.get("/")
        self.assertEqual(off_home.status_code, 302)
        self.assertEqual(off_home["Location"], "/employees/login/")

        response = self.client.post(
            "/it-support/marketing/",
            {
                "action": "set_main_website",
                "main_website_shop_id": str(self.live.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/it-support/marketing/")

        company = get_company_profile()
        self.assertEqual(company.main_website_shop_id, self.live.pk)

        preview = self.client.get("/website/")
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, self.live.name)

        # On → home shows the selected shop catalogue
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, self.live.name)

        # Clear selection → login again
        cleared = self.client.post(
            "/it-support/marketing/",
            {
                "action": "set_main_website",
                "main_website_shop_id": "",
            },
        )
        self.assertEqual(cleared.status_code, 302)
        company = get_company_profile()
        self.assertIsNone(company.main_website_shop_id)
        off_again = self.client.get("/")
        self.assertEqual(off_again.status_code, 302)
        self.assertEqual(off_again["Location"], "/employees/login/")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ShopAllocationModuleGateTests(TestCase):
    """Shop-scoped staff need allocated shops for stock-management and analytics."""

    def setUp(self):
        self.password = "alloc-gate-pass"
        self.user = User.objects.create_user(
            username="810021",
            password=self.password,
            email="alloc-gate@test.local",
            first_name="ALLOC",
            last_name="CASHIER",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="810021",
            phone_country_code="+254",
            phone_number="700000921",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="ALLOC GATE SHOP",
            location="NAIROBI",
            email="alloc-gate@test.local",
            phone_number="0700000921",
            login_code="810121",
            password_hash="x",
            created_by=self.cashier,
        )

    def test_unallocated_cashier_denied_stock_and_analytics(self):
        self.assertFalse(profile_has_shop_allocation(self.cashier))
        self.assertFalse(employee_may_any(self.cashier, "stock-management"))
        self.assertFalse(employee_may_any(self.cashier, "analytics"))

        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.user
        denied_stock = require_module_permission(
            request, self.cashier, "stock-management", "view", as_json=True
        )
        denied_analytics = require_module_permission(
            request, self.cashier, "analytics", "view", as_json=True
        )
        self.assertEqual(denied_stock.status_code, 403)
        self.assertEqual(denied_analytics.status_code, 403)

        sidebar = sidebar_for_role_dashboard(
            self.cashier.role, profile=self.cashier
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Stock Management", labels)
        self.assertNotIn("Analytics", labels)

    def test_allocated_cashier_may_access_stock_and_analytics(self):
        self.cashier.assigned_shops.add(self.shop)
        self.assertTrue(profile_has_shop_allocation(self.cashier))
        self.assertTrue(employee_may_any(self.cashier, "stock-management"))
        self.assertTrue(employee_may_any(self.cashier, "analytics"))

        self.client.login(username="810021", password=self.password)
        stock = self.client.get("/shop-cashier/stock-management/")
        analytics = self.client.get("/shop-cashier/analytics/")
        self.assertEqual(stock.status_code, 200)
        self.assertEqual(analytics.status_code, 200)

    def test_unallocated_cashier_http_redirected_from_modules(self):
        self.client.login(username="810021", password=self.password)
        stock = self.client.get("/shop-cashier/stock-management/")
        analytics = self.client.get("/shop-cashier/analytics/")
        self.assertEqual(stock.status_code, 302)
        self.assertEqual(analytics.status_code, 302)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class StoreManagerRoleTests(TestCase):
    """Company Store Manager is a separate role limited to store management capabilities."""

    def setUp(self):
        from employees.module_permissions import employee_may, profile_has_shop_allocation
        from employees.workspace import get_dashboard_modules, sidebar_for_role_dashboard
        from items.services import actionable_shops_for_profile
        from shops.models import Shop

        self.employee_may = employee_may
        self.profile_has_shop_allocation = profile_has_shop_allocation
        self.actionable_shops_for_profile = actionable_shops_for_profile
        self.get_dashboard_modules = get_dashboard_modules
        self.sidebar_for_role_dashboard = sidebar_for_role_dashboard

        self.password = "store-mgr-pass"
        self.user = User.objects.create_user(
            username="830031",
            password=self.password,
            email="store-mgr@test.local",
            first_name="STORE",
            last_name="MANAGER",
            is_active=True,
        )
        self.manager = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="830031",
            phone_country_code="+254",
            phone_number="700000931",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.COMPANY_STORE_MANAGER,
        )
        self.shop_a = Shop.objects.create(
            name="STORE MGR SHOP A",
            location="NAIROBI",
            email="store-mgr-a@test.local",
            phone_number="0700000931",
            login_code="830131",
            password_hash="x",
            created_by=self.manager,
        )
        self.shop_b = Shop.objects.create(
            name="STORE MGR SHOP B",
            location="MOMBASA",
            email="store-mgr-b@test.local",
            phone_number="0700000932",
            login_code="830132",
            password_hash="x",
            created_by=self.manager,
        )

    def test_role_label_and_portal_home(self):
        self.assertEqual(self.manager.get_role_display(), "Company Store Manager")
        self.client.login(username="830031", password=self.password)
        response = self.client.get("/company-store-manager/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Company store workspace")

    def test_sees_all_shops_without_allocation(self):
        self.assertFalse(self.manager.assigned_shops.exists())
        self.assertTrue(self.profile_has_shop_allocation(self.manager))
        shops = self.actionable_shops_for_profile(self.manager)
        shop_ids = {shop.pk for shop in shops}
        self.assertEqual(shop_ids, {self.shop_a.pk, self.shop_b.pk})

    def test_dashboard_only_store_modules(self):
        slugs = {m["slug"] for m in self.get_dashboard_modules(EmployeeRole.COMPANY_STORE_MANAGER)}
        self.assertEqual(slugs, set())

        sidebar = self.sidebar_for_role_dashboard(
            EmployeeRole.COMPANY_STORE_MANAGER, profile=self.manager
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(labels, ["Item stock", "Suppliers"])
        self.assertNotIn("Item Management", labels)
        self.assertNotIn("Stock Management", labels)
        self.assertNotIn("Analytics", labels)
        self.assertNotIn("Shop Management", labels)
        self.assertNotIn("HR Management", labels)
        self.assertNotIn("Communications", labels)
        self.assertNotIn("Credits", labels)
        self.assertNotIn("Clients", labels)

    def test_store_stock_in_out_separate_from_shop_stock(self):
        from decimal import Decimal

        from items.models import Item, ShopStock, StoreStock
        from items.store_stock_services import (
            record_store_stock_in,
            record_store_stock_out,
        )

        receiver_user = User.objects.create_user(
            username="830032",
            password=self.password,
            email="recv@test.local",
            first_name="RECV",
            last_name="ER",
            is_active=True,
        )
        receiver = EmployeeProfile.objects.create(
            user=receiver_user,
            employee_id="830032",
            phone_country_code="+254",
            phone_number="700000933",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.EMPLOYEE,
        )
        item = Item.objects.create(
            category="PARTS",
            name="STORE BOLT",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            stock=Decimal("5.000"),
        )
        shop_row = ShopStock.objects.create(
            shop=self.shop_a,
            item=item,
            quantity=Decimal("5.000"),
        )
        company_stock_before = item.stock
        shop_qty_before = shop_row.quantity

        inward = record_store_stock_in(
            actor=self.manager,
            item_id=item.pk,
            quantity="3",
            received_by_id=receiver.pk,
        )
        self.assertEqual(inward.quantity, Decimal("3.000"))
        self.assertEqual(StoreStock.objects.get(item=item).quantity, Decimal("3.000"))

        outward = record_store_stock_out(
            actor=self.manager,
            item_id=item.pk,
            quantity="1",
            received_by_id=receiver.pk,
        )
        self.assertEqual(outward.received_by_id, receiver.pk)
        self.assertEqual(StoreStock.objects.get(item=item).quantity, Decimal("2.000"))

        item.refresh_from_db()
        shop_row.refresh_from_db()
        self.assertEqual(item.stock, company_stock_before)
        self.assertEqual(shop_row.quantity, shop_qty_before)

        self.client.login(username="830031", password=self.password)
        page = self.client.get("/company-store-manager/store-stock-in/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Store stock in")
        self.assertContains(page, "STORE BOLT")
        self.assertContains(page, f'name="qty_{item.pk}"')
        posted = self.client.post(
            "/company-store-manager/store-stock-in/",
            {
                f"qty_{item.pk}": "1",
                f"price_{item.pk}": "12.00",
                "received_by_id": receiver.pk,
                "supplier_name": "Brookside",
                "supplier_phone": "0722000000",
                "payment_status": "paid",
                "note": "delivery",
            },
        )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(StoreStock.objects.get(item=item).quantity, Decimal("3.000"))
        item.refresh_from_db()
        shop_row.refresh_from_db()
        self.assertEqual(item.stock, company_stock_before)
        self.assertEqual(shop_row.quantity, shop_qty_before)

        out_page = self.client.get("/company-store-manager/store-stock-out/")
        self.assertEqual(out_page.status_code, 200)
        self.assertContains(out_page, "STORE BOLT")
        self.assertContains(out_page, f'name="qty_{item.pk}"')

        report = self.client.get("/company-store-manager/store-report/")
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Store report")
        self.assertContains(report, "STORE BOLT")
        filtered = self.client.get(
            "/company-store-manager/store-report/",
            {"range": "all", "item_id": item.pk, "type": "in"},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, "STORE BOLT")
        self.assertContains(filtered, "Stock in")

    def test_store_stock_in_registers_supplier_for_suggest(self):
        from decimal import Decimal

        from items.models import (
            Item,
            ShelfItem,
            ShelfStockMovementType,
            Supplier,
        )
        from items.shelf_stock_services import record_shelf_movement
        from items.store_stock_services import record_store_stock_lines

        item = Item.objects.create(
            category="PARTS",
            name="SUPPLIER BOLT",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            stock=Decimal("5.000"),
        )
        record_store_stock_lines(
            mode="in",
            actor=self.manager,
            lines=[(item.pk, Decimal("2"), Decimal("12.00"))],
            received_by_id=self.manager.pk,
            supplier_name="Brookside Distributors",
            supplier_phone="+254 722 000 000",
            payment_status="paid",
        )
        supplier = Supplier.objects.get(phone_number="722000000")
        self.assertEqual(supplier.name, "BROOKSIDE DISTRIBUTORS")
        self.assertEqual(supplier.phone_country_code, "+254")

        # Plain local format lands on the default +254 dial.
        record_store_stock_lines(
            mode="in",
            actor=self.manager,
            lines=[(item.pk, Decimal("1"), None)],
            received_by_id=self.manager.pk,
            supplier_name="Local Farm",
            supplier_phone="0733 111 222",
        )
        local = Supplier.objects.get(name="LOCAL FARM")
        self.assertEqual(local.phone_country_code, "+254")
        self.assertEqual(local.phone_number, "733111222")

        # Shelf stock-in registers too; stock-outs do not.
        shelf = ShelfItem.objects.create(
            shop=self.shop_a,
            name="SALT 1KG",
            unit="PACKETS",
            quantity=Decimal("0"),
            created_by=self.manager,
        )
        record_shelf_movement(
            shop=self.shop_a,
            shelf_item_id=shelf.pk,
            mode=ShelfStockMovementType.IN,
            qty="4",
            actor=self.manager,
            supplier_name="Shelf Supplier",
            supplier_phone="0711 222 333",
        )
        self.assertTrue(Supplier.objects.filter(name="SHELF SUPPLIER").exists())
        record_shelf_movement(
            shop=self.shop_a,
            shelf_item_id=shelf.pk,
            mode=ShelfStockMovementType.OUT,
            qty="1",
            actor=self.manager,
            supplier_name="Not A Supplier",
            supplier_phone="0799 000 000",
        )
        self.assertFalse(Supplier.objects.filter(name="NOT A SUPPLIER").exists())

        # The stock-in page carries the suggest markup + search URL.
        self.client.login(username="830031", password=self.password)
        page = self.client.get("/company-store-manager/store-stock-in/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-supplier-search-url")
        self.assertContains(page, 'data-supplier-search="name"')
        self.assertContains(page, 'data-supplier-search="phone"')
        self.assertContains(page, "data-supplier-suggest")

        # The stock-in popup on the item-stock page carries it too.
        items_page = self.client.get("/company-store-manager/item-stock/")
        self.assertEqual(items_page.status_code, 200)
        self.assertContains(items_page, 'data-modal="store-stock-in"')
        self.assertContains(items_page, "data-supplier-search-url")
        self.assertContains(items_page, 'data-supplier-search="name"')
        self.assertContains(items_page, 'data-supplier-search="phone"')
        self.assertContains(items_page, "data-supplier-suggest")

    def test_store_stock_trade_give_and_receive(self):
        from decimal import Decimal

        from items.models import Item, StoreStock, StoreStockMovement

        item = Item.objects.create(
            category="PARTS",
            name="TRADE WIDGET",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
            stock=Decimal("5.000"),
        )
        other = Item.objects.create(
            category="PARTS",
            name="TRADE GADGET",
            minimum_selling_price=Decimal("8.00"),
            shop_price=Decimal("9.00"),
            stock=Decimal("2.000"),
        )
        StoreStock.objects.create(item=item, quantity=Decimal("4.000"))

        self.client.login(username="830031", password=self.password)

        page = self.client.get("/company-store-manager/item-stock/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Item stock trade")
        self.assertContains(page, 'data-modal="store-stock-trade"')

        trade_page = self.client.get("/company-store-manager/store-stock-trade/")
        self.assertEqual(trade_page.status_code, 200)
        self.assertContains(trade_page, "Item stock trade")
        self.assertContains(trade_page, 'name="trade_kind"')
        self.assertContains(trade_page, f'name="qty_{item.pk}"')

        posted = self.client.post(
            "/company-store-manager/store-stock-trade/",
            {
                "trade_kind": "trade_out",
                f"qty_{item.pk}": "2",
                "person_name": "Jane Trader",
                "person_phone": "0722000000",
                "note": "swap deal",
            },
        )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(
            StoreStock.objects.get(item=item).quantity, Decimal("2.000")
        )

        out_move = StoreStockMovement.objects.get(item=item)
        self.assertEqual(out_move.movement_type, "out")
        self.assertEqual(out_move.reason, "trade_out")
        self.assertEqual(out_move.supplier_name, "JANE TRADER")
        self.assertEqual(out_move.supplier_phone, "0722000000")

        posted = self.client.post(
            "/company-store-manager/store-stock-trade/",
            {
                "trade_kind": "trade_in",
                f"qty_{other.pk}": "3",
                f"price_{other.pk}": "7.50",
                "person_name": "Jane Trader",
                "person_phone": "0722000000",
            },
        )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(
            StoreStock.objects.get(item=other).quantity, Decimal("3.000")
        )

        in_move = StoreStockMovement.objects.get(item=other)
        self.assertEqual(in_move.movement_type, "in")
        self.assertEqual(in_move.reason, "trade_in")
        self.assertEqual(in_move.buying_price, Decimal("7.50"))

        # A trade without a direction is rejected.
        no_kind = self.client.post(
            "/company-store-manager/store-stock-trade/",
            {f"qty_{item.pk}": "1"},
        )
        self.assertEqual(no_kind.status_code, 200)
        self.assertEqual(
            StoreStock.objects.get(item=item).quantity, Decimal("2.000")
        )

        # An empty trade is rejected.
        empty = self.client.post(
            "/company-store-manager/store-stock-trade/",
            {"trade_kind": "trade_out"},
        )
        self.assertEqual(empty.status_code, 200)

        # Giving more than the store holds is rejected.
        too_much = self.client.post(
            "/company-store-manager/store-stock-trade/",
            {"trade_kind": "trade_out", f"qty_{item.pk}": "999"},
        )
        self.assertEqual(too_much.status_code, 200)
        self.assertEqual(
            StoreStock.objects.get(item=item).quantity, Decimal("2.000")
        )

    def test_analytics_sections_limited_to_store_ops(self):
        may = self.employee_may
        self.assertFalse(may(self.manager, "analytics", "view"))
        self.assertTrue(may(self.manager, "analytics", "items"))
        self.assertTrue(may(self.manager, "analytics", "stock"))
        self.assertTrue(may(self.manager, "analytics", "tradings"))
        self.assertFalse(may(self.manager, "analytics", "credits"))
        self.assertFalse(may(self.manager, "analytics", "clients"))
        self.assertFalse(may(self.manager, "analytics", "sales"))
        self.assertTrue(may(self.manager, "analytics", "suppliers"))

        from employees.workspace import sidebar_for_analytics

        sidebar = sidebar_for_analytics(
            EmployeeRole.COMPANY_STORE_MANAGER, active_view="items", profile=self.manager
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertNotIn("Overview", labels)
        self.assertIn("Items", labels)
        self.assertIn("Item report", labels)
        self.assertIn("Stock", labels)
        self.assertIn("Tradings", labels)
        self.assertNotIn("Credits", labels)
        self.assertNotIn("Sales", labels)
        self.assertNotIn("Revenue", labels)
        self.assertNotIn("Clients", labels)
        self.assertNotIn("Suppliers", labels)

        self.client.login(username="830031", password=self.password)
        response = self.client.get("/company-store-manager/analytics/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/company-store-manager/analytics/items/", response.url)
        items = self.client.get("/company-store-manager/analytics/items/")
        self.assertEqual(items.status_code, 200)

    def test_default_permissions_are_store_only(self):
        may = self.employee_may
        self.assertTrue(may(self.manager, "item-management", "view"))
        self.assertTrue(may(self.manager, "item-management", "register"))
        self.assertTrue(may(self.manager, "item-management", "edit"))
        self.assertFalse(may(self.manager, "shop-management", "view"))
        self.assertFalse(may(self.manager, "shop-management", "edit"))
        self.assertTrue(may(self.manager, "stock-management", "in"))
        self.assertFalse(may(self.manager, "stock-management", "settings"))
        self.assertTrue(may(self.manager, "my-shop", "workspace"))
        self.assertTrue(may(self.manager, "my-shop", "shop_settings"))
        self.assertFalse(may(self.manager, "settings", "home"))
        self.assertFalse(may(self.manager, "whatsapp", "view"))
        self.assertFalse(may(self.manager, "hr-management", "home"))

    def test_company_modules_are_blocked(self):
        self.client.login(username="830031", password=self.password)
        shop_mgmt = self.client.get("/company-store-manager/shop-management/")
        settings = self.client.get("/company-store-manager/")  # home ok
        whatsapp = self.client.get("/company-store-manager/whatsapp/")
        item_mgmt = self.client.get("/company-store-manager/item-management/")
        self.assertEqual(shop_mgmt.status_code, 302)
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(whatsapp.status_code, 302)
        self.assertEqual(item_mgmt.status_code, 200)

    def test_dashboard_offers_stock_report_generator(self):
        self.client.login(username="830031", password=self.password)
        response = self.client.get("/company-store-manager/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Generate stock report")
        self.assertContains(
            response, 'action="/company-store-manager/store-report/"'
        )
        self.assertContains(response, 'name="download"')
        self.assertContains(response, 'name="item_id"')

    def test_store_report_download_streams_period_pdf(self):
        from decimal import Decimal

        from items.models import Item
        from items.store_stock_services import (
            build_store_item_stock_report,
            record_store_stock_in,
            record_store_stock_out,
        )

        item = Item.objects.create(
            category="PARTS",
            name="REPORT BOLT",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
        )
        record_store_stock_in(
            actor=self.manager,
            item_id=item.pk,
            quantity="5",
            received_by_id=self.manager.pk,
        )
        record_store_stock_out(
            actor=self.manager,
            item_id=item.pk,
            quantity="2",
            received_by_id=self.manager.pk,
        )

        report = build_store_item_stock_report(day_start=None, day_end=None)
        row = next(r for r in report["rows"] if r["id"] == item.pk)
        self.assertEqual(row["start"], Decimal("0"))
        self.assertEqual(row["in"], Decimal("5.000"))
        self.assertEqual(row["out"], Decimal("2.000"))
        self.assertEqual(row["close"], Decimal("3.000"))

        self.client.login(username="830031", password=self.password)
        pdf = self.client.get(
            "/company-store-manager/store-report/", {"download": "1"}
        )
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("store-stock-report", pdf["Content-Disposition"])

    def test_store_item_stock_report_period_bounds(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from items.models import Item, StoreStockMovement
        from items.store_stock_services import (
            build_store_item_stock_report,
            record_store_stock_in,
            record_store_stock_out,
            record_store_trade,
        )

        item = Item.objects.create(
            category="PARTS",
            name="PERIOD BOLT",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("12.00"),
        )
        record_store_stock_in(
            actor=self.manager,
            item_id=item.pk,
            quantity="5",
            received_by_id=self.manager.pk,
        )
        StoreStockMovement.objects.filter(item=item).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        record_store_stock_out(
            actor=self.manager,
            item_id=item.pk,
            quantity="2",
            received_by_id=self.manager.pk,
        )
        record_store_trade(
            actor=self.manager,
            kind="trade_out",
            person_name="Jane Trader",
            person_phone="0722000000",
            lines=[(item.pk, Decimal("1"), None)],
        )

        now = timezone.now()
        report = build_store_item_stock_report(
            day_start=now - timedelta(days=2),
            day_end=now + timedelta(days=1),
        )
        row = next(r for r in report["rows"] if r["id"] == item.pk)
        self.assertEqual(row["start"], Decimal("5.000"))
        self.assertEqual(row["in"], Decimal("0"))
        self.assertEqual(row["out"], Decimal("2.000"))
        self.assertEqual(row["trades"], Decimal("-1.000"))
        self.assertEqual(row["close"], Decimal("2.000"))

    def test_shop_manager_role_still_exists(self):
        self.assertNotEqual(EmployeeRole.SHOP_MANAGER, EmployeeRole.COMPANY_STORE_MANAGER)
        self.assertEqual(EmployeeRole.SHOP_MANAGER.label, "Shop Manager")
        self.assertEqual(EmployeeRole.COMPANY_STORE_MANAGER.label, "Company Store Manager")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ShopStoreManagerRoleTests(TestCase):
    """Shop Store Manager shares the store-ops allow-list but is scoped to
    allocated shops (no central store, no unallocated shops)."""

    def setUp(self):
        from employees.module_permissions import employee_may, profile_has_shop_allocation
        from employees.workspace import get_dashboard_modules, sidebar_for_role_dashboard
        from items.services import actionable_shops_for_profile
        from shops.models import Shop

        self.employee_may = employee_may
        self.profile_has_shop_allocation = profile_has_shop_allocation
        self.actionable_shops_for_profile = actionable_shops_for_profile
        self.get_dashboard_modules = get_dashboard_modules
        self.sidebar_for_role_dashboard = sidebar_for_role_dashboard

        self.password = "shop-store-mgr-pass"
        self.user = User.objects.create_user(
            username="840031",
            password=self.password,
            email="shop-store-mgr@test.local",
            first_name="SHOP STORE",
            last_name="MANAGER",
            is_active=True,
        )
        self.manager = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840031",
            phone_country_code="+254",
            phone_number="700000941",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_STORE_MANAGER,
        )
        self.shop_a = Shop.objects.create(
            name="SHOP STORE MGR A",
            location="NAIROBI",
            email="shop-store-mgr-a@test.local",
            phone_number="0700000941",
            login_code="840131",
            password_hash="x",
            created_by=self.manager,
        )
        self.shop_b = Shop.objects.create(
            name="SHOP STORE MGR B",
            location="MOMBASA",
            email="shop-store-mgr-b@test.local",
            phone_number="0700000942",
            login_code="840132",
            password_hash="x",
            created_by=self.manager,
        )

    def test_role_label_and_portal_home(self):
        self.assertEqual(self.manager.get_role_display(), "Shop Store Manager")
        self.client.login(username="840031", password=self.password)
        response = self.client.get("/shop-store-manager/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shop store workspace")

    def test_limited_to_allocated_shops(self):
        self.assertFalse(self.profile_has_shop_allocation(self.manager))
        self.assertEqual(self.actionable_shops_for_profile(self.manager), [])

        self.manager.assigned_shops.add(self.shop_a)
        self.assertTrue(self.profile_has_shop_allocation(self.manager))
        self.assertEqual(
            self.actionable_shops_for_profile(self.manager), [self.shop_a]
        )

    def test_default_permissions_match_company_store_manager(self):
        may = self.employee_may
        self.assertTrue(may(self.manager, "item-management", "view"))
        self.assertTrue(may(self.manager, "item-management", "edit"))
        self.assertTrue(may(self.manager, "stock-management", "in"))
        self.assertTrue(may(self.manager, "stock-management", "out"))
        self.assertTrue(may(self.manager, "analytics", "stock"))
        self.assertTrue(may(self.manager, "my-shop", "sale"))
        self.assertTrue(may(self.manager, "my-shop", "workspace"))
        self.assertFalse(may(self.manager, "shop-management", "view"))
        self.assertFalse(may(self.manager, "settings", "home"))
        self.assertFalse(may(self.manager, "hr-management", "home"))

    def test_dashboard_shows_shop_scoped_modules_only(self):
        self.manager.assigned_shops.add(self.shop_a)
        sidebar = self.sidebar_for_role_dashboard(
            EmployeeRole.SHOP_STORE_MANAGER, profile=self.manager
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertIn("Item Management", labels)
        self.assertIn("Stock Management", labels)
        self.assertIn("Analytics", labels)
        self.assertIn("Suppliers", labels)
        # No central-store entry links.
        self.assertNotIn("Item stock", labels)
        self.assertNotIn("Shelf stock", labels)

    def test_central_store_pages_are_company_only(self):
        self.client.login(username="840031", password=self.password)
        for url in (
            "/company-store-manager/item-stock/",
            "/company-store-manager/store-stock-in/",
            "/company-store-manager/store-stock-out/",
            "/company-store-manager/store-report/",
            "/company-store-manager/shelf-stock/",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/shop-store-manager/", response["Location"], url)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class RiderRoleTests(TestCase):
    """Rider has an empty dashboard and no modules by default."""

    def setUp(self):
        from employees.module_permissions import employee_may
        from employees.workspace import get_dashboard_modules, sidebar_for_role_dashboard

        self.employee_may = employee_may
        self.get_dashboard_modules = get_dashboard_modules
        self.sidebar_for_role_dashboard = sidebar_for_role_dashboard

        self.password = "rider-pass"
        self.user = User.objects.create_user(
            username="840041",
            password=self.password,
            email="rider@test.local",
            first_name="RIDE",
            last_name="R",
            is_active=True,
        )
        self.rider = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840041",
            phone_country_code="+254",
            phone_number="700000941",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.RIDER,
        )

    def test_role_label_and_delivery_nav(self):
        self.assertEqual(self.rider.get_role_display(), "Rider")
        self.assertEqual(self.get_dashboard_modules(EmployeeRole.RIDER), [])

        sidebar = self.sidebar_for_role_dashboard(
            EmployeeRole.RIDER, profile=self.rider
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(
            labels,
            ["Open deliveries", "My active deliveries", "Completed deliveries"],
        )
        self.assertEqual(sidebar["primary"][0]["href"], "/rider/")

        self.client.login(username="840041", password=self.password)
        home = self.client.get("/rider/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Open deliveries")
        self.assertContains(home, "My active")
        self.assertContains(home, "Completed")

        open_page = self.client.get("/rider/open/")
        self.assertEqual(open_page.status_code, 302)
        self.assertIn("/rider/", open_page.url)

        active_page = self.client.get("/rider/active/")
        done_page = self.client.get("/rider/completed/")
        self.assertEqual(active_page.status_code, 200)
        self.assertEqual(done_page.status_code, 200)

        via_dashboard = self.client.get("/employees/dashboard/")
        self.assertEqual(via_dashboard.status_code, 302)
        self.assertIn("/rider/", via_dashboard.url)

    def test_modules_are_blocked_by_default(self):
        self.assertFalse(self.employee_may(self.rider, "stock-management", "view"))
        self.assertFalse(self.employee_may(self.rider, "my-shop", "workspace"))
        self.assertFalse(self.employee_may(self.rider, "settings", "home"))

        self.client.login(username="840041", password=self.password)
        stock = self.client.get("/rider/stock-management/")
        self.assertEqual(stock.status_code, 302)
        self.assertIn("/rider/", stock.url)


class CompanyAccountantRoleTests(TestCase):
    """Company accountant: read-only analytics + stock oversight, all shops."""

    def setUp(self):
        from employees.module_permissions import employee_may, profile_has_shop_allocation
        from employees.workspace import sidebar_for_role_dashboard

        self.employee_may = employee_may
        self.profile_has_shop_allocation = profile_has_shop_allocation
        self.sidebar_for_role_dashboard = sidebar_for_role_dashboard

        self.password = "acct-pass"
        self.user = User.objects.create_user(
            username="850051",
            password=self.password,
            email="acct@test.local",
            first_name="ACCOUNT",
            last_name="ANT",
            is_active=True,
        )
        self.accountant = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="850051",
            phone_country_code="+254",
            phone_number="700000951",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.COMPANY_ACCOUNTANT,
        )
        self.shop = Shop.objects.create(
            name="ACCT SHOP",
            location="NAIROBI",
            email="acct-shop@test.local",
            phone_number="0700000951",
            login_code="850151",
            password_hash="x",
            created_by=self.accountant,
        )

    def test_role_label_and_portal_home(self):
        self.assertEqual(
            self.accountant.get_role_display(), "Company Accountant"
        )
        self.client.login(username="850051", password=self.password)
        response = self.client.get("/company-accountant/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accountant workspace")

        via_dashboard = self.client.get("/employees/dashboard/")
        self.assertEqual(via_dashboard.status_code, 302)
        self.assertIn("/company-accountant/", via_dashboard.url)

    def test_dashboard_sidebar_links(self):
        sidebar = self.sidebar_for_role_dashboard(
            EmployeeRole.COMPANY_ACCOUNTANT, profile=self.accountant
        )
        labels = [item["label"] for item in sidebar["primary"]]
        self.assertEqual(
            labels,
            [
                "Stock Management",
                "Analytics",
                "Cashiers",
                "Suppliers",
                "Prepayments",
                "Clock in / out",
            ],
        )
        accounts_link = next(
            item
            for item in sidebar["footer"]
            if item["label"] == "Accounts settings"
        )
        self.assertEqual(
            accounts_link["href"],
            "/company-accountant/analytics/balances/?mode=settings",
        )

    def test_default_permissions_read_only_scope(self):
        for module, submodule in (
            ("analytics", "view"),
            ("analytics", "balances"),
            ("analytics", "stock"),
            ("analytics", "suppliers"),
            ("stock-management", "view"),
            ("stock-management", "movements"),
            ("stock-management", "report"),
            ("settings", "company-pos"),
            ("my-shop", "prepayments"),
        ):
            self.assertTrue(
                self.employee_may(self.accountant, module, submodule),
                f"{module}/{submodule} should be allowed by default",
            )
        for module, submodule in (
            ("analytics", "revenue"),
            ("analytics", "expenses"),
            ("analytics", "account_pay"),
            ("analytics", "confirm-receipts"),
            ("analytics", "return-receipts"),
            ("stock-management", "in"),
            ("stock-management", "out"),
            ("stock-management", "request"),
            ("stock-management", "serials"),
            ("stock-management", "settings"),
            ("item-management", "view"),
            ("hr-management", "home"),
            ("shop-management", "view"),
            ("my-shop", "workspace"),
            ("settings", "home"),
        ):
            self.assertFalse(
                self.employee_may(self.accountant, module, submodule),
                f"{module}/{submodule} should be denied by default",
            )

    def test_sees_all_shops_without_allocation(self):
        self.assertFalse(self.accountant.assigned_shops.exists())
        self.assertTrue(self.profile_has_shop_allocation(self.accountant))
        shop_ids = {
            shop.pk for shop in actionable_shops_for_profile(self.accountant)
        }
        self.assertIn(self.shop.pk, shop_ids)

    def test_allowed_pages_render(self):
        self.client.login(username="850051", password=self.password)
        for url in (
            "/company-accountant/analytics/",
            "/company-accountant/analytics/balances/",
            "/company-accountant/analytics/balances/?mode=settings",
            "/company-accountant/analytics/stock/",
            "/company-accountant/analytics/suppliers/",
            "/company-accountant/stock-management/?mode=view",
            "/company-accountant/stock-management/?mode=movements",
            "/company-accountant/stock-management/?mode=report",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_denied_pages_redirect_home(self):
        self.client.login(username="850051", password=self.password)
        for url in (
            "/company-accountant/analytics/revenue/",
            "/company-accountant/stock-management/?mode=in",
            "/company-accountant/stock-management/?mode=out",
            "/company-accountant/stock-management/?mode=serials",
            "/company-accountant/item-management/",
            "/company-accountant/hr-management/",
            "/company-accountant/shop-management/",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/company-accountant/", response.url, url)

    def test_overview_lists_employee_performance(self):
        from decimal import Decimal

        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_analytics_page

        cashier_user = User.objects.create_user(
            username="850052",
            password="x",
            first_name="Cash",
            last_name="Ier",
            is_active=True,
        )
        cashier = EmployeeProfile.objects.create(
            user=cashier_user,
            employee_id="850052",
            phone_country_code="+254",
            phone_number="700000952",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number="SL-EMP1",
            client_name="Walk-in",
            subtotal=200,
            tax_percent=0,
            tax_amount=0,
            total=200,
            amount_paid=200,
            cash_amount=200,
            payment_method="cash",
            created_by=cashier,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item_name="Perf Item",
            quantity=2,
            unit_price=100,
            unit_cost=40,
            line_total=200,
        )
        request = RequestFactory().get(
            "/company-accountant/analytics/",
            {"range": "day", "date": timezone.localdate().isoformat()},
        )
        request.user = self.user
        page = build_analytics_page(
            profile=self.accountant,
            request=request,
            section_slug="overview",
        )["page"]
        table = next(
            t for t in page["tables"] if t["title"] == "Employee performance"
        )
        labels = [
            (col.get("label") if isinstance(col, dict) else col)
            for col in table["columns"]
        ]
        self.assertEqual(
            labels,
            [
                "Employee",
                "Sales",
                "Credits",
                "Total revenue",
                "Stock sold",
                "Gross margin",
                "Margin",
            ],
        )
        self.assertEqual(len(table["rows"]), 2)
        row = table["rows"][0]
        self.assertEqual(row[0], "Cash Ier · 850052")
        self.assertEqual(row[1]["qty"], "1")
        self.assertEqual(row[1]["amount"], "200")
        self.assertEqual(row[3]["amount"], "200")
        self.assertEqual(row[4]["label"], "80")
        self.assertEqual(row[5]["label"], "120")
        self.assertEqual(row[6]["label"], "60.0%")
        total_row = table["rows"][1]
        self.assertEqual(total_row[0], "All employees")
        self.assertEqual(total_row[1]["qty"], "1")
        self.assertEqual(total_row[3]["amount"], "200")

    def test_explicit_permission_row_overrides_default(self):
        from employees.models import EmployeeModulePermission

        EmployeeModulePermission.objects.create(
            employee=self.accountant,
            module_slug="analytics",
            submodule_slug="revenue",
            allowed=True,
        )
        self.assertTrue(
            self.employee_may(self.accountant, "analytics", "revenue")
        )
        EmployeeModulePermission.objects.create(
            employee=self.accountant,
            module_slug="analytics",
            submodule_slug="balances",
            allowed=False,
        )
        # The permission cache is per profile instance — refetch to see the row.
        fresh = EmployeeProfile.objects.get(pk=self.accountant.pk)
        self.assertFalse(
            self.employee_may(fresh, "analytics", "balances")
        )


class EmployeeHomeReceiptPreviewTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        self.Decimal = Decimal
        self.password = "emp-home-pass"
        self.user = User.objects.create_user(
            username="860041",
            password=self.password,
            email="emp-home@test.local",
            first_name="MARY",
            last_name="PRINTS",
            is_active=True,
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="860041",
            phone_country_code="+254",
            phone_number="700009941",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.EMPLOYEE,
        )
        other_user = User.objects.create_user(
            username="860042",
            password=self.password,
            email="emp-other@test.local",
            is_active=True,
        )
        self.other = EmployeeProfile.objects.create(
            user=other_user,
            employee_id="860042",
            phone_country_code="+254",
            phone_number="700009942",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.EMPLOYEE,
        )
        self.shop = Shop.objects.create(
            name="EMP HOME SHOP",
            location="NAIROBI",
            email="emp-home-shop@test.local",
            phone_number="0700009941",
            login_code="850241",
            password_hash="x",
            created_by=self.employee,
        )
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="EMP-HOME-1",
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.CONFIRMED,
            total=Decimal("120.00"),
            amount_paid=Decimal("120.00"),
            created_by=self.employee,
            client_name="WALK IN",
        )
        ShopReceiptLine.objects.create(
            receipt=self.receipt,
            item_name="TEST WIDGET",
            quantity=Decimal("2.000"),
            unit_price=Decimal("60.00"),
            line_total=Decimal("120.00"),
        )
        self.other_receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="EMP-HOME-2",
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.ACTIVE,
            total=Decimal("40.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.other,
        )

    def test_home_lists_printed_by_and_view_button(self):
        self.client.force_login(self.user)
        response = self.client.get("/employee/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Printed by")
        self.assertContains(response, "MARY PRINTS")
        self.assertContains(
            response,
            f'data-detail-url="/employee/receipts/{self.receipt.pk}/"',
        )

    def test_receipt_detail_returns_payload_for_own_receipt(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/employee/receipts/{self.receipt.pk}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["receipt"]["receipt_number"], "EMP-HOME-1")
        self.assertEqual(payload["receipt"]["status"], "confirmed")
        self.assertEqual(payload["receipt"]["status_label"], "Confirmed")
        self.assertEqual(payload["receipt"]["cashier"], "MARY PRINTS")
        self.assertEqual(
            payload["receipt"]["lines"][0]["item_name"], "TEST WIDGET"
        )

    def test_receipt_detail_rejects_other_employees_receipt(self):
        self.client.force_login(self.user)
        response = self.client.get(
            f"/employee/receipts/{self.other_receipt.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_receipt_detail_requires_employee_role(self):
        self.client.force_login(self.other.user)
        admin_user = User.objects.create_user(
            username="860043",
            password=self.password,
            email="emp-admin@test.local",
            is_active=True,
        )
        EmployeeProfile.objects.create(
            user=admin_user,
            employee_id="860043",
            phone_country_code="+254",
            phone_number="700009943",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SUPER_ADMIN,
        )
        self.client.force_login(admin_user)
        response = self.client.get(f"/employee/receipts/{self.receipt.pk}/")
        self.assertEqual(response.status_code, 302)


class AnalyticsEmployeePageTests(TestCase):
    """Analytics → Employees rows open that employee's items-sold page."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="840021",
            password="emp-analytics-pass",
            email="it-emp-analytics@test.local",
            first_name="IT",
            last_name="SUPPORT",
            is_active=True,
        )
        self.it = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="840021",
            phone_country_code="+254",
            phone_number="700000951",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )
        self.shop_a = Shop.objects.create(
            name="EMP SHOP A",
            location="NAIROBI",
            email="emp-a@test.local",
            phone_number="0700000951",
            login_code="840121",
            password_hash="x",
            created_by=self.it,
        )
        self.shop_b = Shop.objects.create(
            name="EMP SHOP B",
            location="MOMBASA",
            email="emp-b@test.local",
            phone_number="0700000952",
            login_code="840122",
            password_hash="x",
            created_by=self.it,
        )
        cashier_user = User.objects.create_user(
            username="840022",
            password="cashier-pass",
            email="cashier-analytics@test.local",
            first_name="CASH",
            last_name="IER",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=cashier_user,
            employee_id="840022",
            phone_country_code="+254",
            phone_number="700000953",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )

    def _sale(self, *, shop, number, item_name, total=250):
        receipt = ShopReceipt.objects.create(
            shop=shop,
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number=number,
            client_name="Walk-in",
            subtotal=total,
            tax_percent=0,
            tax_amount=0,
            total=total,
            amount_paid=total,
            payment_method="cash",
            created_by=self.cashier,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item_name=item_name,
            quantity=1,
            unit_price=total,
            line_total=total,
        )
        return receipt

    def _employees_page(self):
        from django.test import RequestFactory
        from django.utils import timezone

        from employees.analytics_services import build_analytics_page

        request = RequestFactory().get(
            "/it-support/analytics/employees/",
            {"range": "day", "date": timezone.localdate().isoformat()},
        )
        request.user = self.user
        return build_analytics_page(
            profile=self.it, request=request, section_slug="employees"
        )["page"]

    def test_staff_cell_links_to_employee_page(self):
        self._sale(shop=self.shop_a, number="SL-EMP1", item_name="Charger")
        page = self._employees_page()
        table = next(
            t for t in page["tables"] if t["title"] == "Cashier sales by shop"
        )
        row = table["rows"][0]
        cells = row["cells"] if isinstance(row, dict) else row
        staff_cell = cells[0]
        self.assertEqual(staff_cell["label"], "CASH IER · 840022")
        self.assertIn(
            f"/it-support/analytics/employees/{self.cashier.pk}/",
            staff_cell["href"],
        )

    def test_employee_page_lists_items_sold(self):
        from django.utils import timezone

        self._sale(shop=self.shop_a, number="SL-EMP2", item_name="Charger")
        self.client.force_login(self.user)
        response = self.client.get(
            f"/it-support/analytics/employees/{self.cashier.pk}/",
            {"range": "day", "date": timezone.localdate().isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["employee_subject"]["label"],
            "CASH IER · 840022",
        )
        items = response.context["stats"]["items_sold"]
        self.assertEqual([row["name"] for row in items], ["Charger"])
        html = response.content.decode()
        self.assertIn("Items sold", html)
        self.assertIn("Charger", html)

    def test_employee_page_scopes_items_to_selected_shop(self):
        from django.utils import timezone

        self._sale(shop=self.shop_a, number="SL-EMP3", item_name="Charger")
        self._sale(shop=self.shop_b, number="SL-EMP4", item_name="Cable")
        self.client.force_login(self.user)
        response = self.client.get(
            f"/it-support/analytics/employees/{self.cashier.pk}/",
            {
                "range": "day",
                "date": timezone.localdate().isoformat(),
                "shop_id": str(self.shop_b.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        items = response.context["stats"]["items_sold"]
        self.assertEqual([row["name"] for row in items], ["Cable"])

    def test_employee_page_404s_for_unknown_employee(self):
        self.client.force_login(self.user)
        response = self.client.get("/it-support/analytics/employees/999999/")
        self.assertEqual(response.status_code, 404)

    def test_employee_page_redirects_wrong_role_segment(self):
        self.client.force_login(self.user)
        response = self.client.get(
            f"/shop-manager/analytics/employees/{self.cashier.pk}/"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/it-support/analytics/employees/{self.cashier.pk}/",
            response["Location"],
        )
