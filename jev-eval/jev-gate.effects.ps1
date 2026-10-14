# Jev 告警网关凭据（zhice.ps1 start 时自动加载）
# JEV_API_KEY: https://console.typesafe.ai/keys
# ALERT_WEBHOOK_URL: 已弃用（改用飞书 LARK_ALERT_OPEN_ID）
# LARK_ALERT_OPEN_ID: 飞书告警收件人 open_id（田峰）
# LARK_ALLOWED_OPERATORS: 允许点击卡片按钮的 open_id 白名单（逗号分隔；不设则默认=收件人本人）
# TUSHARE_TOKEN：公告源（复核门 news_risk 判断用，anns_d 需 2000+ 积分）
# QuantDinger 桥凭据（jev_qd_bridge.py 用，QD 重置密码后请同步更新此处）
# zhice OS v2 后台登录（2026-09-24 P0-4 轮换，弱密码 123456 已作废；改密请走 /auth/users/{uid}/password）

# P2-3 告警阈值可配置化（不设则用代码默认值；调优后去掉注释即可）
# $env:GATE_URGENT_THRESHOLD  = "0.8"    # 紧急度（noul P）≥ 该值才推飞书（默认 0.8）
# $env:GATE_MAX_ALERT_AGE_MIN = "120"    # 只看 N 分钟内新告警（默认 120）
# $env:GATE_POLL_SEC          = "60"     # 轮询兜底周期；NOTIFY 事件驱动模式下仅作兜底（默认 60）

# P2-2（A3 2026-10-07）复核门/桥阈值 env 化（默认=历史值；影子期毕业复评后调优再启用）
# $env:REVIEW_NEWS_THRESHOLD    = "0.7"  # 复核门：news_risk ≥ 该值 → REJECT
# $env:REVIEW_CROWD_THRESHOLD   = "0.7"  # 复核门：crowding=hot 置信 ≥ 该值 → REJECT
# $env:REVIEW_FOCUS_THRESHOLD   = "0.7"  # 复核门：quality=low 置信 ≥ 该值 → FOCUS（加分档）
# $env:REVIEW_WATCH_THRESHOLD   = "0.7"  # 复核门：overlap ≥ 该值 → WATCH（仅标注）
# $env:QD_REVIEW_KEEP_THRESHOLD = "0.35" # QD 桥反向复核：keep_p < 该值 → WARN
# $env:QD_CROWD_THRESHOLD       = "0.7"  # QD 桥反向复核：hot 置信 ≥ 该值 → WARN
# $env:JEV_BACKFILL_COVERAGE_MIN = "0.5" # 回填覆盖率 < 该值 → 飞书告警（JevReviewBackfill 任务）

# P1-2（A3 2026-10-07）：Jev 四件套日志 Python 侧落盘目录（jev_common.log() 双写，
# <mod>.log UTF-8 JSON 单行）。ASCII junction 路径，与拉起上下文 %TEMP% 解耦。
New-Item -ItemType Directory -Path $env:JEV_LOG_DIR -Force | Out-Null

# lark-cli 注入 PATH：计划任务/服务上下文无 Trae 注入的用户 PATH，card-handler 启动即 Popen lark-cli，
# 找不到会立刻崩溃（WinError 2）。自动探测插件目录下最新版本，插件升级后无需改这里。
if (-not (Get-Command lark-cli -ErrorAction SilentlyContinue)) {
  $larkRoot = Join-Path $env:USERPROFILE '.trae-cn\plugins\trae-remote-official\lark'
  if (Test-Path $larkRoot) {
    $larkBin = Get-ChildItem $larkRoot -Directory |
      Where-Object { Test-Path (Join-Path $_.FullName 'bin\lark-cli.exe') } |
      Sort-Object { try { [version]$_.Name } catch { [version]'0.0' } } -Descending |
      Select-Object -First 1
    if ($larkBin) { $env:PATH = "$($larkBin.FullName)\bin;$env:PATH" }
  }
}
