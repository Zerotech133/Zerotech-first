# 管理平台 V1 正式契约修正版

> 目标：为 `osint_video` 已完成的 PostgreSQL 知识链路、链接队列和博主运营任务提供可实施的管理平台契约。本文是实现前的架构基线，不涉及代码修改。
>
> **P0 硬修复**：公开 LinkTask DTO 必须移除 `raw_path`，并统一字段命名；健康状态必须使用固定枚举，禁止直接返回 `"ok"`。

## 1. 设计边界与事实源

- 浏览器只访问 FastAPI 管理 API；禁止直连 PostgreSQL、Qdrant、文件系统或采集器。
- PostgreSQL 是队列、博主、运行记录、知识单元、Evidence、策略和审计的权威事实源。
- Qdrant 只负责语义候选召回，KG 只负责有限邻居扩展；所有结果回 PostgreSQL 校验版本、生命周期、Evidence 和权限后才能返回。
- `raw_path`、Cookie、Token、加密凭证、完整采集器 payload 永不进入公开 DTO、日志、Qdrant payload 或图谱属性。
- 所有时间使用 ISO 8601 UTC；所有列表使用 cursor 分页，默认 `size=20`、最大 `size=100`。
- V1 单租户；操作者身份由现有登录/反向代理提供，API 从请求上下文得到 `operator_id`，不信任前端任意传入的操作人。

## 2. 状态转移契约

### 2.1 链接任务 `osint_link_task.status`

| 当前状态 | 允许目标状态 | 触发条件/门禁 |
|---|---|---|
| `pending` | `parsed`, `failed`, `dead`, `waiting_cookie` | 只有认领 worker 可推进；Cookie 失效不消耗重试次数 |
| `waiting_cookie` | `pending` | 仅凭证更新事务恢复，不能由普通重试接口恢复 |
| `parsed` | `downloaded`, `failed`, `dead` | 必须存在解析结果；解析 payload 不出 API |
| `downloaded` | `decrypted`, `failed`, `dead` | 必须完成 hash/文件校验 |
| `decrypted` | `extracted`, `failed`, `dead` | 必须通过媒体校验 |
| `extracted` | `done`, `failed`, `dead` | `done` 前必须完成 PG 知识资源、知识单元、Evidence 写入并登记索引任务 |
| `failed` | `pending`, `dead` | 退避到期可回 `pending`；超过 `max_retry` 只能 `dead` |
| `dead` | 无自动转移 | 人工操作需 reason，生成审计事件后重新入队 |
| `done` | 无 | 终态；修订走新知识版本，不回写链接任务 |

任何越级状态转移返回 HTTP 409、错误码 `STATE_TRANSITION_INVALID`。

### 2.2 博主运营对象 `osint_target.operation_state`

| 当前状态 | 允许目标状态 | 说明 |
|---|---|---|
| `link_received` | `creator_resolved`, `retryable`, `dead_letter` | 链接已发现候选博主 |
| `creator_resolved` | `profile_created`, `retryable`, `dead_letter` | 已取得稳定 finder/平台标识或标记低置信度归并 |
| `profile_created` | `history_backfill_running`, `retryable`, `dead_letter` | 画像记录已存在 |
| `history_backfill_running` | `profile_summarized`, `retryable`, `dead_letter` | 历史回溯完成且结果写入 |
| `profile_summarized` | `daily_monitoring`, `incremental_update`, `retryable`, `dead_letter` | 首次总结可用 |
| `daily_monitoring` | `incremental_update`, `retryable`, `dead_letter` | 到期扫描或人工触发增量 |
| `incremental_update` | `daily_monitoring`, `profile_summarized`, `retryable`, `dead_letter` | 增量完成；必要时重建总结 |
| `retryable` | 原失败前状态、`dead_letter` | 只允许带 reason 的恢复/重试 |
| `dead_letter` | `retryable` | 仅人工恢复，不允许自动循环 |

服务端必须读取当前状态后校验上述边，前端只显示可行动作，不负责状态判断。非法状态转移返回 HTTP 409、错误码 `CREATOR_STATE_TRANSITION_INVALID`。

### 2.3 `osint_creator_run.state`

- `pending → running → succeeded|failed|cancelled|dead_letter`。
- `running` 只能由一次成功的认领获得；`finish` 只能作用于 `running`。
- 已结束 run 的重复 finish 必须幂等返回原记录，不能改写结束状态。

## 3. 事务边界、锁与一致性

### 3.1 链接投递事务

`POST /api/v1/links` 对每个批次执行一个数据库事务：

1. 规范化 URL，计算 `url_hash`；原始 URL 仅按现有权限存储。
2. `INSERT ... ON CONFLICT(url_hash) DO NOTHING`。
3. 返回 `accepted`、`duplicated` 和公开 `LinkTaskPublic`；重复投递不能增加 retry 或更新时间。
4. 在同一事务写入管理变更事件（`change_seq`），提交后才向前端返回成功。

### 3.2 链接认领和状态推进

- worker 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 认领可执行任务，并写租约/心跳（若现有表暂未有租约字段，则由 worker 表或运行表承载）。
- 状态推进事务必须锁定任务行，重新读取当前状态后执行边校验；更新状态、错误、attempt、变更事件必须原子提交。
- 业务 API 不允许直接修改 `status`；只能调用命名动作（retry、resume、quarantine）。

### 3.3 博主状态与游标事务

- `advance_target`：`BEGIN → SELECT osint_target FOR UPDATE → 校验当前/目标状态 → 校验 cursor 单调性 → 更新状态/cursor/last_success_* → 写审计与 change_seq → COMMIT`。
- 游标比较必须使用服务端解码后的 `(source_observed_at, source_item_id)`，不能按不透明字符串比较。
- `input_cursor`、`output_cursor` 只能前进；回退返回 HTTP 409、错误码 `CURSOR_REGRESSION`。
- 同一 target 同时只允许一个 `history_backfill_running` 或一个有效增量 run；建议使用部分唯一索引或目标行锁实现。

### 3.4 Run 完成与总结快照事务

- `start_run` 锁定 run 行，条件为 `state='pending'`，成功才转 `running`。
- `finish_run` 锁定 run 行，条件为 `state='running'`；写完成计数、输出游标、错误和结束时间。
- 总结快照以 `(target_id, snapshot_hash)` 幂等：相同 hash 重复写入返回既有 `summary_id/summary_version`，不得创建新版本。
- 新 hash 事务内锁定 target，计算 `max(summary_version)+1`，插入 `osint_creator_summary`，再更新 `osint_target.last_summary_version/last_summary_hash/summary_updated_at`；三者同事务提交。
- Evidence 数量必须来自已持久化且有效的 Evidence，不信任前端计数。

### 3.5 查询一致性

- dashboard 同一请求使用一个 `as_of`；各聚合查询以该时间点为上界，避免卡片互相穿越。
- `changes` 必须使用持久化、单调递增的 `change_seq`（可新建 `management_change_log`，或复用带序列号的 outbox），不能用“当前时间 + id”临时生成游标。
- 游标响应规则：`next_cursor > request_cursor`；无变更时也返回不回退的当前 cursor。并发写入不得导致事件丢失。

## 4. 幂等键与审计

| 操作 | 幂等依据 | 重试结果 |
|---|---|---|
| 链接批量投递 | `url_hash`；可选 `Idempotency-Key` 绑定请求摘要 | 返回原 task，计入 `duplicated` |
| Link retry/resume | `Idempotency-Key + task_id + action` | 返回当前任务，不重复增加 attempt |
| 创建 creator run | `target_id + run_type + input_cursor + request_id` | 返回既有 pending/running run |
| finish creator run | `run_id + terminal_state + output_cursor` | 返回首次提交的终态记录 |
| 总结快照 | `target_id + snapshot_hash` | 返回既有 summary/version |
| 策略绑定 Evidence | `strategy_key + evidence_key + expected_version` | 返回既有绑定；版本冲突 409 |
| 人工状态动作 | `operator_id + object_id + action + request_id` | 审计事件只写一次 |

审计字段至少包括 `operator_id/action/object_type/object_id/request_id/reason/created_at`。Cookie 更新只记录名称、操作者和恢复数量，禁止记录值。

## 5. 公开 API DTO（冻结版）

### 5.1 通用 envelope

```json
{"code": 0, "data": {}, "message": "ok"}
```

失败响应使用非零 `code`，HTTP 状态码与业务错误码同时表达；`data` 为 `null` 或错误上下文，绝不包含内部堆栈。

### 5.2 LinkTaskPublic（P0 硬修复）

```json
{
  "task_id": "123",
  "url_hash": "sha1...",
  "state": "pending",
  "route": "D",
  "priority": 5,
  "attempt": 0,
  "error_code": null,
  "next_retry_at": null,
  "creator_name": null,
  "video_id": null,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z",
  "stage_duration_ms": null
}
```

字段映射：数据库 `id → task_id`、`status → state`、`retry_count → attempt`。以下字段**禁止**出现在任何公开链接 DTO：`raw_path`、`parse_result`、Cookie、Token、内部租约、完整采集器响应。详情若需展示来源，只返回权限过滤后的 `source_url` 或域名摘要。

### 5.3 HealthStatus（P0 硬修复）

```json
{
  "api": "healthy",
  "pg": "healthy",
  "qdrant": "unavailable",
  "worker": "degraded",
  "as_of": "2026-01-01T00:00:00Z"
}
```

每个组件只能使用 `healthy | degraded | unavailable | unknown`。禁止返回 `"ok"`、布尔值或组件专属枚举。PG 不可用时业务请求 HTTP 503；健康检查本身仍返回 envelope。

### 5.4 知识单元与 Evidence

- `KnowledgeUnitPublic` 主标识为 `unit_key + unit_version`；`unit_id` 是内部数据库 ID，不作为前端详情路径。
- 详情：`GET /api/v1/knowledge/units/{unit_key}?version=2`；不存在返回 404。
- 列表可返回 `unit_key/unit_version/title/content摘要/status/review_status/source_channel/evidence_count/index_status/pg_updated_at`。
- `source_url` 只在详情 EvidenceChain 且通过权限过滤后返回；不得返回 `raw_path`。
- 每条 Evidence 必须带 `evidence_key/source_channel/quote/start_sec/end_sec/reliability/snapshot_hash/validity`；时间范围越界、版本不匹配或 quote 缺失必须生成 `quality_warnings`。

## 6. API 清单与错误码

### 链接/状态

- `POST /api/v1/links`：批量幂等投递。
- `GET /api/v1/links?status=&route=&cursor=&size=`：公开任务列表，返回 `CursorPage<LinkTaskPublic>`。
- `GET /api/v1/links/{task_id}`：公开任务详情和阶段事件摘要。
- `POST /api/v1/links/{task_id}/retry`：仅 failed/dead，需 reason 和 Idempotency-Key。
- `POST /api/v1/credentials/yuanbao_cookie`：加密更新，不回显值。

### 博主运营

- `GET /api/v1/creators`、`GET /api/v1/creators/{target_id}`。
- `GET /api/v1/creators/{target_id}/interests`：兴趣信号必须附 unit/evidence 引用。
- `POST /api/v1/creators/{target_id}/runs`：创建幂等 run。
- `POST /api/v1/creator-runs/{run_id}/start`、`POST /api/v1/creator-runs/{run_id}/finish`。
- `POST /api/v1/creators/{target_id}/state`：服务端状态门禁和游标单调校验。
- `GET /api/v1/creators/due-scans`：有界返回待扫描对象。

### 知识/策略/运维

- `GET /api/v1/knowledge/units`、`GET /api/v1/knowledge/units/{unit_key}`、`GET /api/v1/evidence/{evidence_key}`。
- `GET /api/v1/strategies/{strategy_key}/evidence-chain`。
- `GET /api/v1/timeline`、`GET /api/v1/jobs`、`GET /api/v1/dashboard/summary`、`GET /api/v1/changes`、`GET /api/v1/healthz`。

标准错误码：

- `400 INVALID_ARGUMENT`
- `404 NOT_FOUND`
- `409 STATE_TRANSITION_INVALID`
- `409 CREATOR_STATE_TRANSITION_INVALID`
- `409 CURSOR_REGRESSION`
- `409 IDEMPOTENCY_CONFLICT`
- `422 EVIDENCE_REQUIRED`
- `503 DATABASE_UNAVAILABLE`
- `503 DEPENDENCY_DEGRADED`

## 7. 前端集成契约

- `client.ts` 必须在 API 边界显式解包 `{code,data,message}`，不把数据库字段直接透传给组件。
- `KnowledgePage.openUnit` 使用 `unit.unit_key`，调用 `/knowledge/units/{unit_key}?version=`；不得使用 `unit_id` 替代。
- `LinkInbox` 只消费 `LinkTaskPublic` 的 `task_id/state/attempt`，重试使用 `task_id`。
- POST 投递成功后将返回任务合并进 query cache，立即刷新 dashboard；存在未终态任务时每 3 秒请求 `/changes`。
- `/changes` 使用持久化 cursor；连续三次无变化退避到 10 秒，页面不可见暂停，恢复可见先拉 summary + changes。
- changes 不可用时降级为 10 秒列表刷新；PG 503 时保留本地提交结果并提示“数据源暂不可用”，不得伪造完成。
- 红色表示上涨/正向/`supports`，绿色表示下跌/负向/`contradicts`；API 错误使用橙/紫并显示文字，不能按通用“绿=成功”覆盖现有平台约定。

## 8. 实施任务顺序（最多 5 项）

### T01 项目基础设施与契约类型（P0）

- **源文件**：`admin_ui/package.json`、`admin_ui/vite.config.ts`、`admin_ui/tsconfig.json`、`admin_ui/index.html`、`admin_ui/src/main.tsx`、`admin_ui/src/App.tsx`、`admin_ui/src/types.ts`、`admin_ui/src/api/client.ts`。
- **依赖**：无。
- **交付**：冻结 envelope、LinkTaskPublic、HealthStatus、KnowledgeUnit/Evidence 类型；统一 API client 解包和路径约定。

### T02 数据库约束、状态机与幂等事务（P0）

- **源文件**：`store/schema.sql`、`queue_link/models.py`、`queue_link/repository.py`、`knowledge/creator_ops.py`、`knowledge/service.py`、`common/logging_setup.py`、`tests/test_state_machine.py`、`tests/test_idempotency.py`。
- **依赖**：T01。
- **交付**：博主状态转移门禁、cursor 单调性、run/summary 幂等、行锁/skip locked、审计和 change_seq 持久化。

### T03 管理 API 与安全 DTO（P0）

- **源文件**：`queue_link/api.py`、`knowledge/api.py`、`knowledge/repository.py`、`tests/test_api_contract.py`、`tests/test_query_boundaries.py`、`tests/test_health_api.py`。
- **依赖**：T01、T02。
- **交付**：统一 LinkTaskPublic（无 `raw_path`）、HealthStatus 枚举、unit_key 详情、cursor changes、敏感字段 allow-list 和 503 降级。

### T04 首屏、链接刷新与博主运营 UI（P1）

- **源文件**：`admin_ui/src/pages/Dashboard.tsx`、`admin_ui/src/pages/LinkInbox.tsx`、`admin_ui/src/pages/Creators.tsx`、`admin_ui/src/components/StatusCard.tsx`、`admin_ui/src/components/StatusBadge.tsx`、`admin_ui/src/components/RefreshController.tsx`、`admin_ui/src/styles/theme.ts`、`admin_ui/src/styles/global.css`、`admin_ui/src/**/*.test.tsx`。
- **依赖**：T01、T03。
- **交付**：深色高密度中文工作台、6 张状态卡、链接投递后增量刷新、博主运营状态与可行动作、红涨绿跌语义。

### T05 EvidenceChain、策略、验收与回归（P1）

- **源文件**：`admin_ui/src/pages/KnowledgeUnits.tsx`、`admin_ui/src/pages/Strategies.tsx`、`admin_ui/src/pages/Jobs.tsx`、`admin_ui/src/components/EvidenceChain.tsx`、`knowledge/indexer.py`、`tests/test_evidence_chain_api.py`、`tests/test_link_refresh.py`、`tests/test_management_e2e.py`。
- **依赖**：T02、T03、T04。
- **交付**：Evidence 版本链、策略绑定、Qdrant 降级、媒体清理后证据仍可追溯、端到端验收。

## 9. 验收测试矩阵

### P0 必过

1. **DTO 脱敏**：`POST/GET /links` 响应递归断言不存在 `raw_path/parse_result/value_enc/cookie/token`；同时存在 `task_id/state/attempt`。
2. **健康枚举**：四组件状态仅能为 `healthy/degraded/unavailable/unknown`；PG 断开时业务 API 返回 503，不返回伪造成功。
3. **链接幂等**：同一规范化 URL 并发提交 20 次只产生一个任务，其余均计入 duplicated。
4. **状态门禁**：非法 LinkTask/creator 状态转移返回 409，数据库状态不变化；并发推进只有一个事务成功。
5. **游标单调**：旧 cursor、新 cursor 回退均返回 `CURSOR_REGRESSION`；并发写入后 changes 不漏事件、不重复推进 cursor。
6. **Run 幂等**：重复 start/finish 不产生第二次状态变更；已终态 run 的重复 finish 返回首次终态。
7. **Summary 幂等**：同一 `snapshot_hash` 重试返回同一 summary/version；新 hash 只递增一个版本，target 指针与 summary 同事务更新。
8. **知识详情主键**：按 `unit_key + version` 返回正确版本；传不存在 key 返回 404，不能将内部 `unit_id` 当作路径主键。

### P1 必过

9. **投递刷新**：投递后 3 秒 changes 轮询可看到状态变化；页面隐藏暂停，恢复可见补拉；changes 故障降级列表轮询。
10. **Evidence 发布门禁**：没有有效 Evidence、版本不匹配或时间码越界时不能发布；旧证据不可被静默覆盖。
11. **查询边界**：size>100 被截断/拒绝；Qdrant 返回候选后必须回 PG 过滤，Qdrant 不可用时 PG 查询仍可用。
12. **媒体清理**：删除 MediaAsset 后知识文本、Evidence、来源定位和审计仍可查询；API 不暴露媒体路径。
13. **红绿语义**：支持/上涨使用红色，反对/下跌使用绿色，错误使用橙/紫并带文字；视觉回归测试固定 token。

## 10. 未决事项与假设

- 当前 `osint_creator_summary` 已有 `(target_id, summary_version)` 和 `(target_id, snapshot_hash)` 唯一约束，可直接承载总结幂等；若需严格请求幂等，另加 request_id/管理变更日志。
- 当前 schema 的 `osint_creator_run` 没有唯一运行请求键；实现时由 `request_id` 或部分唯一索引补齐同 target 同类型并发门禁。
- 当前 changes 若仍基于各业务表 `updated_at`，只能视为过渡实现；正式上线必须切换持久化 `change_seq`，否则高并发下存在漏变更风险。
- `finder_id` 是否属于敏感标识需按现有平台权限决定；默认列表脱敏，详情按权限返回。
- Qdrant V1 为可选依赖；健康状态为 `unavailable` 不阻断 PG 写入和证据查询。
