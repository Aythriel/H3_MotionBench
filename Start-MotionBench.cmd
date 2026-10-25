@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-H3Dashboard.ps1" %*
if errorlevel 1 pause
