# Ensure DSH merge-window tasks (11-07 .. 11-11) are enabled and hardened. Idempotent.
# - Enables only the listed window tasks if Disabled; never restarts Running tasks.
# - Hardens settings so a suspended/asleep PC still runs them: StartWhenAvailable + WakeToRun
#   + allow-on-battery (11-06 21:40 window was lost because PC slept 18:07-23:42).
# - 11-06 16:30/21:40 pairs were consumed manually or expired (PC asleep) and are not listed.
$tasks = @(
  "DSH-Prep-1107-1630","DSH-Merge-1107-1630","DSH-Prep-1107-2140","DSH-Merge-1107-2140",
  "DSH-Prep-1108-1630","DSH-Merge-1108-1630","DSH-Prep-1108-2140","DSH-Merge-1108-2140",
  "DSH-Gate-Pin-Commit-1109",
  "DSH-Merge-1109-1630",
  "DSH-Prep-1110-1630","DSH-Merge-1110-1630",
  "DSH-Prep-1110-2140","DSH-Merge-1110-2140"
  "DSH-Prep-1111-1630","DSH-Merge-1111-1630"
"DSH-Prep-1111-2140","DSH-Merge-1111-2140","DSH-Prep-1112-1630","DSH-Prep-1112-2140","DSH-Merge-1112-2140"
  "DSH-Prep-1114-1630","DSH-Merge-1114-1630","DSH-PostMerge-1114-1630",
  "DSH-Prep-1114-2140","DSH-Merge-1114-2140","DSH-PostMerge-1114-2140",
  "DSH-Window-1115-1630"
)
$business = @(
  "AI工作站门户API","IntradayWatch","IntradayWatchGuard","HermesWebUI-Guard",
  "CuratedDailyRefresh","QuantStockAnalysisDaily","DSH-HideConsoleWatcher","DSH-ScreenMonitor"
)
function Snapshot($names) {
  $h = @{}
  foreach ($n in $names) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    $h[$n] = if ($t) { "$($t.State)" } else { "MISSING" }
  }
  return $h
}
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$bizBefore = Snapshot $business
$changes = 0
$hardened = @()
$lines = @()
foreach ($n in $tasks) {
  $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
  if (-not $t) { $lines += "MISSING  $n"; continue }
  if ($t.State -eq "Running") { $lines += "RUNNING  $n (left untouched)"; continue }
  if ($t.State -eq "Disabled") {
    schtasks /Change /TN $n /ENABLE > $null 2>&1
    $t = Get-ScheduledTask -TaskName $n
    $lines += "ENABLED  $n (was Disabled -> $($t.State))"
    $changes++
  } else {
    $lines += "OK       $n ($($t.State))"
  }
  $s = $t.Settings
  $dirty = $false
  if (-not $s.StartWhenAvailable) { $s.StartWhenAvailable = $true; $dirty = $true }
  if (-not $s.WakeToRun) { $s.WakeToRun = $true; $dirty = $true }
  if ($s.DisallowStartIfOnBatteries) { $s.DisallowStartIfOnBatteries = $false; $dirty = $true }
  if ($s.StopIfGoingOnBatteries) { $s.StopIfGoingOnBatteries = $false; $dirty = $true }
  if ($s.MultipleInstances -ne "IgnoreNew") { $s.MultipleInstances = "IgnoreNew"; $dirty = $true }
  if ($dirty) {
    try { Set-ScheduledTask -TaskName $n -Settings $s | Out-Null; $hardened += $n; $changes++ }
    catch { $lines += "HARDEN-FAIL  $n : $($_.Exception.Message)" }
  }
}
$bizAfter = Snapshot $business
$bizStable = $true
$bizLines = @()
foreach ($n in $business) {
  if ($bizBefore[$n] -ne $bizAfter[$n]) { $bizStable = $false; $bizLines += "CHANGED  $n : $($bizBefore[$n]) -> $($bizAfter[$n])" }
}
$out = @()
$out += "==== $stamp ensure_window_tasks ===="
$out += $lines
$out += "CHANGES=$changes"
$out += ("HARDENED=" + ($(if ($hardened.Count) { $hardened -join "," } else { "none" })))
$out += "BUSINESS_STABLE=$bizStable"
if ($bizLines.Count -gt 0) { $out += $bizLines }
$out += ("BUSINESS_STATES=" + (($business | ForEach-Object { "$_=" + $bizAfter[$_] }) -join "; "))
$out -join "`n" | Tee-Object -FilePath C:\Temp\ensure_window_tasks.log -Append
exit 0
