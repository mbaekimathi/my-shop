"""Receipt printing for stock movements (in / out / trade / transfer)."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import (
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)
from items.models import (
    Item,
    ShelfItem,
    ShelfStockMovement,
    ShelfStockMovementType,
    StockMovement,
    StockMovementLine,
    StockMovementType,
    StockOutReason,
    StockRequestStatus,
    StoreStockMovement,
    StoreStockMovementType,
)
from shops.models import CompanyPosSettings, Shop, ShopPosSettings
from shops.services import (
    _invalidate_pos_settings_cache,
    build_shelf_movement_receipt,
    build_stock_in_supplier_receipt,
    build_stock_out_voucher,
    build_stock_request_delivery_note,
    build_store_movement_receipt,
)


def _make_profile(employee_id: str, role: str) -> EmployeeProfile:
    user = User.objects.create_user(
        username=employee_id, password="pass", is_active=True
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=employee_id,
        phone_country_code="+254",
        phone_number="700000001",
        status=EmployeeStatus.ACTIVE,
        role=role,
    )


def _make_shop(name: str, login_code: str, profile) -> Shop:
    return Shop.objects.create(
        name=name,
        location="NAIROBI",
        email=f"{login_code}@test.local",
        phone_number="0700000001",
        login_code=login_code,
        password_hash=make_password("pass"),
        created_by=profile,
    )


class MovementReceiptBuilderTests(TestCase):
    """The builders return the RichcomPrinter payload contract."""

    def setUp(self):
        self.profile = _make_profile("810001", EmployeeRole.SHOP_CASHIER)
        self.shop = _make_shop("ALPHA", "810101", self.profile)
        self.other_shop = _make_shop("BETA", "810102", self.profile)
        self.item = Item.objects.create(
            name="Sugar 1kg", category="Grocery", minimum_selling_price=Decimal("80.00"), shop_price=Decimal("100.00"), stock=Decimal("50"), created_by=self.profile
        )
        self.shelf_item = ShelfItem.objects.create(
            shop=self.shop,
            name="DISPLAY STAND",
            unit="PCS",
            quantity=Decimal("10"),
            created_by=self.profile,
        )

    def _movement(self, kind, **kwargs):
        return StockMovement.objects.create(
            movement_type=kind,
            shop=kwargs.pop("shop", self.shop),
            created_by=self.profile,
            **kwargs,
        )

    def test_stock_out_voucher(self):
        movement = self._movement(StockMovementType.OUT)
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            item_name=self.item.name,
            quantity=Decimal("3"),
            reason=StockOutReason.WASTE,
        )
        payload = build_stock_out_voucher(
            movement, shop=self.shop, authorised_by=self.profile
        )
        self.assertTrue(payload["receipt_text"])
        ticket = payload["receipt_ticket"]
        self.assertEqual(ticket["doc_type"], "stock_out")
        self.assertTrue(payload["receipt_number"].startswith("O"))
        self.assertIn("receipt_font", payload)
        self.assertIn("receipt_paper_width", payload)
        self.assertIn("print_via", payload)

    def test_stock_in_receipt(self):
        movement = self._movement(StockMovementType.IN)
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            item_name=self.item.name,
            quantity=Decimal("5"),
            buying_price=Decimal("80.00"),
        )
        payload = build_stock_in_supplier_receipt(
            movement, shop=self.shop, authorised_by=self.profile
        )
        self.assertTrue(payload["receipt_text"])
        self.assertTrue(payload["receipt_number"].startswith("I"))

    def test_transfer_dispatch_and_received(self):
        movement = self._movement(
            StockMovementType.REQUEST,
            requested_from_shop=self.other_shop,
            request_status=StockRequestStatus.PENDING,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            item_name=self.item.name,
            quantity=Decimal("2"),
        )
        dispatch = build_stock_request_delivery_note(
            movement,
            shop=self.shop,
            authorised_by=self.profile,
            variant="dispatch",
        )
        self.assertEqual(dispatch["receipt_ticket"]["doc_type"], "delivery")
        self.assertTrue(dispatch["receipt_number"].startswith("D"))
        received = build_stock_request_delivery_note(
            movement,
            shop=self.other_shop,
            authorised_by=self.profile,
            variant="received",
        )
        self.assertEqual(received["receipt_ticket"]["doc_type"], "goods_received")
        self.assertTrue(received["receipt_number"].startswith("G"))

    def test_shelf_movement_receipt_kinds(self):
        in_movement = ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("4"),
            balance_after=Decimal("14"),
            created_by=self.profile,
        )
        payload = build_shelf_movement_receipt(
            [in_movement], authorised_by=self.profile
        )
        self.assertTrue(payload["receipt_text"])
        self.assertEqual(payload["receipt_ticket"]["doc_type"], "in")

        out_movement = ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.OUT,
            quantity=Decimal("2"),
            balance_after=Decimal("12"),
            reason=StockOutReason.TRADE_OUT,
            created_by=self.profile,
        )
        payload = build_shelf_movement_receipt(
            [out_movement], authorised_by=self.profile
        )
        self.assertEqual(payload["receipt_ticket"]["doc_type"], "trade_out")

        transfer = ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.OUT,
            quantity=Decimal("1"),
            balance_after=Decimal("11"),
            reason=StockOutReason.TRANSFER,
            note="Sent to BETA",
            created_by=self.profile,
        )
        payload = build_shelf_movement_receipt(
            [transfer],
            authorised_by=self.profile,
            route_from="ALPHA",
            route_to="BETA",
        )
        ticket = payload["receipt_ticket"]
        self.assertEqual(ticket["doc_type"], "transfer_out")
        self.assertEqual(ticket["route_from"], "ALPHA")
        self.assertEqual(ticket["route_to"], "BETA")

    def test_store_movement_receipt(self):
        movements = [
            StoreStockMovement.objects.create(
                movement_type=StoreStockMovementType.IN,
                item=self.item,
                quantity=Decimal("6"),
                balance_after=Decimal("6"),
                created_by=self.profile,
                received_by=self.profile,
                supplier_name="MUMIAS SUGAR",
                buying_price=Decimal("75.00"),
            )
        ]
        payload = build_store_movement_receipt(
            movements, authorised_by=self.profile
        )
        self.assertTrue(payload["receipt_text"])
        self.assertTrue(payload["receipt_number"].startswith("R"))
        self.assertEqual(payload["receipt_ticket"]["doc_type"], "in")


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class MovementReceiptEndpointTests(TestCase):
    def setUp(self):
        self.password = "pass"
        self.manager = _make_profile("820001", EmployeeRole.COMPANY_STORE_MANAGER)
        self.cashier = _make_profile("820002", EmployeeRole.SHOP_CASHIER)
        self.shop = _make_shop("GAMMA", "820101", self.manager)
        self.item = Item.objects.create(
            name="Rice 2kg", category="Grocery", minimum_selling_price=Decimal("80.00"), shop_price=Decimal("100.00"), stock=Decimal("50"), created_by=self.manager
        )
        self.shelf_item = ShelfItem.objects.create(
            shop=self.shop,
            name="SHELF RICE",
            quantity=Decimal("8"),
            created_by=self.manager,
        )
        self.url = reverse("employees:stock_movement_receipt")

    def test_anonymous_denied(self):
        response = Client().get(f"{self.url}?kind=store&ids=1")
        # Portal middleware hides the route from anonymous sessions; either way
        # no receipt payload leaks.
        self.assertIn(response.status_code, (302, 403, 404))

    def test_bad_kind_rejected(self):
        client = Client()
        client.login(username="820001", password="pass")
        response = client.get(f"{self.url}?kind=bogus&ids=1")
        self.assertEqual(response.status_code, 400)

    def test_store_kind_returns_receipt(self):
        movement = StoreStockMovement.objects.create(
            movement_type=StoreStockMovementType.OUT,
            item=self.item,
            quantity=Decimal("2"),
            balance_after=Decimal("4"),
            created_by=self.manager,
            received_by=self.manager,
            reason=StockOutReason.WASTE,
        )
        client = Client()
        client.login(username="820001", password="pass")
        response = client.get(f"{self.url}?kind=store&ids={movement.pk}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["receipt_text"])
        self.assertEqual(payload["receipt_ticket"]["doc_type"], "out")

    def test_movement_kind_returns_dispatch_note(self):
        other = _make_shop("DELTA", "820102", self.manager)
        movement = StockMovement.objects.create(
            movement_type=StockMovementType.REQUEST,
            shop=self.shop,
            requested_from_shop=other,
            request_status=StockRequestStatus.PENDING,
            created_by=self.manager,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            item_name=self.item.name,
            quantity=Decimal("1"),
        )
        client = Client()
        client.login(username="820001", password="pass")
        response = client.get(f"{self.url}?kind=movement&ids={movement.pk}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["receipt_ticket"]["doc_type"], "delivery")


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class ShelfStockPrintRedirectTests(TestCase):
    """Shop-floor shelf posts redirect back with a ?print=shelf:<ids> marker."""

    def setUp(self):
        self.password = "shelf-pass"
        self.profile = _make_profile("830001", EmployeeRole.SHOP_CASHIER)
        self.profile.user.set_password(self.password)
        self.profile.user.save(update_fields=["password"])
        self.shop = _make_shop("ECHO", "830101", self.profile)
        self.shop.password_hash = make_password(self.password)
        self.shop.save(update_fields=["password_hash"])
        CompanyPosSettings.objects.update_or_create(
            pk=1, defaults={"enable_shelf_mode": True}
        )
        ShopPosSettings.objects.filter(shop=self.shop).delete()
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)
        self.url = reverse(
            "employees:my_shop_shelf_stock", kwargs={"shop_id": self.shop.pk}
        )

    def _login(self):
        client = Client()
        response = client.post(
            reverse("employees:shop_login"),
            {"login_code": self.shop.login_code, "password": self.password},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def test_stock_in_redirect_carries_print_marker(self):
        client = self._login()
        response = client.post(
            self.url,
            {
                "action": "register",
                "name": "PRINT TEST ITEM",
                "unit": "PCS",
                "opening_qty": "3",
                "login_code": self.profile.employee_id,
            },
        )
        self.assertEqual(response.status_code, 302)
        # Register alone records a REGISTER ledger row (not an in/out), so it
        # may not print — do a real stock-in on the new item.
        item = ShelfItem.objects.get(shop=self.shop, name="PRINT TEST ITEM")
        response = client.post(
            self.url,
            {
                "action": ShelfStockMovementType.IN,
                "shelf_item_id": str(item.pk),
                "qty": "2",
                "price": "7.50",
                "login_code": self.profile.employee_id,
            },
        )
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertIn("print=shelf:", location)
        movement = ShelfStockMovement.objects.filter(
            shelf_item=item, movement_type=ShelfStockMovementType.IN
        ).latest("pk")
        self.assertIn(str(movement.pk), location)

    def test_stock_out_redirect_carries_print_marker(self):
        client = self._login()
        item = ShelfItem.objects.create(
            shop=self.shop,
            name="PRINT OUT ITEM",
            quantity=Decimal("5"),
            created_by=self.profile,
        )
        response = client.post(
            self.url,
            {
                "action": ShelfStockMovementType.OUT,
                "shelf_item_id": str(item.pk),
                "qty": "1",
                "login_code": self.profile.employee_id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("print=shelf:", response["Location"])


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class MovementLedgerPrintButtonTests(TestCase):
    """The movement ledger timeline renders a per-row print button."""

    def setUp(self):
        self.manager = _make_profile("840001", EmployeeRole.COMPANY_STORE_MANAGER)
        self.shop = _make_shop("FOXTROT", "840101", self.manager)
        self.item = Item.objects.create(
            name="Beans 1kg",
            category="Grocery",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=Decimal("50"),
            created_by=self.manager,
        )
        self.shelf_item = ShelfItem.objects.create(
            shop=self.shop,
            name="LEDGER SHELF ITEM",
            quantity=Decimal("6"),
            created_by=self.manager,
        )
        CompanyPosSettings.objects.update_or_create(
            pk=1, defaults={"enable_shelf_mode": True}
        )
        ShopPosSettings.objects.filter(shop=self.shop).delete()
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)

    def test_shelf_ledger_rows_have_print_buttons(self):
        client = Client()
        client.login(username="840001", password="pass")
        ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.REGISTER,
            quantity=Decimal("6"),
            balance_after=Decimal("6"),
            created_by=self.manager,
        )
        in_row = ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("2"),
            balance_after=Decimal("8"),
            created_by=self.manager,
        )
        out_row = ShelfStockMovement.objects.create(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.OUT,
            quantity=Decimal("1"),
            balance_after=Decimal("7"),
            created_by=self.manager,
        )
        response = client.get(
            f"{reverse('employees:store_shelf_stock')}"
            f"?mode=movements&shop={self.shop.pk}&view_by=timeline"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-print-kind="shelf"', body)
        self.assertIn(f'data-print-ids="{in_row.pk}"', body)
        self.assertIn(f'data-print-ids="{out_row.pk}"', body)
        # Register rows are not printable movement receipts.
        register_row = ShelfStockMovement.objects.get(
            shelf_item=self.shelf_item,
            movement_type=ShelfStockMovementType.REGISTER,
        )
        self.assertNotIn(f'data-print-ids="{register_row.pk}"', body)

    def test_store_ledger_rows_have_print_buttons(self):
        client = Client()
        client.login(username="840001", password="pass")
        movement = StoreStockMovement.objects.create(
            movement_type=StoreStockMovementType.IN,
            item=self.item,
            quantity=Decimal("5"),
            balance_after=Decimal("5"),
            created_by=self.manager,
            received_by=self.manager,
        )
        response = client.get(
            f"{reverse('employees:store_stock_movement')}?view_by=timeline"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-print-kind="store"', body)
        self.assertIn(f'data-print-ids="{movement.pk}"', body)
