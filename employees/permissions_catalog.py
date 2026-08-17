"""Module → submodule permission catalog for HR authorizations."""

from .workspace import (
    DASHBOARD_MODULES,
    HR_SIDEBAR_SECTIONS,
    SETTINGS_NESTED_SECTIONS,
    SETTINGS_SECTIONS,
)


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
            {"slug": "serials", "label": "Serials"},
            {"slug": "movements", "label": "Stock Movement"},
            {"slug": "report", "label": "Stock Report"},
            {"slug": "settings", "label": "Stock Settings"},
            {"slug": "low-stock", "label": "Low Stock Alerts"},
            {"slug": "print", "label": "Print stock"},
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
            {"slug": "balances", "label": "Balances"},
            {"slug": "sales", "label": "Sales"},
            {"slug": "credits", "label": "Credits"},
            {"slug": "items", "label": "Items"},
            {"slug": "stock", "label": "Stock"},
            {"slug": "supply", "label": "Supply analytics"},
            {"slug": "quotations", "label": "Quotations"},
            {"slug": "clients", "label": "Clients"},
            {"slug": "employees", "label": "Employees"},
            {"slug": "suppliers", "label": "Suppliers"},
            {"slug": "expenses", "label": "Expenses"},
            {"slug": "receipts", "label": "Receipts"},
            {"slug": "account_pay", "label": "Pay accounts"},
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
    {
        "slug": "whatsapp",
        "label": "WhatsApp",
        "icon": "messages-square",
        "summary": "Choose what to send automatically and who receives it on WhatsApp.",
        "submodules": (
            {"slug": "view", "label": "View WhatsApp"},
            {"slug": "send", "label": "Send broadcasts"},
            {"slug": "inbox", "label": "Inbox replies"},
            {"slug": "analytics", "label": "Send analytics"},
            {"slug": "connect", "label": "Connect / disconnect"},
        ),
    },
    {
        "slug": "my-shop",
        "label": "MY-SHOP",
        "icon": "shopping-bag",
        "summary": "Shop floor POS, stock buys, expenses, and receipts.",
        "submodules": (
            {"slug": "workspace", "label": "Shop floor / POS"},
            {"slug": "sale", "label": "Cash sale"},
            {"slug": "credit", "label": "Credit sale"},
            {"slug": "quotation", "label": "Quotation"},
            {"slug": "buy_stock", "label": "Buy stock"},
            {"slug": "stock_requests", "label": "View stock requests"},
            {"slug": "respond_stock_request", "label": "Accept / decline requests"},
            {"slug": "register_expense", "label": "Register expense"},
            {"slug": "receipts", "label": "View receipts"},
            {"slug": "return_receipt", "label": "Return / cancel receipt"},
            {"slug": "open_close", "label": "Open / close shop"},
            {"slug": "print", "label": "Print / connect printer"},
        ),
    },
    {
        "slug": "settings",
        "label": "System settings",
        "icon": "settings",
        "summary": "Company profile, theme, POS, and payment configuration.",
        "submodules": (
            {"slug": "home", "label": "Open settings"},
            *tuple(
                {"slug": section["slug"], "label": section["label"]}
                for section in SETTINGS_SECTIONS
            ),
            *tuple(
                {"slug": section["slug"], "label": section["label"]}
                for section in SETTINGS_NESTED_SECTIONS
            ),
        ),
    },
)

PERMISSION_MODULE_BY_SLUG = {module["slug"]: module for module in PERMISSION_MODULES}

# Every dashboard tile must appear in the permission catalog; extra modules
# (MY-SHOP, settings) are allowed beyond the dashboard set.
_DASHBOARD_SLUGS = {module["slug"] for module in DASHBOARD_MODULES}
_PERMISSION_SLUGS = {module["slug"] for module in PERMISSION_MODULES}
assert _DASHBOARD_SLUGS <= _PERMISSION_SLUGS, (
    "Every DASHBOARD_MODULES slug must appear in PERMISSION_MODULES"
)


def permission_modules_for_display():
    """Permission modules with the shop-floor label taken from company profile."""
    from shops.services import get_company_display_name

    brand = get_company_display_name()
    modules = []
    for module in PERMISSION_MODULES:
        if module["slug"] == "my-shop":
            modules.append({**module, "label": brand})
        else:
            modules.append(module)
    return modules


def iter_permission_keys():
    for module in PERMISSION_MODULES:
        for submodule in module["submodules"]:
            yield module["slug"], submodule["slug"]


def is_valid_permission_key(module_slug, submodule_slug):
    module = PERMISSION_MODULE_BY_SLUG.get(module_slug)
    if module is None:
        return False
    return any(sub["slug"] == submodule_slug for sub in module["submodules"])

