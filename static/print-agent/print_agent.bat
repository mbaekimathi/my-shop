@echo off
rem MY-SHOP local print agent - double-click to run.
rem Keep this window open while the shop is open. Closing it stops printing.
title MY-SHOP Print Agent
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 print_agent.py %*
    goto check
)
where python >nul 2>&1
if %errorlevel%==0 (
    python print_agent.py %*
    goto check
)
echo.
echo Python 3 is not installed on this computer.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" during setup, then run this file again.
goto pause

:check
if errorlevel 1 (
    echo.
    echo The agent stopped with an error - read the message above.
    echo Most often it is a missing print_agent.json config or an old token.
    goto pause
)
exit /b 0

:pause
pause
