# deadline_watch_task.ps1 - DeadlineWatch 计划任务包装器（K-ORG-197 轨道B/W33）
# 由 Windows 计划任务 DeadlineWatch 每日五时点（06:05/08:45/16:45/20:45/22:05）拉起：
# 切到本目录后运行数据到点升级器（python -X utf8 deadline_watchdog.py），
# 全部输出追加留痕到本目录 deadline_watch_task.log，并以 python 退出码退出
# （0=全部就绪或已恢复，2=有 pending，与 picks 巡检同语义）。
# 注意：本文件必须保持恰好一个 UTF-8 BOM 头（部署脚本会自检，双 BOM 或无 BOM 均判失败）。

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "deadline_watch_task.log"

"===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') deadline_watch_task 启动 =====" |
    Out-File -FilePath $log -Append -Encoding utf8

python -X utf8 deadline_watchdog.py 2>&1 |
    Out-File -FilePath $log -Append -Encoding utf8

$rc = $LASTEXITCODE
"===== 退出码 $rc =====" | Out-File -FilePath $log -Append -Encoding utf8
exit $rc
