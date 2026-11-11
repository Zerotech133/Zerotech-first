# 以管理员 PowerShell 运行本脚本，注册 KStockIntelDaily 每日计划任务。
# 作用：每天 07:30 自动运行策略中台(k_stock)持仓/近期出票标的的开源情报驱动采集。
# 串接现有更新链路：XqTimelineDaily(07:00) -> [本任务 07:30] -> SentimentSnapshot(08:00)。
$ErrorActionPreference = 'Stop'
$exe = "C:\Program Files\Python311\pythonw.exe"
$arg = "-X utf8 `"C:\aios\news_collector\run_kstock_intel.py`""
$action   = New-ScheduledTaskAction -Execute $exe -Argument $arg
$trigger  = New-ScheduledTaskTrigger -Daily -At "07:30"
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U
Register-ScheduledTask -TaskName "KStockIntelDaily" -Action $action -Trigger $trigger `
    -Principal $principal -Force `
    -Description "策略中台(k_stock)持仓/出票标的 开源情报驱动采集(OSINT1桥); 串接 XqTimelineDaily(07:00)->本任务(07:30)->SentimentSnapshot(08:00)"
Get-ScheduledTask -TaskName "KStockIntelDaily" | Select-Object TaskName, State, `
    @{n='NextRun';e={(Get-ScheduledTaskInfo -TaskName $_.TaskName).NextRunTime}}
