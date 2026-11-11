# Security audit wrapper (weekly task): pip-audit (py311) + bandit (py312)
# 说明：py312 解释器缺 venv 模块，pip-audit 2.x 无法直接跑；用 py311 的 pip-audit
# 审计 py312 的依赖冻结清单（-r），bandit 仍用 py312 扫仓库代码。
$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'
$py312 = 'C:\Program Files\Python311\python.exe'
$py311 = 'C:\Program Files\Python311\python.exe'
$log = 'C:\Temp\security_audit_latest.log'
$status = 'C:\Temp\security_audit_status.json'
$freeze = 'C:\Temp\py312_freeze.txt'
$ts = Get-Date -Format s
Remove-Item $log -ErrorAction SilentlyContinue
"[security-audit] $ts" | Out-File $log -Encoding utf8
try { & $py312 -m pip freeze -l | Where-Object { $_ -notmatch "^-e |^#|^@ " -and $_ -match "==" } | Set-Content $freeze -Encoding utf8 } catch { "freeze failed: $_" | Out-File $log -Append -Encoding utf8 }
$r1 = 1
$osv_rc = -1
$osv_vulns = -1
if (Test-Path $freeze) {
    & $py311 -m pip_audit -r $freeze *>> $log
    $r1 = $LASTEXITCODE
}
if ($r1 -ne 0 -and (Test-Path $freeze)) {
    & $py312 'C:\aios\tools\pip_osv_audit.py' -r $freeze --out 'C:\Temp\pip_osv_report.json' *>> $log
    $osv_rc = $LASTEXITCODE
    if (Test-Path 'C:\Temp\pip_osv_report.json') {
        try {
            $osv = Get-Content 'C:\Temp\pip_osv_report.json' -Raw | ConvertFrom-Json
            $osv_vulns = [int]$osv.vulnerable_packages
        } catch { "osv parse failed: $_" | Out-File $log -Append -Encoding utf8 }
    }
}
Push-Location 'C:\aios'
& $py312 -m bandit -r agent_platform portal tools quant_backtest k_stock news_collector `
    -x '.venv,venv,node_modules,worktrees,archive,__pycache__,tests,test_' `
    -f json -o 'C:\Temp\bandit_latest.json' *>> $log
$r2 = $LASTEXITCODE
Pop-Location

$counts = @{ HIGH = 0; MEDIUM = 0; LOW = 0 }
$bjson = 'C:\Temp\bandit_latest.json'
if (Test-Path $bjson) {
    try {
        $bd = Get-Content $bjson -Raw | ConvertFrom-Json
        foreach ($x in $bd.results) {
            $sev = [string]$x.issue_severity
            if ($counts.ContainsKey($sev)) { $counts[$sev] = $counts[$sev] + 1 }
        }
    } catch { "bandit result parse failed: $_" | Out-File $log -Append -Encoding utf8 }
}
$reg = $false
$base = 'C:\aios\tools\bandit_baseline_v2.json'
if (Test-Path $base) {
    try {
        $b = Get-Content $base -Raw | ConvertFrom-Json
        $bh = 0; $bm = 0
        if ($b.counts) {
            $bh = [int]$b.counts.HIGH; $bm = [int]$b.counts.MEDIUM
        } elseif ($b.results) {
            foreach ($x in $b.results) {
                $sev = [string]$x.issue_severity
                if ($sev -eq 'HIGH') { $bh = $bh + 1 }
                elseif ($sev -eq 'MEDIUM') { $bm = $bm + 1 }
            }
        }
        $reg = ($counts['HIGH'] -gt $bh) -or ($counts['MEDIUM'] -gt $bm)
    } catch { "baseline parse failed: $_" | Out-File $log -Append -Encoding utf8 }
}
@{
    ts = $ts
    pip_audit_rc = $r1
    pip_osv_rc = $osv_rc
    pip_osv_vulnerable = $osv_vulns
    bandit_rc = $r2
    bandit_high = $counts['HIGH']
    bandit_medium = $counts['MEDIUM']
    bandit_low = $counts['LOW']
    bandit_regression = $reg
    log = $log
    freeze = $freeze
    bandit_json = 'C:\Temp\bandit_latest.json'
} | ConvertTo-Json | Out-File $status -Encoding utf8
exit 0

