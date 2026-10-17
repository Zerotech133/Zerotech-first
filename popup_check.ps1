"=== popup final check $(Get-Date -Format s) ==="
# 1. task logon type distribution
$all = Get-ScheduledTask | Where-Object { (@($_.Actions) | Where-Object { ($_.Execute -match 'aios|ollama_watchdog') -or ($_.Arguments -match 'aios|ollama_watchdog') }) }
"tasks total: $(@($all).Count)"
foreach ($g in ($all | Group-Object { $_.Principal.LogonType })) { "LogonType=$($g.Name) count=$($g.Count)" }

# 2. guard status
$gt = Get-ScheduledTask -TaskName 'AIOS-S4U-Guard' -ErrorAction SilentlyContinue
if ($gt) {
  $gi = Get-ScheduledTaskInfo -TaskName 'AIOS-S4U-Guard'
  "guard: State=$($gt.State) LastRun=$($gi.LastRunTime) Result=$($gi.LastTaskResult) NextRun=$($gi.NextRunTime)"
} else { 'guard: NOT FOUND!' }

# 3. guard log tail
'--- guard log (last 5) ---'
if (Test-Path 'C:\aios\s4u_guard.log') { Get-Content 'C:\aios\s4u_guard.log' -Tail 5 } else { '(no log)' }

# 4. visible console windows right now
$vis = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and ($_.Path -match 'aios|ollama' -or $_.ProcessName -match 'wscript|python|ollama') }
'--- visible windows ---'
if ($vis) { foreach ($v in $vis) { "VISIBLE: $($v.ProcessName) PID $($v.Id) title=$($v.MainWindowTitle)" } } else { '(none - all hidden)' }

# 5. recent task run failures (last 2h)
'--- recent failed runs (aios, last 2h) ---'
$fr = $all | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; if ($i.LastTaskResult -ne 0 -and $i.LastRunTime -gt (Get-Date).AddHours(-2)) { "$($_.TaskName) result=$($i.LastTaskResult) at $($i.LastRunTime)" } }
if ($fr) { $fr } else { '(none)' }
