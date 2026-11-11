#requires -Version 5.1
<#
.SYNOPSIS
  GOVAI 判例签字链 · 批量签名器（HUMAN-ONLY）

.DESCRIPTION
  把指定目录下所有 *.payload.json 逐个交给 tools\sign_approval.py 签名：
    · 只输一次口令（不回显），逐条输出 [i/N] 与结果；
    · 签名文件落在 payload 同目录（默认 <payload 文件>.signature.json，
      gov_ai_case_bridge.py / evolve_sign_payloads.py 均可识别）；
    · 私钥与口令不打印、不落盘；脚本拒绝在 Agent 会话中运行。

.PARAMETER Dirs
  一个或多个待签 payload 目录（例如 C:\项目\_vault\sign_requests\govai_case_20261026）。

.PARAMETER PrivateKey
  principal 私钥路径（默认 C:\项目\_vault\keys\principal_private.pem）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\sign_govai_batch.ps1 `
    -Dirs "C:\项目\_vault\sign_requests\govai_case_20261026"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string[]]$Dirs,
    [string]$PrivateKey = "C:\项目\_vault\keys\principal_private.pem",
    [string]$Python = "C:\Program Files\Python311\python.exe",
    [string]$Repo = "C:\项目\老田的AI助手",
    [string]$PassphraseEnv = "DSH_PRINCIPAL_PASSPHRASE"
)

# --- 人类专用守卫：Agent 会话一律拒绝（与 gen_principal_key.ps1 同纪律）---
# 注意：守卫必须在 ErrorActionPreference=Stop 之前，否则 Write-Error 会变成
# 终止错误（rc=1），掩盖约定的 rc=2 退出码。
if ($env:AGENT_NAME -or $env:DEVTOOL_AGENT) {
    Write-Host "[REFUSE] Agent 环境检测到（AGENT_NAME/DEVTOOL_AGENT）——本脚本仅限本人执行" -ForegroundColor Red
    exit 2
}

$ErrorActionPreference = "Stop"

$signTool = Join-Path $Repo "tools\sign_approval.py"
if (-not (Test-Path -LiteralPath $signTool)) { Write-Error "找不到 sign_approval.py：$signTool"; exit 3 }
if (-not (Test-Path -LiteralPath $PrivateKey)) { Write-Error "缺少私钥：$PrivateKey"; exit 3 }

$payloads = @()
foreach ($d in $Dirs) {
    if (Test-Path -LiteralPath $d) {
        $payloads += Get-ChildItem -LiteralPath $d -Filter "*.payload.json" -File | Sort-Object Name
    } else {
        Write-Warning "目录不存在，跳过：$d"
    }
}
if ($payloads.Count -eq 0) { Write-Error "未找到任何 *.payload.json"; exit 4 }

Write-Host "待签 payload：$($payloads.Count) 个"
$sec = Read-Host -AsSecureString "输入 principal 私钥口令（不回显）"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if (-not $plain) { Write-Error "口令为空"; exit 5 }
Set-Item -Path ("Env:" + $PassphraseEnv) -Value $plain
Remove-Variable plain -ErrorAction SilentlyContinue

$signed = 0
try {
    foreach ($p in $payloads) {
        $signed++
        Write-Host ("[{0}/{1}] {2}" -f $signed, $payloads.Count, $p.Name)
        & $Python $signTool --kind experience_write --payload-file $p.FullName `
            --private-key $PrivateKey --passphrase-env $PassphraseEnv --i-am-human
        if ($LASTEXITCODE -ne 0) { throw "签名失败：$($p.Name) exit=$LASTEXITCODE" }
    }
} finally {
    Remove-Item -Path ("Env:" + $PassphraseEnv) -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "签名完成：$($payloads.Count) 个 payload。"
Write-Host "下一步（Agent 可执行，需公钥 env）："
Write-Host "  `$env:EVOLVE_PRINCIPAL_PUBKEY_FILE = 'C:\项目\_vault\keys\principal_pub.hex'"
Write-Host "  `$env:EVOLVE_SIGNATURE_STRICT = '1'"

