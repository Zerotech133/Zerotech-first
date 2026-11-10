# JEV 校准门控到期复核（2026-11-10）：gate_pass = True

- 工单：K-ORG-20261110-GATECHECK
- 复核时间：2026-11-10 09:00（`tools/jev_audit.py --days 45` 刷新后）
- 数据：`agent_platform/data/jev_audit/latest.json`、`baseline_20261110.json`
- 触发：校准门控到期提醒（原预期 n_days 于 11-10 前后达 20；11-26 另有 20 交易日正式复核提醒）

## 结果

| 指标 | 值 | 阈值/判定 |
|---|---|---|
| data_through | 20261109 | 最新已实现交易日 ✅ |
| stale_trade_days | 1 | 今日 11-10 数据待 20:40 刷新，属正常 |
| n_trade_days | 26 | ≥20 ✅ |
| judged_total | 141,221 | — |
| priced_total | 35,650 | — |
| 方向类 n | 15,390 | — |
| accuracy | 53.0% | — |
| baseline_all_bull | 49.1% | — |
| edge_vs_baseline | +3.9pp | ≥2pp ✅ |
| wilson95_lower | 0.522 | > baseline 0.491 ✅ |
| **gate_pass** | **True** | edge≥2pp AND wilson_lower>baseline AND n_days≥20 |

口径：`gate = edge>=2pp AND wilson_lower>baseline AND n_days>=20`（与 2026-10-30 BATCH1 拍板一致）。

## 前提与说明

1. **n_days 25→26**：10-09（id 20261109）交易日数据于 11-09 20:40 刷入 curated 四表后计入；此前 11-08 审计 n_days=25（其中 6 个历史交易日 20260930/20261008/20261009/20261012/20261013/20261014 由 JEV 数据质量回填补入定价）。
2. **工作树依赖（重要）**：本次审计由 `tools/jev_audit.py` 的工作树版本执行，该文件含他会话 HIT-DEV-20261107 的未提交改动（R0：`--audit-db` / `open_db(read_only)` / `dry_run`，`M tools/jev_audit.py`）。改动本身不涉及门控公式，但**在合并进 master 前，本次 gate_pass 应视为"工作树版本结论"**；建议合并后按同一命令复跑一次留档。
3. `JevCoverageDaily` 仍 rc=2（覆盖率 ~18%，streak 5，CRITICAL）——属回填覆盖不足的独立问题，与门控判定解耦，不阻断本次结论。

## 建议

1. `jev.calibration` 健康卡可转绿（门控三条件均满足）。
2. **下游方向信号接入（briefs/roles/portal/MCP 的方向权重）需老田明确批准后执行**；本复核不自动开启。
3. 合并 `jev_audit.py` 后复跑一次 `python -X utf8 -B tools/jev_audit.py --days 45`，若 gate_pass 仍为 True 则结论升级为"主分支确认"。
4. 11-26 20 交易日正式复核提醒保持不变，届时应复核边沿稳定性（当前 edge +3.9pp、wilson 下界 0.522）。

## 顺带复核（prefilter safe 模式）

- 2026-11-10 09:00 重跑（近 30 天，20,000 样本）：skip 610（3.0%）、误杀 1（占 skip 0.2%）、方向信号捕获率 100.0% → 与 11-09 结果一致，无漂移，维持 `JEV_PREFILTER=safe`。
