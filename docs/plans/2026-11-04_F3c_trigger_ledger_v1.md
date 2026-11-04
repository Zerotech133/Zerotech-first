# IFS F3c：watch trigger 独立账本（trigger_id）v1

- **任务单**：`WO-IFS-TRIGGER-LEDGER-20261104`（共享 task-49）
- **车道**：标准道（组合/通道/数据包级；无内核/schema 迁移）
- **日期**：2026-11-04
- **状态**：代码+测试已提交（只 commit，不 merge；生产接线待 Lead）
- **上游契约**：`docs/standards/2026-11-03_ID_CHAIN_v1.md` §3.7（trigger_id 规划）、
  §3.8（confirmation 需带四 ID+trigger_id）；`docs/standards/2026-11-03_F1接口Schema接线说明_v1.md`
  （`trigger_event/v1`、`watch.trigger.create`）

## 一、背景与缺口

IFS F3 原实现中 `watch_trigger_adapter` 直接调 `trading_bot_flow.create_confirmation`，
**跳过独立 trigger 账本**：ID_CHAIN 里 `watch_plan_id → trigger_id` 为「❌ 缺」，
`trigger_id` 未生成，confirmation 只有 `source_report_id`（后经 F4 补三来源 ID）。
本单补 F3c：让每次 armed watch_plan 触发先落一条 `trigger_event/v1`，再把
`trigger_id` 写回 confirmation，形成可审计五 ID 链。

## 二、交付物

| 文件 | 变化 |
|---|---|
| `agent_platform/watch_trigger_ledger.py` | 新增：trigger_id 生成、JSONL 账本读写、幂等、confirmation 回写、CLI 只读查看 |
| `agent_platform/watch_trigger_adapter.py` | `trigger_watch_plan(..., trigger_ledger=None, quote_snapshot=None, now=None)`：配置账本后落账+回写 trigger_id；未配置保持旧行为 |
| `agent_platform/watch_plan_consumer.py` | `trigger_plan(..., trigger_ledger=None)`：透传账本、plan store 持久化 trigger_id、返回 trigger_id/ids、幂等分支补齐；CLI 增 `--trigger-ledger` |
| `agent_platform/tests/test_watch_trigger_ledger.py` | 22 tests：ID 格式/确定性、落行/幂等/冲突/显式 path、confirmation 回写、adapter/consumer 五 ID 链、无执行字段、坏账本 fail-closed |
| `docs/plans/2026-11-04_F3c_trigger_ledger_v1.md` | 本文件（设计/证据/接线/回滚） |

## 三、ID 与账本契约

### 3.1 trigger_id

```text
trigger_id = trg-<yyyymmdd>-<hex6>
hex6       = SHA256("<watch_plan_id>|<canonical_json(quote_snapshot)>")[:6]（小写）
```

- 日期取触发时刻本地时区；
- 同 plan + 同行情快照 + 同一天 → 确定性同 ID（可复现）；
- 唯一性与幂等由账本层兜底：同 `watch_plan_id` 重复触发返回既有事件，**不重复落行**；
- 格式锁：`^trg-\d{8}-[0-9a-f]{6}$`（与 ID_CHAIN §3.7 一致）。

### 3.2 trigger_event/v1（`agent_platform/data/watch_triggers.jsonl`）

```json
{
  "schema": "trigger_event/v1",
  "schema_version": 1,
  "trigger_id": "trg-20261104-8497e0",
  "watch_plan_id": "wp-...",
  "handoff_id": "d1h-...",
  "decision_id": "d1-...",
  "confirmation_id": "CFM-...",
  "quote_snapshot": {"price": 1500.0},
  "created_at": "2026-11-04T10:00:00+08:00",
  "paper_only": true,
  "not_a_trade": true
}
```

- 五 ID 链：`decision_id → handoff_id → watch_plan_id → trigger_id → confirmation_id`；
  上游缺失显式 `null`，不拿 report_id/run_id 冒充；
- 兼容现状：`handoff_id` 取 `d1_handoff.receipt_id`（当前回执主键即 handoff ID；
  若后续回执引入独立 `handoff_id` 字段，账本侧只需换取值，ID 格式不变）；
- 禁执行字段（order/qty/broker/execute/trade_id/fill/account）——`validate_event` 拦截；
- append-only；坏行 fail-closed（`trigger_ledger_corrupt`），不静默丢账。

### 3.3 写边界（关键）

- **无生产默认写入**：`ledger_path()` 只认显式 `path` 或环境变量
  `WATCH_TRIGGER_LEDGER_PATH`；两者皆无 → `trigger_ledger_path_required`，不落盘；
  模块常量 `DEFAULT_LEDGER` 仅为文档/显式引用，函数绝不隐式使用；
- 唯一写操作 = 向显式账本 append 一行 + 把 `trigger_id` 原子写回 confirmation JSON
  （confirmation 既有四 ID/字段原样保留，不执行/不改 paper 账）；
- 未接线时 adapter/consumer 保持旧行为：只建 pending 确认，confirmation 无 trigger_id。

## 四、接线方式（Lead）

1. **配置账本（生产启用点）**：在调用 watch_plan consumer 触发链的环境/计划任务中设置
   `WATCH_TRIGGER_LEDGER_PATH=agent_platform/data/watch_triggers.jsonl`（或调用方显式传
   `trigger_ledger=` / CLI `--trigger-ledger`）。未配置前本单不产生任何生产写入。
2. **触发调用链**：`watch_plan_consumer.trigger_plan(plan_id, ..., trigger_ledger=...)`
   → `watch_trigger_adapter.trigger_watch_plan(..., trigger_ledger=...)`
   →（同一次调用）落 trigger 账本 → 回写 confirmation；
   定时/巡检调用入口沿用 F3 现状（本单不新增计划任务）。
3. **registry 建议**（`docs/standards/INTERFACE_REGISTRY_v1.yaml`，非本单写域）：
   - `watch.trigger.create` 已登记 `trigger_event/v1` draft；建议 schema_file 指向
     `watch_plan_v1.schema.json#/$defs/trigger_event` 并在本单落地后升级 draft→active；
   - 建议登记 `trigger_id` 的 generator = `agent_platform/watch_trigger_ledger.py::new_trigger_id`
     （消除「半有」状态）；
   - `trading_bot_confirmation/v2` 建议在 F4 后续升 v3，把 `trigger_id` 列为**一等字段**
     （当前是兼容追加字段；若 flow 未来做严格 schema，需同批迁移）。
4. **composition 建议**（`compositions/lusu-d1-flow/manifest.yaml`，非本单写域）：
   - data 增加 `dataset/file/watch_triggers`（真实文件：`agent_platform/data/watch_triggers.jsonl`），
     待 Lead 按 dataset_registry 登记后移出 planned；
   - status data_refs 增加 `watch_trigger`；notes 记录 F3c 落地。
5. **门户/API 建议**：`/api/sim/confirmation(s)` 与确认卡片展示 `trigger_id`；
   `api_sim_confirm` 列表按 `trigger_id` 可查（仍只读）。
6. **换血**：常驻服务若 import 本模块，按「修复写盘≠生效」红线在维护窗口重启；
   本单未启停任何服务。

## 五、验收证据（本 worktree，全部 tmp 数据）

```powershell
python -X utf8 -m pytest agent_platform/tests/test_watch_trigger_ledger.py -q
# 22 passed

# 相关回归
python -X utf8 -m pytest agent_platform/tests/test_ifs_handoff.py `
  agent_platform/tests/test_watch_trigger_adapter.py `
  agent_platform/tests/test_watch_feed_consumer.py `
  agent_platform/tests/test_trading_bot_flow.py -q
# 71 passed

# 1 次触发 1 行 + 五 ID 链（tmp 实测输出节选）
{
  "trigger_id": "trg-20261104-8497e0",
  "ledger_lines": 1,
  "repeat_idempotent": true,
  "repeat_same_trigger": true,
  "chain": {
    "decision_id": "d1-20261104010000-abcdef",
    "handoff_id": "d1h-20261104-evidence",
    "watch_plan_id": "wp-trg-evidence",
    "trigger_id": "trg-20261104-8497e0",
    "confirmation_id": "CFM-875A69BE558B"
  },
  "confirmation_has_trigger_id": true,
  "confirmation_status": "pending",
  "executes": false,
  "paper_ledger_created": false
}
```

- 真实 `agent_platform/data/watch_triggers.jsonl` 未创建/未写（测试与证据全程 tmp）；
- confirmation 四 ID 原样保留（`source_decision_id/source_handoff_id/source_watch_plan_id`、
  `source_report_id`），且状态保持 `pending`（人工门未越过）；
- 无 order/qty/broker 等执行字段，无 paper ledger 生成。

## 六、回滚预案

| 层 | 动作 | 验证 |
|---|---|---|
| 代码 | `git revert <commit>`（仅 4 文件 + 本文件，无 WIRE/registry 变更） | 既有 IFS 测试 rc0；consumer/adapter 行为回旧 |
| 数据 | 本单不写生产数据；如已按接线生成 `watch_triggers.jsonl`（显式 path），删除/归档该显式生成文件即可（append-only，无迁移） | 删除后 confirmation/paper/outcome 账本 digest 不变 |
| 接线 | 移除 `WATCH_TRIGGER_LEDGER_PATH` 即停用 trigger 账本/trigger_id 回写（adapter/consumer 回旧行为） | 一次 trigger 不新增行、confirmation 无 trigger_id |
| 服务 | 本单未启停服务；如后续常驻进程接入，需维护窗口重启换血 | 端口/服务不变 |

**风险点**：
1. confirmation JSON 追加 `trigger_id` 属 flow v2 开放 dict 的兼容扩展（flow 读入后原样回写，
   `four_ids`/`id_chain` 不受影响）；若未来 flow 收紧 schema，需同批迁移（见 §四-3）；
2. confirmation 路径口径与 `trading_bot_flow._dirs` 对齐（data_dir > TRADING_BOT_DIR > 默认），
   flow 若改目录语义需同步本模块 `confirmation_path`；
3. 单进程 append 幂等；多进程并发触发同一 plan 存在极小竞态（建议触发入口单实例/串行，
   或后续加文件锁）；幂等键仍是 watch_plan_id，重复行不会造成执行重复（confirmation 侧
   `source_report_id` 天然幂等）。

## 七、无证据不完成（DOD 勾选）

- [x] `pytest agent_platform/tests/test_watch_trigger_ledger.py` rc0（22 passed）
- [x] 相关回归 71 passed；ruff 0；syntax gate PASS
- [x] 1 次 trigger 落 1 行；重复触发 0 新增行、同 trigger_id
- [x] confirmation 带 trigger_id；四 ID 保留；chain 五 ID 全非空（示例）
- [x] 无执行字段/无 paper ledger/无生产数据写入（tmp）
- [x] 显式 path/env 才写；未配置保持旧行为
- [x] 回滚预案、接线项、风险点已列
