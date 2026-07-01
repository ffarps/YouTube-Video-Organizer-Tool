@echo off
setlocal
cd /d "%~dp0"

rem Use the project venv if one exists, otherwise the system Python
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

rem First run: install dependencies if they're missing
%PY% -c "import uvicorn, fastapi, yt_dlp" >nul 2>&1
if errorlevel 1 (
    echo First run - installing dependencies, this can take a few minutes...
    %PY% -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo.
        echo Install failed. Is Python 3.11+ installed and on PATH?
        pause
        exit /b 1
    )
)

echo.
echo   YouTube Video Organizer - http://localhost:8000
echo   Close this window (or press Ctrl+C) to stop.
echo.

rem Open the browser once the server has had a moment to start
start "" cmd /c "timeout /t 2 >nul & start "" http://localhost:8000"

%PY% -m uvicorn app.main:app --port 8000
pause
