# HZ 1104 runner, one-shot 10:00 (ASCII only)
$Root = Split-Path -Parent $PSScriptRoot
$Py = 'C:\Program Files\Python311\python.exe'
$Log = 'C:\Temp\hz_1104_runner.log'
$env:PYTHONIOENCODING = 'utf-8'
& $Py -X utf8 (Join-Path $Root 'tools\huangzhong_1104_run.py') --json *>> $Log
exit $LASTEXITCODE