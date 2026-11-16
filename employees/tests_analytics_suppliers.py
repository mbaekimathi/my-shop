from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from employees.analytics_services import build_analytics_page
from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import (
    Item,
    ShelfItem,
    ShelfStockMovement,
    ShelfStockMovementType,
    StockMovement,
    StockMovementLine,
    StockMovementType,
    StockPaymentStatus,
    Supplier,
)
from shops.models import (
    CompanyPosSettings,
    Expense,
    ExpenseSupplier,
    Shop,
)


class SuppliersAnalyticsShelfTests(TestCase):
    def setUp(self):
        from shops.services import (
            _invalidate_pos_settings_cache,
            set_company_pos_setting,
        )

        self.user = User.objects.create_user(
            username="870701", password="x", is_active=True
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870701",
            phone_country_code="+254",
            phone_number="700001177",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="SUP SHOP",
            location="NAIROBI",
            email="sup@test.local",
            phone_number="0700001177",
            login_code="870177",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        set_company_pos_setting(field="stock_mode_items", enabled=True)
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)

        self.supplier = Supplier.objects.create(
            name="BROOKSIDE",
            phone_country_code="+254",
            phone_number="722000000",
        )
        self.item = Item.objects.create(
            category="GEN",
            name="Milk 500ml",
            minimum_selling_price=Decimal("40"),
            shop_price=Decimal("60"),
            created_by=self.staff,
        )
        movement = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            shop=self.shop,
            payment_status=StockPaymentStatus.UNPAID,
            amount_paid=Decimal("0"),
            created_by=self.staff,
        )
        StockMovementLine.objects.create(
            movement=movement,
            item=self.item,
            quantity=Decimal("10"),
            buying_price=Decimal("30"),
            payment_status=StockPaymentStatus.UNPAID,
            supplier_name="BROOKSIDE",
            supplier_phone_country_code="+254",
            supplier_phone_number="722000000",
        )
        self.shelf = ShelfItem.objects.create(
            shop=self.shop, name="Gunny bags", quantity=Decimal("0")
        )
        self.shelf_movement = ShelfStockMovement.objects.create(
            shelf_item=self.shelf,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("7"),
            balance_after=Decimal("7"),
            supplier_name="KENAFRIC",
            supplier_phone="0733111222",
            payment_status="unpaid",
            buying_price=Decimal("14"),
            created_by=self.staff,
        )

    def _page(self, **params):
        request = RequestFactory().get(
            "/shop-manager/analytics/suppliers/",
            {"range": "all", **params},
        )
        request.user = self.user
        return build_analytics_page(
            profile=self.staff, request=request, section_slug="suppliers"
        )["page"]

    def _labels(self, table):
        labels = []
        for row in table["rows"]:
            cells = row["cells"] if isinstance(row, dict) else row
            first = cells[0]
            labels.append(
                first["label"] if isinstance(first, dict) else first
            )
        return labels

    def test_items_mode_shows_item_supplier_table_only(self):
        page = self._page()
        self.assertEqual(len(page["tables"]), 1)
        table = page["tables"][0]
        self.assertEqual(table["title"], "Suppliers by shop")
        labels = self._labels(table)
        self.assertIn("BROOKSIDE", labels)
        self.assertNotIn("KENAFRIC", labels)

    def test_both_mode_merges_into_one_table(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        page = self._page()
        self.assertEqual(len(page["tables"]), 1)
        table = page["tables"][0]
        self.assertEqual(table["title"], "Suppliers by shop")
        labels = self._labels(table)
        self.assertIn("BROOKSIDE", labels)
        self.assertIn("KENAFRIC", labels)
        # Shelf balance: 7 x 14 = 98 unpaid.
        summary = page["summary_board"]
        self.assertIn("398.00", summary["hero"]["value"])  # 300 + 98

    def test_view_tabs_split_supplier_tables(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        items_page = self._page(view="items")
        self.assertEqual(len(items_page["tables"]), 1)
        items_table = items_page["tables"][0]
        self.assertEqual(items_table["title"], "Item suppliers by shop")
        self.assertIn("BROOKSIDE", self._labels(items_table))
        self.assertNotIn("KENAFRIC", self._labels(items_table))

        shelf_page = self._page(view="shelf")
        self.assertEqual(len(shelf_page["tables"]), 1)
        shelf_table = shelf_page["tables"][0]
        self.assertEqual(shelf_table["title"], "Shelf suppliers by shop")
        self.assertIn("KENAFRIC", self._labels(shelf_table))
        self.assertNotIn("BROOKSIDE", self._labels(shelf_table))

    def test_same_identity_merges_into_one_supplier_row(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_both", enabled=True)
        # Shelf stock-in for BROOKSIDE under the local phone format — the
        # item supplier's +254 722… normalizes to the same identity.
        ShelfStockMovement.objects.create(
            shelf_item=self.shelf,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("3"),
            balance_after=Decimal("3"),
            supplier_name="BROOKSIDE",
            supplier_phone="0722000000",
            payment_status="unpaid",
            buying_price=Decimal("10"),
            created_by=self.staff,
        )
        page = self._page()
        labels = self._labels(page["tables"][0])
        self.assertEqual(labels.count("BROOKSIDE"), 1)
        self.assertIn("KENAFRIC", labels)
        # 300 item + 30 shelf + 98 KENAFRIC unpaid.
        self.assertIn("428.00", page["summary_board"]["hero"]["value"])
        # The merged row links to the Supplier's stock account (which now
        # lists both item and shelf receipts).
        brookside = [
            row
            for row in page["tables"][0]["rows"]
            for cells in [row["cells"] if isinstance(row, dict) else row]
            if isinstance(cells[0], dict)
            and cells[0].get("label") == "BROOKSIDE"
        ][0]
        cell = (brookside["cells"] if isinstance(brookside, dict) else brookside)[0]
        self.assertIn(
            f"/analytics/suppliers/stock/{self.supplier.pk}/", cell["href"]
        )

    def test_shelf_only_mode_shows_shelf_supplier(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        page = self._page()
        self.assertEqual(len(page["tables"]), 1)
        table = page["tables"][0]
        self.assertEqual(table["title"], "Suppliers by shop")
        labels = self._labels(table)
        self.assertIn("KENAFRIC", labels)
        self.assertNotIn("BROOKSIDE", labels)
        # Paid filter hides the unpaid shelf supplier.
        paid_page = self._page(status="paid")
        self.assertNotIn("KENAFRIC", self._labels(paid_page["tables"][0]))

    def test_shelf_supplier_cell_links_to_account(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        page = self._page()
        hrefs = {
            cells[0].get("href")
            for row in page["tables"][0]["rows"]
            for cells in [row["cells"] if isinstance(row, dict) else row]
            if isinstance(cells[0], dict)
        }
        hrefs.discard(None)
        self.assertTrue(hrefs)
        for href in hrefs:
            self.assertIn("/analytics/suppliers/shelf/", href)
        self.assertTrue(
            any("name=KENAFRIC" in href for href in hrefs)
        )

    def test_shelf_supplier_account_page_lists_receipts(self):
        from shops.services import set_company_pos_setting

        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        self.client.force_login(self.user)
        response = self.client.get(
            "/shop-manager/analytics/suppliers/shelf/",
            {"name": "KENAFRIC", "phone": "0733111222"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "KENAFRIC")
        self.assertContains(response, "0733111222")
        self.assertContains(response, "Gunny bags")
        self.assertContains(response, "Shelf supplier")
        self.assertContains(response, "98.00")
        # Shelf receipts are payable like stock receipts.
        self.assertContains(response, "Pay all")
        self.assertContains(response, 'data-ax-pay-open')
        self.assertContains(response, 'value="shelf"')

    def test_shelf_supplier_account_404_for_unknown_name(self):
        self.client.force_login(self.user)
        response = self.client.get(
            "/shop-manager/analytics/suppliers/shelf/",
            {"name": "NOBODY", "phone": "0700000000"},
        )
        self.assertEqual(response.status_code, 404)


class ShelfSupplierAccountPayTests(TestCase):
    def setUp(self):
        from shops.services import (
            _invalidate_pos_settings_cache,
            set_company_pos_setting,
        )

        self.user = User.objects.create_user(
            username="870702", password="x", is_active=True
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870702",
            phone_country_code="+254",
            phone_number="700001178",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="PAY SHOP",
            location="NAIROBI",
            email="pay@test.local",
            phone_number="0700001178",
            login_code="870178",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        set_company_pos_setting(field="stock_mode_shelf", enabled=True)
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)

        self.shelf = ShelfItem.objects.create(
            shop=self.shop, name="Gunny bags", quantity=Decimal("0")
        )
        self.movement = ShelfStockMovement.objects.create(
            shelf_item=self.shelf,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("7"),
            balance_after=Decimal("7"),
            supplier_name="KIM",
            supplier_phone="0795606115",
            payment_status="unpaid",
            buying_price=Decimal("14"),
            created_by=self.staff,
        )
        self.client.force_login(self.user)

    def _pay(self, **data):
        payload = {
            "kind": "shelf",
            "account_id": "0",
            "supplier_name": "KIM",
            "supplier_phone": "0795606115",
            "amount": "0",
            "payment_method": "cash",
            "shop_id": [str(self.shop.pk)],
        }
        payload.update(data)
        return self.client.post("/shop-manager/analytics/account-pay/", payload)

    def test_partial_receipt_payment(self):
        # Receipt total: 7 x 14 = 98.
        response = self._pay(
            receipt_id=str(self.movement.pk), amount="50"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt_status"], "Partial")
        self.assertEqual(data["receipt_due_raw"], "48.00")
        self.assertEqual(data["account_balance_raw"], "48.00")
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.amount_paid, Decimal("50.00"))
        self.assertEqual(self.movement.payment_status, "partial")

    def test_full_receipt_payment(self):
        response = self._pay(
            receipt_id=str(self.movement.pk), amount="98"
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt_status"], "Paid")
        self.assertEqual(data["receipt_due_raw"], "0.00")
        self.assertEqual(data["account_balance_raw"], "0.00")
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.payment_status, "paid")
        # A second pay attempt on a settled receipt is rejected.
        again = self._pay(receipt_id=str(self.movement.pk), amount="1")
        self.assertEqual(again.status_code, 400)

    def test_overpayment_rejected(self):
        response = self._pay(
            receipt_id=str(self.movement.pk), amount="98.01"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds", response.json()["error"])
        self.movement.refresh_from_db()
        self.assertEqual(self.movement.amount_paid, Decimal("0"))

    def test_receipt_out_of_scope_rejected(self):
        other_shop = Shop.objects.create(
            name="OTHER SHOP",
            location="MOMBASA",
            email="other@test.local",
            phone_number="0700001179",
            login_code="870179",
            password_hash="x",
            created_by=self.staff,
        )
        other_item = ShelfItem.objects.create(
            shop=other_shop, name="Crates", quantity=Decimal("0")
        )
        other_movement = ShelfStockMovement.objects.create(
            shelf_item=other_item,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("2"),
            balance_after=Decimal("2"),
            supplier_name="KIM",
            supplier_phone="0795606115",
            payment_status="unpaid",
            buying_price=Decimal("10"),
            created_by=self.staff,
        )
        response = self._pay(
            receipt_id=str(other_movement.pk), amount="10"
        )
        self.assertEqual(response.status_code, 400)

    def test_pay_all_fifo(self):
        second = ShelfStockMovement.objects.create(
            shelf_item=self.shelf,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("4"),
            balance_after=Decimal("4"),
            supplier_name="KIM",
            supplier_phone="0795606115",
            payment_status="unpaid",
            buying_price=Decimal("10"),
            created_by=self.staff,
        )
        # Totals: 98 (oldest) + 40. Pay 110 → first cleared, second partial 28.
        response = self._pay(amount="110")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["cleared"], 2)
        self.assertEqual(data["account_balance_raw"], "28.00")
        self.movement.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.movement.payment_status, "paid")
        self.assertEqual(second.amount_paid, Decimal("12.00"))
        self.assertEqual(second.payment_status, "partial")

    def test_pay_all_requires_supplier_name(self):
        payload = {
            "kind": "shelf",
            "account_id": "0",
            "amount": "10",
            "shop_id": [str(self.shop.pk)],
        }
        response = self.client.post(
            "/shop-manager/analytics/account-pay/", payload
        )
        self.assertEqual(response.status_code, 400)


class MergedSupplierAccountTests(TestCase):
    """Item + shelf activity under one supplier identity shares an account."""

    def setUp(self):
        from shops.services import (
            _invalidate_pos_settings_cache,
            set_company_pos_setting,
        )

        self.user = User.objects.create_user(
            username="870703", password="x", is_active=True
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870703",
            phone_country_code="+254",
            phone_number="700001179",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_MANAGER,
        )
        self.shop = Shop.objects.create(
            name="MERGE SHOP",
            location="NAKURU",
            email="merge@test.local",
            phone_number="0700001180",
            login_code="870180",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        set_company_pos_setting(field="stock_mode_both", enabled=True)
        _invalidate_pos_settings_cache()
        self.addCleanup(_invalidate_pos_settings_cache)

        self.supplier = Supplier.objects.create(
            name="KIM",
            phone_country_code="+254",
            phone_number="795606115",
        )
        self.item = Item.objects.create(
            category="GEN",
            name="Sugar 1kg",
            minimum_selling_price=Decimal("80"),
            shop_price=Decimal("120"),
            created_by=self.staff,
        )
        # Item stock-in: 10 x 10 = 100 unpaid. Created first → oldest.
        self.item_movement = StockMovement.objects.create(
            movement_type=StockMovementType.IN,
            shop=self.shop,
            payment_status=StockPaymentStatus.UNPAID,
            amount_paid=Decimal("0"),
            created_by=self.staff,
        )
        StockMovementLine.objects.create(
            movement=self.item_movement,
            item=self.item,
            quantity=Decimal("10"),
            buying_price=Decimal("10"),
            payment_status=StockPaymentStatus.UNPAID,
            supplier_name="KIM",
            supplier_phone_country_code="+254",
            supplier_phone_number="795606115",
        )
        self.shelf = ShelfItem.objects.create(
            shop=self.shop, name="Gunny bags", quantity=Decimal("0")
        )
        # Shelf stock-in: 7 x 14 = 98 unpaid — same supplier identity via
        # the local phone format (0795… normalizes to +254 795…).
        self.shelf_movement = ShelfStockMovement.objects.create(
            shelf_item=self.shelf,
            movement_type=ShelfStockMovementType.IN,
            quantity=Decimal("7"),
            balance_after=Decimal("7"),
            supplier_name="KIM",
            supplier_phone="0795606115",
            payment_status="unpaid",
            buying_price=Decimal("14"),
            created_by=self.staff,
        )
        self.client.force_login(self.user)

    def test_stock_account_lists_item_and_shelf_receipts(self):
        response = self.client.get(
            f"/shop-manager/analytics/suppliers/stock/{self.supplier.pk}/",
            {"range": "all"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sugar 1kg")
        self.assertContains(response, "Gunny bags")
        self.assertContains(response, "Item stock")
        self.assertContains(response, "Shelf stock")
        self.assertContains(response, "198.00")

    def test_shelf_account_lists_item_and_shelf_receipts(self):
        response = self.client.get(
            "/shop-manager/analytics/suppliers/shelf/",
            {
                "name": "KIM",
                "phone": "0795606115",
                "range": "all",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gunny bags")
        self.assertContains(response, "Sugar 1kg")
        self.assertContains(response, "198.00")

    def test_stock_account_pay_all_clears_shelf_too(self):
        response = self.client.post(
            "/shop-manager/analytics/account-pay/",
            {
                "kind": "stock",
                "account_id": str(self.supplier.pk),
                "amount": "198",
                "payment_method": "cash",
                "shop_id": [str(self.shop.pk)],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["account_balance_raw"], "0.00")
        self.item_movement.refresh_from_db()
        self.shelf_movement.refresh_from_db()
        self.assertEqual(self.item_movement.payment_status, "paid")
        self.assertEqual(self.shelf_movement.payment_status, "paid")
        self.assertEqual(self.shelf_movement.amount_paid, Decimal("98.00"))

    def test_stock_account_pay_all_fifo_across_kinds(self):
        # 150 → item receipt (oldest) cleared, shelf receipt gets 50.
        response = self.client.post(
            "/shop-manager/analytics/account-pay/",
            {
                "kind": "stock",
                "account_id": str(self.supplier.pk),
                "amount": "150",
                "payment_method": "cash",
                "shop_id": [str(self.shop.pk)],
            },
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["account_balance_raw"], "48.00")
        self.item_movement.refresh_from_db()
        self.shelf_movement.refresh_from_db()
        self.assertEqual(self.item_movement.payment_status, "paid")
        self.assertEqual(self.shelf_movement.amount_paid, Decimal("50.00"))
        self.assertEqual(self.shelf_movement.payment_status, "partial")

    def test_shelf_account_pay_all_clears_item_too(self):
        response = self.client.post(
            "/shop-manager/analytics/account-pay/",
            {
                "kind": "shelf",
                "account_id": "0",
                "supplier_name": "KIM",
                "supplier_phone": "0795606115",
                "amount": "198",
                "payment_method": "cash",
                "shop_id": [str(self.shop.pk)],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["account_balance_raw"], "0.00")
        self.item_movement.refresh_from_db()
        self.shelf_movement.refresh_from_db()
        self.assertEqual(self.item_movement.payment_status, "paid")
        self.assertEqual(self.shelf_movement.payment_status, "paid")

    def test_shelf_account_pays_item_receipt_via_receipt_kind(self):
        # Per-receipt Pay on an item row inside the merged shelf account.
        response = self.client.post(
            "/shop-manager/analytics/account-pay/",
            {
                "kind": "shelf",
                "receipt_kind": "stock",
                "account_id": "0",
                "supplier_name": "KIM",
                "supplier_phone": "0795606115",
                "receipt_id": str(self.item_movement.pk),
                "amount": "40",
                "payment_method": "cash",
                "shop_id": [str(self.shop.pk)],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt_due_raw"], "60.00")
        self.item_movement.refresh_from_db()
        self.assertEqual(self.item_movement.amount_paid, Decimal("40.00"))
        self.assertEqual(self.item_movement.payment_status, "partial")


class ExpenseReceiptPayTests(TestCase):
    """Per-expense Pay on the analytics expenses category page."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="870705", password="x", is_active=True
        )
        self.staff = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="870705",
            phone_country_code="+254",
            phone_number="700001181",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="EXP SHOP",
            location="NAIROBI",
            email="exp@test.local",
            phone_number="0700001181",
            login_code="870181",
            password_hash="x",
            created_by=self.staff,
        )
        self.staff.assigned_shops.add(self.shop)
        self.supplier = ExpenseSupplier.objects.create(
            name="PACKCO",
            phone_country_code="+254",
            phone_number="722111222",
        )
        self.expense = Expense.objects.create(
            shop=self.shop,
            category="packaging",
            name="BOXES",
            amount=Decimal("100.00"),
            amount_paid=Decimal("0"),
            payment_status="unpaid",
            supplier=self.supplier,
            supplier_name="PACKCO",
            supplier_phone_country_code="+254",
            supplier_phone_number="722111222",
            created_by=self.staff,
        )
        self.client.force_login(self.user)

    def _pay(self, **data):
        payload = {
            "kind": "expense",
            "account_id": str(self.supplier.pk),
            "amount": "0",
            "payment_method": "cash",
            "shop_id": [str(self.shop.pk)],
        }
        payload.update(data)
        return self.client.post("/shop-cashier/analytics/account-pay/", payload)

    def test_partial_receipt_payment(self):
        response = self._pay(receipt_id=str(self.expense.pk), amount="40")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt_status"], "Partial")
        self.assertEqual(data["receipt_due_raw"], "60.00")
        self.assertEqual(data["account_balance_raw"], "60.00")
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount_paid, Decimal("40.00"))
        self.assertEqual(self.expense.payment_status, "partial")

    def test_full_receipt_payment(self):
        response = self._pay(receipt_id=str(self.expense.pk), amount="100")
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt_status"], "Paid")
        self.assertEqual(data["receipt_due_raw"], "0.00")
        self.assertEqual(data["account_balance_raw"], "0.00")
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.payment_status, "paid")
        # A second pay attempt on a settled expense is rejected.
        again = self._pay(receipt_id=str(self.expense.pk), amount="1")
        self.assertEqual(again.status_code, 400)

    def test_overpayment_rejected(self):
        response = self._pay(receipt_id=str(self.expense.pk), amount="100.01")
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds", response.json()["error"])
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount_paid, Decimal("0"))

    def test_wrong_supplier_rejected(self):
        other = ExpenseSupplier.objects.create(
            name="OTHERCO",
            phone_country_code="+254",
            phone_number="733000111",
        )
        response = self._pay(
            account_id=str(other.pk),
            receipt_id=str(self.expense.pk),
            amount="10",
        )
        self.assertEqual(response.status_code, 400)

    def test_unlinked_expense_payable(self):
        loose = Expense.objects.create(
            shop=self.shop,
            category="packaging",
            name="TAPE",
            amount=Decimal("25.00"),
            amount_paid=Decimal("0"),
            payment_status="unpaid",
            supplier=None,
            supplier_name="WALK-IN",
            supplier_phone_country_code="+254",
            supplier_phone_number="700000000",
            created_by=self.staff,
        )
        response = self._pay(
            account_id="0",
            receipt_id=str(loose.pk),
            amount="25",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["receipt_status"], "Paid")
        loose.refresh_from_db()
        self.assertEqual(loose.payment_status, "paid")

    def test_category_page_shows_pay_controls(self):
        response = self.client.get(
            "/shop-cashier/analytics/expenses/?category=packaging"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-ax-pay-open')
        self.assertContains(response, 'data-ax-pay-modal')
        self.assertContains(
            response, f'data-receipt-id="{self.expense.pk}"'
        )
        self.assertContains(
            response, f'data-account-id="{self.supplier.pk}"'
        )
        self.assertContains(response, "analytics-receipt-ledger.js")

    def test_category_page_hides_pay_without_permission(self):
        from employees.models import EmployeeModulePermission

        EmployeeModulePermission.objects.create(
            employee=self.staff,
            module_slug="analytics",
            submodule_slug="account_pay",
            allowed=False,
        )
        response = self.client.get(
            "/shop-cashier/analytics/expenses/?category=packaging"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-ax-pay-open')
        self.assertNotContains(response, 'data-ax-pay-modal')
