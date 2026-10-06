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

echo Stopping old panel on port 8787 (if any)...
powershell -NoProfile -Command ^
  "Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"name='python.exe' OR name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul

echo Using: %PY%
echo Panel: http://127.0.0.1:8787
echo Stop: Ctrl+C   or run stop.bat
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8787"

%PY% app.py
if errorlevel 1 (
  echo.
  echo Failed to start. Run install.bat again.
  pause
)
