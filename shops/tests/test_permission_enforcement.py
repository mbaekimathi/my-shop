"""Toggled-off permission matrix rows must block the activity, not just hide it.

Each test flips one ``EmployeeModulePermission`` row to ``allowed=False`` and
proves the matching page / API / service refuses the action on surfaces that
previously skipped the matrix: the POS sales API, the offline-sync sale replay,
the Company Store Manager central-store pages, and the floor stock-take submit.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from employees.models import (
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)
from items.models import Item, StoreStockMovement
from shops.models import (
    Shop,
    ShopPaymentMethod,
    ShopReceipt,
    ShopReceiptKind,
    ShopReceiptLine,
    ShopReceiptStatus,
)


def _profile(*, employee_id, role):
    user = User.objects.create_user(
        username=f"perm-{employee_id}",
        password="pass",
        is_active=True,
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=employee_id,
        phone_country_code="+254",
        phone_number="700000000",
        status=EmployeeStatus.ACTIVE,
        role=role,
    )


def _deny(profile, module, submodule):
    return EmployeeModulePermission.objects.create(
        employee=profile,
        module_slug=module,
        submodule_slug=submodule,
        allowed=False,
    )


def _shop(employee_id, profile):
    return Shop.objects.create(
        name=f"Perm Shop {employee_id}",
        location="Nairobi",
        email=f"perm-shop-{employee_id}@test.local",
        phone_number="0700000000",
        login_code=employee_id,
        password_hash=make_password("pass"),
        created_by=profile,
    )


class PosSaleApiPermissionTests(TestCase):
    """``/pos/api/sales/`` must refuse when ``my-shop/sale`` is toggled off."""

    def setUp(self):
        self.profile = _profile(
            employee_id="410001", role=EmployeeRole.SHOP_CASHIER
        )
        self.client.force_login(self.profile.user)
        self.url = reverse("pos:sale_create")

    def test_sale_api_denied_when_toggled_off(self):
        _deny(self.profile, "my-shop", "sale")
        resp = self.client.post(
            self.url,
            data=json.dumps({"lines": []}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])

    def test_sale_api_role_check_still_applies(self):
        employee = _profile(employee_id="410002", role=EmployeeRole.EMPLOYEE)
        self.client.force_login(employee.user)
        resp = self.client.post(
            self.url,
            data=json.dumps({"lines": []}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)


class SyncCreateSalePermissionTests(TestCase):
    """Offline sale replay honours the same ``my-shop/sale`` toggle."""

    def test_sync_sale_denied_when_toggled_off(self):
        from employees.sync_handlers import (
            SyncOperationError,
            _sync_create_sale,
        )

        profile = _profile(
            employee_id="410003", role=EmployeeRole.SHOP_CASHIER
        )
        _deny(profile, "my-shop", "sale")
        with self.assertRaises(SyncOperationError) as ctx:
            _sync_create_sale(profile, {"lines": []})
        self.assertIn("permission", str(ctx.exception))


class StoreStockPermissionTests(TestCase):
    """Company Store Manager pages must follow ``stock-management/*`` toggles."""

    def setUp(self):
        self.profile = _profile(
            employee_id="410004", role=EmployeeRole.COMPANY_STORE_MANAGER
        )
        self.client.force_login(self.profile.user)
        self.item = Item.objects.create(
            name="Perm Item",
            category="Parts",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=10,
            track_serial_number=False,
            created_by=self.profile,
        )

    def test_stock_in_page_denied_when_toggled_off(self):
        _deny(self.profile, "stock-management", "in")
        resp = self.client.get(reverse("employees:store_stock_in"))
        self.assertEqual(resp.status_code, 302)

    def test_stock_in_post_denied_when_toggled_off(self):
        _deny(self.profile, "stock-management", "in")
        resp = self.client.post(
            reverse("employees:store_stock_in"),
            {f"qty_{self.item.pk}": "5"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(StoreStockMovement.objects.count(), 0)

    def test_stock_in_page_allowed_by_default(self):
        resp = self.client.get(reverse("employees:store_stock_in"))
        self.assertEqual(resp.status_code, 200)

    def test_items_page_denied_when_view_toggled_off(self):
        _deny(self.profile, "stock-management", "view")
        resp = self.client.get(reverse("employees:store_stock_items"))
        self.assertEqual(resp.status_code, 302)

    def test_report_denied_when_toggled_off(self):
        _deny(self.profile, "stock-management", "report")
        resp = self.client.get(reverse("employees:store_stock_report"))
        self.assertEqual(resp.status_code, 302)

    def test_item_search_denied_when_all_stock_off(self):
        for key in ("view", "in", "out"):
            _deny(self.profile, "stock-management", key)
        resp = self.client.get(
            reverse("employees:store_stock_item_search") + "?q=perm"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])


class StockTakeSubmitPermissionTests(TestCase):
    """Floor stock-take submit must follow ``my-shop/stock_count``."""

    def setUp(self):
        self.profile = _profile(
            employee_id="410005", role=EmployeeRole.SHOP_CASHIER
        )
        self.shop = _shop("410005", self.profile)
        self.client.force_login(self.profile.user)
        session = self.client.session
        session["active_shop_id"] = str(self.shop.pk)
        session.save()
        self.url = reverse(
            "employees:my_shop_stock_take_submit",
            kwargs={"shop_id": self.shop.pk},
        )

    def test_submit_denied_when_stock_count_toggled_off(self):
        _deny(self.profile, "my-shop", "stock_count")
        resp = self.client.post(self.url, {"assignment_id": "1"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])

    def test_submit_reaches_stock_take_when_allowed(self):
        # No open assignment exists — the request passes the permission gate
        # and lands on the "No open stock take" branch instead of 403.
        resp = self.client.post(self.url, {"assignment_id": "1"})
        self.assertEqual(resp.status_code, 404)


class ReceiptReturnCancelPermissionTests(TestCase):
    """``analytics/return-receipts`` off ⇒ return AND cancel are refused.

    Cancel restocks the remaining lines, so it must follow the same
    "Return receipt" toggle everywhere it appears.
    """

    def setUp(self):
        self.profile = _profile(
            employee_id="410006", role=EmployeeRole.IT_SUPPORT
        )
        self.shop = _shop("410006", self.profile)
        self.client.force_login(self.profile.user)
        self.item = Item.objects.create(
            name="Perm Return Item",
            category="Parts",
            minimum_selling_price=Decimal("50.00"),
            shop_price=Decimal("100.00"),
            stock=3,
            created_by=self.profile,
        )
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="PERM-CANCEL-1",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("200.00"),
            total=Decimal("200.00"),
            amount_paid=Decimal("200.00"),
            cash_amount=Decimal("200.00"),
            created_by=self.profile,
            status=ShopReceiptStatus.CONFIRMED,
        )
        self.line = ShopReceiptLine.objects.create(
            receipt=self.receipt,
            item=self.item,
            item_name=self.item.name,
            quantity=2,
            unit_price=Decimal("100.00"),
            unit_cost=Decimal("60.00"),
            line_total=Decimal("200.00"),
            line_cogs=Decimal("120.00"),
        )

    def _url(self, name):
        return reverse(
            f"employees:{name}",
            kwargs={
                "role_segment": "it-support",
                "shop_id": self.shop.pk,
                "receipt_id": self.receipt.pk,
            },
        )

    def test_cancel_denied_when_return_receipts_toggled_off(self):
        _deny(self.profile, "analytics", "return-receipts")
        resp = self.client.post(self._url("analytics_receipt_cancel"))
        self.assertEqual(resp.status_code, 403)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ShopReceiptStatus.CONFIRMED)

    def test_set_status_cancelled_denied_when_return_receipts_off(self):
        _deny(self.profile, "analytics", "return-receipts")
        resp = self.client.post(
            self._url("analytics_receipt_set_status"), {"status": "cancelled"}
        )
        self.assertEqual(resp.status_code, 403)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ShopReceiptStatus.CONFIRMED)

    def test_set_status_pending_still_works_without_return_perm(self):
        # Confirm-side statuses are not return actions.
        _deny(self.profile, "analytics", "return-receipts")
        self.receipt.status = ShopReceiptStatus.ACTIVE
        self.receipt.save(update_fields=["status"])
        resp = self.client.post(
            self._url("analytics_receipt_set_status"), {"status": "confirmed"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_return_denied_for_viewer_when_return_receipts_off(self):
        _deny(self.profile, "analytics", "return-receipts")
        resp = self.client.post(
            self._url("analytics_receipt_return"),
            data=json.dumps(
                {
                    "login_code": self.profile.employee_id,
                    "reason": "Customer changed mind",
                    "lines": [{"line_id": self.line.pk, "qty": 1}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_return_denied_for_authoriser_when_my_shop_return_off(self):
        _deny(self.profile, "my-shop", "return_receipt")
        resp = self.client.post(
            self._url("analytics_receipt_return"),
            data=json.dumps(
                {
                    "login_code": self.profile.employee_id,
                    "reason": "Customer changed mind",
                    "lines": [{"line_id": self.line.pk, "qty": 1}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_cancel_service_denied_when_return_receipts_off(self):
        from django.core.exceptions import ValidationError

        from shops.services import cancel_pending_shop_receipt

        _deny(self.profile, "analytics", "return-receipts")
        with self.assertRaises(ValidationError):
            cancel_pending_shop_receipt(
                shop=self.shop, receipt_id=self.receipt.pk, actor=self.profile
            )
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ShopReceiptStatus.CONFIRMED)

    def test_cancel_allowed_by_default(self):
        resp = self.client.post(self._url("analytics_receipt_cancel"))
        self.assertEqual(resp.status_code, 200)
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, ShopReceiptStatus.CANCELLED)


class TradeCheckoutPermissionTests(TestCase):
    """Trade out/in checkouts must follow their own matrix keys."""

    def setUp(self):
        self.profile = _profile(
            employee_id="410007", role=EmployeeRole.SHOP_CASHIER
        )
        self.shop = _shop("410007", self.profile)
        self.client.force_login(self.profile.user)
        session = self.client.session
        session["active_shop_id"] = str(self.shop.pk)
        session.save()
        self.url = reverse(
            "employees:my_shop_checkout", kwargs={"shop_id": self.shop.pk}
        )

    def test_trade_out_denied_when_toggled_off(self):
        _deny(self.profile, "my-shop", "trade_out")
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "kind": "trade_out",
                    "login_code": self.profile.employee_id,
                    "lines": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_trade_in_denied_when_toggled_off(self):
        _deny(self.profile, "my-shop", "trade_in")
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "kind": "trade_in",
                    "login_code": self.profile.employee_id,
                    "lines": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)


class FloorReceiptReturnPermissionTests(TestCase):
    """Floor return endpoint must follow ``my-shop/return_receipt``."""

    def setUp(self):
        self.profile = _profile(
            employee_id="410008", role=EmployeeRole.SHOP_CASHIER
        )
        self.shop = _shop("410008", self.profile)
        self.profile.assigned_shops.add(self.shop)
        self.client.force_login(self.profile.user)
        session = self.client.session
        session["active_shop_id"] = str(self.shop.pk)
        session.save()
        self.receipt = ShopReceipt.objects.create(
            shop=self.shop,
            receipt_number="PERM-RET-1",
            kind=ShopReceiptKind.SALE,
            payment_method=ShopPaymentMethod.CASH,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            cash_amount=Decimal("100.00"),
            created_by=self.profile,
            status=ShopReceiptStatus.CONFIRMED,
        )
        self.url = reverse(
            "employees:my_shop_receipt_return",
            kwargs={"shop_id": self.shop.pk, "receipt_id": self.receipt.pk},
        )

    def test_floor_return_denied_when_toggled_off(self):
        _deny(self.profile, "my-shop", "return_receipt")
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "login_code": self.profile.employee_id,
                    "reason": "Changed mind",
                    "lines": [{"line_id": 1, "qty": 1}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
