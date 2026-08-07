import json
from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from employees.access import active_employee_required, get_profile_for_request
from employees.models import EmployeeRole
from employees.throttle import rate_limit

from .models import Product, SaleSource
from .services import SaleValidationError, create_sale_from_payload


def _decimal_str(value: Decimal) -> str:
    return format(value, "f")


@require_GET
def ping_api(request):
    return JsonResponse({"ok": True, "online": True})


@active_employee_required
@require_GET
def product_list_api(request):
    products = list(Product.objects.filter(is_active=True).order_by("name"))
    return JsonResponse(
        {
            "products": [
                {
                    "sku": p.sku,
                    "name": p.name,
                    "price": _decimal_str(p.price),
                    "stock": p.stock,
                }
                for p in products
            ],
            "count": len(products),
        }
    )


@active_employee_required
@rate_limit("pos_sale")
@require_http_methods(["POST"])
def sale_create_api(request):
    profile = get_profile_for_request(request)
    if profile.role not in (
        EmployeeRole.SHOP_CASHIER,
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.SUPER_ADMIN,
    ):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    try:
        sale = create_sale_from_payload(profile, payload, source=SaleSource.ONLINE)
    except SaleValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.code, "message": exc.message}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "sale": {
                "client_id": sale.client_id,
                "total": _decimal_str(sale.total),
                "source": sale.source,
            },
        },
        status=201,
    )
