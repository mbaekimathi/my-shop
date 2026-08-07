"""Background tasks — extend here for maintenance and notifications."""

from celery import shared_task


@shared_task(name="employees.cleanup_expired_sessions")
def cleanup_expired_sessions() -> str:
    """Remove expired session rows from the database (run nightly via Celery Beat)."""
    from django.core.management import call_command

    call_command("clearsessions")
    return "clearsessions completed"


@shared_task(name="employees.ping")
def ping() -> str:
    """Health-check task to verify Celery workers are running."""
    return "pong"
