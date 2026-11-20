@echo off
rem VECTOR test client. Server address and CA file are prefilled on the login form.
rem The console window shows the client log (useful for bug reports); closing it closes the client.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
set /p VECTOR_SERVER_URL=<"%~dp0server-url.txt"
set VECTOR_CA_FILE=%~dp0certs\ca.pem
"%~dp0venv\Scripts\vector-client.exe"
if errorlevel 1 pause
