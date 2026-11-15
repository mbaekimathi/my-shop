#!/usr/bin/env python3
"""MY-SHOP local print agent.

Polls the hosted MY-SHOP server for queued receipt jobs and prints them to
the shop's LAN Wi-Fi printer (raw TCP, usually port 9100). It also scans the
local network for printers about once a minute — and immediately when the
shop floor presses Scan — and reports them, so the Wi-Fi printer picker can
list them even though the server cannot see the shop LAN. Run this on any
computer in the shop that can reach the printer — e.g. the till PC.

Setup:
    1. Put this file anywhere and create print_agent.json next to it:
         {"poll_url": "https://example.com/print-agent/1/jobs/",
          "token": "your-shop-agent-token"}
       The ready-made config is shown under
       Shop settings → Receipt → Cloud print agent.
    2. Run:  python print_agent.py

Keep it running while the shop is open. Python 3.8+ only — no extra packages.
On Windows you can autostart it via Task Scheduler or a shortcut in
shell:startup.

Options:
    --config PATH   use a different config file (default: print_agent.json
                    next to this script)
    --once          poll once, print anything queued, then exit
    --interval SEC  seconds between polls (default: 2)

Extra config keys (optional):
    "report_url": override the derived .../report/ endpoint
    "extra_subnets": ["192.168.100.0/24"] — also scan subnets the PC can
                     route to but isn't directly on (e.g. a second SSID)
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

CONNECT_TIMEOUT = 5.0
WRITE_TIMEOUT = 10.0
MAX_BACKOFF = 30.0

# Windows consoles default to cp1252 which cannot print → or Wi‑Fi names.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# LAN printer discovery: probe each host's first open printer port.
PRINTER_PORTS = (9100, 9101, 9102, 9112, 9200, 10001, 631, 515)
PROBE_TIMEOUT = 0.15
SCAN_WORKERS = 64
SCAN_INTERVAL = 45.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"Config not found: {path}\nSee the setup steps in this file's docstring.")
    except json.JSONDecodeError as exc:
        sys.exit(f"Config {path} is not valid JSON: {exc}")

    poll_url = str(cfg.get("poll_url") or "").strip()
    if not poll_url:
        base = str(cfg.get("base_url") or "").rstrip("/")
        shop_id = cfg.get("shop_id")
        if base and shop_id:
            poll_url = f"{base}/print-agent/{int(shop_id)}/jobs/"
    if not poll_url or not str(cfg.get("token") or "").strip():
        sys.exit("Config must set poll_url and token.")
    cfg["poll_url"] = poll_url
    return cfg


def ack_url(poll_url: str, job_id: int) -> str:
    return f"{poll_url.rstrip('/')}/{job_id}/"


def report_url(cfg: dict) -> str:
    explicit = str(cfg.get("report_url") or "").strip()
    if explicit:
        return explicit
    url = str(cfg["poll_url"]).rstrip("/")
    if url.endswith("/jobs"):
        return url[: -len("/jobs")] + "/report/"
    return url + "/report/"


def request(url: str, *, token: str, method: str = "GET", payload: dict | None = None,
            timeout: float = 15.0) -> tuple[int, dict]:
    body = None
    headers = {"Accept": "application/json", "X-Print-Agent-Token": token}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            return exc.code, {}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return 0, {"ok": False, "error": str(exc)}


def local_ipv4_addresses() -> set[str]:
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
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
    return found


def private_networks(ips) -> list:
    nets = set()
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if isinstance(addr, ipaddress.IPv4Address) and (
            addr.is_private or addr.is_link_local
        ):
            nets.add(ipaddress.ip_network(f"{ip}/24", strict=False))
    return sorted(nets)


def _reverse_name(host: str) -> str:
    try:
        socket.setdefaulttimeout(0.15)
        name, _, _ = socket.gethostbyaddr(host)
        return (name or "").split(".")[0] or ""
    except OSError:
        return ""
    finally:
        socket.setdefaulttimeout(None)


def probe_host(host: str, timeout: float = PROBE_TIMEOUT) -> dict | None:
    for port in PRINTER_PORTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except OSError:
            continue
        label = _reverse_name(host) or "Network printer"
        return {
            "host": host,
            "port": port,
            "name": f"{label} · {host}",
            "detail": f"Raw :{port}",
        }
    return None


def scan_lan(extra_subnets=None) -> tuple[list, int]:
    """TCP-probe this PC's private /24s (plus any extra_subnets) for printers."""
    nets = list(private_networks(local_ipv4_addresses()))
    for raw in extra_subnets or []:
        try:
            nets.append(ipaddress.ip_network(str(raw), strict=False))
        except ValueError:
            log(f"Ignoring invalid extra_subnet: {raw}")
    hosts: list[str] = []
    seen: set[str] = set()
    for net in nets:
        for h in net.hosts():
            if str(h) not in seen:
                seen.add(str(h))
                hosts.append(str(h))
    printers: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for result in pool.map(probe_host, hosts):
            if result:
                printers.append(result)
    printers.sort(key=lambda row: tuple(int(p) for p in row["host"].split(".")))
    return printers, len(hosts)


def report_scan(cfg: dict, printers: list, scanned: int) -> None:
    status, data = request(
        report_url(cfg),
        token=cfg["token"],
        method="POST",
        payload={"printers": printers, "scanned": scanned},
    )
    if status == 200 and data.get("ok"):
        log(f"Reported {len(printers)} LAN printer(s) ({scanned} hosts scanned)")
    else:
        log(f"Printer report failed: {data.get('error') or f'HTTP {status}'}")


def run_scan_request(cfg: dict, req: dict) -> None:
    """On-demand scan asked for via the shop floor's Scan button.

    Probes the requested hosts directly (they may be on a routed subnet
    outside this PC's /24s) and sweeps this PC's LAN, then reports.
    """
    hosts = []
    for raw in (req.get("hosts") or [])[:8]:
        host = str(raw or "").strip()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            hosts.append(str(ip))
    subnets = list(cfg.get("extra_subnets") or [])
    for raw in (req.get("subnets") or [])[:8]:
        if raw not in subnets:
            subnets.append(raw)

    found: dict[str, dict] = {}
    if hosts:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for row in pool.map(lambda h: probe_host(h, timeout=0.6), hosts):
                if row:
                    found[row["host"]] = row
    try:
        printers, scanned = scan_lan(subnets)
    except Exception as exc:
        log(f"Requested LAN scan failed: {exc}")
        printers, scanned = [], 0
    for row in printers:
        found.setdefault(row["host"], row)
    log(
        f"On-demand scan: {len(found)} printer(s) "
        f"({scanned + len(hosts)} hosts probed)"
    )
    report_scan(cfg, list(found.values()), scanned + len(hosts))


def scanner_loop(cfg: dict, stop: threading.Event) -> None:
    """Periodically scan the shop LAN and report printers to the server."""
    while not stop.is_set():
        try:
            printers, scanned = scan_lan(cfg.get("extra_subnets"))
        except Exception as exc:
            log(f"Printer scan failed: {exc}")
            printers, scanned = [], 0
        report_scan(cfg, printers, scanned)
        stop.wait(SCAN_INTERVAL)


def print_job(job: dict) -> None:
    host = str(job.get("host") or "").strip()
    port = int(job.get("port") or 9100)
    data = base64.b64decode(job.get("data") or "")
    if not host or not data:
        raise ValueError("Job is missing printer host or data.")
    with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as sock:
        sock.settimeout(WRITE_TIMEOUT)
        sock.sendall(data)


def run_once(cfg: dict) -> bool:
    """One poll/print cycle. Returns True when the server answered."""
    status, data = request(cfg["poll_url"], token=cfg["token"])
    if status == 401:
        sys.exit(
            "Server rejected the agent token (401). Regenerate the token under "
            "Shop settings → Receipt → Cloud print agent and update print_agent.json."
        )
    if status != 200 or not data.get("ok"):
        log(f"Poll failed: {data.get('error') or f'HTTP {status}'}")
        return False

    for job in data.get("jobs") or []:
        job_id = job.get("id")
        try:
            print_job(job)
        except Exception as exc:
            log(f"Job {job_id} failed → {job.get('host')}:{job.get('port')}: {exc}")
            request(
                ack_url(cfg["poll_url"], int(job_id)),
                token=cfg["token"],
                method="POST",
                payload={"status": "failed", "error": str(exc)[:240]},
            )
            continue
        request(
            ack_url(cfg["poll_url"], int(job_id)),
            token=cfg["token"],
            method="POST",
            payload={"status": "done"},
        )
        log(f"Printed job {job_id} → {job.get('host')}:{job.get('port')}")

    # On-demand scan requested from the shop floor — run it off the poll
    # thread so printing isn't delayed; dedupe by request timestamp.
    req = data.get("scan_request") or {}
    req_at = req.get("at")
    if req_at and req_at != cfg.get("_last_scan_request"):
        cfg["_last_scan_request"] = req_at
        threading.Thread(
            target=lambda: _run_scan_request_safe(cfg, req), daemon=True
        ).start()
    return True


def _run_scan_request_safe(cfg: dict, req: dict) -> None:
    try:
        run_scan_request(cfg, req)
    except Exception as exc:
        log(f"Requested scan failed: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MY-SHOP local print agent")
    parser.add_argument("--config", default="", help="Path to print_agent.json")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds")
    args = parser.parse_args()

    config_path = args.config or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "print_agent.json"
    )
    cfg = load_config(config_path)
    log(f"Agent polling {cfg['poll_url']} every {args.interval:g}s")
    if args.once:
        run_once(cfg)
        try:
            printers, scanned = scan_lan(cfg.get("extra_subnets"))
            report_scan(cfg, printers, scanned)
        except Exception as exc:
            log(f"Printer scan failed: {exc}")
        return

    stop = threading.Event()
    scanner = threading.Thread(
        target=scanner_loop, args=(cfg, stop), daemon=True
    )
    scanner.start()

    backoff = args.interval
    while True:
        try:
            ok = run_once(cfg)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # keep the agent alive through anything
            log(f"Unexpected error: {exc}")
            ok = False
        backoff = args.interval if ok else min(MAX_BACKOFF, max(backoff * 2, 5))
        try:
            time.sleep(backoff)
        except KeyboardInterrupt:
            break
    stop.set()
    log("Stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
