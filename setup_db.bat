@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\setup_mysql.py
) else (
  python scripts\setup_mysql.py
)

if errorlevel 1 (
  echo.
  echo Setup failed. See messages above.
  exit /b 1
)

echo.
echo Done. Run: python manage.py runserver
exit /b 0
