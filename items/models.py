import uuid

from django.db import models


def item_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
    # Unique name on every upload so replacements never reuse a cached path.
    stem = instance.pk or uuid.uuid4().hex
    return f"items/images/{stem}_{uuid.uuid4().hex[:10]}.{ext}"


def shelf_item_image_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
    stem = instance.pk or uuid.uuid4().hex
    return f"items/shelf/{stem}_{uuid.uuid4().hex[:10]}.{ext}"


class Item(models.Model):
    category = models.CharField(max_length=120, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)
    minimum_selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    shop_price = models.DecimalField(max_digits=12, decimal_places=2)
    use_individual_shop_prices = models.BooleanField(default=False, db_index=True)
    avg_buy_qty = models.PositiveIntegerField(
        default=1,
        help_text="Typical quantity a customer buys of this item.",
    )
    discount_min_qty = models.PositiveIntegerField(
        default=0,
        help_text="Buy this many or more to unlock the volume discount. 0 disables it.",
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="KSh off the list price per unit when the volume quantity is met.",
    )
    stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stock_count_enabled = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "Whether POS sales/returns move this item's central store count "
            "(StoreStock). Kept in sync with deduct_stock_on_sale — the "
            "item-stock page and Sale stock tracking page drive one switch."
        ),
    )
    deduct_stock_on_sale = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "Whether selling/returning this item on the shop page moves its "
            "stock counts. Kept in sync with stock_count_enabled — off on "
            "either page means the item sells freely and its stock is manual only."
        ),
    )
    sale_tracking_custom = models.BooleanField(
        default=False,
        help_text=(
            "True once this item's sale stock tracking was picked by hand on "
            "the Sale stock tracking page. False = it auto-follows the company "
            "default (CompanyPosSettings.enable_stock_tracking)."
        ),
    )
    low_stock_notify = models.BooleanField(default=False, db_index=True)
    low_stock_threshold = models.PositiveIntegerField(
        default=0,
        help_text="Alert when total stock across shops is at or below this quantity.",
    )
    image = models.ImageField(upload_to=item_image_path, blank=True, null=True)
    track_serial_number = models.BooleanField(default=False, db_index=True)
    is_combo = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Combo items bundle other registered items. The sum of each "
            "component's price × its portions is the combo's minimum "
            "price — shops sell it at their own selling price on top, and "
            "selling one tallies each component item by those portions "
            "instead of the combo."
        ),
    )
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

    def public_image_url(self) -> str:
        """Catalog URL when the file exists; empty when the DB path is broken."""
        field = self.image
        if not field:
            return ""
        try:
            name = (getattr(field, "name", None) or "").strip()
            if not name:
                return ""
            storage = getattr(field, "storage", None)
            if storage is not None and not storage.exists(name):
                return ""
            if not self.pk:
                return field.url or ""
            from django.urls import reverse
            from urllib.parse import quote

            url = reverse("core:item_photo", kwargs={"item_id": self.pk})
            # Bust browser/CDN cache when the stored file is replaced.
            token = name.rsplit("/", 1)[-1]
            return f"{url}?v={quote(token, safe='')}"
        except (ValueError, OSError, AttributeError):
            return ""

    def resolve_list_price(self, shop_price_override=None):
        """Selling price for catalog/POS.

        An existing per-shop row always wins — including 0.00, which means the
        item is deliberately free at that shop. With no row, the global
        shop_price applies when positive, else the minimum selling price.
        """
        if self.use_individual_shop_prices:
            if shop_price_override is not None:
                return shop_price_override
            if self.shop_price is not None and self.shop_price > 0:
                return self.shop_price
            return self.minimum_selling_price
        if self.shop_price is not None and self.shop_price > 0:
            return self.shop_price
        return self.minimum_selling_price

    def price_for_shop(self, shop):
        """Resolve selling price for a shop (per-shop row → shop_price → min)."""
        shop_id = getattr(shop, "pk", shop)
        row = self.shop_prices.filter(shop_id=shop_id).first()
        if row is not None:
            return self.resolve_list_price(row.price)
        return self.resolve_list_price(None)

    def volume_unit_price(self, list_price, qty):
        """List price after volume discount when qty meets the threshold."""
        from decimal import Decimal

        list_price = list_price if list_price is not None else Decimal("0")
        try:
            qty = int(qty or 0)
        except (TypeError, ValueError):
            qty = 0
        amount = self.discount_amount or Decimal("0")
        threshold = int(self.discount_min_qty or 0)
        if threshold <= 0 or amount <= 0 or qty < threshold:
            return list_price
        discounted = list_price - amount
        floor = self.minimum_selling_price or Decimal("0")
        if discounted < floor:
            return floor
        return discounted


class ItemComboComponent(models.Model):
    """One leg of a combo item.

    ``qty`` is how many units of the component item one combo uses: the
    sum of each component's price × qty is the combo's minimum price, and
    a combo sale tallies each component item's stock and sales by qty ×
    the combo quantity (1 → one unit, 0.5 → half a unit, 2 → two units).
    """

    combo = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="combo_components",
    )
    component = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="combo_memberships",
    )
    qty = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        help_text="Units of this component in one combo.",
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["combo", "component"],
                name="uniq_combo_component",
            )
        ]

    def __str__(self):
        return f"{self.component} × {self.qty} in {self.combo}"


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
    is_sold = models.BooleanField(
        default=True,
        help_text="When off, this item is not listed or sellable at this shop.",
    )
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


class ItemActivityKind(models.TextChoices):
    REGISTERED = "registered", "Registered"
    EDITED = "edited", "Edited"
    DISCOUNT_UPDATED = "discount_updated", "Discount updated"
    SUSPENDED = "suspended", "Suspended"
    UNSUSPENDED = "unsuspended", "Unsuspended"
    DELETED = "deleted", "Deleted"


class ItemActivityEvent(models.Model):
    """Audit trail for item-management register / edit / suspend / delete actions."""

    item = models.ForeignKey(
        Item,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_events",
    )
    item_name = models.CharField(max_length=200, blank=True, default="")
    item_category = models.CharField(max_length=120, blank=True, default="")
    kind = models.CharField(
        max_length=32,
        choices=ItemActivityKind.choices,
        db_index=True,
    )
    detail = models.CharField(max_length=255, blank=True, default="")
    meta = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="item_activity_events",
    )
    shops = models.ManyToManyField(
        "shops.Shop",
        blank=True,
        related_name="item_activity_events",
    )
    occurred_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=["kind", "-occurred_at"],
                name="items_itema_kind_occurred_idx",
            ),
            models.Index(
                fields=["actor", "-occurred_at"],
                name="items_itema_actor_occurred_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.item_name or self.item_id}"


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
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    low_stock_threshold = models.PositiveIntegerField(
        default=0,
        help_text="Alert when this shop's on-hand quantity is at or below this value.",
    )
    low_stock_manual = models.BooleanField(
        default=False,
        help_text="True when Alert at was typed for this shop. Otherwise the weekly average is used.",
    )
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
    status_reason = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional note when a serial status is set or corrected manually.",
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
    REQUEST = "request", "Transfer Stock"


class StockEntrySource(models.TextChoices):
    """Where the movement was submitted from (set by the view, not the user)."""

    BUY_ITEMS = "buy_items", "Buy items"
    STOCK_MANAGEMENT = "stock_management", "Stock management"
    CUSTOMER_RETURN = "customer_return", "Customer return"
    TRADE_OUT = "trade_out", "Trade out"
    TRADE_IN = "trade_in", "Trade in"
    TRADE_EXCHANGE = "trade_exchange", "Trade exchange"


class StockPaymentStatus(models.TextChoices):
    UNPAID = "unpaid", "Unpaid"
    PAID = "paid", "Paid"
    PARTIAL = "partial", "Partial"


class StockOutReason(models.TextChoices):
    WASTE = "waste", "Waste"
    TRANSFER = "transfer", "Transfer"
    DISPLAY = "display", "Display"
    RETURN = "return", "Supplier return"
    TRADE_OUT = "trade_out", "Trade out"
    TRADE_IN = "trade_in", "Trade in"
    GIFT = "gift", "Gift"
    CUSTOM = "custom", "Custom"


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
        help_text="Shop performing stock in/out, or the shop sending a transfer.",
    )
    requested_from_shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.PROTECT,
        related_name="stock_requests_received",
        null=True,
        blank=True,
        help_text="For transfers: the shop receiving the stock (notified to confirm).",
    )
    request_status = models.CharField(
        max_length=16,
        choices=StockRequestStatus.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Used for inter-shop transfers only.",
    )
    requester_notified = models.BooleanField(
        default=True,
        help_text="False after a decision until the sending shop acknowledges it.",
    )
    supplier_notified = models.BooleanField(
        default=True,
        help_text="False after a new transfer until the receiving shop acknowledges it.",
    )
    responded_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_request_responses",
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    response_note = models.TextField(
        blank=True,
        default="",
        help_text="Reason when a pending transfer is declined or cancelled.",
    )
    payment_status = models.CharField(
        max_length=16,
        choices=StockPaymentStatus.choices,
        blank=True,
        default="",
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    rider_name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Optional rider / courier name on inter-shop transfers.",
    )
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
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movement_lines",
    )
    item_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Snapshot of the item name; kept when the item is deleted.",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
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

    def save(self, *args, **kwargs):
        if self.item_id and not self.item_name:
            self.item_name = self.item.name
        super().save(*args, **kwargs)

    def __str__(self):
        name = self.item.name if self.item else (self.item_name or "Deleted item")
        return f"{name} × {self.quantity}"


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


class StockTakeScope(models.TextChoices):
    CATEGORY = "category", "By category"
    ITEMS = "items", "By item"


class StockTakeStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class StockTakeAssignmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUBMITTED = "submitted", "Submitted"


class StockTake(models.Model):
    """Company-led stock count request sent to one or more shops."""

    scope_mode = models.CharField(
        max_length=16,
        choices=StockTakeScope.choices,
        default=StockTakeScope.CATEGORY,
        db_index=True,
    )
    categories = models.JSONField(default=list, blank=True)
    item_ids = models.JSONField(default=list, blank=True)
    deadline = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=16,
        choices=StockTakeStatus.choices,
        default=StockTakeStatus.OPEN,
        db_index=True,
    )
    note = models.CharField(max_length=240, blank=True, default="")
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_takes_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_takes_closed",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Stock take #{self.pk} ({self.status})"


class StockTakeAssignment(models.Model):
    stock_take = models.ForeignKey(
        StockTake,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="stock_take_assignments",
    )
    status = models.CharField(
        max_length=16,
        choices=StockTakeAssignmentStatus.choices,
        default=StockTakeAssignmentStatus.PENDING,
        db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by_label = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        ordering = ["shop__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["stock_take", "shop"],
                name="uniq_stock_take_shop",
            )
        ]

    def __str__(self):
        return f"Stock take #{self.stock_take_id} → {self.shop}"


class StockTakeCount(models.Model):
    assignment = models.ForeignKey(
        StockTakeAssignment,
        on_delete=models.CASCADE,
        related_name="counts",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="stock_take_counts",
    )
    system_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    counted_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["item__category", "item__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "item"],
                name="uniq_stock_take_assignment_item",
            )
        ]

    def __str__(self):
        return f"{self.item_id} @ assignment {self.assignment_id}"


class OnlineStockTakeEntry(models.Model):
    """HQ live physical count for one shop item on one day (not company Stock Take)."""

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="online_stock_take_entries",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="online_stock_take_entries",
    )
    # Local calendar day this count belongs to — supports daily stock takes.
    count_date = models.DateField(db_index=True)
    physical_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    # Book qty frozen when physical_qty was last saved. Variance uses this
    # snapshot so later sales/receipts do not move the variance.
    system_qty_at_count = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    updated_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="online_stock_take_entries",
    )
    updated_by_label = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        ordering = ["-count_date", "item__category", "item__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "item", "count_date"],
                name="uniq_online_stock_take_shop_item_date",
            )
        ]

    def __str__(self):
        return f"Online count {self.item_id} @ shop {self.shop_id} on {self.count_date}"


class ShelfOnlineStockTakeEntry(models.Model):
    """HQ live physical shelf count for one shop shelf item on one day."""

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="shelf_online_stock_take_entries",
    )
    shelf_item = models.ForeignKey(
        "ShelfItem",
        on_delete=models.CASCADE,
        related_name="online_stock_take_entries",
    )
    count_date = models.DateField(db_index=True)
    physical_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    system_qty_at_count = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    updated_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shelf_online_stock_take_entries",
    )
    updated_by_label = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        ordering = ["-count_date", "shelf_item__category", "shelf_item__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "shelf_item", "count_date"],
                name="uniq_shelf_online_take_shop_item_date",
            )
        ]

    def __str__(self):
        return (
            f"Shelf online count {self.shelf_item_id} @ shop {self.shop_id} "
            f"on {self.count_date}"
        )


class ShelfStockTake(models.Model):
    """Physical count of one shop's shelf items (system vs counted compare)."""

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="shelf_stock_takes",
    )
    status = models.CharField(
        max_length=16,
        choices=StockTakeStatus.choices,
        default=StockTakeStatus.OPEN,
        db_index=True,
    )
    note = models.CharField(max_length=240, blank=True, default="")
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shelf_stock_takes_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shelf_stock_takes_closed",
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shelf_stock_takes_applied",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Shelf stock take #{self.pk} ({self.shop_id}) ({self.status})"


class ShelfStockTakeEntry(models.Model):
    take = models.ForeignKey(
        ShelfStockTake,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    shelf_item = models.ForeignKey(
        "ShelfItem",
        on_delete=models.CASCADE,
        related_name="stock_take_entries",
    )
    system_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    counted_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["shelf_item__category", "shelf_item__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["take", "shelf_item"],
                name="uniq_shelf_take_item",
            )
        ]

    def __str__(self):
        return f"{self.shelf_item_id} @ shelf take {self.take_id}"


class StoreStockMovementType(models.TextChoices):
    IN = "in", "Store stock in"
    OUT = "out", "Store stock out"


class StoreStock(models.Model):
    """Central store qty for an item — independent of ShopStock / Item.stock."""

    item = models.OneToOneField(
        Item,
        on_delete=models.CASCADE,
        related_name="store_stock",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item__category", "item__name"]

    def __str__(self):
        return f"Store stock {self.item_id}: {self.quantity}"


class StoreStockMovement(models.Model):
    """Ledger for store stock in/out. Never mutates shop floor stock."""

    movement_type = models.CharField(
        max_length=8,
        choices=StoreStockMovementType.choices,
        db_index=True,
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        related_name="store_stock_movements",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    balance_after = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        related_name="store_stock_movements_created",
    )
    received_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        related_name="store_stock_movements_received",
    )
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone = models.CharField(max_length=40, blank=True, default="")
    payment_status = models.CharField(
        max_length=16,
        choices=StockPaymentStatus.choices,
        blank=True,
        default="",
    )
    buying_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Unit buying price recorded on stock in.",
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
    source = models.CharField(
        max_length=16,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "What created this movement. 'pos' rows mirror shop-floor "
            "receipts; blank rows come from the store stock pages."
        ),
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Store {self.movement_type} {self.quantity} of item {self.item_id}"


class ShelfStockMovementType(models.TextChoices):
    REGISTER = "register", "Registered"
    IN = "in", "Shelf stock in"
    OUT = "out", "Shelf stock out"


class ShelfItem(models.Model):
    """Shelf-mode stock line for one shop. Not an Item: never sold at the POS."""

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="shelf_items",
    )
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=120, blank=True, default="")
    unit = models.CharField(max_length=40, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    low_stock_threshold = models.DecimalField(
        max_digits=12, decimal_places=3, default=0
    )
    portions_per_unit = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text=(
            "Total portions each shelf unit gives on a trade out — the "
            "sum of the linked portion targets' portions. 0 or no "
            "targets = trade out only moves the shelf."
        ),
    )
    image = models.ImageField(
        upload_to=shelf_item_image_path, blank=True, null=True
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    is_suspended = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shelf_items_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "name"],
                name="uniq_shop_shelf_item_name",
            )
        ]

    def __str__(self):
        return f"Shelf {self.name} @ {self.shop_id}: {self.quantity}"

    def public_image_url(self) -> str:
        """Catalog URL when the file exists; empty when the DB path is broken."""
        field = self.image
        if not field:
            return ""
        try:
            name = (getattr(field, "name", None) or "").strip()
            if not name:
                return ""
            storage = getattr(field, "storage", None)
            if storage is not None and not storage.exists(name):
                return ""
            if not self.pk:
                return field.url or ""
            from django.urls import reverse
            from urllib.parse import quote

            url = reverse("core:shelf_photo", kwargs={"shelf_item_id": self.pk})
            token = name.rsplit("/", 1)[-1]
            return f"{url}?v={quote(token, safe='')}"
        except (ValueError, OSError, AttributeError):
            return ""


class ShelfItemPortionTarget(models.Model):
    """One catalog item a shelf trade out stocks portions into.

    ``portions`` is how many of this item each shelf unit gives on a
    trade out — every leg is valued at the item's list price.
    """

    shelf_item = models.ForeignKey(
        ShelfItem,
        on_delete=models.CASCADE,
        related_name="portion_targets",
    )
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="shelf_portion_targets",
    )
    portions = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text="Portions of this item each shelf unit gives.",
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["shelf_item", "item"],
                name="uniq_shelf_portion_target",
            )
        ]

    def __str__(self):
        return f"{self.item} × {self.portions} of {self.shelf_item_id}"


class ShelfStockMovement(models.Model):
    """Ledger for shelf stock. Never touches Item / ShopStock quantities."""

    shelf_item = models.ForeignKey(
        ShelfItem,
        on_delete=models.CASCADE,
        related_name="movements",
    )
    movement_type = models.CharField(
        max_length=10,
        choices=ShelfStockMovementType.choices,
        db_index=True,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    balance_after = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    supplier_name = models.CharField(max_length=160, blank=True, default="")
    supplier_phone = models.CharField(max_length=40, blank=True, default="")
    payment_status = models.CharField(
        max_length=16,
        choices=StockPaymentStatus.choices,
        blank=True,
        default="",
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    buying_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Unit buying price recorded on stock in.",
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
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shelf_stock_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Shelf {self.movement_type} {self.quantity} of {self.shelf_item_id}"
