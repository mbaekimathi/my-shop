"""
Auto-create the MY-SHOP MySQL database and apply Django migrations.

Defaults: user=root, password empty, database=myshop, host=127.0.0.1:3306
Safe to re-run — never drops the database; only applies pending migrations.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def ensure_env() -> None:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE.exists():
            raise SystemExit("Missing .env and .env.example — cannot bootstrap.")
        ENV_PATH.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Created {ENV_PATH} from .env.example")
    load_dotenv(ENV_PATH, override=True)


def mysql_settings() -> dict:
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "myshop"),
    }


def create_database(cfg: dict) -> None:
    name = cfg["database"]
    print(
        f"Connecting to MySQL at {cfg['host']}:{cfg['port']} "
        f"as {cfg['user']!r} (password {'set' if cfg['password'] else 'empty'})..."
    )
    try:
        conn = pymysql.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
        )
    except pymysql.Error as exc:
        raise SystemExit(
            f"Cannot connect to MySQL: {exc}\n"
            "Ensure MySQL/MariaDB is running (e.g. XAMPP) and credentials match .env."
        ) from exc

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.execute("SHOW DATABASES LIKE %s", (name,))
            if not cur.fetchone():
                raise SystemExit(f"Database {name!r} was not created.")
        print(f"Database ready: {name}")
    finally:
        conn.close()


def force_mysql_enabled() -> None:
    """Ensure Django uses MySQL for this process."""
    os.environ["MYSQL_ENABLED"] = "True"
    # Persist in .env if currently disabled
    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
        if "MYSQL_ENABLED=False" in text or "MYSQL_ENABLED=false" in text:
            text = (
                text.replace("MYSQL_ENABLED=False", "MYSQL_ENABLED=True")
                .replace("MYSQL_ENABLED=false", "MYSQL_ENABLED=True")
            )
            ENV_PATH.write_text(text, encoding="utf-8")
            print("Updated .env: MYSQL_ENABLED=True")


def run_migrations() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")

    import django
    from django.core.management import call_command

    django.setup()
    print("Running migrations...")
    call_command("migrate", interactive=False, verbosity=1)
    print("Migrations complete.")


def main() -> int:
    ensure_env()
    force_mysql_enabled()
    cfg = mysql_settings()
    create_database(cfg)
    run_migrations()
    print("MY-SHOP MySQL is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
