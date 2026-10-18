# 一次性注册脚本：AI工作站执行失败巡检（R3 失败必响，联动层一期 2026-10-18）
# 用法（普通用户权限即可，无需管理员；用户 SID + InteractiveToken）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\aios\tools\register_execution_watchdog.ps1
# 等价手工命令：
#   schtasks /Create /TN "AI工作站执行失败巡检" /TR "C:\Program Files\Python311\python.exe -X utf8 C:\aios\tools\execution_watchdog.py" /SC MINUTE /MO 10 /F
# 验证（沙箱可能拦 Export-ScheduledTask，直读 XML 验证注册结果）：
#   schtasks /Query /TN "AI工作站执行失败巡检" /V /FO LIST
#   Select-Xml -Path "C:\Windows\System32\Tasks\AI工作站执行失败巡检" -XPath "//*"

$ErrorActionPreference = 'Stop'
$TaskName = 'AI工作站执行失败巡检'
$Py = 'C:\Program Files\Python311\python.exe'
$Script = 'C:\aios\tools\execution_watchdog.py'

if (-not (Test-Path $Py)) { throw "python 不存在: $Py" }
if (-not (Test-Path $Script)) { throw "巡检脚本不存在: $Script" }

# 已注册则先删（/F 幂等重建）
schtasks /Create /TN $TaskName /TR "`"$Py`" -X utf8 `"$Script`"" /SC MINUTE /MO 10 /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create 失败 exit=$LASTEXITCODE" }

# 注册结果验证：直读任务 XML（不依赖 Export-ScheduledTask）
$xml = Join-Path $env:WINDIR ("System32\Tasks\" + $TaskName)
if (Test-Path $xml) {
    [xml]$doc = Get-Content $xml -Raw
    $action = $doc.Task.Actions.Exec
    Write-Output ("REGISTER_OK command=" + $action.Command + " args=" + $action.Arguments)
    Write-Output ("REGISTER_OK trigger=" + $doc.Task.Triggers.TimeTrigger.Repetition.Interval)
} else {
    Write-Output 'REGISTER_OK (xml not readable, verify via schtasks /Query)'
}
schtasks /Query /TN $TaskName /FO LIST | ForEach-Object { Write-Output $_ }
