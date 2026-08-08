from django.contrib import admin

from .models import (
    Client,
    CompanyCommunicationsSettings,
    CompanyPosSettings,
    CompanyProfile,
    Expense,
    ExpenseSupplier,
    Shop,
    ShopDaySession,
    ShopReceipt,
    ShopReceiptLine,
)

@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "location",
        "email",
        "phone_number",
        "login_code",
        "is_suspended",
        "is_hidden",
        "created_at",
    )
    list_filter = ("is_suspended", "is_hidden", "created_at")
    search_fields = ("name", "location", "email", "phone_number", "login_code")
    readonly_fields = ("password_hash",)


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "phone_number", "email", "location", "updated_at")
    search_fields = ("name", "email", "phone_number", "location")
    readonly_fields = ("updated_at",)


@admin.register(CompanyPosSettings)
class CompanyPosSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "enable_sale",
        "enable_credit",
        "enable_quotation",
        "enable_cash",
        "enable_mpesa",
        "enable_cash_mpesa",
        "enable_discount",
        "enable_tax",
        "tax_percent",
        "compulsory_print_on_sale",
        "enable_print_bluetooth",
        "enable_print_usb",
        "enable_print_wifi",
        "receipt_paper_width",
        "receipt_format_sale",
        "receipt_format_credit",
        "receipt_format_quotation",
        "mpesa_collection_type",
        "mpesa_business_number",
        "mpesa_account_number",
        "mpesa_till_number",
        "receipt_font_size",
        "receipt_font_weight",
        "enable_receipt_qr",
        "receipt_qr_content",
        "receipt_qr_website",
        "updated_at",
    )
    readonly_fields = ("updated_at",)


@admin.register(CompanyCommunicationsSettings)
class CompanyCommunicationsSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "enable_whatsapp",
        "enable_message",
        "enable_sms",
        "enable_automations",
        "enable_bulk_send",
        "sms_provider",
        "updated_at",
    )
    readonly_fields = ("updated_at",)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone_number", "phone_normalized", "created_at")
    search_fields = ("full_name", "phone_number", "phone_normalized")
    readonly_fields = ("phone_normalized", "created_at", "updated_at")


@admin.register(ShopDaySession)
class ShopDaySessionAdmin(admin.ModelAdmin):
    list_display = (
        "shop",
        "opened_at",
        "closed_at",
        "opening_cash",
        "opening_mpesa",
        "opening_credit",
        "opened_by",
        "closed_by",
    )
    list_filter = ("opened_at", "closed_at")
    search_fields = ("shop__name",)
    readonly_fields = ("opened_at",)


class ShopReceiptLineInline(admin.TabularInline):
    model = ShopReceiptLine
    extra = 0
    readonly_fields = (
        "item",
        "item_name",
        "quantity",
        "returned_quantity",
        "unit_price",
        "line_total",
        "serial_numbers",
        "returned_serial_numbers",
    )


@admin.register(ShopReceipt)
class ShopReceiptAdmin(admin.ModelAdmin):
    list_display = (
        "receipt_number",
        "shop",
        "kind",
        "status",
        "payment_method",
        "client_name",
        "subtotal",
        "tax_amount",
        "total",
        "created_by",
        "created_at",
    )
    list_filter = ("kind", "status", "payment_method", "created_at")
    search_fields = ("receipt_number", "client_name", "client_phone")
    inlines = [ShopReceiptLineInline]


@admin.register(ExpenseSupplier)
class ExpenseSupplierAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone_country_code",
        "phone_number",
        "phone_country_iso",
        "updated_at",
    )
    search_fields = ("name", "phone_number", "phone_country_code")
    list_filter = ("phone_country_code", "phone_country_iso")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "shop",
        "category",
        "amount",
        "payment_status",
        "supplier_name",
        "created_by",
        "created_at",
    )
    list_filter = ("category", "payment_status", "created_at")
    search_fields = ("name", "supplier_name", "supplier_phone_number", "shop__name")
    autocomplete_fields = ("shop", "supplier", "created_by")
