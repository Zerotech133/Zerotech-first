# F1b：D1 读接口 envelope / request_id 接线（规范与回滚）

- **编号**：2026-11-04_F1b_D1_envelope接线_v1
- **任务**：`WO-IFS-ENVELOPE-20261104`（共享 task-36）；基线 = 当前 master（`agent_platform/contracts/ifs.py` 已合入）
- **分支**：`task/WO-IFS-ENVELOPE-20261104`（本单只 commit，不 merge）
- **性质**：把 F1 契约真正接到 D1 API 响应层；**只改响应序列化**，不改业务逻辑、不启动服务、不写生产数据、不实盘
- **依据**：`docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS-03/IFS-06/IFS-10）、`docs/standards/2026-11-03_Envelope_Error_v1.md`、`docs/standards/INTERFACE_REGISTRY_v1.yaml`

---

## 一、范围与接线结果

| 接口 | 方法 | interface_id | schema_version（envelope） | 状态 |
|---|---|---|---|---|
| `/api/d1/research` | GET | `d1.research.get` | `d1_research/v1` | ✅ 已接线 |
| `/api/d1/decisions` | GET | `d1.decision.list` | `d1_decision/v1` | ✅ 已接线 |
| `/api/d1/decision` | POST | `d1.decision.create` | `d1_decision/v1` | ✅ 已接线（写但 paper-only，不改业务） |
| `/api/d1/meeting` | GET | `d1.meeting.get` | `d1_meeting/v1` | ✅ 已接线 |
| `/api/d1/meeting` | POST | `d1.meeting.create` | `d1_meeting/v1` | ✅ 已接线 |
| `/api/d1/handoffs` | GET | `d1.handoff.list` | `d1_handoff/v2` | ✅ 已接线 |
| `/api/d1/handoff` | POST | `d1.handoff.create` | `d1_handoff/v2` | ✅ 已接线 |
| `/api/d1/watch-plans` | GET | `d1.watch_plans.list` | `watch_plan/v1` | ✅ 已接线 |
| `/api/d1/training/episodes[/<id>]` | GET | `training.episode.get` | `training_episode/v1`（旧契约优先，见 §三例外） | ✅ 已接线 |
| `/api/acad1/pool` | GET | `acad1.pool.get` | `acad1_pool/v2` | ⏸ **未接线**（可选；待 F2 池真源收口后单独接） |

实现方式：

1. 新增 `portal/ifs_envelope.py`（复用 `agent_platform/contracts/ifs.py`）：
   - `ok_payload(handler, interface_id, legacy)`：旧响应字段原样保留 + 叠加
     `ok/schema_version/request_id/source/data/warnings`；
   - `error_payload(handler, interface_id, legacy, http_status)`：叠加
     `schema_version=error/v1`、`request_id`、`error_detail{code,message,retryable,details}`；
   - `interface_for_path(path, method)` + `send()`：按路径/方法推断 interface_id；
     未登记路径**原样透传**（不接管其它接口）；
   - `request_id_for(handler)`：合法 `X-Request-Id` 沿用、否则 `req-<32hex>` 生成。
2. 各 D1 API 模块（`portal/api_d1_decision.py` / `api_d1_meeting.py` /
   `api_d1_handoff.py` / `api_d1_training.py`）的 `_json()` 统一改为调用
   `ifs_envelope.send(...)`（业务分支/校验/错误码/HTTP 状态全部不变）。
3. `portal/portal_api.py`：
   - `/api/d1/research` 分支改为 `ifs_envelope.ok_payload(self, "d1.research.get", d1_data.d1_snapshot())`；
   - `Handler._json()` 在已有 `_ifs_request_id` 时回写响应头 `X-Request-Id`（与 body.request_id 一致）。
4. 无新增路由（route baseline 不变）、无端口/服务变化、无计划任务变化。

---

## 二、响应契约（兼容期）

### 2.1 成功（旧字段原样保留 + envelope）

```json
{
  "ok": true,
  "schema_version": "d1_handoff/v2",
  "request_id": "req-<32hex>",
  "source": {
    "interface_id": "d1.handoff.list",
    "source_of_truth": "agent_platform/data/d1_handoffs.jsonl",
    "source_mode": "ledger",
    "stale": false
  },
  "data": {"...原 payload 副本..."},
  "warnings": [],
  "...原顶层字段（summary/handoffs/watch_plans/latest/... 全部保留）..."
}
```

- `data` = 原响应体副本（兼容期便于新消费方统一读 `data`；旧消费方继续读原顶层字段）；
- 响应头 `X-Request-Id` 与 `request_id` 一致；
- 不新增执行字段（order/qty/broker/execute/fill/account 一律不出现）。

### 2.2 失败（error/v1 + 兼容期双形态）

```json
{
  "ok": false,
  "schema_version": "error/v1",
  "request_id": "req-<32hex>",
  "error": "bad_target_action",
  "error_detail": {
    "code": "bad_target_action",
    "message": "trade 只能交 TR1/TR2/TR3",
    "retryable": false,
    "details": {"legacy_code": "bad_target_action", "http_status": 400}
  },
  "warnings": [],
  "...原顶层字段（message / source 等，若有）..."
}
```

- **旧 `error` 字符串保留**（D1 旧页面如 `acad1_pool.html` 与既有契约测试依赖）；
  IFS-03 要求的对象形态放在 `error_detail`（`code/message/retryable/details`）；
- HTTP 状态沿用原状态（400/404/413/429/500 等）；`error_detail.details.http_status` 同步记录；
- 旧错误码 → IFS 错误码映射：`bad_*/missing_*→invalid_parameter`、`not_found/episode_not_found/*_not_found→not_found`、
  `execution_field_forbidden/not_paper/bad_target_action` 精确命中、`duplicate_*→duplicate`、
  `*_failed→internal_error`、`body_too_large→invalid_request`、`too many requests→rate_limited`。
- 双形态为**兼容期过渡**：待前端迁移完成后（F1c）把 `error` 切为 IFS 对象（破坏性变更按 IFS-09 走 v2 + 90 天双版本）。

### 2.3 request_id 规则

- 请求带合法 `X-Request-Id`（`^req-[0-9a-f]{32}$`）→ 沿用；非法/缺失 → 服务端生成；
- 每次响应唯一（同请求其他接口可用同一 header 串联 trace）；
- 响应头 `X-Request-Id` 由 `portal_api.Handler._json()` 统一回写。

---

## 三、已知例外 / 兼容碰撞（诚实披露）

1. **`training.episode.get` 字段碰撞**：旧契约顶层已占用 `schema_version`（`training_episode/v1`）
   与 `source`（`episodes.jsonl` 相对路径字符串）。处理：
   - 顶层 `schema_version` 保留旧值 `training_episode/v1`（与数据契约一致）；
   - 顶层 `source` 保留旧字符串；IFS source 对象放 `source_detail`；
   - registry 该接口 schema 目前登记 `training/v1`，与数据契约 `training_episode/v1` 口径待对齐（接线项，不属本单写域）。
2. **`error` 双形态**：见 §2.2；旧页面/旧测试零改动。
3. **`data` 副本体积**：兼容期响应体积约 2×（原 payload + data 副本）；training 列表有 limit 上限（≤200），
   可接受；F1c 前端切换后可去掉旧字段只留 `data`。
4. `/api/acad1/pool` 未接线（可选）：其真源/`source_mode` 待 F2 池收口，避免 envelope 声明与实现不一致。

---

## 四、验证与证据

```powershell
# 新增 F1b 契约测试（离线，不连服务）
python -X utf8 -m pytest portal/tests/test_api_envelope_d1.py -q

# 既有 D1 契约/页面兼容测试（必须全绿）
python -X utf8 -m pytest portal/tests/test_api_d1_decision.py portal/tests/test_api_d1_handoff.py `
  portal/tests/test_api_d1_meeting.py portal/tests/test_api_d1_research.py `
  portal/tests/test_api_d1_training.py -q
```

验收口径：

- [x] 成功：`ok/schema_version/request_id/source/data/warnings` 齐全，旧顶层字段保留；
- [x] 失败：`error/v1 + request_id + error_detail(code/retryable/details)`，旧 `error` 字符串保留；
- [x] `X-Request-Id` 回写（header 与 body 一致）；request_id 非空、唯一、可沿用；
- [x] D1 旧契约测试 38 passed 零改动；新 F1b 测试全绿；
- [x] 只读/paper-only：无执行字段、GET 无写盘副作用；
- [x] 未改业务逻辑、未启动/重启服务、未写生产数据。

---

## 五、回滚

本单改动仅响应包装（无路由/服务/数据变更），回滚无需停服或数据迁移：

```powershell
# 方式 A（推荐）：git revert 本单提交
git revert <本单 commit> --no-edit

# 方式 B：手工回滚清单
#   1) 删除 portal/ifs_envelope.py、portal/tests/test_api_envelope_d1.py
#   2) 四个模块 _json() 恢复为 handler._json(obj, code)（api_d1_decision/meeting/handoff/training）
#   3) portal_api.py：/api/d1/research 分支恢复 self._json(d1_data.d1_snapshot())；
#      _json() 删除 X-Request-Id 两行
#   4) 回滚后验证旧契约测试：
#      python -X utf8 -m pytest portal/tests/test_api_d1_*.py -q
```

影响面：仅 `portal/` 下响应序列化与测试；**不涉及 pool/paper/registry/交易链、无账本写入、无服务进程变更**。

---

## 六、需 Lead / 后续接线项

| # | 项 | 说明 |
|---|---|---|
| 1 | `/api/acad1/pool` envelope（可选） | 待 F2 池真源 `source_mode=pg_official` 后接入 `acad1_pool/v2` |
| 2 | 前端迁移到 envelope | D1 页面逐步读 `data.reply`/`warnings`；`error` 切对象前保留双形态（F1c） |
| 3 | registry 口径对齐 | `training.episode.get` registry schema `training/v1` ↔ 数据契约 `training_episode/v1`（需 registry owner） |
| 4 | 其它接口 envelope | `/api/quant/paper`、`/api/war-room/snapshot`、`/api/acad1/pool` 等仍为旧形态（后续 F 阶段） |
| 5 | X-Request-Id 全链 | 其它写接口/网关逐步统一回写（IFS-10） |

---

## 七、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-04 | 首版：D1 9 个接口 envelope/request_id 接线；兼容期双形态与 training 碰撞例外；测试与回滚 |
