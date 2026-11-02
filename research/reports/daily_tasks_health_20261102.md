# 每日任务健康监控（20261102）

- 生成：2026-11-02T18:38:33+08:00；dry_run=True；总任务 7，健康 7，告警 0

| 任务 | 状态 | Last Result | 日志时间 | 日志 rc | 日志 age(h) | 产出 | 产出 age(h) |
|---|---|---|---|---|---|---|---|
| ZeroBound_DailyKB（每日知识库）| ✅ | 0 | 2026-11-02T08:35:05+08:00 | 0 | 10.06 | portal\static\kb_learning_digest.json | 1.1 |
| ZeroBound_DailyScreening（每日筛选）| ✅ | 0 | 2026-11-02T17:35:03+08:00 | 0 | 1.06 | research\reports\daily_screening_20261030.json | 1.04 |
| ZeroBound_DailyPush（每日飞书推送）| ✅ | 0 | 2026-11-02T17:45:03+08:00 | 0 | 0.89 | research\reports\kb_daily_push_send_20261102.json | 0.89 |
| ZeroBound_LearningIntake（学习-入库）| ✅ | 0 | 2026-11-02T18:37:12+08:00 | 0 | 0.02 | research\reports\learning_gate_20261102.json | 0.01 |
| ZeroBound_LearningPipeline（学习-流水线）| ✅ | 0 | 2026-11-02T18:37:49+08:00 | 0 | 0.01 | research\reports\learning_gate_20261102.json | 0.01 |
| ZeroBound_LearningGovernance（学习-治理）| ✅ | 0 | 2026-11-02T18:37:55+08:00 | 0 | 0.01 | research\reports\learning_gate_20261102.json | 0.01 |
| ZeroBound_LearningStandardize（学习-标准化）| ✅ | 0 | 2026-11-02T18:38:01+08:00 | 0 | 0.01 | research\reports\learning_gate_20261102.json | 0.01 |

## 告警

- 无告警 ✅

## 口径

- 计划任务：`schtasks /query /tn <name> /v /fo LIST` 解析 Last Run Time/Last Result；
- 日志：`C:\Temp\zero_daily_*.log` 最新 `YYYY-MM-DD HH:mm:ss <task> rc=N` 行；
- 新鲜度：产出文件 mtime 超过 `stale_hours`（默认 30h）即过期；
- 默认 dry-run 不发送；`--send` 才调用 news_collector/send_feishu 发送文本告警。
