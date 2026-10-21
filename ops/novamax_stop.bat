@echo off
rem NovaMax stop - free ~40GB unified memory (ports 1234/1238)
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\aios\ops\novamax_stop.ps1"
pause
