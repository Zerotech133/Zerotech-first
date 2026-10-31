# D1 outcome 每日回填/预检（17:10，L2 五要素见 docs/audit/2026-10-31_D1扩样_L2包_v1.md）
# 说明：只在收盘后跑（17:10），默认取价+写账本；额外参数用 env D1_OUTCOME_ARGS 注入
#      （例如测试时 D1_OUTCOME_ARGS='--no-fetch --no-write'，不写生产账本）。
$ErrorActionPreference = 'Continue'
$repo = 'C:\aios'
$log = 'C:\Temp\d1_outcome_daily.log'
$py = 'C:\Program Files\Python311\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
function Write-LogLine([string]$line) {
    $line | Out-File -LiteralPath $log -Append -Encoding utf8
}
$stamp = { (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') }
Write-LogLine ("[{0}] start d1 outcome daily" -f (& $stamp))
Push-Location -LiteralPath $repo
try {
    $extra = @()
    if ($env:D1_OUTCOME_ARGS) { $extra = @($env:D1_OUTCOME_ARGS -split '\s+' | Where-Object { $_ }) }
    $argv = @('-X', 'utf8', 'tools\d1_outcome_daily.py', '--json') + $extra
    $out1 = (& $py @argv 2>&1 | Out-String)
    $rc1 = $LASTEXITCODE
    Write-LogLine $out1.TrimEnd()
    Write-LogLine ("[{0}] d1_outcome_daily rc={1}" -f (& $stamp), $rc1)
    $out2 = (& $py -X utf8 'tools\d1_calibration.py' --json 2>&1 | Out-String)
    $rc2 = $LASTEXITCODE
    Write-LogLine $out2.TrimEnd()
    Write-LogLine ("[{0}] d1_calibration rc={1}" -f (& $stamp), $rc2)
    if ($rc1 -ne 0 -or $rc2 -ne 0) { exit 1 }
    exit 0
}
finally {
    Pop-Location
}
