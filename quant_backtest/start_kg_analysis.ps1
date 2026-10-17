# start_kg_analysis.ps1 — KG 每日 GDS 分析任务包装（KGAnalysis 07:00）
# 依赖：neo4j-kg 容器内已装 GDS 2.13.2（2026-10-16）；kg_schema 密钥链（.env.local）
$ErrorActionPreference = 'Continue'
$Root = 'C:\aios\quant_backtest'
$Log  = 'C:\Temp\kg_analysis_task.log'
$Out  = 'C:\Temp\kg_analysis_out.log'
$Err  = 'C:\Temp\kg_analysis_err.log'

"=== KGAnalysis start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Add-Content $Log -Encoding UTF8

# 注入 KG 凭据（-Encoding UTF8 防 GBK 吞行）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:KG_NEO4J_PASSWORD = ''
Get-Content "$Root\.env.local" -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^(KG_NEO4J_PASSWORD|KG_NEO4J_USER)=(.*)$') {
        Set-Item -Path ("env:" + $Matches[1]) -Value $Matches[2].Trim()
    }
}

$env:PYTHONPATH = $Root
& "C:\Program Files\Python311\python.exe" -X utf8 (Join-Path $Root 'kg_analysis.py') 1>> $Out 2>> $Err
$rc = $LASTEXITCODE
"exit=$rc $(Get-Date -Format 'HH:mm:ss')" | Add-Content $Log -Encoding UTF8
exit $rc
