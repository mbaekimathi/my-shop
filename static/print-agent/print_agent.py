#!/usr/bin/env python3
"""MY-SHOP local print agent.

Polls the hosted MY-SHOP server for queued receipt jobs and prints them to
the shop's LAN Wi-Fi printer (raw TCP, usually port 9100). Run this on any
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
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

CONNECT_TIMEOUT = 5.0
WRITE_TIMEOUT = 10.0
MAX_BACKOFF = 30.0


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
    return True


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
        return

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
    log("Stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
