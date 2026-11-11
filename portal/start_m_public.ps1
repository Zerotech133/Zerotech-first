# start_m_public.ps1 - mobile public entry (ASCII only, Windows PowerShell 5.1 safe)
# 2026-10-25: start m_public_gate (127.0.0.1:8081) + cloudflared quick tunnel,
# print the public https URL and update portal/work/m_link.json automatically.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File portal\start_m_public.ps1
$ErrorActionPreference = 'Stop'
$root = 'C:\aios'                       # junction -> project root (ASCII path)
$portal = Join-Path $root 'portal'
$py = 'C:\Program Files\Python311\python.exe'
$cfg = Join-Path $portal 'work\m_link.json'
$gateLog = 'C:\Temp\m_public_gate.log'
$cfLog = 'C:\Temp\m_cloudflared.log'
$cfExe = Join-Path $root 'bin\cloudflared.exe'

# 1) gate
$listen = Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue
if (-not $listen) {
    Start-Process -FilePath $py -WindowStyle Hidden -ArgumentList @(
        (Join-Path $portal 'm_public_gate.py'), '--port', '8081',
        '--target', 'http://127.0.0.1:8080') `
        -RedirectStandardOutput $gateLog -RedirectStandardError ($gateLog + '.err')
    Start-Sleep -Seconds 2
}
Write-Output ('gate 8081: ' + $(if (Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue) { 'UP' } else { 'DOWN' }))

# 2) cloudflared quick tunnel
$cf = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue
if (-not $cf) {
    if (-not (Test-Path $cfExe)) { Write-Output 'cloudflared.exe missing'; exit 1 }
    Remove-Item $cfLog -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $cfExe -WindowStyle Hidden -ArgumentList @(
        'tunnel', '--no-autoupdate', '--protocol', 'http2',
        '--edge-ip-version', '4', '--url', 'http://127.0.0.1:8081') `
        -RedirectStandardOutput $cfLog -RedirectStandardError ($cfLog + '.err')
}

# 3) wait for URL
$url = ''
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $cfLog) {
        $m = Select-String -Path $cfLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches -ErrorAction SilentlyContinue
        if ($m) { $url = ($m | ForEach-Object { $_.Matches } | ForEach-Object { $_.Value } | Select-Object -Last 1) }
    }
    if ($url) { break }
}
if (-not $url) { Write-Output 'tunnel URL not found (check C:\Temp\m_cloudflared.log)'; exit 1 }

# 4) update m_link.json (python json edit keeps encoding; backup first)
$pyUpdate = @'
import json, shutil, sys
cfg = r"C:\aios\portal\work\m_link.json"
url = sys.argv[1]
shutil.copyfile(cfg, cfg + ".bak")
d = json.load(open(cfg, encoding="utf-8"))
d["enabled"] = True
d["base"] = url
d["note"] = "auto-updated by start_m_public.ps1 at " + url
json.dump(d, open(cfg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("UPDATED", cfg, "->", url)
'@
$tmpPy = 'C:\Temp\m_link_update.py'
Set-Content -Path $tmpPy -Value $pyUpdate -Encoding UTF8
& $py $tmpPy $url
Write-Output ('PUBLIC_URL=' + $url)
Write-Output 'NOTE: restart old bot + exec bot to load new card links (m_links is imported at process start).'

