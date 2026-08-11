from django.urls import path

from . import views
from shops import credit_note_views

app_name = "core"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("manifest.webmanifest", views.web_manifest, name="web_manifest"),
    path(
        "credit-note/<str:token>/",
        credit_note_views.credit_note_page,
        name="credit_note",
    ),
    path(
        "credit-note/<str:token>/stk/",
        credit_note_views.credit_note_stk_initiate,
        name="credit_note_stk",
    ),
    path(
        "credit-note/<str:token>/pay/",
        credit_note_views.credit_note_pay,
        name="credit_note_pay",
    ),
    path(
        "credit-note/<str:token>/stk/<str:payment_id>/",
        credit_note_views.credit_note_stk_status,
        name="credit_note_stk_status",
    ),
]
