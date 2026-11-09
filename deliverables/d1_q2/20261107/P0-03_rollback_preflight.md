# D1 P0-03 回滚演练预检 / runbook

- 检查时间：2026-11-07T16:25:11+08:00
- 模式：preflight；real_drill_performed=False
- 结论：PASS

## 检查

| 检查 | 结果 | 详情 |
| --- | --- | --- |
| manifest.rollback.steps | PASS | steps=4 |
| manifest.rollback.pending | PASS | last_drilled='pending（2026-11-16 前需演练一次；本单未真回滚生产）' |
| nginx.d1.exact_route | PASS | location = /d1 |
| nginx.d1.prefix_route | PASS | location ^~ /d1/ |
| nginx.d1.auth_guard | PASS | auth_request /_auth_guard |
| page.d1.index | PASS | C:\项目\老田的AI助手\portal\d1\index.html size=15598 |
| git.head | PASS | d165d6ff42d149be4eab6193ff138c13015b15d5 |
| git.rollback_targets | PASS | declared=['4788b1527', 'e15cc824b', 'b11cda7c2'] missing=[] |
| container.nginx_test | PASS | nginx: the configuration file /etc/nginx/nginx.conf syntax is ok；nginx: configuration file /etc/nginx/nginx.conf test is successful |
| http/http://127.0.0.1:8091/api/health | PASS | status=200 |
| http/http://127.0.0.1:8091/health | PASS | status=200 |
| http/http://127.0.0.1:8091/api/d1/research | PASS | status=200 |
| http/http://127.0.0.1:8091/api/d1/decisions | PASS | status=200 |
| http/http://127.0.0.1:8091/api/d1/handoffs | PASS | status=200 |
| http/http://127.0.0.1:8091/api/d1/meeting | PASS | status=200 |
| http/http://127.0.0.1:8091/api/d1/watch-plans | PASS | status=200 |
| http/http://127.0.0.1:8091/api/d1/training/episodes | PASS | status=200 |
| paper.state.readable | PASS | {"path": "C:\\项目\\老田的AI助手\\agent_platform\\data\\paper\\state.json", "exists": true, "sha256": "ff5195ea0df580513e1f27d47b986eccaa016cd0bba017941368bc529327f9cb", "positions": 0, "nav": 0.99464} |
| ledger.d1_decisions | PASS | rows=4 sha256=32b57ebef63524b6baac11b976cb99e0c4c938d3636a76c313a08c80a404762c |
| ledger.d1_handoffs | PASS | rows=4 sha256=f036704e7716779caeb8c740ec1fe854ef53838d50128ad29b59d20a819fb2a6 |

## 回滚 runbook（批准窗口执行）

1. 0. 仅 Lead 在批准窗口执行；先跑本命令记录 baseline（含 HTTP/文件哈希）。
2. 1. 备份 portal/nginx.conf；记录 `git rev-parse HEAD` 与 D1 台账/paper state 哈希。
3. 2. 摘除入口：在 nginx 层临时将 /d1 精确路由及 ^~ /d1/ 前缀置为 503/404；
4.    `docker cp` 到 ai-portal，`docker exec ai-portal nginx -t`，`nginx -s reload`；
5.    同步暂停 /api/d1/* 对 8734 的暴露；不停止 8091。
6. 3. 验证：curl /d1 与 /api/d1/* 返回 503/404，/api/health 与其他平台入口仍 200。
7. 4. 恢复：还原 nginx.conf，`nginx -t`、`nginx -s reload`；确认 /d1 与 9 接口恢复 200。
8. 5. 复核：D1 台账行数、paper state 哈希、四 ID 链与 baseline 完全一致；总恢复 ≤10 分钟。
9. 6. 回填 manifest.status.rollback.last_drilled、起止时间、证据路径；不把本 preflight 当演练。

> 本文件是 preflight，不是真实演练记录；last_drilled 仍 pending。
