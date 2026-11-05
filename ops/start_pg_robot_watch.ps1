# PG-Robot watchdog wrapper: start p_g_robot.py only if not already running.
# Replaces LogonTrigger deploy (denied for non-admin); polled every 5 min.
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\quant_backtest\p_g_robot.py'
$chatId = 'oc_16ed759795430734a90a7850ba33bec1,oc_6b97bee8b5340e071d0fc10cc6c8569f,oc_3d55636bb5ac9f8dbf1f4ebaa3698526'
$logPath = 'C:\Temp\pg_robot_watch.log'

# 2026-10-24: credential preflight (stops an infinite restart loop).
# p_g_robot.py reads LARKSUITE_CLI_APP_ID / LARKSUITE_CLI_APP_SECRET from the
# environment. When they are missing it retries 10 x 60s and then exits FATAL;
# this wrapper then starts it again on the next tick, forever. Observed
# 2026-10-24: ~10 min per cycle since at least 11:34, no working robot.
# Skip the launch and log once per "state change" instead of hammering.
$hasAppId = -not [string]::IsNullOrWhiteSpace($env:LARKSUITE_CLI_APP_ID)
$hasSecret = -not [string]::IsNullOrWhiteSpace($env:LARKSUITE_CLI_APP_SECRET)

# 2026-11-06: scheduler does not inherit the operator env. Load the QA bot
# credential from the local secret file when the environment is missing.
# Never print the secret. File is owned by the portal work dir.
if (-not ($hasAppId -and $hasSecret)) {
    $secretPath = 'C:\aios\portal\work\feishu_secrets\zb.quant.qa.json'
    if (Test-Path -LiteralPath $secretPath) {
        try {
            $qa = Get-Content -LiteralPath $secretPath -Raw -ErrorAction Stop | ConvertFrom-Json
            if (-not [string]::IsNullOrWhiteSpace($qa.app_id) -and
                -not [string]::IsNullOrWhiteSpace($qa.app_secret)) {
                $env:LARKSUITE_CLI_APP_ID = $qa.app_id
                $env:LARKSUITE_CLI_APP_SECRET = $qa.app_secret
                $hasAppId = $true
                $hasSecret = $true
                $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
                ("[{0}] loaded QA credentials from secret file (env was empty)" -f $stamp) |
                    Out-File $logPath -Append -Encoding utf8 -ErrorAction SilentlyContinue
            }
        } catch {
            # fall through to the SKIP branch below
        }
    }
}

if (-not ($hasAppId -and $hasSecret)) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $last = ''
    if (Test-Path $logPath) {
        $last = (Get-Content $logPath -Tail 1 -ErrorAction SilentlyContinue) -join ''
    }
    if ($last -notmatch 'SKIP start: missing LARKSUITE') {
        try {
            # NOTE: in PowerShell -f binds tighter than +, so the whole string must be
            # concatenated before formatting; otherwise only the last fragment is
            # substituted and the log keeps a literal [{0}] (observed 2026-10-24).
            # Keep this file ASCII-only: it has no BOM, and PS 5.1 would read any
            # non-ASCII byte as GBK (see AGENTS.md deploy note).
            $msg = "[{0}] SKIP start: missing LARKSUITE_CLI_APP_ID/LARKSUITE_CLI_APP_SECRET " +
                   "(BLOCKED - waiting for credentials; see p_g_robot.py header)"
            ($msg -f $stamp) | Out-File $logPath -Append -Encoding utf8 -ErrorAction Stop
        } catch {
            # log write is best-effort: never let it turn the skip into a failure
        }
    }
    exit 0
}

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*p_g_robot.py*' }
if (-not $running) {
    Start-Process $py -ArgumentList "`"$script`" --chat-id $chatId" `
        -WorkingDirectory 'C:\aios\quant_backtest' -WindowStyle Hidden `
        -RedirectStandardOutput 'C:\Temp\pg_robot.out.log' -RedirectStandardError 'C:\Temp\pg_robot.err.log'
    ("[{0}] started p_g_robot.py" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) |
        Out-File $logPath -Append -Encoding utf8
}
exit 0
