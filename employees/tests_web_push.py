from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from employees.models import (
    EmployeeProfile,
    EmployeeRole,
    EmployeeStatus,
    WebPushSubscription,
)
from employees.web_push import notify_employees, vapid_public_key


VAPID_PUBLIC = "BBtestpublickeyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
VAPID_PRIVATE = "testprivatekeyxxxxxxxxxxxxxxxxxxxxxxx"


class WebPushApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="810099", password="pass-web-push", first_name="Push"
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="810099",
            phone_country_code="+254",
            phone_number="700000099",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.RIDER,
        )

    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY=VAPID_PUBLIC,
        WEB_PUSH_VAPID_PRIVATE_KEY=VAPID_PRIVATE,
    )
    def test_vapid_public_key_endpoint(self):
        response = self.client.get(reverse("employees:web_push_vapid_public_key"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["publicKey"], VAPID_PUBLIC)

    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY=VAPID_PUBLIC,
        WEB_PUSH_VAPID_PRIVATE_KEY=VAPID_PRIVATE,
    )
    def test_subscribe_requires_auth(self):
        response = self.client.post(
            reverse("employees:web_push_subscribe"),
            data='{"endpoint":"https://example.com/push/1","keys":{"p256dh":"abc","auth":"def"}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY=VAPID_PUBLIC,
        WEB_PUSH_VAPID_PRIVATE_KEY=VAPID_PRIVATE,
    )
    def test_subscribe_saves_for_employee(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("employees:web_push_subscribe"),
            data=(
                '{"endpoint":"https://example.com/push/rider-1",'
                '"keys":{"p256dh":"abc123","auth":"def456"}}'
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        row = WebPushSubscription.objects.get(
            endpoint="https://example.com/push/rider-1"
        )
        self.assertEqual(row.employee_id, self.profile.pk)
        self.assertEqual(row.p256dh, "abc123")

    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY=VAPID_PUBLIC,
        WEB_PUSH_VAPID_PRIVATE_KEY=VAPID_PRIVATE,
    )
    def test_unsubscribe_removes_endpoint(self):
        self.client.force_login(self.user)
        WebPushSubscription.objects.create(
            endpoint="https://example.com/push/drop",
            p256dh="a",
            auth="b",
            employee=self.profile,
        )
        response = self.client.post(
            reverse("employees:web_push_unsubscribe"),
            data='{"endpoint":"https://example.com/push/drop"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted"], 1)
        self.assertFalse(
            WebPushSubscription.objects.filter(
                endpoint="https://example.com/push/drop"
            ).exists()
        )


class WebPushNotifyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="810088", password="pass")
        self.rider = EmployeeProfile.objects.create(
            user=self.user,
            employee_id="810088",
            phone_country_code="+254",
            phone_number="700000088",
            status=EmployeeStatus.ACTIVE,
            role=EmployeeRole.RIDER,
        )
        WebPushSubscription.objects.create(
            endpoint="https://example.com/push/rider-notify",
            p256dh="p256",
            auth="auth",
            employee=self.rider,
        )

    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY=VAPID_PUBLIC,
        WEB_PUSH_VAPID_PRIVATE_KEY=VAPID_PRIVATE,
    )
    @patch("pywebpush.webpush")
    def test_notify_employees_sends_push(self, mock_webpush):
        mock_webpush.return_value = MagicMock()
        sent = notify_employees(
            [self.rider],
            title="New delivery request",
            body="Push Shop · Gate A",
            url="/rider/",
            tag="delivery-1",
        )
        self.assertEqual(sent, 1)
        self.assertEqual(mock_webpush.call_count, 1)
        kwargs = mock_webpush.call_args.kwargs
        self.assertIn("New delivery request", kwargs["data"])


class WebPushKeyHelperTests(TestCase):
    def test_vapid_public_key_can_auto_generate(self):
        with override_settings(
            WEB_PUSH_VAPID_PUBLIC_KEY="",
            WEB_PUSH_VAPID_PRIVATE_KEY="",
        ):
            key = vapid_public_key()
            self.assertTrue(key)
            self.assertGreater(len(key), 20)
