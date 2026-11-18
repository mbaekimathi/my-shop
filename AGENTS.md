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
  the item sells past zero. New items register with it OFF and
  `sale_tracking_custom=True` (`create_item`), so they never auto-follow the
  company default — switch them on per item. Toggle page: Stock Management → Settings → "Sale
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

## Stock take system qty (live → frozen)
- `StockTakeCount.system_qty` is **re-stamped to the live `ShopStock` qty the
  moment that line's count is saved** (`submit_stock_take_one` /
  `submit_stock_take_counts` in `items/stock_take.py`); the value stamped at
  take creation is only a placeholder. Uncounted lines therefore display live
  stock on the comparison page (`build_stock_take_comparison`), the shop's own
  results (`build_shop_stock_count_results`), and the HQ form payload
  (`online_stock_take_assignment_payload`); counted/submitted lines show the
  frozen at-count qty. Same convention as `OnlineStockTakeEntry.
  system_qty_at_count` (`_baseline_system` in `items/online_stock_take.py`).
  Tests: `items/tests.py::StockTakeLiveSystemQtyTests`.

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

## Cashiers report (Analytics → Cashiers, slug `balances`)
- `_build_balances` is cashier-centric: `ShopDaySession`s in the filtered
  period roll up by `opened_by` (a day belongs to whoever opened it; the
  closer shows per row). `?employee_id=<pk>` narrows to one cashier —
  parsed in `build_analytics_page` (`filters["selected_employee_id"]`), the
  dropdown renders only for `section_slug == "balances"`.
- `_day_balance_data` carries `opened_by_id`/`closed_by_id` on every
  `session_rows` entry; per-cashier + grand totals are re-summed from those
  rows (disabled till channels are zeroed via `_BALANCE_CHANNEL_KEYS`, and
  `_apply_pos_visibility` still strips M-Pesa/Card-labelled columns when the
  POS flags are off — keep channel names in labels).

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

## Cashier settings (`/shop-cashier/settings/`)
- Replaces "System settings" for `SHOP_CASHIER` profiles: footer sidebars
  (`_footer_site_links`) and `sidebar_for_settings` link to
  `cashier_settings_url()` labeled "Cashier settings" instead. The page needs
  `settings/home` permission; it shadows `role_settings` for the cashier
  segment (registered before `<role_segment>/settings/` in portal_urls).
- "Dashboard sidebar" toggles are **per-employee**, stored as keys in
  `EmployeeProfile.sidebar_prefs` JSON (missing key = shown) —
  `CASHIER_SIDEBAR_LINKS` / `cashier_sidebar_pref_enabled` /
  `set_cashier_sidebar_pref` (employees/workspace.py). POSTs reuse
  `action=toggle_pos_setting` with `field=sidebar_<key>` so `pos-settings.js`
  works unmodified; `_cashier_dashboard_links` drops hidden links (link-only,
  same convention as the floor toggles).
- The "Open & close" group edits the shared company rows
  (`enable_open_close`, `CompanyWorkingHoursSettings.enabled`, per-shop
  `ShopWorkingHoursSettings` times — same as Settings → POS) and is gated by
  `settings/company-pos`. Template `employees/settings_cashier.html`. Tests:
  `employees/tests_cashier_settings.py`.

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
- Cashier dashboard + `role_shop_day` picker (`?mode=drawings` when reached via
  the Drawings link) render per-shop **day cards**: status badge (Open /
  "Yesterday — left open" / Closed), opened-with/sales/expenses/drawings/
  stock-bought/in-till stats, and day actions (open/close, record drawing,
  day-report PDF). Rows come from `cashier_day_summaries_context` +
  `role_shop_day`. **Till data is scoped to the session the cashier opened**
  (`open_session.opened_by_id == profile.pk`) — a day opened by a colleague
  shows only "Open — <name>" / "Day open under <name>" with no figures; the
  Drawings link and Record-drawing buttons also require an own session.
  A **stale-open session keeps showing its own (yesterday's) figures** — that
  is the intended "yesterday's data" display, paired with a "Close yesterday's
  day" action.
- The **Drawings** sidebar link only renders while at least one assigned shop
  has an open day: single shop → `my_shop_drawings` (`/my-shop/<id>/drawings/`),
  multi-shop → the `role_shop_day` picker. The dedicated drawings page is gated
  by `my-shop/register_expense`, carries the live-till block + the
  draw-from-counter form + a "Drawings today" log (rows come from
  `day_session_report_details`, which exposes `drawer`/`by` parsed from the
  stamped `OWNER DRAWING · <name>` expense name). Stale-open days show a
  "close yesterday first" state instead of the form; closed days show an
  "open the day" state. `my-shop-day.js` (v15) tolerates a missing
  `[data-shop-day-form]` so the drawing form works standalone.
- Open/close shop on role dashboards: single-shop cashiers link straight to
  `my_shop_day_toggle`; multi-shop cashiers land on `role_shop_day`
  (`/<role>/shop-day/`), a picker page listing each allocated shop's day state
  with a button into the toggle page — both work at allocation level (no floor
  unlock). `my_shop_day_toggle` accepts a `?back=` local path so its Back button
  returns to the dashboard/picker instead of the floor workspace.
- Owner drawings are equity, not expenses (`ExpenseCategory.OWNER_DRAWINGS` is
  excluded from the expense form and rejected by `register_shop_expense`).
  They're recorded only on the day page's "Draw from counter" section
  (`#drawings`, gated `is_open and can_record_drawing` — `my-shop/register_expense`
  perm, blocked on stale-open days): cash/M-Pesa/card amounts + the releasing
  employee code + the drawer's code (the director/person taking the money);
  `register_owner_drawing` deducts each channel from expected till in
  `day_session_balance_summary`. The cashier dashboard has a "Drawings" link
  (single shop → day page `#drawings`; multi → picker with per-shop "Record
  drawing" buttons on open days).
- Day accounting report PDF: `my_shop_day_report`
  (`/my-shop/<id>/day/report/`, same `my-shop/open_close` gate, inline
  `application/pdf`). `build_shop_day_report_pdf` + `day_session_report_details`
  in `shops/services.py` — KPI strip + money-flow table (opening / sales /
  returns / expenses / stock bought / drawings / expected, per channel) plus
  per-line drawings, expenses, and paid stock-in rows. Reports the open
  session ("Expected in till" preview) or the last closed one (counted
  closing + variance). "Day report (PDF)" button sits in the day page close
  form actions, opened in a new tab.
- Open & close schedule (Settings → POS → Open & close): two modes under the
  `enable_open_close` compulsory toggle — "anytime" (staff open/close whenever)
  or "set" (per-shop opening/closing times). The mode is stored as
  `CompanyWorkingHoursSettings.enabled` (`set_working_hours_enabled` flips only
  that flag — configured days/times survive); per-shop times POST as
  `action=set_open_close_times` → `update_shop_working_hours_from_post`.
  Working days stay under Settings → Working hours.

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

## Offline shop floor
- `/sw.js` + `static/js/offline/` (`store.js` IndexedDB queue, `connectivity.js`
  ping detector, `sync.js` replay, `catalog-cache.js`, `staff.js`, `serials.js`)
  keep `/my-shop/<id>/` selling through an outage. `offline_bar.html` (base.html)
  boots `initOffline()` on every page.
- **Cold-open offline**: `/my-shop/<id>/` navigations are `cache.put` into a
  dedicated `PAGES_CACHE` (`myshop-pages-v1`, kept across `CACHE_VERSION`
  bumps) — the only HTML ever cached. The stored response's `Vary: Cookie`
  scopes it to the signing session; a logout link click posts
  `CLEAR_PAGE_CACHE` to the SW (`init.js`). Other navigations stay uncached.
- Catalog JSON pages persist in IndexedDB with the 12h
  `CATALOG_CACHE_OFFLINE_TTL_SECONDS` (cache is only read offline / on network
  error); queued sales decrement on-card stock via
  `rebuildPendingSalesFromQueue` + `pendingQuantitiesForShop`.
- Offline checkout: `queueOperation("complete_shop_checkout", …)` (same
  `client_id` as the online attempt → idempotent replay). M-Pesa STK without a
  completed `stk_payment_id` refuses to queue. Staff codes verify against a
  24h `staff_verify:` cache; serials search a 12h per-item cache.
- Replay: `syncNow()` chunks the queue at 50 ops/POST (server caps 100) to
  `POST /employees/api/sync/` → `_sync_complete_shop_checkout` runs the real
  `complete_shop_checkout` (staff code, permissions, open day, prices, serials,
  stock all re-validated at sync time). Per-op `attempts`/`lastError` are
  persisted; an op server-rejected 5× turns `stuck`, stops auto-replaying, and
  shows as a red chip in the offline bar with **Retry failed** /
  **Discard** (`retryStuckOps` / `discardStuckOps`). `CSRF` is read cookie-first
  (a cached page's form token can be stale after re-login).
- Known trade-offs: stock is only checked at replay (two tills can sell the
  last unit offline — second replay fails); `require_shop_day_for_sale` runs at
  sync time so a sale synced next morning lands on that day's session; queued
  sales print nothing until synced. Tests:
  `shops/tests/test_offline_shop_sync.py`.

## Performance notes
- `static/sw.js` (served at `/sw.js` via `core.views.service_worker`): bump
  `CACHE_VERSION` on every edit. Runtime caching is deliberately narrow —
  `/static/` is cache-first (all asset URLs carry `?v=` busts), `/media/`
  is network-first, and only `/my-shop/<id>/` navigations are page-cached
  (`PAGES_CACHE`, for offline till reload). Do NOT go back to `cache.put`-ing
  every same-origin GET: per-request Cache Storage writes are a known Chrome
  jank/hang source.
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
- **Idle session timeouts are opt-in** (Settings → POS → Session timeout, both
  default OFF = session ends only on logout): `enable_employee_session_timeout`
  / `employee_session_timeout_minutes` and `enable_shop_session_timeout` /
  `shop_session_timeout_minutes` on `CompanyPosSettings` (company-wide; no
  per-shop override, minutes 1–43200). Enforcement: `EmployeeIdleTimeoutMiddleware`
  (stamp `_emp_idle_ts`; `EMPLOYEE_IDLE_TIMEOUT_SECONDS` env is only the fallback
  when the settings row can't be read) and `ShopFloorSessionMiddleware` →
  `_apply_shop_idle_timeout` (stamp `_shop_portal_idle_ts`; expired portal
  sessions are cleared → floor views redirect to shop login). Activity =
  navigations, non-poll mutations, and `X-MyShop-Activity`-stamped fetches
  (session-guard.js); background polls only *detect* expiry.
  Tests: `shops/tests/test_single_session.py`.
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

## Multi-role employees
- `EmployeeProfile.role` stays the **primary** role; `extra_roles` is a CSV
  CharField of additional granted roles (helpers `split_extra_roles` /
  `join_extra_roles`, `profile.role_values()` = granted set, enum order).
- The picked role is **per-session**: `employee_active_role` session key
  (`access.SESSION_ACTIVE_ROLE_KEY`). `get_employee_meta_for_request` returns
  `meta["role"]` = effective role (+ `primary_role`, `available_roles`);
  `get_profile_for_request` mutates the request-cached `profile.role` in
  memory (`profile.db_role` keeps the DB value) so every existing
  `profile.role`/`meta["role"]` check follows the pick — never written back
  (all signed-in-profile saves use `update_fields` or set `role` explicitly).
  A revoked role silently falls back to the primary role.
- Login: multi-role employees land on `employees:select_role`
  (`/employees/select-role/`, template `employees/select_role.html`,
  `?next=` preserved). Single-role users skip it. "Switch role" lives in the
  workspace user-menu dropdown (`workspace.role_options` context).
- Granting: HR Management → Edit employee → "Can also sign in as" checkboxes
  (`extra_roles[]`; JS disables the box matching the primary role). The
  approvals/access tables still set primary role only; changing a primary
  role auto-drops it from `extra_roles`, and `assigned_shops` are only
  cleared when NO granted role is shop-scoped (`SHOP_ALLOCATION_ROLES`).
  Non-super-admin actors cannot grant `super_admin` as an extra role either.
- `chat` sessions share the key: `chat/auth.py::current_employee` honours
  `employee_active_role` when it is one of the employee's granted roles.
- Tests: `employees/tests_multi_role.py`.

## Receipt return / cancel permission
- Cancel IS a return: `cancel_pending_shop_receipt` restocks the remaining
  lines, so every cancel path also requires the **Return receipt** toggle —
  `analytics/return-receipts` on `analytics_receipt_cancel`,
  `analytics_receipt_set_status` (only when the target status is cancelled),
  and belt-and-braces inside `cancel_pending_shop_receipt` itself
  (`ensure_employee_may`). `analytics_receipt_return` requires
  `analytics/return-receipts` on the viewer plus `my-shop/return_receipt` on
  the authorising staff code; `my_shop_receipt_return` requires
  `my-shop/return_receipt` on the floor profile (portal sessions fall through
  to the staff-code check inside `return_shop_receipt_items`).
- `can_return` / `can_cancel` in receipt-detail payloads and `can_cancel` on
  confirm-receipts rows are masked off when the viewing profile lacks the
  matching return toggle (`_confirm_rows_apply_permissions`), so the buttons
  hide instead of 403ing.
- Checkout perm keys map 1:1 to `my-shop/<kind>` (`sale`, `credit`,
  `quotation`, `trade_out`, `trade_in`) — unknown kinds fall back to
  `workspace`; `complete_shop_checkout` re-checks the authorising code's
  `my-shop/<kind>` and `my-shop/receipts` when confirm-at-checkout is used.
- Tests: `shops/tests/test_permission_enforcement.py`
  (`ReceiptReturnCancelPermissionTests`, `TradeCheckoutPermissionTests`,
  `FloorReceiptReturnPermissionTests`).
