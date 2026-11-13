"""Read-only views over MY-SHOP tables in the shared `myshop` database.

All models are `managed = False` — MY-SHOP owns the schema; MY-CHATBOT only
reads these tables and never migrates them. Only the columns the chatbot
needs are declared.
"""

from django.db import models


class Shop(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=255)
    email = models.EmailField()
    phone_number = models.CharField(max_length=40)
    # Extra NOT NULL columns on the real MY-SHOP table — declare with
    # defaults so ORM inserts work when chat runs inside MY-SHOP.
    receipt_phone_number = models.CharField(max_length=40, blank=True, default="")
    theme = models.CharField(max_length=32, blank=True, default="")
    maps_url = models.CharField(max_length=500, blank=True, default="")
    active_session_key = models.CharField(max_length=40, blank=True, default="")
    login_code = models.CharField(max_length=6)
    password_hash = models.CharField(max_length=128)
    is_suspended = models.BooleanField()
    is_hidden = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "shops_shop"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.location})"


class Item(models.Model):
    id = models.BigAutoField(primary_key=True)
    category = models.CharField(max_length=120)
    name = models.CharField(max_length=200)
    minimum_selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    shop_price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.DecimalField(max_digits=12, decimal_places=3)
    low_stock_notify = models.BooleanField()
    low_stock_threshold = models.PositiveIntegerField()
    track_serial_number = models.BooleanField(default=False)
    is_suspended = models.BooleanField()

    class Meta:
        managed = False
        db_table = "items_item"

    def __str__(self):
        return self.name


class ShopItemPrice(models.Model):
    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_sold = models.BooleanField()

    class Meta:
        managed = False
        db_table = "items_shopitemprice"


class ShopStock(models.Model):
    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    low_stock_threshold = models.PositiveIntegerField(default=0)
    low_stock_manual = models.BooleanField(default=False)
    average_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "items_shopstock"


class ShopReceipt(models.Model):
    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    receipt_number = models.CharField(max_length=40)
    kind = models.CharField(max_length=16)
    payment_method = models.CharField(max_length=16)
    client_id = models.BigIntegerField(null=True, blank=True)
    client_name = models.CharField(max_length=200)
    client_phone = models.CharField(max_length=40, blank=True, default="")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    cash_amount = models.DecimalField(max_digits=12, decimal_places=2)
    mpesa_amount = models.DecimalField(max_digits=12, decimal_places=2)
    mpesa_receipt_number = models.CharField(max_length=40, blank=True, default="")
    share_whatsapp = models.BooleanField(default=False)
    created_by_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20)
    settled_from_credit = models.BooleanField(default=False)
    settled_from_trade = models.BooleanField(default=False)
    trade_exchange_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    trade_settlements = models.JSONField(default=list)
    return_payment_events = models.JSONField(default=list)
    source_shop_id = models.BigIntegerField(null=True, blank=True)
    credit_due_date = models.DateField(null=True, blank=True)
    last_returned_at = models.DateTimeField(null=True, blank=True)
    last_returned_by_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "shops_shopreceipt"

    def __str__(self):
        return f"{self.receipt_number} ({self.kind})"


class ShopReceiptLine(models.Model):
    id = models.BigAutoField(primary_key=True)
    receipt_id = models.BigIntegerField()
    item_id = models.BigIntegerField(null=True, blank=True)
    item_name = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    returned_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    line_cogs = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    serial_numbers = models.JSONField(default=list, blank=True)
    returned_serial_numbers = models.JSONField(default=list, blank=True)
    return_batches = models.JSONField(default=list, blank=True)

    class Meta:
        managed = False
        db_table = "shops_shopreceiptline"


class Client(models.Model):
    id = models.BigAutoField(primary_key=True)
    full_name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=40)
    phone_normalized = models.CharField(max_length=40)
    email = models.CharField(max_length=254, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "shops_client"


class ShopDaySession(models.Model):
    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2)
    opening_mpesa = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        managed = False
        db_table = "shops_shopdaysession"
        ordering = ["-opened_at"]

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class ExpenseSupplier(models.Model):
    """Outside vendor for shop expenses (separate from stock suppliers)."""

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=160)
    phone_country_code = models.CharField(max_length=8)
    phone_country_iso = models.CharField(max_length=2, blank=True, default="KE")
    phone_number = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "shops_expensesupplier"

    def __str__(self):
        return f"{self.name} ({self.phone_country_code} {self.phone_number})"


class Expense(models.Model):
    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    category = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    cash_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mpesa_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=16)
    supplier_id = models.BigIntegerField(null=True, blank=True)
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone_country_code = models.CharField(
        max_length=8, blank=True, default=""
    )
    supplier_phone_number = models.CharField(
        max_length=20, blank=True, default=""
    )
    created_by_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "shops_expense"
        ordering = ["-created_at"]


class CompanyProfile(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=40)
    email = models.EmailField()
    location = models.CharField(max_length=255)

    class Meta:
        managed = False
        db_table = "shops_companyprofile"


class CompanyAnalyticsSettings(models.Model):
    """Company-wide analytics defaults (singleton row, pk=1).

    `receipt_data_source` is `pending` or `confirmed` — mirrors MY-SHOP
    `shops.0083_company_analytics_settings`.
    """

    id = models.BigAutoField(primary_key=True)
    receipt_data_source = models.CharField(max_length=16, default="pending")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "shops_companyanalyticssettings"


class CompanyPosSettings(models.Model):
    """Company-wide MY-SHOP POS defaults (singleton row, pk=1).

    Only the columns the trade flow needs are declared.
    """

    id = models.BigAutoField(primary_key=True)
    enable_trade_out = models.BooleanField(default=True)
    enable_trade_in = models.BooleanField(default=True)
    enable_stock_tracking = models.BooleanField(default=True)
    receipt_format_trade_out = models.CharField(max_length=8, default="T")
    receipt_format_trade_in = models.CharField(max_length=8, default="TI")

    class Meta:
        managed = False
        db_table = "shops_companypossettings"


class ShopPosSettings(models.Model):
    """Per-shop POS overrides — apply only when the override flag is on."""

    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    override_pos = models.BooleanField(default=False)
    override_receipt = models.BooleanField(default=False)
    enable_trade_out = models.BooleanField(default=True)
    enable_trade_in = models.BooleanField(default=True)
    enable_stock_tracking = models.BooleanField(default=True)
    receipt_format_trade_out = models.CharField(max_length=8, default="T")
    receipt_format_trade_in = models.CharField(max_length=8, default="TI")

    class Meta:
        managed = False
        db_table = "shops_shoppossettings"


class ItemSerial(models.Model):
    id = models.BigAutoField(primary_key=True)
    item_id = models.BigIntegerField()
    shop_id = models.BigIntegerField(null=True, blank=True)
    serial_number = models.CharField(max_length=120)
    is_available = models.BooleanField(default=True)
    status_override = models.CharField(max_length=16, blank=True, default="")
    status_reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "items_itemserial"


class Supplier(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=160)
    phone_country_code = models.CharField(max_length=8)
    phone_country_iso = models.CharField(max_length=2, blank=True, default="KE")
    phone_number = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "items_supplier"

    def __str__(self):
        return f"{self.name} ({self.phone_country_code} {self.phone_number})"


class StockMovement(models.Model):
    id = models.BigAutoField(primary_key=True)
    movement_type = models.CharField(max_length=16)
    entry_source = models.CharField(max_length=32, blank=True, default="")
    shop_id = models.BigIntegerField(null=True, blank=True)
    requested_from_shop_id = models.BigIntegerField(null=True, blank=True)
    request_status = models.CharField(max_length=16, blank=True, default="")
    requester_notified = models.BooleanField(default=True)
    supplier_notified = models.BooleanField(default=True)
    responded_by_id = models.BigIntegerField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_note = models.TextField(blank=True, default="")
    payment_status = models.CharField(max_length=16, blank=True, default="")
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    rider_name = models.CharField(max_length=120, blank=True, default="")
    created_by_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "items_stockmovement"


class StockMovementLine(models.Model):
    id = models.BigAutoField(primary_key=True)
    movement_id = models.BigIntegerField()
    item_id = models.BigIntegerField(null=True, blank=True)
    item_name = models.CharField(max_length=200, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    buying_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=16, blank=True, default="")
    reason = models.CharField(max_length=16, blank=True, default="")
    refund = models.CharField(max_length=8, blank=True, default="")
    refund_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    note = models.TextField(blank=True)
    serial_numbers = models.JSONField(default=list, blank=True)
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone_country_code = models.CharField(max_length=8, blank=True, default="")
    supplier_phone_number = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        managed = False
        db_table = "items_stockmovementline"


class StoreStock(models.Model):
    """Central warehouse qty per item — independent of shop floor stock."""

    id = models.BigAutoField(primary_key=True)
    item_id = models.BigIntegerField(unique=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "items_storestock"


class StoreStockMovement(models.Model):
    """Ledger for central store stock in/out. Never mutates shop stock."""

    id = models.BigAutoField(primary_key=True)
    movement_type = models.CharField(max_length=8)  # in | out
    item_id = models.BigIntegerField()
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    balance_after = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    created_by_id = models.BigIntegerField()
    received_by_id = models.BigIntegerField()
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "items_storestockmovement"
        ordering = ["-created_at", "-id"]


class ShopDelivery(models.Model):
    """Customer delivery job from MY-SHOP checkout (`shops_shopdelivery`)."""

    STATUS_OPEN = "open"
    STATUS_ACCEPTED = "accepted"
    STATUS_DELIVERED = "delivered"
    STATUS_DELIVERED_PAID = "delivered_paid"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    DONE_STATUSES = frozenset(
        {
            STATUS_DELIVERED,
            STATUS_DELIVERED_PAID,
            STATUS_COMPLETED,
        }
    )

    id = models.BigAutoField(primary_key=True)
    receipt_id = models.BigIntegerField(unique=True)
    shop_id = models.BigIntegerField()
    status = models.CharField(max_length=16, default=STATUS_OPEN)
    location_text = models.CharField(max_length=500)
    latitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    maps_url = models.URLField(max_length=500, blank=True, default="")
    distance_km = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    eta_minutes = models.PositiveIntegerField(null=True, blank=True)
    expected_at = models.DateTimeField(null=True, blank=True)
    accepted_by_id = models.BigIntegerField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    receiver_name = models.CharField(max_length=200, blank=True, default="")
    payment_reference = models.CharField(max_length=64, blank=True, default="")
    payment_at = models.DateTimeField(null=True, blank=True)
    payment_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    created_by_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "shops_shopdelivery"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Delivery {self.pk}"


class ShopDeliveryAssignedRider(models.Model):
    """Optional assignees — empty means any rider may accept."""

    id = models.BigAutoField(primary_key=True)
    shopdelivery_id = models.BigIntegerField()
    employeeprofile_id = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = "shops_shopdelivery_assigned_riders"
