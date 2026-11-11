# 2026-11-07 SLO 窗口 TOCTOU 复核存档

- 归档时间：2026-11-07 16:05（Asia/Shanghai）
- 对象：第 13 项 `deliverables/d1_q2/slo_window/2026-11-07.json`
- 性质：只读复核证据；**不是验收通过**
- 当前状态：**UNVERIFIED**
- PASS：**0**
- A0 签核：**未签**
- 状态变更：**无，未改任务状态**
- 最终判定：**须以 A0 复核为准**

## 1. 本次结论

2026-11-07 15:54–15:55 连续两次采样，间隔 60 秒：

- 第 1 次：`666,251 B`，mtime `15:50:07.813`，`updated_at=15:50:07`，`samples=1910`，`SHA256=73C898...`
- 第 2 次：`669,728 B`，mtime `15:55:08.056`，`updated_at=15:55:08`，`samples=1920`，`SHA256=E41C67...`
- 两次哈希不同，文件大小增加 3,477 B，samples 增加 10。
- 每次采样内部，`updated_at` 与 mtime 差分别为 **−0.8 秒 / −0.1 秒**，属于同一版本的时间一致。

**解释：支持“文件由采集器每 5 分钟整体替换，stat 与内容读取发生在不同版本之间”的 TOCTOU 解释。**  
这也解释了此前 13:30:06 mtime 与 13:35:06 `updated_at` 的不一致：不是单文件内部矛盾，而是两个版本的采样拼接。

**但这是解释证据，不是验收通过；第 13 项仍为 UNVERIFIED。**

## 2. 可复制 PowerShell 脚本

保存为 `C:\Temp\check_slo_toctou.ps1`：

```powershell
param(
    [string]$Path = 'C:\项目\老田的AI助手\deliverables\d1_q2\slo_window\2026-11-07.json',
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = 'Stop'

function Get-SloSnapshot {
    param([string]$P)

    $f = Get-Item -LiteralPath $P
    $j = Get-Content -LiteralPath $P -Raw -Encoding UTF8 | ConvertFrom-Json
    $updated = [datetimeoffset]::Parse($j.updated_at)
    $mtime = [datetimeoffset]$f.LastWriteTime
    $deltaSec = [math]::Round(($updated - $mtime).TotalSeconds, 1)

    if ([math]::Abs($deltaSec) -le 2) {
        $inSample = 'CONSISTENT'
    } elseif ($deltaSec -gt 2) {
        $inSample = 'UPDATED_AT_AHEAD'
    } else {
        $inSample = 'MTIME_AHEAD'
    }

    [pscustomobject]@{
        SampleAt             = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff')
        Length               = $f.Length
        CreationTime         = $f.CreationTime.ToString('yyyy-MM-dd HH:mm:ss.fff')
        Mtime                = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff')
        UpdatedAt            = $j.updated_at
        Samples              = $j.samples.Count
        Sha256               = (Get-FileHash -LiteralPath $P -Algorithm SHA256).Hash
        UpdatedMinusMtimeSec = $deltaSec
        InSampleConsistency  = $inSample
    }
}

$s1 = Get-SloSnapshot -P $Path
Write-Host "`n--- SAMPLE 1 ---"
$s1 | Format-List

Start-Sleep -Seconds $IntervalSeconds

$s2 = Get-SloSnapshot -P $Path
Write-Host "`n--- SAMPLE 2 ---"
$s2 | Format-List

$sameHash = ($s1.Sha256 -eq $s2.Sha256)
$sameMtime = ($s1.Mtime -eq $s2.Mtime)
$sameUpdated = ($s1.UpdatedAt -eq $s2.UpdatedAt)

if ($sameHash -and $sameMtime -and $sameUpdated -and
    $s1.InSampleConsistency -eq 'CONSISTENT' -and
    $s2.InSampleConsistency -eq 'CONSISTENT') {
    $verdict = 'STABLE_DURING_INTERVAL'
}
elseif (-not $sameHash -and
        $s1.InSampleConsistency -eq 'CONSISTENT' -and
        $s2.InSampleConsistency -eq 'CONSISTENT') {
    $verdict = 'LIVE_WRITER_TOCTOU_SUPPORTED'
}
elseif ($s1.InSampleConsistency -ne 'CONSISTENT' -or
        $s2.InSampleConsistency -ne 'CONSISTENT') {
    $verdict = 'IN_SAMPLE_TIMESTAMP_MISMATCH'
}
else {
    $verdict = 'CHANGED_NEEDS_REVIEW'
}

Write-Host "`n--- COMPARE ---"
[pscustomobject]@{
    IntervalSeconds  = $IntervalSeconds
    SameHash         = $sameHash
    MtimeChanged     = (-not $sameMtime)
    UpdatedAtChanged = (-not $sameUpdated)
    Sample1DeltaSec  = $s1.UpdatedMinusMtimeSec
    Sample2DeltaSec  = $s2.UpdatedMinusMtimeSec
    Verdict          = $verdict
} | Format-List
```

执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Temp\check_slo_toctou.ps1
```

## 3. 本次实际采样完整输出

```text
--- SAMPLE 1 ---
SampleAt             : 2026-11-07 15:54:32.085
Length               : 666251
CreationTime         : 2026-11-07 15:50:07.811
Mtime                : 2026-11-07 15:50:07.813
UpdatedAt            : 2026-11-07T15:50:07+08:00
Samples              : 1910
Sha256               : 73C898EDFFA7E767A692FD4F0165599CB865A7502BEF9A5BA3FBBD994BEFEBAE
UpdatedMinusMtimeSec : -0.8
InSampleConsistency  : CONSISTENT

--- SAMPLE 2 ---
SampleAt             : 2026-11-07 15:55:32.274
Length               : 669728
CreationTime         : 2026-11-07 15:55:08.051
Mtime                : 2026-11-07 15:55:08.056
UpdatedAt            : 2026-11-07T15:55:08+08:00
Samples              : 1920
Sha256               : E41C670DC645CF891602B249CD38D324C42BE00B64DBCB76D87AD9884E17FE12
UpdatedMinusMtimeSec : -0.1
InSampleConsistency  : CONSISTENT

--- COMPARE ---
IntervalSeconds  : 60
SameHash         : False
MtimeChanged     : True
UpdatedAtChanged : True
Sample1DeltaSec  : -0.8
Sample2DeltaSec  : -0.1
Verdict          : LIVE_WRITER_TOCTOU_SUPPORTED
```

## 4. 判定标准

| 判定 | 条件 | 含义 |
|---|---|---|
| `STABLE_DURING_INTERVAL` | 两次哈希、mtime、updated_at 全部相同，且两次内部时间差 ≤2 秒 | 本次间隔内稳定；不能证明长期稳定 |
| `LIVE_WRITER_TOCTOU_SUPPORTED` | 两次哈希/mtime/updated_at 变化，但每次内部时间差 ≤2 秒 | 文件被定期替换，旧差异很可能是不同版本采样拼接 |
| `IN_SAMPLE_TIMESTAMP_MISMATCH` | 任意一次内部 `updated_at` 与 mtime 差 >2 秒 | 单次采样内部矛盾，需查写入/复制/时钟 |
| `UPDATED_AT_AHEAD` | `updated_at - mtime > 2 秒` | 内容时间领先写盘时间，可能 TOCTOU、时钟或复制 |
| `MTIME_AHEAD` | `mtime - updated_at > 2 秒` | 文件被改写/复制后 mtime 晚于内容时间 |
| `MISSING` / `PARSE_ERROR` | 文件不存在或 JSON/updated_at 不可解析 | 不能验收，保持 UNVERIFIED |

## 5. 下一次复核触发条件

满足任一条件即重新采样；否则维持 UNVERIFIED：

1. 第 13 项文件再次变化：mtime、size 或 SHA256 与本次记录不一致。
2. A0 给出书面复核结论，或要求补充证据。
3. 文件责任人提交旧版本、差异说明或时间戳解释。
4. 单次采样内部 `updated_at` 与 mtime 差超过 2 秒。
5. 下一检查点：18:30 DATA1 采集后复核、21:35 W1 次窗预检、次日 07:45/07:50 日报检查。
6. 文件停止更新并形成稳定版本；此时冻结副本并交 A0 验收。

## 6. 采样间隔建议

- **快速一致性检查**：60 秒；适合确认单次采样内部 stat 与 `updated_at` 是否一致。
- **捕捉周期替换**：300 秒（5 分钟）；文件约每 5 分钟更新，60 秒可能恰好落在同一版本。
- **冻结验收前**：至少连续 3 次、每次间隔 300 秒；选择最后一次稳定的哈希，另存冻结副本并记录 SHA256。
- 任何采样都不能替代 A0 签核；文件在持续更新期间不得直接作为验收快照。

## 7. 当前判定

- 第 13 项：**UNVERIFIED**
- PASS：**0**
- A0 签核：**未签**
- 状态：**未修改**
- 最终判定：**以 A0 复核为准**

## 归档校验

- 校验范围：本文件在追加下方 `归档 SHA256` 行之前的全部字节。

<!-- HASH_APPEND_POINT -->

- 归档 SHA256（追加本行前文件）：1D4E8FB69AE31BF75F9517043EF6AACEFEAA407A27345657B3B026851BAEB317
- 说明：本行会改变文件字节，因此上方值为追加前文件哈希；最终文件哈希见交付回执。
