"=== guard hunt + new tasks $(Get-Date -Format s) ==="
'--- any task containing guard (all folders) ---'
Get-ScheduledTask | Where-Object { $_.TaskName -match 'guard' -or $_.TaskName -match 'AIOS' } | ForEach-Object { "$($_.TaskPath)$($_.TaskName) State=$($_.State) Logon=$($_.Principal.LogonType)" }
'--- 26 Interactive tasks detail ---'
$bad = Get-ScheduledTask | Where-Object { $_.Principal.LogonType -eq 'Interactive' -and (@($_.Actions) | Where-Object { ($_.Execute -match 'aios|ollama_watchdog') -or ($_.Arguments -match 'aios|ollama_watchdog') }) }
foreach ($b in $bad) {
  $a = @($b.Actions)[0]
  "$($b.TaskName) | exe=$($a.Execute) | arg=$($a.Arguments.Substring(0,[Math]::Min(70,$a.Arguments.Length)))"
}
"bad count: $(@($bad).Count)"
'--- registration dates of Interactive tasks ---'
foreach ($b in $bad) {
  $x = [xml](Export-ScheduledTask -TaskPath $b.TaskPath -TaskName $b.TaskName)
  "$($b.TaskName) registered=$($x.Task.RegistrationInfo.Date) author=$($x.Task.RegistrationInfo.Author)"
}
