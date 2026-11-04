# ADR-020 研发军团作战屏 v1 数据契约（只读）

- **状态**：**Accepted / M0 冻结（2026-10-28）**——老田会话授权「代确认」记录（见 docs/dev/legion_screen_target.md / milestones.md / review.md；AI 席位未单独回执，非人类签字）
- **日期**：2026-10-28｜**决策人**：ARCH1（DSH-Lead 代起草）
- **关联**：`deliverables/2026-10-27_任务拆解试点_研发军团作战屏v1_工作要素下发书.md`、`docs/dev/legion_screen_contract.json`、`WO-LS02-CONTRACT-20261027`

## 背景
老田/A0/ARCH1 需要一页看全研发军团（组织/11 席角色模型/在制/C6 指标/红线）。当前信息分散在 `org.json`、`.worktrees/tasks.json`、`agent_platform/data/dev/daily_metrics.json`、`docs/dev/org_chart.*`，且无统一只读 API。

## 决策
1. 新增 **只读** `GET /api/dev/legion`（新文件 `portal/api_dev_legion.py`，不改 `portal_api.py` 行数）；
2. 前端新页 `portal/dev_legion.html`（tokens/nav/短屏三件套）；
3. 数据契约冻结在 `docs/dev/legion_screen_contract.json`（v1），字段/类型/来源见下；
4. 只读边界：无写接口、不读凭据、不调生产；数据源缺失 → 空态 + `errors` 标注，**不 500**；
5. 页面不新增端口，接现有 8734 门户静态/路由模式（由 OPS1 在 LS-11 确认）。

## 契约（摘要；完整 JSON 见 `docs/dev/legion_screen_contract.json`）
- `schema`: `legion_screen_contract/v1`
- `endpoint`: `/api/dev/legion`（GET，`readonly=true`）
- `response`：
  - `generated_at`（ISO 字符串）
  - `department`：id/name/lead/lead_name/deputy/groups[]
  - `seats[]`：id/name/role_title/discipline/group/grade/model/org_role/report_to
  - `work_in_progress[]`：owner/tasks[{id,status,desc}]
  - `metrics`：window_days/dev_tasks_recent/dev_merged_recent/owner_legal_rate_recent/verification_rate/e3_trigger/dev_leadtime_p50_min
  - `redlines[]`：id/value/ok
  - `sources[]`：真源路径
- `errors`：缺失/损坏时的降级信息（`fallback="empty"`）

## 数据源
| 数据 | 真源 | 降级 |
|---|---|---|
| 组织/席位/组/汇报线 | `agent_platform/registry/org.json` | `department=null`、`seats=[]` |
| 在制任务 | `.worktrees/tasks.json` | `work_in_progress=[]` |
| C6 指标 | `agent_platform/data/dev/daily_metrics.json` | `metrics={}` |
| 红线 | 指标派生 | `redlines=[]` |

## 约束
- 只读：方法仅 GET；不写库/文件/事件（审计由访问日志承担）；
- 字段只增不改（v1 内）；新增字段必须向后兼容；
- 不暴露凭据/内部路径细节（只返回相对真源路径）；
- 性能：数据文件 ≤1MB，接口 <2s；缓存 ≤60s（由实现决定）。

## 后果
- DEV1/DEV5/DEV6/DEV2/3/4/7/8 按此契约并行；QA1 做契约测试；GUANYU 审只读边界；
- M0 冻结后 M1 才启动；字段变更走 ADR 修订，不静默改。

---

## 2026-11-03 修订（v1 契约不变 + live v2 extras 只增登记）

> **状态**：修订记录 **draft/shadow，待 ARCH1 预审**。
> **边界**：本节之前全文为 ADR-020 v1 原文，**保留不改**；本修订**不新建第二份契约 ADR**，契约真源仍为 `docs/dev/legion_screen_contract.json` + 本 ADR。
> **触发**：WP1+WP3（工单 **WO-CAOCAO-FRAME-20261103**）——API `/api/dev/legion` 已在 live 返回 v2 扩展（`workorder_bus/decisions/events/v2`），但契约未登记，属"实现先行、声明落后"。本修订只补声明，不改运行时。

### R1 新增登记（只增，不改 v1）

| 字段 | 类型 | 来源 | 只读边界 |
|---|---|---|---|
| `workorder_bus` | `array<object>{id,owner,status,desc,created}` | `.worktrees/tasks.json`（`status∈{doing,review}`；`created desc`；**≤30 条**；跨席） | 只读；源缺失/坏 JSON → `[]`+`errors[]` |
| `decisions` | `array<object>{title,due,owner,high,desc,action[,source]}` | `agent_platform/config/dev_legion_decisions.json#items`（配置项可无 `source`）+ 若 `workorder_bus` 非空则追加 1 项（`source=workorder_bus`；**与配置是否可用无关**） | 只读；配置缺失/坏 → 基础项 `[]`+`errors[]`，但 bus 非空时**仍合成 1 项**（不静默） |
| `events` | `array<object>{ts,kind,agent,task}` | `portal/data/dev_events.jsonl`（尾 12 行解析 → 末 10 条） | 只读；文件缺失/不可读 → `[]`+`errors[]` |
| `v2` | `object{readonly:true, extras:[workorder_bus,decisions,events]}` | 派生（非独立真源） | 恒 `readonly=true` |

- **条件性 `extensions[]` 追加**：`v2_workorder_bus` / `v2_decisions` / `v2_events`（对应字段非空时追加；空则不加）。
- **decisions 错误语义（2026-11-03 P0 复核修正）**：配置缺失/坏 JSON → 基础项 `[]` 并记 `errors[]`；**若 `workorder_bus` 非空，仍追加 1 条 `source=workorder_bus` 合成项（不静默）**；`v2_decisions` 扩展标志仍追加。实现依据：`portal/api_dev_legion.py::build_payload`（合成在 `if bus:` 分支，与 decisions 配置错误解耦；本修订对齐实现、不改代码）。
- `schema` 仍 **`legion_screen_contract/v1`**；v1 既有字段/语义不变；新增字段向后兼容（老客户端忽略）；**无写接口/写方法**。
- 机器可读登记：`docs/dev/legion_screen_contract.json` → `optional_extensions.v2`（含 `readonly_boundary`）。

### R2 只读与无写接口（复核口径）

- 仅 `GET /api/dev/legion`；`portal/api_dev_legion.py` 只有 `handle_get`，无 post/put/delete/patch handler；
- 返回顶层 `readonly=true`、`v2.readonly=true`；不写库/文件/事件（HTTP 访问日志承担审计）；
- **错误态**：任一源缺失/损坏 → 对应字段 `[]` + `errors[]` 非空 + `ok=false`，HTTP 仍 **200（不 500）**。

### R3 测试证据

- `agent_platform/tests/test_dev_legion_api.py`：保留并新增 v2 契约断言——字段存在、只读、`v2.readonly`、无写方法、错误态 `errors[]`、字段形状/上限；
- `agent_platform/tests/test_legion_contract.py` v1 契约 4 项保持通过（v1 schema 不变）；
- **live 复核（2026-11-03 06:58:39，只读 8734 `/api/dev/legion`）**：`schema=v1`、`readonly=true`、`v2.readonly=true`；
  `workorder_bus=5`、`decisions=3`（2 条配置项 + 1 条 `source=workorder_bus` 合成项）、`events=10`、`errors=[]`；
  `extensions` 含 `v2_workorder_bus/v2_decisions/v2_events`（另有 v1.1 real 两项，属既有扩展）。字段形状与本节登记一致。
- **P0 回归（2026-11-03 复核）**：`agent_platform/tests/test_dev_legion_api.py::test_v2_decisions_synthetic_when_config_missing`——
  decisions 配置缺失 + `workorder_bus` 非空 → `decisions` 含 1 条 `source=workorder_bus` 合成项、`errors[]` 非空、`v2_decisions` 扩展标志仍在；与本修订措辞一致。

### R4 诚实差异报告（不在本修订扩面，不猜）

实测 live 顶层字段与 v1 `response` 声明仍有**既有差异（非本次引入）**：

- live 顶层实际含：`schema/readonly/ok/errors/extensions/ai_cards/quant_cards/risk_cards/governance/workorder_bus/decisions/events/v2` 等；
- 其中 `ai_cards`/`quant_cards`/`risk_cards`/`governance`/`extensions`（及 v1.1 `real`）**未在 v1 `response` 中声明**；
- 处置：本修订**只登记 v2 extras**（任务范围）；上述差异建议另立契约追平单/下一修订统一处理；本修订不推断其 schema、不猜字段类型。

### R5 影响与纪律

- 本修订**不改运行时**（API/服务/nginx 均未动），仅契约文档 + 测试；
- 组合影子清单 `compositions/dev-legion`（Q1/draft/shadow）引用本契约，但**不接运行时**；
- ADR-015/017 仍 **Proposed**：本组合仅 draft/shadow，需 **ARCH1 预审**后方可升级 lifecycle；不自行合并（Lead 16:30 窗口统一处理）。

### R6 复核命令（只读）

```powershell
cd C:\项目\老田的AI助手
python -X utf8 -m pytest agent_platform/tests/test_dev_legion_api.py agent_platform/tests/test_legion_contract.py -q
python -X utf8 tools/composition.py validate compositions/dev-legion
python -X utf8 tools/composition.py plan --strict
```
