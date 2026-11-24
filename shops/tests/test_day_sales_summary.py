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
    ShopReceiptLine,
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


class DayReportCountedStatusesTests(TestCase):
    """The day report's per-employee status filter (counted_statuses)."""

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="200403",
            password="pass",
            email="report-filter@test.local",
            first_name="Report",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200403",
            phone_country_code="+254",
            phone_number="700000403",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Report Shop",
            location="Nairobi",
            email="report-shop@test.local",
            phone_number="0700000403",
            login_code="200403",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Report Item",
            category="Parts",
            description="Report fixture",
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
                "login_code": "200403",
            },
        )
        self.session = get_open_shop_day(self.shop)
        complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.SALE,
                "payment_method": ShopPaymentMethod.CASH,
                "cash_amount": "100",
                "mpesa_amount": "0",
                "login_code": "200403",
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
                "login_code": "200403",
                "lines": [{"id": self.item.pk, "qty": 2, "price": "100"}],
                "confirm": True,
            },
        )

    def test_confirmed_only_drops_pending_sales(self):
        from shops.services import (
            day_session_balance_summary,
            day_session_report_details,
        )

        counted = {
            ShopReceiptStatus.CONFIRMED,
            ShopReceiptStatus.PARTIAL_RETURN,
        }
        # Till money is always confirmed-scope — the status pick no longer
        # reaches the summary; it still filters the report details.
        summary = day_session_balance_summary(self.session)
        self.assertEqual(summary["cash_sales"], Decimal("0"))
        self.assertEqual(summary["mpesa_sales"], Decimal("200"))
        self.assertEqual(summary["sales_total"], Decimal("200"))
        self.assertEqual(summary["sales_count"], 1)

        details = day_session_report_details(
            self.session, counted_statuses=counted
        )
        self.assertEqual(details["sales_count"], 1)
        self.assertEqual(len(details["items_sold"]), 1)
        self.assertEqual(details["items_sold"][0]["qty"], Decimal("2"))
        # Pending receipts still surface in the flagged list.
        self.assertEqual(len(details["flagged_receipts"]), 1)
        self.assertEqual(
            details["flagged_receipts"][0]["status"],
            ShopReceiptStatus.ACTIVE,
        )

    def test_pending_only_drops_confirmed_sales(self):
        from shops.services import (
            day_session_balance_summary,
            day_session_report_details,
        )

        counted = {ShopReceiptStatus.ACTIVE}
        # Money at hand is not the pending pick: the confirmed sale's
        # tender still counts, the pending one waits for its clear.
        summary = day_session_balance_summary(self.session)
        self.assertEqual(summary["cash_sales"], Decimal("0"))
        self.assertEqual(summary["mpesa_sales"], Decimal("200"))

        details = day_session_report_details(
            self.session, counted_statuses=counted
        )
        self.assertEqual(details["sales_count"], 1)
        self.assertEqual(details["items_sold"][0]["qty"], Decimal("1"))

    def test_sellers_breakdown_aggregates_per_employee(self):
        from shops.services import day_session_report_details

        other_user = User.objects.create_user(
            username="200404",
            password="pass",
            email="second-seller@test.local",
            first_name="Second",
            last_name="Seller",
            is_active=True,
        )
        other = EmployeeProfile.objects.create(
            user=other_user,
            employee_id="200404",
            phone_country_code="+254",
            phone_number="700000404",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.SALE,
            status=ShopReceiptStatus.CONFIRMED,
            receipt_number="SL-OTHER",
            client_name="Walk-in",
            subtotal=50,
            tax_percent=0,
            tax_amount=0,
            total=50,
            amount_paid=50,
            cash_amount=50,
            payment_method="cash",
            created_by=other,
        )
        ShopReceiptLine.objects.create(
            receipt=receipt,
            item_name="Report Item",
            quantity=1,
            unit_price=50,
            line_total=50,
        )

        details = day_session_report_details(self.session)
        sellers = details["sellers"]
        self.assertEqual(len(sellers), 2)
        # Highest amount first — the setUp cashier sold 300 across 2 receipts.
        self.assertEqual(sellers[0]["seller"], "Report Cashier")
        self.assertEqual(sellers[0]["receipts"], 2)
        self.assertEqual(sellers[0]["qty"], Decimal("3"))
        self.assertEqual(sellers[0]["amount"], Decimal("300.00"))
        self.assertEqual(sellers[1]["seller"], "Second Seller")
        self.assertEqual(sellers[1]["receipts"], 1)
        self.assertEqual(sellers[1]["qty"], Decimal("1"))
        self.assertEqual(sellers[1]["amount"], Decimal("50.00"))

    def test_sellers_breakdown_honours_counted_statuses(self):
        from shops.services import day_session_report_details

        details = day_session_report_details(
            self.session,
            counted_statuses={
                ShopReceiptStatus.CONFIRMED,
                ShopReceiptStatus.PARTIAL_RETURN,
            },
        )
        sellers = details["sellers"]
        self.assertEqual(len(sellers), 1)
        self.assertEqual(sellers[0]["seller"], "Report Cashier")
        self.assertEqual(sellers[0]["receipts"], 1)
        self.assertEqual(sellers[0]["qty"], Decimal("2"))
        self.assertEqual(sellers[0]["amount"], Decimal("200.00"))

    def test_default_keeps_all_counted_statuses(self):
        from shops.services import (
            day_session_balance_summary,
            day_session_report_details,
        )

        # Report details still count every sale receipt; till money only
        # counts the confirmed one (pending is not money at hand).
        summary = day_session_balance_summary(self.session)
        self.assertEqual(summary["sales_total"], Decimal("200"))
        self.assertEqual(summary["sales_count"], 1)
        details = day_session_report_details(self.session)
        self.assertEqual(details["sales_count"], 2)
        self.assertEqual(
            details["pending_sales"]["total"], Decimal("100")
        )
        self.assertEqual(details["pending_sales"]["count"], 1)
        self.assertEqual(details["pending_sales"]["counted"], 1)

    def test_pdf_builds_with_status_filter(self):
        from shops.services import build_shop_day_report_pdf

        pdf = build_shop_day_report_pdf(
            shop=self.shop,
            session=self.session,
            printed_by="Report Cashier",
            company_name="TEST-SHOP",
            counted_statuses={ShopReceiptStatus.CONFIRMED},
            sales_scope_label="Confirmed sales only",
        )
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_stocked_in_and_out_lists_with_est_value(self):
        from items.models import (
            StockEntrySource,
            StockMovement,
            StockMovementLine,
            StockMovementType,
            StockOutReason,
        )
        from shops.services import day_session_report_details

        in_mv = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            entry_source=StockEntrySource.STOCK_MANAGEMENT,
            shop=self.shop,
            created_by=self.profile,
        )
        StockMovementLine.objects.create(
            movement=in_mv,
            item=self.item,
            quantity=Decimal("3"),
            buying_price=Decimal("40.00"),
        )
        out_mv = StockMovement.objects.create(
            movement_type=StockMovementType.OUT,
            entry_source=StockEntrySource.STOCK_MANAGEMENT,
            shop=self.shop,
            created_by=self.profile,
        )
        StockMovementLine.objects.create(
            movement=out_mv,
            item=self.item,
            quantity=Decimal("2"),
            unit_cost=Decimal("40.00"),
            reason=StockOutReason.WASTE,
            note="Broken",
        )

        details = day_session_report_details(self.session)
        groups = details["stock_purchases"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["lines"][0]["qty"], Decimal("3"))
        self.assertEqual(groups[0]["lines"][0]["total"], Decimal("120.00"))
        self.assertEqual(groups[0]["paid"], Decimal("0"))
        self.assertEqual(groups[0]["source_label"], "Stock management")

        out = details["stock_out"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["item"], "Report Item")
        self.assertEqual(out[0]["qty"], Decimal("2"))
        self.assertEqual(out[0]["est_value"], Decimal("80.00"))
        self.assertEqual(out[0]["reason_label"], "Waste")
        self.assertEqual(out[0]["note"], "Broken")
        self.assertEqual(
            details["stock_out_est_total"], Decimal("80.00")
        )

    def test_shelf_stocked_in_and_out_lists_with_paid(self):
        from items.models import (
            ShelfItem,
            ShelfStockMovement,
            ShelfStockMovementType,
            StockOutReason,
            StockPaymentStatus,
        )
        from shops.services import (
            day_session_balance_summary,
            day_session_report_details,
        )

        shelf_item = ShelfItem.objects.create(
            shop=self.shop,
            name="Report Flour",
            unit="kg",
            created_by=self.profile,
        )
        ShelfStockMovement.objects.create(
            shelf_item=shelf_item,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("5"),
            buying_price=Decimal("60.00"),
            payment_status=StockPaymentStatus.PAID,
            amount_paid=Decimal("300.00"),
            supplier_name="GRAINS CO",
        )
        ShelfStockMovement.objects.create(
            shelf_item=shelf_item,
            movement_type=ShelfStockMovementType.OUT,
            quantity=Decimal("1"),
            reason=StockOutReason.WASTE,
            refund="yes",
            refund_amount=Decimal("30.00"),
            note="Spoiled",
        )

        details = day_session_report_details(self.session)
        groups = details["shelf_purchases"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["paid"], Decimal("300.00"))
        self.assertEqual(groups[0]["source_label"], "Shelf stock in")
        line = groups[0]["lines"][0]
        self.assertEqual(line["item"], "Report Flour")
        self.assertEqual(line["qty"], Decimal("5"))
        self.assertEqual(line["total"], Decimal("300.00"))
        self.assertEqual(line["payment_status"], "paid")
        self.assertEqual(line["supplier_name"], "GRAINS CO")

        out = details["shelf_out"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["item"], "Report Flour")
        self.assertEqual(out[0]["qty"], Decimal("1"))
        self.assertIsNone(out[0]["est_value"])
        self.assertEqual(out[0]["reason_label"], "Waste")
        self.assertEqual(out[0]["refund_amount"], Decimal("30.00"))

        # Paid shelf stock-ins count as money out of the till too.
        summary = day_session_balance_summary(self.session)
        self.assertEqual(summary["suppliers_paid"], Decimal("300.00"))

    def test_stock_from_till_flags_exclude_supplier_payments(self):
        """Day-report stock toggles double as the not-from-till switch.

        Off = the stock payment didn't leave the till, so it must not
        count in Stock bought or Expected in till.
        """
        from items.models import (
            ShelfItem,
            ShelfStockMovement,
            ShelfStockMovementType,
            StockEntrySource,
            StockMovement,
            StockMovementType,
            StockPaymentStatus,
        )
        from shops.services import day_session_balance_summary

        StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            entry_source=StockEntrySource.BUY_ITEMS,
            shop=self.shop,
            created_by=self.profile,
            payment_status=StockPaymentStatus.PAID,
            amount_paid=Decimal("200.00"),
        )
        shelf_item = ShelfItem.objects.create(
            shop=self.shop,
            name="Till Flour",
            unit="kg",
            created_by=self.profile,
        )
        ShelfStockMovement.objects.create(
            shelf_item=shelf_item,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("2"),
            amount_paid=Decimal("300.00"),
        )

        # Baseline expected cash: 1000 opening (the 100 cash sale is
        # still pending — not yet money at hand).
        base = day_session_balance_summary(
            self.session,
            item_stock_from_till=False,
            shelf_stock_from_till=False,
        )
        self.assertEqual(base["suppliers_paid"], Decimal("0"))
        self.assertEqual(base["expected_cash"], Decimal("1000"))

        summary = day_session_balance_summary(self.session)
        self.assertEqual(summary["suppliers_paid"], Decimal("500.00"))
        self.assertEqual(summary["expected_cash"], Decimal("500"))

        item_only = day_session_balance_summary(
            self.session, shelf_stock_from_till=False
        )
        self.assertEqual(item_only["suppliers_paid"], Decimal("200.00"))
        self.assertEqual(item_only["expected_cash"], Decimal("800"))

        shelf_only = day_session_balance_summary(
            self.session, item_stock_from_till=False
        )
        self.assertEqual(shelf_only["suppliers_paid"], Decimal("300.00"))
        self.assertEqual(shelf_only["expected_cash"], Decimal("700"))


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




class ClearedPendingCollectionTests(TestCase):
    """Receipts printed in one session but paid after it closed land on the
    session that was open when the money moved."""

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="200410",
            password="pass",
            email="cleared@test.local",
            first_name="Cleared",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200410",
            phone_country_code="+254",
            phone_number="700000410",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Cleared Shop",
            location="Nairobi",
            email="cleared-shop@test.local",
            phone_number="0700000410",
            login_code="200410",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Cleared Item",
            category="Parts",
            description="Cleared fixture",
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

    def _open_payload(self):
        return {
            "cash_amount": "1000",
            "mpesa_amount": "500",
            "credit_amount": "0",
            "stock_confirmed": True,
            "login_code": "200410",
        }

    def _sale_payload(self, **extra):
        payload = {
            "kind": ShopReceiptKind.SALE,
            "payment_method": ShopPaymentMethod.CASH,
            "cash_amount": "100",
            "mpesa_amount": "0",
            "login_code": "200410",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }
        payload.update(extra)
        return payload

    def _reopen(self):
        from shops.services import (
            close_shop_day,
            get_open_shop_day,
            open_shop_day,
        )

        close_shop_day(shop=self.shop, payload=self._open_payload())
        open_shop_day(shop=self.shop, payload=self._open_payload())
        return get_open_shop_day(self.shop)

    def test_pending_cleared_in_next_session_lands_on_new_till(self):
        from shops.services import (
            confirm_shop_receipt,
            day_session_balance_summary,
            day_session_report_details,
            get_open_shop_day,
            list_shop_day_sessions,
            open_shop_day,
        )

        open_shop_day(shop=self.shop, payload=self._open_payload())
        first = get_open_shop_day(self.shop)
        pending = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload=self._sale_payload(),
        )["receipt"]

        second = self._reopen()
        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=pending.pk,
            actor=self.profile,
        )
        self.assertTrue(result["ok"])
        pending.refresh_from_db()
        self.assertIsNotNone(pending.confirmed_payment_at)

        first.refresh_from_db()
        first_summary = day_session_balance_summary(first)
        self.assertEqual(first_summary["cleared_pending_total"], Decimal("0"))
        # Printed inside session one — the sale stays on its ledger, but
        # the money moved to the later till: the cleared amount is
        # deducted from this Expected so the deficit clears, and the
        # report only notes who collected it.
        self.assertEqual(first_summary["cash_sales"], Decimal("100"))
        self.assertEqual(
            first_summary["cleared_elsewhere_total"], Decimal("100")
        )
        self.assertEqual(
            first_summary["expected_cash"],
            Decimal(first.opening_cash or 0),
        )
        self.assertEqual(first_summary["cash_variance"], Decimal("0"))

        summary = day_session_balance_summary(second)
        self.assertEqual(summary["cleared_pending_total"], Decimal("100"))
        # Collected into this till but outside Expected — set aside.
        self.assertEqual(
            summary["expected_cash"], Decimal(second.opening_cash or 0)
        )

        first_details = day_session_report_details(first)
        elsewhere = first_details["cleared_elsewhere"]
        self.assertEqual(len(elsewhere), 1)
        self.assertEqual(elsewhere[0]["cleared_by"], "Cleared Cashier")
        self.assertEqual(elsewhere[0]["seller"], "Cleared Cashier")
        self.assertEqual(elsewhere[0]["total"], Decimal("100"))

        details = day_session_report_details(second)
        rows = details["cleared_pending"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["printed_at"], pending.created_at)
        self.assertEqual(rows[0]["at"], pending.confirmed_payment_at)
        self.assertEqual(rows[0]["total"], Decimal("100"))
        self.assertEqual(rows[0]["seller"], "Cleared Cashier")

        listed = list_shop_day_sessions(self.shop)
        second_row = next(r for r in listed if r["session"].pk == second.pk)
        self.assertEqual(second_row["cleared_pending_total"], Decimal("100"))

        # Money-flow memo rows name the cashier on the other side.
        from django.test import RequestFactory

        from employees.analytics_services import (
            build_day_session_report_page,
        )

        request = RequestFactory().get("/x/")
        request.user = self.user
        first_page = build_day_session_report_page(
            profile=self.profile, request=request, session=first
        )["page"]
        first_flow = [
            r["cells"][0] if isinstance(r, dict) else r[0]
            for r in first_page["tables"][1]["rows"]
        ]
        self.assertTrue(
            any("collected by Cleared Cashier" in row for row in first_flow),
            first_flow,
        )
        # The set-aside side must not leak onto the printer's page.
        self.assertFalse(
            any("Set aside — cleared pending" in row for row in first_flow),
            first_flow,
        )
        second_page = build_day_session_report_page(
            profile=self.profile, request=request, session=second
        )["page"]
        second_flow = [
            r["cells"][0] if isinstance(r, dict) else r[0]
            for r in second_page["tables"][1]["rows"]
        ]
        self.assertTrue(
            any("collected for Cleared Cashier" in row for row in second_flow),
            second_flow,
        )
        # And the collected-by note stays off the collector's page.
        self.assertFalse(
            any("collected by" in row for row in second_flow),
            second_flow,
        )

    def test_credit_collected_in_next_session_lands_on_new_till(self):
        from shops.services import (
            confirm_shop_receipt,
            day_session_balance_summary,
            day_session_report_details,
            get_open_shop_day,
            open_shop_day,
        )
        from shops.models import Client

        client = Client.objects.create(
            full_name="Credit Client",
            phone_number="0700111222",
            phone_normalized="254700111222",
            created_by=self.profile,
        )
        open_shop_day(shop=self.shop, payload=self._open_payload())
        first = get_open_shop_day(self.shop)
        credit = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.CREDIT,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number="CR-CLEARED-1",
            client=client,
            client_name=client.full_name,
            client_phone=client.phone_number,
            total=Decimal("80.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.profile,
        )

        second = self._reopen()
        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=credit.pk,
            actor=self.profile,
            amount="80",
            payment_method="cash",
        )
        self.assertTrue(result["ok"])
        credit.refresh_from_db()
        self.assertEqual(credit.kind, ShopReceiptKind.SALE)
        self.assertTrue(credit.settled_from_credit)

        first.refresh_from_db()
        first_summary = day_session_balance_summary(first)
        # The settled-credit receipt is never an ordinary sale for session one.
        self.assertEqual(first_summary["sales_total"], Decimal("0"))
        self.assertEqual(first_summary["credit_collected_total"], Decimal("0"))

        summary = day_session_balance_summary(second)
        self.assertEqual(summary["sales_total"], Decimal("0"))
        self.assertEqual(summary["credit_collected_total"], Decimal("80"))
        self.assertEqual(summary["expected_cash"], Decimal("1080"))

        details = day_session_report_details(second)
        rows = details["credit_payments"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["printed_at"], credit.created_at)
        self.assertEqual(rows[0]["amount"], Decimal("80"))
        self.assertTrue(rows[0]["cleared"])

    def test_clientless_credit_confirm_counts_in_next_session(self):
        from shops.services import (
            confirm_shop_receipt,
            day_session_balance_summary,
            day_session_report_details,
            open_shop_day,
        )

        open_shop_day(shop=self.shop, payload=self._open_payload())
        credit = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.CREDIT,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number="CR-CLIENTLESS-1",
            total=Decimal("60.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.profile,
        )

        second = self._reopen()
        result = confirm_shop_receipt(
            shop=self.shop,
            receipt_id=credit.pk,
            actor=self.profile,
            amount="60",
            payment_method="mpesa",
        )
        self.assertTrue(result["ok"])

        summary = day_session_balance_summary(second)
        self.assertEqual(summary["credit_collected_total"], Decimal("60"))
        self.assertEqual(summary["credit_collected_mpesa"], Decimal("60"))
        self.assertEqual(summary["expected_mpesa"], Decimal("560"))

        details = day_session_report_details(second)
        rows = details["credit_payments"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["channel_label"], "M-Pesa")
        self.assertEqual(rows[0]["printed_at"], credit.created_at)

    def test_trade_settlement_counts_in_paying_session(self):
        from shops.services import (
            day_session_balance_summary,
            day_session_report_details,
            get_open_shop_day,
            open_shop_day,
        )
        from shops.trade_settlement import record_trade_payment

        open_shop_day(shop=self.shop, payload=self._open_payload())
        first = get_open_shop_day(self.shop)
        trade = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.TRADE_OUT,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number="TR-CLEARED-1",
            total=Decimal("120.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.profile,
        )

        second = self._reopen()
        trade.refresh_from_db()
        record_trade_payment(
            trade,
            amount=Decimal("120"),
            payment_method="cash",
            actor=self.profile,
        )
        trade.refresh_from_db()
        self.assertTrue(trade.settled_from_trade)

        first.refresh_from_db()
        first_summary = day_session_balance_summary(first)
        self.assertEqual(first_summary["sales_total"], Decimal("0"))
        self.assertEqual(first_summary["trade_collected_total"], Decimal("0"))

        summary = day_session_balance_summary(second)
        self.assertEqual(summary["sales_total"], Decimal("0"))
        self.assertEqual(summary["trade_collected_total"], Decimal("120"))
        self.assertEqual(summary["expected_cash"], Decimal("1120"))

        details = day_session_report_details(second)
        rows = details["trade_payments"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], Decimal("120"))
        self.assertEqual(rows[0]["printed_at"], trade.created_at)

    def test_trade_in_payout_reduces_expected_till(self):
        from shops.services import (
            day_session_balance_summary,
            open_shop_day,
        )
        from shops.trade_settlement import record_trade_payment

        open_shop_day(shop=self.shop, payload=self._open_payload())
        trade = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.TRADE_IN,
            status=ShopReceiptStatus.ACTIVE,
            receipt_number="TR-IN-1",
            total=Decimal("70.00"),
            amount_paid=Decimal("0.00"),
            created_by=self.profile,
        )
        second = self._reopen()
        trade.refresh_from_db()
        record_trade_payment(
            trade,
            amount=Decimal("70"),
            payment_method="cash",
            actor=self.profile,
        )
        summary = day_session_balance_summary(second)
        self.assertEqual(summary["trade_paid_total"], Decimal("70"))
        self.assertEqual(summary["expected_cash"], Decimal("930"))
