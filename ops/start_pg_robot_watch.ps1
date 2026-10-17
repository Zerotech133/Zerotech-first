# PG-Robot watchdog wrapper: start p_g_robot.py only if not already running.
# Replaces LogonTrigger deploy (denied for non-admin); polled every 5 min.
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\quant_backtest\p_g_robot.py'
$chatId = 'oc_16ed759795430734a90a7850ba33bec1,oc_6b97bee8b5340e071d0fc10cc6c8569f,oc_3d55636bb5ac9f8dbf1f4ebaa3698526'

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*p_g_robot.py*' }
if (-not $running) {
    Start-Process $py -ArgumentList "`"$script`" --chat-id $chatId" `
        -WorkingDirectory 'C:\aios\quant_backtest' -WindowStyle Hidden `
        -RedirectStandardOutput 'C:\Temp\pg_robot.out.log' -RedirectStandardError 'C:\Temp\pg_robot.err.log'
    ("[{0}] started p_g_robot.py" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) |
        Out-File 'C:\Temp\pg_robot_watch.log' -Append -Encoding utf8
}
exit 0
