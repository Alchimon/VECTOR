@echo off
rem VECTOR test stand SELF-CHECK (read-only): server answers over HTTPS, the stand certificate is accepted,
rem demo accounts can log in, demo data for the test scenarios is present (including D-35). Nothing is changed.
rem The stand server must be running. Usage: double-click, or: check-stand.cmd D:\VECTOR-test
setlocal
set "STAND=%USERPROFILE%\VECTOR-test"
if not "%~1"=="" set "STAND=%~1"
rem UTF-8 everywhere: Cyrillic messages from Python must be readable in the console.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
if not exist "%STAND%\venv\Scripts\python.exe" goto nostand
"%STAND%\venv\Scripts\python.exe" "%~dp0..\stand_check.py" --dir "%STAND%"
set RC=%errorlevel%
echo.
pause
exit /b %RC%
:nostand
echo Test stand not found in %STAND% (no venv). Usage: check-stand.cmd D:\path\to\VECTOR-test
pause
exit /b 2
