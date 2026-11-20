@echo off
rem GPUDashboard - start server HIDDEN (no console window). Use stop.bat to quit.
cd /d "%~dp0"
netstat -ano | findstr "LISTENING" | findstr /C:":8000 " >nul && (
  echo Already running on http://localhost:8000
  goto open
)
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '.\.venv\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0' -RedirectStandardOutput 'server.log' -RedirectStandardError 'server.err.log'; Start-Sleep 2"
:open
start "" "http://localhost:8000"
echo GPUDashboard is running (hidden). Logs: server.log. Stop with stop.bat
