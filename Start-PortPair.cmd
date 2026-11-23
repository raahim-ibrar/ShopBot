@echo off
cd /d "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo PortPair's Python environment is missing. See README.md for local setup.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B scripts\run_local.py %*
if errorlevel 1 (
    pause
    exit /b 1
)
