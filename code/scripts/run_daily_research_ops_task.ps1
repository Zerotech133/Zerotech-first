$env:ZHICE_JOB_SOURCE = 'schedule'
$env:ZHICE_JOB_SCHEDULED_AT = (Get-Date -Format 'o')
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot 'run_daily_research_ops.py'
& $py $script
$rc = $LASTEXITCODE

# ---- Wrapper 尾部脚本：失败不再只 warning ----
# 主 16 步由 run_daily_research_ops.py 负责；这里只记录尾部 3 个脚本的 rc/stderr：
#   - 失败时把 [{name, rc, stderr}] 交给 Python helper，追加 failed step 到 job status，
#     status=partial_failure，并写 C:\Temp\zhice_daily_task_alert.json；
#   - 有尾部失败时 PS1 以非 0 退出（Task Scheduler 可见失败），不再吞掉 rc；
#   - 主链失败（$rc -ne 0）时保持原行为：不跑尾部，直接按主 rc 退出。
$tailFailures = New-Object System.Collections.ArrayList

function Invoke-TailScript {
    param(
        [string]$Name,
        [string]$ScriptPath,
        [string[]]$ScriptArgs = @()
    )
    $errFile = Join-Path $env:TEMP ("zhice_tail_{0}.stderr.log" -f ([guid]::NewGuid().ToString('N')))
    try {
        & $py $ScriptPath @ScriptArgs 2> $errFile
        $tailRc = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 1 }
        $stderr = ""
        if (Test-Path -LiteralPath $errFile) {
            $stderr = Get-Content -LiteralPath $errFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($null -eq $stderr) { $stderr = "" }
        }
        if ($tailRc -ne 0) {
            [void]$tailFailures.Add([pscustomobject]@{
                name = $Name
                rc = $tailRc
                stderr = [string]$stderr
            })
            Write-Warning "$Name failed rc=$tailRc (recorded to job status/alert)"
        }
    }
    finally {
        Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
    }
}

if ($rc -eq 0) {
    Invoke-TailScript -Name "acad1_rationale.py" -ScriptPath (Join-Path $PSScriptRoot "acad1_rationale.py") -ScriptArgs @("--write")
    Invoke-TailScript -Name "agent_autopilot.py" -ScriptPath (Join-Path $PSScriptRoot "agent_autopilot.py") -ScriptArgs @("--write")
    # 自动处理可分析待办；人工事项保持 pending，不代填。
    Invoke-TailScript -Name "agent_worker.py" -ScriptPath (Join-Path $PSScriptRoot "agent_worker.py") -ScriptArgs @("--once")

    if ($tailFailures.Count -gt 0) {
        $tailJson = Join-Path $env:TEMP ("zhice_tail_result_{0}.json" -f $PID)
        try {
            $tailPayload = @($tailFailures | ForEach-Object {
                [pscustomobject]@{ name = $_.name; rc = $_.rc; stderr = $_.stderr }
            })
            Set-Content -LiteralPath $tailJson -Encoding UTF8 -Value (
                ConvertTo-Json -InputObject $tailPayload -Depth 5)
            & $py $script --record-tail-file $tailJson
            $recordRc = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 1 }
            if ($recordRc -ne 0) {
                Write-Warning "record tail result failed rc=$recordRc (job status/alert may be incomplete)"
            }
        }
        finally {
            Remove-Item -LiteralPath $tailJson -Force -ErrorAction SilentlyContinue
        }
        $rc = 1
    }
}

exit $rc
