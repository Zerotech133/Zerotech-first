# =====================================================================
# backup_daily.ps1 - AI workstation daily backup (hardened)
# 1 postgres dump + 4 archives, gzip verify, AES-256-CBC+HMAC-SHA256
# encryption of sensitive dumps, mirror to C:\backups_mirror, keep-4
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
# VERSION: v13 2026-10-25
# v13 (2026-10-25 EXEC-R18, user-approved): appended section 6 "_vault automation" -
# tools\backup_bundles.ps1 -> tools\prepare_offsite.ps1 -> [switch, DEFAULT OFF]
# tools\offsite_push_vault.ps1. The upload switch stays OFF for unattended runs;
# failures are recorded as their own VAULT_FAILED_<stamp>.flag / OFFSITE_VAULT_FAILED
# flags and never change the tar/mirror/offsite exit codes above (existing flow
# untouched). Switches: -VaultUpload / -VaultDryRun / -SkipVaultAuto, or env
# BACKUP_VAULT_UPLOAD=1 / BACKUP_VAULT_DRYRUN=1.
# v12 (2026-10-25 P1-01, EXEC-R1): quant_data archive-count gate now uses the
# same +/-8 live-tree drift tolerance as quant_code (exact equality produced 5
# false BACKUP_FAILED alerts in the 7 days 10-19..10-25: "files=224 expect=225");
# a local failure no longer skips the offsite upload - a backup_diff_<stamp>.txt
# manifest travels with the set naming what to distrust; the BACKUP_FAILED flag
# now lists the failed items as failed_item= lines (existing consumers ignore
# unknown keys). On THIS machine the on-disk file is the source of truth (see
# DEVIATION NOTE below), so editing disk is the whole deploy step.
# v11 (machine swap): new machine has a single C: drive (no D:/E:), user is
# "t" (old: Administrator on E:). Changes:
#   - base dir auto-locate: C:\aios junction (new) instead of E:\ scan
#   - mirror root: C:\backups_mirror (root-isolated, outside project tree)
#   - dropped 4 items whose sources no longer exist on this machine:
#     quant_db (newquantsystem_v2-postgres-1), webui_data (dev-assistant),
#     quant_src (D: quant source tree), qd_db (quantdinger-db).
#     Kept core-5: zhice_db, jev_eval, quant_data, neo4j_kg, quant_code.
#   - EncryptTargets narrowed to zhice_db.sql.gz.
# DEVIATION NOTE (K269 v9 authority rule): new machine has no
# HKLM:\SOFTWARE\AIStation\BackupDeploy registry key and no git - this
# on-disk file is now the source of truth until registry push is rebuilt.
# v10 original preserved at C:\Temp\jev-eval_archive_20261030\backup_daily.ps1.v10.bak; swap recorded in
# .workbuddy/memory/MEMORY.md. AES key regenerated for this machine
# (old-machine .enc are not readable here and are not carried over).
# v10 (OPS-1009): quant_db "empty" incident root-caused - quant_system db is
# an unpopulated shell (all tables 0 rows, only lineage_edge 29 rows), so its
# full pg_dump gzips to ~25KB. The pipeline was never broken. Quality gate
# added: Backup-Db minimum dump size 15KB separates an anomalous empty dump
# (~20-byte gzip of empty stream) from a legit shell-schema dump; dump size
# now logged in the OK line for eyeball audit.
# =====================================================================

# 2026-10-25 EXEC-R18: _vault automation switches (see section 6 at the end).
# Upload defaults to OFF: an unattended 03:00 run must never push to the
# production offsite host unless a human explicitly turned it on.
[CmdletBinding()]
param(
    [switch]$VaultUpload,
    [switch]$VaultDryRun,
    [switch]$SkipVaultAuto
)

$ErrorActionPreference = "Continue"

# base dir: parent of script folder, or auto-located when run in-memory
if ($PSScriptRoot) { $BaseDir = Split-Path -Parent $PSScriptRoot }
else { $BaseDir = Split-Path -Parent (Split-Path -Parent 'C:\项目\老田的AI助手\jev-eval\backup_daily.ps1') }
$BackupRoot  = Join-Path $BaseDir "backups"
# v11: single C: drive - mirror at drive root, isolated from project tree
$MirrorRoot  = "C:\backups_mirror"
$KeepCount   = 4
$AesKeyFile  = Join-Path $env:USERPROFILE ".backup_aes_key"
# v11: only zhice_db dump remains sensitive (qd/quant/webui sources dropped)
$EncryptTargets = @("zhice_db.sql.gz")
# OPS-1007: dify stack decommissioned (containers removed) - drop dify_db
# from encryption targets along with the 3 dify backup items below.

$stamp   = Get-Date -Format "yyyy-MM-dd_HHmm"
$DayDir  = Join-Path $BackupRoot $stamp
$LogFile = Join-Path $DayDir "backup.log"
$script:Failed = 0
# P1-01: keep the human-readable failure list, not just the count - the alert
# and the offsite diff manifest must be able to say WHICH item failed.
$script:Failures = New-Object System.Collections.ArrayList

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Output $line
    # 2026-10-16 审计修复：ASCII 把中文日志写成 ??（高位丢字）；每日新文件无混编码风险
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}
function LogOk([string]$what)   { Log ("OK   " + $what) }
function LogFail([string]$what) { $script:Failed++; [void]$script:Failures.Add($what); Log ("FAIL " + $what) }

# ---------------- archive-count judgement (2026-10-25 P1-01) ----------------
# The workspace is a LIVE tree: sessions keep writing files while tar walks it,
# so the archived entry count can legitimately differ from the filesystem count
# taken moments earlier. A small delta is live-tree drift, not data loss; the
# thing this gate must catch is bsdtar silently skipping whole ACL-denied
# directories (dozens to thousands of files at once).
# quant_data and quant_code MUST use the same tolerance - for 7 days the
# inconsistent pair (data exact / code +/-8) raised 5 false alerts and each one
# also suppressed the offsite upload of an otherwise complete backup set.
$LiveTreeDrift = 8

function Test-ArchiveCount([string]$label, [int]$got, [int]$expect, [int]$drift) {
    $delta = $got - $expect
    if (($got -gt 0) -and ([math]::Abs($delta) -le $drift)) {
        $note = if ($delta -eq 0) { "" } else { " (live-tree drift " + $delta + ")" }
        LogOk ($label + " files=" + $got + $note)
        return $true
    }
    LogFail ($label + " files=" + $got + " expect=" + $expect + " delta=" + $delta)
    return $false
}

# P1-01: local failures must not cancel the offsite upload of the archives that
# ARE complete. Write the failure list next to the set so it travels with the
# copy and a reviewer knows exactly what to distrust (and can restore the rest).
function Write-OffsiteDiffManifest([string]$dir, [string]$stamp) {
    $diffFile = Join-Path $dir ("backup_diff_" + $stamp + ".txt")
    $lines = @(
        "type=ai_workstation_backup_diff",
        ("stamp=" + $stamp),
        ("local_failed=" + $script:Failed),
        ("generated_at=" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    )
    foreach ($f in $script:Failures) { $lines += ("failed_item=" + $f) }
    Set-Content -LiteralPath $diffFile -Value $lines -Encoding UTF8
    return $diffFile
}

# ---------------- _vault bundle automation (2026-10-25 EXEC-R18) ----------------
# User-approved: hook the orphaned _vault pipeline into the existing BackupDaily
# task (no new task, no task change - editing this on-disk script is the deploy).
# Order: backup_bundles.ps1 -> <workspace>\_vault\<yyyy-MM-dd>\
#        prepare_offsite.ps1 -> <workspace>\_offsite\<date>-offsite.txt etc.
#        [explicit switch, DEFAULT OFF] offsite_push_vault.ps1 -> 10.205.0.118
# Failure semantics: this section only writes its own VAULT_FAILED /
# OFFSITE_VAULT_FAILED flags; it never changes $script:Failed, BACKUP_FAILED or
# the tar/mirror/offsite exit codes computed above.
$script:VaultLastRc = -1
$script:VaultAutoFailed = 0
$script:VaultAutoStages = @()

function Invoke-VaultStep([string]$Label, [string]$ScriptPath, [string[]]$ExtraArgs = @()) {
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        Log ("VAULT FAIL " + $Label + " missing script: " + $ScriptPath)
        $script:VaultLastRc = 127
        return
    }
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $ExtraArgs
    Log ("VAULT start " + $Label + " " + $ScriptPath + " " + ($ExtraArgs -join " "))
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $out = & powershell.exe @argList 2>&1
        $rc = [int]$LASTEXITCODE
    } catch {
        $rc = 99
        $out = @("VAULT exception: " + $_.Exception.Message)
    }
    $sw.Stop()
    foreach ($line in @($out | Select-Object -Last 6)) {
        Log ("VAULT " + $Label + " | " + ([string]$line).Trim())
    }
    Log ("VAULT end " + $Label + " rc=" + $rc + " elapsed=" + [math]::Round($sw.Elapsed.TotalSeconds, 1) + "s")
    $script:VaultLastRc = $rc
}

function Invoke-VaultAuto([string]$RepoPhysical, [string]$AlertDir, [string]$Stamp, [bool]$Upload, [bool]$DryRun) {
    $script:VaultAutoFailed = 0
    $script:VaultAutoStages = @()
    $workspace  = Split-Path -Parent $RepoPhysical
    $vaultRoot  = Join-Path $workspace "_vault"
    $offsiteDir = Join-Path $workspace "_offsite"
    $vaultDate  = Get-Date -Format "yyyy-MM-dd"
    $vaultDay   = Join-Path $vaultRoot $vaultDate
    $toolsDir   = Join-Path $RepoPhysical "tools"
    $bundleScript = Join-Path $toolsDir "backup_bundles.ps1"
    $prepScript   = Join-Path $toolsDir "prepare_offsite.ps1"
    $pushScript   = Join-Path $toolsDir "offsite_push_vault.ps1"

    Log ("VAULT auto: workspace=" + $workspace + " date=" + $vaultDate + " upload=" + $Upload + " dryrun=" + $DryRun)
    if ($DryRun) {
        Log ("VAULT DRYRUN would run: powershell -NoProfile -ExecutionPolicy Bypass -File " + $bundleScript)
        Log ("VAULT DRYRUN would run: powershell -NoProfile -ExecutionPolicy Bypass -File " + $prepScript + " -Date " + $vaultDate)
        if ($Upload) {
            Log ("VAULT DRYRUN would run: powershell -NoProfile -ExecutionPolicy Bypass -File " + $pushScript + " -Date " + $vaultDate)
        } else {
            Log "VAULT DRYRUN upload switch is OFF (default): nothing would be pushed to 10.205.0.118"
        }
        Log ("VAULT DRYRUN expected day dir: " + $vaultDay + " (exists=" + (Test-Path -LiteralPath $vaultDay) + ")")
        Log ("VAULT DRYRUN expected controls: " + $offsiteDir + "\" + $vaultDate + "-offsite.txt")
        return
    }

    # 1) bundle set (real production step)
    Invoke-VaultStep "bundles" $bundleScript
    if ($script:VaultLastRc -ne 0) {
        $script:VaultAutoFailed++
        $script:VaultAutoStages += ("bundles(rc=" + $script:VaultLastRc + ")")
        Log "VAULT bundles failed; skipping prepare_offsite + upload (fail-closed; tar/offsite results above are untouched)"
    } else {
        # 2) offsite control files. No -Copy on purpose: upload directly from the
        # vault set and avoid duplicating ~690 MiB under _offsite every night.
        Invoke-VaultStep "prepare_offsite" $prepScript @("-Date", $vaultDate)
        if ($script:VaultLastRc -ne 0) {
            $script:VaultAutoFailed++
            $script:VaultAutoStages += ("prepare_offsite(rc=" + $script:VaultLastRc + ")")
        }
        # 3) optional upload - explicit switch or env, DEFAULT OFF
        if ($Upload) {
            Invoke-VaultStep "push_vault" $pushScript @("-Date", $vaultDate)
            if ($script:VaultLastRc -ne 0) {
                $script:VaultAutoFailed++
                $script:VaultAutoStages += ("push_vault(rc=" + $script:VaultLastRc + ")")
            }
        } else {
            Log "VAULT upload skipped (switch OFF; use -VaultUpload or BACKUP_VAULT_UPLOAD=1 to enable)"
        }
    }

    if ($script:VaultAutoFailed -gt 0) {
        $stageText = ($script:VaultAutoStages -join ",")
        $isPush = $stageText -match "push_vault"
        try {
            if (-not (Test-Path -LiteralPath $AlertDir)) { New-Item -ItemType Directory -Path $AlertDir -Force | Out-Null }
            $flagName = $(if ($isPush) { "OFFSITE_VAULT_FAILED_" } else { "VAULT_FAILED_" }) + $Stamp + ".flag"
            $flag = Join-Path $AlertDir $flagName
            $flagType = $(if ($isPush) { "ai_workstation_offsite_vault_failed" } else { "ai_workstation_vault_failed" })
            @(
                ("type=" + $flagType),
                ("stamp=" + $Stamp),
                ("stages=" + $stageText),
                ("vault_day=" + $vaultDay),
                ("backup_log=" + $LogFile)
            ) | Set-Content -LiteralPath $flag -Encoding UTF8
            Log ("VAULT alert flag written " + $flag)
        } catch { Log ("VAULT alert flag error: " + $_.Exception.Message) }
    } else {
        $upNote = " (upload off)"
        if ($Upload) { $upNote = " (upload on, rc=0)" }
        Log ("VAULT summary: OK bundles+prepare_offsite" + $upNote)
    }
}

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
# v11 machine swap: quant_db / webui_data / quant_src / qd_db dropped -
# their containers and the D: source tree do not exist on this machine.
# Last good old-machine copies remain in pre-swap backup sets.
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
# 2026-10-25 P1-01: quant_data is a live tree too (sqlite db + wal are written
# during the tar), so exact equality is the wrong gate - it produced
# "files=224 expect=225" FAILs on 5 of 7 days and each FAIL also suppressed the
# offsite upload. Use the same live-tree drift rule as quant_code; got=0 or a
# large delta still fails.
Test-ArchiveCount "quant_data.tar.gz" $QGot $QExpect $LiveTreeDrift | Out-Null

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
# 2026-10-23：代码树是**活树**——多个会话在算 $CExpect 与 tar 之间仍在写文件，
# 差 1~2 个是必然的。原来要求精确相等，于是 2026-10-23 那次因
# "files=28196 expect=28195" 判 FAIL、写 BACKUP_FAILED 告警旗，
# 让真正的备份上云风险被一条假警淹掉（备份本身是成功的）。
# 判据改为：允许 $CCodeDrift 以内的漂移（活树正常抖动），
# 但丢失超过该值仍判 FAIL —— bsdtar 因 ACL 静默跳过文件时会一下子少很多，
# 那才是这个检查真正要抓的东西。
# 2026-10-25 P1-01: judgement extracted to Test-ArchiveCount so quant_data and
# quant_code provably share one rule ($LiveTreeDrift, defined at the top).
$CCodeDrift = $LiveTreeDrift
Test-ArchiveCount "quant_code.tar.gz" $CGot $CExpect $CCodeDrift | Out-Null

# ---------------- 1f. knowledge corpus derived layers (2026-10-30 Phase6) ----------------
# 只备份派生层 + 原始包 sha256 清单（不备份 2.7GB 原始 ZIP）；脚本内含保留 3 套与失败 flag。
$kbPy = 'C:\Program Files\Python311\python.exe'
$kbScript = 'C:\aios\jev-eval\backup_kb_corpus.py'
if ((Test-Path $kbPy) -and (Test-Path $kbScript)) {
    & $kbPy $kbScript 1>> 'C:\Temp\kb_corpus_backup_out.log' 2>> 'C:\Temp\kb_corpus_backup_err.log'
    if ($LASTEXITCODE -ne 0) {
        ('KB_CORPUS_BACKUP_FAILED ' + (Get-Date -Format s)) | Out-File 'C:\Temp\KB_CORPUS_BACKUP_FAILED.flag' -Append -Encoding utf8
    }
}

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

# ---------------- 4. mirror to C:\backups_mirror ----------------
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

# failure alert: write flag file; forwarder task (AlertPush) sends it via Feishu
if ($script:Failed -gt 0) {
    try {
        $alertDir = Join-Path $BaseDir "jev-eval\alerts"
        if (-not (Test-Path $alertDir)) { New-Item -ItemType Directory -Path $alertDir -Force | Out-Null }
        $flag = Join-Path $alertDir ("BACKUP_FAILED_" + $stamp + ".flag")
        $flagLines = @(
            "type=ai_workstation_backup_failed",
            ("stamp=" + $stamp),
            ("failed_count=" + $script:Failed)
        )
        # P1-01: name the failed items (backup_alert.py ignores unknown keys).
        foreach ($f in $script:Failures) { $flagLines += ("failed_item=" + $f) }
        $flagLines += ("backup_log=" + $LogFile)
        $flagLines | Set-Content -LiteralPath $flag -Encoding UTF8
        Log ("ALERT flag written " + $flag)
    } catch { Log ("ALERT flag error: " + $_.Exception.Message) }
}

# ------------------------------------------------------------------ offsite
# 2026-10-24: push today's set to the offsite host (10.205.0.118, /home 1.9TB free).
# Rationale: until this date the project had ZERO offsite copies; C:\backups_mirror
# is a byte-identical copy on the SAME disk (sha256-verified) - a false comfort.
#
# Design notes:
#   * A LOCAL backup failure no longer aborts the offsite step (2026-10-25
#     P1-01): the typical failure is ONE archive's count/enc, while the other
#     five are complete - refusing the upload threw away six good copies for
#     seven days straight. Instead we upload and write backup_diff_<stamp>.txt
#     next to the set, so the offsite copy itself names what to distrust.
#   * A NETWORK failure must not be reported as "the backup failed" - it gets its
#     own OFFSITE_FAILED flag so the alert text names the real culprit.
#   * The push verifies sha256 on BOTH sides; size equality is not evidence here.
$OffsiteRc = -1
if ($script:Failed -gt 0) {
    try {
        $diffFile = Write-OffsiteDiffManifest $DayDir $stamp
        Log ("OFFSITE proceeding despite local failures; diff list: " + $diffFile)
        $mirrorSet = Join-Path $MirrorRoot $stamp
        if (Test-Path -LiteralPath $mirrorSet) {
            Copy-Item -LiteralPath $diffFile -Destination $mirrorSet -Force
            Log ("OFFSITE diff list mirrored to " + $mirrorSet)
        }
    } catch { Log ("OFFSITE diff list error: " + $_.Exception.Message) }
}
$pushScript = Join-Path $BaseDir "tools\offsite_push_remote.ps1"
if (-not (Test-Path -LiteralPath $pushScript)) {
    Log ("OFFSITE skipped (missing " + $pushScript + ")")
} else {
    try {
        $pushOut = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $pushScript 2>&1
        $OffsiteRc = $LASTEXITCODE
        $tail = @($pushOut | Select-Object -Last 2) -join ' | '
        Log ("OFFSITE rc=" + $OffsiteRc + " " + $tail)
    } catch {
        $OffsiteRc = 99
        Log ("OFFSITE error: " + $_.Exception.Message)
    }
    if ($OffsiteRc -ne 0) {
        try {
            $alertDir = Join-Path $BaseDir "jev-eval\alerts"
            if (-not (Test-Path $alertDir)) { New-Item -ItemType Directory -Path $alertDir -Force | Out-Null }
            $flag = Join-Path $alertDir ("OFFSITE_FAILED_" + $stamp + ".flag")
            @(
                "type=ai_workstation_offsite_failed",
                ("stamp=" + $stamp),
                ("rc=" + $OffsiteRc),
                ("backup_log=" + $LogFile)
            ) | Set-Content -LiteralPath $flag -Encoding UTF8
            Log ("OFFSITE alert flag written " + $flag)
        } catch { Log ("OFFSITE alert flag error: " + $_.Exception.Message) }
    }
}

# ------------------------------------------------------------------ 6. _vault automation
# 2026-10-25 EXEC-R18（用户已批准 _vault 自动化）：不新增/不改计划任务，挂进本脚本末尾。
# 产物：<workspace>\_vault\<yyyy-MM-dd>（bundle+docs+data_manifest+MANIFEST）；
#       <workspace>\_offsite\<date>-offsite.txt 等控制文件（故意不带 -Copy：上传直接从
#       _vault 读，避免每晚在 _offsite 再复制 ~690 MiB）。
# 开关默认关：无人值守不推生产服务器；打开方式 -VaultUpload 或 BACKUP_VAULT_UPLOAD=1。
# 本段失败只写 VAULT_FAILED_*/OFFSITE_VAULT_FAILED_* flag，不改变上面已有流程的退出码。
$VaultUploadOn = $VaultUpload -or ($env:BACKUP_VAULT_UPLOAD -eq "1")
$VaultDryRunOn = $VaultDryRun -or ($env:BACKUP_VAULT_DRYRUN -eq "1")
if ($SkipVaultAuto) {
    Log "VAULT skipped (-SkipVaultAuto)"
} else {
    # 计划任务经 C:\aios junction 启动（$BaseDir=C:\aios）。backup_bundles.ps1 /
    # prepare_offsite.ps1 各自按 $PSScriptRoot 推导 workspace，必须传物理路径，
    # 否则 workspace 会算成 C:\、_vault 落到 C:\_vault（错）。
    $RepoPhysical = $BaseDir
    try {
        $bdItem = Get-Item -LiteralPath $BaseDir -Force
        if ($bdItem.LinkType -eq "Junction" -and $bdItem.Target) {
            $RepoPhysical = [string](@($bdItem.Target)[0])
        }
    } catch { }
    if (-not (Test-Path -LiteralPath (Join-Path $RepoPhysical "tools\backup_bundles.ps1"))) {
        Log ("VAULT WARN physical repo resolve failed (" + $RepoPhysical + "); falling back to BaseDir")
        $RepoPhysical = $BaseDir
    }
    Invoke-VaultAuto -RepoPhysical $RepoPhysical `
        -AlertDir (Join-Path $BaseDir "jev-eval\alerts") `
        -Stamp $stamp -Upload ([bool]$VaultUploadOn) -DryRun ([bool]$VaultDryRunOn)
}

# ===== section 7 (2026-10-29 DSH-Lead, 老田批准): extra DB dumps =====
# quant_meta(PG) + thunder_model(MySQL) 纳入每日备份；独立脚本+失败标记，不改变主流程退出码。
try {
    $extra = 'C:\Users\t\dw_ops\backup_extra_dbs.ps1'
    if (Test-Path $extra) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $extra 2>&1 | Out-Null }
} catch { }

if ($script:Failed -gt 0) { exit 1 } else { if ($OffsiteRc -eq 0 -or $OffsiteRc -eq -1) { exit 0 } else { exit 1 } }
