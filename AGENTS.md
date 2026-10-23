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

## Auth model
- Two independent session types per browser: employee (Django auth) and shop
  portal (`shop_portal_auth` + `shop_portal_shop_id` session keys).
- **Single-session enforcement (employees only)**: `EmployeeProfile.active_session_key`
  holds the session key of the one live employee session. Stamped at login
  (`begin_employee_session`), migrated on session-key rotation
  (`_migrate_employee_session_stamp`), verified in `active_employee_required` /
  `ping_api` / `_sync_actor`. A newer login on another device supersedes the
  older one (it gets signed out). **Shop portal sessions are concurrent** —
  many devices may be signed in to the same shop at once.
  `Shop.active_session_key` only records the most recent sign-in for
  diagnostics (stamped by `bind_shop_portal_session`, migrated by
  `migrate_shop_session_stamp`); `resolve_portal_shop` does not enforce it.
- Session-key rotation is bridged by `RotatedSessionMiddleware` (old key →
  pointer → new key, 120s grace) so in-flight tabs survive login rotations.
- The client-side `session-guard.js` tab lock (localStorage + BroadcastChannel)
  applies to employee workspaces only — shop tabs may run concurrently. It
  covers one browser profile; real enforcement is server-side via the stamps above.
