@echo off
rem VECTOR test stand: encrypted backup with verification and rotation (14 newest kept).
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
"%~dp0venv\Scripts\vector-server.exe" backup --config server.toml --key-file backup.key
echo.
pause
