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

- The test DB is `test_<MYSQL_DATABASE>`; when other test runs (or parallel
  sessions) share the same MySQL server, give each run its own database so the
  `test_` DBs don't collide: `MYSQL_DATABASE=myshop_<slug> python manage.py test ...`
  → `test_myshop_<slug>`. Concurrent `manage.py test` runs against the same
  `test_myshop` drop/recreate it mid-run (random "table doesn't exist" errors).

## Stock modes (Settings → POS → Stock)
- One-of toggles: Items / Shelf / Both → `enable_stock_tracking` / `enable_shelf_mode`
  on `CompanyPosSettings` (+ per-shop `ShopPosSettings` override). See
  `STOCK_MODE_FIELDS` / `stock_mode_of` in `shops/services.py`.
- `ShelfItem` (per shop, name/unit/qty, no price) is never sold at the POS and
  never appears on the shop floor — shelf stock is managed only from Stock
  Management / the shop's Shelf stock page.
- The shop floor catalog (`my_shop_catalog` → `build_shop_catalog_page`) lists
  sellable items only; shelf-only mode returns an empty catalog.
- Per-item sale deduction: `Item.deduct_stock_on_sale` — when off,
  sales/credit/trade-out don't decrement `ShopStock`/`Item.stock`, returns and
  trade-ins don't move them either, insufficient-stock checks are skipped, and
  the item sells past zero. New items default it to
  `CompanyPosSettings.enable_stock_tracking` (off while the company is in
  shelf/no-item-stock mode). Toggle page: Stock Management → Settings → "Sale
  stock tracking" (`?mode=sale-tracking`, shares the `settings` permission).
  Items never picked by hand (`sale_tracking_custom=False`) auto-follow the
  company default: `sync_sale_tracking_defaults()` (`items/services.py`)
  re-stamps them to `enable_stock_tracking` on the sale-tracking page GET and
  whenever the flag changes in `set_company_pos_setting`; a per-item toggle or
  "Track all" marks the item custom so manual picks survive later mode flips.
  Manual Stock In/Out and transfers still move counts; serial tracking and the
  central-store mirror flag (`stock_count_enabled`) are separate. Floor JS
  reads `sale_stock_tracked` from the catalog payload (`data-item-sale-tracked`
  on cards) — untracked items show no stock number on the floor card, product
  popup, serial modal, or trade search. Tests:
  `shops/tests/test_sale_stock_tracking.py`.

## Stock take score report
- `?mode=stock-take&view=scores` on the stock-management page shows the item
  score report: `build_stock_take_item_scores` (`items/stock_take.py`)
  aggregates each item's submitted count lines in the selected period —
  matched/total → average score %, sorted lowest → highest; pending-only
  items sort last with no score. Shares the `stock-take` permission; sidebar
  uses `active_mode="stock-take-scores"`. Tests:
  `items/tests.py::StockTakeItemScoreReportTests`.
- Row checkboxes + "Initiate stock count" GET-submit `item_ids` back to
  `?mode=stock-take` → `stock_take_init_item_ids` opens the create panel on
  the "By item" scope with those items pre-checked (shops + deadline still
  needed). Checkbox JS lives in `stock-take.js` (`data-score-report-form`).

## Stock take deadlines
- A `StockTake` closes itself once `deadline` passes — no scheduler needed:
  `close_overdue_stock_takes()` (`items/stock_take.py`) is swept lazily from the
  stock-take page, `open_stock_take_hide_sets` (cache miss only, so hidden qtys
  unhide), `pending_stock_take_for_shop` / `_count_for_shop` (floor popup and
  badge stop prompting), and the `my_shop_stock_take_submit` endpoint (a late
  submit hits the closed take → "No open stock take"). The deadline check in
  `_assert_assignment_submittable` stays as belt-and-braces for direct callers.
  `closed_by` stays NULL on auto-close.
- `python manage.py close_overdue_stock_takes` runs the same sweep — schedule it
  (Task Scheduler / cron) if takes must close with zero site traffic.

## Compulsory stock fields (Stock Management → Settings)
- `CompanyStockSettings` (singleton, cached via `get_company_stock_settings`)
  holds **independent** required-field flags per stock type: `require_*`
  (buying price / supplier / payment status / reason / refund / transfer
  note) for item stock and `shelf_require_*` for shelf stock. The settings
  page shows an "Item stock" and/or "Shelf stock" section only while that
  stock mode is enabled on `CompanyPosSettings`.
- Toggles go through `set_company_stock_setting` (`STOCK_SETTING_FIELDS` in
  `shops/services.py`); `as_requirements_dict()` / `as_shelf_requirements_dict()`
  feed the `data-stock-requirements` JSON the bulk JS reads.
- Enforcement: shop-floor item movements in `items/services.py`; central
  Store Manager stock in `items/store_stock_services.py`
  (`parse_store_stock_qty_lines` / `parse_store_trade_lines` `require_price`,
  `parse_supplier_payment_details` / `parse_stock_out_details`
  `field_prefix="shelf_"` for shelf posts); shelf single-line/bulk in
  `items/shelf_stock_services.py` (`_shelf_requirement`). Store Manager shelf
  pages enforce the `shelf_*` flags; trade-in honours the matching
  buying-price flag via a per-line `price_<id>` unit value.
- Tests that write `CompanyStockSettings` directly must call
  `_invalidate_stock_settings_cache()` afterwards — the cached row survives
  into later tests otherwise.

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

## Expense payments (Analytics → Expenses → category)
- `Expense` tracks `amount_paid` + `payment_status` (unpaid/partial/paid);
  `cash_amount`/`mpesa_amount`/`card_amount` are till splits set at
  registration/owner-drawings — account payments don't touch them.
- The category page (`/…/analytics/expenses/?category=<slug>`,
  `_build_expense_category_suppliers`) lists Paid/Due/Status per expense and,
  when `analytics/account_pay` is allowed (`filters["can_pay_accounts"]`), a
  per-row **Pay** button (`cell.kind == "pay"` in `analytics.html`). The shared
  `analytics_account_pay_modal.html` + `analytics-receipt-ledger.js` POST to
  `analytics_account_pay` (`kind=expense`, `receipt_id`) →
  `apply_supplier_receipt_payment`, which caps the amount at the receipt's due.
  `account_id` is the `ExpenseSupplier` pk; `0`/empty pays expenses with no
  linked supplier (matched by pk + shop scope). Tests:
  `employees/tests_analytics_suppliers.py::ExpenseReceiptPayTests`.

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
  The agent also TCP-probes its own /24s — plus a built-in list of common
  printer subnets (192.168.x / 10.x / 172.16.x, `COMMON_PRINTER_SUBNETS`,
  disable with `"wide_scan": false` in print_agent.json) — for printer ports
  ~every 45s and POSTs results to `print-agent/<shop>/report/` → stored on
  `shop.print_agent_scan`/`_scan_at`; Wi‑Fi "Scan" merges those (fresh within
  5 min) into results on hosted *and* on-LAN servers (rows flagged
  `via_agent`). Polls stamp `shop.print_agent_seen_at` (30s throttle) →
  `agent_alive` in scan/config responses. Pressing Scan writes
  `shop.print_agent_scan_request` (`{at, hosts, subnets}`); the poll response
  carries it (<10 min old) and the agent reports twice — a quick pass on the
  requested hosts + its own LAN, then a slower wide sweep of the common
  subnets — the JS retries once ~7s later for the quick pass.
  The scan view also sends `shop.wifi_printer_host` as `extra_hosts` to
  `discover_lan_printers` (direct probes off the detected /24s), and the
  relay tries a direct socket for off-/24 hosts before queueing a
  `PrintAgentJob`. A typed IP in the Scan field makes the scan **targeted**:
  only that host is checked — `_probe_host` directly when the server has a
  LAN, plus the matching row in the agent's fresh report (filtered by host);
  no full sweep, and a miss returns a host-specific hint. Invalid/non-LAN
  input 400s; empty field = scan the whole network.
  Agent config self-setup: `print-agent/<shop>/setup/<key>/` returns
  `{poll_url, token}` (or a `print_agent.json` download with `?download=1`);
  `key` is `agent_setup_key(shop)` — an HMAC of the agent token, so a
  regenerated token invalidates old links. The receipt settings page shows the
  link; the agent prompts for it once when `print_agent.json` is missing,
  writes the file itself, and re-prompts on a 401 (`relink`). `--setup URL`
  does it non-interactively.
  Views: `shops/agent_views.py`; token (re)generation: action `print_agent_token`
  on the shop receipt settings page (gated by `shop_settings`). Tests:
  `shops/tests/test_print_agent.py`.

## Performance notes
- `static/sw.js` (served at `/sw.js` via `core.views.service_worker`): bump
  `CACHE_VERSION` on every edit. Runtime caching is deliberately narrow —
  `/static/` is cache-first (all asset URLs carry `?v=` busts) and `/media/`
  is network-first. Do NOT go back to `cache.put`-ing every same-origin GET:
  per-request Cache Storage writes are a known Chrome jank/hang source.
- HTML must never send `Cache-Control: no-store` (`core.middleware.
  NoCacheHtmlMiddleware` sends `no-cache` only) — `no-store` and
  `beforeunload` listeners both permanently block Chrome's back/forward
  cache. Use `pagehide`/`pageshow` (with `event.persisted`) instead of
  `beforeunload` for page-lifecycle cleanup.
- `django.middleware.gzip.GZipMiddleware` compresses HTML/JSON (the shop
  website landing page is ~600KB HTML → ~28KB gzipped).
- Keep `MYSQL_CONN_MAX_AGE` > 0 in `.env`; `0` pays a fresh MySQL handshake
  on every request.
- Shop floor loads ~850KB of deferred JS by design (my-shop.js, stock-bulk.js,
  shop-printer.js…); keep new page scripts `defer` or `type="module"`.

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
