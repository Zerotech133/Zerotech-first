# paper trade launcher (ASCII only)
# Task: PaperTrade, daily two triggers:
#   09:28  open    (pre-open: execute overnight sells then buys; visible-price)
#   15:06  review+equity (after close: sell-judgment, pending orders, NAV)
# Exit code: 0 = ran (business results in JSON, not in rc).
# 2026-11-13: created with rules_v2 (three-tier position sizing).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
[Environment]::SetEnvironmentVariable('NO_PROXY',
    'rookie.xtcloud-tech.com,127.0.0.1,localhost', 'Process')

$log = 'C:\Temp\paper_trade.log'
$py  = 'C:\Program Files\Python311\python.exe'
try {
    $wd = 'C:\aios\quant_backtest'
    if (-not (Test-Path (Join-Path $wd 'paper_trade.py'))) { throw 'paper_trade.py not found' }
    Set-Location $wd

    $h = Get-Date -Format 'HHmm'
    if ($h -ge '0920' -and $h -lt '1200') {
        $body = & $py -X utf8 paper_trade.py open 2>&1 | Out-String
        $mode = 'open'
    } elseif ($h -ge '1501') {
        $body = (& $py -X utf8 paper_trade.py review 2>&1 | Out-String) + `
                (& $py -X utf8 paper_trade.py equity 2>&1 | Out-String)
        $mode = 'review+equity'
    } else {
        $body = 'no scheduled window (09:2x open / 15:0x review)'
        $mode = 'skip'
    }
    $rc = if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE
    }
    ('paper ' + $mode + ' rc=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    $body | Out-File ("C:\Temp\paper_" + $mode.Split('+')[0] + "_" + (Get-Date -Format yyyyMMdd) + ".log") -Encoding UTF8
    exit 0
} catch {
    ('paper ERR ' + $_.Exception.Message + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 1
}
