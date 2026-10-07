# Jev 复核门首期报告提醒（计划任务 2026-09-28 20:35 触发）
# 作用：自动跑 --report 存档 + 飞书消息推送摘要（替代原 Popup 阻塞）
# 注意：路径写死绝对路径（$MyInvocation 在 -File 计划任务下不可靠）
$ErrorActionPreference = 'Continue'
$here = 'E:\老田的AI助手\jev-eval'
$out = "$here\review_data\report-20260928.txt"
$env:PYTHONIOENCODING = 'utf-8'

# 加载飞书配置（LARK_ALERT_OPEN_ID + lark-cli PATH）
. (Join-Path $here 'jev-gate.env.ps1')

python (Join-Path $here 'signal_review_gate.py') --report *> $out

# 摘要截取前 45 行，飞书文本消息长度有限，超长截断
$summary = (Get-Content $out -Encoding UTF8 | Select-Object -First 45) -join "`n"
if ($summary.Length -gt 2800) { $summary = $summary.Substring(0, 2800) + "...`n(已截断)" }

if ($env:LARK_ALERT_OPEN_ID) {
    # PS 5.1 原生传参会剥掉 JSON 内双引号（--content 收到 {text:[..]} 非法），
    # 改为消息写文件 + python subprocess 参数列表包装（backup_alert.py）
    $msgFile = Join-Path $here 'review_data\lark_msg.txt'
    [IO.File]::WriteAllText($msgFile, "Jev 复核门首期报告（9/21+9/22 池 127 条回填后）`n`n$summary`n`n完整报告: $out", (New-Object System.Text.UTF8Encoding($false)))
    & 'C:\Program Files\Python311\python.exe' (Join-Path $here 'backup_alert.py') $msgFile 2>&1 | Out-Null
} else {
    # 无飞书配置时回退到日志（不弹窗，避免计划任务阻塞）
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] LARK_ALERT_OPEN_ID 未配置，报告摘要：`n$summary" |
        Add-Content -LiteralPath "$here\remind_report.log" -Encoding UTF8
}
