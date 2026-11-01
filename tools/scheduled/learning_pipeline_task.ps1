# learning_pipeline_task.ps1 - 学习流水线自动任务（UTF-8 BOM）
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$repo = 'C:\aios'
$py = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_learning_pipeline.log'
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Set-Location $repo
& $py -X utf8 tools/kb_corpus/learning_ops.py pipeline --write *>> $log
$rc = $LASTEXITCODE
"$ts learning_pipeline_task.ps1 rc=$rc" | Out-File -FilePath $log -Encoding utf8 -Append
exit $rc
