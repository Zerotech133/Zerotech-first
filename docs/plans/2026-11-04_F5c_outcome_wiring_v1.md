# F5c：decision_outcomes 接 outcome_chain 生成 outcome_id

- **工单**：WO-IFS-OUTCOME-WIRE-20261104（IFS v1 F5c）
- **车道**：标准道（只读 sidecar 字段扩展 + 测试；不触执行链/账本）
- **owner**：D1（生产）/ DATA1（登记，IFS §八 待 A0 拍板）
- **分支**：`task/WO-IFS-OUTCOME-WIRE-20261104`（基线 = 当前 master，F5 outcome_chain 已合入）
- **状态**：已实现，待 Lead 接线/合并（本单只 commit、不 merge）
- **范围**：`agent_platform/decision_outcomes.py`（接线 + 回填）、`agent_platform/tests/test_outcome_wiring.py`（新）、既有 pit 测试语义更新、本文件
- **不做**：不改 paper/handoff/watch/portal_api、不启停服务、不写生产数据、不实盘

---

## 一、背景与目标

IFS F5 已交付 `agent_platform/training/outcome_chain.py`（outcome/training_episode 只读链），
但生产侧 outcome 真源 `decision_outcomes.jsonl` 由 `agent_platform/decision_outcomes.py`
回填：行内有 `decision_id/next_close/hit` 等，却**无 `outcome_id`**（ID_CHAIN §3.10「outcome 半有」）。
本单把该回填路径接到 F5 的单一生成方，消除链尾空洞：

```text
receipt(五 ID) → outcome(+outcome_id，六 ID) → training_episode
                    ↑ 本单：decision_outcomes 回填路径也产出 outcome_id（同一公式）
```

验收：1 条 outcome 含六 ID；重复回填幂等（同 run_id 同 outcome_id、不重复追加）；
缺行情显式 null（不伪造 next_close/return/hit）；legacy 行兼容。

---

## 二、变更点（`agent_platform/decision_outcomes.py`）

1. **接线单一生成方**：新增
   `new_outcome_id(trade_id="", run_id="", as_of="", horizon_days=None)`，
   优先调用 `agent_platform.training.outcome_chain.new_outcome_id`；仅当
   `outcome_chain` 不可导入（极端裁剪环境/独立脚本）才回退**同公式**本地实现
   （`out-<yyyymmdd>-<SHA256("<上游>|1")[:8] 大写>`），不另立格式。
2. **六 ID 补齐** `fill_outcome_ids(row, ledger_row=None)`（幂等、只补空值）：
   - `decision_id`（已有 enrich 逻辑）/`handoff_id`/`watch_plan_id`/
     `confirmation_id`/`trade_id`/`outcome_id` **键必在**，缺失显式 `null`；
   - 上游 ID 优先取行内值；其次同 `run_id` 的 shadow_ledger 行若带（WIRE 账本
     未来透传 handoff/watch_plan/confirmation/trade）则透传；都缺 → null
     （**不拿 run_id/report 号冒充上游**）；
   - `ids` 为六 ID 显式视图；已有非空字段一律不覆盖（legacy 兼容）。
3. **回填路径全覆盖**：
   - `compute_outcome()`：新 outcome 行建行即带六 ID（上游键 run_id→decision 日）；
   - `repair_outcomes()`：历史行除 decision_id/model/PIT 外，补 outcome_id/六 ID；
   - `run_backfill()`：写盘条件加入 ID 回填（`ids_repaired>0` 也触发写），
     报告新增 `ids_repaired`/`id_fields`（**旧 `repaired` 语义不变**，=decision/model/PIT）。
4. **可观测**：`summarize()` 新增 `id_chain` 覆盖计数（`outcomes_with_outcome_id`/
   `outcomes_with_decision_id`/`outcomes_with_trade_id`/`six_id_keys`）；CLI `--json`
   输出 `ids_repaired`/`id_fields`；文本行追加 `ids=N`。
5. **幂等**：outcome_id 由 `(上游键, 决策日, horizon=1)` 确定性哈希；同 run_id 重复
   回填得到同一 ID；已有 `outcome_id` 不换（ID 不复用）；第二次回填 `ids_repaired=0`、
   `written=False`、sidecar 字节不变。

### 六 ID 语义

| 字段 | 来源 | 缺失行为 |
|---|---|---|
| `decision_id` | 既有 `decision_id()` 单一真源（engine/decision_policy，回退同公式） | null（不猜） |
| `handoff_id` | shadow_ledger 行透传（未来 WIRE 字段） | null |
| `watch_plan_id` | 同上 | null |
| `confirmation_id` | 同上 | null |
| `trade_id` | 同上（有则作为 outcome_id 上游键） | null |
| `outcome_id` | `outcome_chain.new_outcome_id(trade_id→run_id, date)` | null（缺上游/日期，不伪造） |

---

## 三、测试证据（全部 tmp，不写生产）

```powershell
python -X utf8 -m pytest agent_platform/tests/test_outcome_wiring.py -q
python -X utf8 -m pytest agent_platform/tests/test_decision_outcomes.py `
  agent_platform/tests/test_decision_outcomes_pit.py `
  agent_platform/tests/test_d1_outcome_daily.py `
  agent_platform/tests/test_outcome_chain.py -q
python -m ruff check agent_platform/decision_outcomes.py `
  agent_platform/tests/test_outcome_wiring.py agent_platform/tests/test_decision_outcomes_pit.py
```

实测（2026-11-04）：

- 新增 `test_outcome_wiring.py`：**17 passed**（接线调用 spy、outcome_id 格式/确定性、
  六 ID 齐全/缺失 null、上游透传、legacy 回填、幂等/不覆盖、缺行情不写不伪造、
  只读真实数据零写入、CLI JSON id_chain）。
- 相关回归合计 **100 passed**（含既有 decision_outcomes / pit / daily / outcome_chain）。
- 既有语义更新 1 处：`test_decision_outcomes_pit.py::test_repair_skips_legacy_ledger_without_evidence`
  —— 原断言「legacy 无证据行不写盘」改为「PIT 不凭空补（repaired/pit=0 不变）+
  F5c ID 回填（ids_repaired=1）写盘 + 补完再跑零写字节不变」。理由：本单合同要求
  legacy 行回填 outcome_id，与原测试的 PIT 纪律不冲突。
- 生产数据边界：真实 `agent_platform/data/shadow/{decision_outcomes,shadow_ledger}.jsonl`、
  `data/training/` 前后 digest 不变（worktree 内均 absent）。

---

## 四、接线项（Lead）

| # | 项 | 说明/验收 |
|---|---|---|
| R1 | registry `id_chain.entities.outcome_id.generator.component` | 已登记 `decision_outcomes.py::run_backfill`，本实现即满足；可补 note「内部调 outcome_chain.new_outcome_id（单一生成方）」 |
| R2 | `docs/standards/2026-11-03_ID_CHAIN_v1.md` §3.10/§五 缺口状态 | F5c 后 outcome 行带 outcome_id；建议把「⚠️ 半有」改「outcome_id 已有（F5c）」，并把 §五 `trade_id → outcome_id` 缺口标记收口（本单写域不含 standards，未改） |
| R3 | `tools/d1_outcome_daily.py` 日报展示 | 可选新增 `ids_repaired`/`id_chain.outcomes_with_outcome_id` 展示（tools 非本单写域；数据已在 run_backfill/summarize 里） |
| R4 | `compositions`/`dataset_registry` | outcome 数据集保持既有 `agent_platform/data/shadow/decision_outcomes.jsonl`；F5 receipt 派生 outcome 仍由 `outcome_chain` 显式路径输出（无生产默认写），登记归 DATA1 |
| R5 | 合并窗口 | 本单只 commit；由 Lead 在 merge train 合并，无需计划任务/服务变更 |

---

## 五、回滚预案

| 层 | 动作 | 验证 |
|---|---|---|
| 代码 | `git revert <commit>`：仅改 `decision_outcomes.py`（+测试/文档），无 schema/账本/portal 变更 | 回滚后 `pytest agent_platform/tests/test_decision_outcomes*.py agent_platform/tests/test_d1_outcome_daily.py` rc0；sidecar 旧消费不受影响 |
| 数据 | 本单不写生产；若已运行日常回填，新增字段为**只增不改**（旧字段原样），无需数据迁移；如需回退字段可用 `--no-write` 预演 + 从备份/历史快照恢复 sidecar（该文件为可重建 sidecar：删除后按 shadow_ledger 重算，ID 确定性可复现） | 重跑同 run_id 得到同 outcome_id（幂等） |
| 服务 | 无服务启停、无计划任务、无端口变更 | 端口/任务清单不变 |
| 兼容 | 新增键（`outcome_id`/六 ID/`ids`）为向后兼容增量；旧读取方（calibration/daily/summarize）只读既有键，已回归 | `tools/tests` + `agent_platform/tests` 相关用例 rc0 |

---

## 六、风险与边界

1. **一次性改 sidecar 文件（预期）**：legacy 行首次回填会补空值并原子重写
   `decision_outcomes.jsonl`；不覆盖原字段、不改行序、不重复追加。建议正式跑前
   `--no-write` 预演（报告 ids_repaired）并保留既有 sidecar 备份（`mark`/备份链既有机制）。
2. **`repaired` 语义分离**：旧指标只统计 decision/model/PIT；ID 回填单独计数
   （`ids_repaired`/`id_fields`/`summary.id_chain`），避免读数误判「补 ID=补数据」。
3. **上游 ID 空位**：shadow_ledger 当前无 handoff/watch_plan/confirmation/trade 字段 →
   outcome 中这四项显式 null 是**真实缺口**（非 bug）；未来 WIRE 账本透传后自动填。
4. **decision_id 双格式**（既有 deviation）未在本单收敛；outcome_id 不依赖其格式。
5. **outcome_chain 不可导入时**回退同公式实现，单元测试断言两者一致（格式/确定性）。
6. 不写生产数据/不实盘；所有写操作仅 CLI/测试显式 `--out` 决定。

---

## 七、证据包（DOD）

- 命令 + rc：见 §三（新增 17 passed；相关合计 100 passed；ruff rc0）。
- 产物：`agent_platform/decision_outcomes.py` diff、`agent_platform/tests/test_outcome_wiring.py`、
  本文件；tmp 输出由 pytest `tmp_path` 生成（无生产落盘）。
- 验收勾选：
  - [x] 1 条 outcome 六 ID（decision/handoff/watch_plan/confirmation/trade/outcome 键必在）
  - [x] 重复回填幂等（同 outcome_id、不重复追加、字节不变）
  - [x] 缺行情显式 null（不生成/不写/不伪造）
  - [x] legacy 行兼容（原字段不覆盖）
  - [x] 只读（真实数据零写入、无服务/计划任务）
- 回滚：见 §五；合并：Lead merge train（本单只 commit 不 merge）。
