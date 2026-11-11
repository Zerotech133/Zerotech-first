# learning_governance_task.ps1 - 学习流水线自动任务（UTF-8 BOM；含 v2 门禁）
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$repo = 'C:\aios'
$py = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\zero_learning_governance.log'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Set-Location $repo
$out1 = & $py -X utf8 tools/kb_corpus/learning_ops.py governance --write 2>&1 | Out-String
$rc1 = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, $out1 + "`r`n", $utf8)
$outg = & $py -X utf8 tools/kb_corpus/knowledge_governance.py --write 2>&1 | Out-String
$rcg = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, $outg + "`r`n", $utf8)
$outga = & $py -X utf8 tools/kb_corpus/knowledge_governance_all.py 2>&1 | Out-String
$rcga = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, $outga + "`r`n", $utf8)
$tsg = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
[System.IO.File]::AppendAllText($log, "$tsg knowledge_governance rc=$rcg rc_all=$rcga`r`n", $utf8)
$out2 = & $py -X utf8 tools/kb_corpus/learning_gate.py check --stage candidates --write 2>&1 | Out-String
$rc2 = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, $out2 + "`r`n", $utf8)
[System.IO.File]::AppendAllText($log, "$ts learning_governance_task.ps1 rc_pipeline=$rc1 rc_gate=$rc2`r`n", $utf8)
if ($rc1 -ne 0) { exit $rc1 }
exit $rc2

