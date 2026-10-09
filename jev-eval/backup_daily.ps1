# =====================================================================
# backup_daily.ps1 - AI workstation daily backup (hardened)
# 4 postgres dumps + 8 archives, gzip verify, AES-256-CBC+HMAC-SHA256
# encryption of sensitive dumps, mirror to D:\backups_mirror, keep-4
# retention (OPS-1008, count-based; see section 5).
# OPS-0930d: added quant_data.tar.gz (quant knowledge state assets) and
# neo4j_kg.tar.gz (neo4j-kg graph volume). Raw vendor dirs stay excluded.
# OPS-1001: added quant_code.tar.gz (workspace code tree, whitelist).
# OPS-1001d F2: Get-AesKey guard - abort + alert if key file missing while
# historical .enc exist (never silently rotate, that would orphan all .enc).
# Deploy/run: scheduled task reads this source from registry key
# HKLM:\SOFTWARE\AIStation\BackupDeploy (part1+part2, utf8 base64) and
# dot-sources it via ScriptBlock - no on-disk execution needed.
# Manual restore of .enc: dot-source then Unprotect-File <src> <dst>
# AUTHORITY (v9 rule, K269): the registry key is the source of truth.
# This on-disk copy is a runtime mirror (rewritten daily by the task);
# git is the audit record. To change this script: edit disk -> push
# registry -> commit git. All three, every time - pushing registry
# without committing git caused the git drift found on 2026-10-08.
# VERSION: v10 2026-10-09
# v10 (OPS-1009): quant_db "empty" incident root-caused - quant_system db is
# an unpopulated shell (all tables 0 rows, only lineage_edge 29 rows), so its
# full pg_dump gzips to ~25KB. The pipeline was never broken. Quality gate
# added: Backup-Db minimum dump size 15KB separates an anomalous empty dump
# (~20-byte gzip of empty stream) from a legit shell-schema dump; dump size
# now logged in the OK line for eyeball audit.
# =====================================================================
$ErrorActionPreference = "Continue"

# base dir: parent of script folder, or auto-located when run in-memory
if ($PSScriptRoot) { $BaseDir = Split-Path -Parent $PSScriptRoot }
else { $BaseDir = (Get-ChildItem 'E:\' -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'jev-eval\jev_gate.py') } | Select-Object -First 1).FullName }
$BackupRoot  = Join-Path $BaseDir "backups"
$MirrorRoot  = "D:\backups_mirror"
# quant source tree root on D: (Chinese dir name via char codes)
$QuantSrc    = "D:\" + [char]0x7F20 + [char]0x8BBA + [char]0x9009 + [char]0x80A1 + "+" + [char]0x76EF + [char]0x76D8
$KeepCount   = 4
$AesKeyFile  = Join-Path $env:USERPROFILE ".backup_aes_key"
$EncryptTargets = @("zhice_db.sql.gz","qd_db.sql.gz","quant_db.sql.gz","webui_data.tar.gz")
# OPS-1007: dify stack decommissioned (containers removed) - drop dify_db
# from encryption targets along with the 3 dify backup items below.

$stamp   = Get-Date -Format "yyyy-MM-dd_HHmm"
$DayDir  = Join-Path $BackupRoot $stamp
$LogFile = Join-Path $DayDir "backup.log"
$script:Failed = 0

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding ASCII
}
function LogOk([string]$what)   { Log ("OK   " + $what) }
function LogFail([string]$what) { $script:Failed++; Log ("FAIL " + $what) }

New-Item -ItemType Directory -Path $DayDir -Force | Out-Null
Log ("Backup dir: " + $DayDir)

# ---------------- crypto helpers ----------------
function Get-AesKey {
    if (Test-Path $AesKeyFile) {
        return ,([Convert]::FromBase64String((Get-Content $AesKeyFile -Raw).Trim()))
    }
    # OPS-1001d F2: key missing while historical .enc exist -> ABORT + alert.
    # A silently generated fresh key would orphan every prior .enc backup.
    $encAny = Get-ChildItem $MirrorRoot -Recurse -Filter "*.enc" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($encAny) {
        $aDir = Join-Path $BaseDir "jev-eval\alerts"
        if (-not (Test-Path $aDir)) { New-Item -ItemType Directory -Path $aDir -Force | Out-Null }
        $fMsg = "FATAL: AES key file missing but .enc archives exist. Do NOT let backup run with a new key - restore key from offline paper copy first. keyfile=" + $AesKeyFile + " sample_enc=" + $encAny.FullName
        Set-Content -LiteralPath (Join-Path $aDir ("BACKUP_KEY_LOST_" + $stamp + ".flag")) -Value $fMsg -Encoding UTF8
        throw "ABORT Get-AesKey: key file missing while .enc exist (flag raised)"
    }
    $k = New-Object byte[] 32
    $rng = [Security.Cryptography.RNGCryptoServiceProvider]::Create()
    $rng.GetBytes($k)
    [IO.File]::WriteAllText($AesKeyFile, [Convert]::ToBase64String($k))
    return ,$k
}

function Protect-File([string]$src, [string]$dst) {
    $key = Get-AesKey
    $aes = New-Object Security.Cryptography.AESCryptoServiceProvider
    $aes.KeySize = 256
    $aes.Key = $key
    $aes.Mode = [Security.Cryptography.CipherMode]::CBC
    $aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
    $aes.GenerateIV()
    $plain = [IO.File]::ReadAllBytes($src)
    $enc = $aes.CreateEncryptor()
    $ct = $enc.TransformFinalBlock($plain, 0, $plain.Length)
    $h = New-Object Security.Cryptography.HMACSHA256
    $h.Key = $key
    $body = New-Object byte[] (16 + $ct.Length)
    [Buffer]::BlockCopy($aes.IV, 0, $body, 0, 16)
    [Buffer]::BlockCopy($ct, 0, $body, 16, $ct.Length)
    $mac = $h.ComputeHash($body)
    $out = New-Object byte[] ($body.Length + 32)
    [Buffer]::BlockCopy($body, 0, $out, 0, $body.Length)
    [Buffer]::BlockCopy($mac, 0, $out, $body.Length, 32)
    [IO.File]::WriteAllBytes($dst, $out)
    $enc.Dispose(); $aes.Dispose(); $h.Dispose()
}

function Unprotect-File([string]$src, [string]$dst) {
    $key = Get-AesKey
    $data = [IO.File]::ReadAllBytes($src)
    if ($data.Length -lt 49) { throw "file too short: $src" }
    $macOff = $data.Length - 32
    $h = New-Object Security.Cryptography.HMACSHA256
    $h.Key = $key
    $body = New-Object byte[] $macOff
    [Buffer]::BlockCopy($data, 0, $body, 0, $macOff)
    $mac = $h.ComputeHash($body)
    for ($i = 0; $i -lt 32; $i++) {
        if ($mac[$i] -ne $data[$macOff + $i]) { throw "HMAC mismatch: $src" }
    }
    $iv = New-Object byte[] 16
    [Buffer]::BlockCopy($data, 0, $iv, 0, 16)
    $ctLen = $macOff - 16
    $ct = New-Object byte[] $ctLen
    [Buffer]::BlockCopy($data, 16, $ct, 0, $ctLen)
    $aes = New-Object Security.Cryptography.AESCryptoServiceProvider
    $aes.KeySize = 256
    $aes.Key = $key
    $aes.Mode = [Security.Cryptography.CipherMode]::CBC
    $aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
    $aes.IV = $iv
    $dec = $aes.CreateDecryptor()
    $pt = $dec.TransformFinalBlock($ct, 0, $ct.Length)
    [IO.File]::WriteAllBytes($dst, $pt)
    $dec.Dispose(); $aes.Dispose(); $h.Dispose()
}
# ---------------- integrity ----------------
function Test-Gzip([string]$path) {
    $fs = $null; $gz = $null
    try {
        # OPS-1011: 0-byte file passes empty GZipStream read -> false OK. Reject first.
        if ((Get-Item -LiteralPath $path -ErrorAction Stop).Length -eq 0) { return $false }
        $fs = [IO.File]::OpenRead($path)
        $gz = New-Object IO.Compression.GZipStream($fs, [IO.Compression.CompressionMode]::Decompress)
        $buf = New-Object byte[] 65536
        while ($gz.Read($buf, 0, 65536) -gt 0) { }
        return $true
    } catch {
        return $false
    } finally {
        if ($gz) { $gz.Dispose() }
        if ($fs) { $fs.Dispose() }
    }
}

# ---------------- docker stream -> file (binary safe) ----------------
function Save-DockerStream([string]$container, [string]$shCmd, [string]$dest) {
    # OPS-1010 root fix (v8): land dump INSIDE container, docker cp out.
    # Old host-side stream wedged dead when docker.exe hung (OPS-1008 0037).
    # playbook #3: docker exec exitcode unreliable - gate on dest file only.
    if (Test-Path $dest) { Remove-Item $dest -Force }
    $errF = Join-Path $env:TEMP "bk_stderr.txt"
    $tmpCont = "/tmp/bk_" + $stamp + "_" + [IO.Path]::GetFileName($dest)
    $p = Start-Process -FilePath "docker" -ArgumentList @("exec", $container, "sh", "-c", ('"' + $shCmd + " > " + $tmpCont + '"')) -RedirectStandardError $errF -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit(900000)) {
        try { $p.Kill() } catch {}
        try { & docker exec $container rm -f $tmpCont 2>$null } catch {}
        return $false
    }
    $p2 = Start-Process -FilePath "docker" -ArgumentList @("cp", ($container + ":" + $tmpCont), $dest) -RedirectStandardError $errF -WindowStyle Hidden -PassThru
    $cpOk = $p2.WaitForExit(600000)
    if (-not $cpOk) { try { $p2.Kill() } catch {} }
    try { & docker exec $container rm -f $tmpCont 2>$null } catch {}
    # OPS-1011: never leave a dest behind on failure (0-byte residue trap, OPS-1008 0048).
    if (-not $cpOk) { Remove-Item $dest -Force -ErrorAction SilentlyContinue; return $false }
    if (-not (Test-Path $dest)) { return $false }
    if ((Get-Item $dest).Length -eq 0) { Remove-Item $dest -Force -ErrorAction SilentlyContinue; return $false }
    return $true
}

function Backup-Db([string]$container, [string]$user, [string]$db, [string]$name) {
    $dest = Join-Path $DayDir $name
    if (Save-DockerStream $container "pg_dump -U $user $db | gzip -c" $dest) {
        # OPS-1009 (v10): minimum-size quality gate. An anomalous dump (auth
        # failure, wrong db, dead pipe) yields a ~20-byte gzip of an empty
        # stream and would pass the >0 gate in Save-DockerStream. A shell
        # schema dump (unpopulated db) is ~25KB gzipped. 15KB separates them.
        $sz = (Get-Item -LiteralPath $dest).Length
        if ($sz -lt 15360) {
            Remove-Item $dest -Force -ErrorAction SilentlyContinue
            LogFail ($name + " dump too small: " + $sz + " bytes (min 15360)")
        } else {
            LogOk ($name + " (" + [math]::Round($sz/1KB) + "KB)")
        }
    }
    else { LogFail $name }
}

function Backup-TarDir([string]$container, [string]$dir, [string]$name) {
    $dest = Join-Path $DayDir $name
    if (Save-DockerStream $container "tar czf - -C $dir ." $dest) { LogOk $name }
    else { LogFail $name }
}

# ---------------- 1. dumps and archives ----------------
# OPS-1007: dify 3 items removed (containers docker-db_postgres-1 /
# docker-api-1 / docker-qdrant-1 decommissioned; last good copy in
# backups/2026-10-05_0300).
Backup-Db "newquantsystem_v2-postgres-1" "quant_user" "quant_system" "quant_db.sql.gz"
Backup-TarDir "dev-assistant" "/app/backend/data" "webui_data.tar.gz"

$qsrc = Join-Path $DayDir "quant_src.tar.gz"
& tar.exe czf $qsrc -C $QuantSrc newquantsystem_v2
if ($LASTEXITCODE -eq 0 -and (Test-Path $qsrc)) { LogOk "quant_src.tar.gz" } else { LogFail "quant_src.tar.gz" }

Backup-Db "zhice-db" "zhice" "zhice_os_v2" "zhice_db.sql.gz"

$jev = Join-Path $DayDir "jev_eval.tar.gz"
& tar.exe czf $jev -C $BaseDir "jev-eval"
if ($LASTEXITCODE -ne 0) {
    $tmpJ = Join-Path $env:TEMP "jevsrc_$stamp"
    & robocopy.exe (Join-Path $BaseDir "jev-eval") $tmpJ /E /B /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
& tar.exe czf $jev -C $env:TEMP "jevsrc_$stamp"
if (Test-Path $tmpJ) { Remove-Item $tmpJ -Recurse -Force -ErrorAction SilentlyContinue }
}
if ($LASTEXITCODE -eq 0 -and (Test-Path $jev) -and ((Get-Item $jev).Length -gt 20000)) { LogOk "jev_eval.tar.gz" } else { LogFail "jev_eval.tar.gz" }

Backup-Db "quantdinger-db" "quantdinger" "quantdinger" "qd_db.sql.gz"

# ---------------- 1b. quant knowledge state assets (OPS-0930d) ----------------
# state assets only; raw vendor dirs (ashare/tdx/tushare/us ~10.7GB) are
# rebuildable via API pulls and excluded from the daily set.
$QDataDir    = Join-Path $BaseDir 'quant_backtest\data'
$QSubdirs = @('semantica','k266_docs','archive','graph_check','distill','nlmulti','nlquery','features','neo4j','crypto')
$QNames   = @()
foreach ($sd in $QSubdirs) { if (Test-Path (Join-Path $QDataDir $sd)) { $QNames += $sd } }
$QFiles = @()
if (Test-Path $QDataDir) {
    # skip transient sqlite shm; keep db + wal (crash-consistent pair)
    $QFiles = @(Get-ChildItem $QDataDir -File | Where-Object { $_.Name -notmatch '-shm$' })
    $QNames += $QFiles.Name
}
# expected file count: bsdtar silently skips unreadable files (ACL trap),
# so audit archived file entries against a fresh filesystem count.
$QExpect = $QFiles.Count
foreach ($sd in ($QSubdirs | Where-Object { Test-Path (Join-Path $QDataDir $sd) })) {
    $QExpect += @(Get-ChildItem (Join-Path $QDataDir $sd) -Recurse -File).Count
}
$qdata = Join-Path $DayDir 'quant_data.tar.gz'
# no -C switch: under the SYSTEM scheduled task tar argv parsing misbinds it
Push-Location $QDataDir; & tar.exe czf $qdata @QNames; Pop-Location
$QGot = 0
& tar.exe tzf $qdata | ForEach-Object { if (-not $_.EndsWith('/')) { $QGot++ } }
if (($QGot -eq $QExpect) -and ($QGot -gt 0)) { LogOk ("quant_data.tar.gz files=" + $QGot) }
else { LogFail ("quant_data.tar.gz files=" + $QGot + " expect=" + $QExpect) }

# ---------------- 1c. neo4j graph volume (OPS-0930d) ----------------
# volume neo4j-kg-data (~3.1G). exec tar while running = file-level snapshot
# akin to power loss; neo4j recovers from tx logs on restore. Nightly window
# is write-idle (kg chain 06:20). docker stream exitcode unreliable
# (playbook #3) - quality gate is the Test-Gzip full decompress below.
$nk = Join-Path $DayDir 'neo4j_kg.tar.gz'
if (Save-DockerStream 'neo4j-kg' 'tar czf - -C /data .' $nk) { LogOk 'neo4j_kg.tar.gz' }
else { LogFail 'neo4j_kg.tar.gz' }

# ---------------- 1e. workspace code tree (OPS-1001) ----------------
# source + launchers of the whole workspace: quant_backtest (minus its
# data dir, covered by quant_data.tar.gz), news_collector, portal, ops,
# pylibs, quantdinger, prototypes, gzh, knowledge_docs, video, jev-eval,
# CJK docs dir and root-level files. Whitelisted out: backups (self),
# dify installs (dify_* archives cover the data), tools (ffmpeg
# redownloadable). tar --exclude path mirrors the expect-count split.
$kg = [string][char]0x77E5+[char]0x8BC6+[char]0x56FE+[char]0x8C31
$CDirs = @('quant_backtest','news_collector','portal','ops','pylibs','quantdinger','prototypes','gzh','knowledge_docs','video','jev-eval',$kg)
$CNames = @(Get-ChildItem $BaseDir -File).Name
$CExpect = @(Get-ChildItem $BaseDir -File).Count
foreach ($d in $CDirs) {
    $dp = Join-Path $BaseDir $d
    if (-not (Test-Path $dp)) { continue }
    $CNames += $d
    if ($d -ceq 'quant_backtest') {
        $CExpect += @(Get-ChildItem $dp -File).Count
        foreach ($cd in @(Get-ChildItem $dp -Directory | Where-Object { $_.Name -cne 'data' })) { $CExpect += @(Get-ChildItem $cd.FullName -Recurse -File).Count }
    } else {
        $CExpect += @(Get-ChildItem $dp -Recurse -File).Count
    }
}
$CodeArc = Join-Path $DayDir 'quant_code.tar.gz'
Push-Location $BaseDir; & tar.exe czf $CodeArc --exclude 'quant_backtest/data' @CNames; Pop-Location
$CGot = 0
& tar.exe tzf $CodeArc | ForEach-Object { if (-not $_.EndsWith('/')) { $CGot++ } }
if (($CGot -eq $CExpect) -and ($CGot -gt 0)) { LogOk ("quant_code.tar.gz files=" + $CGot) }
else { LogFail ("quant_code.tar.gz files=" + $CGot + " expect=" + $CExpect) }

# ---------------- 2. gzip integrity check on all archives ----------------
foreach ($f in @(Get-ChildItem $DayDir -Filter "*.gz")) {
    if (Test-Gzip $f.FullName) { LogOk ("GZIP " + $f.Name) } else { LogFail ("GZIP " + $f.Name) }
}

# ---------------- 3. encrypt sensitive dumps, remove plaintext ----------------
foreach ($name in $EncryptTargets) {
    $src = Join-Path $DayDir $name
    if (-not (Test-Path $src)) { LogFail ("ENC missing " + $name); continue }
    $dst = $src + ".enc"
    try {
        Protect-File $src $dst
        if ((Test-Path $dst) -and ((Get-Item $dst).Length -gt 48)) {
            Remove-Item $src -Force
            LogOk ("ENC " + $name + ".enc")
        } else {
            LogFail ("ENC " + $name)
        }
    } catch {
        LogFail ("ENC " + $name + " " + $_.Exception.Message)
    }
}

# ---------------- 3b. plaintext residue guard (OPS-1008b) ----------------
# Any *.sql.gz left unencrypted after the ENC pass means a whitelist gap or
# a failed ENC - count it as failure (alert fires, exit 1). Never auto-delete.
$PlainLeft = @(Get-ChildItem $DayDir -Filter "*.sql.gz" -File -ErrorAction SilentlyContinue)
foreach ($pf in $PlainLeft) { LogFail ("PLAINTEXT residue " + $pf.Name + " (not encrypted)") }

# ---------------- 4. mirror to D ----------------
try {
    New-Item -ItemType Directory -Path $MirrorRoot -Force | Out-Null
    $mdest = Join-Path $MirrorRoot $stamp
    if (Test-Path $mdest) { Remove-Item $mdest -Recurse -Force }
    Copy-Item -Path $DayDir -Destination $mdest -Recurse
    LogOk ("MIRROR " + $mdest)
} catch {
    LogFail "MIRROR"
}

# ---------------- 5. retention: keep newest KeepCount backup sets ----------------
# OPS-1008: age-based 14d prune let backup sets pile up (~35GB) because
# burst manual runs create 3-4 same-day sets. Count-based cap bounds total
# size regardless of run frequency: sort by stamp desc, keep first N.
$KeepCount2 = $KeepCount
foreach ($root in @($BackupRoot, $MirrorRoot)) {
    if (-not (Test-Path $root)) { continue }
    $sets = @(Get-ChildItem $root -Directory |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_' } |
        Sort-Object Name -Descending)
    foreach ($d in ($sets | Select-Object -Skip $KeepCount2)) {
        Remove-Item $d.FullName -Recurse -Force
        Log ("PRUNE " + $root + "\" + $d.Name)
    }
}

Log ("DONE failed=" + $script:Failed)

# failure alert: write flag file; forwarder task (Administrator) sends it via Feishu
if ($script:Failed -gt 0) {
    try {
        $alertDir = Join-Path $BaseDir "jev-eval\alerts"
        if (-not (Test-Path $alertDir)) { New-Item -ItemType Directory -Path $alertDir -Force | Out-Null }
        $flag = Join-Path $alertDir ("BACKUP_FAILED_" + $stamp + ".flag")
        @(
            "type=ai_workstation_backup_failed",
            ("stamp=" + $stamp),
            ("failed_count=" + $script:Failed),
            ("backup_log=" + $LogFile)
        ) | Set-Content -LiteralPath $flag -Encoding UTF8
        Log ("ALERT flag written " + $flag)
    } catch { Log ("ALERT flag error: " + $_.Exception.Message) }
}

if ($script:Failed -gt 0) { exit 1 } else { exit 0 }
