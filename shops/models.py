import uuid

from django.db import models


def shop_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"shops/images/{instance.pk or uuid.uuid4().hex}.{ext}"


def company_logo_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    return f"company/logo/{instance.pk or uuid.uuid4().hex}.{ext}"


class Shop(models.Model):
    name = models.CharField(max_length=200, db_index=True)
    location = models.CharField(max_length=255)
    email = models.EmailField()
    phone_number = models.CharField(max_length=40)
    login_code = models.CharField(max_length=6, unique=True, db_index=True)
    password_hash = models.CharField(max_length=128)
    image = models.ImageField(upload_to=shop_image_path, blank=True, null=True)
    is_suspended = models.BooleanField(default=False, db_index=True)
    is_hidden = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shops_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.location})"


class ShopReceiptKind(models.TextChoices):
    SALE = "sale", "Sale"
    CREDIT = "credit", "Credit"
    QUOTATION = "quotation", "Quotation"


class ShopReceiptStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PARTIAL_RETURN = "partial_return", "Partially returned"
    CANCELLED = "cancelled", "Cancelled"


class ShopPaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MPESA = "mpesa", "M-Pesa"
    BOTH = "both", "Cash & M-Pesa"
    NONE = "none", "None"


class Client(models.Model):
    full_name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=40)
    phone_normalized = models.CharField(max_length=40, unique=True, db_index=True)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "id"]

    def __str__(self):
        return f"{self.full_name} ({self.phone_number})"


class ShopReceipt(models.Model):
    shop = models.ForeignKey(
        Shop,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    receipt_number = models.CharField(max_length=40, db_index=True)
    kind = models.CharField(
        max_length=16,
        choices=ShopReceiptKind.choices,
        default=ShopReceiptKind.SALE,
        db_index=True,
    )
    payment_method = models.CharField(
        max_length=16,
        choices=ShopPaymentMethod.choices,
        default=ShopPaymentMethod.CASH,
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipts",
    )
    client_name = models.CharField(max_length=200, blank=True, default="")
    client_phone = models.CharField(max_length=40, blank=True, default="")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mpesa_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mpesa_receipt_number = models.CharField(max_length=40, blank=True, default="")
    share_whatsapp = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=ShopReceiptStatus.choices,
        default=ShopReceiptStatus.ACTIVE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        related_name="shop_receipts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_returned_at = models.DateTimeField(null=True, blank=True)
    last_returned_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_receipts_returned",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "receipt_number"],
                name="uniq_shop_receipt_number",
            ),
        ]

    def __str__(self):
        return f"{self.receipt_number} ({self.get_kind_display()})"


class ShopReceiptLine(models.Model):
    receipt = models.ForeignKey(
        ShopReceipt,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(
        "items.Item",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_receipt_lines",
    )
    item_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    returned_quantity = models.PositiveIntegerField(default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Cost per unit at sale (weighted average / last buy snapshot).",
    )
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    line_cogs = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="unit_cost × original quantity at sale; remaining COGS uses unit_cost × remaining qty.",
    )
    serial_numbers = models.JSONField(default=list, blank=True)
    returned_serial_numbers = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item_name} × {self.quantity}"

    @property
    def remaining_quantity(self) -> int:
        return max(0, int(self.quantity or 0) - int(self.returned_quantity or 0))

    @property
    def remaining_serial_numbers(self) -> list:
        returned = {
            str(s).strip()
            for s in (self.returned_serial_numbers or [])
            if str(s).strip()
        }
        return [
            str(s).strip()
            for s in (self.serial_numbers or [])
            if str(s).strip() and str(s).strip() not in returned
        ]


class ShopDaySession(models.Model):
    """Tracks open/close of a shop trading day with till balances."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.PROTECT,
        related_name="day_sessions",
    )
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    opening_mpesa = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    opening_credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_cash = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    closing_mpesa = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    closing_credit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    stock_confirmed_open = models.BooleanField(default=False)
    stock_confirmed_close = models.BooleanField(default=False)
    opened_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        related_name="shop_days_opened",
    )
    closed_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="shop_days_closed",
    )

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        state = "open" if self.is_open else "closed"
        return f"{self.shop.name} day session ({state})"

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class CompanyProfile(models.Model):
    """Company-wide identity details (singleton row)."""

    name = models.CharField(max_length=200, blank=True, default="")
    phone_number = models.CharField(max_length=40, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    location = models.CharField(max_length=255, blank=True, default="")
    logo = models.ImageField(upload_to=company_logo_path, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company profile"
        verbose_name_plural = "Company profile"

    def __str__(self):
        return self.name or "Company profile"


class CompanyPosSettings(models.Model):
    """Company-wide MY-SHOP POS options (singleton row)."""

    enable_sale = models.BooleanField(default=True)
    enable_credit = models.BooleanField(default=True)
    enable_quotation = models.BooleanField(default=True)
    enable_cash_sale_checkout = models.BooleanField(default=True)
    enable_cash = models.BooleanField(default=True)
    enable_mpesa = models.BooleanField(default=True)
    enable_cash_mpesa = models.BooleanField(default=True)
    enable_discount = models.BooleanField(default=True)
    enable_tax = models.BooleanField(default=False)
    tax_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    compulsory_print_on_sale = models.BooleanField(default=False)
    enable_print_bluetooth = models.BooleanField(default=True)
    enable_print_usb = models.BooleanField(default=True)
    enable_print_wifi = models.BooleanField(default=True)
    receipt_paper_width = models.CharField(
        max_length=8,
        choices=(
            ("80", "80 mm"),
            ("58", "58 mm"),
        ),
        default="80",
    )
    receipt_format_sale = models.CharField(max_length=8, default="S")
    receipt_format_credit = models.CharField(max_length=8, default="C")
    receipt_format_quotation = models.CharField(max_length=8, default="Q")
    mpesa_collection_type = models.CharField(
        max_length=16,
        choices=(
            ("paybill", "Paybill"),
            ("buy_goods", "Buy Goods"),
        ),
        blank=True,
        default="",
    )
    mpesa_business_number = models.CharField(max_length=20, blank=True, default="")
    mpesa_account_number = models.CharField(max_length=40, blank=True, default="")
    mpesa_till_number = models.CharField(max_length=20, blank=True, default="")
    receipt_font_size = models.CharField(
        max_length=16,
        choices=(
            ("small", "Small"),
            ("medium", "Medium"),
            ("large", "Large"),
            ("xlarge", "Extra large"),
        ),
        default="medium",
    )
    receipt_font_weight = models.CharField(
        max_length=16,
        choices=(
            ("regular", "Regular"),
            ("medium", "Medium"),
            ("bold", "Bold"),
            ("extrabold", "Extra bold"),
        ),
        default="regular",
    )
    enable_receipt_qr = models.BooleanField(default=False)
    receipt_qr_content = models.CharField(
        max_length=32,
        choices=(
            ("website", "Company website"),
            ("receipt_details", "Receipt details"),
        ),
        default="website",
    )
    receipt_qr_website = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company POS settings"
        verbose_name_plural = "Company POS settings"

    def __str__(self):
        return "Company POS settings"

    def enabled_kinds(self):
        kinds = []
        if self.enable_sale:
            kinds.append(ShopReceiptKind.SALE)
        if self.enable_credit:
            kinds.append(ShopReceiptKind.CREDIT)
        if self.enable_quotation:
            kinds.append(ShopReceiptKind.QUOTATION)
        return kinds

    def enabled_payment_methods(self):
        methods = []
        if self.enable_cash:
            methods.append(ShopPaymentMethod.CASH)
        if self.enable_mpesa:
            methods.append(ShopPaymentMethod.MPESA)
        if self.enable_cash_mpesa:
            methods.append(ShopPaymentMethod.BOTH)
        return methods

    def kind_enabled(self, kind: str) -> bool:
        return kind in self.enabled_kinds()

    def payment_method_enabled(self, method: str) -> bool:
        return method in self.enabled_payment_methods()

    def cash_sale_checkout_enabled(self) -> bool:
        return self.enable_cash or self.enable_mpesa or self.enable_cash_mpesa

    def enabled_print_channels(self):
        channels = []
        if self.enable_print_bluetooth:
            channels.append("bluetooth")
        if self.enable_print_usb:
            channels.append("usb")
        if self.enable_print_wifi:
            channels.append("wifi")
        return channels

    def print_channel_enabled(self, channel: str) -> bool:
        return channel in self.enabled_print_channels()

    def effective_tax_percent(self):
        from decimal import Decimal

        if not self.enable_tax:
            return Decimal("0.00")
        rate = Decimal(self.tax_percent or 0)
        if rate < 0:
            return Decimal("0.00")
        if rate > Decimal("100.00"):
            return Decimal("100.00")
        return rate.quantize(Decimal("0.01"))

    def tax_breakdown(self, subtotal):
        from decimal import Decimal

        base = Decimal(subtotal or 0).quantize(Decimal("0.01"))
        rate = self.effective_tax_percent()
        tax_amount = (base * rate / Decimal("100")).quantize(Decimal("0.01"))
        return {
            "subtotal": base,
            "tax_percent": rate,
            "tax_amount": tax_amount,
            "total": (base + tax_amount).quantize(Decimal("0.01")),
        }

    def mpesa_payment_details(self) -> dict:
        """Structured M-Pesa collection details for receipts and settings."""
        kind = (self.mpesa_collection_type or "").strip().lower()
        if kind == "paybill":
            business = (self.mpesa_business_number or "").strip()
            account = (self.mpesa_account_number or "").strip()
            if len(business) < 5:
                return {"type": "", "label": "", "lines": []}
            lines = [f"Business No: {business}"]
            if account:
                lines.append(f"Account No: {account}")
            return {
                "type": "paybill",
                "label": "Paybill",
                "business_number": business,
                "account_number": account,
                "till_number": "",
                "lines": lines,
            }
        if kind == "buy_goods":
            till = (self.mpesa_till_number or "").strip()
            if len(till) < 5:
                return {"type": "", "label": "", "lines": []}
            return {
                "type": "buy_goods",
                "label": "Buy Goods",
                "business_number": "",
                "account_number": "",
                "till_number": till,
                "lines": [f"Till No: {till}"],
            }
        return {"type": "", "label": "", "lines": []}


class CompanyStockSettings(models.Model):
    """Company-wide compulsory fields for stock in/out/request (singleton row)."""

    # Stock in
    require_buying_price_on_in = models.BooleanField(default=True)
    require_supplier_on_in = models.BooleanField(default=True)
    require_payment_status_on_in = models.BooleanField(default=True)

    # Stock out
    require_reason_on_out = models.BooleanField(default=True)
    require_refund_on_out = models.BooleanField(default=True)

    # Stock request
    require_note_on_request = models.BooleanField(default=False)

    # Legacy unused column (was phone last-4; last-4 applies to sell serials only).
    enable_supplier_last4_search = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company stock settings"
        verbose_name_plural = "Company stock settings"

    def __str__(self):
        return "Company stock settings"

    def as_requirements_dict(self) -> dict:
        return {
            "in": {
                "buying_price": bool(self.require_buying_price_on_in),
                "supplier": bool(self.require_supplier_on_in),
                "payment_status": bool(self.require_payment_status_on_in),
            },
            "out": {
                "reason": bool(self.require_reason_on_out),
                "refund": bool(self.require_refund_on_out),
            },
            "request": {
                "note": bool(self.require_note_on_request),
            },
        }


class DarajaEnvironment(models.TextChoices):
    SANDBOX = "sandbox", "Sandbox"
    PRODUCTION = "production", "Production"


class CompanyDarajaSettings(models.Model):
    """Safaricom Daraja / Lipa Na M-Pesa STK Push credentials (singleton)."""

    enable_stk_push = models.BooleanField(default=False)
    environment = models.CharField(
        max_length=16,
        choices=DarajaEnvironment.choices,
        default=DarajaEnvironment.SANDBOX,
    )
    consumer_key = models.CharField(max_length=255, blank=True, default="")
    consumer_secret = models.CharField(max_length=255, blank=True, default="")
    passkey = models.CharField(max_length=255, blank=True, default="")
    shortcode = models.CharField(max_length=20, blank=True, default="")
    callback_base_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Public HTTPS base URL for STK callbacks (e.g. https://xxxx.ngrok-free.app).",
    )
    credentials_valid = models.BooleanField(default=False)
    credentials_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company Daraja settings"
        verbose_name_plural = "Company Daraja settings"

    def __str__(self):
        return "Company Daraja settings"

    def has_credentials(self) -> bool:
        return bool(
            (self.consumer_key or "").strip()
            and (self.consumer_secret or "").strip()
            and (self.passkey or "").strip()
            and (self.shortcode or "").strip()
        )

    def has_callback_base_url(self) -> bool:
        return bool((self.callback_base_url or "").strip())

    def has_usable_callback_base(self) -> bool:
        """True when a Safaricom-reachable (public HTTPS) callback base is available."""
        from django.conf import settings as dj_settings

        from shops.daraja_stk import is_safaricom_callback_base

        if is_safaricom_callback_base(self.callback_base_url or ""):
            return True
        return is_safaricom_callback_base(
            getattr(dj_settings, "DARAJA_CALLBACK_BASE_URL", "") or ""
        )

    def is_ready_for_stk(self) -> bool:
        return bool(
            self.enable_stk_push
            and self.credentials_valid
            and self.has_credentials()
            and self.has_usable_callback_base()
        )

    def stk_not_ready_reason(self) -> str:
        """Short reason shown when STK cannot run (empty when ready)."""
        if self.is_ready_for_stk():
            return ""
        if not self.has_credentials() or not self.credentials_valid:
            return "verify Daraja credentials"
        if not self.has_usable_callback_base():
            return "open via public HTTPS / ngrok"
        if not self.enable_stk_push:
            return "STK disabled"
        return "STK not ready"


class SmsProvider(models.TextChoices):
    AFRICAS_TALKING = "africas_talking", "Africa's Talking"
    TWILIO = "twilio", "Twilio"
    CUSTOM = "custom", "Custom HTTP"


class CompanyCommunicationsSettings(models.Model):
    """Company-wide messaging channels, automations, and bulk send (singleton)."""

    # Channels
    enable_whatsapp = models.BooleanField(default=False)
    enable_message = models.BooleanField(default=False)
    enable_sms = models.BooleanField(default=False)

    # Capabilities
    enable_automations = models.BooleanField(default=False)
    enable_bulk_send = models.BooleanField(default=False)

    # Automation triggers (used when enable_automations is on)
    auto_sale_receipt = models.BooleanField(default=False)
    auto_quotation = models.BooleanField(default=False)
    auto_payment_reminder = models.BooleanField(default=False)
    auto_credit_due = models.BooleanField(default=False)

    # WhatsApp Cloud API
    whatsapp_phone_number_id = models.CharField(max_length=64, blank=True, default="")
    whatsapp_business_account_id = models.CharField(max_length=64, blank=True, default="")
    whatsapp_access_token = models.CharField(max_length=512, blank=True, default="")
    whatsapp_from_number = models.CharField(max_length=32, blank=True, default="")

    # SMS / text
    sms_provider = models.CharField(
        max_length=32,
        choices=SmsProvider.choices,
        default=SmsProvider.AFRICAS_TALKING,
    )
    sms_api_key = models.CharField(max_length=255, blank=True, default="")
    sms_api_secret = models.CharField(max_length=255, blank=True, default="")
    sms_sender_id = models.CharField(max_length=32, blank=True, default="")
    sms_api_base_url = models.CharField(max_length=255, blank=True, default="")

    # In-app / message channel
    message_from_name = models.CharField(max_length=120, blank=True, default="")
    message_reply_to = models.EmailField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company WhatsApp settings"
        verbose_name_plural = "Company WhatsApp settings"

    def __str__(self):
        return "Company WhatsApp settings"

    def has_whatsapp_credentials(self) -> bool:
        return bool(
            (self.whatsapp_phone_number_id or "").strip()
            and (self.whatsapp_access_token or "").strip()
        )

    def has_sms_credentials(self) -> bool:
        return bool(
            (self.sms_api_key or "").strip()
            and (self.sms_sender_id or "").strip()
        )


class MpesaStkPurpose(models.TextChoices):
    SALE = "sale", "Sale checkout"
    CREDIT = "credit", "Credit account pay"


class MpesaStkStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


def _new_stk_public_id() -> str:
    return str(uuid.uuid4())


class MpesaStkPayment(models.Model):
    """Tracks a Daraja STK Push request through callback confirmation."""

    public_id = models.CharField(
        max_length=36,
        unique=True,
        editable=False,
        db_index=True,
        default=_new_stk_public_id,
    )
    purpose = models.CharField(
        max_length=16,
        choices=MpesaStkPurpose.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=MpesaStkStatus.choices,
        default=MpesaStkStatus.PENDING,
        db_index=True,
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_stk_payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    phone = models.CharField(max_length=20, db_index=True)
    account_reference = models.CharField(max_length=40, blank=True, default="")
    description = models.CharField(max_length=80, blank=True, default="")
    merchant_request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    checkout_request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    mpesa_receipt_number = models.CharField(max_length=40, blank=True, default="", db_index=True)
    result_code = models.CharField(max_length=16, blank=True, default="")
    result_desc = models.CharField(max_length=255, blank=True, default="")
    account_kind = models.CharField(max_length=16, blank=True, default="")
    account_id = models.PositiveIntegerField(null=True, blank=True)
    applied = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_stk_payments",
    )
    receipt = models.ForeignKey(
        "ShopReceipt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stk_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"STK {self.public_id} ({self.status})"


class ExpensePaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    PARTIAL = "partial", "Partial"


class ExpenseCategory(models.TextChoices):
    RENT = "rent", "Rent"
    UTILITIES = "utilities", "Utilities"
    TRANSPORT = "transport", "Transport"
    SALARIES = "salaries", "Salaries"
    PACKAGING = "packaging", "Packaging"
    MAINTENANCE = "maintenance", "Maintenance"
    MARKETING = "marketing", "Marketing"
    OFFICE = "office", "Office supplies"
    SECURITY = "security", "Security"
    FOOD = "food", "Food & refreshments"
    OWNER_DRAWINGS = "owner_drawings", "Owner drawings"
    MISC = "misc", "Miscellaneous"


class ExpenseSupplier(models.Model):
    """Outside vendor used when registering shop expenses (separate from stock suppliers)."""

    name = models.CharField(max_length=160, db_index=True)
    phone_country_code = models.CharField(max_length=8, db_index=True)
    phone_country_iso = models.CharField(max_length=2, blank=True, default="KE")
    phone_number = models.CharField(max_length=20, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "phone_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["phone_country_code", "phone_number"],
                name="uniq_expense_supplier_phone",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.phone_country_code} {self.phone_number})"


class Expense(models.Model):
    shop = models.ForeignKey(
        Shop,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    category = models.CharField(
        max_length=32,
        choices=ExpenseCategory.choices,
        db_index=True,
    )
    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(
        max_length=16,
        choices=ExpensePaymentStatus.choices,
        default=ExpensePaymentStatus.UNPAID,
        db_index=True,
    )
    supplier = models.ForeignKey(
        ExpenseSupplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone_country_code = models.CharField(max_length=8, blank=True, default="")
    supplier_phone_number = models.CharField(max_length=20, blank=True, default="")
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        related_name="expenses_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} — {self.amount} ({self.shop.name})"
