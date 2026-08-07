# MY-SHOP

Employee portal, shop POS, analytics, and Daraja M-Pesa STK for retail operations.

## Stack

- **Frontend:** Responsive HTML/CSS/JS, Lucide icons
- **Backend:** Django 5–6
- **Database:** MySQL / MariaDB via PyMySQL (SQLite fallback for local bootstrap)
- **Static:** WhiteNoise
- **WSGI:** Passenger (cPanel) or Gunicorn (VPS)

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

Before going live:

1. Copy `.env.example` → `.env` and set a strong `DJANGO_SECRET_KEY`
2. Set `DJANGO_DEBUG=False`
3. Set `DJANGO_ALLOWED_HOSTS` to your domain(s)
4. Set `DJANGO_CSRF_TRUSTED_ORIGINS` to `https://your-domain`
5. Enable MySQL (`MYSQL_ENABLED=True`) with hosting credentials
6. Run `python manage.py migrate`
7. Run `python manage.py collectstatic --noinput`
8. Create a superuser if needed: `python manage.py createsuperuser`
9. Point Daraja callback to your public HTTPS domain (`DARAJA_CALLBACK_BASE_URL`)

### cPanel (Passenger)

1. Create a MySQL database + user in cPanel
2. Create a Python Application (Application root = project folder)
3. Startup file: `passenger_wsgi.py`
4. Enter the venv and install deps: `pip install -r requirements.txt`
5. Place `.env` in the project root (never commit it)
6. Set `SERVE_MEDIA_IN_PRODUCTION=True` if you store uploads on disk
7. `python manage.py migrate && python manage.py collectstatic --noinput`
8. Restart the Python app

WhiteNoise serves CSS/JS from `staticfiles/`. Media files are served by Django when `SERVE_MEDIA_IN_PRODUCTION=True` (fine for small cPanel installs).

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
python scripts/test_load_speed_loop.py
python scripts/test_page_hop_loop.py
```

## License

Private / proprietary unless otherwise stated.
