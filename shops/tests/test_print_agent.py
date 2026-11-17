"""Local print agent: token setup, off-LAN relay queueing, agent poll/ack."""

from __future__ import annotations

import base64
import json
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from shops.agent_views import agent_setup_key
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

    def _report_url(self):
        return reverse(
            "employees:print_agent_report", kwargs={"shop_id": self.shop.pk}
        )

    def _hosted_scan(self, client):
        # Public "server" IP only → no private networks → hosted mode.
        # (TEST-NET ranges like 203.0.113.x count as private for ipaddress.)
        with mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["8.8.8.8"],
        ):
            return client.post(
                reverse(
                    "employees:my_shop_wifi_printer_scan",
                    kwargs={"shop_id": self.shop.pk},
                ),
                data=json.dumps({}),
                content_type="application/json",
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
        with self._off_lan(), mock.patch(
            "socket.create_connection", side_effect=OSError("unreachable")
        ):
            response = self._post_relay(client)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["queued"])
        job = PrintAgentJob.objects.get(shop=self.shop)
        self.assertEqual(job.host, OFF_LAN_HOST)
        self.assertEqual(job.status, PrintAgentJob.Status.PENDING)
        self.assertEqual(bytes(job.data), b"\x1b@hi")

    def test_relay_off_lan_prints_directly_when_reachable(self):
        # Off the detected /24s but routable — send directly, no agent job.
        client = self._portal_client()
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        with self._off_lan(), mock.patch(
            "socket.create_connection", return_value=sock
        ) as connect:
            response = self._post_relay(client)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload.get("queued"))
        connect.assert_called_once()
        sock.sendall.assert_called_once_with(b"\x1b@hi")
        self.assertFalse(
            PrintAgentJob.objects.filter(shop=self.shop).exists()
        )

    def test_relay_off_lan_without_token_errors(self):
        self.shop.print_agent_token = ""
        self.shop.save(update_fields=["print_agent_token"])
        client = self._portal_client()
        with self._off_lan(), mock.patch(
            "socket.create_connection", side_effect=OSError("unreachable")
        ):
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

    def test_poll_stamps_liveness_and_returns_scan_request(self):
        self.shop.print_agent_scan_request = {
            "at": time.time(),
            "hosts": ["192.168.100.171"],
            "subnets": [],
        }
        self.shop.save(update_fields=["print_agent_scan_request"])
        client = Client()
        payload = client.get(
            self._poll_url(), HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN
        ).json()
        req = payload["scan_request"]
        self.assertEqual(req["hosts"], ["192.168.100.171"])
        self.assertTrue(req["at"])
        self.shop.refresh_from_db()
        self.assertIsNotNone(self.shop.print_agent_seen_at)

    def test_poll_drops_expired_scan_request(self):
        self.shop.print_agent_scan_request = {
            "at": time.time() - 700,
            "hosts": ["192.168.100.171"],
        }
        self.shop.save(update_fields=["print_agent_scan_request"])
        payload = Client().get(
            self._poll_url(), HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN
        ).json()
        self.assertIsNone(payload["scan_request"])

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

    # --- agent setup link --------------------------------------------------

    def _setup_url(self, key=None):
        return reverse(
            "employees:print_agent_setup",
            kwargs={
                "shop_id": self.shop.pk,
                "key": agent_setup_key(self.shop) if key is None else key,
            },
        )

    def test_setup_link_requires_valid_key(self):
        client = Client()
        self.assertEqual(client.get(self._setup_url("bogus")).status_code, 401)
        response = client.get(self._setup_url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["token"], AGENT_TOKEN)
        self.assertTrue(
            payload["poll_url"].endswith(
                f"/print-agent/{self.shop.pk}/jobs/"
            )
        )

    def test_setup_link_download_serves_config_file(self):
        response = Client().get(self._setup_url() + "?download=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'filename="print_agent.json"',
            response["Content-Disposition"],
        )
        payload = response.json()
        self.assertNotIn("ok", payload)
        self.assertEqual(payload["token"], AGENT_TOKEN)

    def test_setup_link_invalidated_by_new_token(self):
        old_url = self._setup_url()
        self.shop.print_agent_token = "new-token-after-regenerate"
        self.shop.save(update_fields=["print_agent_token"])
        self.assertEqual(Client().get(old_url).status_code, 401)

    def test_setup_link_rejected_without_token(self):
        self.shop.print_agent_token = ""
        self.shop.save(update_fields=["print_agent_token"])
        self.assertEqual(Client().get(self._setup_url("anything")).status_code, 401)

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

    # --- agent printer report + hosted scan --------------------------------

    def test_report_requires_token(self):
        client = Client()
        response = client.post(
            self._report_url(),
            data=json.dumps({"printers": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_report_keeps_only_lan_hosts(self):
        client = Client()
        response = client.post(
            self._report_url(),
            data=json.dumps(
                {
                    "printers": [
                        {
                            "host": "192.168.100.171",
                            "port": 9100,
                            "name": "POS-80 · 192.168.100.171",
                            "detail": "Raw :9100",
                        },
                        {"host": "8.8.8.8", "port": 9100},
                        {"host": "not-an-ip", "port": 9100},
                    ]
                }
            ),
            content_type="application/json",
            HTTP_X_PRINT_AGENT_TOKEN=AGENT_TOKEN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.shop.refresh_from_db()
        self.assertEqual(len(self.shop.print_agent_scan), 1)
        self.assertEqual(
            self.shop.print_agent_scan[0]["host"], "192.168.100.171"
        )
        self.assertIsNotNone(self.shop.print_agent_scan_at)

    def test_hosted_scan_returns_agent_reported_printers(self):
        self.shop.print_agent_scan = [
            {
                "id": "wifi-192.168.100.171:9100",
                "host": "192.168.100.171",
                "port": 9100,
                "name": "POS-80 · 192.168.100.171",
                "detail": "Raw :9100",
                "kind": "printer",
                "connect_mode": "raw",
            }
        ]
        self.shop.print_agent_scan_at = timezone.now()
        self.shop.save(update_fields=["print_agent_scan", "print_agent_scan_at"])

        response = self._hosted_scan(self._portal_client())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["hosted"])
        self.assertTrue(payload["agent"])
        self.assertTrue(payload["from_agent"])
        self.assertEqual(len(payload["printers"]), 1)
        self.assertEqual(payload["printers"][0]["host"], "192.168.100.171")

    def test_hosted_scan_empty_report_explains(self):
        response = self._hosted_scan(self._portal_client())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["hosted"])
        self.assertEqual(payload["printers"], [])
        self.assertIn("agent", payload["hint"].lower())

    def test_hosted_scan_ignores_stale_report(self):
        self.shop.print_agent_scan = [
            {
                "host": "192.168.1.50",
                "port": 9100,
                "name": "x",
                "detail": "d",
            }
        ]
        self.shop.print_agent_scan_at = timezone.now() - timedelta(minutes=10)
        self.shop.save(update_fields=["print_agent_scan", "print_agent_scan_at"])
        payload = self._hosted_scan(self._portal_client()).json()
        self.assertEqual(payload["printers"], [])

    # --- on-LAN scan merges the agent report + typed host probe ------------

    def _agent_row(self, host="192.168.100.171", port=9100):
        return {
            "id": f"wifi-{host}:{port}",
            "host": host,
            "port": port,
            "name": f"POS-80 · {host}",
            "detail": f"Raw :{port}",
            "kind": "printer",
            "connect_mode": "raw",
        }

    def _onlan_scan(self, client, body=None):
        with mock.patch(
            "shops.printer_discovery.discover_lan_printers",
            return_value={
                "ok": True,
                "printers": [],
                "networks": ["192.168.0.0/24"],
                "local_ips": ["192.168.0.102"],
                "hint": "",
                "cached": False,
            },
        ) as disco:
            response = client.post(
                reverse(
                    "employees:my_shop_wifi_printer_scan",
                    kwargs={"shop_id": self.shop.pk},
                ),
                data=json.dumps(body or {}),
                content_type="application/json",
            )
        return response, disco

    def test_onlan_scan_merges_agent_report(self):
        self.shop.print_agent_scan = [self._agent_row()]
        self.shop.print_agent_scan_at = timezone.now()
        self.shop.save(update_fields=["print_agent_scan", "print_agent_scan_at"])

        response, _ = self._onlan_scan(self._portal_client())
        payload = response.json()
        self.assertFalse(payload["hosted"])
        self.assertTrue(payload["from_agent"])
        self.assertTrue(payload["agent_alive"])
        self.assertEqual(len(payload["printers"]), 1)
        self.assertEqual(payload["printers"][0]["host"], "192.168.100.171")
        self.assertTrue(payload["printers"][0]["via_agent"])

    def test_onlan_scan_ignores_stale_agent_report(self):
        self.shop.print_agent_scan = [self._agent_row()]
        self.shop.print_agent_scan_at = timezone.now() - timedelta(minutes=10)
        self.shop.save(update_fields=["print_agent_scan", "print_agent_scan_at"])

        response, _ = self._onlan_scan(self._portal_client())
        payload = response.json()
        self.assertEqual(payload["printers"], [])
        self.assertFalse(payload["from_agent"])
        self.assertFalse(payload["agent_alive"])

    def test_onlan_scan_passes_saved_host(self):
        self.shop.wifi_printer_host = "192.168.100.171"
        self.shop.save(update_fields=["wifi_printer_host"])

        _, disco = self._onlan_scan(self._portal_client())
        extra = disco.call_args.kwargs.get("extra_hosts") or []
        self.assertIn("192.168.100.171", extra)

    # --- targeted scan (typed IP) -------------------------------------------

    def _targeted_scan(self, client, host, **extra):
        body = {"host": host}
        body.update(extra)
        return client.post(
            reverse(
                "employees:my_shop_wifi_printer_scan",
                kwargs={"shop_id": self.shop.pk},
            ),
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_typed_host_runs_targeted_scan_not_full_sweep(self):
        printer = self._agent_row("192.168.0.55")
        with mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["192.168.0.102"],
        ), mock.patch(
            "shops.printer_discovery._probe_host", return_value=printer
        ) as probe, mock.patch(
            "shops.printer_discovery.discover_lan_printers"
        ) as disco:
            response = self._targeted_scan(
                self._portal_client(), "192.168.0.55"
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [row["host"] for row in payload["printers"]], ["192.168.0.55"]
        )
        probe.assert_called_once_with(
            "192.168.0.55", resolve_name=True, connect_timeout=0.6
        )
        disco.assert_not_called()

    def test_typed_host_rejects_bad_ip(self):
        for host in ("192.168.x.x", "8.8.8.8", "not-an-ip"):
            response = self._targeted_scan(self._portal_client(), host)
            self.assertEqual(response.status_code, 400, host)
            self.assertFalse(response.json()["ok"])
            self.assertIn("printer IP", response.json()["error"])

    def test_typed_host_uses_agent_report_when_hosted(self):
        other = self._agent_row()
        other["host"] = "192.168.100.99"
        self.shop.print_agent_scan = [self._agent_row(), other]
        self.shop.print_agent_scan_at = timezone.now()
        self.shop.save(
            update_fields=["print_agent_scan", "print_agent_scan_at"]
        )
        with mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["8.8.8.8"],
        ):
            response = self._targeted_scan(
                self._portal_client(), "192.168.100.171"
            )
        payload = response.json()
        self.assertTrue(payload["hosted"])
        self.assertEqual(
            [row["host"] for row in payload["printers"]],
            ["192.168.100.171"],
        )
        self.assertTrue(payload["printers"][0]["via_agent"])

    def test_typed_host_miss_reports_specific_hint(self):
        # Agent alive but its report doesn't contain the typed host.
        self.shop.print_agent_seen_at = timezone.now()
        self.shop.save(update_fields=["print_agent_seen_at"])
        with mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["8.8.8.8"],
        ):
            response = self._targeted_scan(
                self._portal_client(), "192.168.100.171"
            )
        payload = response.json()
        self.assertEqual(payload["printers"], [])
        self.assertIn("192.168.100.171", payload["hint"])

    def test_scan_writes_agent_scan_request_with_typed_host(self):
        with mock.patch(
            "shops.printer_discovery._local_ipv4_addresses",
            return_value=["8.8.8.8"],
        ):
            response = self._targeted_scan(
                self._portal_client(), "192.168.100.171"
            )
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        req = self.shop.print_agent_scan_request
        self.assertEqual(req["hosts"], ["192.168.100.171"])
        self.assertTrue(req["at"])

    def test_scan_retry_keeps_existing_scan_request(self):
        self.shop.print_agent_scan_request = {"at": 111.0, "hosts": []}
        self.shop.save(update_fields=["print_agent_scan_request"])
        response, _ = self._onlan_scan(
            self._portal_client(), body={"retry": True}
        )
        self.assertEqual(response.status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.print_agent_scan_request["at"], 111.0)

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
