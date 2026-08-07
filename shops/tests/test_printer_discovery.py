"""Test loops for Wi‑Fi printer discovery (fast + thorough paths)."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from shops import printer_discovery as pd


class PrinterDiscoveryLoopsTests(unittest.TestCase):
    def setUp(self):
        pd.clear_discovery_cache()

    def test_port_open_localhost_closed_high_port(self):
        # Loop: random high ports should not false-positive as printers.
        for port in (59100, 59101, 59102):
            self.assertFalse(pd._port_open("127.0.0.1", port, timeout=0.05))

    def test_merge_prefers_raw_9100(self):
        a = {
            "host": "192.168.1.50",
            "port": 631,
            "name": "IPP",
            "id": "a",
            "ports": [631],
            "services": [],
            "detail": "",
            "kind": "printer",
            "connect_mode": "raw",
        }
        b = {
            "host": "192.168.1.50",
            "port": 9100,
            "name": "Raw",
            "id": "b",
            "ports": [9100],
            "services": [],
            "detail": "",
            "kind": "printer",
            "connect_mode": "raw",
        }
        merged = pd._merge_printers([a], [b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["port"], 9100)

    def test_probe_host_only_returns_printer_ports(self):
        def fake_open(host, port, timeout=0.12):
            return port in {80, 443}

        with mock.patch.object(pd, "_port_open", side_effect=fake_open):
            self.assertIsNone(pd._probe_host("192.168.88.10"))

        def fake_printer(host, port, timeout=0.12):
            return port == 9100

        with mock.patch.object(pd, "_port_open", side_effect=fake_printer):
            with mock.patch.object(pd, "_reverse_name", return_value="ShopPOS"):
                row = pd._probe_host("192.168.88.55", resolve_name=True)
        self.assertIsNotNone(row)
        self.assertEqual(row["host"], "192.168.88.55")
        self.assertEqual(row["port"], 9100)
        self.assertEqual(row["kind"], "printer")
        self.assertIn("ShopPOS", row["name"])

    def test_discover_fast_path_loop_uses_live_hosts_only(self):
        calls = {"ping": 0, "probe": []}

        def fake_ping_sweep(hosts, *, deadline=3.0):
            calls["ping"] += 1
            return {"192.168.88.50", "192.168.88.51"}

        def fake_probe(host, *, resolve_name=False):
            calls["probe"].append(host)
            if host == "192.168.88.50":
                return {
                    "id": "wifi-192.168.88.50:9100",
                    "host": "192.168.88.50",
                    "port": 9100,
                    "ports": [9100],
                    "services": ["Raw / ESC-POS :9100"],
                    "name": "POS · 192.168.88.50",
                    "detail": "Raw / ESC-POS :9100",
                    "kind": "printer",
                    "connect_mode": "raw",
                }
            return None

        with mock.patch.object(pd, "_local_ipv4_addresses", return_value=["192.168.88.237"]):
            with mock.patch.object(
                pd,
                "_networks_from_local_ips",
                return_value=[__import__("ipaddress").ip_network("192.168.88.0/24")],
            ):
                with mock.patch.object(
                    pd, "_arp_live_hosts", return_value={"192.168.88.1", "192.168.88.50"}
                ):
                    with mock.patch.object(pd, "_ping_sweep", side_effect=fake_ping_sweep):
                        with mock.patch.object(pd, "_probe_host", side_effect=fake_probe):
                            with mock.patch.object(pd, "_windows_tcp_printers", return_value=[]):
                                with mock.patch.object(
                                    pd, "_windows_pos_usb_printers", return_value=[]
                                ):
                                    result = pd.discover_lan_printers(
                                        thorough=False, use_cache=False
                                    )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["printers"]), 1)
        self.assertEqual(result["printers"][0]["host"], "192.168.88.50")
        # ARP already found a printer — ping sweep must be skipped.
        self.assertEqual(calls["ping"], 0)
        # Must not probe the entire /24 in fast mode.
        self.assertLessEqual(len(calls["probe"]), 20)

    def test_discover_empty_returns_usb_hint(self):
        with mock.patch.object(pd, "_local_ipv4_addresses", return_value=["192.168.88.237"]):
            with mock.patch.object(
                pd,
                "_networks_from_local_ips",
                return_value=[__import__("ipaddress").ip_network("192.168.88.0/24")],
            ):
                with mock.patch.object(pd, "_arp_live_hosts", return_value=set()):
                    with mock.patch.object(pd, "_ping_sweep", return_value=set()):
                        with mock.patch.object(pd, "_probe_host", return_value=None):
                            with mock.patch.object(pd, "_windows_tcp_printers", return_value=[]):
                                with mock.patch.object(
                                    pd,
                                    "_windows_pos_usb_printers",
                                    return_value=[
                                        {
                                            "id": "usb-win-POS-80C",
                                            "name": "POS-80C",
                                            "port_name": "USB008",
                                            "driver": "POS-80C",
                                            "detail": "USB008",
                                            "kind": "usb_windows",
                                            "connect_mode": "windows",
                                            "fallback_channel": "usb",
                                        },
                                        {
                                            "id": "usb-win-POS80",
                                            "name": "POS80 Printer(2)",
                                            "port_name": "USB009",
                                            "driver": "POS80ENG",
                                            "detail": "USB009",
                                            "kind": "usb_windows",
                                            "connect_mode": "windows",
                                            "fallback_channel": "usb",
                                        },
                                    ],
                                ):
                                    result = pd.discover_lan_printers(
                                        thorough=False, use_cache=False
                                    )

        self.assertTrue(result["ok"])
        self.assertEqual(result["printers"], [])
        self.assertEqual(result["usb_printers"], [])
        self.assertIn("Wi‑Fi", result["hint"])
        self.assertIn("USB channel", result["hint"])
        self.assertIn("POS-80C", result["hint"])
        self.assertIn("POS-80C", result["usb_hints"])

    def test_live_discover_loop_is_fast(self):
        """Integration loop against the real LAN — must finish quickly."""
        times = []
        for _ in range(2):
            t0 = time.perf_counter()
            result = pd.discover_lan_printers(thorough=False, use_cache=False)
            times.append(time.perf_counter() - t0)
            self.assertTrue(result["ok"])
            self.assertIn("networks", result)
            # Fast path target: under 6s even on a busy LAN / slow PowerShell.
            self.assertLess(times[-1], 6.0)
        self.assertLess(min(times), 6.0)

    def test_scan_cache_loop_is_instant(self):
        with mock.patch.object(pd, "_local_ipv4_addresses", return_value=["192.168.88.237"]):
            with mock.patch.object(
                pd,
                "_networks_from_local_ips",
                return_value=[__import__("ipaddress").ip_network("192.168.88.0/24")],
            ):
                with mock.patch.object(pd, "_arp_live_hosts", return_value=set()):
                    with mock.patch.object(pd, "_ping_sweep", return_value=set()):
                        with mock.patch.object(pd, "_probe_host", return_value=None):
                            with mock.patch.object(pd, "_windows_tcp_printers", return_value=[]):
                                with mock.patch.object(
                                    pd, "_windows_pos_usb_printers", return_value=[]
                                ):
                                    first = pd.discover_lan_printers(thorough=False)
                                    second = pd.discover_lan_printers(thorough=False)
        self.assertFalse(first.get("cached"))
        self.assertTrue(second.get("cached"))


if __name__ == "__main__":
    unittest.main()
