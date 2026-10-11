# 来源证据补齐与候选快照 v0.3

本目录保存 2026-10-11 对 `E:\老田的AI助手` 的只读证据登记、候选快照和后续 RAG 基线输出。构建器只读取 E 盘文件、全量索引和证据台账，不修改原始来源，也不启动模型或金融能力。

## 已补齐

- `docs/audit/2026-10-11_产品线全景梳理_产品经理视角_K-STOCK-099.md` → `E-f65e3d180fb3260f`
- `docs/plans/2026-10-11_大平台设计规范与流动方案_K-STOCK-103.md` → `E-73a1a64f2c668c81`
- 两份文档正文声明日期均为 `2026-10-11`；观察回执为 `2026-10-11T23:13:04+08:00`，引用见 `source-evidence-ledger-v0.1.json`。

## 当前阻断

- `docs/plans/2026-10-08_量子双刀策略研究报告.md` 在 E 盘和全量索引中均不存在；`2026-10-09_量子双刀策略研究报告.md` 只作为相关材料保留，不能替代。
- 已于 2026-10-12 重建 E 盘全量索引（267,412 个文件）；`quant_backtest/nine_dim.py` 当前 SHA256 与新索引一致，旧索引已确认过期，`hash_drift` 已解除。
- 7 个来源只有可见回执，没有可证明的 `source_date`，以 `source_date_missing:<path>` 阻断时序门。
- 许可证、本人离线签名、标准定义和交易日历认证仍缺失。

2026-10-12 只读补查结果见 `source-evidence-followup-2026-10-12.json`：精确 2026-10-08 原文和全量索引路径仍不存在，7 个待补日期来源正文未发现可证明的 ISO 日期；快照资格状态保持阻断。

## 重建命令

```powershell
& 'C:\Program Files\Python311\python.exe' tools/rebuild_academy_source_snapshot.py `
  --index 'C:\Users\Administrator\Documents\Codex\2026-10-07\new-chat-2\work\e-drive-inventory-v0.1\E盘数据仓全量清单v0.1.jsonl' `
  --root 'E:\老田的AI助手' `
  --evidence 'E:\老田的AI助手\work\academy-source-evidence-v0.1\source-evidence-ledger-v0.1.json' `
  --output 'E:\老田的AI助手\work\academy-source-evidence-v0.1'
```

输出始终是 `read_only`、`training_eligible=false`、`production_eligible=false`。只有补齐原件、哈希核验、日期/原始证据、许可证、本人离线签名、标准定义和日历认证后，才可重新评审 LoRA；本次不改变训练、模拟成交或交易门禁。
