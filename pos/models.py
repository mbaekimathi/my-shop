import uuid

from django.db import models


class Product(models.Model):
    sku = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.sku} — {self.name}"


class SaleSource(models.TextChoices):
    ONLINE = "online", "Online"
    OFFLINE = "offline", "Offline sync"


class Sale(models.Model):
    client_id = models.CharField(max_length=36, unique=True, db_index=True)
    employee = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.PROTECT,
        related_name="sales",
    )
    total = models.DecimalField(max_digits=12, decimal_places=2)
    source = models.CharField(
        max_length=16,
        choices=SaleSource.choices,
        default=SaleSource.ONLINE,
        db_index=True,
    )
    sold_at = models.DateTimeField()
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sold_at"]


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_lines",
    )
    product_sku = models.CharField(max_length=32)
    product_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]


def new_client_id() -> str:
    return str(uuid.uuid4())
