# HZ 1104 finalize, one-shot 10:30 (ASCII only)
$Root = Split-Path -Parent $PSScriptRoot
$Py = 'C:\Program Files\Python311\python.exe'
$Log = 'C:\Temp\hz_1104_finalize.log'
$env:PYTHONIOENCODING = 'utf-8'
& $Py -X utf8 (Join-Path $Root 'tools\huangzhong_1104_finalize.py') --json *>> $Log
exit $LASTEXITCODE