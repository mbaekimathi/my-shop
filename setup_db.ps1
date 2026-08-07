$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".\.venv\Scripts\python.exe") {
    ".\.venv\Scripts\python.exe"
} else {
    "python"
}

& $python scripts\setup_mysql.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Setup failed."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Done. Run: python manage.py runserver"
