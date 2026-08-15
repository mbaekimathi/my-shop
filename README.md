# MY-SHOP

Employee portal, shop POS, analytics, and Daraja M-Pesa STK for retail operations.

## Stack

- **Frontend:** Responsive HTML/CSS/JS, Lucide icons
- **Backend:** Django 5–6
- **Database:** MySQL / MariaDB via PyMySQL (SQLite fallback for local bootstrap)
- **Static:** WhiteNoise
- **WSGI:** Passenger (cPanel) or Gunicorn (VPS)
- **Passwords:** Argon2 via `argon2-cffi` (required; install from `requirements.txt`). On cPanel, after `pip install -r requirements.txt`, confirm `python -c "import argon2"` succeeds before restarting Passenger.

## Quick start (local)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
setup_db.bat
python manage.py runserver
```

Or: `python scripts/setup_mysql.py`

Open http://127.0.0.1:8000/

## Production checklist

`.env` is minimal — DEBUG, secret key, hosts, CSRF, SSL, media, and Daraja
callback auto-detect for local vs cPanel/Passenger/Gunicorn.

Usually you only edit MySQL credentials on the host:

```env
MYSQL_ENABLED=True
MYSQL_DATABASE=your_db
MYSQL_USER=your_user
MYSQL_PASSWORD=your_password
MYSQL_HOST=localhost
```

Optional markers/overrides:

- Create an empty `.production` file to force hosted mode
- Or set `APP_ENV=production` / `APP_ENV=local`
- Secret key auto-saves to `.secret_key` (gitignored) if unset

Then:

1. `python manage.py migrate`
2. `python manage.py collectstatic --noinput`
3. `python manage.py createsuperuser` (optional)
4. Point Daraja to your public HTTPS domain (auto once you open the site over HTTPS)

### cPanel (Passenger)

1. Create a MySQL database + user in cPanel
2. Create a Python Application (Application root = project folder)
3. Startup file: `passenger_wsgi.py`
4. Enter the venv and install deps: `pip install -r requirements.txt`
5. Place `.env` in the project root with MySQL credentials only (never commit it)
6. `python manage.py migrate && python manage.py collectstatic --noinput`
7. Restart the Python app

WhiteNoise serves CSS/JS from `staticfiles/`. Media is served automatically on hosted installs.

### VPS (Gunicorn + nginx)

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn -c gunicorn.conf.py myshop.wsgi:application
```

Put nginx (or Caddy) in front, terminate TLS, proxy to Gunicorn (`127.0.0.1:8000`), and serve `/media/` from disk or object storage. Prefer `REDIS_URL` when running multiple workers.

Example systemd unit idea: run gunicorn with `WorkingDirectory` = project root and `EnvironmentFile` = `.env`.

## MySQL setup helpers

```bash
setup_db.bat
# or
python scripts/setup_mysql.py
```

## Tests / loops

```bash
python scripts/test_mysql_efficiency.py
python scripts/test_online_offline_loop.py
python scripts/test_connectivity_loop.py
python scripts/test_load_speed_loop.py
python scripts/test_page_hop_loop.py
```

## License

Private / proprietary unless otherwise stated.
