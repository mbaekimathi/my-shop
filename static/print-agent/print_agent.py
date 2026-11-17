#!/usr/bin/env python3
"""MY-SHOP local print agent.

Polls the hosted MY-SHOP server for queued receipt jobs and prints them to
the shop's LAN Wi-Fi printer (raw TCP, usually port 9100). It also scans the
local network for printers about once a minute — and immediately when the
shop floor presses Scan — and reports them, so the Wi-Fi printer picker can
list them even though the server cannot see the shop LAN. Run this on any
computer in the shop that can reach the printer — e.g. the till PC.

Setup:
    1. Put this file anywhere and run it:  python print_agent.py
    2. When asked, paste the Agent setup link shown under
       Shop settings → Receipt → Cloud print agent. The agent fetches
       its config from the server and saves print_agent.json next to
       this file — nothing to type by hand.
       (You can also click "download print_agent.json" on that page and
       save it next to this file, or create it manually:
         {"poll_url": "https://example.com/print-agent/1/jobs/",
          "token": "your-shop-agent-token"})

Keep it running while the shop is open. Python 3.8+ only — no extra packages.
On Windows you can autostart it via Task Scheduler or a shortcut in
shell:startup.

Options:
    --config PATH   use a different config file (default: print_agent.json
                    next to this script)
    --setup URL     fetch the agent config from this setup link now
    --once          poll once, print anything queued, then exit
    --interval SEC  seconds between polls (default: 2)

Extra config keys (optional):
    "report_url": override the derived .../report/ endpoint
    "extra_subnets": ["192.168.100.0/24"] — also scan subnets the PC can
                     route to but isn't directly on (e.g. a second SSID)
    "wide_scan": false — don't sweep the built-in list of common printer
                 subnets (192.168.x, 10.x, 172.16.x) beyond this PC's own;
                 on by default so a printer on another segment is found
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ipaddress
import json
import os
import socket
import subprocess
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
# Shorter port list for subnets outside this PC's own — keeps the wide sweep
# quick while covering what receipt printers actually listen on.
WIDE_PORTS = (9100, 9101, 631, 515)
PROBE_TIMEOUT = 0.15
SCAN_WORKERS = 64
SCAN_INTERVAL = 45.0

# Private subnets beyond this PC's own that printers commonly live on —
# second routers, guest SSIDs, and the fixed defaults of Wi‑Fi print modules
# (many thermal dongles sit on 192.168.123.x / 192.168.10.x). Swept during
# "wide" scans so a printer on a different segment still shows up. Probing
# an unreachable subnet just times out — no harm done.
COMMON_PRINTER_SUBNETS = (
    "192.168.0.0/24", "192.168.1.0/24", "192.168.2.0/24", "192.168.3.0/24",
    "192.168.4.0/24", "192.168.5.0/24", "192.168.8.0/24", "192.168.10.0/24",
    "192.168.11.0/24", "192.168.31.0/24", "192.168.50.0/24",
    "192.168.100.0/24", "192.168.123.0/24", "192.168.168.0/24",
    "192.168.254.0/24", "10.0.0.0/24", "10.1.1.0/24", "172.16.0.0/24",
)
# Canary probe for foreign subnets — ports routers usually answer on, plus
# ICMP ping. A subnet whose likely gateways are all silent is almost
# certainly unroutable from here, so we skip its 254 hosts instead of
# flooding the NAT table with dead connects.
GATEWAY_PORTS = (80, 443, 22, 23, 53, 8080)
GATEWAY_TIMEOUT = 0.3


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_setup(setup_url: str) -> dict:
    """Fetch {"poll_url", "token"} from the setup link on the settings page."""
    setup_url = setup_url.strip()
    req = urllib.request.Request(
        setup_url, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:
        sys.exit(f"Could not fetch the agent config from the setup link: {exc}")
    cfg = {
        "poll_url": str(data.get("poll_url") or "").strip(),
        "token": str(data.get("token") or "").strip(),
    }
    if not cfg["poll_url"] or not cfg["token"]:
        sys.exit(
            "The setup link did not return an agent config. Copy the whole "
            "link from Shop settings → Receipt → Cloud print agent."
        )
    return cfg


def prompt_setup_link() -> str:
    if not sys.stdin.isatty():
        return ""
    print()
    print("Paste the Agent setup link from")
    print("Shop settings → Receipt → Cloud print agent:")
    try:
        return input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def save_config(path: str, cfg: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    log(f"Saved agent config → {path}")


def relink(cfg: dict) -> None:
    """Re-pair after the token was regenerated (server answered 401)."""
    url = prompt_setup_link()
    if not url:
        sys.exit(
            "Server rejected the agent token (401). Regenerate the token under "
            "Shop settings → Receipt → Cloud print agent and update "
            "print_agent.json — or run again and paste the new setup link."
        )
    fresh = fetch_setup(url)
    path = str(cfg.get("_config_path") or "").strip()
    if path:
        save_config(path, fresh)
    cfg.update(fresh)
    log("Agent re-linked to the server.")


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


def probe_host(host: str, timeout: float = PROBE_TIMEOUT,
               ports=PRINTER_PORTS) -> dict | None:
    for port in ports:
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


def _ping_host(host: str, timeout_ms: int = 250) -> bool:
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
    try:
        return subprocess.run(
            cmd, capture_output=True, timeout=1.5, check=False
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def subnet_alive(net, timeout: float = GATEWAY_TIMEOUT) -> bool:
    """Cheap canary: is anything answering at this subnet's likely gateways?

    Checks x.x.x.1 and x.x.x.254 on common router TCP ports and ICMP. Used
    to skip dead subnets before sweeping all their hosts.
    """
    candidates = {str(net.network_address + 1)}
    try:
        candidates.add(str(net.broadcast_address - 1))
    except (AttributeError, TypeError):
        pass
    for ip in candidates:
        if _ping_host(ip):
            return True
        for port in GATEWAY_PORTS:
            try:
                with socket.create_connection((ip, port), timeout=timeout):
                    return True
            except OSError:
                continue
    return False


def _subnet_hosts(nets) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    for net in nets:
        for h in net.hosts():
            if str(h) not in seen:
                seen.add(str(h))
                hosts.append(str(h))
    return hosts


def scan_lan(extra_subnets=None, wide: bool = False) -> tuple[list, int]:
    """TCP-probe for printers.

    Always sweeps this PC's private /24s plus any extra_subnets. When
    ``wide`` is set, also sweeps COMMON_PRINTER_SUBNETS — other private
    ranges a printer on another segment (or a Wi‑Fi dongle's default net)
    might answer on.
    """
    nets = list(private_networks(local_ipv4_addresses()))
    for raw in extra_subnets or []:
        try:
            nets.append(ipaddress.ip_network(str(raw), strict=False))
        except ValueError:
            log(f"Ignoring invalid extra_subnet: {raw}")
    targets: dict[str, tuple] = {
        h: PRINTER_PORTS for h in _subnet_hosts(nets)
    }
    if wide:
        wide_nets = []
        for raw in COMMON_PRINTER_SUBNETS:
            try:
                net = ipaddress.ip_network(raw)
            except ValueError:
                continue
            wide_nets.append(net)
        local_nets = {str(net) for net in nets}
        foreign = [n for n in wide_nets if str(n) not in local_nets]
        # Canary-check foreign subnets in parallel — a /24 whose gateways
        # don't answer is unroutable and not worth 254 dead connects.
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            live = [
                net
                for net, ok in zip(foreign, pool.map(subnet_alive, foreign))
                if ok
            ]
        for host in _subnet_hosts(live):
            targets.setdefault(host, WIDE_PORTS)
    printers: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        futures = {
            pool.submit(probe_host, host, ports=ports): host
            for host, ports in targets.items()
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception:
                continue
            if result:
                printers.append(result)
    printers.sort(key=lambda row: tuple(int(p) for p in row["host"].split(".")))
    return printers, len(targets)


def report_scan(cfg: dict, printers: list, scanned: int) -> None:
    payload = {"printers": printers, "scanned": scanned}
    status, data = request(
        report_url(cfg), token=cfg["token"], method="POST", payload=payload
    )
    if status == 0:
        # Transient drop (NAT busy during a big sweep) — retry once.
        time.sleep(3)
        status, data = request(
            report_url(cfg), token=cfg["token"], method="POST",
            payload=payload,
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

    # Wide pass over the common printer subnets — slower, so it reports in a
    # second post instead of delaying the quick answer above.
    try:
        printers, wide_scanned = scan_lan(subnets, wide=True)
    except Exception as exc:
        log(f"Requested wide scan failed: {exc}")
        return
    for row in printers:
        found.setdefault(row["host"], row)
    log(
        f"Wide scan: {len(printers)} printer(s) "
        f"({wide_scanned} hosts probed)"
    )
    report_scan(
        cfg, list(found.values()), scanned + len(hosts) + wide_scanned
    )


def scanner_loop(cfg: dict, stop: threading.Event) -> None:
    """Periodically scan the shop LAN and report printers to the server."""
    wide = bool(cfg.get("wide_scan", True))
    while not stop.is_set():
        try:
            printers, scanned = scan_lan(cfg.get("extra_subnets"), wide=wide)
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
        relink(cfg)
        return True
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
    parser.add_argument("--setup", default="", help="Agent setup link to fetch config from")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds")
    args = parser.parse_args()

    config_path = args.config or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "print_agent.json"
    )
    if not os.path.exists(config_path):
        setup_url = args.setup or prompt_setup_link()
        if not setup_url:
            sys.exit(
                f"Config not found: {config_path}\n"
                "Run again and paste the Agent setup link from Shop settings "
                "→ Receipt → Cloud print agent, or save the downloaded "
                "print_agent.json next to this file."
            )
        save_config(config_path, fetch_setup(setup_url))
    cfg = load_config(config_path)
    cfg["_config_path"] = config_path
    log(f"Agent polling {cfg['poll_url']} every {args.interval:g}s")
    if args.once:
        run_once(cfg)
        try:
            printers, scanned = scan_lan(
                cfg.get("extra_subnets"),
                wide=bool(cfg.get("wide_scan", True)),
            )
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
