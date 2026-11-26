@echo off
rem VECTOR test stand UPDATE (Windows): install the version from THIS unpacked folder into an existing stand
rem and upgrade the database schema with a verified encrypted backup first. Stand data is kept.
rem Usage: double-click (stand in %USERPROFILE%\VECTOR-test) or: update.cmd D:\VECTOR-test
rem Rollback: restore backups\vector-premigrate-*.vbak into an empty DB with the old version (docs\ops\backup-restore.md).
setlocal
rem STAND - stand folder (default or first argument); REPO - root of this unpacked source folder.
set "STAND=%USERPROFILE%\VECTOR-test"
if not "%~1"=="" set "STAND=%~1"
for %%I in ("%~dp0..\..\..") do set "REPO=%%~fI"
rem UTF-8 everywhere: Cyrillic messages from Python must be readable in the console.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul
if not exist "%STAND%\venv\Scripts\vector-server.exe" goto nostand
echo.
echo VECTOR test stand: %STAND%
echo Close the server window (Ctrl+C) before continuing.
pause
cd /d "%STAND%"
echo.
rem Step 1: install the new program version into the stand venv; dependency versions are pinned by constraints.txt.
echo == 1/3 Installing the new version (a few minutes)...
"%STAND%\venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -c "%REPO%\deploy\test-stand\constraints.txt" "%REPO%[server,client]"
if errorlevel 1 goto failed
echo.
rem Step 2: migrate makes and verifies an encrypted backup BEFORE changing the schema (fail-closed).
echo == 2/3 Database schema upgrade (verified backup first)...
"%STAND%\venv\Scripts\vector-server.exe" migrate --config server.toml --key-file backup.key
if errorlevel 1 goto failed
echo.
rem Step 3: check = config, TLS files, DB connection and schema version must all be OK.
echo == 3/3 Check...
"%STAND%\venv\Scripts\vector-server.exe" check --config server.toml
if errorlevel 1 goto failed
echo.
echo Done. Start the server with start-server.cmd in %STAND%
pause
exit /b 0
:nostand
echo No test stand found in %STAND% (venv\Scripts\vector-server.exe). Run setup.cmd first or pass the stand folder.
pause
exit /b 1
:failed
echo.
echo FAILED - see the message above. The database is unchanged if the schema upgrade did not finish.
pause
exit /b 1
