"""Shop register/edit stores Google Maps coordinates."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from employees.models import EmployeeProfile, EmployeeRole, EmployeeStatus
from shops.models import Shop
from shops.services import create_shop, update_shop, validate_shop_payload


class ShopLocationCoordsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="440001",
            password="pass",
            email="shop-coords@test.local",
            is_active=True,
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="440001",
            phone_country_code="+254",
            phone_number="700004401",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.IT_SUPPORT,
        )

    def _payload(self, **overrides):
        data = {
            "name": "Pipeline Branch",
            "location": "Ongata Rongai, Kenya",
            "email": "pipeline@test.local",
            "phone_number": "+254700000001",
            "login_code": "440001",
            "password": "secret1",
            "password_confirm": "secret1",
            "latitude": "-1.3938636",
            "longitude": "36.7442377",
            "maps_url": "https://www.google.com/maps/search/?api=1&query=-1.3938636,36.7442377",
        }
        data.update(overrides)
        return data

    def test_create_shop_stores_coordinates(self):
        shop = create_shop(self.profile, self._payload(), files={})
        self.assertEqual(shop.location, "ONGATA RONGAI, KENYA")
        self.assertEqual(shop.latitude, Decimal("-1.3938636"))
        self.assertEqual(shop.longitude, Decimal("36.7442377"))
        self.assertIn("query=-1.3938636,36.7442377", shop.maps_url)

    def test_update_shop_updates_coordinates(self):
        shop = create_shop(self.profile, self._payload(), files={})
        update_shop(
            shop,
            self._payload(
                location="Westlands, Nairobi",
                latitude="-1.2670000",
                longitude="36.8110000",
                maps_url="",
                password="",
                password_confirm="",
                login_code="440001",
            ),
            files={},
        )
        shop.refresh_from_db()
        self.assertEqual(shop.location, "WESTLANDS, NAIROBI")
        self.assertEqual(shop.latitude, Decimal("-1.2670000"))
        self.assertEqual(shop.longitude, Decimal("36.8110000"))
        self.assertIn("36.8110000", shop.maps_url)

    def test_coords_must_be_paired(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_shop_payload(
                self._payload(longitude=""),
                files={},
            )
        self.assertTrue(
            any("latitude and longitude" in msg.lower() for msg in ctx.exception.messages)
        )

    def test_location_without_coords_still_allowed(self):
        cleaned = validate_shop_payload(
            self._payload(latitude="", longitude="", maps_url=""),
            files={},
        )
        self.assertIsNone(cleaned["latitude"])
        self.assertIsNone(cleaned["longitude"])
        self.assertIn("maps/search", cleaned["maps_url"])
