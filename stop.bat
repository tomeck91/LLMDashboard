@echo off
rem GPUDashboard - stop server (kills whatever listens on port 8000)
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
echo GPUDashboard stopped.
