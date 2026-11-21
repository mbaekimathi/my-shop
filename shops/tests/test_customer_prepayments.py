"""Customer prepayments — service, role pages, and cashier day-report inclusion."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from employees.models import (
    EmployeeModulePermission,
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
)
from shops.models import CustomerPrepayment, Shop, ShopPaymentMethod
from shops.services import (
    _invalidate_pos_settings_cache,
    day_session_balance_summary,
    day_session_report_details,
    get_open_shop_day,
    open_shop_day,
    register_customer_prepayment,
)


def _employee(username, *, role, first_name, last_name, password):
    user = User.objects.create_user(
        username=username,
        password=password,
        email=f"{username}@test.local",
        first_name=first_name,
        last_name=last_name,
        is_active=True,
    )
    return EmployeeProfile.objects.create(
        user=user,
        employee_id=username,
        phone_country_code="+254",
        phone_number="700002001",
        status=EmployeeStatus.ACTIVE,
        role=role,
    )


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    RATE_LIMITS={
        "login": {"max": 10_000, "window": 60},
        "check_employee_id": {"max": 10_000, "window": 60},
        "register": {"max": 10_000, "window": 60},
        "sync": {"max": 10_000, "window": 60},
    },
)
class CustomerPrepaymentTests(TestCase):
    PASSWORD = "prepayment-pass"

    def setUp(self):
        _invalidate_pos_settings_cache()
        self.cashier = _employee(
            "880101",
            role=EmployeeRole.SHOP_CASHIER,
            first_name="SHOP",
            last_name="CASHIER",
            password=self.PASSWORD,
        )
        self.accountant = _employee(
            "880102",
            role=EmployeeRole.COMPANY_ACCOUNTANT,
            first_name="COMPANY",
            last_name="ACCOUNTANT",
            password=self.PASSWORD,
        )
        self.shop = Shop.objects.create(
            name="PREPAY SHOP",
            location="NAIROBI",
            email="prepay-shop@test.local",
            phone_number="0700088101",
            login_code="880100",
            password_hash=make_password(self.PASSWORD),
            created_by=self.cashier,
        )
        self.other_shop = Shop.objects.create(
            name="OTHER SHOP",
            location="MOMBASA",
            email="other-shop@test.local",
            phone_number="0700088102",
            login_code="880199",
            password_hash=make_password(self.PASSWORD),
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)

    def _login(self, profile):
        client = Client()
        response = client.post(
            reverse("employees:login"),
            {"username": profile.employee_id, "password": self.PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _prepayments_url(self, role):
        from employees.access import role_url_segment

        return reverse(
            "employees:role_prepayments",
            kwargs={"role_segment": role_url_segment(role)},
        )

    def _payload(self, shop_id, **extra):
        payload = {
            "shop_id": str(shop_id),
            "customer_name": "Jane Wanjiku",
            "customer_phone": "0712345678",
            "purpose": "Sofa set order",
            "amount": "250",
            "payment_method": ShopPaymentMethod.CASH,
        }
        payload.update(extra)
        return payload

    def test_service_creates_prepayment(self):
        result = register_customer_prepayment(
            shop=self.shop,
            profile=self.cashier,
            payload=self._payload(self.shop.pk),
        )
        row = result["prepayment"]
        self.assertEqual(row.customer_name, "JANE WANJIKU")
        self.assertEqual(row.customer_phone, "+254712345678")
        self.assertEqual(row.amount, Decimal("250"))
        self.assertEqual(row.cash_amount, Decimal("250"))
        self.assertEqual(row.mpesa_amount, Decimal("0"))
        self.assertEqual(row.created_by, self.cashier)
        self.assertEqual(row.shop, self.shop)

    def test_service_mpesa_channels_amount(self):
        row = register_customer_prepayment(
            shop=self.shop,
            profile=self.cashier,
            payload=self._payload(
                self.shop.pk,
                amount="400",
                payment_method=ShopPaymentMethod.MPESA,
            ),
        )["prepayment"]
        self.assertEqual(row.mpesa_amount, Decimal("400"))
        self.assertEqual(row.cash_amount, Decimal("0"))

    def test_service_requires_name_phone_purpose_amount(self):
        for field in ("customer_name", "customer_phone", "purpose", "amount"):
            payload = self._payload(self.shop.pk)
            payload[field] = ""
            with self.assertRaises(Exception, msg=field):
                register_customer_prepayment(
                    shop=self.shop, profile=self.cashier, payload=payload
                )

    def test_service_rejects_zero_amount(self):
        with self.assertRaises(Exception):
            register_customer_prepayment(
                shop=self.shop,
                profile=self.cashier,
                payload=self._payload(self.shop.pk, amount="0"),
            )

    def test_balance_summary_counts_prepayments_in_till(self):
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "500",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": self.cashier.employee_id,
            },
        )
        session = get_open_shop_day(self.shop)
        register_customer_prepayment(
            shop=self.shop,
            profile=self.cashier,
            payload=self._payload(self.shop.pk, amount="300"),
        )
        register_customer_prepayment(
            shop=self.shop,
            profile=self.cashier,
            payload=self._payload(
                self.shop.pk,
                amount="200",
                payment_method=ShopPaymentMethod.MPESA,
            ),
        )
        summary = day_session_balance_summary(session)
        self.assertEqual(summary["prepayments_cash"], Decimal("300"))
        self.assertEqual(summary["prepayments_mpesa"], Decimal("200"))
        self.assertEqual(summary["prepayments_total"], Decimal("500"))
        self.assertEqual(summary["prepayments_count"], 2)
        # 1000 opening + 300 cash prepayment (no sales).
        self.assertEqual(summary["expected_cash"], Decimal("1300"))
        self.assertEqual(summary["expected_mpesa"], Decimal("700"))

    def test_report_details_lists_prepayments(self):
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": self.cashier.employee_id,
            },
        )
        session = get_open_shop_day(self.shop)
        register_customer_prepayment(
            shop=self.shop,
            profile=self.cashier,
            payload=self._payload(self.shop.pk),
        )
        details = day_session_report_details(session)
        rows = details.get("prepayments") or []
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer_name"], "JANE WANJIKU")
        self.assertEqual(rows[0]["purpose"], "Sofa set order")
        self.assertEqual(rows[0]["amount"], Decimal("250"))

    def test_cashier_post_registers_and_list_shows_row(self):
        client = self._login(self.cashier)
        url = self._prepayments_url(self.cashier.role)
        response = client.post(url, self._payload(self.shop.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CustomerPrepayment.objects.count(), 1)

        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "JANE WANJIKU")
        self.assertContains(response, "Sofa set order")

    def test_accountant_post_registers(self):
        client = self._login(self.accountant)
        url = self._prepayments_url(self.accountant.role)
        response = client.post(
            url, self._payload(self.shop.pk, amount="999")
        )
        self.assertEqual(response.status_code, 302)
        row = CustomerPrepayment.objects.get()
        self.assertEqual(row.amount, Decimal("999"))
        self.assertEqual(row.created_by, self.accountant)

    def test_cashier_cannot_register_for_unassigned_shop(self):
        client = self._login(self.cashier)
        url = self._prepayments_url(self.cashier.role)
        response = client.post(url, self._payload(self.other_shop.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CustomerPrepayment.objects.count(), 0)

    def test_denied_permission_blocks_page_and_post(self):
        EmployeeModulePermission.objects.create(
            employee=self.cashier,
            module_slug="my-shop",
            submodule_slug="prepayments",
            allowed=False,
        )
        self.cashier.__dict__.pop("_employee_module_perm_cache", None)
        client = self._login(self.cashier)
        url = self._prepayments_url(self.cashier.role)
        response = client.get(url)
        self.assertIn(response.status_code, (302, 403))
        response = client.post(url, self._payload(self.shop.pk))
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(CustomerPrepayment.objects.count(), 0)

    def test_cashier_register_action_lives_on_prepayments_page(self):
        client = self._login(self.cashier)
        dashboard = client.get(reverse("employees:role_shop_cashier"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, 'data-modal-open="prepayment"')

        response = client.get(self._prepayments_url(self.cashier.role))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-modal="prepayment"')
        self.assertContains(response, 'data-modal-open="prepayment"')

    def test_accountant_register_action_lives_on_prepayments_page(self):
        client = self._login(self.accountant)
        dashboard = client.get(reverse("employees:role_company_accountant"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, 'data-modal-open="prepayment"')

        response = client.get(self._prepayments_url(self.accountant.role))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-modal="prepayment"')
        self.assertContains(response, 'data-modal-open="prepayment"')
