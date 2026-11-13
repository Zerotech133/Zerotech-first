# 2026-11-13 FIX: ProcWatchdogHealth 由 pw_run.bat 改为 PS wrapper
# 根因: bat + S4U 计划任务 + PT72H ExecutionTimeLimit → 偶发 rc=2147946720 (STATUS_SHARING_VIOLATION)
# 修复: Start-Process python.exe 原生重定向 + 单次短探测 + python 自带 Global Mutex 幂等
param()

$ErrorActionPreference = 'Continue'
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\ops\local_gateway\proc_watchdog.py'
$log = 'C:\Temp\proc_watchdog_wrapper.log'

function Write-Log($msg) {
  try {
    $ts = Get-Date -Format 'MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "[$ts] $msg" -Encoding UTF8
  } catch {}
}

Write-Log '=== run ==='

try {
  $proc = Start-Process -FilePath $py -ArgumentList @('-X','utf8',$script) `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput 'C:\Temp\proc_watchdog_stdout.log' `
    -RedirectStandardError 'C:\Temp\proc_watchdog_stderr.log'
  $rc = $proc.ExitCode
  Write-Log "exit=$rc"
  exit $rc
} catch {
  Write-Log "EXCEPTION: $($_.Exception.Message)"
  exit 1
}

