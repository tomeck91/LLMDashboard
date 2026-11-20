@echo off
rem GPUDashboard - start server (Ctrl+C stops it)
cd /d "%~dp0"
echo Starting GPUDashboard on http://localhost:8000 ...
start "" "http://localhost:8000"
.\.venv\Scripts\python main.py
