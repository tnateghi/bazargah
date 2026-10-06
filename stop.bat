@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo Stopping panel on port 8787...
powershell -NoProfile -Command ^
  "Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"name='python.exe' OR name='pythonw.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done.
timeout /t 2 /nobreak >nul
