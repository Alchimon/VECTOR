@echo off
rem VECTOR test server. Keep this window open; stop with Ctrl+C.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
"%~dp0venv\Scripts\vector-server.exe" check --config server.toml
if errorlevel 1 goto fail
"%~dp0venv\Scripts\vector-server.exe" serve --config server.toml
:fail
echo.
pause
