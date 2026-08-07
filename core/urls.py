from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("manifest.webmanifest", views.web_manifest, name="web_manifest"),
]
