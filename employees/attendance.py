from datetime import timedelta

from django.utils import timezone

from .models import EmployeeAttendance

# Compulsory clock-in rules: after this long the system checks the employee is
# still active; idle past this window they are asked to confirm, then the
# system clocks them out (distinct from a manual clock out).
CLOCK_CHECK_AFTER = timedelta(hours=6)
CLOCK_CONFIRM_GRACE = timedelta(minutes=30)
_ACTIVITY_WRITE_THROTTLE = timedelta(seconds=60)


def open_entry_for(profile):
    """The employee's still-open clock entry, if any."""
    return (
        EmployeeAttendance.objects.filter(
            employee=profile, clock_out__isnull=True
        )
        .order_by("-clock_in")
        .first()
    )


def clock_in(profile, *, when=None):
    when = when or timezone.now()
    return EmployeeAttendance.objects.create(
        employee=profile,
        work_date=timezone.localdate(when),
        clock_in=when,
        last_activity_at=when,
    )


def clock_out(entry, *, when=None, auto=False):
    entry.clock_out = when or timezone.now()
    entry.auto_clocked_out = bool(auto)
    entry.save(update_fields=["clock_out", "auto_clocked_out", "updated_at"])
    return entry


def entries_for(profile):
    """Every clock record for the employee, newest first."""
    return EmployeeAttendance.objects.filter(employee=profile)


def _minutes_label(minutes) -> str:
    minutes = max(0, int(minutes))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _clock_hhmmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    return f"{hours}:{rem // 60:02d}:{rem % 60:02d}"


def _hours_trend(per_day, *, today, days=14, width=720, height=180) -> dict:
    """SVG column payload for daily worked hours over the last `days` days."""
    rows = [
        (today - timedelta(days=days - 1 - i), per_day.get(today - timedelta(days=days - 1 - i), 0))
        for i in range(days)
    ]
    vals = [seconds for _, seconds in rows]
    hi = max(vals) or 1
    pad_top, pad_bottom, pad_x = 14, 26, 10
    plot_h = height - pad_top - pad_bottom
    plot_w = width - pad_x * 2
    n = len(rows)
    slot = plot_w / n
    bar_w = max(6.0, min(26.0, slot * 0.6))
    bars = []
    for i, (day, seconds) in enumerate(rows):
        is_empty = seconds <= 0
        h = max(2.0, (seconds / hi) * plot_h) if not is_empty else 2.0
        x = pad_x + i * slot + (slot - bar_w) / 2
        y = pad_top + plot_h - h
        bars.append(
            {
                "x": f"{x:.1f}",
                "y": f"{y:.1f}",
                "w": f"{bar_w:.1f}",
                "h": f"{h:.1f}",
                "is_today": day == today,
                "is_empty": is_empty,
                "title": (
                    f"{day:%a %d %b} — {_minutes_label(seconds // 60)}"
                    if not is_empty
                    else f"{day:%a %d %b} — no hours"
                ),
            }
        )
    labels = [
        {"x": f"{pad_x + i * slot + slot / 2:.1f}", "text": day.strftime("%a")[:2]}
        for i, (day, _) in enumerate(rows)
    ]
    worked = sum(1 for v in vals if v > 0)
    avg_seconds = sum(vals) / worked if worked else 0
    avg_y = (
        f"{pad_top + plot_h - (avg_seconds / hi) * plot_h:.1f}" if avg_seconds else ""
    )
    return {
        "width": width,
        "height": height,
        "bars": bars,
        "labels": labels,
        "label_y": height - 6,
        "avg_y": avg_y,
        "avg_label": _minutes_label(avg_seconds // 60) if avg_seconds else "",
        "track_y": pad_top + plot_h,
        "empty": not worked,
    }


def clock_summary(profile, *, open_entry=None, now=None) -> dict:
    """Hours-worked stats + 14-day trend for the clock page."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    window_start = today - timedelta(days=29)
    entries = EmployeeAttendance.objects.filter(
        employee=profile, work_date__gte=window_start
    )

    per_day = {}
    worked_dates = set()
    longest_seconds = 0
    in_minutes = []
    out_minutes = []
    for entry in entries:
        end = entry.clock_out or now
        seconds = max(0, int((end - entry.clock_in).total_seconds()))
        per_day[entry.work_date] = per_day.get(entry.work_date, 0) + seconds
        longest_seconds = max(longest_seconds, seconds)
        if seconds:
            worked_dates.add(entry.work_date)
        local_in = timezone.localtime(entry.clock_in)
        in_minutes.append(local_in.hour * 60 + local_in.minute)
        if entry.clock_out is not None:
            local_out = timezone.localtime(entry.clock_out)
            out_minutes.append(local_out.hour * 60 + local_out.minute)

    week_start = today - timedelta(days=today.weekday())
    week_seconds = sum(s for day, s in per_day.items() if day >= week_start)
    week_days = sum(1 for day in worked_dates if day >= week_start)
    today_seconds = per_day.get(today, 0)

    streak = 0
    cursor = today if today_seconds else today - timedelta(days=1)
    while per_day.get(cursor, 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)

    open_seconds = (
        max(0, int((now - open_entry.clock_in).total_seconds()))
        if open_entry is not None
        else 0
    )

    def _avg_time(minutes_list):
        if not minutes_list:
            return ""
        avg = int(round(sum(minutes_list) / len(minutes_list)))
        return f"{avg // 60:02d}:{avg % 60:02d}"

    return {
        "open_seconds": open_seconds,
        "open_label": _clock_hhmmss(open_seconds),
        "open_progress": f"{min(1.0, open_seconds / (8 * 3600)):.3f}",
        "now_label": timezone.localtime(now).strftime("%H:%M:%S"),
        "today_label": _minutes_label(today_seconds // 60),
        "week_label": _minutes_label(week_seconds // 60),
        "week_days": week_days,
        "avg_label": (
            _minutes_label((week_seconds / week_days) // 60) if week_days else ""
        ),
        "streak": streak,
        "usual_in": _avg_time(in_minutes),
        "usual_out": _avg_time(out_minutes),
        "longest_label": _minutes_label(longest_seconds // 60),
        "longest_seconds": longest_seconds,
        "days_30": len(worked_dates),
        "trend": _hours_trend(per_day, today=today),
    }


def clock_in_compulsory(*, pos=None) -> bool:
    """Whether POS settings require employees to be clocked in to sell."""
    if pos is None:
        from shops.services import get_company_pos_settings

        pos = get_company_pos_settings()
    return bool(getattr(pos, "require_clock_in", False))


def clock_in_block_message(profile, *, pos=None) -> str | None:
    """Error when this employee may not sell until they clock in, else None."""
    if profile is None or not clock_in_compulsory(pos=pos):
        return None
    if open_entry_for(profile) is not None:
        return None
    return "Clock in as an employee before selling."


def touch_activity(entry, *, now=None):
    """Stamp last-activity on an open entry (throttled writes)."""
    if entry is None or entry.clock_out is not None:
        return entry
    now = now or timezone.now()
    if (
        entry.last_activity_at is None
        or now - entry.last_activity_at >= _ACTIVITY_WRITE_THROTTLE
    ):
        entry.last_activity_at = now
        entry.save(update_fields=["last_activity_at", "updated_at"])
    return entry


def _auto_close(entry, *, now):
    return clock_out(entry, when=now, auto=True)


def status_payload(profile, *, active=False, now=None) -> dict:
    """Heartbeat payload for the clock-check widget.

    `active=True` means the browser saw real interaction since the last poll —
    it refreshes `last_activity_at`. Past CLOCK_CHECK_AFTER the entry needs a
    confirm; a prompt left unanswered past CLOCK_CONFIRM_GRACE auto-closes it.
    """
    now = now or timezone.now()
    entry = open_entry_for(profile)
    if entry is None:
        return {"ok": True, "clocked_in": False}

    if active:
        touch_activity(entry, now=now)

    elapsed_seconds = int((now - entry.clock_in).total_seconds())
    if entry.confirmed_at is not None or elapsed_seconds < CLOCK_CHECK_AFTER.total_seconds():
        return {
            "ok": True,
            "clocked_in": True,
            "elapsed_seconds": elapsed_seconds,
            "needs_check": False,
        }

    if (
        entry.check_prompted_at is not None
        and now - entry.check_prompted_at >= CLOCK_CONFIRM_GRACE
    ):
        _auto_close(entry, now=now)
        return {"ok": True, "clocked_in": False, "auto_clocked_out": True}

    return {
        "ok": True,
        "clocked_in": True,
        "elapsed_seconds": elapsed_seconds,
        "needs_check": True,
        "prompted": entry.check_prompted_at is not None,
        "prompt_deadline": (
            (entry.check_prompted_at + CLOCK_CONFIRM_GRACE).isoformat()
            if entry.check_prompted_at is not None
            else None
        ),
    }


def confirm_check(profile, *, now=None) -> dict:
    """Employee confirmed they are still clocked in for the 6h check."""
    now = now or timezone.now()
    entry = open_entry_for(profile)
    if entry is None:
        return {"ok": True, "clocked_in": False}
    touch_activity(entry, now=now)
    if entry.confirmed_at is None:
        entry.confirmed_at = now
        entry.save(update_fields=["confirmed_at", "updated_at"])
    return {"ok": True, "clocked_in": True}


def mark_check_prompted(profile, *, now=None) -> dict:
    """Stamp when the confirm prompt first appeared (starts the grace window)."""
    now = now or timezone.now()
    entry = open_entry_for(profile)
    if entry is None:
        return {"ok": True, "clocked_in": False}
    if (
        entry.confirmed_at is None
        and entry.check_prompted_at is None
        and now - entry.clock_in >= CLOCK_CHECK_AFTER
    ):
        entry.check_prompted_at = now
        entry.save(update_fields=["check_prompted_at", "updated_at"])
        deadline = now + CLOCK_CONFIRM_GRACE
    else:
        deadline = (
            entry.check_prompted_at + CLOCK_CONFIRM_GRACE
            if entry.check_prompted_at is not None
            else None
        )
    return {
        "ok": True,
        "clocked_in": True,
        "prompt_deadline": deadline.isoformat() if deadline else None,
    }


def auto_clock_out(profile, *, now=None) -> dict:
    """System clock-out for an idle entry past the check, unconfirmed."""
    now = now or timezone.now()
    entry = open_entry_for(profile)
    if entry is None:
        return {"ok": True, "clocked_in": False, "auto_clocked_out": False}
    if entry.confirmed_at is not None or now - entry.clock_in < CLOCK_CHECK_AFTER:
        return {"ok": False, "clocked_in": True}
    _auto_close(entry, now=now)
    return {"ok": True, "clocked_in": False, "auto_clocked_out": True}
