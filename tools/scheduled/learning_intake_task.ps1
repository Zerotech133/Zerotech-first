# learning_intake_task.ps1 - 学习流水线自动任务（UTF-8 BOM；日志原始字节追加避免乱码）
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$repo = 'C:\aios'
$py = 'C:\项目\py312\python.exe'
$log = 'C:\Temp\zero_learning_intake.log'
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Set-Location $repo
cmd /c ""$py" -X utf8 tools/kb_corpus/learning_intake.py ingest --write >> "$log" 2>&1"
$rc = $LASTEXITCODE
[System.IO.File]::AppendAllText($log, "$ts learning_intake_task.ps1 rc=$rc`r`n", (New-Object System.Text.UTF8Encoding($false)))
exit $rc
