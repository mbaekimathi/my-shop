from django.urls import path

from . import api_views

app_name = "pos"

urlpatterns = [
    path("api/ping/", api_views.ping_api, name="ping"),
    path("api/products/", api_views.product_list_api, name="product_list"),
    path("api/sales/", api_views.sale_create_api, name="sale_create"),
]
