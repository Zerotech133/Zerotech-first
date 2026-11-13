# fix_battery_limits.ps1
# Purpose: clear DisallowStartIfOnBatteries / StopIfGoingOnBatteries on root-path
#          scheduled tasks that still have them set (requires Administrator).
# Context: 2026-11-14, 75 interactive tasks were cleared in a non-admin session;
#          23 S4U/Interactive tasks in the root path returned "Access denied"
#          and need elevation. Microsoft system tasks are intentionally skipped.
# Run:     right-click fix_battery_limits.cmd -> Run as administrator

$ErrorActionPreference = "Stop"
$log = "C:\aios\_battery_fix.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT ELEVATED - run as administrator. Aborting." -ForegroundColor Red
    exit 1
}

$targets = Get-ScheduledTask | Where-Object {
    $_.TaskPath -eq '\' -and (
        $_.Settings.DisallowStartIfOnBatteries -eq $true -or
        $_.Settings.StopIfGoingOnBatteries -eq $true
    )
}

"[$stamp] start: targets=$($targets.Count) user=$($id.Name)" | Out-File -FilePath $log -Encoding utf8

$ok = 0
$fail = @()
foreach ($t in $targets) {
    try {
        $s = $t.Settings
        $s.DisallowStartIfOnBatteries = $false
        $s.StopIfGoingOnBatteries = $false
        Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Settings $s -ErrorAction Stop | Out-Null
        $ok++
        "OK   $($t.TaskName)" | Out-File -Append -FilePath $log -Encoding utf8
    } catch {
        $fail += $t.TaskName
        "FAIL $($t.TaskName) :: $($_.Exception.Message)" | Out-File -Append -FilePath $log -Encoding utf8
    }
}

# read back
$still = Get-ScheduledTask | Where-Object {
    $_.TaskPath -eq '\' -and (
        $_.Settings.DisallowStartIfOnBatteries -eq $true -or
        $_.Settings.StopIfGoingOnBatteries -eq $true
    )
}
$done = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$done] done: ok=$ok fail=$($fail.Count) still_restricted=$($still.Count)" | Out-File -Append -FilePath $log -Encoding utf8

Write-Host ""
Write-Host "ok=$ok fail=$($fail.Count) still_restricted=$($still.Count)"
Write-Host "log: $log"
if ($still.Count -gt 0) {
    Write-Host "still restricted:" -ForegroundColor Yellow
    $still | ForEach-Object { Write-Host ("  " + $_.TaskName) }
}
