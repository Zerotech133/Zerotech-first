# P0-03 真实回滚演练窗口申请（v1）

- 申请编号：`P0-03-DRILL-REQ-20261107-01`
- 申请时间：2026-11-07 16:43 +08:00
- 状态：**REQUESTED / NOT APPROVED / 未开工**
- 申请人：Lead（本会话代理）
- 授权人：老田（human）
- 目标：只摘除 `/d1` 前端入口，验证 10 分钟内恢复；不碰 8091/8789/Watch 服务，不动 `/api/d1/*` 与生产账本。

## 1. 申请窗口

| 项目 | 时间（Asia/Shanghai） |
| --- | --- |
| 预检、备份、批准落盘 | 2026-11-07 17:20–17:28 |
| 演练开始 T0 | **2026-11-07 17:30:00** |
| 入口摘除 | T0 起 60 秒内 |
| 最迟开始恢复 | 2026-11-07 17:35:00 |
| 恢复验证完成 | 2026-11-07 17:38:00 |
| 窗口关闭/证据封存 | **2026-11-07 17:40:00** |
| 批准截止 | 2026-11-07 17:25:00 |

若 17:25 前未取得明确批准，本窗口自动取消；下一候选窗口为 2026-11-07 18:00–18:10。

## 2. Lead 批准方式

1. 老田在本会话明确回复：`批准 P0-03 演练，窗口 2026-11-07 17:30–17:40，范围仅 /d1 UI 入口`。
2. Lead 在 17:25 前写入 `deliverables/d1_q2/20261107/P0-03_drill_approval.md`，记录：
   - `status: approved`
   - `approved_by: Lead`
   - `authorized_by: 老田（human）`
   - `approved_at`、`window_start`、`window_end`
   - `scope: nginx UI /d1 only`
   - `nginx_sha256_before`、`git_head`
   - `abort_authority: Lead，老田可随时叫停`
3. **只有批准文件落盘 + 预检全绿，才允许执行**；脚本不自动批准，未批准不启动。
4. 批准后若变更范围（例如增加 `/api/d1/*`），必须重新批准，不能沿用本申请。

## 3. 降级范围（只摘 UI 入口）

演练期间将 `/d1` 前端 UI 全部临时返回 503：

- `/d1`
- `/d1/`
- `/d1/research`
- `/d1/war-room`
- `/d1/training`
- `/d1/q2/signoff`
- `/d1/q2/signoff/readonly`

以下**不受影响**：

- 8091 进程、`/api/health`、`/api/d1/*` API：保持 200，不重启。
- 8789 watch-admin、WatchAdmin/WatchEngineLoop、Watch 24h 窗口：不重启、不改任务。
- 8734 登录、其他平台入口、`/d1_q2/*` 直接源文件（原本 404）：正常。
- 台账、paper、PG、生产数据：只读校验，不写入。

> 重要偏差：P0-03 计划原文要求“D1 API 也不可用”。本次按你的约束**只摘 /d1 UI 入口，不摘 /api/d1/***，因此本次只能记为“UI 入口摘除演练”，不能单独满足 P0-03 完整验收；API 摘除需另开窗口并明确授权。

## 4. 执行机制（不碰服务进程）

1. 备份 `portal/nginx.conf` 为本次演练专用备份，记录 SHA-256。
2. 在 nginx 配置的 server 上下文中加入临时规则：
   `if ($request_uri ~ ^/d1(?:/|$)) { return 503; }`
   该规则只匹配 `/d1` 及 `/d1/...`，不匹配 `/api/d1/...`、`/d1_q2/...`。
3. `docker cp` 到 `ai-portal`，容器内 `nginx -t`；仅 `nginx -s reload`，不重启 8091/8789/Watch。
4. 恢复时用备份覆盖回 nginx.conf，再次 `nginx -t` + `nginx -s reload`，验证入口恢复。
5. 任一步失败：立即恢复备份并 reload，窗口中止；不继续、不隐瞒。

## 5. 验收与留证

演练目录：`deliverables/d1_q2/20261107/P0-03_drill_20261107_1730/`

必须留下：

- `approval.md`：批准记录、范围、时间。
- `baseline_before.json`：nginx sha256、git HEAD、8091/8789 PID 与 health、`/d1` 与 `/api/d1/*` 状态矩阵、台账/paper 哈希。
- `route_matrix_after.json`：`/d1*` 503；`/api/d1/*`、`/api/health`、其他入口 200；8789/Watch 任务状态不变。
- `restore_after.json`：入口恢复（未登录 302/登录 200）、API 200、数据哈希不变。
- `timeline.log`、`P0-03_drill_receipt.md`。
- 恢复耗时（目标 ≤10 分钟，实际记录到秒）。

成功后才回填 `manifest.status.rollback.last_drilled`；若中止或未恢复，保持 `pending` 并记录失败原因，不伪造通过。

## 6. 批准所需回复

请回复以下任一项：

- `批准，按 17:30–17:40 执行`
- `改到 18:00–18:10`
- `暂不批准`

未收到明确批准前，我不会修改 nginx、不会 reload、不会开始演练。
