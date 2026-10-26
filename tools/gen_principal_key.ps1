#requires -Version 5.1
<#
.SYNOPSIS
  Generate the principal Ed25519 keypair for governance signatures (HUMAN-ONLY).

.DESCRIPTION
  Produces:
    <OutDir>\principal_private.pem  encrypted PKCS8 private key (passphrase)
    <OutDir>\principal_pub.hex      public key hex (32 bytes) for signature checks

  Discipline (K-STOCK-style, mirrors approval_gate):
    * private key NEVER leaves the human's possession / this script never prints it;
    * this script refuses to run when Agent environment variables are present;
    * default OutDir is <repo>\_vault\keys (private area, not tracked by git).

  Usage (run in YOUR OWN terminal, not from an Agent session):
    powershell -ExecutionPolicy Bypass -File tools\gen_principal_key.ps1
    powershell -ExecutionPolicy Bypass -File tools\gen_principal_key.ps1 -PassphraseFile C:\secure\pass.txt

.PARAMETER OutDir
  Output directory. Default: <repo>\_vault\keys

.PARAMETER PassphraseFile
  File containing the passphrase (trailing newline stripped). Omit to be prompted securely.

.PARAMETER Force
  Overwrite existing key files (back them up first!).
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [string]$PassphraseFile = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# --- human-only guard: refuse inside Agent sessions ---
if ($env:AGENT_NAME -or $env:DEVTOOL_AGENT) {
    Write-Error "Agent environment detected (AGENT_NAME/DEVTOOL_AGENT). This script is HUMAN-ONLY; run it from your own terminal."
    exit 2
}

$repo = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $repo "_vault\keys" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$privPath = Join-Path $OutDir "principal_private.pem"
$pubPath  = Join-Path $OutDir "principal_pub.hex"

if (((Test-Path -LiteralPath $privPath) -or (Test-Path -LiteralPath $pubPath)) -and -not $Force) {
    Write-Error ("Key files already exist: {0} / {1}. Back them up, then re-run with -Force to rotate." -f $privPath, $pubPath)
    exit 3
}

if (-not $PassphraseFile) {
    $sec = Read-Host -AsSecureString "Passphrase for the private key (min 8 chars, not echoed)"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        $passphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
} else {
    $passphrase = (Get-Content -LiteralPath $PassphraseFile -Raw)
    $passphrase = $passphrase.TrimEnd([char]13, [char]10)
}
if (-not $passphrase -or $passphrase.Length -lt 8) {
    Write-Error "Passphrase empty or shorter than 8 characters."
    exit 4
}

$python = $env:DSH_PYTHON
if (-not $python) {
    foreach ($cand in @("C:\xiangmu\py312\python.exe", "C:\..\py312\python.exe", "C:\Program Files\Python311\python.exe", "python")) {
        if ($cand -eq "python" -or (Test-Path -LiteralPath $cand)) { $python = $cand; break }
    }
}
if (-not $python) { $python = "python" }

$env:DSH_KEY_OUT_PRIV = $privPath
$env:DSH_KEY_OUT_PUB  = $pubPath
$env:DSH_KEY_PASSPHRASE = $passphrase
$code = @'
import hashlib, os
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
passphrase = os.environ["DSH_KEY_PASSPHRASE"].encode("utf-8")
priv = Ed25519PrivateKey.generate()
pem = priv.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.BestAvailableEncryption(passphrase),
)
with open(os.environ["DSH_KEY_OUT_PRIV"], "wb") as f:
    f.write(pem)
pub = priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)
with open(os.environ["DSH_KEY_OUT_PUB"], "w", encoding="ascii") as f:
    f.write(pub.hex() + "\n")
print("fingerprint_sha256=" + hashlib.sha256(pub).hexdigest())
'@
# 落临时 .py 文件再执行：Windows PowerShell 5.1 把字符串作为原生参数传给
# python -c 时会吞掉内部双引号（实测 f.write(pub.hex() + "\n") 被改成 + \n，
# 触发 Python SyntaxError）。落文件可完全绕开原生参数转义问题。
$pyPath = Join-Path $env:TEMP ("principal_keygen_{0}.py" -f ([guid]::NewGuid().ToString("N")))
try {
    Set-Content -LiteralPath $pyPath -Value $code -Encoding UTF8
    & $python $pyPath
    if ($LASTEXITCODE -ne 0) { throw "key generation failed (exit=$LASTEXITCODE)" }
} finally {
    Remove-Item -LiteralPath $pyPath -Force -ErrorAction SilentlyContinue
    Remove-Item Env:DSH_KEY_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:DSH_KEY_OUT_PRIV -ErrorAction SilentlyContinue
    Remove-Item Env:DSH_KEY_OUT_PUB -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "OK. Private key : $privPath (KEEP IT OFFLINE / BACKED UP)"
Write-Host "     Public key : $pubPath"
Write-Host "Next (human):"
Write-Host "  1) backup the private key now (password manager / offline media);"
Write-Host "  2) point the governance process at the public key via EVOLVE_PRINCIPAL_PUBKEY_FILE=<public hex path>;"
Write-Host "  3) run: python tools\evolve_sign_payloads.py   to produce the 7 payloads, then sign each with:"
Write-Host "     python tools\sign_approval.py --kind experience_write --payload-file <p> --private-key $privPath --i-am-human"
