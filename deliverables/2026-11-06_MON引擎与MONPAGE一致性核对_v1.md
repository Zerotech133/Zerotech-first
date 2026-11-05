# 2026-11-06 MON 引擎与 MONPAGE（master 现状）一致性核对

- 状态：v1（只读核对，未改 MON 分支；结论供 11-06 16:30 B 批决策）
- 任务：`K-STOCK-ANALYSIS-MON-20261104`（P1-3，B 批剩余单）
- 核对对象：master `analysis/monitor.py`（来自 MONPAGE 合入 `d551207e3`，当前生产引擎）vs MON 分支 `task/K-STOCK-ANALYSIS-MON-20261104` 的引擎；以及 master 门户 `portal/api_monitor.py`（`/api/monitor/*` 接口层）。

## 一、结论

**一致，MON 留在 11-06 16:30 B 批**，但合入前必须完成分支 rebase（behind 277）并处理唯一冲突 `tools/portal_route_baseline.json`：

1. MON 引擎与 master 门户 `/api/monitor/*` 的**接口字段一致**：公共 API 函数、`summary`/`list_pools`/`list_alerts`/`run_monitor` 返回键、alert 条目字段（`code/rule/severity/title/detail/trade_date/dedupe_key`）全部相同；无破坏性接口变化。
2. MON 引擎的差异集中在 **PIT 口径增强 + pools 文件并发安全 + 文案修正**（均为内部实现），且以 master 树为底、覆盖 MON 引擎后，master 的 portal 测试与两套 monitor 测试全部通过。
3. `git merge-tree --write-tree master task/K-STOCK-ANALYSIS-MON-20261104` 预测：唯一冲突 `tools/portal_route_baseline.json`；`analysis/monitor.py`/`analysis/tests/test_monitor.py` 将自动取 MON 版本，`portal/api_monitor.py` 保持 master 版本不变。

## 二、证据

### 2.1 基线与指纹

| 项 | master（HEAD `e78d1f55c`） | MON 分支（`0f2865065`） |
|---|---|---|
| 分支距离 | — | ahead 12 / behind 277 |
| `analysis/monitor.py` | blob `f27d4026…`，46,836B | blob `9026585b…`，53,035B |
| `analysis/tests/test_monitor.py` | blob `5be2e0f1…` | blob `faaf06a1…`（较 master 基座 +130 行） |
| `portal/api_monitor.py` | blob `13fe863f…` | 分支无此文件（由 MONPAGE 合入时引入，合并不触碰） |
| `tools/portal_route_baseline.json` | 305 路由，generated `2026-11-05T21:41:55` | 298 路由，generated `2026-11-04T15:46:03` |

引擎 diff：`1 file changed, 187 insertions(+), 43 deletions(-)`（不含测试）。

### 2.2 接口字段核对（master 门户 vs 两版引擎）

- `portal/api_monitor.py` 仅调用公共函数：`list_pools / summary / list_alerts / run_monitor / set_pool / add_codes / remove_codes`。
- 两版引擎公共函数集合相同（均 11 个：`is_valid_code, load_pools, list_pools, set_pool, add_codes, remove_codes, evaluate, run_monitor, list_alerts, summary, main`），**无公共函数缺失或签名变化**。
- 运行时探针（同一数据目录、`as_of=2026-11-05`）两版引擎输出**完全一致**：
  - `summary()` keys：`alerts_total, by_severity, last_run, pools`
  - `load_pools()`：dict，keys `watchlist`
  - `list_pools()`：`{"watchlist": 3}`
  - `run_monitor(pool="watchlist", dry_run=True)` keys：`ok, pool, as_of, scanned, alerts, new_alerts, by_severity, items, run_id, dry_run`；`ok=true`
- 合成告警探针（注入 3 条事件，含 1 条未来日期）：两版 alert 条目 keys 均为 `code, dedupe_key, detail, rule, severity, title, trade_date`；`severity/title/trade_date/dedupe_key` 相同；**唯一差异是 detail 文案**：`近 3 个自然日…`（master）→ `近 4 个自然日…`（MON，修正 `EVENT_WINDOW_DAYS` 的含两端计数口径）。

### 2.3 引擎差异清单（非接口）

- **PIT 增强**：注入数据截断（`events/runs` 仅 <= as_of；`market_latest` 不得晚于 as_of）；告警 `trade_date` 越界夹到 as_of 并重建 `dedupe_key`；`EVENT_WINDOW_DAYS` 注释/计数修正为「含两端 4 个自然日」。
- **pools 并发安全**：新增 `_pools_file_lock / _pools_lock_path / _update_pools / _fsync_dir / POOLS_LOCK_*`，原子写+线程锁；`_read_market_latest(as_of_iso)`、`_read_runs(codes, as_of_iso)` 两处私有签名增加 as_of 参数（公共层不变）。
- 未改动：公共 API、返回结构、告警字段、严重度取值、`portal/api_monitor.py`。

### 2.4 测试与合并预测

| 验证 | 命令 | 结果 |
|---|---|---|
| master 基线（MONPAGE 引擎） | `pytest -q portal/tests/test_api_monitor.py analysis/tests/test_monitor.py` | **43 passed** |
| 合并模拟（master 树 + MON 引擎 + MON 测试 + master 旧测试） | 同上 3 文件（含 `test_monitor_master_legacy.py`） | **83 passed** |
| 合并预测 | `git merge-tree --write-tree master task/K-STOCK-ANALYSIS-MON-20261104` | 唯一冲突 `tools/portal_route_baseline.json`；结果树 `4396b081b…` |
| 结果树 blob | `git ls-tree 4396b081b` | `monitor.py=9026585b…`、`test_monitor=faaf06a1…`、`api_monitor.py=13fe863f…`（master 不变）、route baseline=冲突内容 |

模拟方法：临时 worktree `C:\Temp\mon_merge_sim`（master 全树）覆盖 MON 的 `analysis/monitor.py` 与 `analysis/tests/test_monitor.py`（另留一份 master 旧测试为 legacy）后跑测试；核对完成后已移除该临时 worktree，仓库未改动。

### 2.5 证据文件路径

目录 `C:\Temp\mon_consistency_20261106\`：
- `monitor_master.py` / `monitor_monbranch.py` / `monitor_diff_master_vs_monbranch.diff`（引擎全文与 187+/43− diff）
- `test_monitor_master.py` / `test_monitor_monbranch.py`
- `api_monitor_master.py` / `test_api_monitor_master.py`
- `ast_diff.py` / `ast_diff_result.json`（公共 API/签名对比）
- `field_probe.py` + `probe_master.json` / `probe_mon_engine.json`（运行时字段探针）
- `alert_field_probe.py` + `alert_master.json` / `alert_mon.json`（合成告警字段对比；仅 detail 文案差异）
- `route_compare.py` / `route_total.py`（route baseline 基线）
- `tests_baseline_master.log`（43 passed）/ `tests_sim.log` + `tests_sim.rc`（83 passed）
- `merge_tree_predicted_master_vs_mon.txt` / `predicted_tree_blobs.txt`

## 三、B 批执行条件（11-06 16:30）

1. **20:30 前置**：在 MON worktree 内 `git rebase master`（behind 277 → 0）；唯一冲突 `tools/portal_route_baseline.json` 按 master 端基线（305 路由）重生成；`analysis/monitor.py` 保持 MON 版（`9026585b…`），确认 `portal/api_monitor.py` 未被改动；`analysis/tests/test_monitor.py` 保持 MON 版。
2. **验证**：定向 `portal/tests/test_api_monitor.py` + `analysis/tests/test_monitor.py` 通过；PIN dry-run OK；`git merge-tree master task/…` rc=0；分支 fast 门 GATE PASS。
3. **窗口内**：devtask merge（单一执行器；记录 rc/merge SHA；钩子 token 命中证据）；合入后 smoke `/api/monitor/pools`、`/api/monitor/summary`（必要时 `/api/monitor/alerts`），并复核 `analysis/monitor.py` 实际 blob 为 `9026585b…`。
4. 若 rebase 中出现 `portal/api_monitor.py` 被改、公共函数缺失或测试失败 → **退出 B 批转 REVIEW**，记录证据路径并更新本文件与队列文档 §六。

## 四、风险与备注

- 生产引擎版本切换：master 当前运行 MONPAGE 版引擎，MON 合入后将切换为 MON 版（PIT 更严格、detail 文案「近 3」→「近 4」）；schema 不变，前端/接口无需改动。
- MON 分支基线较旧（ahead 12 基于 11-04 15:46），rebase 后须重跑完整 fast 门，旧 PASS 不算数。
- 分支自身测试文件较 master 基座 +130 行，合入后 master 测试文件将被 MON 版替换（模拟验证已覆盖）。
