<#
check-system-acl.ps1 - Scan files whose ACL denies the local SYSTEM account.

Why: files created by some tooling on this workstation can end up with a DACL
that only contains the interactive Administrator (no SYSTEM, no inheritance).
SYSTEM scheduled tasks then fail in confusing ways (tar "Cannot stat",
powershell -File "Access is denied") while the files look fine in Explorer.

Usage:
  powershell -File check-system-acl.ps1 -Path <dir> [-Exclude backups,node_modules] [-Fix]

Read-only by default. -Fix grants FullControl to NT AUTHORITY\SYSTEM on each
broken file using .NET Set-Acl (icacls mis-handles non-ASCII argv paths).
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string[]]$Exclude = @('backups', 'node_modules', '.git', '__pycache__'),
    [switch]$Fix
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Path)) { Write-Error "Path not found: $Path"; exit 2 }

$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    'NT AUTHORITY\SYSTEM', 'FullControl', 'Allow')

$broken = New-Object System.Collections.Generic.List[string]

function Test-AclMissingSystem([string]$file) {
    try {
        $acl = Get-Acl -LiteralPath $file
    } catch {
        return $true   # unreadable ACL is itself a SYSTEM-access problem
    }
    foreach ($a in $acl.Access) {
        if ($a.IdentityReference.Value -match 'SYSTEM|Authenticated Users|Everyone|BUILTIN\\Users') {
            return $false
        }
    }
    return $true
}

$files = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $p = $_.FullName
        -not ($Exclude | Where-Object { $p -like "*\$_\*" })
    }

foreach ($f in $files) {
    if (Test-AclMissingSystem $f.FullName) { $broken.Add($f.FullName) }
}

Write-Output ("broken={0}" -f $broken.Count)
if ($broken.Count -eq 0) { exit 0 }

if (-not $Fix) {
    $broken | ForEach-Object { Write-Output $_ }
    exit 1
}

$failed = 0
foreach ($p in $broken) {
    try {
        $acl = Get-Acl -LiteralPath $p
        $acl.AddAccessRule($systemRule)
        Set-Acl -LiteralPath $p $acl
        Write-Output ("FIXED {0}" -f $p)
    } catch {
        $failed++
        Write-Output ("FIXFAIL {0} :: {1}" -f $p, $_.Exception.Message)
    }
}
if ($failed -gt 0) { exit 1 } else { exit 0 }
