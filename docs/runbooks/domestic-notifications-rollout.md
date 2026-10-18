# 境内通知渠道（WxPusher）发布手册

- 适用分支：`feat/full-market-scan`（HEAD 3dce089，Task 1-16）
- 计划：`docs/superpowers/plans/2026-09-17-domestic-notification-channels.md`
- 关联测试：`tests/test_notification_*`（contracts / migration_0033 / binding_repository /
  preferences_channels / transports / wxpusher_adapter_contract / bindings_api /
  worker_channels / outbox_channels / routing_channels / ops_alerts）、
  `tests/test_notification_real_pg.py`（real_pg 并发）、
  `frontend/e2e/profile-notifications.spec.ts`（Profile 旅程）

> ⚠️ 未经明确授权，不对生产执行。本手册用于受控维护窗口。

## 变更摘要

- **迁移 0033**（forward-only 语义，仅建表/加列，不动历史数据语义）：
  - 新建 `v2_user_notification_binding`（渠道通用绑定；`(user_id, channel)` 与
    `(channel, recipient_id)` 双唯一约束）与 `v2_notification_binding_challenge`
    （扫码挑战，token 只存哈希）；
  - `v2_user_notification_preference` 通用化为**按渠道**偏好（四事件开关
    signal/fill/morning/evening + 市场报告/静音时段/时区/语言）；
  - 存量 `v2_user.telegram_chat_id` **回填**为 telegram 渠道 active 绑定
    （`bound_at` 记迁移时刻——历史列本无绑定时间戳）；存量 telegram 绑定挑战
    原样搬入通用挑战表；**同一 chat_id 被多账号持有时迁移主动中止**（先解决
    归属再升级）。
- 双渠道投递：outbox `v2_notification_delivery.channel` 分流，worker 按
  `transports` registry 路由（未知渠道终态失败，**绝不回退 Telegram**）；
  收件人发送前读当前 active 绑定（claim 与发送之间重绑以新为准）；
  `recipient_invalid` 终态在同一事务内条件撤绑（`revoke_if_current` 以本次
  实际使用的收件人比对，用户已重绑则新绑定不受影响）。
- 运维告警隔离：系统级运维告警不进入用户渠道偏好矩阵，通道独立。
- 前端：Profile 页双渠道设置（微信扫码绑定卡 + Telegram 卡 + 事件×渠道
  偏好矩阵），中英双语。

## 关键配置（默认全关，缺省即现状）

| 环境变量 | 作用 | 校验 |
| --- | --- | --- |
| `WXPUSHER_APP_TOKEN` / `WXPUSHER_APP_ID` / `WXPUSHER_APP_KEY` | WxPusher 应用凭证 | 开绑定时四项必须齐备 |
| `WXPUSHER_WEBHOOK_PATH_KEY` | webhook 路径密钥 | ≥32 个非空白字符 |
| `WXPUSHER_BINDING_ENABLED`（默认 false） | 扫码绑定端点/UI 入口开关 | 开启需上述四项 + webhook 为公网 https |
| `WXPUSHER_DELIVERY_ENABLED`（默认 false） | worker 是否投递 wxpusher 渠道 | 开启需 `WXPUSHER_APP_TOKEN` 且 `telegram_notification_mode=directed` 且 `multi_user_execution_mode=enforced`（启动即校验，不满足拒绝起服务） |
| `TELEGRAM_NOTIFICATION_MODE`（默认 broadcast） | broadcast/shadow/directed | `directed` 仅允许在 enforced 执行模式下 |

**停用语义**：`WXPUSHER_DELIVERY_ENABLED=false` 时 wxpusher 的 PENDING delivery
**停投不丢**（claim WHERE 排除，留在 outbox），重新开启即恢复消费。

## 发布九步（顺序执行，逐步验证）

### 1. 备份数据库并记录当前迁移版本

```bash
# 207 容器栈：pg_dump 到宿主机（示例，按实际容器名调整）
docker exec <pg容器> pg_dump -U zhice zhice_os_v2 | gzip > backup_$(date +%F_%H%M).sql.gz
# 记录当前版本（预期 0032）
psql ... -tAc "select version_num from alembic_version"
```

### 2. 部署能识别 WxPusher 渠道的新版 API 与 scheduler；旧 worker 全部退出

- api 与 scheduler **同一构建同时部署**（同一镜像/同一份代码），版本内含 0033
  之后的模型与 worker 多渠道路由；
- **升级窗口内旧 worker 必须全部退出**（旧 worker 不认识 `channel` 列语义，
  混跑可能把 wxpusher 行当默认 telegram 处理）；
- 207 注意：**单实例部署**（api / scheduler 各单副本、单进程），不存在多副本
  竞争；旧容器停稳后再起新容器。老坑提醒：重启 api 后 nginx 未重启会短暂 502，
  与本功能无关但同窗口操作时一并留意。

### 3. 执行迁移 0033，核对 Telegram 回填计数与冲突为零

```bash
alembic upgrade 0033
psql ... -tAc "select version_num from alembic_version"   # 应为 0033
```

```sql
-- 回填计数 == 升级前非空 telegram_chat_id 用户数（先在步骤 1 备份上数好）
SELECT count(*) FROM v2_user_notification_binding WHERE channel = 'telegram';
-- 同一收件人被多人持有 == 0（迁移遇冲突会主动中止，此为双保险复核）
SELECT count(*) FROM (
  SELECT recipient_id FROM v2_user_notification_binding
  WHERE channel = 'telegram'
  GROUP BY recipient_id HAVING count(*) > 1
) c;
```

> 若迁移因冲突中止：按备份定位重复 chat_id 的归属，人工改 `v2_user.telegram_chat_id`
> 后重跑迁移（迁移失败不落部分 DDL，可直接重试）。

### 4. 配置真实凭证，保持投递关闭

- `WXPUSHER_APP_TOKEN` / `WXPUSHER_APP_ID` / `WXPUSHER_APP_KEY` /
  `WXPUSHER_WEBHOOK_PATH_KEY`（≥32 非空白字符，生成后自己留存）；
- **保持 `WXPUSHER_BINDING_ENABLED=false`、`WXPUSHER_DELIVERY_ENABLED=false`**；
- 重启 api/scheduler 使配置生效（settings 校验失败会拒绝启动——这正是先验配置
  完整性的手段）。

### 5. WxPusher 后台配置 webhook；确认 nginx 该路径 access log 关闭

- 回调地址：`https://<host>/api/v1/integrations/wxpusher/webhook/<path_key>`；
- nginx 对该 location **关闭 access log**（path_key 出现在 URL 中，不能进访问日志）：

```nginx
location ~ ^/api/v1/integrations/wxpusher/webhook/ {
    access_log off;
    proxy_pass http://api_upstream;   # 与既有 /api 转发一致
}
```

- 用错误 path_key 发一个空 POST：预期 404（不泄露存在性）= 路由与转发正常。

### 6. 管理员 canary：扫码、测试消息、退订回调、重绑

保持面向用户的绑定入口关闭，先用管理员账号（本地起 `WXPUSHER_BINDING_ENABLED=true`
的 staging 或受控配置）全流程走一遍：

1. Profile → 微信通知 → 「扫码绑定微信」→ 手机 WxPusher 客户端确认 →
   页面轮询发现 active（脱敏 UID 可见）；
2. 「发送测试消息」→ 手机收到；
3. 退订回调：客户端取消关注/退订 → webhook 收到事件 → 绑定转 revoked；
4. 重绑：重新扫码绑定新 UID → 旧绑定被覆盖（active 只指向新 UID）。

每步后核对 `v2_user_notification_binding` 与 `v2_notification_binding_challenge`
（挑战 `consumed_at` 应全部非空——不发悬空挑战）。

### 7. 先开绑定，再对少量用户开放 UI

- `WXPUSHER_BINDING_ENABLED=true`（四项凭证 + https webhook 已在步骤 4/5 就绪）；
- 前端入口无需单独开关：绑定关闭时 challenge 端点拒绝，UI 卡片自然不可用；
  开启后对少量白名单用户开放（告知扫码路径）；
- 观察投递前队列与健康度：

```sql
SELECT channel, status, count(*) FROM v2_notification_delivery
GROUP BY 1, 2 ORDER BY 1, 2;
-- 此时 wxpusher 行应停在 PENDING（delivery 尚未开启，停投不丢）
```

### 8. 确认 directed + enforced 后，再启用 WxPusher 投递

前置（settings 启动硬校验，不满足开不了）：

- `TELEGRAM_NOTIFICATION_MODE=directed`；
- `MULTI_USER_EXECUTION_MODE=enforced`（directed 必须建立在 enforced 归属执行上，
  切换前置门禁见 `docs/runbooks/multi-user-rollout.md`）。

然后 `WXPUSHER_DELIVERY_ENABLED=true` 并重启 scheduler。观察首轮 worker：

```sql
SELECT channel, status, count(*) FROM v2_notification_delivery
GROUP BY 1, 2 ORDER BY 1, 2;   -- PENDING 应被消费为 SENT；FAILED 看 last_error_code
```

- `FAILED(notification.binding_missing)`：绑定在发送前被撤销，属正常竞态终态；
- `FAILED(unsupported_channel)`：脏数据渠道值，应排查而不是重试；
- `RETRY` 堆积：看 `next_attempt_at` 与 `last_error_code`（429/网络走退避）。

### 9. 回滚口径

- **只关两个开关**：`WXPUSHER_DELIVERY_ENABLED=false`（投递停、滞留行停投不丢）
  与 `WXPUSHER_BINDING_ENABLED=false`（绑定入口关闭）；
- **保留**绑定与历史（binding/history 表不动）——用户已建立的绑定和偏好是持久
  资产，重开即恢复；
- **不切回 broadcast**：directed 模式下已按用户偏好分渠道入队，回切 broadcast
  会绕过偏好矩阵与归属隔离；
- 不需要降级 0033（表为纯新增/加列，旧代码不读不写新表即可共存）。

## 发布门禁（合入/部署前全绿）

```bash
# 后端定向 11 文件
uv run pytest -q tests/test_notification_contracts.py tests/test_migration_0033.py \
  tests/test_notification_binding_repository.py tests/test_notification_preferences_channels.py \
  tests/test_notification_transports.py tests/test_wxpusher_adapter_contract.py \
  tests/test_notification_bindings_api.py tests/test_notification_worker_channels.py \
  tests/test_notification_outbox_channels.py tests/test_notification_routing_channels.py \
  tests/test_notification_ops_alerts.py
# 全量 + 静态
uv run pytest -q
uv run ruff check src tests
uv run mypy src/zhice_os_v2        # 与基线对比：存量 4 错误可接受，不新增
uv run alembic heads               # 仅 0033
# 前端
npm --prefix frontend run test && npm --prefix frontend run build
npm --prefix frontend run e2e -- profile-notifications.spec.ts
# 真实 PG 并发（专用库，用完 dropdb；PG16 建库必须 -O zhice）
createdb -O zhice zhice_notif_realpg_test
DB_MAIN_URL=postgresql://zhice:<pass>@127.0.0.1:5432/zhice_notif_realpg_test \
  PYTHONPATH=src uv run alembic upgrade 0033
REAL_PG=1 DB_MAIN_URL=postgresql://zhice:<pass>@127.0.0.1:5432/zhice_notif_realpg_test \
  PERSISTENCE_BACKEND=sql PYTHONPATH=src uv run pytest -o addopts="" \
  -m real_pg tests/test_notification_real_pg.py tests/test_notification_worker.py -q
dropdb zhice_notif_realpg_test
```
