# Outcome / training_episode 数据契约 v1（IFS v1 F5）

- **编号**：2026-11-03_OUTCOME_TRAIN_契约_v1
- **阶段/车道**：IFS v1 F5（标准道；WO-IFS-OUTCOME / WO-IFS-TRAIN）
- **owner**：D1（生产）｜steward：DATA1（登记）— IFS v1 §八 待 A0 最终拍板，本文件按「D1 生产、DATA1 登记」执行
- **状态**：契约草案 → F5 实现（`agent_platform/training/outcome_chain.py`）
- **配套**：
  - `docs/standards/2026-11-03_ID_CHAIN_v1.md`（`out-` / `ep-` 格式与上下游）
  - `docs/standards/INTERFACE_REGISTRY_v1.yaml`（`outcome.get` / `training.episode.get`）
  - `agent_platform/paper/trade_receipt.py`（上游五 ID 回执）
  - ADGS v1.1（ADGS §7：无证据不算完成）
- **性质**：只读聚合契约；**不自动训练、不写生产数据、不接实盘、不启服务、不改 portal_api**

---

## 一、范围与数据流

```text
paper receipt（WIRE，五 ID：decision/handoff/watch_plan/confirmation/trade）
  → outcome（+ outcome_id，六 ID；T+1 next_close 由调用方注入）
  → training_episode（+ training_episode_id，七 ID；只读聚合）
```

本契约只覆盖 F5 及其消费方（训练/数据仓/校准/复盘）。执行链保持现状：唯一执行入口 =
`trading_bot_flow`（decide/auto_execute）+ `/api/sim/confirm`（IFS-13），本层**只读消费
receipt**，不产生任何订单/成交。

---

## 二、ID 生成规则（IFS-04 同格式）

| 实体 | 格式 | 生成方（本实现） | 上游 | 幂等键 |
|---|---|---|---|---|
| `outcome_id` | `^out-\d{8}-[0-9A-F]{8}$` | `agent_platform/training/outcome_chain.py::new_outcome_id` | `trade_id`（legacy 回填允许 `run_id` 兼容键） | `trade_id`（+`horizon_days`） |
| `training_episode_id` | `^ep-\d{8}-[0-9A-F]{8}$` | 同文件 `new_training_episode_id` | `outcome_id`（+`trade_id`） | `(combo_id, as_of, outcome_ids, trade_ids)` 内容键 |

规则：

1. `outcome_id` 的日期段取**成交日**（receipt `date`），SHA8 = `SHA256("<上游ID>|<horizon>")`
   大写前 8 位。quote 后续回填**不换 ID**（ID 不复用、不漂移，IFS ID_CHAIN §四.6）。
2. `training_episode_id` 日期段取 `as_of`（缺省取 outcome 的 `next_trade_date`/`date`
   最大值，再缺省取生成当日）；内容键同一 → 同一 ID（重跑幂等）。
3. 缺上游 ID/日期 → **不生成 ID（显式 `null`）**，不拼接、不猜。
4. `decision_id` 双格式并存是既有偏差（registry `deviation decision-id-dual-format`）：
   本层只透传/校验，不收敛、不改历史格式。

---

## 三、outcome 记录契约（schema `d1_outcome/v1`，字段向后兼容增量）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` / `schema_version` | str / int | 固定 `d1_outcome/v1` / `1`（对既有 outcome 行是加字段，IFS-09 兼容） |
| `outcome_id` | str\|null | 规范 `out-…`；缺上游/日期为 null |
| `status` | enum | `complete` / `pending_quote` / `invalid` |
| `decision_id/handoff_id/watch_plan_id/confirmation_id/trade_id` | str\|null | 六 ID 中上游五 ID（**键必在**，缺失显式 null） |
| `ids` | object | 六 ID 显式视图（与顶层字段一致） |
| `source` / `source_report_id` | str | 来源（`paper_receipt` / report 号） |
| `symbol/name/action/date/price` | — | 成交事实（只读抄 receipt，不重算） |
| `horizon_days` | int | T+1 默认 1 |
| `next_trade_date` / `next_close` | str\|null / float\|null | 调用方注入行情；缺失 null |
| `next_day_return` | float\|null | `next_close/price - 1`（round 6）；缺失 null |
| `hit` | 0\|1\|null | 方向命中；`WATCH/HOLD` 无方向 → null |
| `quote_status` | enum | `complete` / `missing` |
| `errors` | list | `[{code, message, ref}]`；缺行情=`quote_missing`，缺价=`missing_price`，缺 trade=`missing_trade_id` |
| `paper_only` / `trading_permitted` / `not_a_trade` | bool | 恒 `true / false / true` |

**缺失语义（禁止造数）**：

- 缺 T+1 行情：`status=pending_quote`，`next_trade_date/next_close/next_day_return/hit`
  全 null；**默认不写入** outcome 账本（IFS ID_CHAIN §3.10「缺价格不写」），
  `include_pending=True` 才落显式审计行（字段仍 null）。
- 缺有效成交价 / 缺 `trade_id`：`status=invalid` + `errors`，**永不写入**，不产生半真记录。
- 行情值非法（0/负数/非数值）：视同缺失（pending_quote），不 clamp、不补默认值。

**行情注入来源**（本模块无默认行情源）：调用方提供 callable / mapping / JSON：

```json
{"600519.SH|2026-11-03": {"next_trade_date": "2026-11-04", "next_close": 1530.0}}
```

或 bars 列表（`{"symbol","date","close"}`，取严格晚于成交日的第一条），或单 symbol
`{"600519.SH": {...}}`（date-less，按调用方口径适用于该 symbol）。文件缺失/坏 JSON
→ 全部 pending_quote（不伪造）。

---

## 四、training_episode/v1 契约

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` / `schema_version` | str / int | 固定 `training_episode/v1` / `1` |
| `training_episode_id` | str | `ep-…` 规范 ID |
| `combo_id` | str\|null | 训练组合；来源缺失显式 null（不猜） |
| `data_snapshot` | object | 调用方注入（`as_of/source/...`）；未提供时 `{as_of, source:"unspecified", note}`（显式声明未提供，不造数） |
| `ids` | object | `decision_ids/handoff_ids/watch_plan_ids/confirmation_ids/trade_ids/outcome_ids` 六个聚合数组（排序去重） |
| `paper_trades` | list | receipt 只读视图（五 ID + symbol/action/date/price/verdict/entry_path；无执行字段） |
| `outcomes` | list | outcome 记录（含 `pending_quote`；六 ID 键必在） |
| `errors` | list | `[{code,message,ref}]`：`trade_missing_id` / `outcome_missing_trade` / `outcome_orphan_trade` / `outcome_pending_quote` / 调用方显式登记项 |
| `corrections` | list | **只接受调用方输入**（人工修正工单/标注），不自动生成、不自动应用 |
| `metrics` | object | `trades/outcomes/complete_outcomes/outcome_coverage/directional_n/hit_rate/avg_return/brier/win_rate`；分母 0 → null（不造 0）；`brier` 无 confidence 时恒 null |
| `paper_only` / `trading_permitted` / `not_a_trade` | bool | 恒 `true / false / true` |
| `read_only` / `auto_training` | bool | 恒 `true / false`（**不自动训练**） |

契约校验：`validate_episode(episode)` 返回违规列表（schema/ID 格式/六键/metrics/执行字段/
只读标记）；写盘前必须为 `[]`。

---

## 五、只读与写边界

- **只读聚合**：outcome/episode 由 receipt/outcome 推导，不改 paper ledger、不改
  confirmation、不改 registry/权重、不写 portal_api。
- **写操作必须显式路径**：`append_outcomes(path)` / `write_episode_json(path)` /
  `append_episode(path)`；`path=None/""` 一律 `OutcomeChainError`（本模块**没有**
  生产默认写入路径）。生产接线由 Lead 决定（建议见 §七）。
- **不自动训练**：本模块不产出训练任务、不改模型、不调训练框架；`auto_training=false`。
- **不伪造**：`_now()` 生成时间/`generated_at` 是真实生成时刻；行情、价格、confidence、
  PIT 全部来自输入，缺失显式 null。
- 模块 import 边界：仅标准库；不 import `portal_api`/`trading_bot_flow`/`requests`/
  `subprocess`/网络库（测试 AST 锁定）。

---

## 六、幂等与回填

1. `generate_outcomes(..., existing=)`：按 `trade_id` 跳过已有 outcome（重复 receipt 不重复生成）。
2. `append_outcomes`：按 `outcome_id`/`trade_id` 双键去重（append-only，不更新旧行）。
3. legacy `decision_outcomes.jsonl` 行：`backfill_outcome_ids(rows)` 纯函数回填
   `outcome_id`（trade_id 缺省时用 `run_id` 兼容键）+ 六 ID 键 + `ids` + status；
   已有 `outcome_id` 原样保留；缺日期/上游 → null（记 `unavailable`）。
4. episode：同 `(combo_id, as_of, outcome_ids, trade_ids)` 内容键 → 同
   `training_episode_id`；`append_episode` 按 ID 去重。

---

## 七、接线建议（Lead；本单不改 WIRE 文件）

1. **registry**（`docs/standards/INTERFACE_REGISTRY_v1.yaml`，任务单 owner）：
   - `id_chain.entities.outcome_id.generator.component` 建议改指
     `agent_platform/training/outcome_chain.py::new_outcome_id`（或保留
     `decision_outcomes.py::run_backfill` 但由其 import 本模块生成 ID——二选一，避免双生成方）；
   - `training.episode.get.schema` 由 `training/v1` 对齐为 `training_episode/v1`
     （或登记本契约别名），`ids.produces=[training_episode_id]` 保持；
   - `outcome.get` 的 source 保持 `decision_outcomes.jsonl`；receipt 派生的 outcome
     建议先落新数据集并登记（见下），避免 IFS-01 双写。
2. **composition planned refs**（`compositions/_schema/planned_refs.yaml`，WIRE 文件，勿在本单改）：
   - 既有 `dataset/file/training_episode`（owner D1，due 11-16）：F5 契约落地后补
     `dataset_registry` 登记再移出 planned；
   - 建议新增 `dataset/file/training_outcome`（owner D1，due 11-16）：对应 receipt 派生
     outcome 数据集（`agent_platform/data/training/outcomes.jsonl`，先注册再写）；
   - `job/ifsp-outcome-backfill`：outcome 回填若上计划任务，按 ADGS §2 L2 道五要素审批，
     **不在本单新建计划任务**。
3. **portal_api / 门户**（Lead 接线，本单不写）：
   - `GET /api/d1/training/episodes`（registry `training.episode.get`，只读、portal-token）
     读取 `episodes.jsonl`（有则 200 无则 `data_unavailable` + null，不造数）；
   - 页面 `/d1/training` 需 route baseline 同批登记（F6）。
4. **decision_outcomes.py**（WIRE 文件，本单不改）：后续可 import
   `outcome_chain.new_outcome_id` 在 `run_backfill` 中补 `outcome_id`，消除「outcome 半有」缺口。
5. **task-28/29 联动**：receipt 已带五 ID；本层读取不需要交易链改动。

---

## 八、回滚预案

| 层 | 动作 | 验证 |
|---|---|---|
| 代码 | `git revert <commit>`（仅新增 `agent_platform/training/`、tests、本文件；无对外接口/无 WIRE 文件改动） | revert 后 `pytest agent_platform/tests/test_outcome_chain.py` 不再收集；既有 IFS 测试 rc0 |
| 数据 | 本单不写生产数据；如已按 §七接线生成 `training/outcomes.jsonl` / `episodes.jsonl`，删除/归档该**显式生成**文件即可（append-only 无迁移、无 schema 变更、不影响交易链） | 删除后交易/paper/outcome 既有账本 digest 不变 |
| 接线 | registry/composition 变更走同批 revert（文档/登记类） | `python -X utf8 tools/composition.py validate --all`、route check rc0 |
| 服务 | 本单不启停任何服务；无需回滚动作 | 端口/服务不变 |

**风险点**：outcome 派生依赖调用方行情；行情错误不会写坏账本（只影响生成文件），可重跑
（同 ID 幂等）；episode 是只读派生物，可随时重建。

---

## 九、证据（DOD）

```powershell
# 契约/单元/全链测试（全部 temp 目录，不写生产）
python -X utf8 -m pytest agent_platform/tests/test_outcome_chain.py -q

# 1 条 episode 从 temp receipt/outcome 生成（不落生产）
python -X utf8 -m agent_platform.training.outcome_chain backfill --receipts <tmp>/receipts.jsonl --quotes <tmp>/quotes.json --out <tmp>/outcomes.jsonl --json
python -X utf8 -m agent_platform.training.outcome_chain episode --receipts <tmp>/receipts.jsonl --outcomes <tmp>/outcomes.jsonl --combo-id paper-main --as-of 2026-11-04 --out <tmp>/episode.json --json

# 只读边界
python -X utf8 -m ruff check agent_platform/training agent_platform/tests/test_outcome_chain.py
```

验收勾选：

- [x] `outcome_id` 生成/回填（规范格式 + 确定性幂等 + 缺失 null）
- [x] 六 ID（decision/handoff/watch_plan/confirmation/trade/outcome）贯通
- [x] `training_episode/v1` 契约（全字段 + validate + 只读/不自动训练）
- [x] 缺行情显式 null（pending_quote；默认不写；不伪造 next_close/return/hit）
- [x] 幂等（receipt 重跑不重复、append 去重、episode 同 ID）
- [x] 只读：temp 数据、真实账本零写入、无网络/无服务/无 portal_api 改动
- [x] 1 条 episode 可从 temp receipt/outcome 生成（含 WIRE 全链集成用例）

---

## 十、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-03 | 首版：F5 outcome/training_episode 契约、ID 规则、缺失语义、幂等/回填、接线建议、回滚与证据 |
