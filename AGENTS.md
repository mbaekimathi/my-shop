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
- Shelf items can be **edited** (name/category/unit/low-stock alert/image —
  qty still only moves via in/out) and **suspended** (`ShelfItem.is_suspended`)
  from the Store Manager Shelf stock page's Manage column
  (`shelf_manage` + `data-modal="shelf-edit"` in
  `shops/partials/shelf_stock_body.html`). Suspended items drop off every list
  and can't stock in/out/trade/transfer; `?mode=suspended` on
  `store_shelf_stock` (sidebar "Suspended items") lists them with an unsuspend
  action. `update_shelf_item` / `set_shelf_item_suspended` /
  `list_suspended_shelf_items` live in `items/shelf_stock_services.py`;
  `apply_shelf_action` handles `edit` / `suspend` / `unsuspend`. Re-registering
  the same name or a stock-in-by-name unsuspends the item.
- **Shelf stock audits** live on their own Store Manager page — `?mode=audits`
  on `store_shelf_stock` (sidebar "Shelf stock audits",
  `shops/partials/shelf_stock_audits.html`, `shelf_audits` +
  `shelf_audit_movements`) — every `ShelfStockMovement` for the filtered
  period (`?range=day|period|month|year` + `date`/`date_from`/`date_to`/
  `month`/`year`, parsed via `items.views._report_range_bounds`, listed by
  `shelf_movements_in_period`). The Store Manager Shelf stock page shows only
  the "On the shelf" list — only the shop floor page keeps the
  "Recent changes" panel (`shelf_movements`, last 30).
- The **Shelf stock report** (`?mode=report` on `store_shelf_stock`) is the
  period report built by `build_shelf_stock_report`
  (`items/shelf_stock_services.py`): per item it shows Start / In /
  Transfers / Trades / Out / Close — start and close are each item's
  `balance_after` at the period bounds, registers count as "in", and
  transfers/trades show net signed qty (received−sent, trade-in−trade-out).
  Same range filters as audits plus an `?item=<pk>` picker; `?download=1`
  streams a branded PDF via `build_stock_report_pdf` (`_shelf_report_download`
  in `employees/store_stock_views.py`). `transfer_shelf_item` stamps
  `reason='transfer'` on both legs so the Transfers column works (older
  legs fall back to the auto "Sent to"/"Received from" note); IN movements
  may carry `trade_in`/`transfer` reasons, OUT movements any
  `StockOutReason`.
- The **stock-management shelf report** (`?mode=report&tab=shelf`,
  `_stock_shelf_report` → `items/stock_shelf_report.html`) is a different,
  lighter report built by `_build_shelf_report` — per shelf item it shows
  On shelf / Registered / In / Out for the period across the in-scope
  shelf shops. It reuses the shared print popup
  (`employees/partials/receipts_print_modal.html` +
  `analytics-receipts-print.js`): `?paper=a4|80|58` renders
  `items/movement_report_print.html` from `_shelf_report_document`
  (`items/views.py`), `?estimate=1` returns the A4 page-count JSON, and
  `?download=1` streams `build_stock_report_pdf` with
  `page_mode="report"` (new `report_numeric_from` kwarg controls where
  right-aligned numeric cells start; `report_table_title`/
  `report_table_subtitle`/`report_numeric_from` document keys let
  `_movement_print_tables` title non-movements tables).
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
  Manual Stock In/Out and transfers still move counts; serial tracking is
  separate. The central-store mirror flag (`stock_count_enabled`, the "Stock
  count" column on the Company Store Manager item-stock page) is the **same
  switch**: `set_item_stock_count_enabled` also writes `deduct_stock_on_sale`
  + `sale_tracking_custom`, the sale-tracking toggle/Track-all write
  `stock_count_enabled`, `sync_sale_tracking_defaults()` re-stamps both, and
  new items register with both OFF. Migration
  `0068_link_stock_count_and_sale_tracking` unified existing rows off-wins.
  Floor JS
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
  Company Store Manager stock in `items/store_stock_services.py`
  (`parse_store_stock_qty_lines` / `parse_store_trade_lines` `require_price`,
  `parse_supplier_payment_details` / `parse_stock_out_details`
  `field_prefix="shelf_"` for shelf posts); shelf single-line/bulk in
  `items/shelf_stock_services.py` (`_shelf_requirement`). Company Store Manager
  shelf pages enforce the `shelf_*` flags; trade-in honours the matching
  buying-price flag via a per-line `price_<id>` unit value.
- Tests that write `CompanyStockSettings` directly must call
  `_invalidate_stock_settings_cache()` afterwards — the cached row survives
  into later tests otherwise.
- Stock-in supplier fields suggest from the shared `Supplier` registry: the
  shop-floor buy-stock forms (split dial + phone) and the central-store
  item/shelf in/out modals + page forms (single free-text
  `supplier_phone`) all carry `data-supplier-search="name|phone"` inside a
  `data-supplier-search-root` wrap with a `[data-supplier-suggest]` box; the
  form's `data-supplier-search-url` points at `employees:supplier_search`.
  `store-stock.js` binds the central-store forms (debounced fetch, pick
  fills name + `dial+phone`, phone digits ≥7 auto-fills on a confident
  match); `stock-bulk.js` does the shop-floor forms. Store/shelf stock-ins
  also *register* the supplier — `register_supplier_from_stock_in`
  (`items/store_stock_services.py`, called from `record_store_stock_lines`
  and plain `record_shelf_movement` IN) splits the free-text phone into
  dial + national digits ("+"/"00" prefix → match `VALID_DIAL_CODES`, else
  default `+254`) and `upsert_supplier`s it. Trade persons are not
  registered; the `chat` store mirror intentionally never touches
  suppliers.

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
- IT support and shop manager dashboards pin a "Cashiers" sidebar shortcut to
  it (`_dashboard_analytics_section_links` — their sidebars are kept in
  parity; `DashboardSidebarLinkTests` asserts they match).
- `_build_balances` is a slim sessions list: a single "Sessions" table lists
  each `ShopDaySession` in the filtered period — Session (date + start/end
  times), Cashier, Shop, Status, Report — whose **Open** link (and the
  session cell itself) goes to
  `<role>/analytics/balances/<session_id>/` (`analytics_day_session`,
  gated by `analytics/balances` + shop allocation). No metrics/till
  boards — the page is just the sessions list plus the standard filters.
- `?employee_id=<pk>` narrows to sessions the cashier opened (a day belongs
  to whoever opened it) — parsed in `build_analytics_page`
  (`filters["selected_employee_id"]`), the dropdown renders only for
  `section_slug == "balances"`. `_day_balance_data` carries
  `id`/`opened_by_id`/`closed_by_id` on every `session_rows` entry.
- The session report page is built by `build_day_session_report_page` — the
  same sections as the day accounting PDF (money flow, items sold,
  sales by employee, pending/cancelled sales, drawings, expenses,
  stocked in/out via
  `day_session_balance_summary` / `day_session_report_details` with the
  employee's day-report counted-status pick; `_apply_pos_visibility` strips
  M-Pesa/Card-labelled columns when the POS flags are off — keep channel
  names in labels). Money-flow channel columns come from
  `day_report_channel_columns` (shop-**effective** channels plus any channel
  with non-zero figures so rows still reconcile), and the page passes the
  effective settings to `_pos_feature_flags` + force-keeps displayed
  channels — a shop override that enables M-Pesa/Card survives the company
  flag strip. A "Credit & gift — not in the till" table follows the
  money flow when `enable_credit`/`enable_gift` are on
  (`day_report_not_till_lines` → Docs/Value/Collected/Still due; credit
  collected+left-to-collect, gift value with "—" collected columns, footnote
  shows the counted credit-slips trail opened/counted/diff). Credit and
  gift value never enters Expected in till or Variance — credit is a
  receivable and gifts are free issues. The "Sales by employee" table
  follows Items sold — `day_session_report_details` returns `sellers`, a
  per-`created_by` rollup (receipts/qty/amount, same counted statuses and
  returned-qty netting as items sold, sorted amount desc; unnamed seller =
  "—") rendered on the page, the `?paper=` print sheet (page.tables loop)
  and the PDF. The Stocked in table lists Item/Qty/Amount/Total per line
  plus a Status chip from `StockMovementLine.payment_status`
  (Paid/Partial/Unpaid — the group header still carries the movement's
  `amount_paid` total); Stocked out lists Item/Qty/Reason/Amount/Total.
  `day_session_report_details` also returns `shelf_purchases` /
  `shelf_out` — the same group/line shapes built from
  `ShelfStockMovement` (REGISTER counts as stocked in; shelf outs carry
  no cost basis so Amount/Total show "—") — rendered as "Shelf stocked
  in/out" tables via the shared `_stocked_in_table`/`_stocked_out_table`
  closures (page) and `_stocked_in_detail`/`_stocked_out_detail` (PDF).
  Item tables show while `enable_stock_tracking` is on, shelf tables
  while `enable_shelf_mode` is on — either pair stays if rows exist for
  a since-disabled mode. Paid shelf stock-ins (`amount_paid`) also count
  in `suppliers_paid` → the money-flow "Stock bought" row and Expected
  in till, matching item stock-ins. Both tables end in a `total_row`
  dict (cells + `total_row: True` →
  `ax-row--total` on screen, `row-total` on the print sheet, the
  `_detail_table` footer on the PDF) summing qty + line totals; the
  stock-out footnote adds "· Refunded" when lines carry
  `refund_amount`. `?download=1` streams the same
  `build_shop_day_report_pdf`. Session links carry a `?back=` URL so "Back
  to cashiers" restores the filters. Tests:
  `AnalyticsDaySessionReportTests`.
- The session report page reuses the receipts **print popup**
  (`page.receipts_print_modal` + `receipts_print_url` = its own path, title/
  lead via `receipts_print_title`/`receipts_print_lead`):
  `?paper=a4|80|58` renders `employees/analytics_day_session_print.html`
  (browser print sheet — A4 portrait, or a thermal strip: narrow tables
  stay real columns — up to 5 on 80 mm, up to 4 on 58 mm — while wider
  ones get `table.sheet-grid.stack` = bold title cell then wrapped
  "Label: value" pairs; to save roll paper the thermal sheet also hides
  the kicker, meta chips, the KPI strip, table footnotes and whole empty
  sections — `.sheet-section:has(.sheet-empty)`), `?estimate=1`
  returns the modal's A4 page-count JSON, `?download=1` stays the A4 PDF.
  The shop-floor `my_shop_day_report` (`/my-shop/<id>/day/report/`) now
  opens the same on-screen report page first — `analytics.html` inside
  the shop-floor chrome (`_render_my_shop_tool_page`, `active="day_history"`,
  portal-safe via `_shop_floor_chrome`) with the same print popup —
  and redirects `?session=`less hits to `?session=<pk>` so print/download
  stay pinned to the day on screen (the popup JS merges
  `location.search`). `?download=1` streams the PDF, `?paper=` the print
  sheet, `?estimate=1` the page-count JSON — day history rows link
  Report / PDF (`&download=1`) / 80 mm / 58 mm. Narrow thermal is keyed
  `58` everywhere now (`analytics_receipts_print` maps legacy `50` →
  `58`; the receipts sheet uses `body.paper-58`, `@page 58mm auto`).

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

## Analytics time filter (Settings → POS → Analytics)
- `CompanyPosSettings.enable_analytics_time_filter` (off by default) adds
  optional `time_from`/`time_to` `type="time"` inputs to the shared
  `employees/partials/analytics_date_range_fields.html` filter partial, so
  every analytics page that includes it gets them at once.
- `_date_filter_context` (`employees/analytics_services.py`) parses the two
  params and, for `day`/`period` ranges only, narrows the `start`/`end`
  bounds: `time_from` moves the start to that time on the first day,
  `time_to` moves the exclusive end to the end of that minute on the last
  day (so "to 17:00" still counts 17:00:xx sales). Blank times = the full
  24-hour window; month/year/all-time ranges ignore the params. Helpers:
  `_parse_filter_time` / `_apply_time_filter_bounds` /
  `_analytics_time_filter_enabled`.
- The time inputs carry `data-range-field="day period"` +
  `data-range-optional`: every `data-range-field` sync snippet now matches a
  space-separated token list (`split(" ").includes(range)`) and skips
  `required` on `data-range-optional` fields. Any page missing the JS update
  just hides the time inputs — degrade, no break.
- Tests: `shops/tests/test_analytics_time_filter.py`.

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
- The "Sell popup" group is also **per-employee** (`SELL_POPUP_TOGGLES`,
  `sidebar_prefs["sell_*"]`, POSTed as `field=sell_<key>`) — it configures
  the dashboard Sell modal's document types, payment channels, and price
  editing in place of the company/shop POS toggles (see "Sell" action below).
- The "Day report" group is a **per-employee** one-of pick of which receipt
  statuses count as sales in the day accounting report — `all` (pending +
  confirmed + partially returned, the default), `pending`, or `confirmed`
  (confirmed + partially returned). Stored as `sidebar_prefs
  ["day_report_status"]` — `day_report_status_mode` /
  `day_report_counted_statuses` / `set_day_report_status_pref` in
  employees/workspace.py reuse the `CASHIER_SALES_STATUSES` buckets; the
  toggles post `field=day_report_status_<mode>` through
  `action=toggle_pos_setting` and use `data-stock-mode-toggle` so
  `pos-settings.js` enforces one-of (unchecked → 400). `my_shop_day_report`
  passes `day_report_counted_statuses(profile)` into
  `build_shop_day_report_pdf` → `day_session_balance_summary` +
  `day_session_report_details` (`counted_statuses` param, None = every sale
  receipt): it filters the sales tender, the counted receipts' return
  refunds (`_collect_return_payment_events`), items sold, and the
  "(N receipts)" count; the flagged pending/cancelled list is unaffected.
  The PDF subtitle shows "Pending sales only" / "Confirmed sales only" when
  filtered. Tests: `shops/tests/test_day_sales_summary.py
  ::DayReportCountedStatusesTests`.
- The "Open & close" group edits the shared company rows
  (`enable_open_close`, `CompanyWorkingHoursSettings.enabled`, per-shop
  `ShopWorkingHoursSettings` times — same as Settings → POS) and is gated by
  `settings/company-pos`. Template `employees/settings_cashier.html`. Tests:
  `employees/tests_cashier_settings.py`.

## Action popups on role dashboards
- Sidebar `_action` items render `data-modal-open`/`data-href`; `workspace_base.html`
  opens `[data-modal="<name>"]` when the modal exists on the page, else follows the
  href. `?modal=sell|buy-stock|register-expense` auto-opens on load.
- `expense_modal_context` / `buy_stock_modal_context` / `sell_modal_context`
  (employees/workspace.py) supply modal context **only for
  single-actionable-shop staff**; multi-shop staff fall back
  to the `data-href` (MY-SHOP picker / page).
- Endpoints used by dashboard popups must allow allocation-level access (no floor
  unlock): `_require_allocated_shop_access` — the staff-ID field in the payload
  authorises the write. Used by register expense, open/close day, verify-login-code,
  **buy stock POST, stock-supplier search, and checkout POST**
  (`my_shop_checkout`). Catalog GETs, serial search, and the client lookup use
  `_require_shop_read_access`.
- Cashier dashboard (`_cashier_dashboard_links`): no Stock Management link — "Sell"
  action (`sell_action`, first quick action, gated by `my-shop/workspace` +
  any of sale/credit/quotation, per-employee `sell` sidebar pref) opens
  `shops/partials/sell_modal.html` (`data-modal="sell"`, `cashier-sell.js`)
  — a lean floor-style cart: live `my_shop_catalog` search,
  sale/credit/quotation/gift picker, payment methods + splits, client
  name/phone autosuggest
  (`my_shop_client_lookup`), serial picker (`my_shop_serial_search`; serial
  lines' qty = picked serials), staff-ID verify, then POSTs the same
  `my_shop_checkout` payload the floor sends. Skips STK push, delivery, trades,
  confirm-at-checkout, and device printing (the result pane shows the
  plain-text receipt with a browser Print). **Exempt from the company/shop POS
  feature toggles** — its own per-employee toggles live under Cashier settings
  → "Sell popup" (`SELL_POPUP_TOGGLES` in employees/workspace.py, stored in
  `sidebar_prefs` as `sell_*` keys, POSTed as `sell_<key>` fields): document
  types (sale/credit/quotation/gift), payment channels (cash/M-Pesa/card — split
  methods derive from enabled channels), and "Edit item prices". Client
  fields are always shown, optional for sales. The payload sends
  `dashboard_popup: true` → `my_shop_checkout` honours it only for a logged-in
  employee with no unlocked floor session (`resolve_active_shop` →
  `pos_unrestricted` in `complete_shop_checkout`, which skips the
  kind/payment/client-data/discount/compulsory-print toggles, and checks the
  `sell_*` prefs + `sell_payment_methods` for the kind/payment/price
  instead); permission checks use `employee_may_unrestricted`
  (employees/module_permissions.py) — HR matrix rows and role defaults still
  apply. Tests: `shops/tests/test_dashboard_sell.py`. Plus a
  "Buy stock" action (`buy_stock_action`, gated by `my-shop/buy_stock` +
  `enable_sidebar_buy_stock`) opens `shops/partials/buy_stock_modal.html` on
  `role_home.html` (stock-catalog.js + stock-bulk.js loaded when
  `buy_stock_modal`), plus a "Suppliers" link (`analytics/suppliers`).
- Cashier dashboard + `role_shop_day` picker (`?mode=drawings` when reached via
  the Drawings link; `?mode=report|history` for the Day report / Day history
  pickers) render per-shop **day cards**: status badge (Open /
  "Yesterday — left open" / Closed), opened-with/sales/expenses/drawings/
  stock-bought/in-till stats, and day actions (open/close, record drawing —
  report/history pickers instead link each card to `my_shop_day_report` /
  `my_shop_day_history`). Rows come from `cashier_day_summaries_context` +
  `role_shop_day`. **Till data is scoped to the session the cashier opened**
  (`open_session.opened_by_id == profile.pk`) — a day opened by a colleague
  shows only "Open — <name>" / "Day open under <name>" with no figures; the
  Drawings link and Record-drawing buttons also require an own session.
  A **stale-open session keeps showing its own (yesterday's) figures** — that
  is the intended "yesterday's data" display, paired with a "Close yesterday's
  day" action.
- **Foreign session lockout** (`cashier_session_blocks` in
  employees/workspace.py): when any allocated shop's open `ShopDaySession`
  belongs to another employee (`opened_by_id != profile.pk`), a cashier is
  locked out entirely — `CashierSessionBlockMiddleware` (last in MIDDLEWARE)
  renders `employees/cashier_session_blocked.html` for every GET under
  `/shop-cashier/*` (403 JSON for XHR/non-GET) naming who must close it, and
  `_cashier_foreign_day_denied` (shops/views.py) blocks the `my-shop/*`
  endpoints at the `_require_allocated_shop_access` /
  `_require_active_shop_session` / `_require_shop_read_access` guards — the
  next cashier cannot sell, buy stock, register expenses/drawings, or close
  the previous cashier's session; they must ask that cashier to close it.
  Shop portal sessions and non-cashier roles are unaffected, so the day can
  always be closed from the floor or by a manager. Call sites that flatten
  the guard's `denied` into a generic JSON 403 pass it through untouched when
  it is already a `JsonResponse`. Tests:
  `shops/tests/test_cashier_session_block.py`.
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
- Day accounting report: `my_shop_day_report`
  (`/my-shop/<id>/day/report/`, same `my-shop/open_close` gate) opens the
  on-screen report page; `?download=1` streams the inline
  `application/pdf`. `build_shop_day_report_pdf` + `day_session_report_details`
  in `shops/services.py` — KPI strip + money-flow table (opening / sales /
  returns / expenses / stock bought / drawings / expected, per channel) plus
  per-line drawings, expenses, and stock movement rows. "Stocked in" lists
  **all** IN `StockMovement`s in the day window (paid or not — unpaid groups
  show the payment-status label; non-purchase `entry_source`s like customer
  return/trade-in are tagged on the item cell), and "Stocked out" lists every
  OUT line with reason/note/refund and an estimated value (`qty ×
  StockMovementLine.unit_cost`, the shop weighted average stamped at
  stock-out) footered as "Estimated value lost". Transfers are `REQUEST`
  movements — not listed in either. Reports the open
  session ("Expected in till" preview) or the last closed one (counted
  closing + variance). For `SHOP_CASHIER` profiles the report and
  `my_shop_day_history` are scoped to sessions that cashier **opened**
  (`_cashier_own_days_only` in shops/views.py → `get_last_shop_day_for` /
  `list_shop_day_sessions(opened_by=…)` in shops/services.py) — the report
  covers their own open→close, never a colleague's day; `?session=` picks are
  likewise restricted to their own sessions. Portal sessions and other roles
  keep the shop-wide view. On the cashier dashboard, "Day report" + "Day history"
  are **sidebar-only links** (`sidebar_only=True` keeps them off the
  role_home "Quick actions" cards, which render `page_sidebar.quick_actions` —
  `resolve_sidebar_hrefs` filters flagged items into that key); both are
  per-employee-hideable via the `day_report`/`day_history` keys in
  `CASHIER_SIDEBAR_LINKS`. Single shop → direct `my_shop_day_report` (new tab)
  / `my_shop_day_history`; multi-shop → `role_shop_day?mode=report|history`.
  When an employee session shares the browser with a shop-portal session, the
  floor sidebar's "Dashboard" link goes to the employee's role home —
  `_portal_home_role` (shops/views.py) feeds `home_role` into
  `sidebar_for_my_shop`.
- Open & close schedule (Settings → POS → Open & close): two modes under the
  `enable_open_close` compulsory toggle — "anytime" (staff open/close whenever)
  or "set" (per-shop opening/closing times). The mode is stored as
  `CompanyWorkingHoursSettings.enabled` (`set_working_hours_enabled` flips only
  that flag — configured days/times survive); per-shop times POST as
  `action=set_open_close_times` → `update_shop_working_hours_from_post`.
  Working days stay under Settings → Working hours.

## Till hand-over (sales after close)
- Day sessions have no receipt FK — every report computes a session's
  activity by the `[window_start, closed_at)` time window. The window's
  lower bound is `session_activity_start(session)` (`shops/services.py`):
  the previous session's `closed_at`, or `opened_at` for a shop's
  first-ever session. Receipts, refunds, expenses, drawings and supplier
  payments stamped **after a cashier closes the till are allocated to the
  next session that opens** — `day_session_balance_summary`,
  `day_session_report_details`, `day_sales_status_summary`,
  `_session_activity_totals`, `list_shop_day_sessions` (bulk-stamps
  `session._activity_start` via `stamp_session_activity_starts`, one
  query) and `_day_balance_data`'s `_activity_for` all use it.
- With `enable_open_close` compulsory ON, a shop that has run at least
  one day keeps trading while closed — `shop_day_floor_state` returns
  `can_trade=True, needs_action=False` when a prior closed session
  exists (no blocking prompt, sidebar shows "Open shop"). A brand-new
  shop still needs its first day opened; a stale-open session still
  blocks; `chat/stock.py`'s `require_shop_day_for_sale` mirrors the same
  rule for the chatbot. Gap sales therefore succeed via floor checkout,
  the dashboard Sell popup, and offline sync.
- Opening-balance expectations (`_opening_expectations`) still compare
  the counted opening against the previous session's counted closing —
  gap sales show up as the opening variance, then count again in the new
  session's sales/expected figures (the drawer really holds both).
- Tests: `shops/tests/test_shop_day_compulsory.py`
  (`test_sale_allowed_between_close_and_next_open`,
  `test_gap_receipts_allocated_to_next_session`,
  `test_first_session_window_starts_at_open`).

## Role dashboard sidebar groups
- Sidebar items can collapse into a dropdown group: `group=True` renders the
  item as a toggle button (`data-nav-group`, `aria-expanded`) in
  `workspace_sidebar.html`; children are marked `child=True` and render
  `hidden data-nav-group-item` — closed on load, expanded by the
  `data-nav-group` click handler in `workspace_base.html` (toggles `is-open`
  + `hidden`, rotates the `workspace-nav-caret` chevron).
  `.workspace-nav-link[hidden]` in `shell.css` keeps collapsed children
  hidden (the base `display:flex` would otherwise beat the attribute).
- IT support / shop manager sidebars use this for **Store management**
  (`_it_support_stock_links` in `employees/workspace.py`): a sidebar-only
  parent (`sidebar_only=True` → no Quick-actions card) wrapping the
  Item/Shelf stock management child links. Single-stock-type setups keep a
  flat link instead of a one-child group.

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

## Store manager roles
- `company_store_manager` (**Company Store Manager**, home `/company-store-manager/`)
  is the former `store_manager` role: company-wide (`COMPANY_WIDE_SHOP_ROLES`),
  sees every active shop without allocation, and owns the central-store pages
  (`/company-store-manager/…`, `store_stock_views.py`, gated by
  `role_required(COMPANY_STORE_MANAGER)` — company only).
- `shop_store_manager` (**Shop Store Manager**, home `/shop-store-manager/`) is
  the shop-linked variant: in `SHOP_ASSIGNABLE_ROLES`, so it works only inside
  allocated `assigned_shops` — `actionable_shops_for_profile`, floor access, and
  the allocation-gated stock-management / analytics modules all scope to the
  delegated shops. No central-store access.
- Both share the `STORE_MANAGER_ALLOWED_KEYS` default allow-list via
  `STORE_MANAGER_ROLES` (`employees/module_permissions.py`); explicit HR
  permission rows still override.
- The company store manager dashboard (`role_home.html`, `store_report`
  context from `_render_role_page`) carries a **Stock report** card — the
  shared `analytics_date_range_fields.html` period picker plus the item
  search (`store_stock_item_search` + `store-stock-report.js`) — whose
  Generate button GET-submits to `store-report/` with `download=1`. The card
  only renders while item stock tracking is on and the profile has
  `stock-management/report`. `store_stock_report` streams a branded
  per-item PDF (Start / In / Transfers / Trades / Out / Close over the
  filtered period, `build_store_item_stock_report` in
  `items/store_stock_services.py` — the store-side mirror of
  `build_shelf_stock_report`) via `_store_report_download` →
  `build_stock_report_pdf`; the report page's own "Download PDF" button
  uses the same URL (`store_report_download_url`).
- Data migration `employees/migrations/0017_*` rewrites `role` +
  `extra_roles` CSV `store_manager` → `company_store_manager` (widens the
  MySQL ENUM first, then shrinks to the final set).

## Company accountant role
- `company_accountant` (**Company Accountant**, home `/company-accountant/`)
  is a read-only oversight role: company-wide (`COMPANY_WIDE_SHOP_ROLES`,
  sees every active shop without allocation — not shop-scoped).
- Its permission default is the `ACCOUNTANT_ALLOWED_KEYS` allow-list in
  `employees/module_permissions.py` (`role_default_permission`) — analytics
  `view`/`balances`/`stock`/`suppliers` + stock-management
  `view`/`movements`/`report`; everything else defaults to deny. Explicit HR
  permission rows still override (the matrix works as for any role).
- It is also in `FULL_STOCK_WORKFLOW_ROLES` so the stock-management view's
  role gate lets `movements`/`report` open — the per-mode permission check
  still blocks in/out/transfer/serials; treat the set as "eligible for the
  full stock pages", not "can run every stock action".
- Dashboard sidebar (`sidebar_for_role_dashboard` →
  `_dashboard_analytics_section_links`) shows: Stock Management, Analytics,
  Cashiers, Suppliers. Tests: `employees/tests.py::CompanyAccountantRoleTests`.
- The analytics **overview** (`/…/analytics/`, `_build_overview`) carries an
  "Employee performance" table after "Individual shops" — needed because the
  Employees section (`analytics/employees`) is not on the accountant's
  allow-list. `_employee_performance_for_period` roll-ups sales+credit docs
  and line-level value/COGS per `receipt__created_by` (same netting and
  last-buy cost fallback as `_trading_by_shop_for_period`), labelled
  "name · employee_id"; columns mirror the shop rollup minus
  expenses/profit (Sales/Credits/Total revenue pairs, Stock sold, Gross
  margin, Margin) + an "All employees" total row.

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
- **Acting shop (unallocated shop-scoped picks)**: when the picked role is
  shop-scoped (`SHOP_ASSIGNABLE_ROLES`) and the employee has no allocated
  active shops, the role picker shows a second "Choose a shop" step; the pick
  is stored in `employee_acting_shop` (`access.SESSION_ACTING_SHOP_KEY`) and
  exposed as `profile.acting_shop_id` (memory only) by `_apply_acting_shop`.
  Consumers treat it as a stand-in allocation **only while the profile has no
  active assignments**: `actionable_shops_for_profile` (items/services.py),
  `get_shop_for_profile` + `profile_allocated_to_shop` (shops/session.py) —
  so dashboard sidebars, `employee_may_any` analytics/stock gating, and
  allocation-level endpoints (`_require_allocated_shop_access`,
  `_require_shop_read_access`) work for the acting shop. Picking a
  non-shop-scoped role (or logout) clears the key; staff-code authorisations
  load fresh profiles so they never see it.
- `chat` sessions share the key: `chat/auth.py::current_employee` honours
  `employee_active_role` when it is one of the employee's granted roles.
- **IT support role picks are view-only**: when an IT-support primary acts in
  a different role (`profile.db_role == it_support` and the picked role
  differs), `_apply_active_role` sets `profile.view_only_role` (memory only;
  `access.profile_view_only` reads it). The picked workspace behaves like
  they are the ones in session — `cashier_session_blocks` returns nothing,
  `_cashier_foreign_day_denied` passes, and the cashier day-summary /
  shop-day rows count the open session as "theirs" (`own_session`) so the
  till figures render and the badge names the employee actually in session
  ("Open — {opener}") — but `SupportViewOnlyMiddleware` denies every unsafe
  method outside a session-navigation allowlist (role picker, `my_shop
  select/switch/leave`, shop login, logout). Mutating affordances are also
  hidden: Sell/open-close/drawings/buy-stock sidebar links, the sell and
  buy-stock modals, `can_draw`/`can_open_close` row flags, and a "View only"
  badge on the day rows. `_cashier_own_days_only` returns False so day
  report/history stay shop-wide. Tests: `SupportViewOnlyRoleTests`.
- Tests: `employees/tests_multi_role.py` (`ActingShopRolePickTests` covers the
  shop step).

## Gift receipts (POS document kind `gift`)
- `ShopReceiptKind.GIFT` is a payment-free issue of stock: no payment method
  (`payment_method=NONE`, no till channels), no delivery, no confirm step
  (created `CONFIRMED`), no WhatsApp share. Stock still deducts like a sale
  (`StockOutReason.GIFT` on the store movement mirror) and serial-tracked
  items still need serials; COGS is recorded on the line. `total` records
  the value of the goods given. The client name/phone section always shows
  for gift (same always-on treatment as the trade kinds — it survives
  `enable_client_data` being off) and fields stay optional unless the
  `require_client_*` flags apply.
- Toggled by `enable_gift` on `CompanyPosSettings` (+ `ShopPosSettings`
  override), permission `my-shop/gift`, analytics section slug `gifts`,
  receipt-number field `receipt_format_gift` (default prefix `GF`). The
  dashboard Sell popup offers it via per-employee `sell_gift` pref and it is
  exempt from POS kind toggles there (same exemption as sale/credit/
  quotation). Returns work (restock) — `can_return` includes gift.
- Gifts never touch the day report till totals (`day_session_*` only reads
  `kind=sale` for money flow) — they appear only in the report's
  "Credit & gift — not in the till" section (`day_report_not_till_lines`,
  `gifts` aggregate in `day_session_report_details`), alongside credit, as
  value-not-money. They never join sales/credit revenue figures — they live
  in their own analytics section + receipt browse filter (`kind=gift`).
- Tests: `shops/tests/test_gift.py`.

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
- Confirmed receipts are protected: any status change off CONFIRMED
  (pending or cancelled) requires `analytics/return-receipts` on the
  viewer (`analytics_receipt_set_status`) plus a belt-and-braces
  `ensure_employee_may` inside `set_confirm_receipt_status` — confirm-side
  staff (`analytics/confirm-receipts` only) cannot undo a confirmed sale.
- `can_return` / `can_cancel` in receipt-detail payloads and `can_cancel` +
  `can_set_pending` (confirmed rows) on confirm-receipts rows are masked off
  when the viewing profile lacks the matching return toggle
  (`_confirm_rows_apply_permissions`, `analytics_receipt_detail`), so the
  buttons hide instead of 403ing.
- Checkout perm keys map 1:1 to `my-shop/<kind>` (`sale`, `credit`,
  `quotation`, `trade_out`, `trade_in`, `gift`) — unknown kinds fall back to
  `workspace`; `complete_shop_checkout` re-checks the authorising code's
  `my-shop/<kind>` and `my-shop/receipts` when confirm-at-checkout is used.
- Tests: `shops/tests/test_permission_enforcement.py`
  (`ReceiptReturnCancelPermissionTests`, `TradeCheckoutPermissionTests`,
  `FloorReceiptReturnPermissionTests`).

## Shop switching (role workspaces + portal)
- The "Switch shop" user-menu item + `shop_switch_modal.html` /
  `shop-switch.js` (`/my-shop/switch/`, `my_shop_switch`) behave differently
  per session kind (`mode` in the GET payload):
  - **Portal sessions** (`is_shop_portal`) move the portal shop:
    `begin_shop_portal_session` + `set_active_shop`, shop password required
    unless `CompanyPosSettings.enable_passwordless_shop_switch` (Settings →
    POS) is on; attempts share the `login` rate-limit bucket; lands on the
    new shop's floor.
  - **Employees in a shop-scoped role** (`SHOP_ASSIGNABLE_ROLES`) re-pick a
    **view shop** — the same session-only `employee_acting_shop` key the
    role picker uses. No floor unlock, no password; POST lands them back on
    the role dashboard. `shop-switch.js` submits one click in `mode=view`.
- The view pick **narrows** `actionable_shops_for_profile` to that shop
  only when it is one of the profile's allocated shops (or any active shop
  while unallocated, same as the role-picker stand-in); anything else is
  ignored and falls back to the full allocation. Allocation-level write
  checks (`get_shop_for_profile`, `profile_allocated_to_shop`) are
  unchanged — the pick is a view overlay, not an extra grant.
- Pickable shops in the GET payload: allocated shops when the profile has
  any (plus an `all_option` "All shops" card → POST `shop_id=` clears the
  pick), else every active shop (role-picker parity for unallocated staff).
  `current_shop_id` is the acting pick; `@ensure_csrf_cookie` covers role
  pages that render no `{% csrf_token %}` form.
- `workspace.can_shop_switch` (`employees/context_processors.py`) shows the
  employee user-menu item for shop-scoped roles when a viewable shop
  exists; the modal + JS load whenever `is_shop_portal or can_shop_switch`.
- `_apply_acting_shop` (`employees/access.py`) now runs for single-role
  staff too — `_apply_active_role` used to bail before applying it when
  `len(role_values) < 2`, so only multi-role picks ever reached
  `profile.acting_shop_id`.
- Tests: `shops/tests/test_shop_switch.py` (`ShopSwitchTests`,
  `EmployeeShopSwitchTests`).
