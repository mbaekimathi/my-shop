"""Fast LAN discovery for Wi‑Fi / Ethernet printers (private networks only)."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import subprocess
import threading
import time
from typing import Iterable

# Printer-only service ports (thermal / JetDirect / IPP / LPR + common variants).
PRINTER_PORTS: dict[int, str] = {
    9100: "Raw / ESC-POS",
    9101: "Raw alt",
    9102: "Raw alt",
    9112: "Raw alt",
    9200: "Raw alt",
    10001: "Raw alt",
    631: "IPP",
    515: "LPR/LPD",
}

PORT_PRIORITY = (9100, 9101, 9102, 9112, 9200, 10001, 631, 515)

CONNECT_TIMEOUT = 0.12
PING_TIMEOUT_MS = 120
MAX_WORKERS = 48
PHASE_DEADLINE_SEC = 3.0
THOROUGH_DEADLINE_SEC = 8.0
FAST_OVERALL_DEADLINE_SEC = 4.5
ARP_TIMEOUT_SEC = 2.0
POWERSHELL_TIMEOUT_SEC = 2.0
SCAN_CACHE_TTL_SEC = 20.0

_scan_cache_lock = threading.Lock()
_scan_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(key: str) -> dict | None:
    with _scan_cache_lock:
        row = _scan_cache.get(key)
        if not row:
            return None
        expires_at, payload = row
        if time.perf_counter() >= expires_at:
            _scan_cache.pop(key, None)
            return None
        return dict(payload)


def _cache_set(key: str, payload: dict) -> None:
    with _scan_cache_lock:
        _scan_cache[key] = (
            time.perf_counter() + SCAN_CACHE_TTL_SEC,
            dict(payload),
        )


def clear_discovery_cache() -> None:
    with _scan_cache_lock:
        _scan_cache.clear()


def _local_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                found.add(ip)
        finally:
            probe.close()
    except OSError:
        pass

    return sorted(found)


def _networks_from_local_ips(local_ips: Iterable[str]) -> list[ipaddress.IPv4Network]:
    networks: dict[str, ipaddress.IPv4Network] = {}
    for ip_text in local_ips:
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if not isinstance(ip, ipaddress.IPv4Address):
            continue
        if not (ip.is_private or ip.is_link_local):
            continue
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        networks[str(net)] = net
    return list(networks.values())


def _arp_live_hosts() -> set[str]:
    hosts: set[str] = set()
    try:
        completed = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            timeout=ARP_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return hosts

    for line in (completed.stdout or "").splitlines():
        for part in line.replace("-", " ").replace("(", " ").replace(")", " ").split():
            try:
                ip = ipaddress.ip_address(part)
            except ValueError:
                continue
            if isinstance(ip, ipaddress.IPv4Address) and (
                ip.is_private or ip.is_link_local
            ):
                hosts.add(str(ip))
    return hosts


def _ping_host(host: str) -> bool:
    try:
        completed = subprocess.run(
            ["ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), host],
            capture_output=True,
            text=True,
            timeout=1.2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _ping_sweep(hosts: Iterable[str], *, deadline: float = PHASE_DEADLINE_SEC) -> set[str]:
    live: set[str] = set()
    ordered = list(dict.fromkeys(hosts))
    if not ordered:
        return live
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_ping_host, host): host for host in ordered}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=deadline):
                host = futures[future]
                try:
                    if future.result():
                        live.add(host)
                except Exception:
                    continue
        except concurrent.futures.TimeoutError:
            for future, host in futures.items():
                if future.done():
                    try:
                        if future.result():
                            live.add(host)
                    except Exception:
                        continue
            for future in futures:
                future.cancel()
    return live


def _port_open(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _reverse_name(host: str) -> str:
    # Keep reverse DNS optional and short — it often dominates scan time.
    try:
        socket.setdefaulttimeout(0.15)
        name, _, _ = socket.gethostbyaddr(host)
        return (name or "").split(".")[0] or ""
    except OSError:
        return ""
    finally:
        socket.setdefaulttimeout(None)


def _probe_host(host: str, *, resolve_name: bool = False) -> dict | None:
    # Stop at the first open printer port (prefer raw 9100 via PORT_PRIORITY order).
    preferred = None
    for port in PORT_PRIORITY:
        if _port_open(host, port):
            preferred = port
            break
    if preferred is None:
        return None

    open_ports = [preferred]
    services = [f"{PRINTER_PORTS.get(preferred, 'Printer')} :{preferred}"]
    dns_name = _reverse_name(host) if resolve_name else ""
    label = dns_name or "Network printer"
    return {
        "id": f"wifi-{host}:{preferred}",
        "host": host,
        "port": preferred,
        "ports": open_ports,
        "services": services,
        "name": f"{label} · {host}",
        "detail": ", ".join(services),
        "kind": "printer",
        "connect_mode": "raw",
    }


def _windows_tcp_printers() -> list[dict]:
    """Pick up Windows TCP/IP printer ports that already know a host address."""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-PrinterPort | "
                    "Where-Object { $_.PrinterHostAddress } | "
                    "Select-Object Name, PrinterHostAddress, PortNumber | "
                    "ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=POWERSHELL_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    raw = (completed.stdout or "").strip()
    if not raw:
        return []

    import json

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    rows = payload if isinstance(payload, list) else [payload]
    printers: list[dict] = []
    for row in rows:
        host = str(row.get("PrinterHostAddress") or "").strip()
        if not host:
            continue
        try:
            ip = ipaddress.ip_address(host)
            if not (ip.is_private or ip.is_link_local or ip.is_loopback):
                continue
            host = str(ip)
        except ValueError:
            # Hostname — keep only simple LAN-looking names.
            if " " in host or host.lower() in {"localhost"}:
                continue
        try:
            port = int(row.get("PortNumber") or 9100)
        except (TypeError, ValueError):
            port = 9100
        if port < 1 or port > 65535:
            port = 9100
        name = str(row.get("Name") or host)
        printers.append(
            {
                "id": f"wifi-win-{host}:{port}",
                "host": host,
                "port": port,
                "ports": [port],
                "services": [f"Windows port :{port}"],
                "name": f"{name} · {host}",
                "detail": f"Windows TCP/IP :{port}",
                "kind": "printer",
                "connect_mode": "raw",
            }
        )
    return printers


def _windows_pos_usb_printers() -> list[dict]:
    """USB POS / receipt printers installed in Windows (for scan fallback)."""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-Printer | "
                    "Where-Object { "
                    "  ($_.PortName -like 'USB*') -or "
                    "  ($_.Name -match 'POS|thermal|receipt|ESC') "
                    "} | "
                    "Where-Object { "
                    "  $_.Name -notmatch 'OneNote|PDF|XPS|Fax' "
                    "} | "
                    "Select-Object Name, PortName, DriverName | "
                    "ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=POWERSHELL_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    raw = (completed.stdout or "").strip()
    if not raw:
        return []

    import json

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    rows = payload if isinstance(payload, list) else [payload]
    printers: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("Name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        port_name = str(row.get("PortName") or "").strip()
        driver = str(row.get("DriverName") or "").strip()
        printers.append(
            {
                "id": f"usb-win-{name}",
                "name": name,
                "port_name": port_name,
                "driver": driver,
                "detail": port_name or "USB",
                "kind": "usb_windows",
                "connect_mode": "windows",
                "fallback_channel": "usb",
            }
        )
    return printers


def _windows_pos_usb_hints() -> list[str]:
    return [row["name"] for row in _windows_pos_usb_printers()]


def _merge_printers(*groups: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in groups:
        for row in group:
            host = str(row.get("host") or "").strip()
            if not host:
                continue
            key = host
            existing = merged.get(key)
            if not existing:
                merged[key] = row
                continue
            # Prefer raw 9100-style ports when merging.
            if int(row.get("port") or 0) == 9100 and int(existing.get("port") or 0) != 9100:
                merged[key] = row
    return sorted(
        merged.values(),
        key=lambda row: (
            0 if int(row.get("port") or 0) == 9100 else 1,
            tuple(int(p) for p in str(row["host"]).split("."))
            if re.fullmatch(r"\d+(?:\.\d+){3}", str(row["host"]))
            else (999, str(row["host"])),
        ),
    )


def discover_lan_printers(*, thorough: bool = False, use_cache: bool = True) -> dict:
    """
    Fast printer discovery:

    1) Windows TCP ports + ARP hosts (cheap)
    2) Probe those hosts for printer ports only
    3) Ping common DHCP suffixes only if still empty
    4) Optional thorough /24 sweep only if still empty
    """
    cache_key = f"thorough={int(bool(thorough))}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            cached["cached"] = True
            return cached

    started = time.perf_counter()

    def _remaining(cap: float = FAST_OVERALL_DEADLINE_SEC) -> float:
        return max(0.05, cap - (time.perf_counter() - started))

    local_ips = _local_ipv4_addresses()
    networks = _networks_from_local_ips(local_ips)
    if not networks:
        return {
            "ok": False,
            "error": "Could not detect a private Wi‑Fi/LAN address on this server.",
            "printers": [],
            "scanned_hosts": 0,
            "networks": [],
            "elapsed_ms": 0,
            "cached": False,
        }

    # Cheap wins in parallel: Windows TCP ports + ARP table.
    windows_ports: list[dict] = []
    arp_hosts: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_win = pool.submit(_windows_tcp_printers)
        fut_arp = pool.submit(_arp_live_hosts)
        try:
            windows_ports = fut_win.result(timeout=min(POWERSHELL_TIMEOUT_SEC + 0.3, _remaining()))
        except Exception:
            windows_ports = []
        try:
            arp_hosts = fut_arp.result(timeout=min(ARP_TIMEOUT_SEC + 0.3, _remaining()))
        except Exception:
            arp_hosts = set()

    seed: set[str] = set()
    for net in networks:
        for ip in arp_hosts:
            try:
                if ipaddress.ip_address(ip) in net:
                    seed.add(ip)
            except ValueError:
                continue
        for suffix in (50, 51, 100, 101, 150, 200, 201, 220, 230, 250):
            seed.add(str(net.network_address + suffix))
        seed.add(str(net.network_address + 1))

    # Probe ARP + Windows hosts immediately (no ping wait).
    immediate_targets = set(arp_hosts)
    for row in windows_ports:
        host = str(row.get("host") or "").strip()
        if host:
            immediate_targets.add(host)

    def _hosts_in_networks(hosts: Iterable[str]) -> list[str]:
        selected = []
        for host in hosts:
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                # Allow simple hostnames discovered via Windows ports.
                selected.append(host)
                continue
            if any(ip in net for net in networks):
                selected.append(host)
        return sorted(
            selected,
            key=lambda h: tuple(int(p) for p in h.split("."))
            if re.fullmatch(r"\d+(?:\.\d+){3}", h)
            else (999, h),
        )

    probe_targets = _hosts_in_networks(immediate_targets)

    found: list[dict] = []
    scanned = 0

    def _probe_many(hosts: list[str], deadline: float) -> None:
        nonlocal scanned, found
        if not hosts or deadline <= 0.05:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_probe_host, host): host for host in hosts}
            try:
                for future in concurrent.futures.as_completed(futures, timeout=deadline):
                    scanned += 1
                    try:
                        result = future.result()
                    except Exception:
                        continue
                    if result:
                        found.append(result)
            except concurrent.futures.TimeoutError:
                for future in futures:
                    if future.done():
                        scanned += 1
                        try:
                            result = future.result()
                        except Exception:
                            continue
                        if result:
                            found.append(result)
                for future in futures:
                    future.cancel()

    _probe_many(probe_targets, min(PHASE_DEADLINE_SEC, 2.0, _remaining()))
    printers = _merge_printers(found, windows_ports)

    # Only ping DHCP guesses when nothing found yet and time remains.
    if not printers and _remaining() > 0.4:
        remaining_seed = [h for h in seed if h not in immediate_targets]
        live = _ping_sweep(
            remaining_seed, deadline=min(PHASE_DEADLINE_SEC, 2.0, _remaining())
        )
        extra_targets = _hosts_in_networks(live)
        _probe_many(extra_targets, min(PHASE_DEADLINE_SEC, _remaining()))
        printers = _merge_printers(found, windows_ports)
        probe_targets = sorted(set(probe_targets) | set(extra_targets))

    # Thorough fallback: full /24 only when still empty.
    if thorough and not printers:
        all_hosts: list[str] = []
        for net in networks:
            all_hosts.extend(str(host) for host in net.hosts())
        remaining = [h for h in all_hosts if h not in probe_targets]
        _probe_many(remaining, min(THOROUGH_DEADLINE_SEC, max(_remaining(THOROUGH_DEADLINE_SEC), 1.0)))
        printers = _merge_printers(found, windows_ports)

    usb_hints: list[str] = []
    if not printers and _remaining() > 0.5:
        usb_printers = _windows_pos_usb_printers()
        usb_hints = [row["name"] for row in usb_printers]
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    hint = ""
    if not printers:
        hint = (
            "No Wi‑Fi printers responded on this network. "
            "Power on the Wi‑Fi printer and join it to the same Wi‑Fi as this PC, then scan again."
        )
        if usb_hints:
            hint += (
                " USB printers on this PC belong in the USB channel"
                f" ({', '.join(usb_hints[:3])})."
            )

    result = {
        "ok": True,
        "printers": printers,
        "usb_printers": [],  # never mix USB into Wi‑Fi results
        "scanned_hosts": scanned,
        "live_hosts": len(probe_targets),
        "candidate_hosts": len(probe_targets),
        "networks": [str(net) for net in networks],
        "local_ips": local_ips,
        "thorough": thorough,
        "elapsed_ms": elapsed_ms,
        "usb_hints": usb_hints,
        "hint": hint,
        "cached": False,
    }
    if use_cache:
        _cache_set(cache_key, result)
    return result
