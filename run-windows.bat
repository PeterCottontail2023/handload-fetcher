@echo off
rem Double-click this to run Handload Fetcher -- no typing required.
rem It handles first-time setup (vendoring dependencies) itself.

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on this computer.
    echo Install it from https://python.org/downloads ^(check "Add python.exe to PATH"
    echo during setup^), then double-click this file again.
    echo.
    pause
    exit /b 1
)

if not exist "handloadfetcher\vendor\pdfplumber" (
    echo Setting up Handload Fetcher for the first time -- this only happens once...
    python -m pip install --target=handloadfetcher\vendor -r requirements.txt
    echo.
)

where pdftotext >nul 2>nul
if errorlevel 1 (
    echo Note: "pdftotext" wasn't found. A couple of manufacturers' data
    echo won't load without it. Get it from https://github.com/oschwartz10612/poppler-windows
    echo ^(unzip, then add its "bin" folder to your PATH^), or search
    echo "poppler for windows" if that link is stale.
    echo.
)

python -m handloadfetcher %*
echo.
pause
