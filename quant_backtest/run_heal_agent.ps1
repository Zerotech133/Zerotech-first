# heal_agent launcher (ASCII only).
# Task: HealAgent, daily 19:20 --apply -- 10 min after ContractHealth (19:10),
#       70 min after PoolDailyPipeline (18:30), well past the 15:30 close window.
#
# Two things this wrapper does that the python script cannot do for itself:
#   1. freshness gate on the health snapshot. heal_agent acts on
#      reports/contract_health.json; a stale snapshot means acting on yesterday's
#      verdict (repairing tables that are already fine, missing new ones). If it
#      is older than 90 min we refresh it here, so a failure of the 19:10 task
#      does not silently disable healing.
#   2. never let a business verdict become a task failure. heal_agent exits 0 by
#      design; only a crash (import error, missing file) can produce non-zero.
#
# Env note: this box has BOTH HTTPS_PROXY and https_proxy; PS 5.1 Start-Process
# dies on the case-insensitive env copy, so everything here calls the exe
# directly with redirection. See run_contract_health.ps1 for the full story.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
[Environment]::SetEnvironmentVariable('NO_PROXY',
    'rookie.xtcloud-tech.com,127.0.0.1,localhost', 'Process')

$stamp = Get-Date -Format 'yyyyMMdd'
$log = 'C:\Temp\heal_agent.log'
$outFile = "C:\Temp\heal_agent_out_$stamp.log"
$errFile = "C:\Temp\heal_agent_err_$stamp.log"
$py = 'C:\Program Files\Python311\python.exe'

try {
    $root = $null
    if (Test-Path 'C:\aios\quant_backtest\heal_agent.py') { $root = 'C:\aios' }
    if (-not $root) { throw 'workspace root not found: C:\aios\quant_backtest' }
    $wd = Join-Path $root 'quant_backtest'
    if (-not (Test-Path 'C:\Temp')) { New-Item -ItemType Directory -Path 'C:\Temp' -Force | Out-Null }
    Set-Location $wd

    # -- 1. refresh the health snapshot if it is stale ------------------------
    $json = Join-Path $wd 'reports\contract_health.json'
    $needRefresh = $true
    if (Test-Path $json) {
        $ageMin = ((Get-Date) - (Get-Item -LiteralPath $json).LastWriteTime).TotalMinutes
        if ($ageMin -lt 90) { $needRefresh = $false }
    }
    if ($needRefresh) {
        ('heal_agent: health snapshot stale, refreshing first ' + (Get-Date -Format s)) |
            Out-File $log -Append -Encoding utf8
        $hBody = & $py -X utf8 'contract_health.py' 2>&1 | Out-String
        $hBody | Set-Content -LiteralPath "C:\Temp\heal_agent_health_$stamp.log" -Encoding UTF8
        ('heal_agent: health refresh rc=' + $LASTEXITCODE) | Out-File $log -Append -Encoding utf8
    }

    # -- 2. apply ------------------------------------------------------------
    $body = & $py -X utf8 'heal_agent.py' '--apply' 2>&1 | Out-String
    $rc = $LASTEXITCODE
    if ($null -eq $rc) { $rc = 1 }
    $body | Set-Content -LiteralPath $outFile -Encoding UTF8
    if ($rc -ne 0) { $body | Set-Content -LiteralPath $errFile -Encoding UTF8 }

    # -- 3. one summary line: how many actions really ran today --------------
    $applied = 0
    $okCount = 0
    $ledger = Join-Path $wd 'reports\heal_log.jsonl'
    $today = Get-Date -Format 'yyyy-MM-dd'
    if (Test-Path $ledger) {
        foreach ($line in (Get-Content -LiteralPath $ledger -Tail 40 -Encoding UTF8)) {
            if ($line -notmatch '"apply": ?true') { continue }
            if (-not $line.Contains($today)) { continue }
            $applied++
            if ($line -match '"ok": ?true') { $okCount++ }
        }
    }
    ('heal_agent rc=' + $rc + ' applied_today=' + $applied + ' ok=' + $okCount +
     ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8

    if ($rc -ne 0) {
        & $py -X utf8 (Join-Path $wd 'alert_fail.py') heal_agent $rc $errFile 1>$null 2>$null
    }
    exit $rc
} catch {
    ('heal_agent ERR ' + $_.Exception.Message + ' ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    exit 1
}
