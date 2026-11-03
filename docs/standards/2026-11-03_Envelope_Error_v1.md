# 统一 envelope / error 规范 v1（IFS-03 定稿）

- **编号**：2026-11-03_Envelope_Error_v1
- **配套**：`docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS-03/IFS-09/IFS-10）、`docs/standards/INTERFACE_REGISTRY_v1.yaml`（`envelope` 段）
- **机器可读**：`docs/standards/schemas/interface_envelope_v1.schema.json`、`docs/standards/schemas/interface_error_v1.schema.json`
- **依据**：`deliverables/2026-11-03_接口对接规范IFS_v1与整改计划.md` §IFS-03、P0-4
- **性质**：规范层定稿（本单只写文档/schema/测试，不改 portal_api.py、不启动服务）

---

## 一、目标与适用范围

解决 P0-4：现有接口返回口径不一（`/api/d1/research` 返回 `items/summary/mode/ok` 无 `schema_version`、`/api/acad1/pool` 无 `schema_version`、`/api/d1/decision` 返回 `ok/decision/summary`、`/api/quant/paper` 错误语义不统一）。

适用于**所有 HTTP / MCP / 内部契约接口的新增与改造**，自 IFS v1 起：

```text
- 成功：ok + schema_version + request_id + source + data + warnings（+ generated_at）
- 失败：ok=false + schema_version=error/v1 + request_id + error{code,message,retryable,details}
- 没有 schema_version 的接口不得进入 v1 状态（IFS-02 registry.status=v1 的前置条件）
```

既有接口迁移策略（F1 `WO-IFS-ENVELOPE-20261103`）：

1. 保留旧字段向后兼容一个周期（不删字段、不改字段语义）；
2. 新增 envelope 顶层字段；
3. 旧字段 deprecated 标注在 IFS 计划与 registry `notes`；
4. 破坏性调整走 v2 + 90 天双版本（IFS-09）。

---

## 二、成功信封（Success Envelope）

### 2.1 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | bool | ✅ | 恒 `true`；**禁止** `ok=true` 里藏 error |
| `schema_version` | string | ✅ | 接口数据契约版本，形如 `d1.research/v1`；禁止缺省 |
| `request_id` | string | ✅ | `^req-[0-9a-f]{32}$`；服务端生成并回写 `X-Request-Id` |
| `trace_id` | string | 可选 | 全链路追踪（IFS-10），与 request_id 可同值 |
| `generated_at` | string | 建议必填 | ISO-8601 带时区（`2026-11-03T07:00:00+08:00`） |
| `source` | object | ✅ | 数据来源与真源声明，见 2.2 |
| `data` | object / array / null | ✅ | 业务数据；`null` 仅限 `ok=true` 且 `warnings` 显式说明 |
| `warnings` | array | ✅ | 无告警必须是 `[]`（不得缺省）；元素 `{code,message,details}` |

### 2.2 `source` 对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `interface_id` | string | ✅ | registry 中登记的 interface id（如 `d1.research.get`） |
| `source_of_truth` | string | ✅ | 真源标识（PG 表/账本/目录/数据集） |
| `source_mode` | enum | ✅ | `pg_official` / `projection` / `ledger` / `static` / `aggregate` / `manifest` |
| `as_of` | string/null | 建议 | 数据时点（PIT）；不得把采集时间当数据时点（IFS-07） |
| `freshness_s` | int/null | 建议 | 数据新鲜度（秒）；缺 freshness 不得判 stale |
| `stale` | bool | 建议 | 是否过期；`stale=true` 允许 `ok=true` 但必须进 `warnings` |
| 其它 | any | 可选 | 接口相关 ID（`pool_id`/`decision_id`/`handoff_id` 等，见 ID chain） |

### 2.3 成功示例

```json
{
  "ok": true,
  "schema_version": "d1.research/v1",
  "request_id": "req-3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c",
  "generated_at": "2026-11-03T07:05:00+08:00",
  "source": {
    "interface_id": "d1.research.get",
    "source_of_truth": "agent_platform/data/pool_review/ + shadow/shadow_ledger.jsonl",
    "source_mode": "aggregate",
    "as_of": "2026-11-03",
    "freshness_s": 3600,
    "stale": false,
    "pool_id": "ACAD1-REC-20261102"
  },
  "data": {"items": [], "summary": {"total": 0}},
  "warnings": []
}
```

### 2.4 降级数据（fail-visible，不假成功）

```json
{
  "ok": true,
  "schema_version": "acad1.pool/v2",
  "request_id": "req-...",
  "source": {
    "interface_id": "acad1.pool.get",
    "source_of_truth": "zhice_os_v2.public.v2_acad1_pool",
    "source_mode": "projection",
    "stale": true
  },
  "data": null,
  "warnings": [
    {"code": "stale_projection", "message": "PG 官方池未刷新，JSON 投影落后 2 个交易日", "details": {"lag_days": 2}}
  ]
}
```

规则：数据缺失/不可用必须显式（`data=null` + warning 或 `data_unavailable` 错误），**禁止用 0/空数组冒充“无数据”**。

---

## 三、错误信封（Error Envelope）

### 3.1 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | bool | ✅ | 恒 `false` |
| `schema_version` | string | ✅ | 恒 `error/v1` |
| `request_id` | string | ✅ | 同成功信封（失败也要能追） |
| `trace_id` | string | 可选 | — |
| `error` | object | ✅ | 见 3.2 |

### 3.2 `error` 对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | string | ✅ | 错误码枚举（见 3.3），**机器判断唯一依据** |
| `message` | string | ✅ | 人类可读中文说明；不得泄露堆栈/凭据 |
| `retryable` | bool | ✅ | 是否可重试（客户端按此决定退避） |
| `details` | object | ✅ | 结构化细节（可为 `{}`）；字段级错误用 `details.field` |
| `param` | string | 可选 | 出错参数名（`invalid_parameter` 场景） |
| `retry_after_s` | int | 可选 | `rate_limited` 场景建议值 |

### 3.3 错误码表（与 registry `envelope.error_codes` 一致）

| HTTP | code | retryable | 场景 |
|---|---|---|---|
| 400 | `invalid_request` | false | 请求格式/方法/路由不匹配 |
| 400 | `invalid_parameter` | false | 参数取值非法（枚举/范围/类型） |
| 400 | `bad_body` | false | body 非 JSON / 非 object |
| 400 | `execution_field_forbidden` | false | handoff/确认携带 `order/qty/broker/execute/trade_id/fill/account`（IFS-05） |
| 400 | `bad_target_action` | false | target/action 组合非法（watch/trade 目标域） |
| 401 | `unauthorized` | false | 缺 portal-token；8787 401 为登录门非故障（IFS-08） |
| 403 | `forbidden` | false | roles 不足 |
| 403 | `not_paper` | false | `paper_only=true` 被违反（企图实盘） |
| 404 | `not_found` | false | 资源不存在 |
| 404 | `route_not_found` | false | 未登记路由 |
| 409 | `conflict` | false | 状态冲突（已确认/已执行） |
| 409 | `duplicate` | false | 重复创建但语义非幂等 |
| 200 | `idempotent_replay` | false | 同 key 重复请求返回原记录（`ok=true` + warning，不是错误体） |
| 422 | `unprocessable` | false | 业务规则拒绝（风控/证据不足） |
| 429 | `rate_limited` | true | 限流，按 `Retry-After` 退避 |
| 500 | `internal_error` | true | 接口内部异常（掩码堆栈） |
| 502 | `upstream_unavailable` | true | 上游依赖不可用 |
| 503 | `not_ready` | true | 依赖未就绪（上下文/预热） |
| 503 | `data_unavailable` | true | 数据缺失/未生成（不造数） |
| 503 | `kill_switch_active` | false | `PAPER_KILL` 生效（不自动恢复） |
| 504 | `timeout` | true | 处理超时 |

补充约定：

1. `error.code` 是**唯一**程序化判断依据；HTTP 码只表达传输语义；
2. `retryable=true` 的通用重试策略：指数退避（0.5s/1s/2s，最多 3 次），`rate_limited` 以 `retry_after_s` 为准；
3. 未知/未登记错误码一律按 `internal_error` 处理并上报（禁止客户端猜语义）；
4. 认证失败（401/403）不得重试；
5. 业务拒绝（422）不得重试，需人工处理。

### 3.4 错误示例

```json
{
  "ok": false,
  "schema_version": "error/v1",
  "request_id": "req-9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d",
  "error": {
    "code": "bad_target_action",
    "message": "watch 只能交 WA1/WA2/WA3/WATCH_TOWER",
    "retryable": false,
    "details": {"field": "target", "action": "watch", "allowed": ["WA1", "WA2", "WA3", "WATCH_TOWER"]}
  }
}
```

---

## 四、request_id / trace_id 规则

1. 服务端为**每个请求**生成 `request_id`（`req-` + 32 hex），并在响应头回写 `X-Request-Id`；
2. 客户端传入 `X-Request-Id` 时，服务端沿用并校验格式（非法则重新生成并在 warnings 说明）；
3. `trace_id` 用于跨接口全链（IFS-10），MCP/内部契约同样携带；
4. 审计日志（`audit.jsonl`/`dev_events.jsonl`）必须记录 `request_id`、`interface_id`、`ok`、`error.code`、`latency_ms`。

---

## 五、幂等语义

| 场景 | HTTP | 响应 |
|---|---|---|
| 首次创建 | 200/201 | 正常成功信封 |
| 同 `Idempotency-Key` 或同上游 ID 重复 | 200 | 成功信封 + `warnings[].code=idempotent_replay` + `data` 为原记录 |
| 同 key 但请求参数不同 | 409 | `duplicate`（语义冲突，不覆盖原记录） |
| 状态机冲突（已 approve 再 approve） | 409 | `conflict` |
| 上游 ID 缺失 | 422 | `unprocessable`（details 指明缺失上游） |

写接口（registry `write=true`）必须声明幂等键来源：`source_report_id`（确认）/ `decision_id`（交棒）/ `confirmation_id`（成交）等，登记于 registry `notes` 或接口契约。

---

## 六、契约测试骨架（IFS-12）

模板位置：`tools/tests/test_interface_registry_contract.py`（本单已交付，可直接复用 `validate_envelope()` / `validate_error()` 断言函数；不依赖真实服务）。

每个接口上线前至少覆盖：

- [ ] schema 必填字段/类型（envelope + data schema）
- [ ] 成功样例（200 + `ok=true` + `schema_version`）
- [ ] 失败样例（错误码 + `retryable` + `details`）
- [ ] 幂等（同 key 两次 → 同 ID/`idempotent_replay`）
- [ ] 认证（无 token → 401；roles 不足 → 403）
- [ ] 数据缺失（不造数：`data=null` + warning 或 `data_unavailable`）
- [ ] 端到端 1 条（F7 全链用例）
- [ ] 只读校验（GET 无写副作用；写接口 `paper_only=true`）

运行方式（离线，不启动服务）：

```powershell
python -X utf8 -m pytest tools/tests/test_interface_registry_contract.py -q
```

---

## 七、版本与兼容（IFS-09）

1. `schema_version` 与接口版本同批更新；破坏性变更 → v2 + 90 天双版本；
2. 错误信封自身版本固定 `error/v1`，错误码只增不删；废弃错误码保留映射一个周期；
3. 响应字段只增不减；字段语义变更等同破坏性变更；
4. registry、composition manifest、页面 route baseline 与接口版本**同批提交**（IFS-09/IFS-11）。

---

## 八、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-03 | 首版：成功/错误信封、source 对象、21 个错误码、request_id/trace_id、幂等语义、契约测试清单 |
