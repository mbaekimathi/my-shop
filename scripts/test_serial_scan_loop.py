"""
Serial scan improve loop — verify wiring + QR/image decode until green.

Usage:
  python scripts/test_serial_scan_loop.py
  python scripts/test_serial_scan_loop.py --iterations 5
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FIX_HINTS = {
    "vendor lib": "Ensure static/vendor/html5-qrcode.min.js exists.",
    "serial-scan.js": "static/js/serial-scan.js must export MyShopSerialScan.",
    "page wiring": "Each serial page must include serial-scan.js.",
    "data-serial-scan": "Filter search inputs need data-serial-scan.",
    "enhance buttons": "Scanner should inject .serial-scan-btn next to inputs.",
    "apply serial": "MyShopSerialScan.apply must fill the active input.",
    "decode QR image": "html5-qrcode scanFile must read generated QR PNGs.",
}


def check_static_files() -> list[tuple[str, bool, str]]:
    checks = []
    vendor = ROOT / "static" / "vendor" / "html5-qrcode.min.js"
    js = ROOT / "static" / "js" / "serial-scan.js"
    css = ROOT / "static" / "css" / "app.css"
    checks.append(("vendor lib", vendor.exists() and vendor.stat().st_size > 10_000, str(vendor)))
    checks.append(("serial-scan.js", js.exists() and "MyShopSerialScan" in js.read_text(encoding="utf-8"), str(js)))
    checks.append(
        (
            "css scanner styles",
            ".serial-scan-modal" in css.read_text(encoding="utf-8"),
            "app.css includes .serial-scan-modal",
        )
    )
    return checks


def check_page_wiring() -> list[tuple[str, bool, str]]:
    required = {
        "stock management": ROOT / "templates" / "items" / "stock_management.html",
        "buy stock": ROOT / "templates" / "shops" / "my_shop_buy_stock.html",
        "workspace": ROOT / "templates" / "shops" / "my_shop_workspace.html",
        "stock requests": ROOT / "templates" / "shops" / "my_shop_stock_requests.html",
        "serial detail": ROOT / "templates" / "items" / "stock_serial_detail.html",
        "return client": ROOT / "templates" / "items" / "stock_serial_return_client.html",
    }
    results = []
    for label, path in required.items():
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        ok = "serial-scan.js" in text and "html5-qrcode" in text
        results.append((f"page wiring:{label}", ok, str(path)))
    detail = (ROOT / "templates" / "items" / "stock_serial_detail.html").read_text(encoding="utf-8")
    returns = (ROOT / "templates" / "items" / "stock_serial_return_client.html").read_text(encoding="utf-8")
    results.append(("data-serial-scan:detail", "data-serial-scan" in detail, "stock_serial_detail.html"))
    results.append(("data-serial-scan:returns", "data-serial-scan" in returns, "stock_serial_return_client.html"))
    return results


def make_qr_png(path: Path, payload: str) -> None:
    import qrcode

    img = qrcode.make(payload)
    img.save(path)


class _RootHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format, *args):  # noqa: A003
        return


def start_static_server() -> tuple[socketserver.TCPServer, str]:
    # Bind ephemeral port on localhost.
    server = socketserver.TCPServer(("127.0.0.1", 0), _RootHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def run_playwright_checks(base_url: str, qr_path: Path, expected: str) -> list[tuple[str, bool, str]]:
    from playwright.sync_api import sync_playwright

    results: list[tuple[str, bool, str]] = []
    harness = f"{base_url}/scripts/serial_scan_harness.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(harness, wait_until="networkidle", timeout=30_000)
        page.wait_for_function("() => window.__harnessReady === true", timeout=15_000)

        # Buttons injected for all four input kinds.
        for attr in (
            "data-harness-stock-input",
            "data-harness-sale-input",
            "data-harness-transfer-input",
            "data-harness-filter-input",
        ):
            count = page.locator(f"[{attr}]").evaluate(
                """(el) => {
                  const row = el.closest('.stock-serial-row, .shop-serial-row');
                  const wrap = el.closest('.stock-serial-input-wrap, .shop-serial-input-wrap, .stock-request-serial-wrap, .serial-scan-field-wrap');
                  const host = row || wrap || el.parentElement;
                  return host ? host.querySelectorAll('[data-serial-scan-open]').length : 0;
                }"""
            )
            results.append((f"enhance buttons:{attr}", count >= 1, f"found {count}"))

        # Manual apply into stock input.
        applied = page.evaluate(
            """(serial) => {
              const input = document.querySelector('[data-harness-stock-input]');
              window.MyShopSerialScan.open(input);
              const ok = window.MyShopSerialScan.apply(serial);
              return { ok, value: input.value };
            }""",
            expected,
        )
        results.append(
            (
                "apply serial",
                bool(applied.get("ok")) and applied.get("value") == expected.upper(),
                str(applied),
            )
        )

        # Decode QR image via browser File + html5-qrcode path.
        decoded = page.evaluate(
            """async (qrUrl) => {
              const res = await fetch(qrUrl);
              const blob = await res.blob();
              const file = new File([blob], 'serial-qr.png', { type: 'image/png' });
              try {
                const serial = await window.MyShopSerialScan.decodeImageFile(file);
                return { ok: true, serial };
              } catch (err) {
                return { ok: false, error: String(err && err.message || err) };
              }
            }""",
            f"{base_url}/{qr_path.relative_to(ROOT).as_posix()}",
        )
        results.append(
            (
                "decode QR image",
                bool(decoded.get("ok"))
                and str(decoded.get("serial") or "").upper() == expected.upper(),
                str(decoded),
            )
        )

        # Opening modal should not throw and should expose dialog.
        modal_ok = page.evaluate(
            """() => {
              const input = document.querySelector('[data-harness-filter-input]');
              window.MyShopSerialScan.open(input);
              const modal = document.querySelector('[data-serial-scan-modal]');
              const open = modal && modal.classList.contains('is-open');
              window.MyShopSerialScan.close();
              return Boolean(open);
            }"""
        )
        results.append(("modal open/close", bool(modal_ok), "modal open flag"))

        # Dynamically added serial input must get a scan button via MutationObserver.
        dynamic_ok = page.evaluate(
            """() => new Promise((resolve) => {
              const row = document.createElement('div');
              row.className = 'stock-serial-row';
              row.setAttribute('data-harness-dynamic-row', '');
              const input = document.createElement('input');
              input.type = 'text';
              input.setAttribute('data-stock-serial-input', '');
              input.setAttribute('data-harness-dynamic-input', '');
              row.appendChild(input);
              document.querySelector('.harness').appendChild(row);
              window.setTimeout(() => {
                const btn = row.querySelector('[data-serial-scan-open]');
                resolve(Boolean(btn));
              }, 80);
            })"""
        )
        results.append(("enhance dynamic row", bool(dynamic_ok), "observer injects scan btn"))

        browser.close()

    return results


def print_report(iteration: int, checks: list[tuple[str, bool, str]]) -> bool:
    print(f"\n=== Serial scan loop iteration {iteration} ===")
    all_ok = True
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name} — {detail}")
        if not ok:
            all_ok = False
            key = name.split(":")[0]
            hint = FIX_HINTS.get(key) or FIX_HINTS.get(name)
            if hint:
                print(f"         hint: {hint}")
    return all_ok


def maybe_autofix(checks: list[tuple[str, bool, str]]) -> bool:
    """Apply safe mechanical fixes when possible. Returns True if something changed."""
    changed = False
    failed = {name for name, ok, _ in checks if not ok}

    if any(name.startswith("page wiring:") for name in failed):
        # Already expected to be wired; no silent rewrite of templates here.
        pass

    js_path = ROOT / "static" / "js" / "serial-scan.js"
    if "serial-scan.js" in failed and js_path.exists():
        text = js_path.read_text(encoding="utf-8")
        if "window.MyShopSerialScan" not in text:
            print("  autofix: cannot invent API export automatically")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Serial scan improve loop")
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    server = None
    tmp_dir = Path(tempfile.mkdtemp(prefix="serial-scan-"))
    qr_path = ROOT / "scripts" / "_serial_scan_fixture_qr.png"
    expected = "SN-TEST-88421"

    try:
        make_qr_png(qr_path, expected)
        server, base_url = start_static_server()
        # Give server a tick.
        time.sleep(0.15)

        overall_ok = False
        for i in range(1, args.iterations + 1):
            checks: list[tuple[str, bool, str]] = []
            checks.extend(check_static_files())
            checks.extend(check_page_wiring())
            try:
                checks.extend(run_playwright_checks(base_url, qr_path, expected))
            except Exception as exc:  # noqa: BLE001
                checks.append(("playwright run", False, str(exc)))

            ok = print_report(i, checks)
            if ok:
                overall_ok = True
                print("\nAll serial scan checks passed.")
                break

            changed = maybe_autofix(checks)
            if not changed and i < args.iterations:
                print("  No autofix applied; re-running to confirm stability…")
                time.sleep(0.4)
        else:
            print("\nSerial scan loop finished with failures.")
            return 1

        return 0 if overall_ok else 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if qr_path.exists():
            try:
                qr_path.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
