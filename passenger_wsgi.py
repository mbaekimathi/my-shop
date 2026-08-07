"""
Passenger WSGI entry for cPanel / CloudLinux Python apps.

Point the Application Root at this project folder and set the
Application Startup File to: passenger_wsgi.py

Ensure the Python app virtualenv has: pip install -r requirements.txt
and that .env exists next to manage.py with production values.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Prefer project root on sys.path (Passenger sometimes starts elsewhere).
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myshop.settings")

# Load .env before Django if python-dotenv is available.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
