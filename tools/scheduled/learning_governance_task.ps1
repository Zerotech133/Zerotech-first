# learning_governance_task.ps1 - 学习流水线自动任务（UTF-8 BOM；含 v2 门禁）
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$repo = 'C:\aios'
$py = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_learning_governance.log'
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Set-Location $repo
cmd /c "\"$py\" -X utf8 tools/kb_corpus/learning_ops.py governance --write >> \"$log\" 2>&1"
$rc1 = $LASTEXITCODE
cmd /c "\"$py\" -X utf8 tools/kb_corpus/learning_gate.py check --stage candidates --write >> \"$log\" 2>&1"
$rc2 = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, "$ts learning_governance_task.ps1 rc_pipeline=$rc1 rc_gate=$rc2`r`n", (New-Object System.Text.UTF8Encoding($false)))
if ($rc1 -ne 0) { exit $rc1 }
exit $rc2
