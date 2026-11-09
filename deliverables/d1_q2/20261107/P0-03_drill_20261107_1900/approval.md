# P0-03 真实回滚演练批准记录（19:00 窗口）

- 批准编号：`P0-03-DRILL-APPROVAL-20261107-1900`
- 状态：**APPROVED / NOT YET EXECUTED**
- 批准时间：2026-11-07 18:30 +08:00
- 批准截止：2026-11-07 18:55 +08:00（已满足）
- 演练窗口：**2026-11-07 19:00:00–19:10:00 +08:00**
- 执行人：Lead（本会话代理）
- 授权人：老田（human）
- 批准方式：老田在本会话明确批准；Lead 在截止前落盘本记录
- 执行令牌：`P0-03-DRILL-20261107-1900`

## 1. 批准范围

仅摘除 `/d1` 前端 UI 入口，临时返回 503：

- `/d1`
- `/d1/`
- `/d1/research`
- `/d1/war-room`
- `/d1/training`
- `/d1/q2/signoff`
- `/d1/q2/signoff/readonly`

**不改变**：

- 8091 进程、`/api/health`、`/api/d1/*` API
- 8789 watch-admin、WatchAdmin/WatchEngineLoop、Watch 24h 窗口
- 台账、paper、PG、生产数据
- 8734 登录与其他平台入口

## 2. 授权文本与截断哈希

```text
批准 P0-03 演练，窗口 2026-11-07 19:00–19:10，范围仅 /d1 UI 入口，授权人老田，批准文件请落盘并记录截断哈希，批准截止 18:55 前落盘，未获批准则取消本次窗口，
```

- 算法：`sha256(approval_text.encode("utf-8")).hexdigest()`
- 完整 SHA-256：`19df11fe670f55e1495af711d1617048f3006bf670fcce74f329b98315dbd09b`
- **截断哈希（前 16 位）：`19df11fe670f55e1`**

## 3. 批准时基线

- git HEAD：`018dd42b51ddc0655fa2f25098cc2d3f40a81a21`
- host `portal/nginx.conf` SHA-256：`5f80ffa665d06b474e9acac651312dbcb0e53ba8e7d8f35fd33d4a879c1378ac`
- 容器 `/etc/nginx/conf.d/default.conf` SHA-256：同 host hash
- 容器 `nginx -t`：通过
- 8091 LISTEN PID：`54780`
- 8789 LISTEN PID：`11964`

## 4. 执行前强制条件

执行前（18:50–18:58）必须全部满足：

1. 本文件 `status=approved`，且当前时间早于 19:00、晚于批准截止之前已落盘。
2. `portal/nginx.conf` 仍有 `/d1` 精确路由、`/d1/` 重定向、`^~ /d1/` 反代；若结构变化，中止并重新申请。
3. 容器 `nginx -t` 通过；8091/8789 PID 与 health 正常；发布窗口没有被其他变更占用。
4. 备份原始 `portal/nginx.conf` 到演练目录并记录 hash。
5. 预检失败或时间不足，直接取消，不“抢跑”。

## 5. 中止与恢复

- 任何一步 `nginx -t` 失败、503 未生效、API/服务异常、8789/Watch 任务状态变化，立即用备份恢复并 reload，窗口中止。
- 最迟 19:05 开始恢复；19:08 前必须恢复验证完成；19:10 关闭窗口。
- 恢复后更新 `manifest.status.rollback.last_drilled`；若中止/回滚失败，保持 pending 并记录原因。

## 6. 留证目录

`deliverables/d1_q2/20261107/P0-03_drill_20261107_1900/`
