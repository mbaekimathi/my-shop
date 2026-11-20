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
