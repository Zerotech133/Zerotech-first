# repo_layout 结构债裁定材料 · 2026-11-11

> 状态：**待老田裁定**（两个选项二选一），本文件只列事实与建议，不动任何文件。
> 触发：`tests/test_repo_layout.py` 两用例持续失败（L-09 红线），全量回归 3 failed 中的 2 个。

## 一、事实

1. **基线**：`_qpzm_rd_20261106/maintenance/repo_layout_baseline.json`（2026-11-06 生成）冻结根级 `.py` **577 个**。
2. **现状**：quant_backtest 根级现有 **668 个 `.py`**，超基线 **91 个**（+`test_buy_filter_strict.py` 触发第二条红线）。
3. **这 91 个的构成**（逐一分组，无未知项）：

| 分组 | 数量 | 代表文件 | 性质 |
|---|---|---|---|
| 一次性研究脚本（`_` 前缀） | 17 | `_p69_fetch_fin.py`、`_k260_regression.py` | 用完即弃，未清理 |
| bt10m 回测系列 | 40 | `bt10m_sweep.py`、`bt10m_frozen.py` | 长期演化中的实验族，互相 import |
| 流水线常驻 | 21 | `pool_daily.py`、`h5_pack.py`、`sector_state.py`、`pipeline_health.py` | **每日 15:02/18:30 定时任务直接引用** |
| 信号/因子核心 | 7 | `chanlun_beichi24.py`、`magic_pool.py`、`vr_leg_3buy.py` | 被流水线 import |
| 工具/运维 | 7 | `ops_backup.py`、`register_pool_daily_task.py`、`ui_kit.py` | 计划任务/部署脚本引用 |
| 其他 | 1 | `pipeline_health.py`（分组归属） | 监控入口 |

4. **git 状态**：91 个里 **89 个已被提交跟踪**，仅 `_pc2_check.py`、`_ui_audit_shots.py` 2 个未跟踪。
5. **引用耦合**：根级 76 个 `.ps1` 部署脚本、Windows 计划任务（PoolCloseReview 15:02 / PoolDailyPipeline 18:30）、
   以及跨文件 import 都按根级路径写死。**物理搬迁任何一个常驻文件都会断链**。

## 二、两个选项（二选一）

### 选项 A：重生成基线，把 91 个全部收编（建议）

```bash
cd quant_backtest && python _qpzm_rd_20261106/maintenance/gen_repo_layout_baseline.py
```
- **理由**：这 91 个不是垃圾——流水线常驻 21 + 信号核心 7 + 运维 7 = **35 个是生产链路活文件**；
  测试红线本意是「管住增量」，而增量已经事实上变成了产线的一部分，冻结旧基线与现实脱钩。
- **配套动作**（收编 ≠ 放弃治理）：
  1. 重生成后立即打 tag（如 `repo-layout-20261111`），红线从新基线起算；
  2. `test_buy_filter_strict.py` 移入 `tests/`（唯一真违规：测试文件就该在 tests/）；
  3. 17 个 `_` 前缀一次性脚本登记进 `archive/` 清单，下次卫生批次集中归档；
  4. 2 个未跟踪文件确认去留。
- **代价**：一次提交；不搬任何文件，零断链风险。

### 选项 B：维持红线，逐步搬迁
- 21 个流水线常驻迁 `pipeline/`、7 个运维迁 `tools/`，同步改 76 个 `.ps1` + 计划任务注册。
- **代价**：跨 100+ 文件的路径重写 + 计划任务重注册，需独立变更窗口 + 全链路验收；
  盘前窗口内做不完，风险与收益不成比。

## 三、裁定请求

选 **A** / **B** / **搁置**（搁置则 `test_repo_layout` 2 个失败保持挂账，不影响其余 2486 项）。
