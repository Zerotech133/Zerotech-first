# portal api launcher (ASCII only - SYSTEM-safe path via C:\aios junction)
# K98: Start-Process -PassThru ExitCode always EMPTY here (same fake-green
# family as morning; "python exited code= " x5 today, alert argv collapsed:
# log path shifted into code slot). Use & + $LASTEXITCODE instead.
$log = 'C:\Temp\portal_api_task.log'
# K-RESTART (10-17): sentinel flag bypassing the K234 health-lock for a
# controlled restart. Flag present => kill orphans first, then fall through
# to the normal start branch (probe fails => fresh start with current code).
# One-shot: flag is consumed by the next script run.
if (Test-Path 'C:\Temp\PORTAL_API_RESTART.flag') {
    Remove-Item 'C:\Temp\PORTAL_API_RESTART.flag' -Force -ErrorAction SilentlyContinue
    # 2026-10-24 OS-CLOSE-INFRA1: S4U/Session0 CIM CommandLine is null; old filter
    # missed the live PID so the health lock skipped restart. Kill by listen port first.
    $killed = @()
    try {
        Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction Stop |
            ForEach-Object {
                $p = $_.OwningProcess
                if ($p -and $p -ne $PID) {
                    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
                    $killed += $p
                }
            }
    } catch { }
    Get-CimInstance -Query "SELECT ProcessId FROM Win32_Process WHERE Name = 'python.exe' AND CommandLine LIKE '%portal_api%'" |
        ForEach-Object {
            $p = $_.ProcessId
            if ($p -and $p -notin $killed) {
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
                $killed += $p
            }
        }
    ('restart-flag: killed [' + (($killed | Sort-Object -Unique) -join ',') + '] ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    Start-Sleep -Seconds 1
}
# K234 idempotent lock: if 8091 already serves /health, SKIP entirely.
# Root cause of the 18:0x kill loop (6x code=-1 in 18min): every trigger of
# this script (DockerWatchdog v2.1 health guard / E107 grid fallback / manual)
# unconditionally Stop-Process'd ALL portal_api pythons first - killing the
# LIVE serving instance as an "orphan". Concurrent triggers = bounce war =
# user-facing 502 windows. With the lock, every concurrent trigger is a no-op.
# K234b (10-08): probe up to 3x (3s apart) before declaring death. The
# 6:05-6:12 outage chain started with ONE transient probe failure -> healthy
# instance killed -> new one aborted mid-start (0x800700E0) -> 502 for 7 min.
# Never kill on a single failed probe; only a truly dead service enters the
# kill branch below (and there killing orphans is a safe no-op).
$h = 0
for ($i = 0; $i -lt 3; $i++) {
    try {
        # K234c (10-10): Invoke-WebRequest honors system proxy - local probe
        # went THROUGH 127.0.0.1:54654 -> fake 200 when service dead (skip
        # forever) and fake fail when proxy blips (kill loop / bounce war).
        # Use raw HttpWebRequest with Proxy=$null for a TRUE local probe.
        $req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8091/health')
        $req.Proxy = $null
        $req.Timeout = 5000
        $resp = $req.GetResponse()
        $resp.Close()
        if ($resp.StatusCode -eq 'OK') { $h = 1; break }
    } catch { Start-Sleep -Seconds 3 }
}
if ($h -eq 1) {
    ('skip: 8091 already serving ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    exit 0
}
# K-ORG-155 (10-17): cold-start linkage guard. Parallel portal rework can
# leave the tree with broken imports (api_misc deleted / job_spec_registry
# missing / chanlun_data removed -> cold start crashes the whole portal
# line at boot). Before killing orphans and starting, run an import smoke
# of portal_api; a broken tree => refuse to start, alert, exit 1. Re-run
# when the tree is healthy. Restart-flag path also passes through here.
$smoke = (& 'C:\Program Files\Python311\python.exe' -X utf8 -c "import sys; sys.path.insert(0, r'C:\aios\portal'); import portal_api" 2>&1 | Out-String).Trim()
$smoke_rc = $LASTEXITCODE
if ($smoke_rc -ne 0) {
    ('cold-start guard BLOCKED: portal_api import failed rc=' + $smoke_rc + ' ' + (Get-Date -Format s)) |
        Out-File $log -Append -Encoding utf8
    $smoke | Out-File 'C:\Temp\portal_api_err.log' -Append -Encoding utf8
    & 'C:\Program Files\Python311\python.exe' -X utf8 C:\aios\news_collector\alert_fail_wrap.py portal_api $smoke_rc C:\Temp\portal_api_err.log 1>$null 2>$null
    exit 1
}
('cold-start guard OK: portal_api import smoke passed ' + (Get-Date -Format s)) |
    Out-File $log -Append -Encoding utf8
try {
    # task Stop cannot kill the child; clear orphans first
    # K186: WQL server-side filter (was full Win32Process enum - K132 hang root)
    Get-CimInstance -Query "SELECT ProcessId FROM Win32_Process WHERE Name = 'python.exe' AND CommandLine LIKE '%portal_api%'" |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 1
    ('starting ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    # P1-2 (2026-10-27): 启用 BM25 RAG（可在 zhuge_grounding 内回退关键词；去掉此行即回退）
    $env:ZHUGE_RAG = '1'
    # W27 (2026-11-04): 生产 kb_qa 生成端走 llm_router（v4d→v2→ollama）；
    # 2026-11-05 00:30 回滚到本地 ollama 做 multi 答案质量对比（L2 已批准）：
    # 恢复 router 时把下方值改回 'router' 并重启；审批见 docs/ops/2026-11-04_W27_kb_qa_router_L2审批单.md
    $env:KB_QA_LLM_PROVIDER = 'ollama'
    # K382 (10-14): append-mode for out/err - overwrite mode erased every bounce
    # scene (08:25/09:24 code=1 root cause unreadable). Session separator first.
    ('===== session start ' + (Get-Date -Format s) + ' =====') |
        Out-File 'C:\Temp\portal_api_err.log' -Append -Encoding utf8
    # ★2026-11-10 根治：Start-Process 不带 -Wait — portal_api 是常驻进程不会退出，
    # -Wait 会卡死直到 ExecutionTimeLimit 杀进程 → rc=2147946720 (SHARING_VIOLATION)。
    # 改为：启动后最多 15s 探测 health endpoint，起来 exit 0；没起来 exit 1。
    $pyProc = Start-Process -FilePath 'C:\Program Files\Python311\python.exe' `
        -ArgumentList @('C:\aios\portal\portal_api.py') `
        -WindowStyle Hidden `
        -RedirectStandardOutput 'C:\Temp\portal_api_out.log' `
        -RedirectStandardError 'C:\Temp\portal_api_err.log' `
        -PassThru
    $healthy = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 1000
        try {
            $r = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8091/health')
            $r.Proxy = $null; $r.Timeout = 2500
            $resp = $r.GetResponse()
            $resp.Close()
            $healthy = $true; break
        } catch { }
    }
    if ($healthy) {
        ('started OK PID=' + $pyProc.Id + ' health=up ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        exit 0
    }
    ('started but health probe failed PID=' + $pyProc.Id + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 1
} catch {
    ('ERR ' + $_.Exception.Message) | Out-File $log -Append -Encoding utf8
    exit 1
}
