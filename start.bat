@echo off
setlocal
cd /d "%~dp0"

rem Use the project venv if one exists, otherwise the system Python
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    set "PYW=.venv\Scripts\pythonw.exe"
) else (
    set "PY=python"
    set "PYW=pythonw"
)

rem First run: install dependencies if they're missing
%PY% -c "import uvicorn, fastapi, yt_dlp, webview" >nul 2>&1
if errorlevel 1 (
    echo First run - installing dependencies, this can take a few minutes...
    %PY% -m pip install -e ".[dev,desktop]"
    if errorlevel 1 (
        echo.
        echo Install failed. Is Python 3.11+ installed and on PATH?
        pause
        exit /b 1
    )
)

rem Watchlog.vbs calls this just for the install check above.
if /i "%~1"=="--install-only" exit /b 0

if /i "%~1"=="browser" goto :browser

rem Normal launch: pythonw.exe has no console, so the app opens as a plain
rem window and this one closes immediately. Errors that would have gone to a
rem console end up in watchlog-desktop.log instead.
start "" %PYW% -m app.desktop
exit /b 0

:browser
rem "start.bat browser" - the development path: server in this console, app in
rem your web browser, reloading itself whenever a file under app\ changes.
echo.
echo   My Watch Log - http://localhost:8000
echo   Close this window (or press Ctrl+C) to stop.
echo.
start "" cmd /c "timeout /t 2 >nul & start "" http://localhost:8000"
%PY% -m uvicorn app.main:app --port 8000 --reload --reload-dir app
pause
