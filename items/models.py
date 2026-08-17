import uuid

from django.db import models


def item_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"items/images/{instance.pk or uuid.uuid4().hex}.{ext}"


class Item(models.Model):
    category = models.CharField(max_length=120, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)
    minimum_selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    shop_price = models.DecimalField(max_digits=12, decimal_places=2)
    use_individual_shop_prices = models.BooleanField(default=False, db_index=True)
    stock = models.PositiveIntegerField(default=0)
    low_stock_notify = models.BooleanField(default=False, db_index=True)
    low_stock_threshold = models.PositiveIntegerField(
        default=0,
        help_text="Alert when total stock across shops is at or below this quantity.",
    )
    image = models.ImageField(upload_to=item_image_path, blank=True, null=True)
    track_serial_number = models.BooleanField(default=False, db_index=True)
    is_suspended = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.category})"

    def resolve_list_price(self, shop_price_override=None):
        """Selling price for catalog/POS.

        Uses the per-shop override when individual pricing is on and the
        override is positive; otherwise the global shop_price when positive.
        Missing or zero prices fall back to the minimum selling price so
        shops never surface a 0 list price.
        """
        if self.use_individual_shop_prices:
            if shop_price_override is not None and shop_price_override > 0:
                return shop_price_override
            if self.shop_price is not None and self.shop_price > 0:
                return self.shop_price
            return self.minimum_selling_price
        if self.shop_price is not None and self.shop_price > 0:
            return self.shop_price
        return self.minimum_selling_price

    def price_for_shop(self, shop):
        """Resolve selling price for a shop (override → shop_price → min)."""
        override = None
        if self.use_individual_shop_prices:
            row = self.shop_prices.filter(shop_id=getattr(shop, "pk", shop)).first()
            if row is not None:
                override = row.price
        return self.resolve_list_price(override)


class ShopItemPrice(models.Model):
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="item_prices",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="shop_prices",
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop__name", "item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "item"],
                name="uniq_shop_item_price",
            )
        ]

    def __str__(self):
        return f"{self.item.name} @ {self.shop.name}: {self.price}"


class ShopStock(models.Model):
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="item_stocks",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="shop_stocks",
    )
    quantity = models.PositiveIntegerField(default=0)
    average_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Weighted average unit cost for this shop's on-hand stock.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["shop__name", "item__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "item"],
                name="uniq_shop_item_stock",
            )
        ]

    def __str__(self):
        return f"{self.item.name} @ {self.shop.name}: {self.quantity}"


class ItemSerialStatus(models.TextChoices):
    IN_STOCK = "in_stock", "In stock"
    SOLD = "sold", "Sold"
    RETURNED = "returned", "Returned"
    OUT = "out", "Stocked out"


class ItemSerial(models.Model):
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="serials",
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="item_serials",
    )
    serial_number = models.CharField(max_length=120, db_index=True)
    is_available = models.BooleanField(default=True, db_index=True)
    status_override = models.CharField(
        max_length=16,
        blank=True,
        default="",
        db_index=True,
        choices=ItemSerialStatus.choices,
        help_text="When set, serial pages use this status instead of inferring it from sales and stock.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["serial_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "serial_number"],
                name="uniq_item_serial_number",
            )
        ]

    def __str__(self):
        state = "in stock" if self.is_available else "out"
        return f"{self.serial_number} ({self.item.name}, {state})"


class StockMovementType(models.TextChoices):
    IN = "in", "Stock In"
    OUT = "out", "Stock Out"
    REQUEST = "request", "Request Stock"


class StockEntrySource(models.TextChoices):
    """Where the movement was submitted from (set by the view, not the user)."""

    BUY_ITEMS = "buy_items", "Buy items"
    STOCK_MANAGEMENT = "stock_management", "Stock management"


class StockPaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    PARTIAL = "partial", "Partial"


class StockOutReason(models.TextChoices):
    WASTE = "waste", "Waste"
    TRANSFER = "transfer", "Transfer"
    DISPLAY = "display", "Display"
    RETURN = "return", "Supplier return"


class StockRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    FULFILLED = "fulfilled", "Fulfilled"
    DECLINED = "declined", "Declined"


class StockMovement(models.Model):
    movement_type = models.CharField(
        max_length=16,
        choices=StockMovementType.choices,
        db_index=True,
    )
    entry_source = models.CharField(
        max_length=32,
        choices=StockEntrySource.choices,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "UI entry point that created this movement. Blank for legacy rows "
            "created before this field existed."
        ),
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.PROTECT,
        related_name="stock_movements",
        null=True,
        blank=True,
        help_text="Shop performing stock in/out, or the shop requesting stock.",
    )
    requested_from_shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.PROTECT,
        related_name="stock_requests_received",
        null=True,
        blank=True,
        help_text="For requests: the shop being asked to supply stock.",
    )
    request_status = models.CharField(
        max_length=16,
        choices=StockRequestStatus.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Used for stock requests only.",
    )
    requester_notified = models.BooleanField(
        default=True,
        help_text="False after a decision until the requesting shop acknowledges it.",
    )
    supplier_notified = models.BooleanField(
        default=True,
        help_text="False after a new request until the supplying shop acknowledges it.",
    )
    responded_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_request_responses",
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    payment_status = models.CharField(
        max_length=16,
        choices=StockPaymentStatus.choices,
        blank=True,
        default="",
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["shop", "-created_at"],
                name="items_stockmv_shop_created_idx",
            ),
            models.Index(
                fields=["movement_type", "-created_at"],
                name="items_stockmv_type_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_movement_type_display()} #{self.pk}"


class StockMovementLine(models.Model):
    movement = models.ForeignKey(
        StockMovement,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="stock_movement_lines",
    )
    quantity = models.PositiveIntegerField()
    buying_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        db_index=True,
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Cost per unit removed on stock-out (shop weighted average at the time).",
    )
    payment_status = models.CharField(
        max_length=16,
        choices=StockPaymentStatus.choices,
        blank=True,
        default="",
    )
    reason = models.CharField(
        max_length=16,
        choices=StockOutReason.choices,
        blank=True,
        default="",
    )
    refund = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text="yes or no for stock-out refund",
    )
    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    note = models.TextField(blank=True)
    serial_numbers = models.JSONField(default=list, blank=True)
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone_country_code = models.CharField(max_length=8, blank=True, default="")
    supplier_phone_number = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.item.name} × {self.quantity}"


class Supplier(models.Model):
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
                name="uniq_supplier_phone",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.phone_country_code} {self.phone_number})"
