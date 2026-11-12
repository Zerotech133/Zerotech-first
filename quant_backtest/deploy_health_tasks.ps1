# Register / repair the data-health tasks (ASCII only, idempotent).
#
# 2026-11-12: registered after tdx_sector_daily went dead for 25 days with every
# existing check green. The two tasks are a pair and must stay in this order:
#
#   ContractHealth  19:10  -> writes reports/contract_health.json (the verdict)
#   HealAgent       19:20  -> acts on that verdict, whitelist actions only
#
# 19:10 sits 40 min after PoolDailyPipeline (18:30, time limit PT30M) so the two
# never fight over the same tables. 19:20 is long past the 15:30 close window;
# heal_agent refuses to write anything intraday by itself (a mid-session resync
# pulls a half-day bar, and the post-close sync appends a second copy of the day).
#
# Re-run this any time: -Force overwrites the existing registration.
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy_health_tasks.ps1
#
# Exit code: 0 = both registered, 1 = something threw.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$wd = 'C:\aios\quant_backtest'
$ps = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

$specs = @(
    @{ Name = 'ContractHealth'; At = '19:10'; Script = 'run_contract_health.ps1'
       Limit = (New-TimeSpan -Minutes 30)
       Desc  = 'quant_backtest data contract health check (daily 19:10)' },
    @{ Name = 'HealAgent'; At = '19:20'; Script = 'run_heal_agent.ps1'
       Limit = (New-TimeSpan -Hours 2)
       Desc  = 'quant_backtest self-heal, whitelist actions only (daily 19:20 --apply)' }
)

try {
    if (-not (Test-Path $wd)) { throw "missing $wd" }
    $prin = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    foreach ($s in $specs) {
        $script = Join-Path $wd $s.Script
        if (-not (Test-Path $script)) { throw "missing $script" }
        $act = New-ScheduledTaskAction -Execute $ps `
            -Argument ('-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $script) `
            -WorkingDirectory $wd
        $trg = New-ScheduledTaskTrigger -Daily -At $s.At
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit $s.Limit
        Register-ScheduledTask -TaskName $s.Name -Action $act -Trigger $trg -Settings $set `
            -Principal $prin -Description $s.Desc -Force | Out-Null
        $t = Get-ScheduledTask -TaskName $s.Name
        ('{0,-16} {1}  next={2}' -f $s.Name, $t.State,
            (Get-ScheduledTaskInfo -TaskName $s.Name).NextRunTime) | Write-Output
    }
    exit 0
} catch {
    ('deploy_health_tasks ERR ' + $_.Exception.Message) | Write-Output
    exit 1
}
