# 研发计划执行回执（2026-10-30 12:30）

执行范围：P0 H5 舆情研究 / P0 H14 影子盘 / P1 catalog v17 / P1 去 flaky / P2 监控与数据收尾

## P0-1 H5 舆情情绪研究（pilot 完成，结论：数据不足）
- 数据实测：xq_status 33,303 条（2012-2026），但 `xq_status_classified` 月度分布：2026-08 **18 条** / 2026-09 **54 条** / 2026-10 **229 条**；news.db 102,588 条但仅约 1 个月历史（保留 365 天、历史不可回填）
- pilot（2026-08~10，64 帖/75 观测）：bullish 1 日 CAR **-0.85%（t=-1.65）**、3 日 +0.55%、5 日 +0.68%；bearish 仅 5 个样本（不足）→ 方向不稳健
- 结论：**当前不能做严格四关回测**；H5 状态 **in_progress（数据积累中）**，建议 2027-02 前后（≥3 个月密集样本）重评；急用可另立 H19（公告标题情绪，anns_d 2019+ 量大）
- 交付：`tools/research/h5_sentiment_pilot.py`、`research/reports/H5_sentiment_pilot_20261030.{md,json}`

## P0-2 H14 影子盘（BUG 修复 + 每月运行就绪）
- **修复真实 BUG**：`h14_shadow.py` 的 `END` 写死 `20261029`，11-01 后月度任务不会纳入新数据 → 改为动态 `昨天`（`datetime.now()-1d`）
- 已验证：脚本重跑正常（46 个月、764 只首期持仓、陷阱组 5.0%）
- 定时任务 `H14ShadowMonthly` Ready，下次运行 **2026-11-01 08:30**；滚动评估 `eval.jsonl` 自第 2 期起记录

## P1-1 catalog v17（含新数据源）
- 重建完成：**catalog v17 / 255 表**（v16 253 → +2：`moneyflow_hsgt`、`hk_hold`）；manifest closed 241 / not_applicable 12 / open 1 / missing 1
- registry：physical **255**、catalog sha 一致（`23f163ac…`）、写盘校验全过
- 新增 ODCS 契约 2 份：`agent_platform/catalogs/contracts/moneyflow_hsgt.odcs.yaml`、`hk_hold.odcs.yaml`
- 驾驶舱同步：来源 **68 个**（新源中文名/接入方式/条数/滞后可见），catalog 显示 v17

## P1-2 去 flaky 治理（12 项）
- 隔离复跑结果：**11/12 通过**；唯一确定性失败 `test_minute_router_isolated` 已**根因定位并修复**：
  1) split B1 后 worker 线程在 `api_backtest_bt2` 启动，而测试只 patch `api_backtest` 再导出副本 → `worker_done` 永不置位（原「flaky」实为确定性失败）；
  2) 冷启动只删 `portal_api`/`api_backtest`，未删 `api_backtest_bt2`/`api_backtest_minute`（内存态分钟源注册表残留）→ 冷启动断言失败。
  修复：测试改 patch `api_backtest_bt2._bt2_worker`；冷启动一并 pop 两个模块。验证：`test_minute_router_isolated.py` **1 passed（2s）**、`test_minute_router_process_restart.py` **1 passed**。
  说明：仅改测试代码（test-scope），未改生产逻辑；其余 10 项隔离复跑均通过。

## P2 监控与数据收尾
- 定时任务现状：FxFillDaily(08:20) / AHMonitorDaily(工作日 15:30) / H14ShadowMonthly(每月1日 08:30) / H18RevisionAlert(工作日 08:40) 均 Ready
- 数据仓审计：日频 9 表 + 财报 4 表 + 汇率 2 序列 + 事件型 2 表全部 aligned；catalog/registry 一致
- 待今晚验证：03:00 BackupDaily 首次包含 DB dumps（quant_meta/cainiao）

## 汇总
| 项 | 状态 |
|---|---|
| H5 舆情 pilot | ✅ 完成（数据不足，in_progress） |
| H14 影子盘 | ✅ BUG 修复 + 11-01 自动运行就绪 |
| catalog v17 | ✅ 255 表 / 2 新源 / 2 契约 / registry sha 一致 |
| 去 flaky | ✅ 12 项分诊：11 通过 + 1 修复（分钟 router 两测试均过） |
| 监控/数据收尾 | ✅ 4 任务 Ready；审计全 aligned |