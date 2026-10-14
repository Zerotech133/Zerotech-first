# K-STOCK-358 · bt2_state 混目录分离（346 BUG 土壤清除）

日期：2026-10-14 ｜ 提交：见 git log（本文件与代码同提交） ｜ 作者：主控会话（老田「下一步」驱动，最后一项技术挂账）

## 背景

346 修 `/api/bt2_jobs` 恒 0 BUG 时的判例：bt2_state 目录混放 68 个条目（21 state + 40 历史日志 + 7 事件缓存），「取最近 N 个」未过滤时 state 永远进不了窗口=前端轮询失明。读侧已修，本次把**土壤**清掉：目录职责分离。

## 方案与执行

**目录约定（新）**：
- `quant_backtest/data/sig_events/` —— 事件缓存（7 个 sig_events_*.json 全部迁入）
- `quant_backtest/data/runlogs/` —— 历史跑批日志（40 个 log_*.txt，无代码引用，纯归档）
- `quant_backtest/data/bt2_state/` —— 只剩 21 个 bt2_*.state.json（8091 API 唯一读写对象，路径不变零重启）

**代码路径同步（6 处·落盘脚本精确锚点断言）**：
- backtest_engine_v2.py ×4：SIG_EVENTS_CACHE 模板（227 读/940 读）、v3 缓存读（281）、v2 回退读（284）、v3 读（937）
- market_scan.py ×1：OUT_DIR 写入
- open1016_run.py ×2：STATE_DIR→SIG_EVENTS_DIR 更名+引用
- _327_paired.py ×1：CACHE 读

**迁移**：shutil.move 同盘原子（sig 7 + log 40），断言 sig==7、bt2_state 残留全为 bt2_*.state.json。

## 验证

- 4 文件 py_compile 全过。
- **决定性验证**：一年窗 bt2 真跑（事件缓存走新目录）——结果与 311 锚定基线**逐位一致**（-21.90% / 768 笔 / PF 0.91）=确定性复现，缓存迁移零数据损失。
- 8091 零重启（其读写对象 bt2_state 未动）；门户 /api/bt2_jobs 回归正常。

## 效果

- bt2_state 目录从 68 条目 → 21 条目（纯 state），「取最近 N」类操作不再有污染风险。
- open1016 七臂（10-16 开闸）缓存检查/自愈路径已随迁，开闸无感。
