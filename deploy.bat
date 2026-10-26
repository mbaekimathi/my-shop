@echo off
setlocal
cd /d "%~dp0"

REM Deploy update for MY-SHOP: pull code, apply migrations, rebuild statics.
REM Run this on the server after every update — git pull alone does NOT refresh
REM the JS/CSS browsers receive (WhiteNoise serves the collected staticfiles/).

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo [1/3] Pulling latest code...
git pull --ff-only origin main
if errorlevel 1 (
  echo.
  echo Pull failed. Resolve local changes or conflicts, then re-run deploy.
  exit /b 1
)

echo [2/3] Applying database migrations...
%PY% manage.py migrate --noinput
if errorlevel 1 (
  echo.
  echo Migrations failed. See messages above.
  exit /b 1
)

echo [3/3] Rebuilding static files...
%PY% manage.py collectstatic --noinput
if errorlevel 1 (
  echo.
  echo collectstatic failed. See messages above.
  exit /b 1
)

REM Signal Passenger (cPanel) to reload the app on next request; harmless elsewhere.
if not exist tmp mkdir tmp
copy /b tmp\restart.txt +,, >nul 2>&1

echo.
echo Deploy complete. Gunicorn/VPS installs still need a service restart
echo (Passenger reloads automatically via tmp\restart.txt).
exit /b 0
