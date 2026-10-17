# -*- coding: utf-8 -*-
# K263/K260 日链到点自动验收观察脚本（挂机用，10-07 11:56 启）
# 验证点：GubaCrawl 17:50 · KgDailySync 明晨06:20 · NoticeBackfill 明晨06:45
# 061 自愈：挂机先跑 backup_daily_regen.py（第四次丢失误删防护，缺失/漂移自动处理）
$log = 'C:\Temp\auto_verify_jobs.log'
function W($m){ $s = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'; "$s  $m" | Tee-Object -FilePath $log -Append }
# 2026-10-16 换机修复：E: 盘已不存在；裸 python 违反产线铁律（会解析到 PATH 的 3.12）。
# 改为显式 Python311 + C:\aios junction 路径。
& 'C:\Program Files\Python311\python.exe' -X utf8 "C:\aios\jev-eval\backup_daily_regen.py" 2>&1 | ForEach-Object { W("regen: $_") }
function Wait-Until($t, $desc){
    while((Get-Date) -lt $t){ Start-Sleep 60 }
    Start-Sleep 300   # 任务触发后给 5 分钟余量让结果落定
    W("触发点已过: $desc ($t)")
}
function TaskInfo($n){
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if(-not $t){ return "NOT FOUND" }
    $i = $t | Get-ScheduledTaskInfo
    return "state=$($t.State) last=$($i.LastRunTime) rc=$($i.LastTaskResult)"
}

# 1) 今晚 17:50 GubaCrawl 首批调度
$g17 = (Get-Date).Date.AddHours(17).AddMinutes(50)
Wait-Until $g17 "GubaCrawl 17:50"
W("VERIFY GubaCrawl : $(TaskInfo 'GubaCrawl')")

# 2) 明晨 06:20 KgDailySync
$k20 = (Get-Date).Date.AddDays(1).AddHours(6).AddMinutes(20)
Wait-Until $k20 "KgDailySync 06:20"
W("VERIFY KgDailySync: $(TaskInfo 'KgDailySync')")

# 3) 明晨 06:45 NoticeBackfill
$n45 = (Get-Date).Date.AddDays(1).AddHours(6).AddMinutes(45)
Wait-Until $n45 "NoticeBackfill 06:45"
W("VERIFY NoticeBackfill: $(TaskInfo 'NoticeBackfill')")

# 4) 明晨 03:00 每日备份自动跑（顺带核验磁盘脚本留驻）
$bk = (Get-Date).Date.AddDays(1).AddHours(3)
Wait-Until $bk "每日备份 03:00"
W("VERIFY 每日备份: $(TaskInfo 'AI工作站每日备份')")
$sd = Get-Item 'e:\老田的AI助手\jev-eval\backup_daily.ps1' -ErrorAction SilentlyContinue
W("磁盘 backup_daily.ps1 : " + $(if($sd){"EXISTS len=$($sd.Length)"}else{"MISSING"}))

W("=== 全部验收点已检查 ===")