"""Local print agent: token setup, off-LAN relay queueing, agent poll/ack."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from shops.models import PrintAgentJob, Shop
from shops.session import SESSION_SHOP_PORTAL_SHOP_KEY

AGENT_TOKEN = "test-agent-token-0123456789abcdef"
OFF_LAN_HOST = "192.168.55.66"


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class PrintAgentFlowTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            name="AGENT SHOP",
            location="NAIROBI",
            email="agent-shop@test.local",
            phone_number="0700000999",
            login_code="800999",
            password_hash=make_password("pw"),
            print_agent_token=AGENT_TOKEN,
        )

    def _portal_client(self):
        client = Client()
        session = client.session
        session["shop_portal_auth"] = True
        session[SESSION_SHOP_PORTAL_SHOP_KEY] = str(self.shop.pk)
        session.save()
        return client

    def _poll_url(self):
        return reverse("employees:print_agent_poll", kwargs={"shop_id": self.shop.pk})

    def _ack_url(self, job_id):
        return reverse(
            "employees:print_agent_ack",
            kwargs={"shop_id": self.shop.pk, "job_id": job_id},
        )

    def _post_relay(self, client, host=OFF_LAN_HOST):
        return client.post(
            reverse("employees:my_shop_print_relay", kwargs={"shop_id": self.shop.pk}),
            data=json.dumps(
                {"host": host, "port": 9100, "data": base64.b64encode(b"\x1b@hi").decode()}
            ),
            content_type="application/json",
        )

    # --- relay queueing ---------------------------------------------------

    def _off_lan(self):
        # Pin the "server" LAN so OFF_LAN_HOST is always outside it.
        return mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["10.9.9.9"],
        )

    def test_relay_queues_off_lan_when_token_set(self):
        client = self._portal_client()
        with self._off_lan():
            response = self._post_relay(client)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["queued"])
        job = PrintAgentJob.objects.get(shop=self.shop)
        self.assertEqual(job.host, OFF_LAN_HOST)
        self.assertEqual(job.status, PrintAgentJob.Status.PENDING)
        self.assertEqual(bytes(job.data), b"\x1b@hi")

    def test_relay_off_lan_without_token_errors(self):
        self.shop.print_agent_token = ""
        self.shop.save(update_fields=["print_agent_token"])
        client = self._portal_client()
        with self._off_lan():
            response = self._post_relay(client)
        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload.get("agent_required"))
        self.assertFalse(
            PrintAgentJob.objects.filter(shop=self.shop).exists()
        )

    # --- agent poll --------------------------------------------------------

    def test_poll_requires_token(self):
        client = Client()
        self.assertEqual(client.get(self._poll_url()).status_code, 401)
        self.assertEqual(
            client.get(
                self._poll_url(), HTTP_X_PRINT_AGENT_TOKEN="wrong"
            ).status_code,
            401,
        )

    def test_poll_claims_pending_jobs(self):
        job = PrintAgentJob.objects.create(
            shop=self.shop, host=OFF_LAN_HOST, port=9100, data=b"abc"
        )
        client = Client()
        response = client.get(
            self._poll_url(), HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["jobs"]), 1)
        row = payload["jobs"][0]
        self.assertEqual(row["id"], job.pk)
        self.assertEqual(row["host"], OFF_LAN_HOST)
        self.assertEqual(base64.b64decode(row["data"]), b"abc")

        job.refresh_from_db()
        self.assertEqual(job.status, PrintAgentJob.Status.CLAIMED)
        self.assertEqual(job.attempts, 1)

        # Claimed jobs are not handed out twice.
        again = client.get(
            self._poll_url(), HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN
        ).json()
        self.assertEqual(again["jobs"], [])

    def test_poll_requeues_stale_claims(self):
        job = PrintAgentJob.objects.create(
            shop=self.shop,
            host=OFF_LAN_HOST,
            port=9100,
            data=b"abc",
            status=PrintAgentJob.Status.CLAIMED,
            claimed_at=timezone.now() - timedelta(minutes=10),
        )
        client = Client()
        payload = client.get(
            self._poll_url(), HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN
        ).json()
        self.assertEqual([row["id"] for row in payload["jobs"]], [job.pk])

    # --- agent ack ---------------------------------------------------------

    def test_ack_done_and_failed(self):
        done_job = PrintAgentJob.objects.create(
            shop=self.shop,
            host=OFF_LAN_HOST,
            data=b"a",
            status=PrintAgentJob.Status.CLAIMED,
            claimed_at=timezone.now(),
        )
        fail_job = PrintAgentJob.objects.create(
            shop=self.shop,
            host=OFF_LAN_HOST,
            data=b"b",
            status=PrintAgentJob.Status.CLAIMED,
            claimed_at=timezone.now(),
        )
        client = Client()
        response = client.post(
            self._ack_url(done_job.pk),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
            HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN,
        )
        self.assertEqual(response.status_code, 200)
        done_job.refresh_from_db()
        self.assertEqual(done_job.status, PrintAgentJob.Status.DONE)
        self.assertIsNotNone(done_job.done_at)

        response = client.post(
            self._ack_url(fail_job.pk),
            data=json.dumps({"status": "failed", "error": "printer offline"}),
            content_type="application/json",
            HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN,
        )
        self.assertEqual(response.status_code, 200)
        fail_job.refresh_from_db()
        self.assertEqual(fail_job.status, PrintAgentJob.Status.FAILED)
        self.assertEqual(fail_job.error, "printer offline")

        # Done jobs can't be acked again.
        self.assertEqual(
            client.post(
                self._ack_url(done_job.pk),
                data=json.dumps({"status": "done"}),
                content_type="application/json",
                HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN,
            ).status_code,
            404,
        )

    def test_ack_rejects_bad_token(self):
        job = PrintAgentJob.objects.create(
            shop=self.shop,
            host=OFF_LAN_HOST,
            data=b"a",
            status=PrintAgentJob.Status.CLAIMED,
            claimed_at=timezone.now(),
        )
        client = Client()
        response = client.post(
            self._ack_url(job.pk),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
            HTTP_X_PRINT_AGENT_TOKEN="wrong",
        )
        self.assertEqual(response.status_code, 401)
        job.refresh_from_db()
        self.assertEqual(job.status, PrintAgentJob.Status.CLAIMED)

    # --- token management --------------------------------------------------

    def test_settings_action_generates_and_regenerates_token(self):
        self.shop.print_agent_token = ""
        self.shop.save(update_fields=["print_agent_token"])
        client = self._portal_client()
        url = reverse(
            "employees:my_shop_settings_receipt", kwargs={"shop_id": self.shop.pk}
        )
        response = client.post(
            url,
            data={"action": "print_agent_token"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        first = response.json()["print_agent_token"]
        self.assertEqual(len(first), 40)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.print_agent_token, first)

        response = client.post(
            url,
            data={"action": "print_agent_token"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        second = response.json()["print_agent_token"]
        self.assertNotEqual(first, second)

        # Settings page renders the agent section with the ready-made config.
        page = client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Cloud print agent")
        self.assertContains(page, second)
        self.assertContains(
            page, reverse("employees:print_agent_poll", kwargs={"shop_id": self.shop.pk})
        )
