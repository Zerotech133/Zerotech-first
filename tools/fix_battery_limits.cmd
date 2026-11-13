@echo off
rem Right-click this file and choose "Run as administrator".
rem Clears DisallowStartIfOnBatteries / StopIfGoingOnBatteries on root-path tasks.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_battery_limits.ps1"
echo.
pause
