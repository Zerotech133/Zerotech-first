# setup_named_tunnel.ps1 - named cloudflare tunnel for mobile H5 (ASCII only)
# Usage:
#   A) cert mode (after `cloudflared tunnel login` wrote ~/.cloudflared/cert.pem):
#      powershell -File portal\setup_named_tunnel.ps1 -Domain m.example.com -TunnelName lingjie-mobile
#   B) token mode (tunnel created in Cloudflare Zero Trust dashboard):
#      powershell -File portal\setup_named_tunnel.ps1 -TunnelToken <token>
# After success: m_link.json + data/m/urls.json default_base are updated and a
# verification request is printed.
param(
    [string]$Domain = '',
    [string]$TunnelName = 'lingjie-mobile',
    [string]$TunnelToken = ''
)
$ErrorActionPreference = 'Stop'
$root = 'C:\aios'
$portal = Join-Path $root 'portal'
$cf = Join-Path $root 'bin\cloudflared.exe'
$cfgDir = Join-Path $env:USERPROFILE '.cloudflared'
$cfLog = 'C:\Temp\m_named_tunnel.log'
$py = 'C:\Program Files\Python311\python.exe'

if (-not (Test-Path $cf)) { Write-Output 'cloudflared.exe missing'; exit 1 }

function Update-Base([string]$url) {
    $pyCode = @'
import json, shutil, sys
url = sys.argv[1]
cfg = r"C:\aios\portal\work\m_link.json"
shutil.copyfile(cfg, cfg + ".bak")
d = json.load(open(cfg, encoding="utf-8"))
d["enabled"] = True
d["base"] = url
d["note"] = "named tunnel base updated automatically"
json.dump(d, open(cfg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
up = r"C:\aios\portal\data\m\urls.json"
u = json.load(open(up, encoding="utf-8"))
u["default_base"] = url
json.dump(u, open(up, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("BASE_UPDATED", url)
'@
    $tmp = 'C:\Temp\m_named_update.py'
    Set-Content -Path $tmp -Value $pyCode -Encoding UTF8
    & $py $tmp $url
}

if ($TunnelToken) {
    Start-Process -FilePath $cf -WindowStyle Hidden -ArgumentList @(
        'tunnel', '--no-autoupdate', 'run', '--token', $TunnelToken) `
        -RedirectStandardOutput $cfLog -RedirectStandardError ($cfLog + '.err')
    Write-Output 'token-mode tunnel started; configure Public Hostname in Cloudflare dashboard:'
    Write-Output ('  service = http://127.0.0.1:8081   (domain = your chosen hostname)')
    Write-Output 'then update base:'
    Write-Output ('  powershell -File portal\setup_named_tunnel.ps1 -Domain <host> -TunnelName update-only')
    exit 0
}

if (-not $Domain) { Write-Output 'need -Domain (e.g. m.example.com)'; exit 2 }
$cert = Join-Path $cfgDir 'cert.pem'
if (-not (Test-Path $cert)) {
    Write-Output 'cert.pem missing: run "cloudflared tunnel login" and authorize first'
    exit 2
}

# 1) create tunnel if missing
$list = & $cf tunnel list --output json 2>$null | Out-String
$exists = $false
try { $arr = $list | ConvertFrom-Json; foreach ($t in $arr) { if ($t.name -eq $TunnelName) { $exists = $true } } } catch { }
if (-not $exists) {
    & $cf tunnel create $TunnelName
}
# 2) route DNS
& $cf tunnel route dns $TunnelName $Domain

# 3) config.yml
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$creds = Get-ChildItem $cfgDir -Filter '*.json' | Where-Object { $_.Name -ne 'cert.json' } | Select-Object -First 1
$tunnelId = ''
if ($creds) { $tunnelId = [System.IO.Path]::GetFileNameWithoutExtension($creds.Name) }
if (-not $tunnelId) { Write-Output 'tunnel credentials not found'; exit 3 }
$yaml = @"
tunnel: $tunnelId
credentials-file: $cfgDir\$tunnelId.json

ingress:
  - hostname: $Domain
    service: http://127.0.0.1:8081
  - service: http_status:404
"@
Set-Content -Path (Join-Path $cfgDir 'config.yml') -Value $yaml -Encoding UTF8

# 4) run (hidden)
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Start-Process -FilePath $cf -WindowStyle Hidden -ArgumentList @(
    'tunnel', '--no-autoupdate', '--config', (Join-Path $cfgDir 'config.yml'), 'run', $TunnelName) `
    -RedirectStandardOutput $cfLog -RedirectStandardError ($cfLog + '.err')
Start-Sleep -Seconds 6

# 5) update base + verify
Update-Base ("https://" + $Domain)
try {
    $r = Invoke-WebRequest -Uri ("https://" + $Domain + "/api/m/health") -UseBasicParsing -TimeoutSec 20
    Write-Output ("VERIFY_OK " + $r.StatusCode)
} catch {
    Write-Output ("VERIFY_FAIL " + $_.Exception.Message + " (check " + $cfLog + ")")
}

