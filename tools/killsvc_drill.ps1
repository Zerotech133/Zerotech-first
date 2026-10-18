#requires -Version 5.1
<#
.SYNOPSIS
    Hermes8787-KillSvc Gate 0 演练脚本（dry-run 语义，零注册零提权）。

.DESCRIPTION
    设计定稿：docs/runbooks/2026-10-18_UAC提权操作盘点与高权限自愈方案.md（§4.2 演练形态）

    本脚本做四件事，全程 dry-run：
      [1] 起一个诱饵监听进程（python 绑 18787 端口，进程名/命令行/端口与 8787 特征错开，
          绝不使用 8787 端口、绝不使用 server.py 命令行）；
      [2] 用 tools/killsvc_guard.py 的 verdict 做反向断言：
          - 诱饵（真实探测其身份后构造 dict）必须被拒（W2 三重核对不过）；
          - 真 8787 特征样本（纯构造 dict，非真探测）必须被放行（allow）；
      [3] 收尾清理：只杀自己起的诱饵进程（PID 全程变量持有，绝不用名字通配杀）；
      [4] 自证零接触：实跑前后真 8787 监听 PID 不变（只读 Get-NetTCPConnection，不碰进程）。

    全程不 Stop-Process / taskkill 任何真实进程；唯一的 Stop-Process 目标是
    本脚本自己拉起的诱饵 PID（$decoy.Id）。

    ============================================================
    Gate 0 后接入真 KillSvc 时的验收步骤（§4.2 原文摘录，届时另立脚本执行，
    本 dry-run 脚本不做其中任何一步）：
      1. 准备诱饵僵死进程（真 8787 优雅停掉后，用同 server.py 路径起一个
         只绑端口不响应 HTTP 的最小仿制品，保证诱饵 CommandLine 含 server.py）；
      2. 非提权普通 PowerShell 里 Start-ScheduledTask -TaskName 'Hermes8787-KillSvc'；
      3. 断言：全程无 UAC 弹窗；C:\aios\Hermes\kill_svc.log 出现
         found PID -> identity OK -> killed rc=0 -> port freed；
         30s 内 Start-ScheduledTask HermesWebUI-Guard30 拉活且 /api/observe/state 200；
      4. 反向断言（验收③）：诱饵换成 CommandLine 不含 server.py 的监听
         （如 python -m http.server 8787），重复步骤 2——日志应出现
         identity mismatch, refuse to kill，端口占用原样保留；
      5. 恢复现场：Start-ScheduledTask Guard30 拉回真服务，核验 console 卡片可见。
    ============================================================
#>

$ErrorActionPreference = 'Stop'

# 与子进程（python helper 输出 UTF-8）对齐，避免 GBK 控制台把 UTF-8 误读成乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$here = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$py = 'C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe'
if (-not (Test-Path $py)) { throw "python 不存在: $py" }

$results = New-Object System.Collections.Generic.List[string]
$failCount = 0

function Add-Case {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    if ($Passed) {
        $script:results.Add(("PASS  {0}  {1}" -f $Name, $Detail))
    } else {
        $script:results.Add(("FAIL  {0}  {1}" -f $Name, $Detail))
        $script:failCount++
    }
    Write-Output $script:results[$script:results.Count - 1]
}

# ---------------------------------------------------------------------------
# 自证前置：实跑前记录真 8787 监听 PID（只读，不碰进程）
# ---------------------------------------------------------------------------
$before8787 = (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess
Write-Output ("[prep] 实跑前 8787 监听 PID = {0}（只读观测）" -f $(if ($before8787) { $before8787 } else { '<无监听>' }))

# ---------------------------------------------------------------------------
# guard 调用助手：把 request JSON 写临时文件，调 killsvc_guard.check 取 verdict
# ---------------------------------------------------------------------------
$guardHelper = Join-Path $env:TEMP 'killsvc_drill_guard_helper.py'
Set-Content -Path $guardHelper -Encoding UTF8 -Value @'
import sys, json, time
sys.path.insert(0, sys.argv[2])
import killsvc_guard as g
with open(sys.argv[1], encoding="utf-8-sig") as f:
    req = json.load(f)
now = req.pop("_now", None) or time.time()
last = req.pop("_last_trigger_ts", None)
seen = req.pop("_seen_nonces", None) or []
v = g.check(req, now=now, last_trigger_ts=last, seen_nonces=seen)
print(json.dumps({"allow": v.allow, "rule": v.rule, "reason": v.reason}, ensure_ascii=True))
'@

function Invoke-Guard {
    param([hashtable]$Request)
    $reqFile = Join-Path $env:TEMP 'killsvc_drill_req.json'
    $Request | ConvertTo-Json -Depth 5 | Set-Content -Path $reqFile -Encoding UTF8
    $out = & $py $guardHelper $reqFile $here 2>&1
    if ($LASTEXITCODE -ne 0) { throw "guard helper 失败: $out" }
    return ($out | Select-Object -Last 1 | ConvertFrom-Json)
}

# ---------------------------------------------------------------------------
# [1] 起诱饵监听进程：绑 18787（非 8787），命令行不含 server.py
# ---------------------------------------------------------------------------
$decoyPy = Join-Path $env:TEMP 'killsvc_drill_decoy.py'
Set-Content -Path $decoyPy -Encoding ASCII -Value @'
import socket, time
s = socket.socket()
s.bind(("127.0.0.1", 18787))
s.listen(1)
time.sleep(120)
'@

$decoy = $null
try {
    $decoy = Start-Process -FilePath $py -ArgumentList ('"{0}"' -f $decoyPy) -PassThru -WindowStyle Hidden
    Write-Output ("[decoy] 诱饵监听进程已起 PID={0} 端口=18787" -f $decoy.Id)

    $ready = $false
    foreach ($i in 1..50) {
        Start-Sleep -Milliseconds 200
        if (Get-NetTCPConnection -LocalPort 18787 -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
    }
    Add-Case 'DECOY_UP' $ready ("诱饵 18787 监听就绪（PID={0}）" -f $decoy.Id)

    # -----------------------------------------------------------------------
    # [2a] 反向断言：诱饵（真实读其身份后构造 dict）必须被 W2 拒杀
    #      （§4.2 步骤4 反向断言的 dry-run 形态：identity mismatch, refuse to kill）
    # -----------------------------------------------------------------------
    $ci = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $decoy.Id)
    $decoyRequest = @{
        requester      = 'guard30_chain'
        nonce          = ('drill-{0}-{1}' -f $PID, [DateTimeOffset]::Now.ToUnixTimeMilliseconds())
        issued_at      = [DateTimeOffset]::Now.ToUnixTimeSeconds()
        target         = @{
            pid          = $decoy.Id
            process_name = $ci.Name
            command_line = $ci.CommandLine
            session_id   = $ci.SessionId
            owner        = "$env:COMPUTERNAME\$env:USERNAME"
            port         = 18787
            listening    = $true
            probe_failures = 3
        }
        reason         = 'drill_decoy_must_be_refused'
        audit_log_path = 'C:\aios\Hermes\kill_svc.log'
    }
    $vDecoy = Invoke-Guard -Request $decoyRequest
    Add-Case 'DECOY_DENIED' ((-not $vDecoy.allow) -and $vDecoy.rule -eq 'W2') `
        ("诱饵被拒：rule={0} reason={1}" -f $vDecoy.rule, $vDecoy.reason)

    # -----------------------------------------------------------------------
    # [2b] 正向断言：真 8787 特征样本（纯构造 dict，非真探测）被放行
    # -----------------------------------------------------------------------
    $realRequest = @{
        requester      = 'n1_selfheal'
        nonce          = ('drill-positive-{0}-{1}' -f $PID, [DateTimeOffset]::Now.ToUnixTimeMilliseconds())
        issued_at      = [DateTimeOffset]::Now.ToUnixTimeSeconds()
        target         = @{
            pid          = 49536
            process_name = 'pythonw.exe'
            command_line = 'pythonw.exe C:\项目\老田的AI助手\Hermes\hermes-webui-next\server.py --port 8787'
            session_id   = 0
            owner        = 'SYSTEM'
            port         = 8787
            listening    = $true
            probe_failures = 3
        }
        reason         = 'state_b_http_probe_3_fail'
        audit_log_path = 'C:\aios\Hermes\kill_svc.log'
    }
    $vReal = Invoke-Guard -Request $realRequest
    Add-Case 'REAL_SIGNATURE_ALLOWED' ($vReal.allow) `
        ("真 8787 特征构造样本放行：rule={0}" -f $vReal.rule)

    # -----------------------------------------------------------------------
    # [2c] 反向断言补强：冷却窗内重复触发必须被 W3 拒（§3.2.4 防抖）
    # -----------------------------------------------------------------------
    $replayRequest = @{
        requester      = 'n1_selfheal'
        nonce          = 'drill-replay-001'
        issued_at      = [DateTimeOffset]::Now.ToUnixTimeSeconds()
        target         = $realRequest.target
        reason         = 'state_b_retry_within_cooldown'
        audit_log_path = 'C:\aios\Hermes\kill_svc.log'
        _last_trigger_ts = [DateTimeOffset]::Now.ToUnixTimeSeconds() - 60
    }
    $vReplay = Invoke-Guard -Request $replayRequest
    Add-Case 'COOLDOWN_DENIED' ((-not $vReplay.allow) -and $vReplay.rule -eq 'W3') `
        ("冷却窗内重复触发被拒：rule={0}" -f $vReplay.rule)
}
finally {
    # -----------------------------------------------------------------------
    # [3] 收尾清理：只杀自己起的诱饵（PID 变量全程持有，绝不按名字通配杀）
    # -----------------------------------------------------------------------
    if ($decoy -and -not $decoy.HasExited) {
        Stop-Process -Id $decoy.Id -Force -ErrorAction SilentlyContinue
        Write-Output ("[cleanup] 诱饵 PID={0} 已清理（仅此一个目标）" -f $decoy.Id)
    }
    Remove-Item -Path $decoyPy, $guardHelper -ErrorAction SilentlyContinue -Force
    Remove-Item -Path (Join-Path $env:TEMP 'killsvc_drill_req.json') -ErrorAction SilentlyContinue -Force
}

$after8787 = (Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess

# ---------------------------------------------------------------------------
# [4] 零接触自证 + 清理核验
# ---------------------------------------------------------------------------
Add-Case 'REAL_8787_UNTOUCHED' ($before8787 -eq $after8787) `
    ("实跑前后 8787 监听 PID 不变（before={0} after={1}），全程零接触" -f `
        $(if ($before8787) { $before8787 } else { '<无监听>' }), `
        $(if ($after8787) { $after8787 } else { '<无监听>' }))

$decoyFreed = -not (Get-NetTCPConnection -LocalPort 18787 -State Listen -ErrorAction SilentlyContinue)
Add-Case 'DECOY_CLEANED' $decoyFreed '诱饵端口 18787 已释放，现场无残留'

Write-Output ('-' * 72)
Write-Output ("drill 汇总: {0} PASS / {1} FAIL" -f ($results.Count - $failCount), $failCount)
if ($failCount -gt 0) { exit 1 }
exit 0
