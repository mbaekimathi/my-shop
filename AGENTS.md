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

## Wi‑Fi printing
- Channels (Bluetooth/USB/Wi‑Fi) → `enable_print_*` flags; "Connect to printer"
  lives in the shop floor workspace sidebar, gated by `my-shop/print` permission.
- LAN printer discovery: `shops/printer_discovery.py` (fast scan = ARP + Windows
  ports + common DHCP suffixes; deep scan = ping-sweep then TCP-probe live hosts
  on ports 9100/9101/9102/9112/9200/10001/631/515).
- USB + Bluetooth print **client-side** (WebUSB/Web Serial/Web Bluetooth +
  hidden-iframe `win.print()`); Wi‑Fi prints **server-side** via
  `my_shop_print_relay` (server opens TCP to `host:9100`).
- **Hosted (cPanel) mode**: the server can't reach shop LAN printers, so the
  relay queues a `PrintAgentJob` when the target is off the server's LAN and
  `shop.print_agent_token` is set (else 502 `agent_required`). A local agent
  (`static/print-agent/print_agent.py`) polls `print-agent/<shop>/jobs/` with
  the `X-Print-Agent-Token` header and acks `…/jobs/<id>/` (done/failed).
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
