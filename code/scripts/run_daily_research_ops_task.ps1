$env:ZHICE_JOB_SOURCE = 'schedule'
$env:ZHICE_JOB_SCHEDULED_AT = (Get-Date -Format 'o')
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot 'run_daily_research_ops.py'
$env:PYTHONPATH = (Join-Path $root 'src')

# ── 2026-11-16 PG DSN 修复 ──────────────────────────────────────────────
# 修复点在 python 侧：run_daily_research_ops.py 的 _bootstrap_pg_dsn() 从
# jev_common.DB_DSN（32 位口令，与 8091 同源）注入 DB_MAIN_URL。
# 不在 PS 侧注入的原因：PS 5.1 向原生进程传中文路径参数会乱码，
# import jev_common 必失败（已实测）。

# ── 2026-11-16 退出码可靠性修复（只跑一次）──────────────────────────────
# 问题：`& $py $script` 之后 $LASTEXITCODE 可能为 $null（无管道消费时），
#       `exit $null` 被当作 0 —— 失败任务在计划任务里报 LastTaskResult=0，
#       告警与看板全部静默（2026-11-16 03:15 实例）。
# 做法：一律用 Start-Process -Wait -PassThru 取 ExitCode，**只执行一次**；
#       输出重定向到临时文件后回显，保证计划任务日志仍可见。
$tmpOut = Join-Path $env:TEMP ("zhice_daily_{0}.out.log" -f $PID)
$tmpErr = Join-Path $env:TEMP ("zhice_daily_{0}.err.log" -f $PID)
try {
    $proc = Start-Process -FilePath $py -ArgumentList @($script) `
        -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
    $rc = [int]$proc.ExitCode
    Write-Host "[rc] python exit=$rc"
    if (Test-Path -LiteralPath $tmpOut) {
        Get-Content -LiteralPath $tmpOut -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
    if (Test-Path -LiteralPath $tmpErr) {
        Get-Content -LiteralPath $tmpErr -Encoding UTF8 | Where-Object { $_ } |
            ForEach-Object { Write-Warning $_ }
    }
} catch {
    Write-Warning "[rc] start failed: $($_.Exception.Message)"
    $rc = 1
} finally {
    Remove-Item -LiteralPath $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
}

# ---- Wrapper 尾部脚本：失败不再只 warning ----
# 主 17 步由 run_daily_research_ops.py 负责；这里记录尾部 6 个脚本的 rc/stderr。
# 尾部顺序：acad1_rationale → agent_autopilot → agent_worker → build_strategy_market → sync_research_artifacts → seed_strategy_governance。
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
        # 2026-11-16 修复：`& $py ... 2> $errFile` 之后 $LASTEXITCODE 可能为 $null，
        # 原逻辑把 $null 判成 1 —— 造成 6 个尾部脚本"假失败"（实际手动跑 rc=0）。
        # 改用 Start-Process -Wait -PassThru 取真实 ExitCode。
        $tailProc = Start-Process -FilePath $py `
            -ArgumentList (@($ScriptPath) + $ScriptArgs) `
            -WorkingDirectory $root -NoNewWindow -Wait -PassThru `
            -RedirectStandardError $errFile
        $tailRc = [int]$tailProc.ExitCode
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
    # 2026-11-10: 先生成策略市场配置，再生成 artifacts manifest；
    # manifest 必须记录本次 build 后的策略市场 hash，避免先 sync 后 build 造成一轮陈旧。
    # 研究 artifacts / 策略市场 / 治理归属每日刷新；失败只记 tail，不阻塞选股主链
    Invoke-TailScript -Name "build_strategy_market.py" -ScriptPath (Join-Path $PSScriptRoot "build_strategy_market.py")
    Invoke-TailScript -Name "sync_research_artifacts.py" -ScriptPath (Join-Path $PSScriptRoot "sync_research_artifacts.py") -ScriptArgs @("--write")
    Invoke-TailScript -Name "seed_strategy_governance.py" -ScriptPath (Join-Path $PSScriptRoot "seed_strategy_governance.py")

    if ($tailFailures.Count -gt 0) {
        $tailJson = Join-Path $env:TEMP ("zhice_tail_result_{0}.json" -f $PID)
        try {
            $tailPayload = @($tailFailures | ForEach-Object {
                [pscustomobject]@{ name = $_.name; rc = $_.rc; stderr = $_.stderr }
            })
            Set-Content -LiteralPath $tailJson -Encoding UTF8 -Value (
                ConvertTo-Json -InputObject $tailPayload -Depth 5)
            & $py $script --record-tail-file $tailJson
            # 2026-11-16：同一 $LASTEXITCODE 可能为 $null；此步仅记录，缺失按成功处理，
            # 避免把"记录不了"误报成"尾部失败"。
            $recordRc = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
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

# ── 2026-11-16 防假成功闸 ────────────────────────────────────────────────
# 背景：2026-11-16 03:15 日任务 job status=failed/rc=1，但计划任务
#       LastTaskResult=0（rc=0），告警不触发。
# 做法：以 job status 为准做二次判定，status 非 success 时强制非 0 退出。
#       这样即使 python 因任何原因返回 0，包装层也能把失败传出去。
$statusPath = $env:ZHICE_JOB_STATUS_PATH
if (-not $statusPath) { $statusPath = 'C:\Temp\zhice_job_status.json' }
if (Test-Path -LiteralPath $statusPath) {
    try {
        $js = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($js.status -and $js.status -ne 'success' -and $rc -eq 0) {
            Write-Warning "[gate] job status=$($js.status) but rc=0; forcing exit 1"
            $rc = 1
        }
    } catch {
        Write-Warning "[gate] job status unreadable: $($_.Exception.Message)"
    }
} else {
    Write-Warning "[gate] job status file missing: $statusPath"
}

exit $rc
