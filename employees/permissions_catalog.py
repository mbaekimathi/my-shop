"""Module → submodule permission catalog for HR authorizations."""

from .workspace import DASHBOARD_MODULES, HR_SIDEBAR_SECTIONS


PERMISSION_MODULES = (
    {
        "slug": "stock-management",
        "label": "Stock Management",
        "icon": "package",
        "summary": "Inventory levels and stock movement.",
        "submodules": (
            {"slug": "view", "label": "Current Stock"},
            {"slug": "in", "label": "Stock In"},
            {"slug": "out", "label": "Stock Out"},
            {"slug": "request", "label": "Request Stock"},
            {"slug": "movements", "label": "Stock Movement"},
            {"slug": "report", "label": "Stock Report"},
        ),
    },
    {
        "slug": "analytics",
        "label": "Analytics",
        "icon": "bar-chart-3",
        "summary": "Decision analytics across revenue, stock, people, and costs.",
        "submodules": (
            {"slug": "view", "label": "View analytics"},
            {"slug": "revenue", "label": "Revenue"},
            {"slug": "sales", "label": "Sales"},
            {"slug": "items", "label": "Items"},
            {"slug": "stock", "label": "Stock"},
            {"slug": "quotations", "label": "Quotations"},
            {"slug": "credits", "label": "Credits"},
            {"slug": "clients", "label": "Clients"},
            {"slug": "employees", "label": "Employees"},
            {"slug": "suppliers", "label": "Suppliers"},
            {"slug": "expenses", "label": "Expenses"},
            {"slug": "receipts", "label": "Receipts"},
        ),
    },
    {
        "slug": "item-management",
        "label": "Item Management",
        "icon": "tags",
        "summary": "Products, SKUs, and pricing.",
        "submodules": (
            {"slug": "view", "label": "View items"},
            {"slug": "register", "label": "Register item"},
            {"slug": "edit", "label": "Edit item"},
            {"slug": "toggle_suspend", "label": "Suspend / activate"},
            {"slug": "delete", "label": "Delete item"},
        ),
    },
    {
        "slug": "hr-management",
        "label": "HR Management",
        "icon": "users",
        "summary": "Staff records, roles, and approvals.",
        "submodules": (
            {"slug": "home", "label": "Employee list"},
            {"slug": "register", "label": "Register employee"},
            {"slug": "edit", "label": "Edit employee"},
            {"slug": "toggle_suspend", "label": "Suspend / activate"},
            {"slug": "delete", "label": "Delete employee"},
            *tuple(
                {"slug": section["slug"], "label": section["label"]}
                for section in HR_SIDEBAR_SECTIONS
            ),
            {"slug": "permissions", "label": "Permissions"},
        ),
    },
    {
        "slug": "shop-management",
        "label": "Shop Management",
        "icon": "store",
        "summary": "Shops, floors, and operations.",
        "submodules": (
            {"slug": "view", "label": "View shops"},
            {"slug": "register", "label": "Register shop"},
            {"slug": "edit", "label": "Edit shop"},
            {"slug": "toggle_suspend", "label": "Suspend / unsuspend"},
            {"slug": "toggle_hide", "label": "Hide / show"},
            {"slug": "delete", "label": "Delete shop"},
        ),
    },
)

PERMISSION_MODULE_BY_SLUG = {module["slug"]: module for module in PERMISSION_MODULES}

# Keep catalog aligned with dashboard modules (fail loudly in tests/checks if drifted).
_DASHBOARD_SLUGS = {module["slug"] for module in DASHBOARD_MODULES}
_PERMISSION_SLUGS = {module["slug"] for module in PERMISSION_MODULES}
assert _DASHBOARD_SLUGS == _PERMISSION_SLUGS, (
    "PERMISSION_MODULES slugs must match DASHBOARD_MODULES"
)


def iter_permission_keys():
    for module in PERMISSION_MODULES:
        for submodule in module["submodules"]:
            yield module["slug"], submodule["slug"]


def is_valid_permission_key(module_slug, submodule_slug):
    module = PERMISSION_MODULE_BY_SLUG.get(module_slug)
    if module is None:
        return False
    return any(sub["slug"] == submodule_slug for sub in module["submodules"])
