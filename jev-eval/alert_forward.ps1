# alert_forward.ps1 - forward pending *.flag alerts to Feishu
# Runs as Administrator (interactive session) via scheduled task, every 30 min.
# The SYSTEM backup task only writes flag files (no lark auth under SYSTEM);
# this script picks them up and sends via backup_alert.py -> lark-cli.
# Keep this file ASCII-only (PS 5.1 reads no-BOM UTF-8 as ANSI).
$ErrorActionPreference = 'Continue'
$pyExe  = 'C:\Program Files\Python311\python.exe'

# 2026-10-16 machine swap: prefer C:\aios junction (new machine), E:\ scan kept as old-machine fallback
$BaseDir = $null
if (Test-Path 'C:\aios\jev-eval\backup_alert.py') {
    $BaseDir = 'C:\aios'
}
if (-not $BaseDir) {
    foreach ($d in (Get-ChildItem 'E:\' -Directory -ErrorAction SilentlyContinue)) {
        if (Test-Path (Join-Path $d.FullName 'jev-eval\backup_alert.py')) { $BaseDir = $d.FullName; break }
    }
}
if (-not $BaseDir) { exit 1 }

$AlertsDir = Join-Path $BaseDir 'jev-eval\alerts'
$SentDir   = Join-Path $AlertsDir 'sent'
$AlertPy   = Join-Path $BaseDir 'jev-eval\backup_alert.py'
$LogFile   = Join-Path $AlertsDir 'forward.log'
foreach ($p in @($AlertsDir, $SentDir)) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}
function Log([string]$m) {
    ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) | Out-File -FilePath $LogFile -Append -Encoding ascii
}

$flags = @(Get-ChildItem $AlertsDir -Filter '*.flag' -File -ErrorAction SilentlyContinue)
if ($flags.Count -eq 0) { exit 0 }

foreach ($f in $flags) {
    try {
        # PS 5.1: flag is UTF-8 (backup script writes -Encoding UTF8, with BOM).
        # Explicit -Encoding UTF8 also tolerates no-BOM UTF-8 flags; default ANSI
        # would turn Chinese paths into mojibake in the card.
        $raw = Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8
        $body = "[{0}]`n{1}" -f $f.BaseName, $raw
        $tmp = Join-Path $AlertsDir 'pending_msg.txt'
        [IO.File]::WriteAllText($tmp, $body, (New-Object System.Text.UTF8Encoding($false)))
        $out = & $pyExe $AlertPy $tmp 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            Move-Item -LiteralPath $f.FullName -Destination (Join-Path $SentDir ($f.Name + '.' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.done')) -Force
            Log ("SENT " + $f.Name)
        } else {
            Log ("FAIL " + $f.Name + " :: " + $out.Trim())
        }
    } catch {
        Log ("ERROR " + $f.Name + " :: " + $_.Exception.Message)
    }
}
exit 0
