# 国内通知渠道设计

**日期：** 2026-09-17  
**状态：** 已确认  
**范围：** 为普通用户增加 WxPusher 微信定向通知，保留 Telegram；管理员运维告警继续使用企业微信群机器人。

## 1. 目标

在不破坏现有 Telegram 用户和通知状态机的前提下，提供国内稳定可达的微信通知能力：

- 普通用户通过微信扫码自动绑定 WxPusher。
- 策略信号、成交提醒、晨报和晚报可按用户、事件类型、渠道定向发送。
- Telegram 与 WxPusher 并存，用户可以启用其中一个或同时启用两个。
- 系统运维告警继续发送到企业微信群机器人，不混入普通用户的业务消息。

## 2. 非目标

本次不实现：

- WxPusher 失败后自动改发 Telegram。
- 短信、邮件、钉钉或飞书通知。
- 微信公众号认证服务号或小程序订阅消息。
- 通过企业微信群机器人向普通用户发送私密业务消息。
- 将通知正文或接收地址永久快照到 delivery 记录。

## 3. 已验证条件

2026-09-17 从生产节点 `159.75.220.212` 进行只读 HTTPS 连通性检查：

- WxPusher：HTTP 200。
- 企业微信 API：HTTP 200。
- Telegram API：连接超时。

因此，WxPusher 用于国内用户通知，企业微信群机器人用于运维告警；Telegram 作为可选境外渠道保留。

## 4. 总体架构

```text
业务事件
  │
  ├─ 用户定向通知
  │    └─ Notification Outbox
  │          ├─ WxPusher → 用户微信
  │          └─ Telegram → 用户 Telegram
  │
  └─ 系统运维告警
       └─ 企业微信群机器人 → 管理员群
```

### 4.1 WxPusher

WxPusher 是国内用户的主要通知渠道。用户在个人中心生成专属参数二维码，微信扫码后由 WxPusher 回调系统，系统消费一次性 challenge 并保存该用户的 WxPusher UID。

### 4.2 Telegram

保留现有 Telegram 能力。两个用户渠道分别绑定、分别配置偏好、分别创建 delivery、分别重试。一个渠道的失败不影响另一个渠道。

### 4.3 企业微信群机器人

企业微信群机器人只承载系统和运维告警，例如采集异常、调度失败、通知 worker 异常和数据源持续不可用。普通用户的策略、成交、持仓和报告内容不得进入管理员群。

## 5. 数据模型

### 5.1 用户渠道绑定

新增渠道无关的绑定表 `user_notification_binding`：

| 字段 | 含义 |
|---|---|
| `id` | 主键 |
| `user_id` | 系统用户 |
| `channel` | `wxpusher` 或 `telegram` |
| `recipient_id` | WxPusher UID 或 Telegram chat ID |
| `status` | `active` 或 `revoked` |
| `bound_at` | 绑定时间 |
| `revoked_at` | 失效或解绑时间 |
| `created_at` / `updated_at` | 审计时间 |

约束：

```text
UNIQUE(user_id, channel)
UNIQUE(channel, recipient_id)
```

这保证每个用户每个渠道只有一个绑定，同一渠道接收地址不能绑定多个系统账户。

不持久化 `recipient_masked`；API 返回时按渠道运行时脱敏，避免重复保存派生数据。

现有 `v2_user.telegram_chat_id` 第一阶段保留兼容，并通过迁移回填至新绑定表。所有读路径切换并稳定后，后续独立迁移才能删除旧列。

### 5.2 绑定 challenge

将现有 Telegram challenge 模式扩展为渠道无关的 `notification_binding_challenge`：

| 字段 | 含义 |
|---|---|
| `id` | 主键 |
| `user_id` | 发起绑定的用户 |
| `channel` | 本次为 `wxpusher` |
| `token_hash` | 随机 Token 的 SHA-256 哈希 |
| `expires_at` | 过期时间 |
| `consumed_at` | 消费时间 |
| `created_at` | 创建时间 |

规则：

- 明文 Token 使用密码学安全随机数生成。
- 数据库只保存 SHA-256 哈希。
- challenge 有效期为 10 分钟。
- 每个 challenge 只能消费一次。
- 同一用户重新生成二维码时，撤销该渠道尚未消费的旧 challenge。
- 回调校验应用身份后，绑定写入和 challenge 消费在同一事务内完成。

## 6. 绑定流程

```text
用户登录个人中心
  ↓
POST /auth/me/notification-bindings/wxpusher/challenge
  ↓
后端创建一次性 challenge 并请求 WxPusher 参数二维码
  ↓
返回二维码 URL 和过期时间
  ↓
用户微信扫码并关注
  ↓
WxPusher 调用 POST /integrations/wxpusher/webhook
  ↓
校验来源、事件类型、challenge 和 UID
  ↓
事务内 upsert active binding，并消费 challenge
  ↓
前端轮询绑定状态，显示绑定成功
```

建议用户 API：

```text
POST   /auth/me/notification-bindings/wxpusher/challenge
GET    /auth/me/notification-bindings
DELETE /auth/me/notification-bindings/wxpusher
POST   /auth/me/notification-bindings/wxpusher/test
```

集成回调：

```text
POST /integrations/wxpusher/webhook
```

测试消息只能发给当前登录用户自己的 active binding。VIEWER 可以管理自己的绑定和偏好，因为这是账号自助能力，不授予业务数据写权限。

## 7. Outbox 与投递

现有 notification event 保持渠道无关。对每个目标用户，根据 active binding 和渠道偏好创建 delivery：

```text
一个 notification_event
  ├─ user A / wxpusher delivery
  ├─ user A / telegram delivery
  ├─ user B / wxpusher delivery
  └─ user B / telegram delivery
```

保留现有幂等约束：

```text
UNIQUE(event_id, user_id, channel)
```

### 7.1 入队规则

对每个业务事件和目标用户：

1. 解析收件用户，禁止无 owner 时广播兜底。
2. 查询该用户各渠道 active binding。
3. 查询该事件类型在对应渠道的偏好。
4. 为每个“已绑定且已启用”的渠道创建一条 delivery。
5. 单个渠道入队失败通过现有 SAVEPOINT 隔离，不污染业务事务。

### 7.2 Worker 快照

认领快照必须包含：

```text
delivery_id
user_id
channel
event_type
payload
attempt_count
claim_epoch
```

`channel` 必须进入快照并参与 transport 分流。接收地址不固化在 outbox；发送前查询当前 active binding：

- 用户解绑后，待发送消息不再投递。
- 用户重新绑定后，待发送消息使用新地址。
- delivery 表不长期复制 UID 或 chat ID。

### 7.3 Transport 接口

建立渠道无关接口：

```python
class NotificationTransport(Protocol):
    def send(
        self,
        recipient_id: str,
        message: RenderedMessage,
    ) -> SendResult: ...
```

实现：

- `TelegramTransport`
- `WxPusherTransport`

统一 `SendResult` 至少包含：

- 是否成功；
- 服务端 HTTP 状态；
- 渠道错误码；
- 是否可重试；
- 可选 `retry_after`；
- 脱敏错误摘要。

每个 transport 独立负责请求格式、正文格式、超时、响应解析和错误分类。Worker 不包含渠道专属 HTTP 逻辑。

## 8. 偏好与默认值

通知偏好按 `(user_id, channel)` 管理，并覆盖：

- 成交提醒；
- 策略信号；
- 晨报；
- 晚报。

默认行为：

- 新用户界面优先展示微信通知。
- WxPusher 绑定成功后，为该渠道创建默认偏好并启用产品当前默认允许的事件类型。
- 不自动关闭 Telegram。
- 未绑定渠道的开关在前端置灰，后端也拒绝将其启用，防止出现“已启用但无法投递”的状态。
- 企业微信群机器人是系统级配置，不进入用户偏好。

## 9. 失败与重试

渠道完全独立；WxPusher 失败不会自动触发 Telegram 代发。

### 9.1 永久错误

以下错误不重试：

- UID 不存在或格式无效；
- 用户取消关注；
- 应用 Token 无效；
- 请求正文永久不合法；
- 用户已解绑或 binding 已 revoked。

若 WxPusher 明确表示 UID 永久失效，则在事务内将 binding 标记为 `revoked`，当前 delivery 标记为不可重试失败。页面提示用户重新扫码绑定。

### 9.2 临时错误

以下错误可重试：

- 网络超时；
- HTTP 429；
- HTTP 5xx；
- WxPusher 明确的暂时性服务错误。

沿用现有有界退避机制。若响应提供可信 `retry_after`，优先使用；否则按现有退避策略计算。达到上限后将 delivery 标记为终态失败。

## 10. 安全与隐私

- `WXPUSHER_APP_TOKEN` 和回调校验凭据仅放服务器环境变量。
- appToken、完整 UID、Telegram chat ID 和 challenge 明文不得出现在日志、前端资源或错误响应中。
- API 只返回脱敏接收标识，例如 `UID_a1***9x`。
- webhook 必须校验 WxPusher 官方实际支持的来源验证机制；若官方事件回调没有签名字段，则使用不可猜测的回调路径密钥，并严格校验应用 ID、事件类型、challenge、时效和一次性消费。实施前以当时官方文档为准，不假设不存在的签名协议。
- webhook 需要幂等，重复回调不能创建重复 binding，也不能重复消费 challenge。
- 同一 UID 绑定另一账户时返回冲突，不允许静默转移。
- 解除绑定只影响当前用户和指定渠道。

## 11. 页面设计

个人中心“通知设置”包含两个渠道卡片。

### 11.1 微信通知

未绑定状态显示“扫码绑定微信”按钮。点击后展示二维码、10 分钟倒计时和绑定状态轮询。绑定成功后自动更新，无需刷新页面。

已绑定状态显示：

- `已绑定`；
- 脱敏 UID；
- 绑定时间；
- `发送测试消息`；
- `解除绑定`。

### 11.2 Telegram

保留现有功能，标注为可选境外渠道，排在微信通知之后。现有用户无需重新绑定。

### 11.3 偏好矩阵

| 通知类型 | 微信 | Telegram |
|---|---:|---:|
| 成交提醒 | 独立开关 | 独立开关 |
| 策略信号 | 独立开关 | 独立开关 |
| 晨报 | 独立开关 | 独立开关 |
| 晚报 | 独立开关 | 独立开关 |

未绑定渠道对应列置灰并提示先绑定。

## 12. 运维告警

企业微信群机器人沿用现有 `ALERT_WEBHOOK_URL` 路径。新增以下告警：

- 某渠道连续失败超过阈值；
- notification worker 停滞或积压超过阈值；
- WxPusher 认证失败；
- delivery 终态失败率异常升高。

告警不得包含完整 UID、chat ID、Token 或普通用户私密业务正文。

建议指标：

```text
notification_delivery_total{channel,status}
notification_delivery_latency_seconds{channel}
notification_retry_total{channel,reason}
notification_binding_active{channel}
```

## 13. 测试与验收

### 13.1 后端自动化测试

必须覆盖：

- challenge 只保存哈希；
- challenge 过期、重复消费和旧 challenge 作废；
- webhook 来源验证和幂等；
- 同一 UID 不能绑定多个账户；
- 用户只能读取、测试和解除自己的 binding；
- 解绑后不创建新 delivery；
- 两渠道同时启用时创建两条 delivery；
- 关闭一个渠道时只创建另一个渠道 delivery；
- worker 根据 `channel` 选择正确 transport；
- 永久错误不重试；
- 临时错误按退避重试；
- 一个渠道失败不改变另一个渠道 delivery；
- revoked binding 不再接收消息；
- VIEWER 只能管理自己的绑定和偏好；
- 现有 Telegram 行为保持回归通过。

### 13.2 前端自动化测试

覆盖：

- 生成二维码、倒计时和轮询；
- 扫码成功后状态切换；
- 测试消息与解绑确认；
- 未绑定渠道开关置灰；
- 两渠道偏好独立保存；
- 中文和英文文案；
- VIEWER 可访问自身通知设置，但看不到其他用户信息。

### 13.3 生产真实链路验收

在 `159.75.220.212` 验证：

1. WxPusher 和企业微信 HTTPS 可达。
2. 用户扫码关注并自动绑定。
3. 测试消息到达微信。
4. 业务测试事件按用户生成正确 delivery。
5. WxPusher 与 Telegram 状态独立。
6. 模拟临时错误后按退避重试。
7. 模拟永久 UID 错误后 binding 被 revoked。
8. 企业微信群收到脱敏的运维告警。
9. 日志和 API 响应不泄露完整 UID、Token 或 challenge。

## 14. 上线与回滚

建议分阶段上线：

1. 上线数据库表、迁移回填和 transport，但默认不向普通用户开放。
2. 在管理员测试账户完成 WxPusher 全链验证。
3. 对少量用户开放扫码绑定和 WxPusher preference。
4. 观察 delivery 成功率、重试率和积压。
5. 全量开放，同时保留 Telegram。

回滚时关闭 WxPusher 渠道入队和 worker 分流即可；保留 binding 和历史 delivery 数据，不删除用户绑定。Telegram 和企业微信告警链路继续工作。
