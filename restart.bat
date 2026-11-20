@echo off
rem GPUDashboard - restart server (stop + start, hidden)
cd /d "%~dp0"
call stop.bat
call start.bat
