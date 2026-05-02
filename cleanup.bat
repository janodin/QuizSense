@echo off
taskkill /F /FI "IMAGENAME eq python.exe" 2>nul
powershell.exe -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Format-Table -AutoSize"
