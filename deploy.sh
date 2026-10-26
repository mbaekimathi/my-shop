#!/usr/bin/env bash
# Deploy update for MY-SHOP: pull code, apply migrations, rebuild statics.
# Run this on the server after every update — git pull alone does NOT refresh
# the JS/CSS browsers receive (WhiteNoise serves the collected staticfiles/).
set -euo pipefail
cd "$(dirname "$0")"

PY=python
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"
[ -x "venv/bin/python" ] && PY="venv/bin/python"

echo "[1/3] Pulling latest code..."
git pull --ff-only origin main

echo "[2/3] Applying database migrations..."
"$PY" manage.py migrate --noinput

echo "[3/3] Rebuilding static files..."
"$PY" manage.py collectstatic --noinput

# Signal Passenger (cPanel) to reload the app on next request; harmless elsewhere.
mkdir -p tmp && touch tmp/restart.txt

echo ""
echo "Deploy complete. Gunicorn/VPS installs still need a service restart"
echo "(Passenger reloads automatically via tmp/restart.txt)."
