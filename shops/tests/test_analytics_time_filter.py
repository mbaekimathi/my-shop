"""Analytics time filter: the company POS toggle adds from/to time fields.

On = `analytics_date_range_fields.html` renders `time_from`/`time_to` inputs for
the Day and Period ranges and `_date_filter_context` narrows the start/end
bounds to those clock times. Blank times keep the full 24-hour window. The
toggle itself is `CompanyPosSettings.enable_analytics_time_filter`, switched
from Settings → POS → Analytics.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from employees.analytics_services import (
    _apply_time_filter_bounds,
    _date_filter_context,
    _parse_filter_time,
    build_analytics_page,
)
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop, ShopReceipt, ShopReceiptKind, ShopReceiptStatus
from shops.services import (
    _invalidate_pos_settings_cache,
    get_company_pos_settings,
    set_company_pos_setting,
)

FIELD = "enable_analytics_time_filter"


class AnalyticsTimeFilterToggleTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()

    def tearDown(self):
        set_company_pos_setting(field=FIELD, enabled=False)
        _invalidate_pos_settings_cache()

    def test_toggle_round_trips_through_company_pos_setting(self):
        self.assertFalse(get_company_pos_settings().enable_analytics_time_filter)
        row = set_company_pos_setting(field=FIELD, enabled=True)
        self.assertTrue(row.enable_analytics_time_filter)
        self.assertTrue(get_company_pos_settings().enable_analytics_time_filter)
        row = set_company_pos_setting(field=FIELD, enabled=False)
        self.assertFalse(row.enable_analytics_time_filter)


class AnalyticsTimeFilterBoundsTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.tz = timezone.get_current_timezone()

    def tearDown(self):
        set_company_pos_setting(field=FIELD, enabled=False)
        _invalidate_pos_settings_cache()

    def _aware(self, day, clock):
        return timezone.make_aware(datetime.combine(day, clock), self.tz)

    def _filters(self, params):
        request = RequestFactory().get("/it-support/analytics/overview/", params)
        request.user = None
        return _date_filter_context(request)

    def test_flag_off_ignores_time_params(self):
        ctx = self._filters(
            {
                "range": "day",
                "date": "2026-11-10",
                "time_from": "08:00",
                "time_to": "17:00",
            }
        )
        self.assertFalse(ctx["time_filter_enabled"])
        self.assertEqual(ctx["start"], self._aware(datetime(2026, 11, 10).date(), time.min))
        self.assertEqual(
            ctx["end"],
            self._aware(datetime(2026, 11, 10).date(), time.min) + timedelta(days=1),
        )
        self.assertEqual(ctx["report_time_from"], "")
        self.assertEqual(ctx["report_time_to"], "")

    def test_blank_times_cover_the_full_day(self):
        set_company_pos_setting(field=FIELD, enabled=True)
        ctx = self._filters({"range": "day", "date": "2026-11-10"})
        self.assertTrue(ctx["time_filter_enabled"])
        self.assertEqual(ctx["start"], self._aware(datetime(2026, 11, 10).date(), time.min))
        self.assertEqual(
            ctx["end"],
            self._aware(datetime(2026, 11, 10).date(), time.min) + timedelta(days=1),
        )

    def test_day_range_narrows_to_picked_times(self):
        set_company_pos_setting(field=FIELD, enabled=True)
        day = datetime(2026, 11, 10).date()
        ctx = self._filters(
            {
                "range": "day",
                "date": day.isoformat(),
                "time_from": "08:30",
                "time_to": "17:00",
            }
        )
        self.assertEqual(ctx["start"], self._aware(day, time(8, 30)))
        # The "to" minute is inclusive: to 17:00 covers until 17:00:59.
        self.assertEqual(ctx["end"], self._aware(day, time(17, 1)))
        self.assertEqual(ctx["report_time_from"], "08:30")
        self.assertEqual(ctx["report_time_to"], "17:00")
        # The previous window is the same clock times on the previous day,
        # so trend comparisons line up hour-for-hour.
        yesterday = day - timedelta(days=1)
        self.assertEqual(ctx["prev_start"], self._aware(yesterday, time(8, 30)))
        self.assertEqual(ctx["prev_end"], self._aware(yesterday, time(17, 1)))

    def test_period_range_applies_times_on_boundary_dates(self):
        set_company_pos_setting(field=FIELD, enabled=True)
        ctx = self._filters(
            {
                "range": "period",
                "date_from": "2026-11-01",
                "date_to": "2026-11-10",
                "time_from": "09:00",
                "time_to": "18:00",
            }
        )
        self.assertEqual(
            ctx["start"],
            self._aware(datetime(2026, 11, 1).date(), time(9, 0)),
        )
        self.assertEqual(
            ctx["end"],
            self._aware(datetime(2026, 11, 10).date(), time(18, 1)),
        )

    def test_month_range_ignores_time_params(self):
        set_company_pos_setting(field=FIELD, enabled=True)
        ctx = self._filters(
            {
                "range": "month",
                "month": "2026-11",
                "time_from": "08:00",
                "time_to": "17:00",
            }
        )
        self.assertEqual(
            ctx["start"],
            self._aware(datetime(2026, 11, 1).date(), time.min),
        )
        self.assertEqual(
            ctx["end"],
            self._aware(datetime(2026, 11, 30).date(), time.min)
            + timedelta(days=1),
        )

    def test_single_time_and_invalid_values(self):
        self.assertIsNone(_parse_filter_time(""))
        self.assertIsNone(_parse_filter_time("  "))
        self.assertIsNone(_parse_filter_time("not-a-time"))
        self.assertEqual(_parse_filter_time("07:45"), time(7, 45))
        self.assertEqual(_parse_filter_time("07:45:30"), time(7, 45, 30))

        day = datetime(2026, 11, 10).date()
        start = self._aware(day, time.min)
        end = start + timedelta(days=1)
        # Only a "from" time: end stays at end of day.
        new_start, new_end = _apply_time_filter_bounds(
            start, end, time_from=time(9, 15)
        )
        self.assertEqual(new_start, self._aware(day, time(9, 15)))
        self.assertEqual(new_end, end)
        # Only a "to" time: start stays at start of day.
        new_start, new_end = _apply_time_filter_bounds(
            start, end, time_to=time(12, 30)
        )
        self.assertEqual(new_start, start)
        self.assertEqual(new_end, self._aware(day, time(12, 31)))


class AnalyticsTimeFilterReceiptsTests(TestCase):
    """End-to-end: the receipts section only lists receipts inside the window."""

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="870077",
            password="time-pass",
            email="timefilter@test.local",
            is_active=True,
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870077",
            phone_country_code="+254",
            phone_number="700007707",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="TIME SHOP",
            location="NAIROBI",
            email="time-shop@test.local",
            phone_number="0700007707",
            login_code="870177",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        self.today = timezone.localdate()
        self.tz = timezone.get_current_timezone()

    def tearDown(self):
        set_company_pos_setting(field=FIELD, enabled=False)
        _invalidate_pos_settings_cache()

    def _receipt(self, number, hour, minute=0, second=0):
        receipt = ShopReceipt.objects.create(
            shop=self.shop,
            kind=ShopReceiptKind.SALE,
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
        stamp = timezone.make_aware(
            datetime.combine(self.today, time(hour, minute, second)), self.tz
        )
        ShopReceipt.objects.filter(pk=receipt.pk).update(created_at=stamp)
        return receipt

    def _page(self, params):
        request = RequestFactory().get(
            "/shop-manager/analytics/receipts/", params
        )
        request.user = self.user
        context = build_analytics_page(
            profile=self.staff, request=request, section_slug="receipts"
        )
        return context["page"]

    def _numbers(self, page):
        return {row["cells"][0]["label"] for row in page["tables"][0]["rows"]}

    def test_time_window_filters_receipts_on_the_day(self):
        self._receipt("SL-MORNING", 9)
        self._receipt("SL-NOON", 12, 30)
        self._receipt("SL-LATE", 21)
        self._receipt("SL-EDGE", 17, 0, 30)

        set_company_pos_setting(field=FIELD, enabled=True)
        page = self._page(
            {
                "range": "day",
                "date": self.today.isoformat(),
                "time_from": "08:00",
                "time_to": "17:00",
            }
        )
        # "to 17:00" is minute-inclusive, so the 17:00:30 sale still counts.
        self.assertEqual(self._numbers(page), {"SL-MORNING", "SL-NOON", "SL-EDGE"})

        page = self._page(
            {"range": "day", "date": self.today.isoformat()}
        )
        self.assertEqual(
            self._numbers(page),
            {"SL-MORNING", "SL-NOON", "SL-LATE", "SL-EDGE"},
        )

    def test_flag_off_time_params_do_not_filter(self):
        self._receipt("SL-MORNING", 9)
        self._receipt("SL-LATE", 21)
        page = self._page(
            {
                "range": "day",
                "date": self.today.isoformat(),
                "time_from": "08:00",
                "time_to": "17:00",
            }
        )
        self.assertEqual(self._numbers(page), {"SL-MORNING", "SL-LATE"})
