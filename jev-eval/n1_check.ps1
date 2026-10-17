# N1Check v1.8 - daily 07:30 morning verification (OPS-1001e H3; v1.2 OPS-1004 nightly trio; v1.3 OPS-1012 file-count baseline; v1.4 OPS-1013 script presence; v1.5 K-NEWS-001 jev backlog; v1.6 K-STOCK-061 regen self-heal; v1.6.1 K-STOCK-071 KEY_LOST 只查 pending 不算 sent 归档; v1.7 K-STOCK-076 neo4j 周备新鲜度)
# v1.8 2026-10-16 换机适配（单 C 盘 / 用户 t / 无注册表权威）：
#   - mirror 根 D:\backups_mirror -> C:\backups_mirror
#   - OneDrive C:\Users\Administrator -> C:\Users\t
#   - neo4j 周备目录 E:\KGStockAnalysis\backups\neo4j -> C:\aios\backups\neo4j
#   - MinFiles 10 -> 6（v11 清单裁剪：quant_db/qd_db/webui_data/quant_src 移除，
#     剩 zhice_db.enc + jev_eval + quant_data + neo4j_kg + quant_code + backup.log）
#   - quant_code 基线待新机首次备份后回填（旧机末值不同源不可比）
# Checks:
#  (0) regen self-heal (K-STOCK-061): backup_daily_regen.py exit=0 before presence check
#      (v1.8: 新机无注册表源，regen 以 DISABLED+exit0 报告，不再是误报 FAIL)
#  (1) today's backup set: DONE failed=0 + mirror files>=$MinFiles + quant_code files count
#  (2) v5 key guard normal path: no BACKUP_KEY_LOST flag anywhere
#  (3) offsite sync: task RC=0 + log "OK set=<day>" + OneDrive set present + prune<=2
#  (4) alert queue empty (no pending flags)
#  (5) no ENGINE_DOWN event pending
#  (6) nightly trio first-run (OPS-1004): TushareSync exit= line numeric/fresh/0
#      + RPS resonance & daily report task RC=0 + daily exit= line
#      (v1.8: 新机三件套任务未重建前此检查按设计 FAIL 响亮提醒)
#  (7) jev judgment backlog (K-NEWS-001): latest P2 left= line fresh & <= threshold
#  (8) neo4j weekly dump (K-STOCK-075): latest *.dump mtime within 8 days
# FAIL -> N1_CHECK_FAIL flag (alert forwarder pushes to Feishu) + report file.
$ErrorActionPreference = 'Continue'
$day = (Get-Date).ToString('yyyy-MM-dd')
# v1.8 换机：v11 清单 = 6 文件/集（zhice_db.sql.gz.enc, jev_eval.tar.gz,
# quant_data.tar.gz, neo4j_kg.tar.gz, quant_code.tar.gz, backup.log）。
# zhice_db 空库期间（数据未恢复）dump 过质量门会被删，集=5 文件+叠加 FAIL，
# 属响亮设计（用户决策 2026-10-16）。Update here only, never re-hardcode at check sites.
$MinFiles = 6
$fail = 0
$report = @()

# 2026-10-16 换机修复：优先 C:\aios junction（新机），E:\ 扫描降为旧机回退
$BaseDir = $null
if (Test-Path 'C:\aios\jev-eval\backup_alert.py') {
    $BaseDir = 'C:\aios'
}
if (-not $BaseDir) {
    foreach ($d in (Get-ChildItem 'E:\' -Directory -ErrorAction SilentlyContinue)) {
        if (Test-Path (Join-Path $d.FullName 'jev-eval\backup_alert.py')) { $BaseDir = $d.FullName; break }
    }
}
if (-not $BaseDir) {
    # 2026-10-16 审计修复：原 return = rc0 假绿，巡检失明静默通过。
    # 改落盲旗 + exit 1：计划任务 rc 非零，晨检/人工可捕获。
    if (-not (Test-Path 'C:\Temp')) { New-Item -ItemType Directory -Path 'C:\Temp' -Force | Out-Null }
    Set-Content -Path ('C:\Temp\n1_blind_{0}.flag' -f (Get-Date -Format 'yyyyMMdd_HHmmss')) -Value ('n1_check: base dir not found ' + (Get-Date)) -Encoding UTF8
    exit 1
}
$alertsDir = Join-Path $BaseDir 'jev-eval\alerts'
function Report($s) { $script:report += $s }
function Fail($s) { $script:fail++; $script:report += ('[FAIL] ' + $s) }

# --- 0a. regen self-heal (v1.6 / K-STOCK-061): heal BEFORE presence check ---
# v1.8: 新机无注册表权威，regen 以 DISABLED+exit0 返回（机制停用、日志可见）
$regenScript = Join-Path $BaseDir 'jev-eval\backup_daily_regen.py'
if (Test-Path $regenScript) {
    $regenOut = (& 'C://Program Files\Python311\python.exe' -X utf8 $regenScript 2>&1 | Select-Object -Last 1)
    if ($LASTEXITCODE -eq 0) { Report ('[OK] regen self-heal: ' + $regenOut) }
    else { Fail ('regen self-heal exit=' + $LASTEXITCODE + ' ' + $regenOut) }
} else { Fail 'backup_daily_regen.py MISSING from disk (regen guard itself lost)' }

# --- 0. backup_daily.ps1 disk presence (v1.4 / OPS-1013) ---
# 10-08 the script vanished from disk twice silently; N1 only checked backup
# SET integrity, never the script itself. v1.8 新机无注册表自动恢复——
# 本检查从"缩短发现时间"升级为唯一防线（丢了没人救，必须响亮）。
# NOTE: must stay AFTER Report/Fail definitions (PS functions are order-sensitive).
$bdScript = Join-Path $BaseDir 'jev-eval\backup_daily.ps1'
if (Test-Path $bdScript) { Report ('[OK] backup_daily.ps1 on disk ' + ((Get-Item $bdScript).Length) + 'B') }
else { Fail 'backup_daily.ps1 MISSING from disk (v1.8: no registry authority on new machine - restore from .v10.bak/MEMORY manually)' }

# --- 1. today's backup set ---
$set = Get-ChildItem 'C:\backups_mirror' -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like ($day + '_*') } | Sort-Object Name -Descending | Select-Object -First 1
if ($set) {
    $stat = Get-ChildItem $set.FullName -Recurse -File | Measure-Object -Sum Length
    Report ('[OK] backup set ' + $set.Name + ' files=' + $stat.Count + ' MB=' + [math]::Round($stat.Sum/1MB,1))
    if ($stat.Count -lt $MinFiles) { Fail ('mirror files=' + $stat.Count + ', expected >=' + $MinFiles) }
    # DONE 行由 backup_daily.ps1 在 MIRROR 复制之后才写入，mirror 副本永远不含
    # DONE —— DONE/quant_code 必须读原件（副本只用于上面的文件数核对）
    $blog = Join-Path $BaseDir ('backups\' + $set.Name + '\backup.log')
    if (Test-Path $blog) {
        $tail = @(Get-Content $blog -Tail 80 -ErrorAction SilentlyContinue)
        $anyDone = @($tail | Where-Object { $_ -match 'DONE' })
        if ($anyDone.Count -gt 0 -and $anyDone[0] -match 'failed=0') { Report '[OK] backup.log DONE failed=0' }
        elseif ($anyDone.Count -gt 0) { Fail ('backup DONE but failed<>0: ' + $anyDone[0]) }
        else { Fail 'backup.log has no DONE line (incomplete run?)' }
        $fline = @($tail | Where-Object { $_ -match 'quant_code' -and $_ -match 'files=\d+' })
        if ($fline.Count -gt 0) {
            if ($fline[0] -match 'files=(\d+)') { Report ('[OK] quant_code files=' + $Matches[1] + ' (v1.8 baseline 6572, first new-machine run 2026-10-16, holiday drift ~0)') }
        } else { Report '[WARN] quant_code files line not found in backup.log tail' }
    } else { Fail ('backup.log missing (original ' + $blog + ')') }
} else { Fail ('no backup set for ' + $day + ' under C:\backups_mirror') }

# --- 2. v5 key guard normal path ---
# v1.6.1 修正（K-STOCK-071）：sent/ 是转送后归档（*.done 已处置），
# 归档≠在告——只有 pending 区（alertsDir 根）的 flag 才算 FAIL，
# 否则 10-09 的已处置 KEY_LOST 存档会天天误报（10-10 预演实锤）
$keyLost = @(Get-ChildItem $alertsDir -Filter 'BACKUP_KEY_LOST_*.flag' -File -ErrorAction SilentlyContinue)
if ($keyLost.Count -eq 0) { Report '[OK] v5 key guard silent (normal path)' }
else { Fail 'BACKUP_KEY_LOST flag PENDING - key file issue, see flag' }

# --- 3. offsite sync ---
# v1.8: match both 'OffsiteSync' (task-name style) and 'offsite_sync.ps1'
# (new-machine script path) - -match is substring, underscore breaks the old pattern
$task = Get-ScheduledTask | Where-Object {
    @($_.Actions | Where-Object { $_.Arguments -match 'OffsiteSync|offsite_sync' }).Count -gt 0
}
if ($task) {
    $ti = $task | Get-ScheduledTaskInfo
    if ($ti.LastTaskResult -eq 0 -and $ti.LastRunTime -ge [datetime]($day + ' 04:00')) {
        Report ('[OK] offsite task RC=0 LastRun=' + $ti.LastRunTime)
    } else { Fail ('offsite task RC=' + $ti.LastTaskResult + ' LastRun=' + $ti.LastRunTime) }
} else { Fail 'offsite sync task not found' }

$slog = 'C:\Temp\offsite_sync.log'
if (Test-Path $slog) {
    $okLine = Select-String -Path $slog -Pattern ('OK set=' + $day) -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($okLine) { Report ('[OK] offsite log: ' + $okLine.Line.Trim()) }
    else { Fail ('offsite_sync.log has no "OK set=' + $day + '" line') }
} else { Fail 'C:\Temp\offsite_sync.log missing' }

$od = Join-Path $env:USERPROFILE 'OneDrive\backups_mirror'
$odSet = Get-ChildItem $od -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like ($day + '_*') } | Sort-Object Name -Descending | Select-Object -First 1
if ($odSet) {
    $ostat = Get-ChildItem $odSet.FullName -Recurse -File | Measure-Object -Sum Length
    Report ('[OK] OneDrive set ' + $odSet.Name + ' files=' + $ostat.Count)
    if ($ostat.Count -lt $MinFiles) { Fail ('OneDrive set files=' + $ostat.Count + ', expected >=' + $MinFiles) }
} else { Fail ('OneDrive mirror has no set for ' + $day) }
$odAll = @(Get-ChildItem $od -Directory -ErrorAction SilentlyContinue)
if ($odAll.Count -gt 2) { Fail ('OneDrive mirror holds ' + $odAll.Count + ' sets, prune failed') }
else { Report ('[OK] OneDrive sets=' + $odAll.Count + ' (prune to 2 works)') }

# --- 4. alert queue ---
$pend = @(Get-ChildItem $alertsDir -Filter '*.flag' -File -ErrorAction SilentlyContinue)
if ($pend.Count -eq 0) { Report '[OK] alert queue empty' }
else { Fail ('alert queue not empty: ' + (($pend | Select-Object -First 3 | ForEach-Object { $_.Name }) -join ',')) }

# --- 5. engine watchdog ---
$eng = @(Get-ChildItem $alertsDir -Filter 'ENGINE_DOWN_*.flag' -File -ErrorAction SilentlyContinue)
if ($eng.Count -eq 0) { Report '[OK] no ENGINE_DOWN event pending' }
else { Fail ('ENGINE_DOWN flag pending: ' + $eng[0].Name + ' - check docker engine') }

# --- 6. nightly trio first-run (OPS-1004 hardening) ---
# window: yesterday 20:00 -> now, covers TushareSync 23:30 / DailyReport 03:00
# v1.8 machine swap: task names re-pointed to the rebuilt new-machine tasks
#   AI量化RPS共振   -> RpsDaily    (weekly Sat 16:05, checked as 8-day window)
#   AI工作站统一日报 -> DailyReport (daily 03:00, nightly window)
# log contracts verified identical on new machine:
#   tushare_sync exit= / daily exit= lines still land in C:\Temp\*_task.log
$win = ([datetime]$day).AddHours(-4)
function Check-NightTask($name) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Fail ($name + ': task not found'); return }
    $ti = $t | Get-ScheduledTaskInfo
    if ($ti.LastTaskResult -eq 0 -and $ti.LastRunTime -ge $win) {
        Report ('[OK] ' + $name + ' RC=0 LastRun=' + $ti.LastRunTime)
    } else { Fail ($name + ' RC=' + $ti.LastTaskResult + ' LastRun=' + $ti.LastRunTime) }
}
function Check-WeeklyTask($name, $maxDays) {
    # v1.8: RpsDaily is weekly (Sat 16:05) - freshness window in DAYS, not the
    # nightly-trio 20:00 window. Same semantics as neo4j weekly dump check.
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { Fail ($name + ': task not found'); return }
    $ti = $t | Get-ScheduledTaskInfo
    if ($ti.LastRunTime -eq [datetime]::MinValue) { Fail ($name + ' never ran'); return }
    if ($ti.LastTaskResult -eq 0 -and $ti.LastRunTime -ge (Get-Date).AddDays(-$maxDays)) {
        Report ('[OK] ' + $name + ' RC=0 LastRun=' + $ti.LastRunTime)
    } else { Fail ($name + ' RC=' + $ti.LastTaskResult + ' LastRun=' + $ti.LastRunTime + ' (>' + $maxDays + 'd)') }
}
function Check-ExitLine($tag, $log) {
    # OPS-1004 ExitCode-null family: line must exist, timestamp fresh, code numeric and 0
    if (-not (Test-Path $log)) { Fail ($tag + ': ' + $log + ' missing'); return }
    $last = @(Get-Content $log -ErrorAction SilentlyContinue |
        Where-Object { $_ -match ('^' + $tag + ' exit=') }) | Select-Object -Last 1
    if (-not $last) { Fail ($tag + ': no exit= line in ' + $log); return }
    if ($last -match ('^' + $tag + ' exit=(\S+)\s+(.+)$')) {
        $code = $Matches[1]; $tsText = $Matches[2].Trim()
        $ts = [datetime]::MinValue
        if (-not [datetime]::TryParse($tsText, [ref]$ts)) {
            Fail ($tag + ': exit line timestamp unparsable: ' + $last); return
        }
        if ($ts -lt $win) { Fail ($tag + ': exit line stale (older than ' + $win + '): ' + $last); return }
        if ($code -notmatch '^\d+$') {
            Fail ($tag + ': exit code not numeric (null-family signature): ' + $last); return
        }
        if ($code -ne '0') { Fail ($tag + ': exit=' + $code + ' (see launcher log)'); return }
        Report ('[OK] ' + $tag + ' exit line: ' + $last)
    } else { Fail ($tag + ': unparsable exit line: ' + $last) }
}
$tsTask = Get-ScheduledTask -TaskName 'TushareSync' -ErrorAction SilentlyContinue
if ($tsTask -and $tsTask.State -eq 'Running') {
    Report '[WARN] TushareSync still running at morning check (long incremental)'
} else {
    Check-ExitLine 'tushare_sync' 'C:\Temp\tushare_sync_task.log'
}
Check-NightTask 'TushareSync'
Check-NightTask 'DailyReport'
Check-WeeklyTask 'RpsDaily' 8
Check-ExitLine 'daily' 'C:\Temp\daily_report_task.log'

# --- 7. jev judgment backlog (v1.5 / K-NEWS-001, 2026-10-08 lockout lesson) ---
# Signal = latest "P2 exit=N ... left=M" line in C:\jev_backfill_task.log
# (written by the hourly JEVBackfill-Hourly wrapper). A hung instance emits
# no new P2 line, so the staleness check is what catches the 0x800710E0
# lockout chain (08:57 hang: host killed by ExecutionTimeLimit, python
# orphaned, IgnoreNew refused every later trigger). Backlog threshold is
# deliberately loose: daytime collector inserts new rows continuously, so
# left routinely oscillates; FAIL only on clear runaway.
$MaxJevLeft = 150
$JevLog = 'C:\jev_backfill_task.log'
$JevStaleH = 12
if (-not (Test-Path $JevLog)) {
    Fail ('jev: ' + $JevLog + ' missing (JEVBackfill-Hourly never ran?)')
} else {
    $jlast = @(Get-Content $JevLog -ErrorAction SilentlyContinue |
        Where-Object { $_ -match 'P2 exit=' }) | Select-Object -Last 1
    if (-not $jlast) { Fail 'jev: no P2 line ever in jev_backfill_task.log' }
    elseif ($jlast -match '^\[([\d\- :]+)\] P2 exit=(\d+)') {
        $jtsText = $Matches[1]; $jcode = $Matches[2]
        $jts = [datetime]::MinValue
        $jok = [datetime]::TryParseExact($jtsText, 'MM-dd HH:mm:ss', $null,
            [System.Globalization.DateTimeStyles]::None, [ref]$jts)
        if ($jok) { $jts = $jts.AddYears((Get-Date).Year - $jts.Year) }  # log line has no year
        if (-not $jok) { Fail ('jev: P2 timestamp unparsable: ' + $jlast) }
        elseif ($jts -lt (Get-Date).AddHours(-$JevStaleH)) {
            Fail ('jev: P2 line STALE (last=' + $jtsText + ', >' + $JevStaleH + 'h) - instance hung or task dead (0x800710E0 chain)')
        } elseif ($jcode -ne '0') {
            Fail ('jev: backfill exit=' + $jcode + ' (see ' + $JevLog + ')')
        } elseif ($jlast -match 'left=(\d+)') {
            $jleft = [int]$Matches[1]
            if ($jleft -gt $MaxJevLeft) { Fail ('jev: backlog left=' + $jleft + ' > ' + $MaxJevLeft + ' (cloud outage or loop stuck)') }
            else { Report ('[OK] jev backlog left=' + $jleft + ' (' + $jtsText + ')') }
        } else { Report '[WARN] jev P2 line has no left= value: ' + $jlast }
    } else { Fail ('jev: unparsable P2 line: ' + $jlast) }
}

# --- 8. neo4j weekly dump freshness (v1.7 / K-STOCK-076) ---
# KG-Neo4jWeeklyDump 周六 05:30 停库 dump；最新 *.dump 超 8 天=周备断了
# v1.8: 目录换机 C:\aios\backups\neo4j
# 2026-10-16 章程 §5：去 junction 依赖，改真实路径（与 neo4j_weekly_dump.ps1 同步）
$nbDir = 'C:\项目\老田的AI助手\backups\neo4j'
$nbLatest = @(Get-ChildItem $nbDir -Filter '*.dump' -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1)
if (-not $nbLatest) {
    Fail 'neo4j dump: no *.dump found in ' + $nbDir
} elseif ($nbLatest[0].LastWriteTime -lt (Get-Date).AddDays(-8)) {
    Fail ('neo4j dump STALE: latest=' + $nbLatest[0].Name + ' @ ' +
        $nbLatest[0].LastWriteTime + ' (>8d) - KG-Neo4jWeeklyDump broken?')
} else {
    Report ('[OK] neo4j dump fresh: ' + $nbLatest[0].Name + ' @ ' +
        $nbLatest[0].LastWriteTime)
}

# --- summary ---
if ($fail -eq 0) { $report += 'N1 ALL PASS' } else { $report += ('N1 FAILURES=' + $fail) }
$rf = Join-Path $BaseDir ('jev-eval\n1_report_' + $day + '.txt')
$report | Out-File $rf -Encoding UTF8
if ($fail -gt 0) {
    $flag = Join-Path $alertsDir ('N1_CHECK_FAIL_' + (Get-Date -Format 'yyyyMMdd_HHmm') + '.flag')
    ($report -join "`n") | Set-Content -LiteralPath $flag -Encoding UTF8
}
