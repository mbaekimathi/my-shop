from django.contrib import admin

from .models import Item, ItemSerial, ShopItemPrice, ShopStock, StockMovement, StockMovementLine, Supplier


class StockMovementLineInline(admin.TabularInline):
    model = StockMovementLine
    extra = 0
    autocomplete_fields = ("item",)
    readonly_fields = ("serial_numbers",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone_country_code",
        "phone_number",
        "phone_country_iso",
        "updated_at",
    )
    search_fields = ("name", "phone_number", "phone_country_code")
    list_filter = ("phone_country_code", "phone_country_iso")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "stock",
        "shop_price",
        "use_individual_shop_prices",
        "track_serial_number",
        "is_suspended",
        "minimum_selling_price",
        "created_at",
    )
    list_filter = (
        "category",
        "use_individual_shop_prices",
        "track_serial_number",
        "is_suspended",
        "created_at",
    )
    search_fields = ("name", "category", "description")


@admin.register(ShopItemPrice)
class ShopItemPriceAdmin(admin.ModelAdmin):
    list_display = ("item", "shop", "price", "updated_at")
    list_filter = ("shop",)
    search_fields = ("item__name", "shop__name")
    autocomplete_fields = ("item", "shop")


@admin.register(ShopStock)
class ShopStockAdmin(admin.ModelAdmin):
    list_display = (
        "item",
        "shop",
        "quantity",
        "low_stock_threshold",
        "low_stock_manual",
        "updated_at",
    )
    list_filter = ("shop",)
    search_fields = ("item__name", "shop__name")
    autocomplete_fields = ("item", "shop")


@admin.register(ItemSerial)
class ItemSerialAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "item",
        "shop",
        "is_available",
        "status_override",
        "updated_at",
    )
    list_filter = ("is_available", "status_override", "shop", "item__category")
    search_fields = ("serial_number", "item__name", "shop__name")
    autocomplete_fields = ("item", "shop")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("id", "movement_type", "shop", "requested_from_shop", "payment_status", "created_by", "created_at")
    list_filter = ("movement_type", "shop", "requested_from_shop", "payment_status", "created_at")
    search_fields = ("notes",)
    autocomplete_fields = ("shop", "requested_from_shop")
    inlines = (StockMovementLineInline,)
