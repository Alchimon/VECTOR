@echo off
rem VECTOR test stand setup (Windows). Double-click this file or run: setup.cmd [-Lan] [-NoDemo] [-Dir path]
rem All logic is in setup-test-stand.ps1 (next to this file); see docs\ops\test-pc.md.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-test-stand.ps1" %*
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
