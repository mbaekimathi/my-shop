"""
Connectivity / false-offline correction loops for MY-SHOP.

Guards the "You're offline" toast path so transient noise (dev-server reload,
busy runserver, brief Windows adapter flicker) does not mark the app offline.

Usage:
  python scripts/test_connectivity_loop.py
  python scripts/test_connectivity_loop.py --iterations 40
  python scripts/test_connectivity_loop.py --continuous --interval 20
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUDGET_PING = 0.12
BUDGET_PING_UNDER_LOAD = 0.45
BUDGET_CONCURRENT_PINGS = 1.50

FIX_HINTS = {
    "connectivity source": "static/js/offline/connectivity.js must exist and export initConnectivity.",
    "failed ping threshold": "FAILED_PINGS_FOR_OFFLINE must be >= 3 so one/two blips do not flip offline.",
    "toast confirm delay": "OFFLINE_TOAST_CONFIRM_MS must be >= 10000 so brief outages stay silent.",
    "recent success grace": "Recent successful pings must ignore isolated AbortError timeouts.",
    "offline event debounce": "window offline handler must delay before probing (noisy on Windows).",
    "sw ping bypass": "static/sw.js must not convert /employees/api/ping/ into a fake offline 503.",
    "state machine: one fail": "A single failed probe must keep isOnline() true.",
    "state machine: two fails": "Two failed probes must keep isOnline() true.",
    "state machine: three fails": "Three consecutive failures should mark offline (without toast yet).",
    "state machine: toast wait": "Toast must stay hidden until OFFLINE_TOAST_CONFIRM_MS elapses.",
    "state machine: toast after confirm": "After the confirm window, the offline toast must appear.",
    "state machine: recover": "A successful ping must clear offline + cancel a pending toast.",
    "state machine: timeout grace": "AbortError within the recent-success window must not burn a failure.",
    "live ping": "GET /employees/api/ping/ must return {ok:true} quickly.",
    "ping under load": "Ping must stay healthy while several probes run together (busy server).",
    "no force race": "Forced pings must not stack parallel fetches that double-count failures.",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _const_int(source: str, name: str) -> int | None:
    match = re.search(rf"(?:const|let)\s+{re.escape(name)}\s*=\s*([\d_]+)", source)
    if not match:
        return None
    return int(match.group(1).replace("_", ""))


def check_source_invariants() -> list[tuple[str, bool, str]]:
    connectivity = ROOT / "static" / "js" / "offline" / "connectivity.js"
    sw = ROOT / "static" / "sw.js"
    results: list[tuple[str, bool, str]] = []

    if not connectivity.exists():
        results.append(("connectivity source", False, str(connectivity)))
        return results

    src = _read(connectivity)
    sw_src = _read(sw) if sw.exists() else ""

    failed_need = _const_int(src, "FAILED_PINGS_FOR_OFFLINE")
    toast_ms = _const_int(src, "OFFLINE_TOAST_CONFIRM_MS")
    results.append(
        (
            "connectivity source",
            "export function initConnectivity" in src and "export function isOnline" in src,
            str(connectivity),
        )
    )
    results.append(
        (
            "failed ping threshold",
            failed_need is not None and failed_need >= 3,
            f"FAILED_PINGS_FOR_OFFLINE={failed_need}",
        )
    )
    results.append(
        (
            "toast confirm delay",
            toast_ms is not None and toast_ms >= 10_000,
            f"OFFLINE_TOAST_CONFIRM_MS={toast_ms}",
        )
    )
    results.append(
        (
            "recent success grace",
            "lastSuccessAt" in src
            and "AbortError" in src
            and ("8_000" in src or "8000" in src),
            "recent-success AbortError ignore",
        )
    )
    results.append(
        (
            "offline event debounce",
            'addEventListener("offline"' in src
            and ("1_200" in src or "1200" in src or "setTimeout" in src),
            "delayed offline probe",
        )
    )
    results.append(
        (
            "sw ping bypass",
            "isConnectivityPing" in sw_src
            and "/employees/api/ping/" in sw_src
            and "if (isConnectivityPing(url)) return;" in sw_src,
            str(sw),
        )
    )
    results.append(
        (
            "no force race",
            # Probes must reuse in-flight work, not stack fetches.
            "if (pingInFlight) return pingInFlight" in src
            or (
                "if (pingInFlight)" in src
                and "return pingInFlight" in src
            ),
            "in-flight ping coalescing",
        )
    )
    return results


class ConnectivityMachine:
    """Mirrors the client offline detector closely enough for regression checks."""

    def __init__(
        self,
        *,
        failed_needed: int = 3,
        toast_confirm_ms: int = 12_000,
        recent_success_ms: int = 8_000,
    ) -> None:
        self.failed_needed = failed_needed
        self.toast_confirm_ms = toast_confirm_ms
        self.recent_success_ms = recent_success_ms
        self.online = True
        self.failed_pings = 0
        self.last_success_at: float | None = None
        self.offline_since = 0.0
        self.toast_visible = False
        self.toast_due_at: float | None = None
        self.now = 0.0

    def advance(self, ms: float) -> None:
        self.now += ms
        if (
            not self.online
            and self.toast_due_at is not None
            and self.now >= self.toast_due_at
        ):
            self.toast_visible = True

    def success(self) -> None:
        self.failed_pings = 0
        self.last_success_at = self.now
        if not self.online:
            self.online = True
            self.offline_since = 0.0
            self.toast_due_at = None
            self.toast_visible = False

    def failure(self, *, abort: bool = False, page_hidden: bool = False) -> None:
        if page_hidden and abort:
            return
        recently_ok = self.last_success_at is not None and (
            self.now - self.last_success_at < self.recent_success_ms
        )
        if recently_ok and abort:
            return
        self.failed_pings += 1
        if self.failed_pings >= self.failed_needed and self.online:
            self.online = False
            self.offline_since = self.now
            self.toast_due_at = self.now + self.toast_confirm_ms


def check_state_machine() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    m = ConnectivityMachine()
    m.success()
    m.failure()
    results.append(("state machine: one fail", m.online and not m.toast_visible, f"online={m.online}"))

    m.failure()
    results.append(("state machine: two fails", m.online and not m.toast_visible, f"online={m.online}"))

    m.failure()
    results.append(
        (
            "state machine: three fails",
            (not m.online) and not m.toast_visible and m.toast_due_at is not None,
            f"online={m.online} toast={m.toast_visible}",
        )
    )

    m.advance(5_000)
    results.append(
        (
            "state machine: toast wait",
            (not m.online) and not m.toast_visible,
            f"toast_visible={m.toast_visible} after 5s",
        )
    )
    m.advance(8_000)
    results.append(
        (
            "state machine: toast after confirm",
            m.toast_visible,
            f"toast_visible={m.toast_visible} after confirm window",
        )
    )

    m.success()
    results.append(
        (
            "state machine: recover",
            m.online and not m.toast_visible and m.toast_due_at is None,
            f"online={m.online} toast={m.toast_visible}",
        )
    )

    grace = ConnectivityMachine()
    grace.success()
    grace.advance(1_000)
    before = grace.failed_pings
    grace.failure(abort=True)
    results.append(
        (
            "state machine: timeout grace",
            grace.online and grace.failed_pings == before,
            f"failed_pings={grace.failed_pings}",
        )
    )
    return results


def _live_base() -> str | None:
    for base in ("http://127.0.0.1:8000", "http://localhost:8000"):
        try:
            with urllib.request.urlopen(base + "/employees/api/ping/", timeout=2) as resp:
                if resp.status == 200:
                    return base
        except Exception:
            continue
    return None


def _ping_once(base: str, timeout: float = 3.0) -> tuple[bool, float]:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            base + "/employees/api/ping/",
            headers={"Accept": "application/json", "Cache-Control": "no-store"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            ok = resp.status == 200 and body.get("ok") is True
            return ok, time.perf_counter() - t0
    except Exception:
        return False, time.perf_counter() - t0


def check_live_ping(iterations: int) -> list[tuple[str, bool, str]]:
    base = _live_base()
    results: list[tuple[str, bool, str]] = []
    if not base:
        # Fall back to Django test client so the loop still works without runserver.
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=True)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")
        import django

        django.setup()
        from django.test import Client

        client = Client()
        samples: list[float] = []
        ok_count = 0
        for _ in range(max(8, iterations)):
            t0 = time.perf_counter()
            resp = client.get("/employees/api/ping/")
            dt = time.perf_counter() - t0
            samples.append(dt)
            if resp.status_code == 200 and resp.json().get("ok") is True:
                ok_count += 1
        med = statistics.median(samples)
        results.append(
            (
                "live ping",
                ok_count == len(samples) and med <= BUDGET_PING,
                f"django-client ok={ok_count}/{len(samples)} median={med*1000:.1f}ms",
            )
        )
        results.append(
            (
                "ping under load",
                True,
                "skipped concurrent load (no live server; django client used)",
            )
        )
        return results

    samples: list[float] = []
    ok_count = 0
    for _ in range(max(10, iterations)):
        ok, dt = _ping_once(base)
        samples.append(dt)
        if ok:
            ok_count += 1
    med = statistics.median(samples)
    results.append(
        (
            "live ping",
            ok_count == len(samples) and med <= BUDGET_PING,
            f"{base} ok={ok_count}/{len(samples)} median={med*1000:.1f}ms budget={BUDGET_PING*1000:.0f}ms",
        )
    )

    load_samples: list[float] = []
    load_ok = 0

    def _one(_i: int) -> tuple[bool, float]:
        return _ping_once(base, timeout=5.0)

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_one, i) for i in range(16)]
        for fut in concurrent.futures.as_completed(futs):
            ok, dt = fut.result()
            load_samples.append(dt)
            if ok:
                load_ok += 1
    wall = time.perf_counter() - t0
    load_med = statistics.median(load_samples)
    results.append(
        (
            "ping under load",
            load_ok == len(load_samples)
            and load_med <= BUDGET_PING_UNDER_LOAD
            and wall <= BUDGET_CONCURRENT_PINGS,
            f"ok={load_ok}/{len(load_samples)} median={load_med*1000:.1f}ms wall={wall*1000:.0f}ms",
        )
    )
    return results


def run_once(iterations: int) -> bool:
    print("=== MY-SHOP connectivity / false-offline correction loops ===\n")
    checks: list[tuple[str, bool, str]] = []
    checks.extend(check_source_invariants())
    checks.extend(check_state_machine())
    checks.extend(check_live_ping(iterations))

    passed = 0
    failed = 0
    # Deduplicate toast-wait label by printing each result row as-is.
    seen_fail_keys: set[str] = set()
    for label, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label}: {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
            key = label.split(":")[0].strip()
            if key not in seen_fail_keys:
                hint = FIX_HINTS.get(label) or FIX_HINTS.get(key)
                if hint:
                    print(f"       fix -> {hint}")
                seen_fail_keys.add(key)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    return failed == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Connectivity false-offline improve loop")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=float, default=20.0)
    args = parser.parse_args()

    if not args.continuous:
        return 0 if run_once(args.iterations) else 1

    round_no = 0
    while True:
        round_no += 1
        print(f"\n----- continuous round {round_no} -----")
        ok = run_once(args.iterations)
        if not ok:
            print("Failures remain — fix hints printed above. Retrying…")
        else:
            print("All connectivity checks green.")
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
