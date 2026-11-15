# MY-SHOP — project notes

## Stack
Django 5 app: `employees` (portal + HR), `shops` (shop floor/POS), `items` (stock),
`pos`, `communications`. Templates in `templates/`, JS in `static/js/`.

## Database
- `MYSQL_ENABLED=True` (default) expects MariaDB/MySQL on 127.0.0.1:3306 (XAMPP).
  Start it with: `"C:\xampp\mysql\bin\mysqld.exe" --defaults-file="C:\xampp\mysql\bin\my.ini"`
- `MYSQL_ENABLED=False` falls back to `db.sqlite3`, but some migrations use
  MySQL-only SQL (`AUTO_INCREMENT`, `ENUM`) — the full test suite requires MySQL.

## Tests
```bash
python manage.py test shops.tests -v 1
```
Session-related suites: `test_single_session`, `test_portal_session_exclusivity`,
`test_shop_portal_session`, `test_switch_login_tmp`.

## Stock modes (Settings → POS → Stock)
- One-of toggles: Items / Shelf / Both → `enable_stock_tracking` / `enable_shelf_mode`
  on `CompanyPosSettings` (+ per-shop `ShopPosSettings` override). See
  `STOCK_MODE_FIELDS` / `stock_mode_of` in `shops/services.py`.
- `ShelfItem` (per shop, name/unit/qty, no price) is never sold at the POS and
  never appears on the shop floor — shelf stock is managed only from Stock
  Management / the shop's Shelf stock page.
- The shop floor catalog (`my_shop_catalog` → `build_shop_catalog_page`) lists
  sellable items only; shelf-only mode returns an empty catalog.

## Item pricing
- Prices are stored per shop on `ShopItemPrice` (`price` + `is_sold` flag);
  `Item.shop_price` is just the lowest/display fallback. `is_sold=False`
  (the "Sold in shops" checkboxes) removes the item from the shop floor
  catalog and blocks checkout.
- A `-`, `0`, or negative entry in a price field means **free**: it stores
  `0.00` and the item stays listed and sellable at KSh 0.00. Explicit 0
  prices bypass the minimum-price floor (`_clamp_price` never raises 0) and
  the checkout below-cost guard (only `0 < unit_price < unit_cost` blocks).
  `resolve_list_price` honours a per-shop 0 row as free. `-`/`0` in
  "Minimum selling price" stores `0.00` (no floor). See `_is_free_price` /
  `validate_item_payload` in `items/services.py`; tests in
  `ShopPriceIsolationTests`.

## Shop floor sidebar toggles (Settings → POS)
- `enable_sidebar_*` on `CompanyPosSettings` (+ per-shop `ShopPosSettings`
  override) are **link-only**: they hide menu links in the `/my-shop/<id>/`
  sidebar (`sidebar_for_my_shop` in `employees/workspace.py`, `_flag` checks).
  They do not drop `my-shop/*` permission keys, do not block the pages by URL,
  and do not affect employee surfaces (e.g. the cashier role dashboard) —
  employees keep access per the permission matrix. Tests:
  `shops/tests/test_pos_toggle_enforcement.py`.

## Action popups on role dashboards
- Sidebar `_action` items render `data-modal-open`/`data-href`; `workspace_base.html`
  opens `[data-modal="<name>"]` when the modal exists on the page, else follows the
  href. `?modal=buy-stock|register-expense` auto-opens on load.
- `expense_modal_context` / `buy_stock_modal_context` (employees/workspace.py) supply
  modal context **only for single-actionable-shop staff**; multi-shop staff fall back
  to the `data-href` (MY-SHOP picker / page).
- Endpoints used by dashboard popups must allow allocation-level access (no floor
  unlock): `_require_allocated_shop_access` — the staff-ID field in the payload
  authorises the write. Used by register expense, open/close day, verify-login-code,
  **buy stock POST and stock-supplier search**. Catalog GETs use
  `_require_shop_read_access`.
- Cashier dashboard (`_cashier_dashboard_links`): no Stock Management link — "Buy
  stock" action (`buy_stock_action`, gated by `my-shop/buy_stock` +
  `enable_sidebar_buy_stock`) opens `shops/partials/buy_stock_modal.html` on
  `role_home.html` (stock-catalog.js + stock-bulk.js loaded when
  `buy_stock_modal`), plus a "Suppliers" link (`analytics/suppliers`).

## Wi‑Fi printing
- Channels (Bluetooth/USB/Wi‑Fi) → `enable_print_*` flags; "Connect to printer"
  lives in the shop floor workspace sidebar, gated by `my-shop/print` permission.
- LAN printer discovery: `shops/printer_discovery.py` (fast scan = ARP + Windows
  ports + common DHCP suffixes; deep scan = ping-sweep then TCP-probe live hosts
  on ports 9100/9101/9102/9112/9200/10001/631/515).
- USB + Bluetooth print **client-side** (WebUSB/Web Serial/Web Bluetooth +
  hidden-iframe `win.print()`); Wi‑Fi prints **server-side** via
  `my_shop_print_relay` (server opens TCP to `host:9100`).
- The Windows-print fallback (`browserPrint` in `shop-printer.js`) renders the
  receipt into a **`srcdoc` iframe** and calls `win.print()` on iframe `load`
  (capped at ~900ms — receipt CSS is inline, fonts.css is only the Manrope
  face). Do not switch back to `doc.write()` + `contentWindow.onload`: that
  load hook can miss, and checkout/delivery/goods-received callers race the
  print against a page navigation, so a late `win.print()` is destroyed
  before the dialog opens.
- Multi-copy sales (customer/staff/cashier toggles) go through
  `browserPrintCopies` / `printReceiptCopies`: on the Windows-dialog path
  (USB "Windows printer" mode or browser fallback) all copies render into
  ONE srcdoc document with `page-break-before` between them — a single
  print dialog, driver cuts each copy. Raw USB / Wi‑Fi / Bluetooth keep
  one job per copy (those auto-cut, no dialog).
- **Hosted (cPanel) mode**: the server can't reach shop LAN printers, so the
  relay queues a `PrintAgentJob` when the target is off the server's LAN and
  `shop.print_agent_token` is set (else 502 `agent_required`). A local agent
  (`static/print-agent/print_agent.py`) polls `print-agent/<shop>/jobs/` with
  the `X-Print-Agent-Token` header and acks `…/jobs/<id>/` (done/failed).
  The agent also TCP-probes its own /24s for printer ports ~every 45s and
  POSTs results to `print-agent/<shop>/report/` → stored on
  `shop.print_agent_scan`/`_scan_at`; Wi‑Fi "Scan" merges those (fresh within
  5 min) into results on hosted *and* on-LAN servers (rows flagged
  `via_agent`). Polls stamp `shop.print_agent_seen_at` (30s throttle) →
  `agent_alive` in scan/config responses. Pressing Scan writes
  `shop.print_agent_scan_request` (`{at, hosts, subnets}`); the poll response
  carries it (<10 min old) and the agent probes the requested hosts + its LAN
  and reports — the JS retries once ~7s later to collect the results.
  The scan view also sends the typed host + `shop.wifi_printer_host` as
  `extra_hosts` to `discover_lan_printers` (direct probes off the detected
  /24s), and the relay tries a direct socket for off-/24 hosts before
  queueing a `PrintAgentJob`.
  Views: `shops/agent_views.py`; token (re)generation: action `print_agent_token`
  on the shop receipt settings page (gated by `shop_settings`). Tests:
  `shops/tests/test_print_agent.py`.

## Auth model
- Two independent session types per browser: employee (Django auth) and shop
  portal (`shop_portal_auth` + `shop_portal_shop_id` session keys).
- **Employee and shop portal sessions are concurrent**: many devices/tabs may
  stay signed in at once. `EmployeeProfile.active_session_key` and
  `Shop.active_session_key` only record the most recent sign-in for diagnostics
  (stamped at login / `bind_shop_portal_session`, migrated on session-key
  rotation); they are not enforced. A newer login must not sign out older
  sessions.
- Session-key rotation is bridged by `RotatedSessionMiddleware` (old key →
  pointer → new key, 120s grace) so in-flight tabs survive login rotations.
- Note: one browser profile still shares one Django session cookie, so two
  *different* employee accounts cannot be signed in in the same browser profile
  at once — use separate browsers/profiles for that.
- **HR Management is permission-gated, not role-gated**: any active employee may
  open `/<role>/hr-management/` pages when the matching toggle under
  `hr-management` in the permissions matrix (`…/hr-management/permissions/`) is
  enabled (`employee_may` / `require_module_permission`). Approving registrations
  requires `hr-management/approvals`. Non-super-admin actors may only update
  employees still `pending_approval`, may not assign the Super Admin role, and
  may not modify Super Admin accounts (edit/suspend/delete). The same rules are
  enforced for queued offline syncs in `sync_handlers._sync_update_employee_access`.
