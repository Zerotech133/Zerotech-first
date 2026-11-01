# 每日任务健康监控（20261101）

- 生成：2026-11-01T23:55:47+08:00；dry_run=False；总任务 3，健康 3，告警 0

| 任务 | 状态 | Last Result | 日志时间 | 日志 rc | 日志 age(h) | 产出 | 产出 age(h) |
|---|---|---|---|---|---|---|---|
| ZeroBound_DailyKB（每日知识库）| ✅ | 0 | 2026-11-01T23:41:20+08:00 | 0 | 0.24 | portal\static\kb_learning_digest.json | 0.03 |
| ZeroBound_DailyScreening（每日筛选）| ✅ | 0 | 2026-11-01T23:40:31+08:00 | 0 | 0.25 | research\reports\daily_screening_20261030.json | 0.03 |
| ZeroBound_DailyPush（每日飞书推送）| ✅ | 0 | 2026-11-01T23:49:22+08:00 | 0 | 0.11 | research\reports\kb_daily_push_send_20261101.json | 0.06 |

## 告警

- 无告警 ✅

## 口径

- 计划任务：`schtasks /query /tn <name> /v /fo LIST` 解析 Last Run Time/Last Result；
- 日志：`C:\Temp\zero_daily_*.log` 最新 `YYYY-MM-DD HH:mm:ss <task> rc=N` 行；
- 新鲜度：产出文件 mtime 超过 `stale_hours`（默认 30h）即过期；
- 默认 dry-run 不发送；`--send` 才调用 news_collector/send_feishu 发送文本告警。
