# contract_health launcher (ASCII only, no Chinese literals on purpose:
# PowerShell 5.1 mangles non-ASCII paths, see 2026-11-12 case).
# Task: ContractHealth, daily 19:10 -- after PoolDailyPipeline (18:30, limit PT30M)
#       so it never competes with the report pipeline over the same tables.
#
# Exit code semantics: 0 = the check ran to completion. A red verdict
# (dead / absent / shrink / contract_bug) is a BUSINESS result written to JSON +
# the report body, NOT to $LASTEXITCODE. Business code in the exit code is what
# turned "ran fine, has warnings" into "task failed" before.
#
# 2026-11-12: do NOT use Start-Process here. This box has both HTTPS_PROXY and
# https_proxy in the environment; PS 5.1's Start-Process builds its env dictionary
# case-sensitively and dies with "key already added: https_proxy". Calling the
# exe directly with redirection is immune, and the hidden console of the parent
# powershell.exe keeps the window from popping up.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
[Environment]::SetEnvironmentVariable('NO_PROXY',
    'rookie.xtcloud-tech.com,127.0.0.1,localhost', 'Process')

$stamp = Get-Date -Format 'yyyyMMdd'
$log = 'C:\Temp\contract_health.log'
$outFile = "C:\Temp\contract_health_out_$stamp.log"
$errFile = "C:\Temp\contract_health_err_$stamp.log"
$py = 'C:\Program Files\Python311\python.exe'

try {
    $root = $null
    if (Test-Path 'C:\aios\quant_backtest\contract_health.py') { $root = 'C:\aios' }
    if (-not $root) { throw 'workspace root not found: C:\aios\quant_backtest' }
    $wd = Join-Path $root 'quant_backtest'
    if (-not (Test-Path 'C:\Temp')) { New-Item -ItemType Directory -Path 'C:\Temp' -Force | Out-Null }
    Set-Location $wd

    $body = & $py -X utf8 'contract_health.py' 2>&1 | Out-String
    $rc = $LASTEXITCODE
    if ($null -eq $rc) { $rc = 1 }
    $body | Set-Content -LiteralPath $outFile -Encoding UTF8
    # 错误流已并入 $body，err 文件保留占位，告警脚本仍能拿到路径
    if ($rc -ne 0) { $body | Set-Content -LiteralPath $errFile -Encoding UTF8 }

    $verdict = ''
    $json = Join-Path $wd 'reports\contract_health.json'
    if (Test-Path $json) {
        try {
            $h = Get-Content -LiteralPath $json -Raw -Encoding UTF8 | ConvertFrom-Json
            $verdict = ' counts=' + ($h.counts | ConvertTo-Json -Compress) +
                       ' base=' + $h.base_asof + ' elapsed=' + $h.elapsed_s + 's'
        } catch { $verdict = ' json_unreadable' }
    } else {
        $verdict = ' json_missing'
    }

    ('contract_health rc=' + $rc + ' ' + (Get-Date -Format s) + $verdict) |
        Out-File $log -Append -Encoding utf8
    if ($rc -ne 0) {
        & $py -X utf8 (Join-Path $wd 'alert_fail.py') contract_health $rc $errFile 1>$null 2>$null
    }
    exit $rc
} catch {
    ('contract_health ERR ' + $_.Exception.Message + ' ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    exit 1
}
