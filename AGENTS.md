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
- **Trade-out portions** (`ShelfItem.portions_per_unit` +
  `ShelfItemPortionTarget` legs): register/edit can link a shelf item to
  one or more catalog `Item`s and give each a per-unit `portions` count —
  the shelf item's `portions_per_unit` is the derived sum of its targets'
  portions (validated/written by `_resolve_portion_targets` /
  `_write_portion_targets`; a suspended item stays pickable only while it
  is a current target). The modals render the shared
  `items/partials/shelf_portion_picker.html` (type-to-search or open-list
  picker, posts repeated `portion_item_id` / `portion_qty` + an
  always-posted `portion_links` marker, options from
  `portion_item_options`); `stock-shelf.js` owns the picker JS (rows +
  live total, `window.shelfPortionPicker.fill/reset` for the edit-modal
  prefill via `data-portion-targets`) — posts that omit all three keys
  leave the links untouched, while a posted-but-empty picker clears
  them. **Company Both mode only**: `shelf_portions_enabled()` gates the
  pickers (`shelf_portions_enabled` template flag hides the modal
  fields), rejects any register/edit link change, and skips the auto
  stock-in — links survive a mode flip but can't be toggled until Both
  is back on. A shelf **trade out** then auto-stocks each target into
  the central store: `record_shelf_movement` fires `record_store_stock_in`
  per leg with `qty × target.portions`, valued at the item's list price
  (`target.item.resolve_list_price()` → `buying_price`; a suspended
  target's leg is skipped, not moved) tagged `reason='trade_in'`
  ("Portions stocked in" is appended to the trade success message;
  movements are exposed as `movement.portion_movements`).
  `transfer_shelf_item` copies the targets verbatim to the
  auto-registered target. Rows carry `portion_targets` /
  `portion_targets_json` / `portion_hint` ("Trade out → +N ITEM · +M
  ITEM2"). Migration `0072_portion_targets_per_item` converted `share`
  % to `portions` (`portions = portions_per_unit × share / 100`).
  Tests: `shops/tests/test_shelf_stock.py::StoreManagerShelfStockTests`
  (`test_shelf_trade_out_stocks_portions_into_linked_item`,
  `test_shelf_portions_split_across_multiple_items`,
  `test_shelf_portion_link_requires_item_and_count`,
  `test_shelf_portion_link_only_toggles_in_both_mode`).
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
  Only items that moved inside the filtered period are listed — rows whose
  whole content is a carried start/close balance are hidden.
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
  shelf shops, listing only shelf items that moved in the period. It
  reuses the shared print popup
  (`employees/partials/receipts_print_modal.html` +
  `analytics-receipts-print.js`): `?paper=a4|80|58` renders
  `items/movement_report_print.html` from `_shelf_report_document`
  (`items/views.py`), `?estimate=1` returns the A4 page-count JSON, and
  `?download=1` streams `build_stock_report_pdf` with
  `page_mode="report"` (new `report_numeric_from` kwarg controls where
  right-aligned numeric cells start; `report_table_title`/
  `report_table_subtitle`/`report_numeric_from` document keys let
  `_movement_print_tables` title non-movements tables).
- The **stock-management item report** (`?mode=report` item tab,
  `_build_item_report_rows` in `items/views.py`) follows the same rule:
  a row appears only when the item had period activity (in / out /
  transfer in / transfer out / sale / return / trade) — carried
  start/close balances with no in-period movement are hidden, and in
  multi-shop grouping only shops with period activity are listed under
  each item. The daily view (`_build_item_report_daily_rows`) already
  emits only days with activity. The movements page's TYPE=All item
  summary is the exception — it still lists idle stock as a
  current-stock board (`extra_items` → `_group_movement_events_by_item`).
- The shop floor catalog (`my_shop_catalog` → `build_shop_catalog_page`) lists
  sellable items only; shelf-only mode returns an empty catalog.
- **Block negative stock** (`CompanyPosSettings.block_negative_stock`, default
  on, company-only — not a shop override) is the Stock group's fourth toggle,
  hidden unless an items mode is on (`data-stock-items-dependent`). On: the
  `complete_shop_checkout` insufficient-stock check blocks sale/credit/
  trade-out/gift lines that would take `ShopStock` below zero (trade-in adds
  stock, quotations never move it). Off: tracked items can sell into negative
  `ShopStock` (`Item.stock` still floors at 0). Floor JS reads
  `data-pos-block-negative` → `stockLimited` (`my-shop.js`, `shop-catalog.js`)
  which replaces `stockTrackingEnabled` at the sell gates only (out-of-stock
  card state, qty clamps, serial caps, trade-out limits) — stock numbers still
  display. Chat sale/trade flows pass `allow_negative` into `_parse_lines`
  (`chat/stock.py`); serial-tracked items still need real serials either way.
  The standalone `pos:sale_create` API has its own Product model and always
  blocks — it does not read this flag. Tests:
  `shops/tests/test_pos_toggle_enforcement.py::PosToggleEnforcementTests::test_block_negative_stock_toggle_gates_overselling`.
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

## Stock health column (store manager pages)
- Both Company Store Manager stock lists carry a **Health** column rendering
  the shared `.low-stock-badge` pill — `is-low` "Low now" (animated pulse on
  the dot), `is-ok` "Watching", `is-off` "Off".
- `/company-store-manager/item-stock/`: rows come from
  `list_store_stock_items` (`items/store_stock_services.py`). There is no
  central-store threshold, so health is the item's low-stock alert status
  across shops: `Item.low_stock_notify` + each `ShopStock` alert level
  (manual `low_stock_threshold`, else the weekly-average auto threshold —
  same rules as `list_shop_low_stock_alerts`, rolled up by
  `_low_stock_shop_names` / `_item_health`). `StoreStock` qty itself is not
  part of the check.
- `/company-store-manager/shelf-stock/`: `ShelfItem.low_stock_threshold > 0`
  is the alert toggle (no notify flag); `_shelf_item_rows` stamps
  `health`/`health_label`/`health_title` off the same `is_low` logic.
- The shelf "On the shelf" table is the shared
  `shops/partials/shelf_stock_body.html` — the column only renders when the
  include passes `shelf_health=True` (the company page does; the shop's own
  shelf page does not). Cat-row colspan is computed from the
  readonly/manage/health flags.
- Pulse CSS: `.low-stock-badge.is-low::before` in `stock.css`
  (`low-stock-badge-pulse`, suppressed by prefers-reduced-motion). Tests:
  `shops/tests/test_stock_health_column.py`.
- `/shop-store-manager/stock-management/` (any role hitting the same view)
  shows the same Health column on **both** stock surfaces:
  - Item matrix: the view matrix is JS-rendered by `stock-catalog.js`, so
    `build_stock_catalog_page` (`items/services.py`) stamps `health`/
    `health_label`/`health_title` plus a per-shop `shop_health` array
    (aligned with `shop_quantities`/`shops`) on each `mode="view"` row —
    same alert rules as `list_shop_low_stock_alerts` (manual
    `low_stock_threshold` or weekly-average auto; a shop with no
    `ShopStock` row can't be low). Hidden-qty cells stay `null` in
    `shop_health` and out of the rollup. The JS adds a trailing Health
    `<th>`/`<td>` in the readOnlyMatrix path only and marks low qty cells
    `data-shelf-low`; the server-rendered `items_by_category` matrix is
    dead code (never populated). Action modes (in/out/request) carry no
    health fields.
  - Shelf matrix: `_shelf_view_matrix` (`items/views.py`) stamps the same
    row health off each cell's `is_low`/`has_alert`
    (`low_stock_threshold > 0` is the alert toggle) — feeds both the view
    Shelf stock tab and `items/partials/stock_shelf_panel.html` (in/out
    editable panel), which both render a trailing Health column.

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

## Combo items
- A **combo** is a normal `Item` with `is_combo=True` plus
  `ItemComboComponent` legs (`combo`/`component` FKs, `qty` portions,
  `position`; `uniq_combo_component` blocks duplicate legs). Each leg's
  `qty` is how many units of that item one combo uses (1 plate, 0.5 kg,
  2 pieces — any positive amount, no shared total). Registered/edited
  from the same Item Management form via the "Item type" radio — the
  combo panel (`data-combo-*` in `items/partials/item_form_fields.html`,
  `static/js/item-combo.js`) searches the management catalog API, lists
  picked items each with a `combo_qty` input (default 1), and shows the
  computed combo price (Σ price × portion). Typing a leg's KSh value
  (`data-combo-contrib`) back-solves that leg's qty. Migration
  `items.0073_combo_component_qty` converted `share` % → `qty`
  (qty = share/100, lossless at 4dp).
- Price: the **minimum** is each shop's items total (Σ component resolved
  shop price × qty) — `Item.minimum_selling_price` stores the lowest
  computed shop sum. The shop-pricing block stays visible for combos and
  sets the **selling** price (`_parse_combo_shop_prices`): a single price
  or per-shop prices via the usual `shop_price`/`shop_price_<id>` fields,
  each floored at that shop's computed total, while a blank field sells at
  the total. `Item.shop_price`/`ShopItemPrice` keep the entered selling
  prices (`shop_price` = the lowest). Serial tracking doesn't apply —
  components can't be suspended, combos, or serial-tracked, and a combo
  can't contain itself (`_parse_combo_components`; qty must be in
  (0, 9999.9999]).
- Resync: `_sync_combo_prices` recomputes the floor and writes selling
  prices — with posted prices it stores them, without them (component
  price/leg change via `update_item`/`delete_item` →
  `_sync_dependent_combo_prices`) it keeps each shop's stored price and
  only raises it where the new items total moved above it. Switching a
  combo back to "single" deletes its legs.
- Sale: `complete_shop_checkout` (`shops/services.py`) and the chat mirror
  (`chat/stock.py`, mirror model `ItemComboComponent` in
  `chat/myshop_models.py`) explode a combo cart line into one receipt line
  per component — leg qty = combo qty × leg.qty, unit price = component
  list scaled by charged-price/items-total, last leg absorbs rounding
  drift so line totals equal the charged total. The floor check rejects a
  combo charged below its shop's items total (`combo_min`; chat clamps
  instead). Stock, serial, sold-flag,
  cost, and return logic then run on the components (`from_combo` skips
  the sold-flag/min-price re-checks since the combo line already passed
  them); a suspended component or a legless combo blocks the sale.
- Combos hold no stock and are excluded from the stock-management matrix.
  Tests: `shops/tests/test_combo_items.py`.
- **Feature toggle**: `CompanyPosSettings.enable_combo_items` (default on,
  company-only — "Combo items" section on the company POS settings page).
  Off → the register form hides the item-type toggle, the "Register item
  combo" sidebar link disappears, `item_type=combo` posts are rejected for
  anything that isn't already a combo (existing combos stay editable and
  can be converted back to single), combos drop out of the floor catalog
  (`_catalog_base_qs`), and checkout + the chat mirror refuse combo lines.
  Mirrored on `chat.myshop_models.CompanyPosSettings`.

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
- `?mode=settings` swaps the sessions list for **Cashier settings** —
  company-wide options that govern cashier sessions
  (`_build_balances_settings` + `BALANCES_CASHIER_SETTING_GROUPS`: Open &
  close incl. `require_clock_in` + schedule mode/per-shop times, Shop
  switching, Session timeout incl. both idle limits) **plus company
  defaults for the per-cashier picks** on `/shop-cashier/settings/` —
  Dashboard sidebar, Sell popup, Day report (one-of status pick), and Day
  report sections. The defaults are stored on
  `CompanyPosSettings.cashier_default_prefs` (JSON) and edited via
  `set_cashier_default_pref` / `set_cashier_default_day_report_status`
  (employees/workspace.py, `CASHIER_DEFAULT_PREF_KEYS`); the pref readers
  (`cashier_sidebar_pref_enabled` / `cashier_sell_pref_enabled` /
  `day_report_status_mode` / `day_report_section_enabled`) resolve
  employee pick → company default → built-in default, so a cashier's own
  `sidebar_prefs` entry still wins where they've set one. Posted fields
  are the same as the cashier page (`sidebar_<key>`, `sell_<key>`,
  `day_report_sec_<key>`, `day_report_status_<mode>` — the last rides
  `data-stock-mode-toggle` via the group's `stock_mode` flag).
  Gated by `settings/company-pos` — the header "Cashier settings" button
  renders only with it (`filters["can_edit_company_pos"]`), and the GET
  mode itself is perm-checked in `_render_analytics`. The panel is
  `employees/partials/analytics_cashier_settings.html`
  (`data-pos-settings` + `pos-settings.js`), which POSTs to the balances
  URL — `analytics_section` allows POST for `balances` only and
  `_balances_settings_post` handles `toggle_pos_setting` (pref fields →
  company defaults; `BALANCES_CASHIER_SETTING_FIELDS` →
  `set_company_pos_setting`), `set_open_close_mode`,
  `set_open_close_times`, `set_employee_session_timeout` and
  `set_shop_session_timeout`, re-checking `analytics/balances` +
  `settings/company-pos`. POSTs on other sections → 405. For the company
  accountant this panel doubles as **"Accounts settings"** — their sidebar
  footer settings link (`_footer_site_links` COMPANY_ACCOUNTANT branch)
  points here since `settings/home` stays denied for that role. Tests:
  `employees/tests_cashier_settings.py::BalancesCashierSettingsTests`.
- The session report page is built by `build_day_session_report_page` —
  rendered as a **flat ledger, no cards**: `.ax--day-report` CSS at the end
  of `analytics.css` strips the `ax-card`/`ax-kpi` chrome into plain
  sections and keeps real rows/columns at every width (the shared
  mobile card-collapse is overridden — tables scroll sideways instead).
  The `?paper=` sheet renders every table through `sheet-grid`/`stack`
  (thermal mini/expense "sheet-minicard" blocks were removed), and the
  A4 PDF dropped the `BOX` outlines + padding for hairline rows.
  Same sections as the day accounting PDF (money flow, items sold,
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
  flag strip. The not-in-till figures render as **separate** tables —
  "Credit — not in the till" (Docs/Value/Collected/Still due + the counted
  credit-slips trail opened/counted/diff in the footnote,
  `day_report_sec_not_till`) and "Gifts — not in the till" (Docs/Value,
  `day_report_sec_gift`) — each followed by its own "Credit receipts" /
  "Gift receipts" line list when receipts exist
  (`day_report_not_till_lines` lines carry `key` = credit/gift so callers
  can split them). The "Credit payments collected" table + money-flow memo
  row hide under `day_report_sec_credit_payments` (all three keys are in
  `DAY_REPORT_SECTION_TOGGLES`, so they also appear in the cashier
  settings + company-defaults day-report-sections panels). Credit and
  gift value never enters Expected in till or Variance — credit is a
  receivable and gifts are free issues. The "Sales by employee" table
  follows Items sold — `day_session_report_details` returns `sellers`, a
  per-`created_by` rollup (receipts/qty/amount, same counted statuses and
  returned-qty netting as items sold, sorted amount desc; unnamed seller =
  "—") rendered on the page, the `?paper=` print sheet (page.tables loop)
  and the PDF. The Stocked in table is a flat list — Time +
  Item/Qty/Amount/Total per line plus a Status chip from
  `StockMovementLine.payment_status` (Paid/Partial/Unpaid); the item cell
  sub-label carries the entry source on every line and the movement's
  `amount_paid` on the batch's first line (the PDF repeats the time per
  row and stamps the batch `amount_paid` once on the first line's Paid
  column). Stocked out lists Time/Item/Qty/Reason/Amount/Total.
  `day_session_report_details` also returns `shelf_purchases` /
  `shelf_out` — the same group/line shapes built from
  `ShelfStockMovement` (REGISTER counts as stocked in; shelf outs carry
  no cost basis so Amount/Total show "—") — rendered as "Shelf stocked
  in/out" tables via the shared `_stocked_in_table`/`_stocked_out_table`
  closures (page) and `_stocked_in_detail`/`_stocked_out_detail` (PDF).
  Item tables show while `enable_stock_tracking` is on, shelf tables
  while `enable_shelf_mode` is on — `shop_stock_mode_flags`
  (`shops/services.py`) ANDs the company/effective flag with the shop's
  own `ShopPosSettings` flag while the shop runs custom POS settings
  (`override_pos`), so a shop that disables a stock type hides its pair
  (shops on company defaults follow the company mode). Either pair stays
  if rows exist for a since-disabled mode. Paid shelf stock-ins (`amount_paid`) also count
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
- The session report page also carries an **"Edit session"** action
  (header button → `data-modal-open="edit-day-session"`, partial
  `employees/partials/analytics_day_session_edit_modal.html`, JS
  `analytics-day-session-edit.js`) gated by the `analytics/edit-balances`
  permission key. It corrects only the session's recorded **opening float
  and counted closing** per till channel (`opening_cash`/`mpesa`/`card`
  + `closing_*` fields; closing inputs render only when the day is
  closed) — every report figure recalculates off the saved values.
  `build_day_session_report_page` emits `page.edit_session` only when its
  `allow_edit=True` kwarg is passed (just the `analytics_day_session`
  view — the shop-floor `my_shop_day_report` has no POST endpoint and
  stays view-only) and `employee_may(profile, "analytics",
  "edit-balances")` holds. POSTs hit the same URL:
  `update_shop_day_balances` (`shops/services.py`) re-parses the shown
  till channels via `_parse_balance_fields` (same open/close rules —
  shown channels are required, hidden channels keep their stored value),
  then `messages` + redirect back to `request.get_full_path()` so the
  `?back=` link survives. Tests:
  `AnalyticsDaySessionReportTests::test_edit_session_*`.

## Employees report (Analytics → Employees, slug `employees`)
- `_build_employees` lists cashier sales by shop; each Staff cell links to
  `<role>/analytics/employees/<profile_pk>/` (`analytics_employee`,
  `analytics_employee_url` in `employees/analytics_services.py`) carrying the
  current query string so the drill-down keeps the picked period + shops.
- The drill-down reuses the employee "My analytics" page —
  `build_employee_analytics` (`employees/services.py`, optional `shop_ids=`
  scopes it to the viewer's active shop filter) rendered by
  `employees/employee_analytics.html`; `employee_subject` in context swaps
  the "My analytics" copy for the employee's name. Gated by
  `analytics/employees`. Tests: `employees/tests.py::AnalyticsEmployeePageTests`.

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

## Suppliers workspace (Analytics → Suppliers, slug `suppliers`)
- Sidebar tabs (`sidebar_for_suppliers`, `?view=`): `all` (merged item+shelf
  suppliers), `items` (Supplier-registry stock-ins), `shelf` (free-text
  shelf stock-in suppliers).
- The `items` and `shelf` tabs carry an **in-page switch**
  (`?mode=supplied`, `page.view_tabs` → the shared `.ax-tabs` nav):
  *Suppliers* (supplier rows → their accounts) vs **Items supplied**
  (per-item Qty/Amt matrix from supplier-linked stock-in lines → that
  item's suppliers). `?mode=` only applies to `items`/`shelf`; `all`
  ignores it. Legacy `?view=supplied` redirects to
  `?view=items&mode=supplied` (also normalized inside `_build_suppliers`).
- The picked tab **and mode stick in the session**: `analytics_section`
  saves `suppliers_view` + `suppliers_mode` whenever `?view=` is present
  and redirects a bare `/suppliers/` visit to the saved pair
  (`?view=all` opts back out). `page.view` + `page.list_mode` drive hidden
  `view`/`mode` inputs in the shared filter form so Apply keeps both.
- **Items supplied** rows link to `/…/analytics/suppliers/items/<item_id>/`
  (`analytics_item_suppliers` → `build_item_suppliers_page`); shelf rows
  link to `/…/analytics/suppliers/shelf-items/<item_id>/`
  (`analytics_shelf_item_suppliers` → `build_shelf_item_suppliers_page`,
  sourced from `ShelfStockMovement` free-text supplier fields). Both pages
  show per supplier — receipts, item qty/value, their whole-account
  balance for the period, and a per-row **Pay** button (pay-all FIFO on
  their account; kind `stock` for registry suppliers, `shelf` + name/phone
  for free-text). Registered suppliers resolve by normalized identity
  (name + national phone digits) so `0722…` vs `+254 722…` merge; the cell
  links to the stock or shelf supplier ledger.
  `analytics-receipt-ledger.js` sets the pay form's `kind` input from the
  button's `data-pay-kind` and shows the button's due for pay-all.
  `_shelf_supplied_data` mirrors `_item_supplied_data` (each
  `ShelfStockMovement` is its own receipt); `_item_supplied_rows` takes
  `url_for`/`sub_of` hooks so the shelf matrix reuses it. Tests:
  `employees/tests_analytics_suppliers.py` (`test_supplied_mode_*`,
  `test_*_supplied_mode_*`, `test_item_suppliers_page_*`,
  `test_shelf_item_suppliers_page_*`,
  `test_suppliers_view_restores_last_tab`,
  `test_legacy_supplied_view_redirects_to_items_mode`).

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

## POS settings tabs + linked settings (`/<role>/settings/company-pos/`)
- `POS_SETTING_GROUPS` entries carry a `"tab"` (`POS_SETTING_TABS` in
  `employees/workspace.py`: all / checkout / stock / staff / floor). The tabs
  are **sidebar nav links**: on the company-pos page `sidebar_for_settings`
  renders the "POS settings" entry as an open nav group (`group=True` +
  `group_open=True`, children `child=True` + `child_open=True` — flags the
  sidebar template/`data-nav-group` collapse JS understand) whose child links
  are `<page>#tab-<slug>` anchors tagged `data-nav-key="pos-tab-<slug>"`.
  `pos-settings.js` intercepts those clicks (same-page, no reload), marks the
  active link `is-active`, and hides other tabs' groups via
  `data-pos-tab-section` + `.is-tab-hidden`. `#tab-<slug>` deep-links a tab;
  `#pos-group-<slug>` opens the tab holding that group. Typing in the
  settings search auto-switches to the All tab; a tab link dims
  (`.is-empty`) when no group in it matches. The STK Push section lives
  under Payment methods in the checkout tab (`show_stk_push` group flag →
  `employees/partials/pos_stk_push_group.html`).
- **Linked settings** hide while their parent toggle is off:
  `data-pos-requires="field_a|field_b"` on a row/section → `syncPosRequires()`
  in `pos-settings.js` shows it only while one of those toggles is on (e.g.
  STK Push needs an M-Pesa method, sidebar Trade needs a trade type, sidebar
  Shelf stock needs shelf mode, cart payment options needs a payment method).
  Server-side `hidden` mirrors the same rule on first render. Older linked
  pairs keep their dedicated attrs: `data-client-data-dependent`,
  `data-stock-items-dependent` (items/both mode — also gates Combo items),
  `data-open-close-dependent`, `data-tax-percent-row`,
  `data-delivery-amount-row`, `data-employee/shop-timeout-row`. Sidebar
  "Connect to printer" hides when `pos.enabled_print_channels()` is empty
  (`data-print-channels-dependent`, server-side only — print channels are set
  on the Receipt settings page).

## Receipt settings tabs + linked settings (`/<role>/settings/company-receipt/`)
- Same sidebar-tab pattern as the POS page: `RECEIPT_SETTING_TABS` in
  `employees/workspace.py` (all / printing / content / confirm) — on
  company-receipt the "Receipt settings" entry becomes the open nav group
  (children `data-nav-key="pos-tab-<slug>"` driven by the shared
  `pos-settings.js` machinery). `RECEIPT_SETTING_GROUPS` entries carry `"tab"`
  plus `"requires_channels"` (group only shows while a print channel is on)
  and `"hide_when_no_mpesa"` (server-side hide while
  `stk_shop.mpesa_checkout_enabled` is off).
- **Linked settings**: every print-only section requires at least one enabled
  print channel (`data-pos-requires="enable_print_bluetooth|enable_print_usb|enable_print_wifi"`,
  mirrored server-side off `pos_settings.enabled_print_channels`) — paper
  size, receipts-to-print copies, copy preview, M-Pesa payment details, text
  style, QR, return policy, maintainer, compulsory printing, and the
  on-receipt content toggles. `syncPosRequires()` in `pos-settings.js` also
  understands `data-pos-requires-all="a|b"` (every listed toggle must be on —
  the Payment details section needs a channel AND `enable_receipt_payment_methods`,
  the same flag that gates `payment_details` on printed receipts) and
  `data-pos-locked-hidden` (pins hidden because the parent toggle lives on
  another page: "Show activated payment methods" needs any POS payment
  method, "Show client phone number" needs `enable_client_data`, the copies
  group + preview need sale or credit enabled). Per-kind receipt-number-format
  rows stay server-hidden while that transaction kind is disabled
  (`data-receipt-kind-row`, `pos_settings.enable_*`).

## Cashier settings (`/shop-cashier/settings/`)
- Replaces "System settings" for `SHOP_CASHIER` profiles: footer sidebars
  (`_footer_site_links`) and `sidebar_for_settings` link to
  `cashier_settings_url()` labeled "Cashier settings" instead. The page needs
  `settings/home` permission; it shadows `role_settings` for the cashier
  segment (registered before `<role_segment>/settings/` in portal_urls).
- Cashiers never see Shop settings: `sidebar_for_my_shop` skips the footer
  "Shop settings" link for `SHOP_CASHIER` (falls through to the usual
  "Cashier settings" footer link) and points settings-page primary nav at
  `cashier_settings_url()`; the five `my_shop_settings*` views redirect a
  cashier profile there (`_cashier_shop_settings_redirect` in shops/views.py,
  checked before the `my-shop/shop_settings` permission gate). Shop portal
  sessions (profile=None) and other roles keep the shop settings pages.
- "Dashboard sidebar" toggles are **per-employee**, stored as keys in
  `EmployeeProfile.sidebar_prefs` JSON (missing key = shown) —
  `CASHIER_SIDEBAR_LINKS` / `cashier_sidebar_pref_enabled` /
  `set_cashier_sidebar_pref` (employees/workspace.py). POSTs reuse
  `action=toggle_pos_setting` with `field=sidebar_<key>` so `pos-settings.js`
  works unmodified; `_cashier_dashboard_links` drops hidden links (link-only,
  same convention as the floor toggles). Every per-employee pick
  (sidebar, Sell popup, day report status/sections) resolves employee
  pref → **company default** → built-in: the company defaults live on
  `CompanyPosSettings.cashier_default_prefs` and are edited from the
  analytics Cashier settings panel (see `?mode=settings` above), so the
  toggles here always show the effective value and a cashier's own pick
  overrides the default once set.
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
- The "Day report sections" group is also **per-employee** —
  `DAY_REPORT_SECTION_TOGGLES` (employees/workspace.py) stores
  `sidebar_prefs["day_report_sec_<section>"]` keys (missing = on, except
  `DAY_REPORT_SECTIONS_OFF_BY_DEFAULT`), POSTed as
  the pref key itself through `action=toggle_pos_setting`:
  money flow / not-in-till / items sold / sales by employee / pending &
  cancelled / drawings / expenses / item stock in-out / shelf stock in-out /
  dates.
  `day_report_section_enabled(profile, key)` checks them; the on-screen
  report tags each table via `_table(section=…)` and filters
  `page["tables"]`, while `build_shop_day_report_pdf` takes a
  `section_visible` predicate the callers (`my_shop_day_report`,
  `analytics_day_session`, chat `all_session_report`) build from the
  viewing profile's prefs — a disabled section is dropped even when it has
  rows (unlike the stock-mode gate, which keeps a pair alive for
  since-disabled modes with data). The all-shops assistant's generated
  "report" honours them too: `api_all_chat` passes the same predicate into
  `reply_for_all` / `card_for_all` → `_all_shops_report` — Sales/channels
  + refunds follow `day_report_sec_money_flow`, customer prepayments
  `day_report_sec_prepayments`, credit collections
  `day_report_sec_credit_payments`, expenses/item stock/shelf
  stock/drawings their own keys, credit `day_report_sec_not_till`, gifts
  `day_report_sec_gift`, the items-sold trailer
  `day_report_sec_items_sold`, and Paid out / each session's `out` sum
  only the enabled parts (sessions rows always list). Tests:
  `chat/tests.py::AllShopsReportSectionTests`.
  The two stock toggles also double as the **paid-from-till switch**:
  `day_report_sec_item_stock` / `day_report_sec_shelf_stock` feed
  `day_session_balance_summary(item_stock_from_till=…,
  shelf_stock_from_till=…)` — a section switched off means those paid
  stock-ins did NOT leave the till, so they're dropped from
  `suppliers_paid` ("Stock bought") and Expected in till on the report
  page and PDF alike (other callers default to from-till).
  `day_report_sec_dates` ("Dates") is the lone **opt-in** pick —
  `DAY_REPORT_SECTIONS_OFF_BY_DEFAULT` keeps it off until enabled; on,
  every row-level Time cell renders `dd Mmm · HH:MM` and the Time headers
  read "Date · time" on the page, the `?paper=` sheet, and the A4 PDF
  (`build_shop_day_report_pdf` reads it through `section_visible`, so
  callers without a predicate keep time-only rows). It tags no table —
  display-only.
- The "Open & close" group edits the shared company rows
  (`enable_open_close`, `CompanyWorkingHoursSettings.enabled`, per-shop
  `ShopWorkingHoursSettings` times — same as Settings → POS) and is gated by
  `settings/company-pos`. Template `employees/settings_cashier.html`. Tests:
  `employees/tests_cashier_settings.py`.

## Clock in / out (`/employee/clock/`)
- Every role dashboard sidebar carries a "Clock in / out" link
  (`sidebar_for_role_dashboard` + `sidebar_for_rider`);
  `DashboardSidebarLinkTests.test_every_role_dashboard_has_clock_link`
  asserts it for every `EmployeeRole`.
- `require_clock_in` (Settings → POS → Open & close, also in the
  analytics-balances Cashier settings panel) is company-only — it is NOT in
  `SHOP_POS_FIELD_NAMES`, so shop POS overrides can't weaken it. When on,
  `clock_in_block_message` (`employees/attendance.py`) blocks sale-like
  checkouts (sale/credit/trade-out/trade-in/gift) in
  `complete_shop_checkout` and the standalone `pos:sale_create` API until
  the authorising profile has an open clock entry; a clocked-out
  (auto-closed at day end) entry does not count. Floor JS shows the block
  via `staff-code` verify (`clocked_in` flag) before checkout. Tests:
  `shops/tests/test_clock_in_enforcement.py`.

## Action popups on role dashboards
- Sidebar `_action` items render `data-modal-open`/`data-href`; `workspace_base.html`
  opens `[data-modal="<name>"]` when the modal exists on the page, else follows the
  href. `?modal=sell|buy-stock|register-expense|prepayment` auto-opens on load.
- `expense_modal_context` / `buy_stock_modal_context` / `sell_modal_context`
  (employees/workspace.py) supply modal context **only for
  single-actionable-shop staff**; multi-shop staff fall back
  to the `data-href` (MY-SHOP picker / page).
- **Customer prepayments** are the exception: `prepayment_modal_context`
  works for multi-shop staff (a shop picker rides in the form). The
  "Register prepayment" action lives **only on the role prepayments page
  sidebar** (`sidebar_for_prepayments` — dashboards no longer carry it);
  the page popups `shops/partials/prepayment_modal.html`
  (`data-modal="prepayment"`, also via its own header button) and POSTs to
  `role_prepayments`
  (`/<role>/prepayments/` — GET lists, POST registers via
  `register_customer_prepayment`; `my_shop_prepayment_register` at
  `my-shop/<id>/prepayments/` is the shop-scoped variant). Prepayments are
  till money received ahead of orders — `_session_activity_totals` adds
  `prepayments_*` per channel into Expected in till (they are not sales,
  expenses, drawings, or credit collections), `day_session_report_details`
  returns `prepayments` rows, and the day report + PDF render a "Customer
  prepayments" money-flow row and detail table behind
  `day_report_sec_prepayments`. Payment choices come from
  `prepayment_payment_options` (the shop's active till channels only).
  Doc code: `prepayment_doc_code` → `A0007` style. Tests:
  `shops/tests/test_customer_prepayments.py`.
- Endpoints used by dashboard popups must allow allocation-level access (no floor
  unlock): `_require_allocated_shop_access` — the staff-ID field in the payload
  authorises the write. Used by register expense, open/close day, verify-login-code,
  **buy stock POST, stock-supplier search, and checkout POST**
  (`my_shop_checkout`). Catalog GETs, serial search, and the client lookup use
  `_require_shop_read_access`.
- Cashier dashboard (`_cashier_dashboard_links`) links render **only on
  `/shop-cashier/`** — other cashier pages get focused sidebars
  (`focused_role_sidebar` — Dashboard + page links): the shop-day picker
  uses `sidebar_for_cashier_shop_day` (mode links), the clock page
  `sidebar_for_employee_clock`, prepayments `sidebar_for_prepayments`, and
  the clients analytics pages `sidebar_for_analytics_section`. No Stock
  Management link — "Sell"
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
  `/shop-cashier/*` (403 JSON for XHR/non-GET) naming who must close it —
  **except `/shop-cashier/settings/`**, which stays reachable (it only stores
  the cashier's own prefs, so the blocked page's ACCOUNT section links it —
  `render_cashier_session_blocked` gates the footer link on
  `employee_may_any(profile, "settings")`) — and
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
  delegated shops. No central-store access. Exception: the Stock Management
  `?mode=settings` page (compulsory stock fields — company-wide, no shop data)
  is exempt from the shop-allocation gate via `ALLOCATION_EXEMPT_KEYS` in
  `employees/module_permissions.py` (`require_module_permission`,
  `ensure_employee_may`, and `module_entry_allowed` — the `workspace_module`
  dispatcher checks the request `?mode`); the "Store settings" footer link on
  store-manager sidebars therefore stays visible and open before any shop is
  allocated.
- Both share the `STORE_MANAGER_ALLOWED_KEYS` default allow-list via
  `STORE_MANAGER_ROLES` (`employees/module_permissions.py`); explicit HR
  permission rows still override.
- The shop store manager dashboard (`sidebar_for_role_dashboard`) carries
  **Item stock** / **Shelf stock** sidebar links + Quick-actions cards like
  the company dashboard, but shop-scoped: `_shop_item_stock_link` →
  `stock-management/?mode=view` (item view) shown while item stock tracking
  is on company-wide AND at least one in-scope shop tracks items
  (`_shop_stock_tracking_enabled`, requires `stock-management/view`);
  `_shop_shelf_stock_link` → `?mode=view&tab=shelf` shown while at least one
  in-scope shop runs Shelf mode (`_shop_shelf_mode_enabled`, requires
  `my-shop/shelf_stock`). `_shop_stock_scope_shops` defines "in scope" as
  the manager's allocated shops — or every active shop while unallocated,
  since they can still pick one on the role picker / Switch shop menu.
  Tests: `employees/tests.py::ShopStoreManagerRoleTests`.
- The company store manager dashboard (`role_home.html`, `store_report`
  context from `_render_role_page`) carries a **Stock report** card — the
  shared `analytics_date_range_fields.html` period picker plus the item
  search (`store_stock_item_search` + `store-stock-report.js`) — whose
  Generate button GET-submits to `store-report/` with `download=1`. The card
  only renders while item stock tracking is on and the profile has
  `stock-management/report`. `store_stock_report` streams a branded
  per-item PDF (Start / In / Transfers / Trades / Out / Close over the
  filtered period — like the other stock reports, only items that moved
  in the period are listed, `build_store_item_stock_report` in
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
  `view`/`movements`/`report` + `settings/company-pos` (the Cashiers
  report's `?mode=settings` cashier-defaults panel); everything else
  defaults to deny. Explicit HR permission rows still override (the matrix
  works as for any role).
- It is also in `FULL_STOCK_WORKFLOW_ROLES` so the stock-management view's
  role gate lets `movements`/`report` open — the per-mode permission check
  still blocks in/out/transfer/serials; treat the set as "eligible for the
  full stock pages", not "can run every stock action".
- Dashboard sidebar (`sidebar_for_role_dashboard` →
  `_dashboard_analytics_section_links`) shows: Stock Management, Analytics,
  Cashiers, Suppliers. The sidebar **footer** gets an "Accounts settings"
  link (`_footer_site_links` → `analytics/balances/?mode=settings`, marked
  active via `sidebar_for_analytics_section(settings_active=…)` on the
  settings mode) instead of the usual "System settings" — `settings/home`
  is denied, so the settings hub would dead-end. Tests:
  `employees/tests.py::CompanyAccountantRoleTests`.
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
- **IT support / super admin role picks get a writable support pass**: when
  an IT-support or super-admin primary acts in a different role
  (`profile.db_role` in `{it_support, super_admin}` and the picked role
  differs), `_apply_active_role` sets `profile.support_pass` (memory only;
  `access.profile_support_pass` reads it). The picked workspace is fully
  writable — writes go through the normal endpoints — and a foreign open
  cashier session still counts as "theirs" so support is never locked out
  of a live day: `cashier_session_blocks` returns nothing,
  `_cashier_foreign_day_denied` passes, and the cashier day-summary /
  shop-day rows count the open session as "theirs" (`own_session`) so the
  till figures render and the badge names the employee actually in session
  ("Open — {opener}"). Sidebar mutating links (Sell, open/close, Drawings,
  Buy stock), the sell/buy-stock modals, and `can_draw`/`can_open_close`
  row flags all work on the pass. `_cashier_own_days_only` returns False so
  day report/history stay shop-wide. Tests: `SupportPassRoleTests`.
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
  `kind=sale` for money flow) — they appear only in the report's own
  "Gifts — not in the till" + "Gift receipts" tables
  (`day_report_not_till_lines` `key="gift"` line, `gifts` aggregate +
  `credit_gift_receipts` in `day_session_report_details`), separate from
  credit, as value-not-money. They never join sales/credit revenue
  figures — they live in their own analytics section + receipt browse
  filter (`kind=gift`).
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

## Confirm-receipts: change receipt type
- The page Type filter (`CONFIRM_RECEIPT_KIND_FILTERS`) merges sales +
  credits into one **Sales & credits** (`sales_credits`) option and lists
  every other receipt kind (quotations, gifts, trade out/in, delivery
  notes, goods received) — "All types" pulls them all. Legacy
  `?kind=sale|credit` params normalize to `sales_credits` in
  `_confirm_receipts_effective_params`. Non-sale/credit rows are
  review-only there (`can_confirm`/`can_cancel`/`can_edit_status` off) —
  quotations/gifts/trades still confirm through their own flows.
- The confirm modal can convert a **pending** sale/credit to another kind
  (sale / credit / gift / quotation) — gated by the
  `analytics/change-receipt-type` permission on the **viewing** profile.
  The "Receipt type" select renders only when
  `receipt.can_change_kind` (set in `analytics_receipt_detail`); the
  confirm endpoint re-checks the same toggle whenever the posted
  `receipt_kind` differs from the stored kind.
- `change_shop_receipt_kind` (`shops/services.py`) does the conversion:
  only `ACTIVE` receipts, never trade kinds. → credit leaves the receipt
  pending (optional `credit_due_date`, credit audit when a client is
  linked); → gift confirms as a free issue; → quotation **restocks** all
  remaining lines (quotations never deduct stock); → sale confirms with
  payment — credit sources settle the full due via
  `record_credit_collection` (single-channel method only), quotation
  sources deduct stock first. A credit with `amount_paid > 0` may only
  convert to sale.
- The modal shows **Client name + Client phone** on every kind change
  (prefilled from the receipt). `analytics_receipt_detail` also sends
  `collect_client_data` / `require_client_name` / `require_client_phone` /
  `credit_whatsapp_required` so the JS can mark them required. The service
  applies the checkout rules: credit + quotation targets need a name or
  phone while `enable_client_data` is on (phone compulsory when
  `require_client_phone` or `credit_whatsapp_required`, name when
  `require_client_name`); posted values relink `receipt.client` via
  `upsert_client` / `find_client_by_phone` (phone-only credit registers a
  "CUSTOMER" client). Posted blanks replace the stored details; keys left
  out of the payload keep them.
- `analytics-confirm-receipts.js` carries `can_change_kind` forward across
  `renderDetail` payloads (confirm/set-status responses don't resend it),
  hides payment fields for non-sale targets, and restricts "Paid by" to
  single-channel methods when a credit converts to a sale.
- The list keeps compact merged columns: **Sold** stacks `sold_by` over
  `sold_as_label`, **Ref · Paid** stacks `reference` over
  `paid_when` (+ `paid_via`), rendered the same in the template and in
  `renderRowHtml` (`data-ax-confirm-rows`). "Print list" reuses the shared
  receipts print popup (`receipts_print_modal.html` +
  `analytics-receipts-print.js`): `?paper=a4|80|58` renders
  `analytics_confirm_receipts_print.html` (thermal gets
  `table.sheet-grid.stack` — one bold receipt line then wrapped
  "Label: value" pairs), `?estimate=1` returns the A4 page-count JSON, and
  `?download=1` streams `confirm_receipts_report_pdf_response`
  (`build_confirm_receipts_report_pdf` → `_analytics_report_pdf`, flat
  ledger with the same merged columns and a totals row).
- `sw.js` serves every `/static/` asset **cache-first keyed on the `?v=`
  URL** — bump the template `?v=` whenever the JS changes, or browsers
  keep running the stale file (a feature can look "missing" while the
  backend is correct).
- Tests: `shops/tests/test_receipt_kind_change.py`.

## Credit settlement → sale (paid-day revenue)
- A credit converts to `kind=SALE` + `settled_from_credit=True` the moment
  it's fully paid (`convert_settled_credit_to_sale` /
  `record_credit_collection` in `shops/credit_settlement.py`; mirrors in
  `chat/stock.py`). Surfaces: confirm-receipts, kind-change → sale,
  client account pay (`apply_account_payment` / `apply_credit_receipt_payment`
  FIFO), credit-note STK self-pay, full-return, chat.
- **Paid day = revenue day**: every collection/conversion stamps
  `confirmed_payment_at` (`paid_at` kwarg, default `timezone.now()`) — on a
  settled sale that is the day the credit cleared. Analytics attributes
  settled sales to that stamp, not `created_at`: `_settled_sale_range_q`
  (period filter in `_receipts_qs` / `_trading_line_qs`) and
  `_receipt_revenue_moment` / `_revenue_moment_trunc` (bucket truncs in
  `_docs_by_bucket`, `_sales_breakdown_table`,
  `_trading_by_bucket_for_period`). Settled rows with a NULL stamp fall
  back to `created_at` (migration `0125` backfills from the last credit
  PAYMENT event). Collection money also lands on the paid day via
  `ClientCreditAccountEvent` PAYMENT_* `occurred_at` — the confirm /
  kind-change flows now pass the entered `paid_at` as `occurred_at` too.
- **Not in the till**: session/day till math excludes settled credits'
  channel amounts — `day_session_balance_summary`,
  `list_shop_day_sessions`, `_day_balance_data` all filter
  `settled_from_credit=False`. Collections are account-level (analytics /
  STK) and never enter a cashier's session till, so counting them on the
  receipt's `created_at` day inflated the issue session's expected till.
- How-many-paid / conversion data: `settled_from_credit` + the paid stamp
  on the receipt, `ClientCreditAccountEvent` audit rows (meta.cleared),
  the analytics Paid cash/M-Pesa/card/total columns, and the
  "… now recorded as a sale" conversion notes in pay responses.
- The session day report surfaces the collections in its money-flow area:
  `day_session_report_details` returns `credit_payments` (rows + per-channel
  totals + `receipts_converted`) from the window's PAYMENT_* events;
  `build_day_session_report_page` adds a "Credit payments collected —
  outside till" memo row + a per-payment table (Time/Receipt/Channel/
  Outcome/Collected by/Amount, outcome = Converted to sale / Paid in full /
  Part payment), and `build_shop_day_report_pdf` prints the same memo row +
  detail table — all inside `day_report_sec_money_flow`, gated on
  `pos.enable_credit`.
- Tests: `shops/tests/test_credit_settlement.py`
  (`SettledSaleRevenueDayTests`).

## Receipt tally codes (dual kind codes on conversion)
- `ShopReceipt.converted_from_kind` records the immediate source kind when a
  receipt converts (`change_shop_receipt_kind`, `convert_settled_credit_to_sale`,
  trade settlement, `chat/stock.py` mirror — every `save(update_fields=…)` site
  lists it). `kind` stays the *current* kind.
- `receipt_tally_code(receipt)` (`shops/services.py`) is the display code used
  wherever a receipt is tallied/listed: unconverted receipts show their stored
  number (`S0005`); converted ones show both `RECEIPT_TALLY_LETTERS` letters +
  the original sequence — `CS0005` (credit→sale), `SC0007` (sale→credit),
  `SG0003` (sale→gift). Rows converted before the field existed fall back to
  `settled_from_credit`/`settled_from_trade` then `_infer_receipt_source_kind`
  (reads the stored number's prefix). `ShopReceipt.save()` enforces the
  provenance at the model level too (`from_db`/`refresh_from_db` track the
  loaded kind): any write that changes `kind` — including
  `save(update_fields=[...])` — auto-stamps `converted_from_kind` with the
  previous kind and appends the field to `update_fields`, so a missed
  stamping path can't produce a bare renumbered receipt again. The
  unmanaged `chat.myshop_models.ShopReceipt` mirror carries the same guard.
  Migration `0128` backfills
  `converted_from_kind` on pre-field conversions (settled flags + prefix
  inference), and `_build_receipt_ticket_data` prints the tally code so a
  converted doc never shows a misleading bare number on reprints.
- `CustomerPrepayment` ("advancements") is not a receipt — its tally code is
  `doc_code`/`prepayment_doc_code` (`DOC_NUMBER_PREFIX["prepayment"]` = `A`,
  e.g. `A0007`), shown on the floor receipts list, analytics browse, the day
  report prepayments table (Doc column, web + PDF), and the role prepayments
  page.
- Filters match **both** identities of a converted receipt: `list_shop_receipts`,
  `_analytics_receipt_kind_filter`, `_confirm_receipt_sale_rows`, and chat
  `list_receipts` all OR `kind=<k>` with `converted_from_kind=<k>` (+ the
  `settled_from_*` flags), so a sale→credit shows under Sales and Credits;
  "all"-type collectors dedupe by receipt id. Floor/analytics filters gain an
  `advancement` type that lists `CustomerPrepayment` rows.
- Consumed in `_receipt_list_item`/`tally_code`, `get_shop_receipt_detail`,
  `_collect_analytics_receipts_browse` rows (`_row_payload`, raw number kept in
  `search_text`), `build_analytics_receipts_list`, `_confirm_receipt_sale_rows`
  (`number`), `day_session_report_details` (`flagged_receipts`,
  `credit_gift_receipts`, `credit_payments`), the returned-receipts audit, and
  chat `_receipt_list_row`/`_receipt_tally_code` + bot replies.
- Tests: `shops/tests/test_tally_codes.py`.

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

## Shop store analytics (`?mode=shop-analytics` on stock-management)
- Sidebar link "Shop store analytics" in `sidebar_for_stock_management`
  (next to Activity analytics), gated by the normalized `report`
  permission — `normalize_submodule` maps `shop-analytics` → `report` in
  `employees/module_permissions.py`, so every role that can see the stock
  report sees it (store managers included).
- `stock_shop_analytics` (`items/views.py`, dispatched from
  `stock_management`) renders `items/stock_shop_analytics.html` with the
  usual shop select + `_report_range_bounds` period filters; full stock
  workflow roles only, scoped to `actionable_shops_for_profile` ∩
  item-stock-tracking shops.
- `_build_shop_store_analytics` groups shop trade-outs — `StockMovementLine`
  on OUT movements with `reason=trade_out` (POS trade receipts and manual
  stock outs alike) — per item: qty, weighted buying price, and value
  (`unit_cost`, falling back to `buying_price`). Per-supplier rollup =
  the amount supposed to be given to suppliers: each item is attributed
  to the supplier on its most recent stock-in line (in-scope shop first,
  then any shop); items never supplied with a supplier land in
  "Supplier not recorded". Matched `Supplier` registry rows link to the
  stock supplier account (`supplier_account_url(role, "stock", pk)`).
- **Shelf trade outs are folded into the same tables** —
  `ShelfStockMovement` OUT rows with `reason=trade_out` (the shelf
  request panel's trade flow), keyed `shelf:<pk>` so they never merge
  with a catalog item. The OUT row carries no price (`buying_price` is
  nulled on outs), so shelf trade outs are valued **FIFO**: each item's
  movement history is replayed and outbound qty draws down the oldest
  priced batch (a price-less in inherits the last batch price). Supplier
  attribution is the name/phone on the item's latest **stock-in** row —
  the person recorded on the trade out (`supplier_name`/
  `supplier_phone` via `keep_person`) is the *receiver*, not the
  supplier; items with no stock-in supplier land in "Supplier not
  recorded". Rows render with a "(shelf)" tag (`row.is_shelf`).
- The stock-management shelf sidebar links "Shelf stock trade" →
  `?mode=request&tab=shelf&flow=trade_out` (`shelf_scope` only, gated by
  the `request` permission); `stock-shelf.js` pre-checks the `?flow=`
  radio, and the view passes `active_mode="shelf-trade"` so the trade
  link highlights instead of "Shelf stock transfer".
- The shelf flow panel shows a **Cost** column — `cell.unit_cost` /
  `unit_cost_display` from `_shelf_view_matrix`, computed FIFO by
  `_fifo_unit_cost` (oldest surviving batch's price; a price-less in
  inherits the last batch price). The same `data-shelf-cost` feeds the
  floating submit panel's "@ cost" line and `≈` amount estimate.
- Tests: `items/tests.py::ShopStoreAnalyticsTests`.

## Chatbot (`chat` app)
- **Live updates on installed devices** are all polling — no websockets.
  `chatbot.js` beats `api_poll` every 2.5s (8s hidden, backoff to 15s on
  errors) and also fires immediately on `visibilitychange`/`pageshow`
  (bfcache)/`focus`/`online`. The heartbeat returns the conversation list
  + new messages (`after_id`) + a **change feed**: `Message.updated_at`
  (`auto_now`, migration `0015`) drives `active.changed` — already-held
  messages whose stored copy was rewritten since the client's last `now`
  watermark (`changed_since`, −3s overlap for in-flight commits, capped
  200). The client swaps in any serialized message that differs and
  repaints — this is how transfer outcomes (`card_msg`/`feed_msg` meta)
  and pending-confirmation rewrites (`msg.meta`/`body`) reach other
  devices. IMPORTANT: `update_fields` saves must list `"updated_at"`
  (Django's `auto_now` does not fire on partial saves); all three
  in-place mutation sites in `views.py` do. `client_tag` retag saves
  don't stamp it — tags never serialize. `pwa-update.js` + the
  pass-through `sw-chatbot.js` (`SW_VERSION` bump forces the reload that
  pulls new code on installed PWAs) keep the app shell current; bump the
  `?v=` on the changed script tags too. The receipts page (`receipts.js`,
  8s quiet refresh of list + open detail, scroll-preserving) and the
  store-manager feed (`store-manager.js`, 8s while the feed panel is
  open) live-update the same way.
- Rule-based assistant, no external AI: `chat/bot.py` `reply_for(shop,
  text)` drives the per-shop chat (sales / expenses / credit / stock /
  movements / top sellers / receipts / day status / prepayments);
  `reply_for_all` / `card_for_all` drive the all-shops assistant
  (`/chatbot/`, `api_all_chat`) whose cards render via
  `static/js/all.js`.
- `chat/myshop_models.py` mirrors the shared MY-SHOP tables as
  `managed = False` models — keep the mirrored columns (and the
  `ShopReceipt` conversion guard) in sync with `shops/models.py` when the
  real schema changes.
- Money semantics track the canonical services rather than older
  receipt-day math: `settled_from_credit` sales are excluded from till
  channel totals (their tender fields are zeroed via `_tender_exprs`)
  and attributed to the paid day through `confirmed_payment_at`
  (`_settled_window_q`); `CustomerPrepayment.cash/mpesa/card_amount` and
  `ClientCreditAccountEvent` PAYMENT_* rows are counted as separate
  money-in streams; `ShelfStockMovement.amount_paid` joins the paid-out
  side.
- Per-shop stock lookups honour the shop's own stock modes via
  `shop_stock_modes` (per-shop `ShopPosSettings` override → company
  default): item vs shelf ledgers are only searched when that mode is
  on, and shelf items appear in stock / low-stock replies only in
  shelf / both mode.
- The **stock assistant thread** (`ConversationKind.STOCK_BOT`) is a
  second per-shop bot conversation — `services.get_or_create_stock_bot`
  (pair_key `stockbot:<id>`, subtitle `STOCK_BOT_SUBTITLE` "Stock data
  helper", title `<shop> - Movements`) auto-created in
  `_conversations_payload` when the shop has any stock mode on. It
  answers stock questions only via `bot.reply_for_stock` /
  `card_meta_stock` — section queries (stock in / out, stock trade
  combined or traded in / out singly, transfers, suppliers, stock
  report) are built by merging
  `all_stock.build_stock_day` across the parsed date range
  (`_merge_stock_days`, ≤62 days); low stock, item lookups and the
  movement card reuse the main engine's helpers; anything else gets a
  "stock only" hint. Serialized as `kind: "bot"` (plus `stock_bot:
  true` and per-thread `suggestions` = `STOCK_SUGGESTIONS`) so the
  client reuses assistant UX; `chatbot.js` rebuilds the chips from
  `convo.suggestions` on open and `/chat/?open=stock` deep-links the
  thread. `is_bot_thread(convo)` covers both bot kinds for
  typing/transfer guards and message serialization. The chat header's
  package-search icon (`templates/chat/app.html`) links `?open=stock`.
- The **expenses assistant thread** (`ConversationKind.EXPENSE_BOT`,
  migration `0016_alter_conversation_kind`) is a third per-shop bot —
  `services.get_or_create_expense_bot` (pair_key `expensebot:<id>`,
  subtitle `EXPENSE_BOT_SUBTITLE` "Expense data helper", title
  `<shop> - Expenses`) auto-created unconditionally in
  `_conversations_payload`. `api_send` routes its messages to
  `bot.reply_for_expenses` / `card_meta_expenses` — the shop-scoped
  expense engine (`expenses`/`paid`/`unpaid`/`by category`/`suppliers`/
  `expense report`); anything else gets an "expense questions only"
  hint. Serialized like the stock bot — `kind: "bot"`,
  `expense_bot: true`, per-thread `suggestions` = `EXPENSE_SUGGESTIONS` —
  and `is_bot_thread` covers all three kinds. `/chat/?open=expenses`
  deep-links it (`chatbot.js` matches `expense_bot` on the convo list;
  `?open=assistant`/`?open=stock` pick the other bots). The shop chat
  sidebar pins **Shop accounts / Shop stock movement / Shop expenses**
  rows (`.wa-app__links` in `app.html`, stock row gated on
  `me.stock_modes`) to the three `?open=` links; staff see a second
  block — **Company accounts / Company stock movements / Company
  expenses** — linking `/chatbot/all/` + `?open=stock|expenses`.
  Tests: `chat/tests.py::ExpenseBotTests`.
- `/chatbot/all/?open=stock` renders the all-shops page in **stock
  mode** (`stock_mode` context): stock-only suggestion chips
  (stock in/out/trade/transfers/suppliers/movement/report/low stock),
  a stock welcome bubble, `data-stock-mode` on `.wa-app` so `all.js`
  posts `mode:"stock"`, and `api_all_chat` routes those to
  `bot.reply_for_all_stock` — the same section engine as the per-shop
  stock thread but `shop_id=None` / company stock modes (replies say
  "ALL SHOPS" and row heads name the shop). The sidebar "Stock
  movement" row links there and is `is-active` in stock mode;
  `/all/?open=stock` also auto-opens the Stock movement period picker.
  A **shop scope `<select>`** in the suggestion row (`allShopId`,
  stock + expenses modes) posts `shop=<pk>`; `api_all_chat` resolves
  it via `services.shop_by_id` and the reply/card engines delegate to
  the per-shop path so heads drop the repeated shop name.
  The **suppliers card** (`_stock_suppliers_card`, same in the per-shop
  stock thread) lists **unpaid suppliers only** — each supplier's Due
  is `Σ max(0, their value in a movement − the movement's
  `amount_paid`)` tracked per move in `all_stock._add_supplier`
  (shelf movements pay per row; `StoreStockMovement` has no
  `amount_paid` so `payment_status="paid"` counts fully settled), the
  same convention as `_registered_supplier_balance`. The card foot
  carries a `link` (`{"label", "ask"}` on the `table` payload, rendered
  by `chatbot.js` as a `data-ask` `.wa-link` that re-sends the query —
  same pattern as `data-bitems`) — "View N paid suppliers →" re-asks
  `<query> paid`; `suppliers paid …` (`_suppliers_paid_flag`) returns
  the settled list with a Paid column and a link back to the unpaid
  ones. `_stock_suppliers_reply` mirrors the split in text.
- `/chatbot/all/?open=expenses` renders the same page in **expenses
  mode** (`expenses_mode` context, `data-expense-mode` on `.wa-app`,
  sidebar "Expenses" row). Expense-only chips — expenses, paid,
  unpaid, by category, by shop, suppliers, expense report — routed by
  `api_all_chat` to `bot.reply_for_all_expenses` /
  `card_for_all_expenses` (`Expense` mirror: amount/amount_paid/
  payment_status/category/supplier). Replies and `table` cards honor
  the same shop scope + sticky topic as stock mode; today's expense
  totals come from `views._expenses_overview`.
- `/chatbot/all/stock/` (`chat:all_stock`, `all_stock_view`) is the
  all-shops stock movement **page** — reachable directly by URL (the
  "Stock movement" chip on `/chatbot/all/` is a `data-sug` chat query
  again).
  `/chatbot/chat/stock/` (`chat:shop_stock`, `shop_stock_view`,
  `@shop_required`) is the same page scoped to the signed-in shop —
  still reachable by URL though the chat header icon now opens the
  stock chat instead; a shop sees its own movements plus
  transfers where it is sender or receiver. Three ledgers via
  `chat/all_stock.py::build_stock_day(day, shop_id=, kind=, scope=)`:
  `scope=shops`+`kind=items` → shop-floor `StockMovement`,
  `kind=shelf` → per-shop `ShelfStockMovement` (registers count as
  in; transfers/trades ride on `reason`), `scope=store` → central
  `StoreStockMovement` (item stock only; POS mirror rows group by
  note/minute into one card). Hero chips pick scope (all page) and
  item-vs-shelf kind (only when both modes are on — company modes on
  `/all/stock/`, the shop's effective modes on `/chat/stock/`).
  Bucketing: `entry_source` wins on the shop ledger, legacy rows fall
  back to per-line `reason` (same trade filters as
  `items/views.py::_trade_qty_by_item_shop`); trade-tagged customer
  returns count as traded in, trade-in returns as traded out. Values:
  in/trade-in lines at `buying_price` (fallback `unit_cost`), out/
  trade-out/transfer lines at `unit_cost` (fallback `buying_price`).
  `?date=` picks the day, `?view=in|out|trade_in|trade_out|transfers|
  suppliers|report` drills into per-movement detail (`report` stacks
  all five sections). Suppliers rollup comes from `supplier_name` on
  in-lines (item + shelf + store ledgers). Renders
  `chat/all_stock.html` on the `chat-report.css` `.rpt-*` chrome;
  same `can_view_all_shops` gate as the assistant page. Tests:
  `chat/tests.py::AllShopsStockPageTests`.
- Tests: `chat/tests.py` (`BotMoneySemanticsTests`,
  `ShopShelfAssistantTests`, `AllShopsReportSectionTests`,
  `AllShopsShelfMovementTests`). The suite takes ~8 min:
  `MYSQL_DATABASE=myshop_<slug> python manage.py test chat -v 1`.
