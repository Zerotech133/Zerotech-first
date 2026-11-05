# 量化面 · 告警规则历史样本研究（P6 §三 口径）

> 口径：数据可溯 + 回测/模拟 + 风险结论；证据 = 数据版本/PIT + 回测产物 + 风险结论 + 五元组；**禁真实交易**。
> 研究问题：监控规则在 PIT `as_of` 口径下的历史样本告警密度与风险特征（是否可作为日常研判输入）。

## 一、方法（可复现）

- 引擎：master 版 `analysis.monitor`（PIT as_of 截断：events/runs/market_latest 均按 as_of 截；告警 trade_date 越界夹到 as_of 并重建 dedupe_key）。
- 调用：`run_monitor(pool="watchlist", as_of=d, dry_run=True)`，d = 2026-11-02 / 11-03 / 11-04 / 11-05。
- 只读：`dry_run=True` 不写 runs/alerts 表（本机 `monitor.db` 因此不存在，见数据版本清单）。
- 股票池：watchlist 3 只；数据：本地 curated parquet + 事件库。
- 脚本与产物：`证据/quant_monitor_sim.py`、`证据/runs_raw.json`、`证据/alerts_flat.csv`、`证据/summary.json`。

## 二、数据版本（PIT 证据）

| 数据 | bytes | mtime | SHA256（前 16 位） |
|---|---|---|---|
| catalog.json | 302,675 | 2026-11-05 13:19:31 | `fe885e8ae4657fb8` |
| warehouse_registry.json | 789,679 | 2026-11-05 13:19:47 | `29876408da231770` |
| xueqiu_info.db | 30,486,528 | 2026-11-05 18:32:15 | `ded562f4e9a7b9d2` |
| analysis_work/monitor/pools.json | 80 | 2026-11-04 03:20:18 | `b526d25a773542d9` |
| analysis/monitor.db | 不存在（dry_run 不落库） | — | — |
| analysis/watchlist.txt | 160 | 2026-11-02 03:25:26 | `a4c966bd21ea6eb9` |

## 三、结果（4 日 / 20 条告警）

| as_of | 告警数 | high | medium | low |
|---|---|---|---|---|
| 2026-11-02 | 8 | 3 | 2 | 3 |
| 2026-11-03 | 5 | 1 | 1 | 3 |
| 2026-11-04 | 5 | 0 | 2 | 3 |
| 2026-11-05 | 2 | 0 | 2 | 0 |
| **合计** | **20** | **4** | **7** | **9** |

规则分布：`data_stale` 9（45%）、`new_event` 6（30%）、`price_move` 4（20%）、`limit_move` 1（5%）；`volume_spike`/`rating_change` 0。

## 四、风险结论

1. **数据时效是第一风险**：`data_stale` 占 45%，告警结论对「数据是否已按交易日落地」高度敏感；离线研究/服务使用前必须记录 as_of 与数据版本（本表）。
2. **事件窗口口径敏感**：MON 分支把「新增事件回看窗口」由 3 天修正为 4 天（含两端），同一份事件在 detail 文案上会差 1 天；对外解释告警时必须带口径版本。
3. **样本不足**：仅 3 只股票池、4 个交易日、20 条告警，不能外推胜率/收益；本研究的结论只到「规则口径可用性与风险特征」。
4. **不构成投资建议、禁真实交易**：本包所有输出为研究样本，不建议据此下单。

## 五、局限与下一步

- 未做：更长历史（30/60 日）、多股票池、告警后收益统计（需另立研究，且不得用于真实交易）。
- 建议：把 as_of + 数据版本哈希写入每日监控运行记录，作为后续复盘基线。
