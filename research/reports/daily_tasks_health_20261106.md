# 每日任务健康监控（20261106）

- 生成：2026-11-06T23:48:11+08:00；dry_run=False；总任务 7，健康 3，告警 4

| 任务 | 状态 | Last Result | 日志时间 | 日志 rc | 日志 age(h) | 产出 | 产出 age(h) |
|---|---|---|---|---|---|---|---|
| ZeroBound_DailyKB（每日知识库）| ✅ | 0 | 2026-11-06T08:35:04+08:00 | 0 | 15.22 | portal\static\kb_learning_digest.json | 15.22 |
| ZeroBound_DailyScreening（每日筛选）| ✅ | 0 | 2026-11-06T17:35:03+08:00 | 0 | 6.22 | research\reports\daily_screening_20261105.json | 6.16 |
| ZeroBound_DailyPush（每日飞书推送）| ✅ | 0 | 2026-11-06T17:45:03+08:00 | 0 | 6.05 | research\reports\kb_daily_push_send_20261106.json | 6.05 |
| ZeroBound_LearningIntake（学习-入库）| 🔴 告警 | 267009 | 2026-11-06T18:03:16+08:00 | 0 | 5.75 | research\reports\learning_gate_20261106_intake.json | 5.75 |
| ZeroBound_LearningPipeline（学习-流水线）| 🔴 告警 | 267009 | 2026-11-03T20:00:02+08:00 | 0 | 75.8 | research\reports\learning_gate_20261106_intake.json | 5.75 |
| ZeroBound_LearningGovernance（学习-治理）| 🔴 告警 | 0 | 2026-11-06T02:00:02+08:00 | 0 | 21.8 | research\reports\learning_gate_20261106_intake.json | 5.75 |
| ZeroBound_LearningStandardize（学习-标准化）| 🔴 告警 | 0 | 2026-11-02T18:38:01+08:00 | 0 | 101.17 | research\reports\learning_gate_20261106_intake.json | 5.75 |

## 告警

### ZeroBound_LearningIntake（学习-入库）
- Last Result=267009
- gate 产出过期 67.43h（>36h）

### ZeroBound_LearningPipeline（学习-流水线）
- Last Result=267009
- 日志过期 75.8h
- gate 产出过期 67.43h（>36h）

### ZeroBound_LearningGovernance（学习-治理）
- gate 产出过期 67.43h（>36h）

### ZeroBound_LearningStandardize（学习-标准化）
- 日志过期 101.17h
- gate 产出过期 67.43h（>36h）

### 告警文本

```
【零界·每日任务健康告警】

⚠️ ZeroBound_LearningIntake（学习-入库）
  Last Result: 267009
  最后日志: 2026-11-06T18:03:16+08:00
  日志行: 2026-11-06 18:03:16 learning_intake_task.ps1 rc_pipeline=0 rc_gate=0
  rc: {'rc_pipeline': 0, 'rc_gate': 0}
  gate: ok=True age=67.43h
  - Last Result=267009
  - gate 产出过期 67.43h（>36h）

⚠️ ZeroBound_LearningPipeline（学习-流水线）
  Last Result: 267009
  最后日志: 2026-11-03T20:00:02+08:00
  日志行: 2026-11-03 20:00:02 learning_pipeline_task.ps1 rc_pipeline=0 rc_gate=0
  rc: {'rc_pipeline': 0, 'rc_gate': 0}
  gate: ok=True age=67.43h
  - Last Result=267009
  - 日志过期 75.8h
  - gate 产出过期 67.43h（>36h）

⚠️ ZeroBound_LearningGovernance（学习-治理）
  Last Result: 0
  最后日志: 2026-11-06T02:00:02+08:00
  日志行: 2026-11-06 02:00:02 learning_governance_task.ps1 rc_pipeline=0 rc_gate=0
  rc: {'rc_pipeline': 0, 'rc_gate': 0}
  gate: ok=True age=67.43h
  - gate 产出过期 67.43h（>36h）

⚠️ ZeroBound_LearningStandardize（学习-标准化）
  Last Result: 0
  最后日志: 2026-11-02T18:38:01+08:00
  日志行: 2026-11-02 18:38:01 learning_standardize_task.ps1 rc_pipeline=0 rc_gate=0
  rc: {'rc_pipeline': 0, 'rc_gate': 0}
  gate: ok=True age=67.43h
  - 日志过期 101.17h
  - gate 产出过期 67.43h（>36h）

请检查 C:\Temp\zero_daily_*.log 并重跑对应任务；本消息为自动告警。
```

## 口径

- 计划任务：`schtasks /query /tn <name> /v /fo LIST` 解析 Last Run Time/Last Result；
- 日志：`C:\Temp\zero_daily_*.log` 最新 `YYYY-MM-DD HH:mm:ss <task> rc=N` 行；
- 新鲜度：产出文件 mtime 超过 `stale_hours`（默认 30h）即过期；
- 默认 dry-run 不发送；`--send` 才调用 news_collector/send_feishu 发送文本告警。
