"""Portal stock-requests page exposes a read-only transfer detail view."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class PortalStockRequestViewTests(TestCase):
    def setUp(self):
        self.password = "portal-view-pass"
        self.user = User.objects.create_user(
            username="800022",
            password=self.password,
            email="portal-view@test.local",
            first_name="PORTAL",
            last_name="VIEWER",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="800022",
            phone_country_code="+254",
            phone_number="700000822",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="PORTAL VIEW SHOP",
            location="NAIROBI",
            email="portal-view-shop@test.local",
            phone_number="0700000822",
            login_code="800222",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.other_shop = Shop.objects.create(
            name="PORTAL VIEW SHOP B",
            location="MOMBASA",
            email="portal-view-shop-b@test.local",
            phone_number="0700000823",
            login_code="800223",
            password_hash=make_password(self.password),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)

        from items.models import (
            Item,
            StockMovement,
            StockMovementLine,
            StockMovementType,
            StockRequestStatus,
        )

        self.tracked_item = Item.objects.create(
            category="BULK",
            name="VIEW TRACKED ITEM",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            track_serial_number=True,
            created_by=self.profile,
        )
        self.plain_item = Item.objects.create(
            category="BULK",
            name="VIEW PLAIN ITEM",
            minimum_selling_price=Decimal("10.00"),
            shop_price=Decimal("20.00"),
            created_by=self.profile,
        )
        self.movement = StockMovement.objects.create(
            movement_type=StockMovementType.REQUEST,
            shop=self.other_shop,
            requested_from_shop=self.shop,
            request_status=StockRequestStatus.FULFILLED,
            created_by=self.profile,
            responded_by=self.profile,
            responded_at=timezone.now(),
        )
        StockMovementLine.objects.create(
            movement=self.movement,
            item=self.tracked_item,
            quantity=Decimal("2"),
            serial_numbers=["SN-001", "SN-002"],
        )
        StockMovementLine.objects.create(
            movement=self.movement,
            item=self.plain_item,
            quantity=Decimal("1"),
        )

        # Pending incoming transfer with a serial-tracked line.
        from items.models import ItemSerial, ShopStock

        self.pending = StockMovement.objects.create(
            movement_type=StockMovementType.REQUEST,
            shop=self.other_shop,
            requested_from_shop=self.shop,
            request_status=StockRequestStatus.PENDING,
            created_by=self.profile,
        )
        self.pending_line = StockMovementLine.objects.create(
            movement=self.pending,
            item=self.tracked_item,
            quantity=Decimal("2"),
            serial_numbers=["SN-901", "SN-902"],
        )
        self.pending_plain_line = StockMovementLine.objects.create(
            movement=self.pending,
            item=self.plain_item,
            quantity=Decimal("3"),
        )
        ShopStock.objects.create(
            shop=self.other_shop, item=self.tracked_item, quantity=Decimal("5")
        )
        ShopStock.objects.create(
            shop=self.other_shop, item=self.plain_item, quantity=Decimal("9")
        )
        for serial in ("SN-901", "SN-902"):
            ItemSerial.objects.create(
                item=self.tracked_item,
                shop=self.other_shop,
                serial_number=serial,
                is_available=True,
            )

    def _login(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_previous_transfers_render_view_eye_with_serial_data(self):
        client = self._login()
        response = client.get(
            reverse(
                "employees:my_shop_stock_requests",
                kwargs={"shop_id": self.shop.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-stock-request-view', body)
        self.assertIn('data-stock-request-view-modal', body)
        self.assertIn('data-stock-request-serials-modal', body)
        self.assertIn(f'data-request-id="{self.movement.pk}"', body)
        self.assertIn('data-request-status="fulfilled"', body)
        self.assertIn('data-item-tracked="1"', body)
        self.assertIn('<span data-serial>SN-001</span>', body)
        self.assertIn('<span data-serial>SN-002</span>', body)

    def test_pending_transfer_renders_serial_entry_blocks(self):
        client = self._login()
        response = client.get(
            reverse(
                "employees:my_shop_stock_requests",
                kwargs={"shop_id": self.shop.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-serial-panel', body)
        self.assertIn('data-serial-mode="transfer"', body)
        self.assertIn(f'data-line-id="{self.pending_line.pk}"', body)
        self.assertIn('<li data-serial-allowed>SN-901</li>', body)
        self.assertIn('<li data-serial-allowed>SN-902</li>', body)
        self.assertIn('data-qty-from-serial="1"', body)

    def test_accept_with_matching_serials_moves_them_to_receiver(self):
        from items.models import ItemSerial, StockRequestStatus

        client = self._login()
        response = client.post(
            reverse(
                "employees:my_shop_stock_request_respond",
                kwargs={
                    "shop_id": self.shop.pk,
                    "request_id": self.pending.pk,
                },
            ),
            {
                "decision": "accept",
                "login_code": self.profile.employee_id,
                "ajax": "1",
                f"qty_{self.pending_line.pk}": "2",
                f"qty_{self.pending_plain_line.pk}": "3",
                f"serials_{self.pending_line.pk}": ["SN-901", "SN-902"],
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"), data)

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.request_status, StockRequestStatus.FULFILLED)
        serials = ItemSerial.objects.filter(item=self.tracked_item)
        self.assertEqual(serials.count(), 2)
        for serial in serials:
            self.assertEqual(serial.shop_id, self.shop.pk)
            self.assertTrue(serial.is_available)
