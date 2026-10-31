# deadline_watch_task.ps1 - DeadlineWatch 计划任务包装器（K-ORG-197 轨道B/W33）
# 由 Windows 计划任务 DeadlineWatch 每日五时点（06:05/08:45/16:45/20:45/22:05）拉起：
# 运行数据到点升级器（python -X utf8 deadline_watchdog.py），输出追加留痕到日志。
#
# 2026-10-31 L2-5（WO-GUANYU-P121-20261031）：
#   ① 退出码语义：python rc=2 = 「有 pending」设计语义（历史 57/57 次），不再当失败；
#      wrapper 对 rc=2 记 PENDING 行后 exit 0；pending 明细仍由 deadline_watchdog.py
#      写 progress/work/deadline_pending.json；仅真实错误（rc 非 0/2）才非零退出。
#   ② GBK 管道乱码：原实现 `python ... 2>&1 | Out-File` 由 PS 5.1 按 GBK 解码导致中文
#      乱码；改为 python 直接重定向到临时文件、再以 UTF-8 读入追加日志。
#   ③ 测试隔离（不影响生产）：DEADLINE_WATCH_SCRIPT / DEADLINE_WATCH_WORKDIR /
#      DEADLINE_WATCH_LOG 可覆盖脚本、工作目录、日志路径（单测用）。
# 注意：本文件必须保持恰好一个 UTF-8 BOM 头（部署脚本会自检）。
$ErrorActionPreference = "Continue"
$log = if ($env:DEADLINE_WATCH_LOG) { $env:DEADLINE_WATCH_LOG } else { Join-Path $PSScriptRoot "deadline_watch_task.log" }
$workdir = if ($env:DEADLINE_WATCH_WORKDIR) { $env:DEADLINE_WATCH_WORKDIR } else { $PSScriptRoot }
$scriptName = if ($env:DEADLINE_WATCH_SCRIPT) { $env:DEADLINE_WATCH_SCRIPT } else { 'deadline_watchdog.py' }
$encNoBom = New-Object System.Text.UTF8Encoding($false)

function Add-Log([string]$m) { [System.IO.File]::AppendAllText($log, $m + "`r`n", $encNoBom) }

Add-Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') deadline_watch_task 启动 ====="

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$pyCmd = Get-Command python -ErrorAction SilentlyContinue
$py = if ($pyCmd) { $pyCmd.Source } else { 'python' }
$outFile = Join-Path $env:TEMP ("dlw_out_" + $PID + ".txt")
$errFile = Join-Path $env:TEMP ("dlw_err_" + $PID + ".txt")
$rc = 1
try {
    $proc = Start-Process -FilePath $py -ArgumentList @('-X', 'utf8', $scriptName) `
        -WorkingDirectory $workdir -RedirectStandardOutput $outFile -RedirectStandardError $errFile `
        -NoNewWindow -Wait -PassThru -ErrorAction Stop
    $rc = [int]$proc.ExitCode
    foreach ($f in @($outFile, $errFile)) {
        if (Test-Path -LiteralPath $f) {
            $txt = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
            if ($txt) { Add-Log ($txt.TrimEnd()) }
        }
    }
} catch {
    Add-Log ("===== 启动失败: " + $_.Exception.Message + " =====")
    $rc = 1
} finally {
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
}

Add-Log "===== 退出码 $rc ====="
if ($rc -eq 2) {
    Add-Log "===== PENDING（设计语义：有 pending，任务按成功退出 0；明细见 deadline_pending.json）====="
    exit 0
}
exit $rc
