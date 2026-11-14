@echo off
setlocal
cd /d "%~dp0"

REM Deploy update for MY-SHOP on any Windows server.
REM Steps: pull latest code -> install requirements -> migrate -> collectstatic -> reload.
REM git pull alone does NOT refresh the JS/CSS browsers receive (WhiteNoise serves
REM the collected staticfiles/), so always deploy with this script.
REM
REM Optional settings (environment variables):
REM   DEPLOY_BRANCH=main      branch to deploy
REM   DEPLOY_PYTHON=C:\path   Python to use (otherwise auto-detected)
REM   DEPLOY_SKIP_PIP=1       skip "pip install -r requirements.txt"

if not defined DEPLOY_BRANCH set "DEPLOY_BRANCH=main"
if /i "%~1"=="--pulled" goto after_pull

echo [1/4] Pulling latest code (%DEPLOY_BRANCH%)...
git fetch origin "+refs/heads/%DEPLOY_BRANCH%:refs/remotes/origin/%DEPLOY_BRANCH%"
if errorlevel 1 (
  echo.
  echo Deploy stopped: could not download from GitHub ^(check internet/credentials^).
  exit /b 1
)

git diff --quiet
if errorlevel 1 goto dirty
git diff --cached --quiet
if errorlevel 1 goto dirty

REM Kept on one line: cmd reads this file as it runs, and checkout/merge may
REM replace it, so the freshly pulled copy is re-run for the remaining steps.
git checkout -q %DEPLOY_BRANCH% && git merge --ff-only origin/%DEPLOY_BRANCH% && (call "%~f0" --pulled & exit /b) || (echo. & echo Deploy stopped: could not switch to %DEPLOY_BRANCH% or this server copy has its own commits that are not on GitHub. Ask a developer to sort it out. & exit /b 1)

:after_pull
set "PY="
if defined DEPLOY_PYTHON set "PY=%DEPLOY_PYTHON%"
if not defined PY if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PY=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY if exist "env\Scripts\python.exe" set "PY=%CD%\env\Scripts\python.exe"
if not defined PY set "PY=python"
echo Using Python: %PY%

if "%DEPLOY_SKIP_PIP%"=="1" (
  echo [2/4] Skipping requirements ^(DEPLOY_SKIP_PIP=1^).
) else (
  echo [2/4] Installing requirements...
  "%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Deploy stopped: pip install failed. See messages above.
    exit /b 1
  )
)

echo [3/4] Applying database migrations...
"%PY%" manage.py migrate --noinput
if errorlevel 1 (
  echo.
  echo Deploy stopped: migrations failed. See messages above.
  exit /b 1
)

echo [4/4] Rebuilding static files...
"%PY%" manage.py collectstatic --noinput
if errorlevel 1 (
  echo.
  echo Deploy stopped: collectstatic failed. See messages above.
  exit /b 1
)

REM Signal Passenger (cPanel) to reload the app on next request; harmless elsewhere.
if not exist tmp mkdir tmp
copy /b tmp\restart.txt +,, >nul 2>&1 || type nul > tmp\restart.txt

echo.
echo Deploy complete. Restart the app/service if it does not reload on its own.
exit /b 0

:dirty
git status --short --untracked-files=no
echo.
echo Deploy stopped: the files above were edited on this server. Undo them
echo (git checkout -- ^<file^>) or commit them, then re-run.
exit /b 1
