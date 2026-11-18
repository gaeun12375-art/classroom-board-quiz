@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [Board game] Checking Python 3.12 or 3.13...
set "PY_CMD="
py -3.12 --version >nul 2>&1 && set "PY_CMD=py -3.12"
if not defined PY_CMD py -3.13 --version >nul 2>&1 && set "PY_CMD=py -3.13"
if not defined PY_CMD (
    echo.
    echo Python 3.12 or 3.13 could not be found.
    echo Please install Python 3.12.10 for Windows from:
    echo https://www.python.org/downloads/release/python-31210/
    echo You do NOT need to uninstall Python 3.15.
    echo After installation, close this window and double-click this file again.
    echo.
    pause
    exit /b 1
)
echo Using %PY_CMD%
if not exist ".venv-boardgame\Scripts\python.exe" (
    echo Creating the app environment...
    %PY_CMD% -m venv .venv-boardgame
    if errorlevel 1 goto failed
)
echo Installing app dependencies...
".venv-boardgame\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Ready. Keep this window open while playing.
echo Open http://127.0.0.1:8000 in a browser on THIS computer.
echo.
".venv-boardgame\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
if errorlevel 1 goto failed
pause
exit /b 0

:failed
echo.
echo The app could not start. Please take a photo of the errors above.
pause
exit /b 1
