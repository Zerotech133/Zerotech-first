# 每日任务健康监控（20261109）

- 生成：2026-11-09T18:10:06+08:00；dry_run=False；总任务 7，健康 6，告警 1

| 任务 | 状态 | Last Result | 日志时间 | 日志 rc | 日志 age(h) | 产出 | 产出 age(h) |
|---|---|---|---|---|---|---|---|
| ZeroBound_DailyKB（每日知识库）| ✅ | 0 | 2026-11-09T08:35:03+08:00 | 0 | 9.58 | portal\static\kb_learning_digest.json | 9.58 |
| ZeroBound_DailyScreening（每日筛选）| ✅ | 0 | 2026-11-08T20:50:03+08:00 | 0 | 21.33 | research\reports\daily_screening_20261106.json | 21.32 |
| ZeroBound_DailyPush（每日飞书推送）| ✅ | 0 | 2026-11-09T17:45:05+08:00 | 0 | 0.42 | research\reports\kb_daily_push_send_20261109.json | 0.42 |
| ZeroBound_LearningIntake（学习-入库）| ✅ | 0 | 2026-11-09T18:03:16+08:00 | 0 | 0.11 | research\reports\learning_gate_20261109_intake.json | 0.11 |
| ZeroBound_LearningPipeline（学习-流水线）| 🔴 告警 | 0 | 2026-11-03T20:00:02+08:00 | 0 | 142.17 | research\reports\learning_gate_20261109_intake.json | 0.11 |
| ZeroBound_LearningGovernance（学习-治理）| ✅ | 0 | 2026-11-09T02:00:06+08:00 | 0 | 16.17 | research\reports\learning_gate_20261109_intake.json | 0.11 |
| ZeroBound_LearningStandardize（学习-标准化）| ✅ | 0 | 2026-11-09T10:00:01+08:00 | 0 | 8.17 | research\reports\learning_gate_20261109_intake.json | 0.11 |

## 告警

### ZeroBound_LearningPipeline（学习-流水线）
- 日志过期 142.17h

### 告警文本

```
【零界·每日任务健康告警】

⚠️ ZeroBound_LearningPipeline（学习-流水线）
  Last Result: 0
  最后日志: 2026-11-03T20:00:02+08:00
  日志行: 2026-11-03 20:00:02 learning_pipeline_task.ps1 rc_pipeline=0 rc_gate=0
  rc: {'rc_pipeline': 0, 'rc_gate': 0}
  gate: ok=True age=8.17h
  - 日志过期 142.17h

请检查 C:\Temp\zero_daily_*.log 并重跑对应任务；本消息为自动告警。
```

## 口径

- 计划任务：`schtasks /query /tn <name> /v /fo LIST` 解析 Last Run Time/Last Result；
- 日志：`C:\Temp\zero_daily_*.log` 最新 `YYYY-MM-DD HH:mm:ss <task> rc=N` 行；
- 新鲜度：产出文件 mtime 超过 `stale_hours`（默认 30h）即过期；
- 默认 dry-run 不发送；`--send` 才调用 news_collector/send_feishu 发送文本告警。
