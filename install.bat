@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo   Bazargah setup (Windows)
echo ========================================
echo.

where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if %errorlevel%==0 (
    set "PY=python"
  ) else (
    echo [ERROR] Python not found.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Enable "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
  )
)

echo [1/4] Creating .venv ...
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
) else (
  echo       .venv already exists.
)

set "VPY=.venv\Scripts\python.exe"
set "VPIP=.venv\Scripts\pip.exe"

echo [2/4] Upgrading pip ...
"%VPY%" -m pip install --upgrade pip

echo [3/4] Installing requirements ...
"%VPIP%" install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install requirements.
  pause
  exit /b 1
)

echo [4/4] Installing Playwright Chromium ...
"%VPY%" -m playwright install chromium
if errorlevel 1 (
  echo [ERROR] Failed to install Chromium.
  pause
  exit /b 1
)

if not exist "data" mkdir data
echo. > "data\.gitkeep" 2>nul

echo.
echo ========================================
echo   Setup done. Run start.bat next.
echo ========================================
echo.
pause
