from django.urls import reverse

from .access import role_home_url_name, role_url_segment
from .models import EmployeeRole

ROLE_PAGE_META = {
    EmployeeRole.EMPLOYEE: {
        "title": "Employee Workspace",
        "headline": "Employee workspace",
        "summary": "View your profile and account details.",
        "icon": "badge-check",
    },
    EmployeeRole.SUPER_ADMIN: {
        "title": "Super Admin",
        "headline": "System control center",
        "summary": "Manage employee approvals, roles, and account status.",
        "icon": "shield-check",
    },
    EmployeeRole.COMPANY_MANAGER: {
        "title": "Company Manager",
        "headline": "Company workspace",
        "summary": "Your company manager workspace.",
        "icon": "building-2",
    },
    EmployeeRole.SHOP_MANAGER: {
        "title": "Shop Manager",
        "headline": "Shop workspace",
        "summary": "Your shop manager workspace.",
        "icon": "store",
    },
    EmployeeRole.SHOP_CASHIER: {
        "title": "Shop Cashier",
        "headline": "Cashier workspace",
        "summary": "Your cashier workspace.",
        "icon": "scan-barcode",
    },
    EmployeeRole.IT_SUPPORT: {
        "title": "IT Support",
        "headline": "Support workspace",
        "summary": "Your IT support workspace.",
        "icon": "monitor-cog",
    },
}


DASHBOARD_MODULES = (
    {
        "slug": "stock-management",
        "label": "Stock Management",
        "icon": "package",
        "summary": "Track stock levels and inventory movement.",
    },
    {
        "slug": "analytics",
        "label": "Analytics",
        "icon": "bar-chart-3",
        "summary": "Decision analytics for revenue, stock, people, and costs.",
    },
    {
        "slug": "item-management",
        "label": "Item Management",
        "icon": "tags",
        "summary": "Manage products, SKUs, and pricing.",
    },
    {
        "slug": "hr-management",
        "label": "HR Management",
        "icon": "users",
        "summary": "Handle staff records, roles, and approvals.",
    },
    {
        "slug": "shop-management",
        "label": "Shop Management",
        "icon": "store",
        "summary": "Configure shops, floors, and operations.",
    },
    {
        "slug": "whatsapp",
        "label": "WhatsApp",
        "icon": "messages-square",
        "summary": "WhatsApp client broadcasts from POS purchase history.",
    },
)

DASHBOARD_MODULE_BY_SLUG = {module["slug"]: module for module in DASHBOARD_MODULES}


def get_dashboard_modules(role):
    """Resolved module links for dashboard sidebar navigation."""
    segment = role_url_segment(role)
    return [
        {
            **module,
            "href": reverse(
                "employees:workspace_module",
                kwargs={"role_segment": segment, "module_slug": module["slug"]},
            ),
        }
        for module in DASHBOARD_MODULES
    ]


def get_dashboard_module(slug, role):
    module = DASHBOARD_MODULE_BY_SLUG.get(slug)
    if module is None:
        return None
    segment = role_url_segment(role)
    return {
        **module,
        "href": reverse(
            "employees:workspace_module",
            kwargs={"role_segment": segment, "module_slug": module["slug"]},
        ),
    }


WORKSPACE_DASHBOARD_VIEWS = frozenset(
    {
        "employees:dashboard",
        "employees:role_employee",
        "employees:role_super_admin",
        "employees:role_super_admin_page",
        "employees:role_company_manager",
        "employees:role_shop_manager",
        "employees:role_shop_cashier",
        "employees:role_it_support",
    }
)


def is_workspace_dashboard(request):
    view_name = getattr(getattr(request, "resolver_match", None), "view_name", None)
    return view_name in WORKSPACE_DASHBOARD_VIEWS


def workspace_back_url(request, role):
    """Parent URL in the site path hierarchy for the current page."""
    if is_workspace_dashboard(request):
        return None

    match = getattr(request, "resolver_match", None)
    if match is None:
        return reverse(role_home_url_name(role))

    view_name = match.view_name
    kwargs = match.kwargs
    segment = role_url_segment(role)

    if view_name == "employees:profile":
        return reverse(role_home_url_name(role))

    if view_name == "employees:settings":
        return reverse(role_home_url_name(role))

    if view_name == "employees:settings_section":
        section = kwargs.get("section")
        if section == "company-receipt":
            return settings_section_url("company-pos")
        return reverse("employees:settings")

    if view_name == "employees:workspace_module":
        return reverse(role_home_url_name(role))

    if view_name in {
        "employees:my_shop",
        "employees:my_shop_select",
        "employees:my_shop_workspace",
        "employees:my_shop_buy_stock",
        "employees:my_shop_stock_requests",
        "employees:my_shop_register_expense",
        "employees:my_shop_receipts",
        "employees:my_shop_reprint",
        "employees:my_shop_day_toggle",
    }:
        return reverse(role_home_url_name(role))

    if view_name == "employees:hr_section":
        return hr_management_url(role)

    if view_name == "employees:hr_employee_approvals":
        return hr_management_url(role)

    if view_name == "employees:hr_employee_approvals_page":
        from .pagination import page_url

        page = int(kwargs.get("page", 1))
        if page > 1:
            return page_url(
                "hr_employee_approvals",
                page - 1,
                url_kwargs={"role_segment": segment},
            )
        return hr_management_url(role)

    return reverse(role_home_url_name(role))


def _link(label, icon, *, url_name=None, href=None, active=False, danger=False, muted=False, badge=None):
    item = {
        "type": "link",
        "label": label,
        "icon": icon,
        "active": active,
        "danger": danger,
        "muted": muted,
    }
    if url_name:
        item["url_name"] = url_name
        item["href"] = reverse(url_name)
    elif href:
        item["href"] = href
    if badge is not None and int(badge) > 0:
        item["badge"] = int(badge)
    return item


def my_shop_url(*, switch=False):
    url = reverse("employees:my_shop")
    return f"{url}?switch=1" if switch else url


def _myshop_link(*, active=False):
    return _link("MY-SHOP", "store", href=my_shop_url(), active=active)


def _employee_login_switch_link():
    """Sign out current shop/employee session and open employee login."""
    return _link(
        "Employee Login",
        "log-in",
        url_name="employees:to_employee_login",
    )


def _footer_site_links(
    *,
    myshop_active=False,
    settings_active=False,
    employee_login=False,
    profile=None,
    tail,
):
    """System settings, MY-SHOP (or Employee Login), then page-specific footer links."""
    from .module_permissions import employee_may_any

    links = []
    if profile is None or employee_may_any(profile, "settings"):
        links.append(
            _link(
                "System settings",
                "settings",
                url_name="employees:settings",
                active=settings_active,
            )
        )
    if employee_login:
        links.append(_employee_login_switch_link())
    elif profile is None or employee_may_any(profile, "my-shop"):
        links.append(_myshop_link(active=myshop_active))
    links.extend(tail)
    return links


def resolve_sidebar_hrefs(sidebar):
    """Ensure every link item has a resolved href."""
    if not sidebar:
        return sidebar
    for section in ("primary", "footer"):
        for item in sidebar.get(section, []):
            if item.get("type") == "link" and "href" not in item and item.get("url_name"):
                item["href"] = reverse(item["url_name"])
    return sidebar


def _module_sidebar_links(role, *, active_slug=None, profile=None):
    from .module_permissions import employee_may_any

    modules = get_dashboard_modules(role)
    if profile is not None:
        modules = [module for module in modules if employee_may_any(profile, module["slug"])]
    return [
        _link(
            module["label"],
            module["icon"],
            href=module["href"],
            active=module["slug"] == active_slug if active_slug else False,
        )
        for module in modules
    ]


def sidebar_for_role_dashboard(role, profile=None):
    """Sidebar links for role home / dashboard pages."""
    dashboard_url = reverse(role_home_url_name(role))
    return resolve_sidebar_hrefs(
        {
            "page": "role_dashboard",
            "dashboard_url": dashboard_url,
            "primary": _module_sidebar_links(role, profile=profile),
            "footer": _footer_site_links(
                profile=profile,
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_my_shop(
    role,
    *,
    shop=None,
    shops=None,
    active="workspace",
    shop_open=False,
    print_channels=None,
    portal=False,
    profile=None,
    pending_request_count=0,
):
    """Sidebar for MY-SHOP entry picker and shop floor workspace."""
    from .module_permissions import employee_may

    def _allowed(submodule):
        # Shop portal sessions are not employee-matrix gated.
        if portal or profile is None:
            return True
        return employee_may(profile, "my-shop", submodule)

    shops = shops or []
    print_channels = list(print_channels or [])
    if portal and shop is not None:
        dashboard_url = reverse(
            "employees:my_shop_workspace", kwargs={"shop_id": shop.pk}
        )
        primary = []
        sign_out = _link(
            "Sign out", "log-out", url_name="employees:shop_logout", danger=True
        )
    else:
        dashboard_url = reverse(role_home_url_name(role))
        # MY-SHOP shop pages: no Dashboard link (shop links only).
        primary = []
        sign_out = _link("Sign out", "log-out", url_name="employees:logout", danger=True)
    if shop is not None:
        if _allowed("workspace"):
            primary.append(
                _link(
                    shop.name,
                    "store",
                    href=reverse("employees:my_shop_workspace", kwargs={"shop_id": shop.pk}),
                    active=active == "workspace",
                )
            )
        # Floor tools only on the main shop page — not on buy-stock / receipts / etc.
        if active == "workspace":
            if _allowed("open_close"):
                if shop_open:
                    primary.append(
                        _link(
                            "Close shop",
                            "door-closed",
                            href=reverse(
                                "employees:my_shop_day_toggle", kwargs={"shop_id": shop.pk}
                            ),
                            active=active == "day_toggle",
                        )
                    )
                else:
                    primary.append(
                        _link(
                            "Open shop",
                            "door-open",
                            href=reverse(
                                "employees:my_shop_day_toggle", kwargs={"shop_id": shop.pk}
                            ),
                            active=active == "day_toggle",
                        )
                    )
            if _allowed("buy_stock"):
                primary.append(
                    _action(
                        "Buy stock items",
                        "package-plus",
                        action="buy-stock",
                        href=reverse("employees:my_shop_buy_stock", kwargs={"shop_id": shop.pk}),
                    )
                )
            if _allowed("stock_requests"):
                primary.append(
                    _link(
                        "Stock requests",
                        "clipboard-list",
                        href=reverse(
                            "employees:my_shop_stock_requests", kwargs={"shop_id": shop.pk}
                        ),
                        active=active == "stock_requests",
                        badge=pending_request_count,
                    )
                )
            if _allowed("register_expense"):
                primary.append(
                    _action(
                        "Register expense",
                        "wallet",
                        action="register-expense",
                        href=reverse(
                            "employees:my_shop_register_expense", kwargs={"shop_id": shop.pk}
                        ),
                    )
                )
            if print_channels and _allowed("print"):
                primary.append(
                    _action("Connect to printer", "bluetooth", action="connect-printer")
                )
            if _allowed("receipts"):
                primary.append(
                    _link(
                        "Receipts",
                        "receipt",
                        href=reverse("employees:my_shop_receipts", kwargs={"shop_id": shop.pk}),
                        active=active == "receipts",
                    )
                )
        elif active == "stock_requests" and _allowed("stock_requests"):
            primary.append(
                _action(
                    "Request stock",
                    "plus",
                    action="request-stock",
                )
            )
        if not portal and len(shops) > 1:
            primary.append(
                _link(
                    "Switch shop",
                    "arrow-left-right",
                    href=my_shop_url(switch=True),
                )
            )
    elif not portal:
        primary.append(
            _link("Choose shop", "store", href=my_shop_url(), active=True)
        )

    return resolve_sidebar_hrefs(
        {
            "page": "my_shop",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                employee_login=True,
                profile=None if portal else profile,
                tail=[sign_out],
            ),
        }
    )


def _action(label, icon, *, action, href=None):
    item = {
        "type": "action",
        "label": label,
        "icon": icon,
        "action": action,
    }
    if href:
        item["href"] = href
    return item


def sidebar_for_item_management(role, profile=None):
    """Sidebar links for the Item Management module."""
    from .module_permissions import employee_may

    dashboard_url = reverse(role_home_url_name(role))
    primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
    if profile is None or employee_may(profile, "item-management", "register"):
        primary.append(_action("Register item", "plus", action="register-item"))
    return resolve_sidebar_hrefs(
        {
            "page": "item_management",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_shop_management(role, profile=None):
    """Sidebar links for the Shop Management module."""
    from .module_permissions import employee_may

    dashboard_url = reverse(role_home_url_name(role))
    primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
    if profile is None or employee_may(profile, "shop-management", "register"):
        primary.append(_action("Register shop", "plus", action="register-shop"))
    return resolve_sidebar_hrefs(
        {
            "page": "shop_management",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def stock_management_url(
    role,
    mode="view",
    *,
    shop_id="",
    shop_ids=None,
    requested_from_shop_id="",
    report_params=None,
):
    from urllib.parse import urlencode

    segment = role_url_segment(role)
    base = reverse(
        "employees:workspace_module",
        kwargs={"role_segment": segment, "module_slug": "stock-management"},
    )
    params = [("mode", mode or "view")]
    ids = []
    if shop_ids:
        ids = [str(sid).strip() for sid in shop_ids if str(sid).strip()]
    elif shop_id:
        ids = [str(shop_id).strip()]
    for sid in ids:
        params.append(("shop_id", sid))
    if mode == "request" and requested_from_shop_id:
        params.append(("requested_from_shop_id", str(requested_from_shop_id)))
    if mode in ("report", "movements") and report_params:
        for key, value in report_params.items():
            if value:
                params.append((key, value))
    return f"{base}?{urlencode(params)}"


def sidebar_for_stock_management(
    role,
    *,
    active_mode="view",
    shop_id="",
    shop_ids=None,
    requested_from_shop_id="",
    report_params=None,
    profile=None,
):
    """Sidebar for stock-management. Full workflow links for shop-manager and IT support."""
    from .module_permissions import employee_may

    dashboard_url = reverse(role_home_url_name(role))
    resolved_shop_ids = []
    if shop_ids:
        resolved_shop_ids = [str(sid).strip() for sid in shop_ids if str(sid).strip()]
    elif shop_id:
        resolved_shop_ids = [str(shop_id).strip()]
    shop_kwargs = {
        "shop_ids": resolved_shop_ids,
        "requested_from_shop_id": requested_from_shop_id or "",
    }
    default_report_params = {"range": "day", "item_mode": "all"}
    report_href = stock_management_url(
        role, "report", report_params=report_params or default_report_params
    )
    movements_href = stock_management_url(
        role, "movements", report_params=report_params or default_report_params
    )

    full_workflow_roles = {
        EmployeeRole.SHOP_MANAGER,
        EmployeeRole.IT_SUPPORT,
    }

    def _allowed(mode):
        return profile is None or employee_may(profile, "stock-management", mode)

    if role in full_workflow_roles:
        primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
        # Serials / return-clients: Serials + Return clients only.
        if active_mode in ("serials", "return-clients"):
            if _allowed("serials"):
                primary.extend(
                    [
                        _link(
                            "Serials",
                            "hash",
                            href=stock_management_url(role, "serials"),
                            active=active_mode == "serials",
                        ),
                        _link(
                            "Return clients",
                            "undo-2",
                            href=stock_management_url(role, "return-clients"),
                            active=active_mode == "return-clients",
                        ),
                    ]
                )
        else:
            view_shop_id = (
                resolved_shop_ids[0] if len(resolved_shop_ids) == 1 else ""
            )
            candidates = [
                (
                    "view",
                    _link(
                        "Current Stock",
                        "boxes",
                        href=stock_management_url(role, "view", shop_id=view_shop_id),
                        active=active_mode == "view",
                    ),
                ),
                (
                    "in",
                    _link(
                        "Stock In",
                        "package-plus",
                        href=stock_management_url(
                            role, "in", shop_ids=resolved_shop_ids
                        ),
                        active=active_mode == "in",
                    ),
                ),
                (
                    "out",
                    _link(
                        "Stock Out",
                        "package-minus",
                        href=stock_management_url(
                            role, "out", shop_ids=resolved_shop_ids
                        ),
                        active=active_mode == "out",
                    ),
                ),
                (
                    "request",
                    _link(
                        "Request Stock",
                        "clipboard-list",
                        href=stock_management_url(role, "request", **shop_kwargs),
                        active=active_mode == "request",
                    ),
                ),
                (
                    "serials",
                    _link(
                        "Serials",
                        "hash",
                        href=stock_management_url(role, "serials"),
                        active=active_mode == "serials",
                    ),
                ),
                (
                    "movements",
                    _link(
                        "Stock Movement",
                        "arrow-down-up",
                        href=movements_href,
                        active=active_mode == "movements",
                    ),
                ),
                (
                    "report",
                    _link(
                        "Stock Report",
                        "bar-chart-3",
                        href=report_href,
                        active=active_mode == "report",
                    ),
                ),
                (
                    "view",
                    _link(
                        "Stock Settings",
                        "sliders-horizontal",
                        href=stock_management_url(role, "settings"),
                        active=active_mode == "settings",
                    ),
                ),
                (
                    "view",
                    _link(
                        "Low Stock Alerts",
                        "bell-ring",
                        href=stock_management_url(role, "low-stock"),
                        active=active_mode == "low-stock",
                    ),
                ),
            ]
            primary.extend(link for mode, link in candidates if _allowed(mode))
            if active_mode == "view" and _allowed("view"):
                primary.append(
                    _action("Print stock", "printer", action="print-stock")
                )
    else:
        primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
        if _allowed("view"):
            primary.append(
                _link(
                    "Current Stock",
                    "boxes",
                    href=stock_management_url(role, "view", shop_id=shop_id),
                    active=active_mode == "view",
                )
            )
            primary.append(
                _link(
                    "Stock Settings",
                    "sliders-horizontal",
                    href=stock_management_url(role, "settings"),
                    active=active_mode == "settings",
                )
            )
            primary.append(
                _link(
                    "Low Stock Alerts",
                    "bell-ring",
                    href=stock_management_url(role, "low-stock"),
                    active=active_mode == "low-stock",
                )
            )
            primary.append(
                _action("Print stock", "printer", action="print-stock")
            )

    return resolve_sidebar_hrefs(
        {
            "page": "stock_management",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def hr_approvals_url(role):
    segment = role_url_segment(role)
    return reverse(
        "employees:hr_employee_approvals",
        kwargs={"role_segment": segment},
    )


def hr_section_url(role, section):
    segment = role_url_segment(role)
    return reverse(
        "employees:hr_section",
        kwargs={"role_segment": segment, "section": section},
    )


def hr_management_url(role):
    segment = role_url_segment(role)
    return reverse(
        "employees:workspace_module",
        kwargs={"role_segment": segment, "module_slug": "hr-management"},
    )


# Only ship finished HR sections in the sidebar. Stub routes
# (payrolls/leaves/performance/audits) remain reachable by URL but stay hidden.
HR_SIDEBAR_SECTIONS = (
    {"slug": "approvals", "label": "Approvals", "icon": "clock-3"},
    {"slug": "authorizations", "label": "Authorizations", "icon": "shield-check"},
    {"slug": "permissions", "label": "Permissions", "icon": "key-round"},
)


def _hr_section_href(role, section):
    if section == "approvals":
        return hr_approvals_url(role)
    return hr_section_url(role, section)


def sidebar_for_hr_management(role, *, active_view="home", profile=None):
    """Sidebar links for the HR Management module."""
    from .module_permissions import employee_may

    dashboard_url = reverse(role_home_url_name(role))
    hr_url = hr_management_url(role)
    primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
    if profile is None or employee_may(profile, "hr-management", "home"):
        primary.append(
            _link(
                "HR Management",
                "users",
                href=hr_url,
                active=active_view == "home",
            )
        )
    section_links = [
        _link(
            section["label"],
            section["icon"],
            href=_hr_section_href(role, section["slug"]),
            active=active_view == section["slug"],
        )
        for section in HR_SIDEBAR_SECTIONS
        if profile is None or employee_may(profile, "hr-management", section["slug"])
    ]
    if active_view == "home" and (
        profile is None or employee_may(profile, "hr-management", "register")
    ):
        primary.append(
            _action("Register employee", "user-plus", action="register-employee")
        )
    primary.extend(section_links)
    return resolve_sidebar_hrefs(
        {
            "page": "hr_management",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                profile=profile,
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_hr_permissions(role, profile=None):
    """Sidebar for the permissions matrix — jump links to each module section."""
    from .permissions_catalog import PERMISSION_MODULES

    dashboard_url = reverse(role_home_url_name(role))
    primary = [
        _link("Dashboard", "layout-dashboard", href=dashboard_url),
        *[
            _link(
                module["label"],
                module["icon"],
                href=f"#module-{module['slug']}",
            )
            for module in PERMISSION_MODULES
        ],
    ]
    return resolve_sidebar_hrefs(
        {
            "page": "hr_permissions",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": [
                _link("Sign out", "log-out", url_name="employees:logout", danger=True),
            ],
        }
    )


def analytics_url(role):
    return reverse(
        "employees:workspace_module",
        kwargs={
            "role_segment": role_url_segment(role),
            "module_slug": "analytics",
        },
    )


def analytics_section_url(role, section):
    if section in (None, "", "overview"):
        return analytics_url(role)
    return reverse(
        "employees:analytics_section",
        kwargs={
            "role_segment": role_url_segment(role),
            "section": section,
        },
    )


def sidebar_for_analytics(role, *, active_view="overview", profile=None):
    """Sidebar links for Analytics — each section is its own page."""
    from .analytics_services import ANALYTICS_SECTIONS
    from .module_permissions import employee_may

    dashboard_url = reverse(role_home_url_name(role))
    home_url = analytics_url(role)
    active_view = (active_view or "overview").strip().lower()
    section_links = [
        _link(
            section["label"],
            section["icon"],
            href=analytics_section_url(role, section["slug"]),
            active=active_view == section["slug"],
        )
        for section in ANALYTICS_SECTIONS
        if section["slug"] != "overview"
        and (profile is None or employee_may(profile, "analytics", section["slug"]))
    ]
    primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
    if profile is None or employee_may(profile, "analytics", "view"):
        primary.append(
            _link(
                "Overview",
                "layout-grid",
                href=home_url,
                active=active_view == "overview",
            )
        )
    primary.extend(section_links)
    return resolve_sidebar_hrefs(
        {
            "page": "analytics",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_communications(role, *, active_view="home", profile=None):
    """Sidebar links for the WhatsApp module."""
    from .module_permissions import employee_may

    dashboard_url = reverse(role_home_url_name(role))
    segment = role_url_segment(role)
    whatsapp_url = reverse(
        "employees:workspace_module",
        kwargs={"role_segment": segment, "module_slug": "whatsapp"},
    )
    primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
    if profile is None or employee_may(profile, "whatsapp", "view"):
        primary.append(
            _link(
                "WhatsApp",
                "messages-square",
                href=whatsapp_url,
                active=active_view == "home",
            )
        )
    if profile is None or employee_may(profile, "settings", "whatsapp"):
        primary.append(
            _link(
                "WhatsApp settings",
                "settings-2",
                href=settings_section_url("whatsapp"),
                active=active_view == "settings",
            )
        )
    return resolve_sidebar_hrefs(
        {
            "page": "whatsapp",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                profile=profile,
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_module(role, module_slug, profile=None):
    """Sidebar links when viewing a dashboard module page."""
    if module_slug == "item-management":
        return sidebar_for_item_management(role, profile=profile)
    if module_slug == "stock-management":
        return sidebar_for_stock_management(role, profile=profile)
    if module_slug == "hr-management":
        return sidebar_for_hr_management(role, profile=profile)
    if module_slug == "shop-management":
        return sidebar_for_shop_management(role, profile=profile)
    if module_slug == "analytics":
        return sidebar_for_analytics(role, profile=profile)
    if module_slug == "whatsapp":
        return sidebar_for_communications(role, profile=profile)

    dashboard_url = reverse(role_home_url_name(role))
    return resolve_sidebar_hrefs(
        {
            "page": "workspace_module",
            "dashboard_url": dashboard_url,
            "primary": [
                _link("Dashboard", "layout-dashboard", href=dashboard_url),
            ],
            "footer": _footer_site_links(
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_super_admin():
    """Sidebar links unique to the Super Admin dashboard page."""
    dashboard_url = reverse("employees:role_super_admin")
    return resolve_sidebar_hrefs(
        {
            "page": "super_admin_dashboard",
            "dashboard_url": dashboard_url,
            "primary": [
                *_module_sidebar_links(EmployeeRole.SUPER_ADMIN),
                _link(
                    "Employee access",
                    "users-round",
                    href=f"{dashboard_url}#employee-access",
                ),
            ],
            "footer": _footer_site_links(
                myshop_active=True,
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


def sidebar_for_profile(role):
    """Sidebar links unique to the My Profile page."""
    dashboard_url = reverse(role_home_url_name(role))
    return resolve_sidebar_hrefs(
        {
            "page": "profile",
            "dashboard_url": dashboard_url,
            "primary": [
                _link("Dashboard", "layout-dashboard", href=dashboard_url),
            ],
            "footer": _footer_site_links(
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )


SETTINGS_SECTIONS = (
    {
        "slug": "company-profile",
        "label": "Company profile",
        "icon": "building-2",
        "summary": "Company name, contact details, and brand identity.",
    },
    {
        "slug": "company-theme",
        "label": "Company theme",
        "icon": "palette",
        "summary": "Colors, logos, and visual appearance across MY-SHOP.",
    },
    {
        "slug": "company-pos",
        "label": "POS settings",
        "icon": "monitor-smartphone",
        "summary": "Transaction types and payment methods shown on the shop page.",
    },
    {
        "slug": "company-payments",
        "label": "Company payments settings",
        "icon": "credit-card",
        "summary": "Daraja STK Push and M-Pesa API settlement preferences.",
    },
    {
        "slug": "whatsapp",
        "label": "WhatsApp settings",
        "icon": "messages-square",
        "summary": "API credentials for WhatsApp, Message, and Text channels.",
    },
)

# Nested under POS / payments — shown in those pages' sidebars only.
SETTINGS_NESTED_SECTIONS = (
    {
        "slug": "company-receipt",
        "label": "Receipt settings",
        "icon": "receipt",
        "summary": "Print channels, paper size, and how receipts look when printed.",
        "parent": "company-pos",
    },
    {
        "slug": "company-daraja",
        "label": "Daraja settings",
        "icon": "smartphone",
        "summary": "Safaricom Daraja STK Push credentials for sandbox and production.",
        "parent": "company-payments",
    },
)

SETTINGS_SECTION_BY_SLUG = {section["slug"]: section for section in SETTINGS_SECTIONS}
SETTINGS_NESTED_BY_SLUG = {section["slug"]: section for section in SETTINGS_NESTED_SECTIONS}
POS_SIDEBAR_SLUGS = frozenset({"company-pos", "company-receipt"})
PAYMENTS_SIDEBAR_SLUGS = frozenset({"company-payments", "company-daraja"})


def settings_section_url(section):
    return reverse("employees:settings_section", kwargs={"section": section})


def get_settings_sections(*, active_slug=None):
    return [
        {
            **section,
            "href": settings_section_url(section["slug"]),
            "active": section["slug"] == active_slug if active_slug else False,
        }
        for section in SETTINGS_SECTIONS
    ]


def get_settings_section(slug):
    section = SETTINGS_SECTION_BY_SLUG.get(slug) or SETTINGS_NESTED_BY_SLUG.get(slug)
    if section is None:
        return None
    return {
        **section,
        "href": settings_section_url(section["slug"]),
    }


def sidebar_for_settings(role, *, active_view="home", profile=None):
    """Sidebar links unique to the System Settings pages.

    Section links (Company profile, theme, POS, payments) only appear on the
    main System Settings home page — not on nested settings section pages.
    POS settings pages also show Receipt settings in the sidebar.
    Payments settings pages also show Daraja settings in the sidebar.
    """
    from .module_permissions import employee_may

    def _allowed(submodule):
        return profile is None or employee_may(profile, "settings", submodule)

    dashboard_url = reverse(role_home_url_name(role))
    settings_url = reverse("employees:settings")
    primary = [_link("Dashboard", "layout-dashboard", href=dashboard_url)]
    if _allowed("home"):
        primary.append(
            _link(
                "System settings",
                "settings",
                href=settings_url,
                active=active_view == "home",
            )
        )
    if active_view == "home":
        section_links = [
            _link(
                section["label"],
                section["icon"],
                href=section["href"],
                active=False,
            )
            for section in get_settings_sections()
            if _allowed(section["slug"])
        ]
        primary.extend(section_links)
    elif active_view in POS_SIDEBAR_SLUGS:
        pos_section = get_settings_section("company-pos")
        receipt_section = get_settings_section("company-receipt")
        if _allowed("company-pos"):
            primary.append(
                _link(
                    pos_section["label"],
                    pos_section["icon"],
                    href=pos_section["href"],
                    active=active_view == "company-pos",
                )
            )
        if _allowed("company-receipt"):
            primary.append(
                _link(
                    receipt_section["label"],
                    receipt_section["icon"],
                    href=receipt_section["href"],
                    active=active_view == "company-receipt",
                )
            )
    elif active_view in PAYMENTS_SIDEBAR_SLUGS:
        payments_section = get_settings_section("company-payments")
        daraja_section = get_settings_section("company-daraja")
        if _allowed("company-payments"):
            primary.append(
                _link(
                    payments_section["label"],
                    payments_section["icon"],
                    href=payments_section["href"],
                    active=active_view == "company-payments",
                )
            )
        if _allowed("company-daraja"):
            primary.append(
                _link(
                    daraja_section["label"],
                    daraja_section["icon"],
                    href=daraja_section["href"],
                    active=active_view == "company-daraja",
                )
            )
    elif active_view == "whatsapp":
        whatsapp_section = get_settings_section("whatsapp")
        segment = role_url_segment(role)
        whatsapp_url = reverse(
            "employees:workspace_module",
            kwargs={"role_segment": segment, "module_slug": "whatsapp"},
        )
        if profile is None or employee_may(profile, "whatsapp", "view"):
            primary.append(
                _link(
                    "WhatsApp",
                    "messages-square",
                    href=whatsapp_url,
                )
            )
        if _allowed("whatsapp"):
            primary.append(
                _link(
                    whatsapp_section["label"],
                    whatsapp_section["icon"],
                    href=whatsapp_section["href"],
                    active=True,
                )
            )
    return resolve_sidebar_hrefs(
        {
            "page": "settings",
            "dashboard_url": dashboard_url,
            "primary": primary,
            "footer": _footer_site_links(
                settings_active=True,
                profile=profile,
                tail=[
                    _link("Sign out", "log-out", url_name="employees:logout", danger=True),
                ],
            ),
        }
    )
