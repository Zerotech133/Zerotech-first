# paper trade launcher (ASCII only)
# Task: PaperTrade, daily two triggers:
#   09:35  open    (execute overnight sells then buys; visible-price)
#   18:25  review+equity (sell-judgment, pending orders, NAV)
#   [v2.3]   每个分支执行完后自动调 _audit_stage_a_verify.py 验判据门
# Exit code: 0 = ran + verify OK or PENDING; 1 = crash / verify FAIL.
# 2026-11-14 v2.4: verify is three-state (0 pass / 1 fail / 2 pending).
#   PENDING must NOT fail the task; non-trading-day [SKIP] skips verify;
#   --phase open|close is passed through (NAV only exists after 18:25).
# 2026-11-13: created with rules_v2 (three-tier position sizing).
# 2026-11-13 v2.2: rc is no longer always 0; review and equity are
#   captured separately so a crash in review cannot be masked by equity.
# 2026-11-14 v2.3: 自动调 verify 验判据门，rc 合并进总 rc；
# 2026-11-14 v2.5: pre-open account snapshot before open (evidence only, never changes rc).
#   verify 脚本不存在则 skip（兼容老环境）；verify rc 非 0 = 停跑信号。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
[Environment]::SetEnvironmentVariable('NO_PROXY',
    'rookie.xtcloud-tech.com,127.0.0.1,localhost', 'Process')

$log    = 'C:\Temp\paper_trade.log'
$py     = 'C:\Program Files\Python311\python.exe'
try {
    $wd     = 'C:\aios\quant_backtest'
    $verify = Join-Path $wd '_audit_stage_a_verify.py'
    $day    = Get-Date -Format 'yyyyMMdd'
    if (-not (Test-Path (Join-Path $wd 'paper_trade.py'))) { throw 'paper_trade.py not found' }
    Set-Location $wd

    $h  = Get-Date -Format 'HHmm'
    $rc = 0
    if ($h -ge '0920' -and $h -lt '1200') {
        # [v2.5] pre-open snapshot: evidence only, never touches $rc.
        # Closes criterion-3 blind spot (open discards filled sells).
        $snap = Join-Path $wd 'paper_preopen_snapshot.py'
        if (Test-Path $snap) {
            $snap_out = & $py -X utf8 $snap --day $day 2>&1 | Out-String
            $snap_rc  = if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE }
            ('paper preopen snapshot rc=' + $snap_rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
            $snap_out | Out-File ("C:\Temp\paper_preopen_" + $day + ".log") -Encoding UTF8
        } else {
            ('paper preopen snapshot SKIP (script not found: ' + $snap + ') ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        }
        $body = & $py -X utf8 paper_trade.py open 2>&1 | Out-String
        $rc = if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE }
        $mode = 'open'
    } elseif ($h -ge '1501') {
        $r1 = & $py -X utf8 paper_trade.py review 2>&1 | Out-String
        $rc1 = if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE }
        $r2 = & $py -X utf8 paper_trade.py equity 2>&1 | Out-String
        $rc2 = if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE }
        $body = $r1 + $r2
        $rc = [Math]::Max($rc1, $rc2)
        $mode = 'review+equity'
    } else {
        $body = 'no scheduled window (09:35 open / 18:25 review)'
        $mode = 'skip'
    }
    ('paper ' + $mode + ' rc=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    $body | Out-File ("C:\Temp\paper_" + $mode.Split('+')[0] + "_" + $day + ".log") -Encoding UTF8

    # ── v2.3 自动验判据门 ────────────────────────────────────────────
    $verify_output = ''
    $verify_rc     = 0
    if ($mode -ne 'skip' -and $rc -eq 0 -and -not ($body -match '\[SKIP\]')) {
        if (Test-Path $verify) {
            if ($mode -eq 'open') { $phase = 'open' } else { $phase = 'close' }
            $verify_output = & $py -X utf8 $verify --day $day --phase $phase 2>&1 | Out-String
            $verify_rc = if ($LASTEXITCODE -eq $null) { 0 } else { $LASTEXITCODE }
            ('paper verify rc=' + $verify_rc + ' ' + $mode + ' phase=' + $phase + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
            $verify_output | Out-File ("C:\Temp\paper_verify_" + $day + "_" + $phase + ".log") -Encoding UTF8
        } else {
            ('paper verify SKIP (script not found: ' + $verify + ') ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        }
    }
    # verify FAIL → 提升总 rc
    if ($verify_rc -eq 1) {
        $rc = 1
        ('paper FAIL verify 触发停跑 rc=' + $verify_rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    }

    if ($verify_rc -eq 2) {
        ('paper verify PENDING (not fully evaluated; not a failure) ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    }
    if ($rc -ne 0) {
        ('paper FAIL ' + $mode + ' rc=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
        exit 1
    }
    exit 0
} catch {
    ('paper ERR ' + $_.Exception.Message + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
    exit 1
}
