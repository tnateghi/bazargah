@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "PY="

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import fastapi" >nul 2>&1
  if not errorlevel 1 set "PY=.venv\Scripts\python.exe"
)

if not defined PY (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -c "import fastapi" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
  )
)

if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import fastapi" >nul 2>&1
    if not errorlevel 1 set "PY=python"
  )
)

if not defined PY (
  echo [ERROR] FastAPI not found.
  echo Run install.bat once, or: pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo Using: %PY%
echo Panel: http://127.0.0.1:8787
echo Stop: Ctrl+C
echo.

rem If port already open, just open browser and exit
powershell -NoProfile -Command "try { $c = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction Stop; if ($c) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  echo Port 8787 already in use — opening existing panel.
  start "" "http://127.0.0.1:8787"
  pause
  exit /b 0
)

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8787"

%PY% app.py
if errorlevel 1 (
  echo.
  echo Failed to start. Run install.bat again.
  pause
)
