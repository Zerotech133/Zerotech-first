# F1d：war-room/snapshot envelope + 页面迁移（规范与回滚）

- **编号**：2026-11-04_F1d_war_room_envelope_v1
- **任务**：`WO-IFS-WARROOM-ENVELOPE-20261104`（共享 task-43）；基线 = 当前 master（F1b envelope 已合入）
- **分支**：`task/WO-IFS-WARROOM-ENVELOPE-20261104`（本单只 commit，不 merge）
- **性质**：war-room 只读快照接口接 IFS envelope + `d1_war_room.html` 迁移到 `ifs_client.js`（data 优先/旧字段 fallback）；**不改业务聚合逻辑、不新增路由、不启动服务、不写生产数据、不接实盘**
- **依据**：`docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS-03/IFS-07/IFS-10）、`docs/standards/2026-11-03_Envelope_Error_v1.md`、`portal/static/ifs_client.js`（F1c）

---

## 一、范围与接线结果

| 接口/页面 | 方法 | interface_id | schema_version | 状态 |
|---|---|---|---|---|
| `/api/war-room/snapshot` | GET | `war_room.snapshot.get` | `war_room/v1`（本地扩展，registry 待登记） | ✅ 已接线 |
| `/d1/war-room`、`/d1_war_room.html` | GET | — | — | ✅ 页面已迁移（路由不变） |

实现：

1. `portal/ifs_envelope.py`（F1b 共用）新增 **`LOCAL_INTERFACES`** 本地扩展映射
   （`war_room.snapshot.get`，`source_mode=aggregate`，真源=`portal/war_room_data.py` 聚合）；
   `INTERFACES` 保持「registry 已登记接口」不变，避免污染 F1b 的 registry 对齐契约测试；
   运行时统一查 `INTERFACE_META = INTERFACES + LOCAL_INTERFACES`。
2. `portal/war_room_data.py` 新增 `snapshot_envelope(handler)`（只读）：
   - 成功 → `ifs_envelope.ok_payload(...)`；
   - 异常 → `ifs_envelope.error_payload(..., "war_room_snapshot_failed", 500)`（fail-visible，不伪造快照）。
3. `portal/portal_api.py` 的 `/api/war-room/snapshot` 分支改为
   `snapshot_payload, snapshot_status = war_room_data.snapshot_envelope(self)`（+2 行，原 `self._json(war_room_data.war_room_snapshot())` 移除）；路由/鉴权/基线不变。
4. `portal/d1_war_room.html`：
   - `<head>` 引入 `<script src="/static/ifs_client.js"></script>`；
   - `loadLive()` 优先 `IFSClient.fetchJson()` → `unwrapEnvelope`，**`env.data` 优先、旧格式回退旧顶层字段**；
   - `#data-date` chip 追加 `· live · <request_id> · warnings N · stale` 等只读元信息（复用既有元素，不改 UI 结构）；
   - 失败回落页面内静态快照（`· snapshot`），与迁移前行为一致；
   - 无任何写请求（仅 GET `/api/war-room/snapshot`）。

---

## 二、响应契约（兼容期，复用 F1b 规则）

成功（旧顶层字段 100% 保留 + envelope）：

```json
{
  "ok": true,
  "schema_version": "war_room/v1",
  "request_id": "req-<32hex>",
  "source": {"interface_id": "war_room.snapshot.get",
             "source_of_truth": "portal/war_room_data.py 聚合（...）",
             "source_mode": "aggregate", "stale": false},
  "data": {"...原快照 payload 副本..."},
  "warnings": [],
  "mode": "war_room_live",
  "items": [], "strategies": [], "paper": {}, "watch": {},
  "confirmations": {}, "links": {}, "trading_permitted": false,
  "...原顶层字段..."
}
```

失败（error/v1，兼容期 `error` 字符串保留）：

```json
{
  "ok": false,
  "schema_version": "error/v1",
  "request_id": "req-<32hex>",
  "error": "war_room_snapshot_failed",
  "error_detail": {"code": "internal_error", "message": "...", "retryable": true,
                   "details": {"legacy_code": "war_room_snapshot_failed", "http_status": 500}},
  "warnings": []
}
```

- `X-Request-Id` 由 `portal_api.Handler._json()` 统一回写（F1b 已有）；
- `schema war_room/v1` 为 F1d 本地契约：`INTERFACE_REGISTRY_v1.yaml` 目前无 war-room 条目（待登记，见 §五）；
- 无执行字段（order/qty/broker/execute/fill/account 不出现）；`trading_permitted=false`。

---

## 三、页面迁移细节（`d1_war_room.html`）

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 数据获取 | `fetch` + 直接 `d.ok`/顶层字段 | `IFSClient.fetchJson()`；缺失 `ifs_client.js` 时回退原生 `fetch` |
| 解包 | 无 envelope 概念 | `env.data` 优先；旧格式（无 `data`）用原顶层字段 |
| 元信息 | 仅 `#data-date` | `#data-date` 追加 `request_id`/`warnings` 数/stale（只读文本） |
| 错误 | 仅 `HTTP <status>` | `IFSClient.errorText()`（`error_detail` 优先，旧 `error` 兼容） |
| 失败兜底 | 页面内静态快照 | 不变（`· snapshot`） |
| UI 结构 | — | 未改布局/组件/样式；未新增写交互 |

---

## 四、验证与证据

```powershell
# 新增 F1d 测试（离线；node --check + data/fallback 行为）
python -X utf8 -m pytest portal/tests/test_api_war_room_envelope.py -q

# 回归：F1b D1 envelope + 旧 war-room 页面/路由测试
python -X utf8 -m pytest portal/tests/test_api_envelope_d1.py portal/tests/test_api_war_room.py -q
```

验收口径：

- [x] 成功：`ok/schema_version=war_room/v1/request_id/source/data/warnings` + 旧顶层字段保留；
- [x] 失败：`error/v1 + error_detail`（HTTP 500 映射、retryable、不伪造快照）；
- [x] `X-Request-Id` 回写（沿用合法 header / 非法换新）；
- [x] 页面 data 优先/旧字段 fallback；warnings/request_id 只在既有 chip 展示，不改 UI 结构；
- [x] 只读：无写请求/无写盘；`war_room_data.py` 无落账调用；
- [x] `node --check portal/static/ifs_client.js` 通过；node 函数级验证新旧格式；
- [x] route baseline 不变（`/api/war-room/snapshot` 为既有路由，零 diff）。

---

## 五、接线/未接线项

| # | 项 | 说明 |
|---|---|---|
| 1 | `war_room.snapshot.get` 登记 registry | `docs/standards/INTERFACE_REGISTRY_v1.yaml` 增 war-room 条目（interface_id/path/schema `war_room/v1`/source/SLO）；本单写域不含 `docs/standards/`，待 registry owner 处理 |
| 2 | war-room 其它读接口 | 目前仅 `/api/war-room/snapshot`；`watch_admin`（8789）为外部服务，不在本单范围 |
| 3 | 前端其它页面 | `d1_research.html`/`d1_training.html` 已由 F1c 迁移；war-room 本单完成 |
| 4 | envelope 全站推广 | 其余接口（`/api/quant/paper`、`/api/dw/*` 等）后续 F 阶段接入 |

---

## 六、回滚

仅响应包装与页面读取逻辑，无路由/服务/数据变更；回滚无需停服或数据迁移：

```powershell
# 方式 A（推荐）
git revert <本单 commit> --no-edit

# 方式 B：手工回滚清单
#   1) portal/d1_war_room.html：删除 ifs_client.js <script>，loadLive 恢复为单 fetch 旧实现
#   2) portal/portal_api.py：/api/war-room/snapshot 分支恢复 self._json(war_room_data.war_room_snapshot())
#   3) portal/war_room_data.py：删除 snapshot_envelope() 与 import ifs_envelope
#   4) portal/ifs_envelope.py：删除 LOCAL_INTERFACES / INTERFACE_META 相关新增（恢复 F1b 原样）
#   5) 删除 portal/tests/test_api_war_room_envelope.py
#   回滚验证：pytest portal/tests/test_api_war_room.py portal/tests/test_api_envelope_d1.py -q
```

影响面：仅 `portal/` 下 war-room 快照响应与页面读取；**不涉及 pool/paper/registry/交易链、无账本写入、无服务进程变更**。

---

## 七、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-04 | 首版：war-room/snapshot envelope（本地 schema war_room/v1）+ `d1_war_room.html` ifs_client 迁移 + 测试/回滚 |
