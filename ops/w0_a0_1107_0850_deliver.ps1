$ErrorActionPreference = 'Stop'
$Root = 'C:\项目\老田的AI助手'
Set-Location -LiteralPath $Root
& 'C:\Program Files\Python311\python.exe' -X utf8 'tools\w0_a0_1107_0850_deliver.py' *>> 'C:\Temp\w0_a0_1107_0850_deliver.log'
exit $LASTEXITCODE
