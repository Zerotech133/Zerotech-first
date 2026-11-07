# P0-03 完整演练批准记录（20:30 窗口）

- 批准编号：`P0-03-DRILL-APPROVAL-20261107-2030`
- 状态：**APPROVED / NOT YET EXECUTED**
- 批准时间：2026-11-07 19:32 +08:00
- 批准截止：2026-11-07 20:25:00 +08:00（需在此前落盘本记录）
- 演练窗口：**2026-11-07 20:30:00–20:40:00 +08:00**
- 执行人：Lead（本会话代理）
- 授权人：老田（human）
- 执行令牌：`P0-03-DRILL-20261107-2030`
- 授权方式：老田在本会话选择“批准 20:30–20:40（推荐）”，作为独立于 19:00 UI-only 窗口的新授权

## 1. 授权原文

```text
继续做完整 P0-03，另开窗口摘 /api/d1/* 并单独授权
选择：批准 20:30–20:40（推荐）
```

- 授权文本完整 SHA-256：`182d4893be6fa675e5139c013232f1506f5a70dfb95705cda77422746c0a41d0`
- **授权文本截断哈希（前 16 位）：`182d4893be6fa675`**

## 2. 演练范围

在窗口内，仅对 **8734 门户入口**临时摘除：

### 2.1 `/d1` UI 入口

- `/d1`
- `/d1/`
- `/d1/research`
- `/d1/war-room`
- `/d1/training`
- `/d1/q2/signoff`
- `/d1/q2/signoff/readonly`

### 2.2 `/api/d1/*` 门户暴露

覆盖 9 个 D1 契约接口对应的 8 个唯一 HTTP 路径：

- `GET /api/d1/research`
- `POST /api/d1/decision`
- `GET /api/d1/decisions`
- `GET /api/d1/meeting`
- `POST /api/d1/meeting`
- `POST /api/d1/handoff`
- `GET /api/d1/handoffs`
- `GET /api/d1/watch-plans`
- `GET /api/d1/training/episodes`

## 3. 明确不做

- **不停止 8091 进程**，不重启 8091/8789/Watch；8091 内部 `http://127.0.0.1:8091/api/d1/*` 保持可读，仅摘除 8734 暴露。
- 不修改 `portal/portal_api.py`、不删除数据、不写 PG/生产账本/paper。
- 不改变 8734 登录、`/api/health`、其他 `/api/*`、Watch 24h 窗口。
- 不使用 dry-run 冒充真实摘除；必须实际临时返回 503 并恢复。

## 4. 预期与恢复

- 窗口内：`/d1*` 与 `/api/d1/*`（经 8734）→ 503；`/api/health`、其他 API → 200；8091 直连 `/api/d1/decisions` → 200。
- 恢复：窗口内使用精确备份字节写回 host `portal/nginx.conf`，`nginx -t`、`nginx -s reload`，再验证 `/d1*` 回到 302/200、`/api/d1/*` 回到非 503、数据哈希不变。
- 最迟 20:40 前恢复；目标总耗时 ≤10 分钟。
- 失败/超时立即恢复并中止；不允许把未恢复状态留给用户。

## 5. 证据

演练后写入 `deliverables/d1_q2/20261107/P0-03_full_drill_20261107_2030/`，并更新 `compositions/lusu-d1-flow/manifest.yaml` 的 `status.rollback`；只有完整矩阵与恢复均通过，才允许把 `last_drilled` 从 pending 改为完整演练记录。
