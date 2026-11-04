# DSH one-shot elevated restart for 8796 (ASCII only)
$ErrorActionPreference = 'Stop'
$log = 'C:\Temp\dsh_restart_8796.log'
function W($m){ ('[' + (Get-Date -Format s) + '] ' + $m) | Out-File $log -Append -Encoding utf8 }
try {
  $c = Get-NetTCPConnection -LocalPort 8796 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($c) { W ('stop pid=' + $c.OwningProcess); Stop-Process -Id $c.OwningProcess -Force; Start-Sleep -Seconds 2 }
  $py = 'C:\Program Files\Python311\python.exe'
  if (-not (Test-Path $py)) { throw 'venv python missing' }
  Start-Process -FilePath $py -ArgumentList '-X','utf8','C:\项目\老田的AI助手\agent_platform\legion_command\serve.py','--port','8796','--host','127.0.0.1' -WorkingDirectory 'C:\aios' -WindowStyle Hidden -RedirectStandardOutput 'C:\Temp\legion_command_8796.log' -RedirectStandardError 'C:\Temp\legion_command_8796.log.err'
  Start-Sleep -Seconds 6
  $h = Invoke-RestMethod 'http://127.0.0.1:8796/api/hq/meeting/list' -TimeoutSec 10
  W ('health ok=' + $h.ok)
  exit 0
} catch {
  W ('FAIL ' + $_.Exception.Message)
  exit 1
}