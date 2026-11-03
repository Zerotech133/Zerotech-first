# LingJie A0 daily board run - 07:50 wrapper (ASCII only)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Py = 'C:\Program Files\Python311\python.exe'
$Log = 'C:\Temp\hq_daily_run.log'
$env:PYTHONIOENCODING = 'utf-8'
& $Py -X utf8 (Join-Path $Root 'tools\hq_daily_run.py') *>> $Log
exit $LASTEXITCODE
