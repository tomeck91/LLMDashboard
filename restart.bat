@echo off
rem GPUDashboard - restart server (stop + start)
cd /d "%~dp0"
call stop.bat
start "" "http://localhost:8000"
.\.venv\Scripts\python main.py
