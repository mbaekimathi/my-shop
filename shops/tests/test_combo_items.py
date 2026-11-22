"""Combo items — price from portions and per-component sale tally."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ItemComboComponent, ShopItemPrice, ShopStock
from items.services import create_item, update_item
from shops.models import Shop, ShopPaymentMethod, ShopReceiptKind
from shops.services import (
    _invalidate_pos_settings_cache,
    complete_shop_checkout,
    get_company_pos_settings,
    open_shop_day,
)
from shops.views import build_shop_catalog_page


class ComboItemTests(TestCase):
    def setUp(self):
        _invalidate_pos_settings_cache()
        self.user = User.objects.create_user(
            username="770099",
            password="pass",
            email="combo@test.local",
            first_name="Combo",
            last_name="Cashier",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="770099",
            phone_country_code="+254",
            phone_number="700009900",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Combo Shop",
            location="Nairobi",
            email="combo@test.local",
            phone_number="0700009900",
            login_code="770099",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        self.profile.assigned_shops.add(self.shop)
        pos = get_company_pos_settings()
        if not pos.enable_stock_tracking:
            pos.enable_stock_tracking = True
            pos.save(update_fields=["enable_stock_tracking", "updated_at"])

        self.tea = Item.objects.create(
            name="Tea",
            category="Cafe",
            minimum_selling_price=Decimal("80.00"),
            shop_price=Decimal("100.00"),
            stock=Decimal("20.000"),
            track_serial_number=False,
            created_by=self.profile,
        )
        self.mandazi = Item.objects.create(
            name="Mandazi",
            category="Cafe",
            minimum_selling_price=Decimal("30.00"),
            shop_price=Decimal("40.00"),
            stock=Decimal("20.000"),
            track_serial_number=False,
            created_by=self.profile,
        )
        for item, qty in ((self.tea, "20.000"), (self.mandazi, "20.000")):
            ShopStock.objects.create(
                shop=self.shop,
                item=item,
                quantity=Decimal(qty),
                average_cost=Decimal("10.00"),
            )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "770099",
            },
        )

    def _combo_post(self, qtys, **extra):
        post = {
            "category": "COMBOS",
            "name": "BREAKFAST COMBO",
            "description": "",
            "item_type": "combo",
            "combo_item_id": [str(self.tea.pk), str(self.mandazi.pk)],
            "combo_qty": [str(qty) for qty in qtys],
        }
        post.update(extra)
        return post

    def _make_combo(self, qtys=("0.75", "0.25"), **extra):
        return create_item(self.profile, self._combo_post(qtys, **extra), {})

    def _checkout(self, item, qty="1", price="85.00", kind=ShopReceiptKind.SALE):
        line = {"id": item.pk, "qty": qty, "price": price}
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": kind,
                "payment_method": ShopPaymentMethod.CASH,
                "login_code": "770099",
                "lines": [line],
            },
        )

    def _shop_qty(self, item):
        return ShopStock.objects.get(shop=self.shop, item=item).quantity

    # -- registration ----------------------------------------------------

    def test_create_combo_writes_components_and_formula_price(self):
        combo = self._make_combo()
        self.assertTrue(combo.is_combo)
        legs = list(combo.combo_components.select_related("component"))
        self.assertEqual(len(legs), 2)
        qtys = {leg.component_id: leg.qty for leg in legs}
        self.assertEqual(qtys[self.tea.pk], Decimal("0.7500"))
        self.assertEqual(qtys[self.mandazi.pk], Decimal("0.2500"))
        # Price = 100×0.75 + 40×0.25 = 85 — with no selling price posted the
        # combo sells at its items' total, which is also its minimum price.
        self.assertEqual(combo.shop_price, Decimal("85.00"))
        self.assertEqual(combo.minimum_selling_price, Decimal("85.00"))
        row = ShopItemPrice.objects.get(shop=self.shop, item=combo)
        self.assertEqual(row.price, Decimal("85.00"))
        self.assertTrue(row.is_sold)

    def test_combo_requires_at_least_one_component(self):
        with self.assertRaises(ValidationError):
            create_item(
                self.profile,
                {
                    "category": "COMBOS",
                    "name": "EMPTY COMBO",
                    "item_type": "combo",
                    "combo_item_id": [],
                    "combo_qty": [],
                },
                {},
            )

    def test_combo_rejects_duplicates_combos_and_serial_items(self):
        serial_item = Item.objects.create(
            name="Serial Phone",
            category="Tech",
            minimum_selling_price=Decimal("1000.00"),
            shop_price=Decimal("1200.00"),
            track_serial_number=True,
            created_by=self.profile,
        )
        combo = self._make_combo()
        for extra_ids, extra_qtys in (
            ([self.tea.pk, self.tea.pk], ["0.6", "0.4"]),   # duplicate leg
            ([combo.pk], ["1"]),                        # nested combo
            ([serial_item.pk], ["1"]),                  # serial-tracked leg
        ):
            with self.assertRaises(ValidationError):
                create_item(
                    self.profile,
                    {
                        "category": "COMBOS",
                        "name": "BAD COMBO",
                        "item_type": "combo",
                        "combo_item_id": [str(pk) for pk in extra_ids],
                        "combo_qty": extra_qtys,
                    },
                    {},
                )

    def test_combo_qtys_validate_but_need_no_total(self):
        # Zero / negative / missing portions are rejected.
        for bad in ("0", "-0.5", ""):
            with self.assertRaises(ValidationError):
                self._make_combo(qtys=(bad, "0.25"))
        # Any positive portions are fine — no shared total to keep in
        # step, so one combo can use whole + fractional units and even
        # several units of an item.
        combo = self._make_combo(qtys=("1", "3"))
        qtys = {leg.component_id: leg.qty
                for leg in combo.combo_components.all()}
        self.assertEqual(qtys[self.mandazi.pk], Decimal("3.0000"))
        combo.refresh_from_db()
        # 100×1 + 40×3 = 220
        self.assertEqual(combo.shop_price, Decimal("220.00"))

    def test_component_price_edit_reprices_combo(self):
        combo = self._make_combo()
        update_item(
            self.tea,
            {
                "category": "Cafe",
                "name": "Tea",
                "description": "",
                "minimum_selling_price": "80",
                "shop_price": "120",
                "pricing_mode": "single",
            },
            {},
        )
        combo.refresh_from_db()
        # 120×0.75 + 40×0.25 = 100
        self.assertEqual(combo.shop_price, Decimal("100.00"))
        row = ShopItemPrice.objects.get(shop=self.shop, item=combo)
        self.assertEqual(row.price, Decimal("100.00"))

    def test_update_combo_adds_and_removes_components(self):
        combo = self._make_combo()
        bread = Item.objects.create(
            name="Bread",
            category="Cafe",
            minimum_selling_price=Decimal("20.00"),
            shop_price=Decimal("25.00"),
            stock=Decimal("10.000"),
            track_serial_number=False,
            created_by=self.profile,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=bread,
            quantity=Decimal("10.000"),
            average_cost=Decimal("8.00"),
        )
        update_item(
            combo,
            {
                "category": "COMBOS",
                "name": "BREAKFAST COMBO",
                "description": "",
                "item_type": "combo",
                "combo_item_id": [str(bread.pk), str(self.mandazi.pk)],
                "combo_qty": ["0.6", "0.4"],
            },
            {},
        )
        legs = {leg.component_id: leg.qty
                for leg in combo.combo_components.all()}
        # Tea removed, bread added.
        self.assertEqual(
            legs, {bread.pk: Decimal("0.6000"), self.mandazi.pk: Decimal("0.4000")}
        )
        combo.refresh_from_db()
        # 25×0.6 + 40×0.4 = 31
        self.assertEqual(combo.shop_price, Decimal("31.00"))
        result = self._checkout(combo, qty="1", price="31.00")
        self.assertEqual(result["receipt"].total, Decimal("31.00"))
        self.assertEqual(self._shop_qty(bread), Decimal("9.400"))
        self.assertEqual(self._shop_qty(self.mandazi), Decimal("19.600"))
        self.assertEqual(self._shop_qty(self.tea), Decimal("20.000"))

    def test_update_combo_back_to_single_removes_legs(self):
        combo = self._make_combo()
        update_item(
            combo,
            {
                "category": "COMBOS",
                "name": "BREAKFAST COMBO",
                "description": "",
                "item_type": "single",
                "minimum_selling_price": "50",
                "shop_price": "60",
                "pricing_mode": "single",
            },
            {},
        )
        combo.refresh_from_db()
        self.assertFalse(combo.is_combo)
        self.assertEqual(combo.combo_components.count(), 0)
        self.assertEqual(combo.shop_price, Decimal("60.00"))

    def test_combo_catalog_row_lists_components(self):
        combo = self._make_combo()
        row = build_shop_catalog_page(self.shop)
        found = next(r for r in row["items"] if r["id"] == combo.pk)
        self.assertTrue(found["is_combo"])
        self.assertEqual(found["price"], "85.00")

    # -- sale tally --------------------------------------------------------

    def test_sale_tallies_components_by_portion(self):
        combo = self._make_combo()
        result = self._checkout(combo, qty="2")
        receipt = result["receipt"]
        self.assertEqual(receipt.total, Decimal("170.00"))

        lines = list(receipt.lines.order_by("id"))
        self.assertEqual({line.item_id for line in lines},
                         {self.tea.pk, self.mandazi.pk})
        by_item = {line.item_id: line for line in lines}
        # 2 combos × 0.75 tea → 1.5 units at full price.
        self.assertEqual(by_item[self.tea.pk].quantity, Decimal("1.500"))
        self.assertEqual(by_item[self.tea.pk].unit_price, Decimal("100.00"))
        self.assertEqual(by_item[self.tea.pk].line_total, Decimal("150.00"))
        # 2 combos × 0.25 mandazi → 0.5 unit.
        self.assertEqual(by_item[self.mandazi.pk].quantity, Decimal("0.500"))
        self.assertEqual(by_item[self.mandazi.pk].line_total, Decimal("20.00"))

        self.assertEqual(self._shop_qty(self.tea), Decimal("18.500"))
        self.assertEqual(self._shop_qty(self.mandazi), Decimal("19.500"))
        self.tea.refresh_from_db()
        self.mandazi.refresh_from_db()
        self.assertEqual(self.tea.stock, Decimal("18.500"))
        self.assertEqual(self.mandazi.stock, Decimal("19.500"))

    def test_component_stock_shortage_blocks_combo_sale(self):
        combo = self._make_combo()
        stock = ShopStock.objects.get(shop=self.shop, item=self.tea)
        stock.quantity = Decimal("0.400")
        stock.save(update_fields=["quantity"])
        # One combo needs 0.75 tea — blocked.
        with self.assertRaises(ValidationError):
            self._checkout(combo, qty="1")
        self.assertEqual(
            self._shop_qty(self.mandazi), Decimal("20.000")
        )

    def test_edited_combo_price_scales_component_lines(self):
        combo = self._make_combo()
        pos = get_company_pos_settings()
        pos.enable_discount = True
        pos.save(update_fields=["enable_discount", "updated_at"])
        _invalidate_pos_settings_cache()

        # Charge 90 instead of the items-total 85 → each leg scales by 90/85.
        result = self._checkout(combo, qty="1", price="90.00")
        receipt = result["receipt"]
        by_item = {line.item_id: line for line in receipt.lines.all()}
        self.assertEqual(by_item[self.tea.pk].unit_price, Decimal("105.88"))
        # Last leg absorbs rounding drift so the leg totals equal 90.00.
        self.assertEqual(by_item[self.mandazi.pk].unit_price, Decimal("42.36"))
        self.assertEqual(receipt.total, Decimal("90.00"))

    def test_combo_below_floor_rejected(self):
        combo = self._make_combo()
        pos = get_company_pos_settings()
        pos.enable_discount = True
        pos.save(update_fields=["enable_discount", "updated_at"])
        _invalidate_pos_settings_cache()
        # Floor = the combo's items total = 100×0.75 + 40×0.25 = 85.
        self.assertEqual(combo.minimum_selling_price, Decimal("85.00"))
        with self.assertRaises(ValidationError):
            self._checkout(combo, qty="1", price="40.00")
        with self.assertRaises(ValidationError):
            self._checkout(combo, qty="1", price="84.99")

    # -- selling prices on top of the items-total minimum -----------------

    def test_combo_selling_price_above_minimum(self):
        combo = self._make_combo(
            ("0.75", "0.25"),
            pricing_mode="single",
            shop_price="120",
        )
        # Items' total (85) is the floor; the posted 120 is what shops sell at.
        self.assertEqual(combo.shop_price, Decimal("120.00"))
        self.assertEqual(combo.minimum_selling_price, Decimal("85.00"))
        row = ShopItemPrice.objects.get(shop=self.shop, item=combo)
        self.assertEqual(row.price, Decimal("120.00"))
        result = self._checkout(combo, qty="1", price="120.00")
        self.assertEqual(result["receipt"].total, Decimal("120.00"))

    def test_combo_selling_price_below_minimum_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self._make_combo(
                ("0.75", "0.25"),
                pricing_mode="single",
                shop_price="50",
            )
        self.assertIn("minimum", str(ctx.exception).lower())

    def test_combo_individual_shop_prices(self):
        other = Shop.objects.create(
            name="Second Shop",
            location="Nakuru",
            email="second@test.local",
            phone_number="0700009911",
            login_code="770098",
            password_hash=make_password("pass"),
            created_by=self.profile,
        )
        # Tea sells dearer at the second shop → its items-total floor is
        # 200×0.75 + 40×0.25 = 160 there vs 85 here. Per-shop rows only
        # count when the item prices per shop — the flag every registered
        # item gets.
        self.tea.use_individual_shop_prices = True
        self.tea.save(update_fields=["use_individual_shop_prices"])
        ShopItemPrice.objects.create(
            shop=other, item=self.tea, price=Decimal("200.00")
        )
        with self.assertRaises(ValidationError):
            # 100 is below the second shop's 160 floor.
            self._make_combo(
                ("0.75", "0.25"),
                pricing_mode="individual",
                **{
                    f"shop_price_{self.shop.pk}": "100",
                    f"shop_price_{other.pk}": "100",
                },
            )
        combo = self._make_combo(
            ("0.75", "0.25"),
            pricing_mode="individual",
            **{
                f"shop_price_{self.shop.pk}": "100",
                f"shop_price_{other.pk}": "200",
            },
        )
        self.assertEqual(combo.minimum_selling_price, Decimal("85.00"))
        self.assertEqual(combo.shop_price, Decimal("100.00"))
        prices = {
            row.shop_id: row.price
            for row in ShopItemPrice.objects.filter(item=combo)
        }
        self.assertEqual(
            prices, {self.shop.pk: Decimal("100.00"), other.pk: Decimal("200.00")}
        )

    def test_component_price_rise_lifts_stored_combo_price_to_floor(self):
        combo = self._make_combo(("0.75", "0.25"), shop_price="120")
        # A markup above the floor survives a component price rise.
        update_item(
            self.tea,
            {
                "category": "Cafe",
                "name": "Tea",
                "description": "",
                "minimum_selling_price": "80",
                "shop_price": "140",
                "pricing_mode": "single",
            },
            {},
        )
        combo.refresh_from_db()
        # New items total = 140×0.75 + 40×0.25 = 115 < 120 → price keeps.
        self.assertEqual(combo.minimum_selling_price, Decimal("115.00"))
        self.assertEqual(
            ShopItemPrice.objects.get(shop=self.shop, item=combo).price,
            Decimal("120.00"),
        )
        update_item(
            self.tea,
            {
                "category": "Cafe",
                "name": "Tea",
                "description": "",
                "minimum_selling_price": "80",
                "shop_price": "200",
                "pricing_mode": "single",
            },
            {},
        )
        combo.refresh_from_db()
        # New items total = 200×0.75 + 40×0.25 = 160 > 120 → price lifts.
        self.assertEqual(combo.minimum_selling_price, Decimal("160.00"))
        self.assertEqual(
            ShopItemPrice.objects.get(shop=self.shop, item=combo).price,
            Decimal("160.00"),
        )

    # -- feature toggle ---------------------------------------------------

    def test_combo_toggle_off_blocks_register_catalog_and_sale(self):
        from shops.services import set_company_pos_setting

        combo = self._make_combo()
        set_company_pos_setting(field="enable_combo_items", enabled=False)
        try:
            with self.assertRaises(ValidationError):
                create_item(
                    self.profile,
                    {
                        "category": "COMBOS",
                        "name": "SECOND COMBO",
                        "item_type": "combo",
                        "combo_item_id": [str(self.tea.pk)],
                        "combo_qty": ["1"],
                    },
                    {},
                )
            row = build_shop_catalog_page(self.shop)
            self.assertNotIn(
                combo.pk, {r["id"] for r in row["items"]}
            )
            with self.assertRaises(ValidationError):
                self._checkout(combo, qty="1", price="60.00")
        finally:
            set_company_pos_setting(field="enable_combo_items", enabled=True)

    def test_existing_combo_still_editable_while_disabled(self):
        from shops.services import set_company_pos_setting

        combo = self._make_combo()
        set_company_pos_setting(field="enable_combo_items", enabled=False)
        try:
            update_item(
                combo,
                {
                    "category": "COMBOS",
                    "name": "BREAKFAST COMBO",
                    "description": "",
                    "item_type": "combo",
                    "combo_item_id": [str(self.tea.pk), str(self.mandazi.pk)],
                    "combo_qty": ["0.6", "0.4"],
                },
                {},
            )
        finally:
            set_company_pos_setting(field="enable_combo_items", enabled=True)
        combo.refresh_from_db()
        self.assertTrue(combo.is_combo)
        qtys = {leg.component_id: leg.qty
                  for leg in combo.combo_components.all()}
        self.assertEqual(qtys[self.tea.pk], Decimal("0.6000"))

    def test_quotation_lists_components_without_moving_stock(self):
        combo = self._make_combo()
        result = complete_shop_checkout(
            shop=self.shop,
            profile=self.profile,
            payload={
                "kind": ShopReceiptKind.QUOTATION,
                "client_name": "JANE DOE",
                "login_code": "770099",
                "lines": [{"id": combo.pk, "qty": "1", "price": "85.00"}],
            },
        )
        self.assertEqual(result["receipt"].lines.count(), 2)
        self.assertEqual(self._shop_qty(self.tea), Decimal("20.000"))
