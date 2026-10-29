# dirty_watch.ps1 - 脏文件 2h 巡检（L2 批准单配套脚本，2026-10-29）
#
# 行为（只读巡检，不写数据根/不改 git/不重启服务）：
#   1) 调 python tools/check_dirty.py --strict --max-age-hours 4 --json
#   2) 超阈值（跟踪脏文件 >4h 且非已登记 WIP）→ 写日志 + events.py patrol_verdict 事件
#   3) 正常 → 日志追加 OK 行
# 退出码：0=正常/已告警（巡检本身成功）；1=巡检执行失败（脚本异常）
param(
    [int]$MaxAgeHours = 4,
    [string]$LogDir = $env:TEMP
)
$ErrorActionPreference = 'Continue'
$repo = 'C:\项目\老田的AI助手'
$today = Get-Date -Format 'yyyyMMdd'
$log = Join-Path $LogDir ("dirty_watch_{0}.log" -f $today)
$env:DEVTOOL_AGENT = 'GUANYU'
$env:DEVTOOL_IDE = 'DSH-GUANYU'
$env:DEVTOOL_MODEL = 'deepseek-flash'

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

try {
    Push-Location $repo
    $json = & python "$repo\tools\check_dirty.py" --strict --max-age-hours $MaxAgeHours --json 2>&1
    $rc = $LASTEXITCODE
    # 解析超阈值项（check_dirty 的 JSON 结构：{rows, over, known_over, max_age_hours}）
    $overCount = 0
    $overPaths = @()
    try {
        $obj = ($json | Out-String) | ConvertFrom-Json
        if ($obj -and $obj.over) {
            $overCount = @($obj.over).Count
            $overPaths = @($obj.over | ForEach-Object { $_.path })
        }
    } catch {
        $overCount = -1
    }
    if ($rc -eq 1 -and $overCount -gt 0) {
        Write-Log ("WARN 超阈值跟踪脏文件 {0} 个（>{1}h）：{2}" -f $overCount, $MaxAgeHours, ($overPaths -join '; '))
        & python "$repo\tools\events.py" emit --kind patrol_verdict --task 'DIRTY_WATCH_2H' --agent GUANYU --ide DSH-GUANYU --model deepseek-flash --detail ("脏文件巡检告警：{0} 个跟踪脏文件 >{1}h：{2}" -f $overCount, $MaxAgeHours, ($overPaths -join ', ')) | Out-Null
        exit 0
    }
    if ($rc -ne 0 -and $rc -ne 1) {
        Write-Log ("ERROR check_dirty rc={0}（巡检失败，保守告警）" -f $rc)
        & python "$repo\tools\events.py" emit --kind patrol_verdict --task 'DIRTY_WATCH_2H' --agent GUANYU --ide DSH-GUANYU --model deepseek-flash --detail ("脏文件巡检执行失败 rc={0}" -f $rc) | Out-Null
        exit 1
    }
    Write-Log "OK 无超阈值跟踪脏文件"
    exit 0
} catch {
    Write-Log ("FATAL {0}" -f $_.Exception.Message)
    exit 1
} finally {
    Pop-Location
}
