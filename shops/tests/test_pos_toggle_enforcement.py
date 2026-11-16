"""POS/receipt settings toggles: each switch must work when on and block when off.

Covers the toggles rendered on:
- /<role>/settings/company-pos/      (POS_SETTING_GROUPS)
- /<role>/settings/company-receipt/  (RECEIPT_SETTING_GROUPS)
- /my-shop/<id>/settings/pos/        (SHOP_POS_SETTING_GROUPS + override_pos)
- /my-shop/<id>/settings/receipt/    (SHOP_RECEIPT_SETTING_GROUPS + override_receipt)
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import (
    Shop,
    ShopDelivery,
    ShopPaymentMethod,
    ShopPosSettings,
    ShopReceiptKind,
)
from shops.services import (
    _invalidate_pos_settings_cache,
    build_receipt_print_copies,
    company_pos_disabled_permission_keys,
    complete_shop_checkout,
    enabled_receipt_print_roles,
    get_company_pos_settings,
    get_effective_pos_settings,
    open_shop_day,
    pos_settings_as_dict,
    receipt_accepted_payment_methods_label,
    set_company_delivery_amount,
    set_company_pos_setting,
    set_company_tax_percent,
    set_shop_delivery_amount,
    set_shop_pos_override,
    set_shop_pos_setting,
    stock_mode_of,
)

FIELDS = (
    "enable_sale",
    "enable_credit",
    "enable_quotation",
    "enable_trade_out",
    "enable_trade_in",
    "enable_cash",
    "enable_mpesa",
    "enable_cash_mpesa",
    "enable_discount",
    "enable_open_close",
    "enable_client_data",
    "require_client_phone",
    "require_client_name",
    "enable_cart_confirm_receipt",
    "enable_cart_delivery",
    "enable_tax",
    "enable_sidebar_open_close",
    "enable_sidebar_trade",
    "enable_sidebar_buy_stock",
    "enable_sidebar_stock_transfers",
    "enable_sidebar_stock_count",
    "enable_sidebar_shelf_stock",
    "enable_sidebar_expense",
    "enable_sidebar_printer",
    "enable_sidebar_receipts",
    "enable_sidebar_website",
    "compulsory_print_on_sale",
    "enable_print_bluetooth",
    "enable_print_usb",
    "enable_print_wifi",
    "print_customer_receipt",
    "print_staff_receipt",
    "print_cashier_receipt",
    "enable_receipt_payment_methods",
    "show_client_phone_on_receipt",
    "require_mpesa_reference_on_confirm",
)

DEFAULTS = {
    "enable_sale": True,
    "enable_credit": True,
    "enable_quotation": True,
    "enable_trade_out": True,
    "enable_trade_in": True,
    "enable_cash": True,
    "enable_mpesa": True,
    "enable_cash_mpesa": True,
    "enable_discount": True,
    "enable_stock_tracking": True,
    "enable_shelf_mode": False,
    "enable_open_close": True,
    "enable_client_data": True,
    "require_client_phone": False,
    "require_client_name": False,
    "enable_cart_confirm_receipt": True,
    "enable_cart_delivery": True,
    "enable_tax": False,
    "enable_sidebar_open_close": True,
    "enable_sidebar_trade": True,
    "enable_sidebar_buy_stock": True,
    "enable_sidebar_stock_transfers": True,
    "enable_sidebar_stock_count": True,
    "enable_sidebar_shelf_stock": True,
    "enable_sidebar_expense": True,
    "enable_sidebar_printer": True,
    "enable_sidebar_receipts": True,
    "enable_sidebar_website": True,
    "compulsory_print_on_sale": False,
    "enable_print_bluetooth": True,
    "enable_print_usb": True,
    "enable_print_wifi": True,
    "print_customer_receipt": True,
    "print_staff_receipt": False,
    "print_cashier_receipt": False,
    "enable_receipt_payment_methods": True,
    "show_client_phone_on_receipt": True,
    "require_mpesa_reference_on_confirm": False,
}


class PosToggleEnforcementTests(TestCase):
    """Flip every company toggle both ways through complete_shop_checkout."""

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="200777",
            password="pass",
            email="toggles@test.local",
            first_name="Toggle",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="200777",
            phone_country_code="+254",
            phone_number="700000777",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Toggle Shop",
            location="Nairobi",
            email="toggle-shop@test.local",
            phone_number="0700000777",
            login_code="200777",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Toggle Item",
            category="Parts",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )
        self.stock = ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=10,
            average_cost=Decimal("40.00"),
        )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "200777",
            },
        )

    def tearDown(self):
        row = get_company_pos_settings()
        changed = []
        for field, value in DEFAULTS.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(field)
        if row.tax_percent != Decimal("0.00"):
            row.tax_percent = Decimal("0.00")
            changed.append("tax_percent")
        if row.delivery_amount != Decimal("0.00"):
            row.delivery_amount = Decimal("0.00")
            changed.append("delivery_amount")
        if changed:
            row.save(update_fields=[*changed, "updated_at"])
        _invalidate_pos_settings_cache()

    # -- helpers ----------------------------------------------------------

    def _checkout(self, **extra):
        payload = {
            "kind": ShopReceiptKind.SALE,
            "payment_method": ShopPaymentMethod.CASH,
            "login_code": "200777",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }
        payload.update(extra)
        return complete_shop_checkout(
            shop=self.shop, profile=self.profile, payload=payload
        )

    def _blocked(self, needle="", **extra):
        with self.assertRaises(ValidationError) as ctx:
            self._checkout(**extra)
        if needle:
            self.assertIn(needle, str(ctx.exception))
        return ctx.exception

    def _trade_payload(self, kind, **extra):
        payload = {
            "kind": kind,
            "client_name": "TRADE CLIENT",
            "client_phone": "0712345678",
            "login_code": "200777",
            "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
        }
        payload.update(extra)
        return payload

    # -- company POS page: transaction kinds --------------------------------

    def test_every_toggle_field_is_known_and_persists(self):
        for field in FIELDS:
            with self.subTest(field=field):
                row = set_company_pos_setting(field=field, enabled=False)
                self.assertFalse(getattr(row, field))
                self.assertFalse(getattr(get_company_pos_settings(), field))
                row = set_company_pos_setting(field=field, enabled=True)
                self.assertTrue(getattr(row, field))
        with self.assertRaises(ValidationError):
            set_company_pos_setting(field="not_a_real_field", enabled=True)

    def test_kind_toggles_block_and_allow_checkout(self):
        cases = [
            ("enable_sale", {"kind": ShopReceiptKind.SALE,
                             "payment_method": ShopPaymentMethod.CASH}),
            ("enable_credit", {"kind": ShopReceiptKind.CREDIT,
                               "client_name": "CREDIT CLIENT",
                               "client_phone": "0712345678"}),
            ("enable_quotation", {"kind": ShopReceiptKind.QUOTATION,
                                  "client_name": "QUOTE CLIENT",
                                  "client_phone": "0712345678"}),
            ("enable_trade_out", {"kind": ShopReceiptKind.TRADE_OUT,
                                  "client_name": "TRADE CLIENT",
                                  "client_phone": "0712345678"}),
            ("enable_trade_in", {"kind": ShopReceiptKind.TRADE_IN,
                                 "client_name": "TRADE CLIENT",
                                 "client_phone": "0712345678"}),
        ]
        for field, extra in cases:
            with self.subTest(field=field):
                set_company_pos_setting(field=field, enabled=False)
                self._blocked("transaction type is disabled", **extra)
                kind = extra["kind"]
                self.assertNotIn(kind, pos_settings_as_dict()["kinds"])
                set_company_pos_setting(field=field, enabled=True)
                result = self._checkout(**extra)
                self.assertEqual(result["kind"], kind)

    def test_cart_delivery_off_drops_analytics_deliveries_key(self):
        self.assertNotIn(
            ("analytics", "deliveries"), company_pos_disabled_permission_keys()
        )
        set_company_pos_setting(field="enable_cart_delivery", enabled=False)
        self.assertIn(
            ("analytics", "deliveries"), company_pos_disabled_permission_keys()
        )
        set_company_pos_setting(field="enable_cart_delivery", enabled=True)
        self.assertNotIn(
            ("analytics", "deliveries"), company_pos_disabled_permission_keys()
        )

    def test_kind_off_also_drops_permission_key(self):
        set_company_pos_setting(field="enable_credit", enabled=False)
        disabled = company_pos_disabled_permission_keys()
        self.assertIn(("my-shop", "credit"), disabled)
        self.assertIn(("analytics", "credits"), disabled)
        set_company_pos_setting(field="enable_credit", enabled=True)

    # -- company POS page: payment methods -----------------------------------

    def test_payment_method_toggles_block_and_allow(self):
        cases = [
            ("enable_cash", ShopPaymentMethod.CASH, ShopPaymentMethod.MPESA),
            ("enable_mpesa", ShopPaymentMethod.MPESA, ShopPaymentMethod.CASH),
            ("enable_cash_mpesa", ShopPaymentMethod.BOTH, ShopPaymentMethod.CASH),
        ]
        for field, blocked_method, allowed_method in cases:
            with self.subTest(field=field):
                set_company_pos_setting(field=field, enabled=False)
                self._blocked(
                    "payment method is disabled", payment_method=blocked_method,
                    cash_amount="60", mpesa_amount="40",
                )
                result = self._checkout(payment_method=allowed_method)
                self.assertEqual(result["kind"], ShopReceiptKind.SALE)
                set_company_pos_setting(field=field, enabled=True)
                result = self._checkout(
                    payment_method=blocked_method,
                    cash_amount="60", mpesa_amount="40",
                )
                self.assertEqual(result["kind"], ShopReceiptKind.SALE)

    def test_sale_blocked_when_no_payment_method_enabled(self):
        for field in ("enable_cash", "enable_mpesa", "enable_cash_mpesa"):
            set_company_pos_setting(field=field, enabled=False)
        self._blocked("No payment methods are enabled")
        self.assertEqual(pos_settings_as_dict()["payment_methods"], [])
        for field in ("enable_cash", "enable_mpesa", "enable_cash_mpesa"):
            set_company_pos_setting(field=field, enabled=True)

    # -- company POS page: discount ------------------------------------------

    def test_discount_off_forces_list_price(self):
        discounted = self._checkout(
            lines=[{"id": self.item.pk, "qty": 1, "price": "80"}]
        )
        self.assertEqual(
            discounted["receipt"].lines.first().unit_price, Decimal("80.00")
        )
        set_company_pos_setting(field="enable_discount", enabled=False)
        forced = self._checkout(
            lines=[{"id": self.item.pk, "qty": 1, "price": "80"}]
        )
        self.assertEqual(
            forced["receipt"].lines.first().unit_price, Decimal("100.00")
        )
        set_company_pos_setting(field="enable_discount", enabled=True)

    # -- company POS page: stock modes ---------------------------------------

    def test_stock_modes_are_exclusive_and_gate_stock_check(self):
        row = set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.assertEqual(stock_mode_of(row), "stock_mode_shelf")
        self.assertFalse(row.enable_stock_tracking)
        # Items mode back on restores tracking.
        row = set_company_pos_setting(field="stock_mode_items", enabled=True)
        self.assertTrue(row.enable_stock_tracking)
        self.assertFalse(row.enable_shelf_mode)
        # The active mode cannot be switched off directly.
        with self.assertRaises(ValidationError):
            set_company_pos_setting(field="stock_mode_items", enabled=False)

        # Tracking off (shelf mode): overselling is allowed.
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        result = self._checkout(
            lines=[{"id": self.item.pk, "qty": 999, "price": "100"}]
        )
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)

        # Tracking on: overselling is blocked.
        set_company_pos_setting(field="stock_mode_items", enabled=True)
        self._blocked(
            "Insufficient stock",
            lines=[{"id": self.item.pk, "qty": 999, "price": "100"}],
        )

    # -- company POS page: open & close --------------------------------------

    def test_open_close_toggle_gates_trading(self):
        closed_shop = Shop.objects.create(
            name="Closed Shop",
            location="Nairobi",
            email="closed-shop@test.local",
            phone_number="0700000778",
            login_code="200778",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=closed_shop, item=self.item, quantity=10,
            average_cost=Decimal("40.00"),
        )

        # Compulsory open & close ON (default): no open day -> blocked.
        with self.assertRaises(ValidationError) as ctx:
            complete_shop_checkout(
                shop=closed_shop,
                profile=self.profile,
                payload={
                    "kind": ShopReceiptKind.SALE,
                    "payment_method": ShopPaymentMethod.CASH,
                    "login_code": "200777",
                    "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
                },
            )
        self.assertIn("Open the shop day", str(ctx.exception))

        # OFF: sale goes through without an open day.
        set_company_pos_setting(field="enable_open_close", enabled=False)
        result = complete_shop_checkout(
            shop=closed_shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.SALE,
                "payment_method": ShopPaymentMethod.CASH,
                "login_code": "200777",
                "lines": [{"id": self.item.pk, "qty": 1, "price": "100"}],
            },
        )
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="enable_open_close", enabled=True)

    # -- company POS page: clients -------------------------------------------

    def test_client_data_off_strips_client_fields(self):
        result = self._checkout(client_name="JANE DOE", client_phone="0712345678")
        self.assertEqual(result["receipt"].client_name, "JANE DOE")

        set_company_pos_setting(field="enable_client_data", enabled=False)
        result = self._checkout(client_name="JANE DOE", client_phone="0712345678")
        self.assertEqual(result["receipt"].client_name, "")
        self.assertEqual(result["receipt"].client_phone, "")
        set_company_pos_setting(field="enable_client_data", enabled=True)

    def test_require_client_phone_blocks_and_allows(self):
        set_company_pos_setting(field="require_client_phone", enabled=True)
        self._blocked("client phone")
        result = self._checkout(client_phone="0712345678")
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="require_client_phone", enabled=False)
        result = self._checkout()
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)

    def test_require_client_name_blocks_and_allows(self):
        set_company_pos_setting(field="require_client_name", enabled=True)
        self._blocked("client full name")
        result = self._checkout(client_name="JANE DOE")
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="require_client_name", enabled=False)

    def test_require_flags_ignored_when_client_data_off(self):
        set_company_pos_setting(field="require_client_phone", enabled=True)
        set_company_pos_setting(field="require_client_name", enabled=True)
        set_company_pos_setting(field="enable_client_data", enabled=False)
        result = self._checkout()
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="enable_client_data", enabled=True)
        set_company_pos_setting(field="require_client_phone", enabled=False)
        set_company_pos_setting(field="require_client_name", enabled=False)

    # -- company POS page: cart ----------------------------------------------

    def test_cart_confirm_toggle_blocks_and_allows(self):
        set_company_pos_setting(field="enable_cart_confirm_receipt", enabled=False)
        self._blocked("Confirm receipt at checkout is disabled", confirm=True)
        set_company_pos_setting(field="enable_cart_confirm_receipt", enabled=True)
        result = self._checkout(confirm=True)
        self.assertTrue(result["confirmed"])

    def test_cart_delivery_toggle_blocks_and_allows(self):
        delivery = {
            "delivery": True,
            "client_name": "JANE DOE",
            "client_phone": "0712345678",
            "delivery_location": "Nairobi CBD",
        }
        set_company_pos_setting(field="enable_cart_delivery", enabled=False)
        self._blocked("Delivery is disabled", **delivery)
        set_company_pos_setting(field="enable_cart_delivery", enabled=True)
        result = self._checkout(**delivery)
        self.assertTrue(
            ShopDelivery.objects.filter(receipt=result["receipt"]).exists()
        )

    def test_delivery_is_free_when_no_amount_set(self):
        result = self._checkout(
            delivery=True, client_name="JANE DOE", client_phone="0712345678"
        )
        self.assertEqual(result["receipt"].total, Decimal("100.00"))
        self.assertEqual(result["delivery_fee"], "0.00")
        row = ShopDelivery.objects.get(receipt=result["receipt"])
        self.assertEqual(row.fee_amount, Decimal("0.00"))

    def test_delivery_amount_adds_to_sale_total(self):
        set_company_delivery_amount(amount="150")
        result = self._checkout(
            delivery=True,
            client_name="JANE DOE",
            client_phone="0712345678",
            delivery_location="Westlands",
        )
        self.assertEqual(result["receipt"].subtotal, Decimal("100.00"))
        self.assertEqual(result["receipt"].total, Decimal("250.00"))
        self.assertEqual(result["delivery_fee"], "150.00")
        row = ShopDelivery.objects.get(receipt=result["receipt"])
        self.assertEqual(row.fee_amount, Decimal("150.00"))
        # No delivery -> no fee.
        result = self._checkout()
        self.assertEqual(result["receipt"].total, Decimal("100.00"))
        self.assertEqual(result["delivery_fee"], "0.00")
        set_company_delivery_amount(amount="0")

    def test_delivery_amount_included_in_tax_and_split_total(self):
        set_company_delivery_amount(amount="50")
        set_company_tax_percent(percent="10")
        set_company_pos_setting(field="enable_tax", enabled=True)
        # 100 items + 10 tax + 50 delivery = 160.
        result = self._checkout(
            delivery=True, client_name="JANE DOE", client_phone="0712345678"
        )
        self.assertEqual(result["receipt"].tax_amount, Decimal("10.00"))
        self.assertEqual(result["receipt"].total, Decimal("160.00"))
        # Split payment must match the fee-inclusive total.
        self._blocked(
            "add up to the cart total",
            delivery=True,
            client_name="JANE DOE",
            client_phone="0712345678",
            payment_method=ShopPaymentMethod.BOTH,
            cash_amount="100",
            mpesa_amount="10",
        )
        result = self._checkout(
            delivery=True,
            client_name="JANE DOE",
            client_phone="0712345678",
            payment_method=ShopPaymentMethod.BOTH,
            cash_amount="100",
            mpesa_amount="60",
        )
        self.assertEqual(result["receipt"].total, Decimal("160.00"))
        self.assertEqual(result["receipt"].mpesa_amount, Decimal("60.00"))
        set_company_pos_setting(field="enable_tax", enabled=False)
        set_company_tax_percent(percent="0")
        set_company_delivery_amount(amount="0")

    def test_delivery_amount_applies_to_credit(self):
        set_company_delivery_amount(amount="80")
        result = self._checkout(
            kind=ShopReceiptKind.CREDIT,
            delivery=True,
            client_name="CREDIT CLIENT",
            client_phone="0712345678",
        )
        self.assertEqual(result["receipt"].total, Decimal("180.00"))
        self.assertEqual(
            ShopDelivery.objects.get(receipt=result["receipt"]).fee_amount,
            Decimal("80.00"),
        )
        set_company_delivery_amount(amount="0")

    def test_delivery_location_is_optional(self):
        # Blank location no longer blocks checkout.
        result = self._checkout(
            delivery=True, client_name="JANE DOE", client_phone="0712345678"
        )
        row = ShopDelivery.objects.get(receipt=result["receipt"])
        self.assertEqual(row.location_text, "")
        self.assertEqual(row.maps_url, "")
        # Client name and phone are still required for delivery.
        self._blocked("Client full name is required for delivery", delivery=True)
        self._blocked(
            "Client phone is required for delivery",
            delivery=True,
            client_name="JANE DOE",
        )

    def test_delivery_amount_input_validation(self):
        with self.assertRaises(ValidationError):
            set_company_delivery_amount(amount="abc")
        with self.assertRaises(ValidationError):
            set_company_delivery_amount(amount="-50")
        # Blank input = free delivery.
        row = set_company_delivery_amount(amount="")
        self.assertEqual(row.delivery_amount, Decimal("0.00"))
        row = set_company_delivery_amount(amount="99.99")
        self.assertEqual(row.delivery_amount, Decimal("99.99"))
        self.assertEqual(pos_settings_as_dict()["delivery_amount"], "99.99")
        set_company_delivery_amount(amount="0")

    def test_shop_delivery_amount_override(self):
        set_company_delivery_amount(amount="100")
        # The shop setter needs the pos override first.
        with self.assertRaises(ValidationError):
            set_shop_delivery_amount(shop=self.shop, amount="25")
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        set_shop_delivery_amount(shop=self.shop, amount="25")
        effective = get_effective_pos_settings(self.shop)
        self.assertEqual(effective.delivery_amount, Decimal("25.00"))
        result = self._checkout(
            delivery=True, client_name="JANE DOE", client_phone="0712345678"
        )
        self.assertEqual(result["receipt"].total, Decimal("125.00"))
        # Turning the override off falls back to the company amount.
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)
        result = self._checkout(
            delivery=True, client_name="JANE DOE", client_phone="0712345678"
        )
        self.assertEqual(result["receipt"].total, Decimal("200.00"))
        set_company_delivery_amount(amount="0")

    # -- company POS page: tax -----------------------------------------------

    def test_tax_toggle_applies_and_zeroes_tax(self):
        set_company_tax_percent(percent="16")
        result = self._checkout()  # tax off by default
        self.assertEqual(result["receipt"].tax_amount, Decimal("0.00"))
        self.assertEqual(result["receipt"].total, Decimal("100.00"))

        set_company_pos_setting(field="enable_tax", enabled=True)
        result = self._checkout()
        self.assertEqual(result["receipt"].tax_amount, Decimal("16.00"))
        self.assertEqual(result["receipt"].total, Decimal("116.00"))
        set_company_pos_setting(field="enable_tax", enabled=False)
        set_company_tax_percent(percent="0")

    # -- company receipt page: printing --------------------------------------

    def test_compulsory_print_requires_channel_on_sale(self):
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=True)
        self._blocked("Select a print method")
        result = self._checkout(print_via="usb")
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=False)

    def test_disabled_print_channel_rejected_even_when_optional(self):
        self._checkout(print_via="wifi")  # optional, works when enabled
        set_company_pos_setting(field="enable_print_wifi", enabled=False)
        self._blocked("print method is disabled", print_via="wifi")
        # Sale without print_via still works while printing is optional.
        result = self._checkout()
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="enable_print_wifi", enabled=True)

    def test_disabled_print_channel_blocked_with_compulsory(self):
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=True)
        set_company_pos_setting(field="enable_print_bluetooth", enabled=False)
        self._blocked("print method is disabled", print_via="bluetooth")
        result = self._checkout(print_via="usb")
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)
        set_company_pos_setting(field="enable_print_bluetooth", enabled=True)
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=False)

    def test_no_print_channels_drops_print_permission_key(self):
        for field in ("enable_print_bluetooth", "enable_print_usb",
                      "enable_print_wifi"):
            set_company_pos_setting(field=field, enabled=False)
        self.assertIn(("my-shop", "print"), company_pos_disabled_permission_keys())
        self.assertEqual(pos_settings_as_dict()["print_channels"], [])
        for field in ("enable_print_bluetooth", "enable_print_usb",
                      "enable_print_wifi"):
            set_company_pos_setting(field=field, enabled=True)

    # -- company receipt page: copies ----------------------------------------

    def test_copy_toggles_drive_receipt_print_copies(self):
        roles = enabled_receipt_print_roles()
        self.assertEqual(roles, ["customer"])

        set_company_pos_setting(field="print_staff_receipt", enabled=True)
        set_company_pos_setting(field="print_cashier_receipt", enabled=True)
        roles = enabled_receipt_print_roles()
        self.assertEqual(roles, ["customer", "staff", "cashier"])

        copies = build_receipt_print_copies(
            {"document_title": "RECEIPT", "lines": []}, kind="sale"
        )
        self.assertEqual([c["role"] for c in copies], roles)

        set_company_pos_setting(field="print_customer_receipt", enabled=False)
        set_company_pos_setting(field="print_staff_receipt", enabled=False)
        set_company_pos_setting(field="print_cashier_receipt", enabled=False)
        # Nothing enabled, printing optional -> nothing to print.
        self.assertEqual(
            build_receipt_print_copies(
                {"document_title": "RECEIPT", "lines": []}, kind="sale"
            ),
            [],
        )
        # Compulsory printing falls back to a customer copy.
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=True)
        copies = build_receipt_print_copies(
            {"document_title": "RECEIPT", "lines": []}, kind="sale"
        )
        self.assertEqual([c["role"] for c in copies], ["customer"])
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=False)
        set_company_pos_setting(field="print_customer_receipt", enabled=True)

    def test_copy_toggles_do_not_affect_other_kinds(self):
        set_company_pos_setting(field="print_customer_receipt", enabled=False)
        copies = build_receipt_print_copies(
            {"document_title": "QUOTE", "lines": []}, kind="quotation"
        )
        self.assertEqual(len(copies), 1)
        set_company_pos_setting(field="print_customer_receipt", enabled=True)

    # -- company receipt page: payment methods line ---------------------------

    def test_receipt_payment_methods_toggle_hides_methods_line(self):
        label = receipt_accepted_payment_methods_label()
        self.assertIn("Cash", label)
        self.assertIn("M-Pesa", label)
        set_company_pos_setting(
            field="enable_receipt_payment_methods", enabled=False
        )
        self.assertEqual(receipt_accepted_payment_methods_label(), "")
        set_company_pos_setting(
            field="enable_receipt_payment_methods", enabled=True
        )

    # -- shop-level overrides -------------------------------------------------

    def test_shop_toggle_rejected_without_override(self):
        ShopPosSettings.objects.filter(shop=self.shop).delete()
        _invalidate_pos_settings_cache()
        with self.assertRaises(ValidationError) as ctx:
            set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=False)
        self.assertIn("custom shop POS settings", str(ctx.exception))
        with self.assertRaises(ValidationError) as ctx:
            set_shop_pos_setting(
                shop=self.shop, field="compulsory_print_on_sale", enabled=True
            )
        self.assertIn("custom shop receipt settings", str(ctx.exception))

    def test_shop_override_pos_applies_only_pos_fields(self):
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=False)
        effective = get_effective_pos_settings(self.shop)
        self.assertFalse(effective.enable_credit)
        # Company row untouched.
        self.assertTrue(get_company_pos_settings().enable_credit)
        # Receipt fields still require the receipt override, not pos.
        with self.assertRaises(ValidationError):
            set_shop_pos_setting(
                shop=self.shop, field="compulsory_print_on_sale", enabled=True
            )
        # Checkout at this shop honours the shop override.
        self._blocked(
            "transaction type is disabled",
            kind=ShopReceiptKind.CREDIT,
            client_name="CREDIT CLIENT",
            client_phone="0712345678",
        )
        # Turning the override off restores company behaviour.
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)
        result = self._checkout(
            kind=ShopReceiptKind.CREDIT,
            client_name="CREDIT CLIENT",
            client_phone="0712345678",
        )
        self.assertEqual(result["kind"], ShopReceiptKind.CREDIT)

    def test_shop_override_receipt_applies_receipt_fields(self):
        set_shop_pos_override(shop=self.shop, section="receipt", enabled=True)
        set_shop_pos_setting(
            shop=self.shop, field="compulsory_print_on_sale", enabled=True
        )
        effective = get_effective_pos_settings(self.shop)
        self.assertTrue(effective.compulsory_print_on_sale)
        self.assertFalse(get_company_pos_settings().compulsory_print_on_sale)
        # POS fields still come from the company.
        with self.assertRaises(ValidationError):
            set_shop_pos_setting(shop=self.shop, field="enable_sale", enabled=False)
        # The override is honoured at checkout for this shop.
        self._blocked("Select a print method")
        set_shop_pos_override(shop=self.shop, section="receipt", enabled=False)
        result = self._checkout()
        self.assertEqual(result["kind"], ShopReceiptKind.SALE)

    def test_shop_stock_mode_cannot_be_overridden(self):
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        for field in ("stock_mode_shelf", "enable_shelf_mode",
                      "enable_stock_tracking"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    set_shop_pos_setting(
                        shop=self.shop, field=field, enabled=True
                    )
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)

    def test_company_require_reference_syncs_to_override_shops(self):
        set_shop_pos_override(shop=self.shop, section="receipt", enabled=True)
        shop_row = ShopPosSettings.objects.get(shop=self.shop)
        self.assertFalse(shop_row.require_mpesa_reference_on_confirm)
        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=True
        )
        shop_row.refresh_from_db()
        self.assertTrue(shop_row.require_mpesa_reference_on_confirm)
        self.assertTrue(
            get_effective_pos_settings(self.shop).require_mpesa_reference_on_confirm
        )
        set_company_pos_setting(
            field="require_mpesa_reference_on_confirm", enabled=False
        )
        shop_row.refresh_from_db()
        self.assertFalse(shop_row.require_mpesa_reference_on_confirm)

    # -- per-field inheritance -----------------------------------------------

    def test_company_changes_reach_uncustomised_fields(self):
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        # The shop customises only credit; every other field tracks company.
        set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=False)
        set_company_pos_setting(field="enable_quotation", enabled=False)
        effective = get_effective_pos_settings(self.shop)
        self.assertFalse(effective.enable_credit)
        self.assertFalse(effective.enable_quotation)
        set_company_pos_setting(field="enable_quotation", enabled=True)
        self.assertTrue(get_effective_pos_settings(self.shop).enable_quotation)
        # The custom value still sticks.
        self.assertFalse(get_effective_pos_settings(self.shop).enable_credit)

    def test_shop_field_matching_company_reinherits(self):
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=False)
        shop_row = ShopPosSettings.objects.get(shop=self.shop)
        self.assertIn("enable_credit", shop_row.custom_pos_fields)
        # Writing the company value back re-inherits the company default.
        set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=True)
        shop_row.refresh_from_db()
        self.assertNotIn("enable_credit", shop_row.custom_pos_fields)
        set_company_pos_setting(field="enable_credit", enabled=False)
        self.assertFalse(get_effective_pos_settings(self.shop).enable_credit)
        set_company_pos_setting(field="enable_credit", enabled=True)

    def test_company_detail_input_is_default_until_shop_writes(self):
        set_company_delivery_amount(amount="120")
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        # Untouched detail inputs follow company changes live.
        self.assertEqual(
            get_effective_pos_settings(self.shop).delivery_amount,
            Decimal("120.00"),
        )
        set_company_delivery_amount(amount="150")
        self.assertEqual(
            get_effective_pos_settings(self.shop).delivery_amount,
            Decimal("150.00"),
        )
        # Once the shop writes its own, later company changes leave it alone.
        set_shop_delivery_amount(shop=self.shop, amount="40")
        set_company_delivery_amount(amount="200")
        self.assertEqual(
            get_effective_pos_settings(self.shop).delivery_amount,
            Decimal("40.00"),
        )
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)
        set_company_delivery_amount(amount="0")

    def test_disabling_override_clears_custom_fields(self):
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=False)
        set_shop_delivery_amount(shop=self.shop, amount="40")
        shop_row = ShopPosSettings.objects.get(shop=self.shop)
        self.assertIn("enable_credit", shop_row.custom_pos_fields)
        self.assertIn("delivery_amount", shop_row.custom_pos_fields)
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)
        shop_row.refresh_from_db()
        self.assertEqual(shop_row.custom_pos_fields, [])
        effective = get_effective_pos_settings(self.shop)
        self.assertTrue(effective.enable_credit)
        self.assertEqual(effective.delivery_amount, Decimal("0.00"))

    def test_receipt_fields_track_company_until_customised(self):
        set_company_pos_setting(field="print_staff_receipt", enabled=True)
        set_shop_pos_override(shop=self.shop, section="receipt", enabled=True)
        self.assertTrue(
            get_effective_pos_settings(self.shop).print_staff_receipt
        )
        set_company_pos_setting(field="print_staff_receipt", enabled=False)
        self.assertFalse(
            get_effective_pos_settings(self.shop).print_staff_receipt
        )
        # A shop write diverges and then sticks.
        set_shop_pos_setting(
            shop=self.shop, field="print_staff_receipt", enabled=True
        )
        self.assertTrue(
            get_effective_pos_settings(self.shop).print_staff_receipt
        )
        # Receipt overrides do not stop POS fields tracking the company.
        set_company_pos_setting(field="enable_sale", enabled=False)
        self.assertFalse(get_effective_pos_settings(self.shop).enable_sale)
        set_company_pos_setting(field="enable_sale", enabled=True)

    def test_bundled_detail_save_marks_only_differing_fields(self):
        from shops.services import set_shop_receipt_number_formats

        set_shop_pos_override(shop=self.shop, section="receipt", enabled=True)
        # Sale differs from the company default ("S"); the rest match.
        set_shop_receipt_number_formats(
            shop=self.shop, sale="SX", credit="C", quotation="Q",
            trade_out="T", trade_in="TI",
        )
        shop_row = ShopPosSettings.objects.get(shop=self.shop)
        self.assertEqual(
            shop_row.custom_receipt_fields, ["receipt_format_sale"]
        )
        # Company changes to the untouched formats still reach the shop.
        set_company_pos_setting(field="enable_credit", enabled=True)
        company = get_company_pos_settings()
        company.receipt_format_credit = "CR"
        company.save(update_fields=["receipt_format_credit", "updated_at"])
        _invalidate_pos_settings_cache()
        effective = get_effective_pos_settings(self.shop)
        self.assertEqual(effective.receipt_format_sale, "SX")
        self.assertEqual(effective.receipt_format_credit, "CR")

    # -- hardening -----------------------------------------------------------

    def test_raw_stock_flags_cannot_leave_no_mode(self):
        # Raw field writes still work but must not leave both modes off.
        row = set_company_pos_setting(field="enable_shelf_mode", enabled=True)
        self.assertEqual(stock_mode_of(row), "stock_mode_both")
        row = set_company_pos_setting(
            field="enable_stock_tracking", enabled=False
        )
        self.assertEqual(stock_mode_of(row), "stock_mode_shelf")
        with self.assertRaises(ValidationError):
            set_company_pos_setting(field="enable_shelf_mode", enabled=False)
        row = set_company_pos_setting(field="enable_stock_tracking", enabled=True)
        self.assertEqual(stock_mode_of(row), "stock_mode_both")
        row = set_company_pos_setting(field="enable_shelf_mode", enabled=False)
        self.assertEqual(stock_mode_of(row), "stock_mode_items")

    def test_compulsory_print_also_gates_credit(self):
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=True)
        self._blocked(
            "Select a print method",
            kind=ShopReceiptKind.CREDIT,
            client_name="CREDIT CLIENT",
            client_phone="0712345678",
        )
        result = self._checkout(
            kind=ShopReceiptKind.CREDIT,
            client_name="CREDIT CLIENT",
            client_phone="0712345678",
            print_via="usb",
        )
        self.assertEqual(result["kind"], ShopReceiptKind.CREDIT)
        self.assertTrue(result["print_required"])
        set_company_pos_setting(field="compulsory_print_on_sale", enabled=False)

    def test_shop_settings_page_rejects_other_section_fields(self):
        from django.test import Client
        from django.urls import reverse

        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        set_shop_pos_override(shop=self.shop, section="receipt", enabled=True)
        client = Client()
        # Sign in as the shop portal without hitting the rate-limited
        # /shops/login/ endpoint (the bucket is shared across the suite).
        session = client.session
        session["shop_portal_auth"] = True
        session["shop_portal_shop_id"] = str(self.shop.pk)
        session.save()
        pos_url = reverse(
            "employees:my_shop_settings_pos", kwargs={"shop_id": self.shop.pk}
        )
        receipt_url = reverse(
            "employees:my_shop_settings_receipt",
            kwargs={"shop_id": self.shop.pk},
        )
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

        # A POS field cannot be toggled through the receipt page.
        response = client.post(
            receipt_url,
            {"action": "toggle_pos_setting", "field": "enable_sale",
             "enabled": "0"},
            **ajax,
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(get_effective_pos_settings(self.shop).enable_sale)

        # A receipt field cannot be toggled through the pos page.
        response = client.post(
            pos_url,
            {"action": "toggle_pos_setting",
             "field": "compulsory_print_on_sale", "enabled": "1"},
            **ajax,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            get_effective_pos_settings(self.shop).compulsory_print_on_sale
        )

        # The right page still toggles its own fields.
        response = client.post(
            pos_url,
            {"action": "toggle_pos_setting", "field": "enable_credit",
             "enabled": "0"},
            **ajax,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_effective_pos_settings(self.shop).enable_credit)

        # The POS cart and the public storefront honour the cart toggles
        # (this shop has override_pos on, so flip via the shop setter).
        workspace_url = reverse(
            "employees:my_shop_workspace", kwargs={"shop_id": self.shop.pk}
        )
        website_url = reverse(
            "employees:shop_website", kwargs={"shop_id": self.shop.pk}
        )

        html = client.get(workspace_url).content.decode()
        self.assertIn("data-cart-delivery-wrap", html)
        self.assertIn("data-cart-confirm-wrap", html)
        html = client.get(website_url).content.decode()
        self.assertIn('data-delivery-enabled="1"', html)

        set_shop_pos_setting(
            shop=self.shop, field="enable_cart_delivery", enabled=False
        )
        set_shop_pos_setting(
            shop=self.shop, field="enable_cart_confirm_receipt", enabled=False
        )
        html = client.get(workspace_url).content.decode()
        self.assertNotIn("data-cart-delivery-wrap", html)
        self.assertNotIn("data-cart-confirm-wrap", html)
        html = client.get(website_url).content.decode()
        self.assertIn('data-delivery-enabled="0"', html)

        set_shop_pos_setting(
            shop=self.shop, field="enable_cart_delivery", enabled=True
        )
        set_shop_pos_setting(
            shop=self.shop, field="enable_cart_confirm_receipt", enabled=True
        )

        # The POS settings page renders the delivery amount input.
        html = client.get(pos_url).content.decode()
        self.assertIn("data-delivery-amount-input", html)

        # Non-toggle actions are pinned to their own page too.
        response = client.post(
            receipt_url,
            {"action": "set_tax_percent", "tax_percent": "16"},
            **ajax,
        )
        self.assertEqual(response.status_code, 400)
        response = client.post(
            pos_url,
            {"action": "set_tax_percent", "tax_percent": "16"},
            **ajax,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            get_effective_pos_settings(self.shop).tax_percent, Decimal("16.00")
        )
        response = client.post(
            receipt_url,
            {"action": "set_delivery_amount", "delivery_amount": "300"},
            **ajax,
        )
        self.assertEqual(response.status_code, 400)
        response = client.post(
            pos_url,
            {"action": "set_delivery_amount", "delivery_amount": "300"},
            **ajax,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            get_effective_pos_settings(self.shop).delivery_amount,
            Decimal("300.00"),
        )
        response = client.post(
            pos_url,
            {"action": "set_return_notice_text", "return_notice_text": "hi"},
            **ajax,
        )
        self.assertEqual(response.status_code, 400)

        set_shop_pos_setting(shop=self.shop, field="enable_credit", enabled=True)
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)
        set_shop_pos_override(shop=self.shop, section="receipt", enabled=False)

    # -- company POS page: shop floor sidebar ---------------------------------

    def _sidebar_labels(self):
        from employees.workspace import sidebar_for_my_shop

        sidebar = sidebar_for_my_shop(
            None,
            shop=self.shop,
            portal=True,
            active="workspace",
            shop_open=True,
            print_channels=["usb"],
            trade_in_enabled=True,
            trade_out_enabled=True,
            shelf_mode_enabled=True,
        )
        return [item["label"] for item in sidebar["primary"]]

    def test_sidebar_toggles_hide_links_only(self):
        labels = self._sidebar_labels()
        for label in (
            "Close shop",
            "Trade",
            "Buy stock items",
            "Stock transfers",
            "Stock count",
            "Shelf stock",
            "Register expense",
            "Connect to printer",
            "Receipts",
            "Shop website",
        ):
            self.assertIn(label, labels)

        for field in (
            "enable_sidebar_open_close",
            "enable_sidebar_trade",
            "enable_sidebar_buy_stock",
            "enable_sidebar_stock_transfers",
            "enable_sidebar_stock_count",
            "enable_sidebar_shelf_stock",
            "enable_sidebar_expense",
            "enable_sidebar_printer",
            "enable_sidebar_receipts",
            "enable_sidebar_website",
        ):
            set_company_pos_setting(field=field, enabled=False)

        labels = self._sidebar_labels()
        for label in (
            "Close shop",
            "Open shop",
            "Trade",
            "Buy stock items",
            "Stock transfers",
            "Stock count",
            "Shelf stock",
            "Register expense",
            "Connect to printer",
            "Receipts",
            "Shop website",
        ):
            self.assertNotIn(label, labels)

        # Sidebar toggles are link-only: they never drop permission keys, so
        # employees (e.g. cashiers) keep the features on their own surfaces.
        disabled = company_pos_disabled_permission_keys()
        for key in (
            ("my-shop", "open_close"),
            ("my-shop", "trade_out"),
            ("my-shop", "trade_in"),
            ("my-shop", "buy_stock"),
            ("my-shop", "stock_requests"),
            ("my-shop", "stock_count"),
            ("my-shop", "shelf_stock"),
            ("my-shop", "register_expense"),
            ("my-shop", "receipts"),
            ("my-shop", "print"),
            ("my-shop", "workspace"),
        ):
            self.assertNotIn(key, disabled)

        for field in (
            "enable_sidebar_open_close",
            "enable_sidebar_trade",
            "enable_sidebar_buy_stock",
            "enable_sidebar_stock_transfers",
            "enable_sidebar_stock_count",
            "enable_sidebar_shelf_stock",
            "enable_sidebar_expense",
            "enable_sidebar_printer",
            "enable_sidebar_receipts",
            "enable_sidebar_website",
        ):
            set_company_pos_setting(field=field, enabled=True)

    def test_sidebar_hidden_page_stays_reachable(self):
        from django.urls import reverse

        client = self.client_class()
        session = client.session
        session["shop_portal_auth"] = True
        session["shop_portal_shop_id"] = self.shop.pk
        session.save()

        transfers_url = reverse(
            "employees:my_shop_stock_requests", kwargs={"shop_id": self.shop.pk}
        )
        response = client.get(transfers_url)
        self.assertEqual(response.status_code, 200)

        # Toggling the link off does not block the page itself.
        set_company_pos_setting(
            field="enable_sidebar_stock_transfers", enabled=False
        )
        response = client.get(transfers_url)
        self.assertEqual(response.status_code, 200)
        set_company_pos_setting(
            field="enable_sidebar_stock_transfers", enabled=True
        )

    def test_shop_sidebar_override_hides_per_shop(self):
        # Company default: links visible. A shop override can hide its own.
        set_shop_pos_override(shop=self.shop, section="pos", enabled=True)
        set_shop_pos_setting(
            shop=self.shop, field="enable_sidebar_receipts", enabled=False
        )
        labels = self._sidebar_labels()
        self.assertNotIn("Receipts", labels)
        self.assertIn("Shop website", labels)
        # Company row untouched.
        self.assertTrue(get_company_pos_settings().enable_sidebar_receipts)
        set_shop_pos_setting(
            shop=self.shop, field="enable_sidebar_receipts", enabled=True
        )
        set_shop_pos_override(shop=self.shop, section="pos", enabled=False)
