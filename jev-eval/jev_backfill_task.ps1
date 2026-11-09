# VERSION: jev_backfill_task v2 (2026-10-11, traceback-full-log fix)
$ErrorActionPreference = 'Stop'
# 2026-10-16 审计修复：C:\ 根日志 15:00 起被拒写（Access denied，ACL 却允许，
# 疑受控文件夹访问拦截）→ 16:00 后每轮 rc=1 且零日志、backlog 停摆 5h。
# 迁至项目内 review_data（S4U 任务已验证可写），并保留旧日志搬运。
$log = Join-Path $PSScriptRoot 'review_data\jev_backfill_task.log'
if (-not (Test-Path -LiteralPath $log) -and (Test-Path -LiteralPath 'C:\jev_backfill_task.log')) {
    try { Copy-Item 'C:\jev_backfill_task.log' $log -ErrorAction Stop } catch {}
}
function W([string]$m) {
    ("[{0}] {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}
try {
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
    W 'P0 start'
    # 2026-10-30 PREFILTERON (user approved): safe prefilter for hourly rounds.
    $env:JEV_PREFILTER = 'safe'
    # 2026-10-27 JEVHOURLY: night-window guard. During 21:50-07:00 the night run owns
    # the shared ollama_batch lock; the :00 hourly run was contending and cost night
    # round R16 a skipped round (2026-10-27 04:07-04:22). Pause flag == night window.
    if (Test-Path 'C:\Temp\k266_m3.pause') {
        W 'P0 night window: skip hourly (night owns ollama_batch lock)'
        exit 0
    }
    # locate Chinese-path news_collector via marker file (ASCII cmdline rule)
    $dir = $null
    foreach ($drv in @('C:\', 'E:\', 'D:\')) {  # 2026-10-16 换机：C 盘优先
        if (-not (Test-Path -LiteralPath $drv)) { continue }
        $hit = Get-ChildItem -LiteralPath $drv -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                $sub = Join-Path $_.FullName 'news_collector'
                if (Test-Path -LiteralPath (Join-Path $sub 'jev_backfill.py')) { $sub }
            } | Select-Object -First 1
        if ($hit) { $dir = $hit; break }
    }
    # 换机重建（2026-10-16）：junction 直接命中（C:\aios -> C:\项目\老田的AI助手）
    if (-not $dir -and (Test-Path -LiteralPath 'C:\aios\news_collector\jev_backfill.py')) {
        $dir = 'C:\aios\news_collector'
    }
    if (-not $dir) { throw 'news_collector dir not found on E:/D:/C:' }
    W ('P1 dir=' + $dir)
    # python resolution
    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }
    # bounded batch; Ollama down -> judged=0, harmless idle (exit 0)
    # v2 fix(2026-10-11): Stop 偏好下 stderr 任意一行会变 ErrorRecord 直接 throw,
    #   traceback 只剩首行——native 段局部改 Continue, 全文由 P2 落盘
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $dir 'jev_backfill.py') --batch 0 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    W ('P2 exit=' + $code + ' out=' + ($out -join ' | '))
    # 2026-10-30 JEVALERTB (user approved B): JEV risk bridge -> LOCAL OUTBOX ONLY.
    # High-impact bear events (last 2h, conf>=0.8) appended to risk_alerts.jsonl; 2026-11-08 老田批准 S3:加 --notify 红即到人(seen 去重+hub 4h 指纹).
    $riskPy = Join-Path (Split-Path $dir -Parent) 'tools\jev_risk_alert.py'
    if (-not (Test-Path -LiteralPath $riskPy)) { $riskPy = 'C:\aios\tools\jev_risk_alert.py' }
    if (Test-Path -LiteralPath $riskPy) {
        $ErrorActionPreference = 'Continue'
        $riskOut = & $py -X utf8 -B $riskPy --hours 2 --emit-outbox --notify 2>&1
        $riskLine = ($riskOut | Select-Object -Last 1)
        if ($null -eq $riskLine) { $riskLine = '' }
        $riskLine = ([string]$riskLine).Substring(0, [Math]::Min(220, ([string]$riskLine).Length))
        W ('P2.5 risk outbox exit=' + $LASTEXITCODE + ' out=' + $riskLine)
        $ErrorActionPreference = 'Stop'
    } else {
        W ('P2.5 risk SKIP: not found ' + $riskPy)
    }
    # log cap ~400KB: keep tail
    if ((Get-Item -LiteralPath $log).Length -gt 400KB) {
        $tail = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8
        Set-Content -LiteralPath $log -Value $tail -Encoding UTF8
        W 'P3 log rotated'
    }
    exit $code
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}

