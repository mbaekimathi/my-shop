from django.contrib import admin

from .models import Product, Sale, SaleLine


class SaleLineInline(admin.TabularInline):
    model = SaleLine
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "price", "stock", "is_active")
    list_per_page = 50


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("client_id", "employee", "total", "source", "sold_at")
    inlines = [SaleLineInline]
    list_per_page = 50
