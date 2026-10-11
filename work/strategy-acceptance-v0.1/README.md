# 策略分层验收 v0.1

本目录是“训练候选治理 → 只读 RAG → P1 可观测性 → 策略验收”链路的只读门禁产物。
矩阵覆盖缠论、九维、weak、双刀四种策略，要求每个策略同时提供：

- 可复核来源、来源日期回执和样本 SHA256；
- train/validation/test 时间切分和不跨切分的 `split_group`；
- 同窗口基准和显式手续费、滑点；
- 缺数据拒绝日志；
- 能回放暂态结构的快照字段。

矩阵必须声明完整的 `global_requirements` 和布尔型 `policy` 门禁；证据必须使用
`strategy_acceptance_evidence/v1`，来源日期回执要求带时区时间戳和 SHA256，三组切分必须非空、日期有序且样本 ID 不跨组。来源哈希数量不足以覆盖样本数、`NaN/Inf` 数值、非只读或错误 schema 的 P1 报告都会阻断。CLI 输入异常会写入 `status=error` 的只读报告并返回非零码。

`tools/audit_strategy_acceptance.py` 只读读取矩阵、证据清单和 P1 报告。当前矩阵将标准定义、交易日日历、训练、金融执行和商业化开关全部设为关闭，因此任何策略最多只能得到 `ready_for_shadow_review`，不能获得 `training_eligible`、`production_eligible` 或收费资格。缺证据时固定输出 `blocked`，不会补造排程执行或交易回执。

运行示例：

```powershell
C:\\Program Files\\Python311\\python.exe -X utf8 tools/audit_strategy_acceptance.py `
  --matrix work/strategy-acceptance-v0.1/strategy-acceptance-matrix-v0.1.json `
  --evidence work/strategy-acceptance-v0.1/strategy-evidence-v0.1.json `
  --p1-report work/p1-observability-audit-v0.1.json `
  --output work/strategy-acceptance-v0.1/strategy-acceptance-report-v0.1.json
```

训练、LoRA、金融训练、模拟成交、交易和收费上架在标准定义与日历认证前保持关闭。
