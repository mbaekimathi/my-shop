"""Shared business logic with caching for hot database paths."""

import re

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .countries import COUNTRY_DIAL_CODES
from .models import SHOP_ALLOCATION_ROLES, SHOP_ASSIGNABLE_ROLES, EmployeeProfile, EmployeeRole, EmployeeStatus, join_extra_roles

EMPLOYEE_ID_RE = re.compile(r"^\d{6}$")
PHONE_RE = re.compile(r"^[\d\s\-()]{7,20}$")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_PROFILE_BYTES = 5 * 1024 * 1024  # 5 MB

EMPTY_EMPLOYEE_FORM = {
    "first_name": "",
    "last_name": "",
    "email": "",
    "phone_country_code": "+254",
    "phone_country_iso": "KE",
    "phone_number": "",
    "employee_id": "",
    "extra_roles": [],
}

EMPLOYEE_ID_CACHE_PREFIX = "emp_id_taken:"
EMPLOYEE_ID_CACHE_TTL = 120  # seconds — short TTL; invalidated on registration


def verify_active_employee_code(code: str):
    """
    Return a profile when code matches any active row in the employees table.

    Role does not matter — shop manager, cashier, IT support, etc. all qualify
    as long as status is active.
    """
    code = (code or "").strip()
    if not EMPLOYEE_ID_RE.match(code):
        return None
    return (
        EmployeeProfile.objects.filter(
            employee_id=code,
            status=EmployeeStatus.ACTIVE,
            user__is_active=True,
        )
        .select_related("user")
        .first()
    )


def invalid_staff_code_message(code: str, *, shop=None) -> str:
    """Explain why a cart/staff 6-digit code was rejected."""
    code = (code or "").strip()
    if not code:
        return "Enter a valid active staff 6-digit ID."
    if not EMPLOYEE_ID_RE.match(code):
        return "Staff ID must be exactly 6 digits."

    shop_code = str(getattr(shop, "login_code", "") or "").strip()
    if shop_code and shop_code == code:
        return (
            "That's the shop branch code, not a staff ID. "
            "Enter the employee's personal 6-digit ID."
        )

    profile = (
        EmployeeProfile.objects.filter(employee_id=code)
        .select_related("user")
        .first()
    )
    if profile is None:
        return "Not a valid active staff ID."
    if profile.status == EmployeeStatus.PENDING_APPROVAL:
        return "That staff ID is still pending approval."
    if profile.status == EmployeeStatus.SUSPENDED:
        return "That staff ID is suspended."
    if not profile.user.is_active:
        return "That staff account is disabled."
    return "Not a valid active staff ID."


def _employee_id_cache_key(code: str) -> str:
    return f"{EMPLOYEE_ID_CACHE_PREFIX}{code}"


def employee_id_is_taken(code: str, *, exclude_employee_id: str | None = None) -> bool:
    """Check whether a 6-digit employee ID is already registered (cached)."""
    if exclude_employee_id and code == exclude_employee_id:
        return False

    key = _employee_id_cache_key(code)
    cached = cache.get(key)
    if cached is not None:
        if exclude_employee_id:
            taken = User.objects.filter(
                Q(username=code) | Q(employee_profile__employee_id=code)
            ).exclude(employee_profile__employee_id=exclude_employee_id).exists()
            return taken
        return cached

    qs = User.objects.filter(
        Q(username=code) | Q(employee_profile__employee_id=code)
    )
    if exclude_employee_id:
        qs = qs.exclude(employee_profile__employee_id=exclude_employee_id)
    taken = qs.exists()
    cache.set(key, taken, EMPLOYEE_ID_CACHE_TTL)
    return taken


def mark_employee_id_taken(code: str) -> None:
    """Record that an employee ID is now in use (after successful registration)."""
    cache.set(_employee_id_cache_key(code), True, EMPLOYEE_ID_CACHE_TTL)


def invalidate_employee_id_cache(code: str) -> None:
    cache.delete(_employee_id_cache_key(code))


def invalidate_profile_cache(user_id: int) -> None:
    cache.delete(f"profile_meta:{user_id}")


def employee_form_data_from_post(post) -> dict:
    """Extract employee registration fields from a POST body."""
    return {
        "first_name": post.get("first_name", "").strip().upper(),
        "last_name": post.get("last_name", "").strip().upper(),
        "email": post.get("email", "").strip().lower(),
        "phone_country_code": post.get("phone_country_code", "+254").strip(),
        "phone_country_iso": post.get("phone_country_iso", "KE").strip().upper(),
        "phone_number": post.get("phone_number", "").strip().upper(),
        "employee_id": post.get("employee_id", "").strip(),
    }


def validate_employee_registration(form_data, password, password_confirm, profile_photo):
    """Return a list of validation errors for employee registration."""
    errors = []
    valid_dials = {country["dial"] for country in COUNTRY_DIAL_CODES}
    valid_isos = {country["iso"] for country in COUNTRY_DIAL_CODES}

    if not form_data["first_name"]:
        errors.append("First name is required.")
    if not form_data["last_name"]:
        errors.append("Last name is required.")
    if not form_data["email"]:
        errors.append("Email is required.")
    elif "@" not in form_data["email"] or "." not in form_data["email"].split("@")[-1]:
        errors.append("Enter a valid email address.")
    if form_data["phone_country_code"] not in valid_dials:
        errors.append("Select a valid country code.")
    if form_data["phone_country_iso"] not in valid_isos:
        form_data["phone_country_iso"] = "KE"
    if not form_data["phone_number"]:
        errors.append("Phone number is required.")
    elif not PHONE_RE.match(form_data["phone_number"]):
        errors.append("Enter a valid phone number.")
    if not EMPLOYEE_ID_RE.match(form_data["employee_id"]):
        errors.append("Employee ID must be exactly 6 digits.")
    elif employee_id_is_taken(form_data["employee_id"]):
        errors.append("That employee ID is already registered.")
    if not password:
        errors.append("Password is required.")
    elif len(password) < 6:
        errors.append(
            "Password must be at least 6 characters. "
            "Letters, numbers, and symbols are allowed."
        )
    if password != password_confirm:
        errors.append("Password and confirm password do not match.")
    if profile_photo:
        if profile_photo.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Profile photo must be JPG, PNG, WEBP, or GIF.")
        elif profile_photo.size > MAX_PROFILE_BYTES:
            errors.append("Profile photo must be 5 MB or smaller.")
    return errors


def register_employee(form_data, password, profile_photo=None):
    """Create a pending employee account. Returns the new employee ID."""
    with transaction.atomic():
        user = User.objects.create_user(
            username=form_data["employee_id"],
            email=form_data["email"],
            password=password,
            first_name=form_data["first_name"],
            last_name=form_data["last_name"],
            is_active=True,
        )
        EmployeeProfile.objects.create(
            user=user,
            employee_id=form_data["employee_id"],
            phone_country_code=form_data["phone_country_code"],
            phone_number=form_data["phone_number"],
            profile_photo=profile_photo,
            status=EmployeeStatus.PENDING_APPROVAL,
            role=EmployeeRole.EMPLOYEE,
        )
    mark_employee_id_taken(form_data["employee_id"])
    return form_data["employee_id"]


def employee_edit_form_data_from_post(post) -> dict:
    """Extract employee edit fields from a POST body."""
    data = employee_form_data_from_post(post)
    data["role"] = post.get("role", "").strip()
    data["extra_roles"] = post.getlist("extra_roles")
    return data


def employee_edit_form_data_from_profile(profile: EmployeeProfile) -> dict:
    """Build edit form defaults from an employee profile."""
    dial_to_iso = {country["dial"]: country["iso"] for country in COUNTRY_DIAL_CODES}
    return {
        "first_name": profile.user.first_name,
        "last_name": profile.user.last_name,
        "email": profile.user.email,
        "phone_country_code": profile.phone_country_code,
        "phone_country_iso": dial_to_iso.get(profile.phone_country_code, "KE"),
        "phone_number": profile.phone_number,
        "employee_id": profile.employee_id,
        "role": profile.role,
        "extra_roles": profile.extra_role_list(),
    }


def validate_employee_update(
    form_data,
    password,
    password_confirm,
    profile_photo,
    *,
    current_employee_id,
):
    """Return validation errors for updating an existing employee."""
    errors = []
    valid_dials = {country["dial"] for country in COUNTRY_DIAL_CODES}
    valid_isos = {country["iso"] for country in COUNTRY_DIAL_CODES}
    valid_roles = {choice[0] for choice in EmployeeRole.choices}

    if not form_data["first_name"]:
        errors.append("First name is required.")
    if not form_data["last_name"]:
        errors.append("Last name is required.")
    if not form_data["email"]:
        errors.append("Email is required.")
    elif "@" not in form_data["email"] or "." not in form_data["email"].split("@")[-1]:
        errors.append("Enter a valid email address.")
    if form_data["phone_country_code"] not in valid_dials:
        errors.append("Select a valid country code.")
    if form_data["phone_country_iso"] not in valid_isos:
        form_data["phone_country_iso"] = "KE"
    if not form_data["phone_number"]:
        errors.append("Phone number is required.")
    elif not PHONE_RE.match(form_data["phone_number"]):
        errors.append("Enter a valid phone number.")
    if not EMPLOYEE_ID_RE.match(form_data["employee_id"]):
        errors.append("Employee ID must be exactly 6 digits.")
    elif employee_id_is_taken(
        form_data["employee_id"],
        exclude_employee_id=current_employee_id,
    ):
        errors.append("That employee ID is already registered.")
    if form_data["role"] not in valid_roles:
        errors.append("Select a valid role.")
    extras = [
        role
        for role in dict.fromkeys(form_data.get("extra_roles") or [])
        if role != form_data["role"]
    ]
    if any(role not in valid_roles for role in extras):
        errors.append("Select valid additional roles.")
    form_data["extra_roles"] = [
        role for role in extras if role in valid_roles
    ]
    if password or password_confirm:
        if len(password) < 6:
            errors.append(
                "Password must be at least 6 characters. "
                "Letters, numbers, and symbols are allowed."
            )
        if password != password_confirm:
            errors.append("Password and confirm password do not match.")
    if profile_photo:
        if profile_photo.content_type not in ALLOWED_IMAGE_TYPES:
            errors.append("Profile photo must be JPG, PNG, WEBP, or GIF.")
        elif profile_photo.size > MAX_PROFILE_BYTES:
            errors.append("Profile photo must be 5 MB or smaller.")
    return errors


def update_employee(
    profile: EmployeeProfile,
    form_data,
    *,
    password="",
    profile_photo=None,
    remove_photo=False,
):
    """Update an existing employee profile and linked user account."""
    user = profile.user
    previous_employee_id = profile.employee_id
    new_employee_id = form_data["employee_id"]

    with transaction.atomic():
        user.first_name = form_data["first_name"]
        user.last_name = form_data["last_name"]
        user.email = form_data["email"]
        if password:
            user.set_password(password)
        if new_employee_id != previous_employee_id:
            user.username = new_employee_id
            profile.employee_id = new_employee_id

        user_update_fields = ["first_name", "last_name", "email"]
        if password:
            user_update_fields.append("password")
        if new_employee_id != previous_employee_id:
            user_update_fields.append("username")
        user.save(update_fields=user_update_fields)

        profile.phone_country_code = form_data["phone_country_code"]
        profile.phone_number = form_data["phone_number"]
        profile.role = form_data["role"]
        profile.extra_roles = join_extra_roles(
            form_data.get("extra_roles"), exclude=form_data["role"]
        )

        if remove_photo and profile.profile_photo:
            profile.profile_photo.delete(save=False)
            profile.profile_photo = None
        elif profile_photo:
            if profile.profile_photo:
                profile.profile_photo.delete(save=False)
            profile.profile_photo = profile_photo

        profile.save(
            update_fields=[
                "employee_id",
                "phone_country_code",
                "phone_number",
                "role",
                "extra_roles",
                "profile_photo",
                "updated_at",
            ]
        )
        granted = {form_data["role"], *profile.extra_role_list()}
        if not granted & SHOP_ALLOCATION_ROLES:
            profile.assigned_shops.clear()

    if new_employee_id != previous_employee_id:
        invalidate_employee_id_cache(previous_employee_id)
        mark_employee_id_taken(new_employee_id)
    invalidate_profile_cache(user.pk)
    return profile


def toggle_employee_suspended(profile: EmployeeProfile) -> EmployeeProfile:
    """Toggle an employee between active and suspended."""
    if profile.status == EmployeeStatus.ACTIVE:
        profile.status = EmployeeStatus.SUSPENDED
    elif profile.status == EmployeeStatus.SUSPENDED:
        profile.status = EmployeeStatus.ACTIVE
    else:
        raise ValueError("Only active or suspended employees can be toggled.")
    profile.save(update_fields=["status", "updated_at"])
    invalidate_profile_cache(profile.user_id)
    return profile


def delete_employee(profile: EmployeeProfile) -> None:
    """Permanently delete an employee account."""
    employee_id = profile.employee_id
    user_id = profile.user_id
    user = profile.user
    user.delete()
    invalidate_employee_id_cache(employee_id)
    invalidate_profile_cache(user_id)


def _money_ksh(value) -> str:
    amount = Decimal(value or 0).quantize(Decimal("0.01"))
    return f"KSh {amount:,.2f}"


EMPLOYEE_HOME_RECEIPTS_PAGE_SIZE = 100


def build_employee_performance(
    profile: EmployeeProfile,
    *,
    receipt_bounds=None,
    period_label="",
    receipt_status=None,
    page=1,
    per_page=EMPLOYEE_HOME_RECEIPTS_PAGE_SIZE,
) -> dict:
    """Sales/receipt activity for one employee — powers the employee home page.

    *receipt_bounds* is an optional ``(start_date, end_date)`` inclusive local-
    date pair limiting every section; without it the stat cards show their own
    fixed windows. *receipt_status* optionally limits to one receipt status
    (e.g. pending/confirmed). *page* selects the receipts-list page (Paginator).
    """
    from datetime import datetime, time

    from pos.models import Sale as PosSale
    from shops.models import (
        COUNTED_RECEIPT_STATUSES,
        ShopReceipt,
        ShopReceiptKind,
        ShopReceiptStatus,
    )

    now = timezone.now()
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(today, time.min), tz)

    receipts = ShopReceipt.objects.filter(created_by=profile)
    sale_statuses = (
        (receipt_status,) if receipt_status else COUNTED_RECEIPT_STATUSES
    )
    sales = receipts.filter(
        kind=ShopReceiptKind.SALE, status__in=sale_statuses
    )
    receipts_list = (
        receipts.select_related("shop", "created_by__user")
        .only(
            "id",
            "receipt_number",
            "shop_id",
            "shop__name",
            "kind",
            "status",
            "total",
            "created_at",
            "created_by",
            "created_by__user__first_name",
            "created_by__user__last_name",
            "created_by__user__username",
        )
    )
    if receipt_status:
        receipts_list = receipts_list.filter(status=receipt_status)
    scoped_sales = sales
    if receipt_bounds is not None:
        start_dt = timezone.make_aware(
            datetime.combine(receipt_bounds[0], time.min), tz
        )
        end_dt = timezone.make_aware(
            datetime.combine(receipt_bounds[1], time.max), tz
        )
        scoped_sales = sales.filter(
            created_at__gte=start_dt, created_at__lte=end_dt
        )
        receipts_list = receipts_list.filter(
            created_at__gte=start_dt, created_at__lte=end_dt
        )

    def _totals(qs):
        row = qs.aggregate(
            docs=Count("id"),
            amount=Coalesce(Sum("total"), Decimal("0.00")),
        )
        return {
            "docs": int(row["docs"] or 0),
            "amount": row["amount"],
            "amount_label": _money_ksh(row["amount"]),
        }

    if receipt_bounds is not None:
        selected = {
            "key": "selected",
            "label": period_label or "Selected period",
            "icon": "calendar-check",
            **_totals(scoped_sales),
        }
        periods = [selected]
        by_key = {"all": selected}
    else:
        # One query: conditional aggregates for every fixed window. Datetime
        # bounds (not __date lookups) — MySQL here has no tz tables.
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)
        agg = sales.aggregate(
            today_docs=Count("id", filter=Q(created_at__gte=day_start)),
            today_amount=Coalesce(
                Sum("total", filter=Q(created_at__gte=day_start)),
                Decimal("0.00"),
            ),
            week_docs=Count("id", filter=Q(created_at__gte=week_start)),
            week_amount=Coalesce(
                Sum("total", filter=Q(created_at__gte=week_start)),
                Decimal("0.00"),
            ),
            month_docs=Count("id", filter=Q(created_at__gte=month_start)),
            month_amount=Coalesce(
                Sum("total", filter=Q(created_at__gte=month_start)),
                Decimal("0.00"),
            ),
            all_docs=Count("id"),
            all_amount=Coalesce(Sum("total"), Decimal("0.00")),
        )
        periods = [
            {
                "key": key,
                "label": label,
                "icon": icon,
                "docs": int(agg[f"{key}_docs"] or 0),
                "amount": agg[f"{key}_amount"],
                "amount_label": _money_ksh(agg[f"{key}_amount"]),
            }
            for key, label, icon in (
                ("today", "Today", "calendar-day"),
                ("week", "Last 7 days", "calendar-days"),
                ("month", "Last 30 days", "calendar-range"),
                ("all", "All time", "infinity"),
            )
        ]
        by_key = {row["key"]: row for row in periods}

    pill_by_status = {
        ShopReceiptStatus.ACTIVE: "serial",
        ShopReceiptStatus.CONFIRMED: "active",
        ShopReceiptStatus.PARTIAL_RETURN: "active",
        ShopReceiptStatus.CANCELLED: "suspended",
    }
    status_tone = {
        ShopReceiptStatus.ACTIVE: "pending",
        ShopReceiptStatus.CONFIRMED: "confirmed",
        ShopReceiptStatus.CANCELLED: "cancelled",
    }.get(receipt_status, "")
    receipts_page = Paginator(
        receipts_list.order_by("-created_at", "-id"), per_page
    ).get_page(page)
    from shops.services import employee_display_name

    all_receipts = [
        {
            "id": receipt.pk,
            "number": receipt.receipt_number,
            "shop": receipt.shop.name if receipt.shop_id else "—",
            "kind": receipt.get_kind_display(),
            "status": receipt.get_status_display(),
            "status_pill": pill_by_status.get(receipt.status, "serial"),
            "total": _money_ksh(receipt.total),
            "when": receipt.created_at,
            "printed_by": employee_display_name(receipt.created_by),
        }
        for receipt in receipts_page.object_list
    ]

    pos_qs = PosSale.objects.filter(employee=profile)
    if receipt_bounds is not None:
        pos_qs = pos_qs.filter(sold_at__gte=start_dt, sold_at__lte=end_dt)
    pos_row = pos_qs.aggregate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), Decimal("0.00")),
    )
    pos_sales = (
        {
            "docs": int(pos_row["docs"] or 0),
            "amount": pos_row["amount"],
            "amount_label": _money_ksh(pos_row["amount"]),
        }
        if pos_row["docs"]
        else None
    )

    return {
        "periods": periods,
        "status_tone": status_tone,
        "all": by_key["all"],
        "all_caption": (
            f"sales · {period_label}" if period_label else "all-time sales"
        ),
        "recent": all_receipts,
        "receipts_page": receipts_page,
        "pos_sales": pos_sales,
        "has_activity": bool(receipts.exists() or pos_sales),
    }


def _qty_label(value) -> str:
    qty = Decimal(value or 0).normalize()
    return f"{qty:,f}"


def _trend_bucket_windows(start, end, bucket_kind):
    """Split [start, end) into hour/day/month windows: (label, win_start, win_end)."""
    if bucket_kind == "hour":
        cursor = start.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)

        def label_for(dt):
            return dt.strftime("%H:00")
    elif bucket_kind == "day":
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)

        def label_for(dt):
            return dt.strftime("%d %b").lstrip("0")
    else:
        cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        step = None

        def label_for(dt):
            return dt.strftime("%b %Y")
    buckets = []
    while cursor < end:
        if step is None:
            year, month = cursor.year, cursor.month + 1
            if month > 12:
                year, month = year + 1, 1
            nxt = cursor.replace(year=year, month=month)
        else:
            nxt = cursor + step
        buckets.append((label_for(cursor), cursor, nxt))
        cursor = nxt
    return buckets


def build_employee_analytics(
    profile: EmployeeProfile, date_filter: dict | None = None
) -> dict:
    """Deeper performance breakdown for the employee "My analytics" page.

    ``date_filter`` is the shared day/period/month/year context from
    ``analytics_services._date_filter_context`` — stats scope to that window
    and the trend chart buckets as hours (day / short period), days (month /
    mid period) or months (year / long period). Without a filter the page
    falls back to all-time stats and a trailing-12-month trend.
    """
    from datetime import date, datetime, time

    from django.db.models import DecimalField, ExpressionWrapper, F

    from shops.models import (
        COUNTED_RECEIPT_STATUSES,
        ShopReceipt,
        ShopReceiptKind,
        ShopReceiptLine,
        ShopReceiptStatus,
    )

    range_start = (date_filter or {}).get("start")
    range_end = (date_filter or {}).get("end")
    range_type = (date_filter or {}).get("range_type") or "day"
    period_label = (date_filter or {}).get("report_period_label") or "All time"

    receipts = ShopReceipt.objects.filter(created_by=profile)
    if range_start and range_end:
        receipts = receipts.filter(
            created_at__gte=range_start, created_at__lt=range_end
        )
    sales = receipts.filter(
        kind=ShopReceiptKind.SALE, status__in=COUNTED_RECEIPT_STATUSES
    )

    status_rows = {
        row["status"]: row
        for row in receipts.values("status").annotate(
            docs=Count("id"),
            amount=Coalesce(Sum("total"), Decimal("0.00")),
        )
    }

    def _bucket(status):
        row = status_rows.get(status) or {"docs": 0, "amount": Decimal("0.00")}
        return {
            "docs": int(row["docs"] or 0),
            "amount": row["amount"],
            "amount_label": _money_ksh(row["amount"]),
        }

    sales_row = sales.aggregate(
        docs=Count("id"),
        amount=Coalesce(Sum("total"), Decimal("0.00")),
    )

    tz = timezone.get_current_timezone()
    counted_status = Q(status__in=COUNTED_RECEIPT_STATUSES)
    cancelled_status = Q(status=ShopReceiptStatus.CANCELLED)

    if range_start and range_end:
        from .analytics_services import _period_breakdown_config

        breakdown = _period_breakdown_config(
            {
                "range_type": range_type,
                "start": range_start,
                "end": range_end,
            }
        ) or {}
        bucket_kind = breakdown.get("bucket_kind") or "month"
        windows = _trend_bucket_windows(range_start, range_end, bucket_kind)
        trend_title = {
            "hour": "Sales by hour",
            "day": "Sales by day",
            "month": "Sales by month",
        }.get(bucket_kind, "Sales by month")
        trend_subtitle = period_label
    else:
        today = timezone.localdate()
        first = today.replace(day=1)
        months = []
        for i in range(11, -1, -1):
            year, month = first.year, first.month - i
            while month <= 0:
                month += 12
                year -= 1
            start_day = date(year, month, 1)
            if month == 12:
                end_day = date(year + 1, 1, 1)
            else:
                end_day = date(year, month + 1, 1)
            months.append((start_day, end_day))
        windows = [
            (
                start_day.strftime("%b %Y"),
                timezone.make_aware(datetime.combine(start_day, time.min), tz),
                timezone.make_aware(datetime.combine(end_day, time.min), tz),
            )
            for start_day, end_day in months
        ]
        trend_title = "Sales by month"
        trend_subtitle = "Last 12 months"

    kwargs = {}
    for idx, (_label, win_start, win_end) in enumerate(windows):
        window = Q(created_at__gte=win_start, created_at__lt=win_end)
        kwargs[f"m{idx}_docs"] = Count(
            "id", filter=window & counted_status
        )
        kwargs[f"m{idx}_amount"] = Coalesce(
            Sum("total", filter=window & counted_status),
            Decimal("0.00"),
        )
        kwargs[f"c{idx}_amount"] = Coalesce(
            Sum("total", filter=window & cancelled_status),
            Decimal("0.00"),
        )
    trend_agg = ShopReceipt.objects.filter(
        created_by=profile, kind=ShopReceiptKind.SALE
    ).aggregate(**kwargs)
    trend_rows = [
        {
            "label": label,
            "docs": int(trend_agg[f"m{i}_docs"] or 0),
            "amount": trend_agg[f"m{i}_amount"],
            "amount_label": _money_ksh(trend_agg[f"m{i}_amount"]),
        }
        for i, (label, _ws, _we) in enumerate(windows)
    ]
    peak = max(trend_rows, key=lambda row: row["amount"], default=None)

    prev_start = (date_filter or {}).get("prev_start")
    prev_end = (date_filter or {}).get("prev_end")
    sales_hint = ""
    sales_hint_tone = "neutral"
    if prev_start and prev_end:
        from .analytics_services import _trend_hint

        prev_amount = (
            ShopReceipt.objects.filter(
                created_by=profile,
                kind=ShopReceiptKind.SALE,
                status__in=COUNTED_RECEIPT_STATUSES,
                created_at__gte=prev_start,
                created_at__lt=prev_end,
            ).aggregate(
                amount=Coalesce(Sum("total"), Decimal("0.00"))
            )["amount"]
        )
        sales_hint, sales_hint_tone = _trend_hint(
            sales_row["amount"], prev_amount
        )

    top_lines = ShopReceiptLine.objects.filter(
        receipt__created_by=profile,
        receipt__kind=ShopReceiptKind.SALE,
        receipt__status__in=COUNTED_RECEIPT_STATUSES,
    )
    if range_start and range_end:
        top_lines = top_lines.filter(
            receipt__created_at__gte=range_start,
            receipt__created_at__lt=range_end,
        )
    remaining = ExpressionWrapper(
        F("quantity") - F("returned_quantity"),
        output_field=DecimalField(max_digits=14, decimal_places=3),
    )
    top_items = [
        {
            "name": row["item_name"],
            "qty": row["qty"],
            "qty_label": _qty_label(row["qty"]),
            "amount": row["revenue"],
            "amount_label": _money_ksh(row["revenue"]),
        }
        for row in top_lines.values("item_name")
        .annotate(
            qty=Coalesce(Sum(remaining), Decimal("0")),
            revenue=Coalesce(
                Sum(
                    ExpressionWrapper(
                        remaining * F("unit_price"),
                        output_field=DecimalField(
                            max_digits=14, decimal_places=2
                        ),
                    )
                ),
                Decimal("0.00"),
            ),
        )
        .order_by("-revenue", "item_name")[:15]
    ]

    from .analytics_services import (
        _chart_bars,
        _column_chart,
        _trend_line_chart,
    )

    pending = _bucket(ShopReceiptStatus.ACTIVE)
    confirmed = _bucket(ShopReceiptStatus.CONFIRMED)
    partial_return = _bucket(ShopReceiptStatus.PARTIAL_RETURN)
    cancelled = _bucket(ShopReceiptStatus.CANCELLED)
    trend_line = _trend_line_chart(
        [
            (row["label"], row["amount"], trend_agg[f"c{i}_amount"])
            for i, row in enumerate(trend_rows)
        ]
    )
    time_columns = _column_chart(
        [(row["label"], row["docs"]) for row in trend_rows]
    )
    top_bars = _chart_bars(
        [(row["name"], row["amount"]) for row in top_items]
    )

    return {
        "sales": {
            "docs": int(sales_row["docs"] or 0),
            "amount": sales_row["amount"],
            "amount_label": _money_ksh(sales_row["amount"]),
        },
        "sales_hint": sales_hint,
        "sales_hint_tone": sales_hint_tone,
        "period_label": period_label,
        "pending": pending,
        "confirmed": confirmed,
        "partial_return": partial_return,
        "cancelled": cancelled,
        "receipts_total": sum(
            int(row["docs"] or 0) for row in status_rows.values()
        ),
        "trend_rows": trend_rows,
        "peak": peak,
        "trend_title": trend_title,
        "trend_subtitle": trend_subtitle,
        "top_items": top_items,
        "trend_line": trend_line,
        "time_columns": time_columns,
        "top_bars": top_bars,
    }
