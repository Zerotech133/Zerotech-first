# 盯盘 runner 常驻循环（P0.3-C，paper-only，不执行/不下单）
# 由用户启动文件夹的 WatchEngineLoop.lnk 在登录时隐藏启动。
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$python = "C:\Program Files\Python311\python.exe"
$data = Join-Path $root "agent_platform\data"
$engineDir = Join-Path $data "watch_engine"
New-Item -ItemType Directory -Force -Path $engineDir | Out-Null

$planLedger = Join-Path $data "watch_plans.jsonl"
$handoffLedger = Join-Path $data "d1_handoffs.jsonl"
$triggerLedger = Join-Path $data "watch_triggers.jsonl"
$alertLedger = Join-Path $data "watch_alerts.jsonl"
$statusPath = Join-Path $engineDir "status.json"
$autoarmStatus = Join-Path $engineDir "autoarm_status.json"
$guardAlert = Join-Path $engineDir "guard_alerts.jsonl"
$botDir = Join-Path $data "trading_bot"
$log = "C:\Temp\watch_engine_loop.log"
$lastObserve = Get-Date

$env:WATCH_ENGINE_STATUS_PATH = $statusPath
$env:WATCH_ALERT_LEDGER_PATH = $alertLedger
$env:WATCH_TRIGGER_LEDGER_PATH = $triggerLedger

function In-Trading-Window {
    $now = Get-Date
    if ($now.DayOfWeek -in @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)) { return $false }
    $t = $now.TimeOfDay
    return ($t -ge [TimeSpan]::Parse("09:25:00")) -and ($t -le [TimeSpan]::Parse("15:05:00"))
}

while ($true) {
    try {
        if (((Get-Date) - $lastObserve).TotalMinutes -ge 10) {
            & $python -X utf8 tools\watch_24h_probe.py --json *>> $log
            $lastObserve = Get-Date
        }
        if (In-Trading-Window) {
            $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
            "[$stamp] tick" | Out-File -FilePath $log -Append -Encoding utf8
            if (Test-Path $handoffLedger) {
                & $python -X utf8 -m agent_platform.watch_plan_autoarm `
                    --once `
                    --ledger $handoffLedger `
                    --plans $planLedger `
                    --status $autoarmStatus `
                    --json *>> $log
            }
            if (Test-Path $planLedger) {
                & $python -X utf8 tools\watch_guard.py `
                    --status $statusPath `
                    --expire-due `
                    --plan-ledger $planLedger `
                    --alert-file $guardAlert `
                    --json *>> $log
            }
            & $python -X utf8 -m agent_platform.watch_runner `
                --once `
                --plan-ledger $planLedger `
                --trigger-ledger $triggerLedger `
                --alert-ledger $alertLedger `
                --alert-feishu-target "quant_group" `
                --data-dir $botDir `
                --quote-source snapshot `
                --status $statusPath `
                --json *>> $log
            & $python -X utf8 tools\watch_guard.py `
                --status $statusPath `
                --alert-file $guardAlert `
                --json *>> $log
        }
    } catch {
        "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] runner_error=$($_.Exception.Message)" |
            Out-File -FilePath $log -Append -Encoding utf8
    }
    Start-Sleep -Seconds 60
}
