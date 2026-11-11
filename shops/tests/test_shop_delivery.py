"""Customer delivery jobs from MY-SHOP checkout and rider accept exclusivity."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from items.models import Item, ShopStock
from shops.models import (
    Shop,
    ShopDelivery,
    ShopDeliveryStatus,
    ShopPaymentMethod,
    ShopReceiptKind,
)
from shops.services import (
    accept_shop_delivery,
    complete_shop_checkout,
    complete_shop_delivery,
    open_deliveries_for_rider,
    open_shop_day,
    rider_delivery_alert_payload,
)


class ShopDeliveryTests(TestCase):
    def setUp(self):
        from shops.services import _invalidate_pos_settings_cache

        _invalidate_pos_settings_cache()
        self.cashier_user = User.objects.create_user(
            username="330001",
            password="pass",
            email="cashier-deliv@test.local",
            first_name="CASH",
            last_name="IER",
            is_active=True,
        )
        self.cashier = EmployeeProfile.objects.create(
            user=self.cashier_user,
            employee_id="330001",
            phone_country_code="+254",
            phone_number="700003301",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.SHOP_CASHIER,
        )
        self.shop = Shop.objects.create(
            name="Delivery Shop",
            location="Nairobi",
            latitude=Decimal("-1.2863890"),
            longitude=Decimal("36.8172230"),
            email="delivery-shop@test.local",
            phone_number="0700003301",
            login_code="330001",
            password_hash=make_password("pass"),
            created_by=self.cashier,
        )
        self.cashier.assigned_shops.add(self.shop)
        self.item = Item.objects.create(
            name="Delivery Phone",
            category="Phones",
            minimum_selling_price=Decimal("1000.00"),
            shop_price=Decimal("1200.00"),
            stock=Decimal("5.000"),
            track_serial_number=False,
            created_by=self.cashier,
        )
        ShopStock.objects.create(
            shop=self.shop,
            item=self.item,
            quantity=Decimal("5.000"),
            average_cost=Decimal("800.00"),
        )
        open_shop_day(
            shop=self.shop,
            payload={
                "cash_amount": "1000",
                "mpesa_amount": "0",
                "credit_amount": "0",
                "stock_confirmed": True,
                "login_code": "330001",
            },
        )
        self.rider_a = self._make_rider("840101", "RIDE", "A", "700008401")
        self.rider_b = self._make_rider("840102", "RIDE", "B", "700008402")

    def _make_rider(self, employee_id, first, last, phone):
        user = User.objects.create_user(
            username=employee_id,
            password="pass",
            email=f"{employee_id}@test.local",
            first_name=first,
            last_name=last,
            is_active=True,
        )
        return EmployeeProfile.objects.create(
            user=user,
            employee_id=employee_id,
            phone_country_code="+254",
            phone_number=phone,
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.RIDER,
        )

    def _checkout(self, *, delivery=False, rider_ids=None, **extra):
        payload = {
            "kind": ShopReceiptKind.SALE,
            "payment_method": ShopPaymentMethod.CASH,
            "cash_amount": "1200",
            "mpesa_amount": "0",
            "login_code": "330001",
            "client_name": "CLIENT ONE",
            "client_phone": "0712345678",
            "lines": [{"id": self.item.pk, "qty": "1", "price": "1200"}],
        }
        if delivery:
            payload.update(
                {
                    "delivery": True,
                    "delivery_location": "Westlands, Nairobi",
                    "delivery_lat": "-1.2670000",
                    "delivery_lng": "36.8110000",
                    "delivery_maps_url": (
                        "https://www.google.com/maps/search/?api=1"
                        "&query=-1.267,36.811"
                    ),
                    "delivery_rider_ids": rider_ids or [],
                }
            )
        payload.update(extra)
        return complete_shop_checkout(
            shop=self.shop,
            profile=self.cashier,
            payload=payload,
        )

    def test_checkout_creates_open_delivery_for_any_rider(self):
        self._checkout(delivery=True)
        delivery = ShopDelivery.objects.get()
        self.assertEqual(delivery.status, ShopDeliveryStatus.OPEN)
        self.assertEqual(delivery.location_text, "Westlands, Nairobi")
        self.assertEqual(delivery.latitude, Decimal("-1.2670000"))
        self.assertEqual(delivery.longitude, Decimal("36.8110000"))
        self.assertIsNotNone(delivery.distance_km)
        self.assertGreater(delivery.distance_km, 0)
        self.assertIsNotNone(delivery.eta_minutes)
        self.assertGreaterEqual(delivery.eta_minutes, 5)
        self.assertFalse(delivery.assigned_riders.exists())
        self.assertEqual(len(open_deliveries_for_rider(self.rider_a)), 1)
        self.assertEqual(len(open_deliveries_for_rider(self.rider_b)), 1)

    def test_accept_sets_expected_complete_from_eta(self):
        self._checkout(
            delivery=True,
            delivery_distance_km="4.50",
            delivery_eta_minutes="18",
        )
        delivery = ShopDelivery.objects.get()
        self.assertEqual(delivery.distance_km, Decimal("4.50"))
        self.assertEqual(delivery.eta_minutes, 18)
        accept_shop_delivery(delivery_id=delivery.pk, rider=self.rider_a)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ShopDeliveryStatus.ACCEPTED)
        self.assertIsNotNone(delivery.expected_at)
        self.assertEqual(
            int((delivery.expected_at - delivery.accepted_at).total_seconds() // 60),
            18,
        )

    def test_assigned_riders_limit_open_board(self):
        self._checkout(delivery=True, rider_ids=[self.rider_a.pk])
        self.assertEqual(len(open_deliveries_for_rider(self.rider_a)), 1)
        self.assertEqual(len(open_deliveries_for_rider(self.rider_b)), 0)
        payload_a = rider_delivery_alert_payload(self.rider_a)
        payload_b = rider_delivery_alert_payload(self.rider_b)
        self.assertEqual(payload_a["open_count"], 1)
        self.assertTrue(payload_a["open"][0]["assigned"])
        self.assertEqual(payload_b["open_count"], 0)

    def test_rider_delivery_status_api(self):
        self._checkout(delivery=True, rider_ids=[self.rider_a.pk])
        self.client.login(username="840101", password="pass")
        response = self.client.get("/rider/deliveries/status/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["open_count"], 1)
        self.assertTrue(data["open"][0]["assigned"])

    def test_accept_hides_from_other_riders(self):
        self._checkout(delivery=True)
        delivery = ShopDelivery.objects.get()
        accept_shop_delivery(delivery_id=delivery.pk, rider=self.rider_a)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ShopDeliveryStatus.ACCEPTED)
        self.assertEqual(delivery.accepted_by_id, self.rider_a.pk)
        self.assertEqual(len(open_deliveries_for_rider(self.rider_a)), 0)
        self.assertEqual(len(open_deliveries_for_rider(self.rider_b)), 0)
        with self.assertRaises(ValidationError):
            accept_shop_delivery(delivery_id=delivery.pk, rider=self.rider_b)

    def test_delivery_requires_location_and_client(self):
        with self.assertRaises(ValidationError):
            self._checkout(
                delivery=True,
                client_name="",
                delivery_location="Westlands",
                delivery_lat="-1.2",
                delivery_lng="36.8",
            )
        with self.assertRaises(ValidationError):
            complete_shop_checkout(
                shop=self.shop,
                profile=self.cashier,
                payload={
                    "kind": ShopReceiptKind.SALE,
                    "payment_method": ShopPaymentMethod.CASH,
                    "cash_amount": "1200",
                    "login_code": "330001",
                    "client_name": "CLIENT ONE",
                    "client_phone": "0712345678",
                    "delivery": True,
                    "delivery_location": "",
                    "lines": [{"id": self.item.pk, "qty": "1", "price": "1200"}],
                },
            )

    def test_rider_portal_accept_and_complete(self):
        self._checkout(delivery=True)
        delivery = ShopDelivery.objects.get()
        self.client.login(username="840101", password="pass")
        accept_url = reverse(
            "employees:rider_delivery_accept",
            kwargs={"delivery_id": delivery.pk},
        )
        response = self.client.post(accept_url)
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.accepted_by_id, self.rider_a.pk)

        active = self.client.get("/rider/active/")
        self.assertEqual(active.status_code, 200)
        self.assertContains(active, "CLIENT ONE")
        self.assertContains(active, "Westlands, Nairobi")
        self.assertContains(active, "+254712345678")
        self.assertContains(active, "Received by")
        self.assertContains(active, 'name="receiver_name"')

        home = self.client.get("/rider/")
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, "Accept delivery")

        self.client.logout()
        self.client.login(username="840102", password="pass")
        other_home = self.client.get("/rider/")
        self.assertEqual(other_home.status_code, 200)
        self.assertNotContains(other_home, "CLIENT ONE")
        self.assertNotContains(other_home, "Accept delivery")

        self.client.logout()
        self.client.login(username="840101", password="pass")
        complete_url = reverse(
            "employees:rider_delivery_complete",
            kwargs={"delivery_id": delivery.pk},
        )
        done = self.client.post(
            complete_url,
            {"receiver_name": "CLIENT ONE"},
        )
        self.assertEqual(done.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ShopDeliveryStatus.DELIVERED)
        self.assertEqual(delivery.receiver_name, "CLIENT ONE")
        delivery.receipt.refresh_from_db()
        self.assertEqual(delivery.receipt.kind, ShopReceiptKind.SALE)

    def test_complete_shop_delivery_service(self):
        self._checkout(delivery=True)
        delivery = ShopDelivery.objects.get()
        accept_shop_delivery(delivery_id=delivery.pk, rider=self.rider_a)
        complete_shop_delivery(
            delivery_id=delivery.pk,
            rider=self.rider_a,
            receiver_name="Neighbour",
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ShopDeliveryStatus.DELIVERED)
        self.assertEqual(delivery.receiver_name, "Neighbour")
        self.assertIsNotNone(delivery.completed_at)
        delivery.receipt.refresh_from_db()
        self.assertEqual(delivery.receipt.kind, ShopReceiptKind.SALE)

    def test_complete_credit_delivery_unpaid_and_paid(self):
        self._checkout(
            delivery=True,
            kind=ShopReceiptKind.CREDIT,
            payment_method=ShopPaymentMethod.NONE,
            cash_amount="0",
            mpesa_amount="0",
        )
        delivery = ShopDelivery.objects.select_related("receipt").get()
        receipt_status = delivery.receipt.status
        accept_shop_delivery(delivery_id=delivery.pk, rider=self.rider_a)

        complete_shop_delivery(delivery_id=delivery.pk, rider=self.rider_a)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ShopDeliveryStatus.DELIVERED)
        self.assertEqual(delivery.payment_reference, "")
        self.assertIsNone(delivery.payment_at)
        delivery.receipt.refresh_from_db()
        self.assertEqual(delivery.receipt.status, receipt_status)
        self.assertEqual(delivery.receipt.kind, ShopReceiptKind.CREDIT)

        # Re-open as accepted to exercise the paid path.
        delivery.status = ShopDeliveryStatus.ACCEPTED
        delivery.completed_at = None
        delivery.save(update_fields=["status", "completed_at", "updated_at"])
        complete_shop_delivery(
            delivery_id=delivery.pk,
            rider=self.rider_a,
            payment_reference="QWERTY12345",
            payment_at="2026-11-11T14:30",
            payment_amount="1200",
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, ShopDeliveryStatus.DELIVERED_PAID)
        self.assertEqual(delivery.payment_reference, "QWERTY12345")
        self.assertEqual(delivery.payment_amount, Decimal("1200.00"))
        self.assertIsNotNone(delivery.payment_at)
        delivery.receipt.refresh_from_db()
        self.assertEqual(delivery.receipt.status, receipt_status)
        self.assertEqual(delivery.receipt.kind, ShopReceiptKind.CREDIT)

    def test_receipt_ticket_includes_delivery_details(self):
        from shops.services import _build_receipt_ticket_data, _render_receipt_text

        self._checkout(delivery=True, rider_ids=[self.rider_a.pk])
        delivery = ShopDelivery.objects.select_related("receipt").get()
        ticket = _build_receipt_ticket_data(
            delivery.receipt, list(delivery.receipt.lines.all())
        )
        self.assertTrue(ticket["has_delivery"])
        self.assertEqual(ticket["delivery_location"], "Westlands, Nairobi")
        self.assertIn("RIDE", ticket["delivery_rider"].upper())
        text = _render_receipt_text(ticket)
        self.assertIn("Westlands, Nairobi", text)
        self.assertIn("Rider", text)

    def test_client_lookup_includes_previous_delivery_location(self):
        self._checkout(delivery=True)
        session = self.client.session
        session["active_shop_id"] = str(self.shop.pk)
        session["shop_portal_auth"] = True
        session.save()
        url = reverse(
            "employees:my_shop_client_lookup",
            kwargs={"shop_id": self.shop.pk},
        )
        response = self.client.get(url, {"phone": "+254712345678"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["found"])
        last = data.get("last_delivery") or {}
        self.assertEqual(last.get("location_text"), "Westlands, Nairobi")
        self.assertEqual(last.get("lat"), "-1.2670000")
        self.assertEqual(last.get("lng"), "36.8110000")

        by_name = self.client.get(url, {"name": "CLIENT"})
        self.assertEqual(by_name.status_code, 200)
        name_data = by_name.json()
        self.assertTrue(name_data["ok"])
        self.assertTrue(name_data["results"])
        self.assertEqual(
            name_data["results"][0]["last_delivery"]["location_text"],
            "Westlands, Nairobi",
        )
