import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from .access import get_employee_meta_for_request, get_profile_for_request
from .models import EmployeeStatus
from .sync_handlers import SyncOperationError, _sync_register_employee, process_sync_operations
from .throttle import rate_limit


def _sync_actor(request):
    """Return (employee_profile, portal_shop) for offline queue replay."""
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        meta = get_employee_meta_for_request(request) or {}
        if meta.get("status") == EmployeeStatus.ACTIVE:
            profile = get_profile_for_request(request)
            if profile is not None:
                return profile, None

    from shops.session import resolve_portal_shop

    portal_shop = resolve_portal_shop(request)
    if portal_shop is not None:
        return None, portal_shop
    return None, None


@require_GET
def ping_api(request):
    """Lightweight connectivity check for offline clients."""
    return JsonResponse({"ok": True})


@rate_limit("sync")
@require_http_methods(["POST"])
def sync_api(request):
    """Batch sync for employee or shop-portal offline queues."""
    profile, portal_shop = _sync_actor(request)
    if profile is None and portal_shop is None:
        return JsonResponse(
            {
                "ok": False,
                "error": "auth_required",
                "message": "Sign in again to sync queued changes.",
            },
            status=403,
        )

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {"ok": False, "error": "invalid_json", "message": "Invalid JSON body."},
            status=400,
        )

    operations = body.get("operations") or []
    if not isinstance(operations, list):
        return JsonResponse(
            {"ok": False, "error": "invalid_operations", "message": "operations must be a list."},
            status=400,
        )

    if len(operations) > 100:
        return JsonResponse(
            {"ok": False, "error": "batch_too_large", "message": "Max 100 operations per batch."},
            status=400,
        )

    result = process_sync_operations(
        profile, operations, portal_shop=portal_shop
    )
    status = 200 if result["failed"] == 0 else 207
    return JsonResponse(result, status=status)


@require_http_methods(["POST"])
@rate_limit("register")
def sync_register_api(request):
    """Replay queued employee registrations after offline signup."""
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {"ok": False, "error": "invalid_json", "message": "Invalid JSON body."},
            status=400,
        )

    operations = body.get("operations") or []
    if not isinstance(operations, list) or not operations:
        return JsonResponse(
            {"ok": False, "error": "no_operations", "message": "No operations to sync."},
            status=400,
        )

    results = []
    for op in operations:
        if op.get("type") != "register_employee":
            results.append(
                {
                    "id": op.get("id"),
                    "ok": False,
                    "error": "unsupported_type",
                    "message": "Only register_employee supported on this endpoint.",
                }
            )
            continue
        try:
            result = _sync_register_employee(op.get("payload") or {})
            results.append({"id": op.get("id"), "ok": True, "result": result})
        except SyncOperationError as exc:
            results.append(
                {
                    "id": op.get("id"),
                    "ok": False,
                    "error": exc.code,
                    "message": exc.message,
                }
            )

    failed = sum(1 for r in results if not r["ok"])
    return JsonResponse(
        {
            "ok": failed == 0,
            "applied": len(results) - failed,
            "failed": failed,
            "results": results,
        },
        status=200 if failed == 0 else 207,
    )
