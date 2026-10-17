'--- elevated/related processes ---'
Get-Process powershell, schtasks -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime, MainWindowTitle | Format-Table -AutoSize | Out-String
'--- launch flag ---'
Get-Content 'C:\aios\s4u_launch.txt' -ErrorAction SilentlyContinue
'--- any task currently Running state? ---'
Get-ScheduledTask | Where-Object State -eq 'Running' | Select-Object TaskName | Out-String
'--- LogonType snapshot ---'
$all = Get-ScheduledTask | Where-Object { @($_.Actions) | Where-Object { ($_.Execute -match 'aios|ollama_watchdog') -or ($_.Arguments -match 'aios|ollama_watchdog') } }
foreach ($g in ($all | Group-Object { $_.Principal.LogonType })) { "LogonType=$($g.Name) count=$($g.Count)" }
