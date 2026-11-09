# 每日任务健康监控（20261103）

- 生成：2026-11-03T18:10:02+08:00；dry_run=False；总任务 7，健康 7，告警 0

| 任务 | 状态 | Last Result | 日志时间 | 日志 rc | 日志 age(h) | 产出 | 产出 age(h) |
|---|---|---|---|---|---|---|---|
| ZeroBound_DailyKB（每日知识库）| ✅ | 0 | 2026-11-03T08:35:02+08:00 | 0 | 9.58 | portal\static\kb_learning_digest.json | 8.45 |
| ZeroBound_DailyScreening（每日筛选）| ✅ | 0 | 2026-11-03T17:35:02+08:00 | 0 | 0.58 | research\reports\daily_screening_20261102.json | 0.57 |
| ZeroBound_DailyPush（每日飞书推送）| ✅ | 0 | 2026-11-03T17:45:01+08:00 | 0 | 0.42 | research\reports\kb_daily_push_send_20261103.json | 0.42 |
| ZeroBound_LearningIntake（学习-入库）| ✅ | 0 | 2026-11-03T18:03:15+08:00 | 0 | 0.11 | research\reports\learning_gate_20261103_intake.json | 0.11 |
| ZeroBound_LearningPipeline（学习-流水线）| ✅ | 0 | 2026-11-02T20:00:01+08:00 | 0 | 22.17 | research\reports\learning_gate_20261103_intake.json | 0.11 |
| ZeroBound_LearningGovernance（学习-治理）| ✅ | 0 | 2026-11-03T02:00:01+08:00 | 0 | 16.17 | research\reports\learning_gate_20261103_intake.json | 0.11 |
| ZeroBound_LearningStandardize（学习-标准化）| ✅ | 0 | 2026-11-02T18:38:01+08:00 | 0 | 23.53 | research\reports\learning_gate_20261103_intake.json | 0.11 |

## 告警

- 无告警 ✅

## 口径

- 计划任务：`schtasks /query /tn <name> /v /fo LIST` 解析 Last Run Time/Last Result；
- 日志：`C:\Temp\zero_daily_*.log` 最新 `YYYY-MM-DD HH:mm:ss <task> rc=N` 行；
- 新鲜度：产出文件 mtime 超过 `stale_hours`（默认 30h）即过期；
- 默认 dry-run 不发送；`--send` 才调用 news_collector/send_feishu 发送文本告警。
