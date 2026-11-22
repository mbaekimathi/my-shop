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
    stock_count_enabled = models.BooleanField(default=True)
    deduct_stock_on_sale = models.BooleanField(default=True)
    low_stock_notify = models.BooleanField()
    low_stock_threshold = models.PositiveIntegerField()
    track_serial_number = models.BooleanField(default=False)
    use_individual_shop_prices = models.BooleanField(default=False)
    is_combo = models.BooleanField(default=False)
    is_suspended = models.BooleanField()

    class Meta:
        managed = False
        db_table = "items_item"

    def __str__(self):
        return self.name


class ItemComboComponent(models.Model):
    id = models.BigAutoField(primary_key=True)
    combo_id = models.BigIntegerField()
    component_id = models.BigIntegerField()
    qty = models.DecimalField(max_digits=12, decimal_places=4)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        managed = False
        db_table = "items_itemcombocomponent"


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
    card_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mpesa_receipt_number = models.CharField(max_length=40, blank=True, default="")
    share_whatsapp = models.BooleanField(default=False)
    created_by_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20)
    settled_from_credit = models.BooleanField(default=False)
    settled_from_trade = models.BooleanField(default=False)
    converted_from_kind = models.CharField(max_length=16, blank=True, default="")
    trade_exchange_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    trade_settlements = models.JSONField(default=list)
    return_payment_events = models.JSONField(default=list)
    source_shop_id = models.BigIntegerField(null=True, blank=True)
    credit_due_date = models.DateField(null=True, blank=True)
    confirmed_payment_at = models.DateTimeField(null=True, blank=True)
    last_returned_at = models.DateTimeField(null=True, blank=True)
    last_returned_by_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "shops_shopreceipt"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        if "kind" not in instance.get_deferred_fields():
            instance._db_kind = instance.kind
        return instance

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._db_kind = self.kind

    def save(self, *args, **kwargs):
        # Same conversion guard as shops.ShopReceipt: a write that changes
        # `kind` must record the source so tallies show the dual code.
        update_fields = kwargs.get("update_fields")
        if self.pk is not None and (
            update_fields is None or "kind" in update_fields
        ):
            previous_kind = getattr(self, "_db_kind", None)
            if previous_kind is None:
                previous_kind = (
                    type(self)
                    ._default_manager.filter(pk=self.pk)
                    .values_list("kind", flat=True)
                    .first()
                )
            if previous_kind and previous_kind != self.kind:
                self.converted_from_kind = previous_kind
                if update_fields is not None and (
                    "converted_from_kind" not in update_fields
                ):
                    kwargs["update_fields"] = set(update_fields) | {
                        "converted_from_kind"
                    }
        super().save(*args, **kwargs)
        self._db_kind = self.kind

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
    opening_card = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    opening_credit = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    closing_cash = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    closing_mpesa = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    closing_card = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    closing_credit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    stock_confirmed_open = models.BooleanField(default=False)
    stock_confirmed_close = models.BooleanField(default=False)
    opened_by_id = models.BigIntegerField()
    closed_by_id = models.BigIntegerField(null=True, blank=True)

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
    card_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
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


class CompanyPosSettings(models.Model):
    """Company-wide MY-SHOP POS defaults (singleton row, pk=1).

    Only the columns the trade flow needs are declared.
    """

    id = models.BigAutoField(primary_key=True)
    enable_sale = models.BooleanField(default=True)
    enable_credit = models.BooleanField(default=True)
    enable_quotation = models.BooleanField(default=True)
    enable_trade_out = models.BooleanField(default=True)
    enable_trade_in = models.BooleanField(default=True)
    enable_gift = models.BooleanField(default=True)
    enable_cash = models.BooleanField(default=True)
    enable_mpesa = models.BooleanField(default=True)
    enable_card = models.BooleanField(default=False)
    enable_cash_mpesa = models.BooleanField(default=True)
    enable_cash_card = models.BooleanField(default=False)
    enable_mpesa_card = models.BooleanField(default=False)
    enable_cash_mpesa_card = models.BooleanField(default=False)
    enable_discount = models.BooleanField(default=True)
    enable_stock_tracking = models.BooleanField(default=True)
    enable_shelf_mode = models.BooleanField(default=False)
    block_negative_stock = models.BooleanField(default=True)
    enable_combo_items = models.BooleanField(default=True)
    enable_open_close = models.BooleanField(default=True)
    enable_client_data = models.BooleanField(default=True)
    require_client_phone = models.BooleanField(default=False)
    require_client_name = models.BooleanField(default=False)
    require_mpesa_reference_on_confirm = models.BooleanField(default=False)
    receipt_format_sale = models.CharField(max_length=8, default="S")
    receipt_format_credit = models.CharField(max_length=8, default="C")
    receipt_format_quotation = models.CharField(max_length=8, default="Q")
    receipt_format_trade_out = models.CharField(max_length=8, default="T")
    receipt_format_trade_in = models.CharField(max_length=8, default="TI")
    receipt_format_gift = models.CharField(max_length=8, default="GF")

    class Meta:
        managed = False
        db_table = "shops_companypossettings"


class ShopPosSettings(models.Model):
    """Per-shop POS overrides — apply only while the override flag is on and
    the field is listed in the matching custom_*_fields set."""

    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    override_pos = models.BooleanField(default=False)
    override_receipt = models.BooleanField(default=False)
    custom_pos_fields = models.JSONField(default=list, blank=True)
    custom_receipt_fields = models.JSONField(default=list, blank=True)
    enable_sale = models.BooleanField(default=True)
    enable_credit = models.BooleanField(default=True)
    enable_quotation = models.BooleanField(default=True)
    enable_trade_out = models.BooleanField(default=True)
    enable_trade_in = models.BooleanField(default=True)
    enable_gift = models.BooleanField(default=True)
    enable_cash = models.BooleanField(default=True)
    enable_mpesa = models.BooleanField(default=True)
    enable_card = models.BooleanField(default=False)
    enable_cash_mpesa = models.BooleanField(default=True)
    enable_cash_card = models.BooleanField(default=False)
    enable_mpesa_card = models.BooleanField(default=False)
    enable_cash_mpesa_card = models.BooleanField(default=False)
    enable_discount = models.BooleanField(default=True)
    enable_stock_tracking = models.BooleanField(default=True)
    enable_shelf_mode = models.BooleanField(default=False)
    enable_open_close = models.BooleanField(default=True)
    enable_client_data = models.BooleanField(default=True)
    require_client_phone = models.BooleanField(default=False)
    require_client_name = models.BooleanField(default=False)
    require_mpesa_reference_on_confirm = models.BooleanField(default=False)
    receipt_format_sale = models.CharField(max_length=8, default="S")
    receipt_format_credit = models.CharField(max_length=8, default="C")
    receipt_format_quotation = models.CharField(max_length=8, default="Q")
    receipt_format_trade_out = models.CharField(max_length=8, default="T")
    receipt_format_trade_in = models.CharField(max_length=8, default="TI")
    receipt_format_gift = models.CharField(max_length=8, default="GF")

    class Meta:
        managed = False
        db_table = "shops_shoppossettings"


class CompanyStockSettings(models.Model):
    """Company-wide compulsory fields for stock in/out/request (singleton row,
    pk=1). Mirrors `shops_companystocksettings` — only the item-stock flags the
    chatbot needs; the shelf_* flags belong to the shelf pages."""

    id = models.BigAutoField(primary_key=True)
    require_buying_price_on_in = models.BooleanField(default=True)
    require_supplier_on_in = models.BooleanField(default=True)
    require_payment_status_on_in = models.BooleanField(default=True)
    require_reason_on_out = models.BooleanField(default=True)
    require_refund_on_out = models.BooleanField(default=True)
    require_note_on_request = models.BooleanField(default=False)
    shelf_require_buying_price_on_in = models.BooleanField(default=True)
    shelf_require_supplier_on_in = models.BooleanField(default=True)
    shelf_require_payment_status_on_in = models.BooleanField(default=True)
    shelf_require_reason_on_out = models.BooleanField(default=True)
    shelf_require_refund_on_out = models.BooleanField(default=True)
    shelf_require_note_on_request = models.BooleanField(default=False)
    enable_supplier_last4_search = models.BooleanField(default=False)
    hide_stock_mode = models.CharField(max_length=16, default="show")
    hide_stock_categories = models.JSONField(default=list, blank=True)
    hide_stock_item_ids = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "shops_companystocksettings"

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

    def as_shelf_requirements_dict(self) -> dict:
        return {
            "in": {
                "buying_price": bool(self.shelf_require_buying_price_on_in),
                "supplier": bool(self.shelf_require_supplier_on_in),
                "payment_status": bool(
                    self.shelf_require_payment_status_on_in
                ),
            },
            "out": {
                "reason": bool(self.shelf_require_reason_on_out),
                "refund": bool(self.shelf_require_refund_on_out),
            },
            "request": {
                "note": bool(self.shelf_require_note_on_request),
            },
        }


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
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone = models.CharField(max_length=40, blank=True, default="")
    payment_status = models.CharField(max_length=16, blank=True, default="")
    buying_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    reason = models.CharField(max_length=16, blank=True, default="")
    refund = models.CharField(max_length=8, blank=True, default="")
    refund_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    source = models.CharField(max_length=16, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "items_storestockmovement"
        ordering = ["-created_at", "-id"]


class ShelfItem(models.Model):
    """Shelf-mode stock line for one shop (`items_shelfitem`).

    Never sold at the POS — only the columns the assistant needs.
    """

    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=120, blank=True, default="")
    unit = models.CharField(max_length=40, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    low_stock_threshold = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    is_archived = models.BooleanField(default=False)
    is_suspended = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = "items_shelfitem"


class ShelfStockMovement(models.Model):
    """Shelf ledger row (`items_shelfstockmovement`) — one row per move.

    ``movement_type`` is register | in | out; transfers and trades keep the
    leg's ``reason`` (``transfer`` / ``trade_in`` / ``trade_out``) and a
    "Sent to" / "Received from" ``note``.
    """

    id = models.BigAutoField(primary_key=True)
    shelf_item_id = models.BigIntegerField()
    movement_type = models.CharField(max_length=10)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    balance_after = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone = models.CharField(max_length=40, blank=True, default="")
    payment_status = models.CharField(max_length=16, blank=True, default="")
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    buying_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    reason = models.CharField(max_length=16, blank=True, default="")
    refund = models.CharField(max_length=8, blank=True, default="")
    refund_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_by_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "items_shelfstockmovement"
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


class CustomerPrepayment(models.Model):
    """Advance customer deposit (`shops_customerprepayment`) — till money in."""

    id = models.BigAutoField(primary_key=True)
    shop_id = models.BigIntegerField()
    client_id = models.BigIntegerField(null=True, blank=True)
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40)
    purpose = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=16, default="cash")
    cash_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mpesa_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    card_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_by_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "shops_customerprepayment"
        ordering = ["-created_at"]


class ClientCreditAccountEvent(models.Model):
    """Credit-account audit row (`shops_clientcreditaccountevent`).

    The ``payment_*`` kinds are collections against credit receipts — till
    money in on ``occurred_at``, separate from the sale receipts they settle.
    """

    id = models.BigAutoField(primary_key=True)
    client_id = models.BigIntegerField()
    shop_id = models.BigIntegerField(null=True, blank=True)
    receipt_id = models.BigIntegerField(null=True, blank=True)
    stk_payment_id = models.BigIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=32)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    detail = models.CharField(max_length=255, blank=True, default="")
    meta = models.JSONField(default=dict, blank=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "shops_clientcreditaccountevent"
        ordering = ["-occurred_at", "-pk"]
