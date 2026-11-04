# LingJie A0 daily board run - 07:50 wrapper (ASCII only)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Py = 'C:\Program Files\Python311\python.exe'
$Date = Get-Date -Format 'yyyy-MM-dd'
$Log = 'C:\Temp\hq_daily_run.log'
$BriefDir = Join-Path $Root 'deliverables\data_briefs'
$Brief = Join-Path $BriefDir ($Date + '_data_brief.json')
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Path $BriefDir -Force | Out-Null

# 1) 先产数据简报；失败不挡跑板，但会留日志
& $Py -X utf8 (Join-Path $Root 'tools\datawarehouse\hq_brief.py') --json --out $Brief *>> $Log

# 2) 17 席跑板；有简报就注入，没有就按原逻辑
if (Test-Path $Brief) {
    & $Py -X utf8 (Join-Path $Root 'tools\hq_daily_run.py') --data-brief $Brief --notify-inbox *>> $Log
} else {
    & $Py -X utf8 (Join-Path $Root 'tools\hq_daily_run.py') *>> $Log
}
exit $LASTEXITCODE
