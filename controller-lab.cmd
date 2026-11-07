@echo off
rem Starts Controller Lab. Needs uv: https://docs.astral.sh/uv/
where uv >nul 2>nul
if errorlevel 1 (
    echo Controller Lab needs uv, which is not installed or not on PATH.
    echo Install it with:
    echo.
    echo     winget install --id=astral-sh.uv -e
    echo.
    echo Then open a new terminal, or double-click this file again.
    pause
    exit /b 1
)
uv run --project "%~dp0." controller-lab
if errorlevel 1 pause
