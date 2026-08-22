from django.urls import path

from . import api_views, views
from items.views import (
    serial_in_stock_check_api,
    serial_search_api,
    supplier_search_api,
)
from shops.views import expense_supplier_search_api

urlpatterns = [
    path("login/", views.employee_login, name="login"),
    path("register/", views.employee_register, name="register"),
    path(
        "api/check-employee-id/",
        views.check_employee_id,
        name="check_employee_id",
    ),
    path("api/ping/", api_views.ping_api, name="ping"),
    path("api/sync/", api_views.sync_api, name="sync"),
    path("api/sync/register/", api_views.sync_register_api, name="sync_register"),
    path("api/suppliers/", supplier_search_api, name="supplier_search"),
    path(
        "api/expense-suppliers/",
        expense_supplier_search_api,
        name="expense_supplier_search",
    ),
    path("api/serials/", serial_search_api, name="serial_search"),
    path(
        "api/serials/in-stock/",
        serial_in_stock_check_api,
        name="serial_in_stock_check",
    ),
    path(
        "register/submitted/<str:employee_id>/",
        views.registration_submitted,
        name="registration_submitted",
    ),
    path("pending/", views.pending_approval, name="pending"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("profile/", views.employee_profile, name="profile"),
    path("settings/", views.employee_settings, name="settings"),
    path(
        "settings/communications/",
        views.legacy_settings_communications_redirect,
        name="legacy_settings_communications",
    ),
    path(
        "settings/<slug:section>/",
        views.employee_settings_section,
        name="settings_section",
    ),
    path(
        "developer-payments/dismiss/",
        views.developer_payment_dismiss,
        name="developer_payment_dismiss",
    ),
    path(
        "developer-payments/stk/",
        views.developer_payment_stk_initiate,
        name="developer_payment_stk_initiate",
    ),
    path(
        "developer-payments/stk/<uuid:payment_id>/",
        views.developer_payment_stk_status,
        name="developer_payment_stk_status",
    ),
    path("logout/", views.employee_logout, name="logout"),
    path(
        "to-employee-login/",
        views.switch_to_employee_login,
        name="to_employee_login",
    ),
]
