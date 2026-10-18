# 多用户策略运行与 Telegram 定向通知设计

- 日期：2026-09-06
- 状态：用户已确认，实施计划已形成
- 适用系统：zhice-OS V2

## 1. 目标

系统支持以下业务模型：

1. 平台提供公共策略，所有用户可查看并选择运行。
2. 用户可以创建私有策略；私有策略默认仅创建者可见，可申请公开，经管理员审核后进入公共策略库。
3. 同一公共策略可被多个用户同时运行，但每个用户拥有独立的运行实例、账户归属、参数、风控、持仓、订单、成交、盈亏与通知。
4. 用户可以选择独立策略子账户或自己的共享组合账户；新启用公共策略时默认创建独立策略子账户。
5. 公共策略发布新版本后，现有用户实例继续固定旧版本，必须由用户确认升级。
6. Telegram Bot Token 由平台管理员在服务器统一配置一次。用户通过机器人一次性链接绑定自己的 Telegram，不接触 Token，也不手填 chat_id。
7. Telegram 通知根据实际产生事件的用户策略运行实例定向，只发送给所属用户，并支持按用户、市场和策略实例配置通知偏好。
8. 公开注册用户默认获得新增的 `USER` 角色，可管理自己的业务资源；现有 `VIEWER` 继续保持严格只读。

## 2. 非目标

首期不实现：

- 每用户独立 Telegram Bot Token；
- 多组织或企业租户；
- 跨用户共享交易账户；
- 策略市场、收益分成或用户间策略交易；
- 社交跟单；
- 多用户实盘券商开户；
- 任意用户脚本直接在生产服务器执行。

## 3. 当前系统差距

当前系统是“全局策略目录 + 全局调度器 + 全局模拟账本 + Telegram 全员广播”，尚未具备用户运行边界：

- `v2_strategy`、`v2_strategy_version` 没有 owner/visibility；
- 前端策略选择只是页面筛选，不会持久化，也不影响调度；
- 自建策略是全局记录，不是用户私有资源；
- `strategy_version_id` 能贯穿大部分交易链，但不能区分同一公共版本由哪个用户运行；
- 当前按策略统计的“账户”是全局持仓计算视图，不是持久化用户账户；
- 计划、订单、成交、持仓、复盘没有 `user_strategy_id` 或 `account_id`；
- `broadcast_telegram()` 将晨晚报和成交消息发给所有已绑定的活跃用户；
- chat_id 由用户手工填写，无 Telegram 侧所有权验证，也无唯一约束；
- 策略层和版本层状态由不同执行链读取，启停语义不一致；
- 已发布版本可原地编辑，历史交易无法稳定还原执行时算法。

因此不能仅修改 Telegram 收件人查询。必须先建立用户策略运行实例与持久化账户，才能可靠判断每笔事件属于哪个用户。

## 4. 核心架构

```text
策略定义层
Strategy
└─ StrategyVersion（发布后不可变）
   ├─ PUBLIC：平台公共策略版本
   └─ PRIVATE：用户私有策略版本

用户运行层
UserStrategyInstance
├─ user_id
├─ strategy_version_id
├─ account_id
├─ enabled
├─ personal parameters
├─ auto_execute
└─ notification preferences

账户与交易层
UserAccount
└─ UserStrategyInstance
   └─ TradePlan
      └─ OrderIntent
         └─ OrderRequest
            └─ ExecutionRecord
               └─ FillRecord
                  └─ Position / Replay / PnL

通知层
交易事件
→ user_strategy_id
→ user_id
→ verified telegram_chat_id
→ 用户通知偏好
→ 定向 Telegram 推送
```

职责边界：

- `Strategy/StrategyVersion` 描述算法定义与发布状态；
- `UserStrategyInstance` 描述某用户是否运行某个具体版本，以及个人参数、账户和通知偏好；
- `UserAccount` 是用户独立资金与风控账本；
- 交易链保存运行实例和账户归属；
- 通知层只消费带明确归属的领域事件，不反向猜测收件人。

## 5. 策略定义与版本模型

### 5.1 `v2_strategy` 扩展字段

```text
owner_user_id        VARCHAR(64) NULL
visibility           VARCHAR(16) NOT NULL  # PUBLIC / PRIVATE
publication_status   VARCHAR(24) NOT NULL  # PRIVATE / PENDING_REVIEW / PUBLISHED / REJECTED
created_at           TIMESTAMPTZ NOT NULL
updated_at           TIMESTAMPTZ NOT NULL
```

公开申请使用独立的 `v2_strategy_publication_request`：

```text
request_id           VARCHAR(64) PRIMARY KEY
source_strategy_id   VARCHAR(64) NOT NULL
requester_user_id    VARCHAR(64) NOT NULL
status               VARCHAR(16) NOT NULL  # PENDING / APPROVED / REJECTED
reviewed_by          VARCHAR(64) NULL
reviewed_at          TIMESTAMPTZ NULL
derived_strategy_id  VARCHAR(64) NULL
created_at           TIMESTAMPTZ NOT NULL
```

语义：

- `owner_user_id IS NULL AND visibility=PUBLIC`：平台公共策略；
- `owner_user_id=<user> AND visibility=PRIVATE`：用户私有策略；
- 普通用户可读取公共策略，但无权修改；
- 私有策略只有 owner 和管理员可读取、编辑、创建新版本；
- 申请公开将 `publication_status` 置为 `PENDING_REVIEW`；
- 管理员审核通过时，派生一份平台所有的公共策略和不可变公共版本；原私有策略、私有版本及创建者正在运行的实例保持私有且不变，避免公开操作反向暴露或改写用户资产。

### 5.2 不可变版本

`v2_strategy_version` 一旦满足任一条件即不可原地修改：

- 已发布；
- 已被 `UserStrategyInstance` 引用；
- 已被任何交易或复盘记录引用。

编辑操作必须创建新版本 ID。公共策略发布新版时：

- 旧运行实例继续引用旧版本；
- 设置 `upgrade_available_version_id`；
- 用户明确确认后才切换；
- 默认采用“旧持仓平仓后升级”，避免新版本接管旧持仓退出规则；
- 历史计划、成交、持仓和复盘永远保留原版本引用。

## 6. 用户账户模型

新增 `v2_user_account`：

```text
account_id            VARCHAR(64) PRIMARY KEY
user_id               VARCHAR(64) NOT NULL
market                VARCHAR(16) NOT NULL
currency              VARCHAR(16) NOT NULL
account_type          VARCHAR(24) NOT NULL  # SHARED_PORTFOLIO / STRATEGY_DEDICATED
initial_capital       NUMERIC NOT NULL
cash_balance          NUMERIC NOT NULL
reserved_cash         NUMERIC NOT NULL DEFAULT 0
status                VARCHAR(16) NOT NULL  # ACTIVE / FROZEN / CLOSED
created_at            TIMESTAMPTZ NOT NULL
updated_at            TIMESTAMPTZ NOT NULL
```

约束：

- 账户只属于一个用户；
- 普通用户只能访问自己的账户；
- 每个账户固定一个市场和本位币；
- `STRATEGY_DEDICATED` 默认只绑定一个运行实例；
- `SHARED_PORTFOLIO` 可绑定同一用户的多个运行实例；
- 跨用户共享账户不在首期范围。

### 6.1 独立策略子账户

用户首次选择公共策略时默认创建：

- 独立本金和现金；
- 独立持仓、盈亏和风险预算；
- 不占用其他策略账户资金。

### 6.2 共享组合账户

同一用户可主动将多个策略实例绑定到自己的共享组合账户：

- 策略共享现金池和账户总资产；
- 每个策略实例有 `capital_quota`、`max_position_ratio` 和风险预算；
- 策略配额总和不得超过账户可分配额度；
- 风控先检查策略实例预算，再检查账户总敞口；
- 下单前必须原子预留现金/敞口，避免并发策略超额占用。

## 7. 用户策略运行实例

新增 `v2_user_strategy_instance`：

```text
user_strategy_id              VARCHAR(64) PRIMARY KEY
user_id                       VARCHAR(64) NOT NULL
strategy_version_id           VARCHAR(64) NOT NULL
account_id                    VARCHAR(64) NOT NULL
enabled                       BOOLEAN NOT NULL
execution_mode                VARCHAR(16) NOT NULL
                              # MANUAL / SEMI_AUTO / FULL_AUTO
auto_execute                  BOOLEAN NOT NULL
capital_quota                 NUMERIC NULL
max_position_ratio            NUMERIC NOT NULL
risk_parameters               JSONB NOT NULL
strategy_parameters           JSONB NOT NULL
notification_preferences      JSONB NOT NULL
upgrade_available_version_id  VARCHAR(64) NULL
pending_upgrade_version_id    VARCHAR(64) NULL
upgrade_requested_at          TIMESTAMPTZ NULL
created_at                    TIMESTAMPTZ NOT NULL
updated_at                    TIMESTAMPTZ NOT NULL
```

约束和规则：

- `user_id` 必须与 `account_id` 的 owner 相同；
- 私有策略只能由 owner 创建运行实例；
- 公共策略所有已授权用户可创建运行实例；
- 同一账户内禁止重复启用同一具体策略版本；
- 同一用户可在不同账户中运行同一版本；
- `Strategy/StrategyVersion.status` 只表示发布与治理状态；
- `UserStrategyInstance.enabled` 才表示该用户是否实际运行；
- 平台 circuit breaker 保留为最高优先级总开关；
- 全局 `sim_auto` 迁移为平台总开关，实例级 `auto_execute` 决定具体用户策略是否自动执行。

## 8. 交易归属与执行流程

### 8.1 必须直接保存运行归属的实体

新增字段：

```text
v2_trade_plan.user_strategy_id
v2_trade_plan.account_id
v2_order_intent.user_strategy_id
v2_order_intent.account_id
v2_position.user_strategy_id
v2_position.account_id
```

建议 `v2_approval_request` 同时保存 `user_strategy_id/account_id`，因为部分授权路径不能可靠从 `order_intent_id` 反查。

后续关系可沿现有链追溯：

```text
FillRecord
→ ExecutionRecord.request_id
→ OrderRequest.intent_id
→ OrderIntent.user_strategy_id/account_id
→ UserStrategyInstance.user_id
```

`strategy_version_id` 与 `user_strategy_id` 同时保留：

- 前者回答“使用哪个算法版本”；
- 后者回答“哪个用户的哪次运行实例”。

### 8.2 候选池共享规则

- 公共策略版本在公共参数下产生的候选池可按市场/版本共享，计算一次供多个运行实例消费；
- 用户个性参数只影响资金、仓位、止盈止损等执行参数时，仍可共享候选；
- 个性参数影响 universe、选股规则或入场信号时，候选池必须保存 `user_strategy_id` 并按实例单独计算；
- 创建交易计划时始终进入用户实例边界，生成独立的账户级计划和风控结果。

### 8.3 调度模型

调度器从“市场 × 全局策略”调整为：

1. 市场级 job 仍按固定 cron 触发；
2. job 查询该市场所有 `enabled=true` 的 `UserStrategyInstance`；
3. 按 `strategy_version_id + 可共享参数哈希` 对公共计算分组；
4. 共享候选只计算一次；
5. 对每个用户实例分别做计划、预算、风控、审批和执行；
6. 所有写入携带 `user_strategy_id/account_id`。

必须修复现有“数据库零 ACTIVE 策略时回退硬编码策略”的行为；零可运行实例应明确跳过，不得隐式执行。

## 9. 权限模型

### 9.1 `USER` 业务用户

公开注册默认创建为 `USER`。该角色可执行：

- 查看公共策略；
- 查看和管理自己的私有策略；
- 选择公共策略创建自己的运行实例；
- 管理自己的账户、运行参数、启停、升级和通知偏好；
- 查看自己的计划、审批、订单、成交、持仓、盈亏、复盘和报告。

禁止：

- 修改公共策略；
- 查看或修改其他用户的私有策略、账户或交易数据；
- 使用其他用户的账户；
- 确认其他用户的审批；
- 读取其他用户的 Telegram 绑定或通知记录。

### 9.2 现有角色兼容

- `VIEWER`：继续严格只读，不能创建账户、选择或启停策略；
- `USER`：仅管理自己的业务资源；后端写中间件只允许 `/api/v1/my/*` 和本人 auth 自助端点，其他全局写路径统一拒绝；
- `TRADER`、`RISK_MANAGER`、`RESEARCHER`：保留平台运营职责，但在普通业务 API 中仍受 owner 边界约束；
- `ADMIN`：通过显式管理端点查看全局或执行平台操作。

现有 VIEWER 不自动升级为 USER；历史用户由管理员逐个决定是否变更角色。注册服务端必须硬编码默认 `USER`，不能接受请求体提交角色，防止注册提权。

### 9.3 管理员

管理员通过明确的管理端点：

- 管理公共策略与版本；
- 审核策略公开申请；
- 查看全局运行状态和审计记录；
- 执行平台级急停。

管理员普通业务查询仍应带 owner 边界；全局查看必须走显式 admin API，并写入持久化 `v2_access_audit`（actor_user_id、action、resource_type、resource_id、request_id、created_at；不保存密码、Token 或敏感响应正文）。

## 10. Telegram 安全绑定

### 10.1 平台配置

服务器统一配置：

```env
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_BOT_USERNAME=<public username>
TELEGRAM_WEBHOOK_SECRET=<secret>
```

Token 和 webhook secret 不进入前端、不存用户表、不写日志。

部署约束：

- Telegram webhook 必须使用公网可达、受可信 CA 签发的 HTTPS URL；HTTP 和自签名证书均不满足正式绑定要求；
- 一个 Telegram Bot 同一时刻只能配置一个 webhook；“平台统一一个 Bot Token”指一个共享数据库的逻辑部署；
- 113、159 这类数据库相互独立的部署若都要独立接受绑定，必须各自使用独立 Bot Token，或后续另建共享 Telegram 控制面；首期不实现中央控制面；
- 开启 `TELEGRAM_BINDING_ENABLED` 前必须先配置并验证 `TELEGRAM_WEBHOOK_PUBLIC_URL`，且 URL 必须为 `https://`。

### 10.2 一次性绑定挑战

新增 `v2_telegram_binding_challenge`：

```text
challenge_id       VARCHAR(64) PRIMARY KEY
user_id            VARCHAR(64) NOT NULL
token_hash         VARCHAR(128) NOT NULL UNIQUE
expires_at         TIMESTAMPTZ NOT NULL
consumed_at        TIMESTAMPTZ NULL
created_at         TIMESTAMPTZ NOT NULL
```

流程：

1. 登录用户调用 `POST /auth/me/telegram/challenge`；
2. 后端生成高熵一次性 token，只保存哈希，默认 10 分钟失效；
3. 返回 `https://t.me/<bot_username>?start=<token>`；
4. 用户点击链接并在 Telegram 中 Start；
5. Telegram webhook 携带 `/start <token>`；
6. 后端验证 webhook secret、token 哈希、有效期和未消费状态；
7. 原子绑定 update 中的 `chat_id` 到 challenge 对应 user；
8. 消费 challenge，并使该用户其他未消费 challenge 失效。

约束：

- `v2_user.telegram_chat_id` 增加唯一索引；
- 一个 Telegram chat 只能绑定一个系统用户；
- 用户无需使用 `@userinfobot`，也不能手填任意 chat_id；
- 解绑和重新绑定必须在系统内登录；
- webhook 不接受前端直接声明 user_id。

## 11. 通知偏好与路由

### 11.1 用户级通知偏好

新增 `v2_user_notification_preference`：

```text
user_id                 VARCHAR(64) NOT NULL
channel                 VARCHAR(16) NOT NULL  # TELEGRAM
market_reports_enabled  JSONB NOT NULL        # 按市场开关
system_alerts_enabled   BOOLEAN NOT NULL
quiet_hours             JSONB NULL
timezone                VARCHAR(64) NOT NULL
locale                  VARCHAR(8) NOT NULL
PRIMARY KEY (user_id, channel)
```

`market_reports_enabled` 示例：

```json
{"CN": true, "US": true, "HK": false, "CRYPTO": true}
```

### 11.2 策略实例级偏好

`notification_preferences` 默认完整闭环：

```json
{
  "signal": true,
  "plan": true,
  "approval": true,
  "risk": true,
  "order": true,
  "fill": true,
  "stop_loss": true,
  "take_profit": true,
  "runtime_error": true
}
```

用户可对不同运行实例分别关闭某类消息。

### 11.3 领域事件

通知层消费带归属的事件：

```text
StrategySignalCreated
TradePlanCreated
ApprovalRequested
RiskChecked
OrderSubmitted
OrderRejected
FillRecorded
PositionClosed
StopLossTriggered
TakeProfitTriggered
StrategyRuntimeFailed
```

每个事件至少包含：

```text
event_id
user_strategy_id
account_id
event_type
occurred_at
payload
```

收件人解析必须使用：

```text
user_strategy_id
→ UserStrategyInstance.user_id
→ User.telegram_chat_id
```

禁止使用：

- 所有已绑定用户广播；
- 策略创建者；
- 所有曾选择过该模板的用户；
- 仅凭 `strategy_version_id` 推断收件人。

## 12. 晨晚报

保留两类报告：

### 12.1 公共市场报告

现有 `DailyReport(market, date, type)` 继续作为市场公共事实。发送给满足全部条件的用户：

- 已完成 Telegram 验证绑定；
- 用户级偏好启用该市场报告；
- 在该市场至少有一个 `enabled` 的运行实例。

同一用户在同一市场运行多个策略，也只收到一份公共市场报告。

### 12.2 用户个性报告

按 `user_id/account_id/user_strategy_id` 聚合：

- 用户账户总资产、现金和盈亏；
- 用户启用策略及其配额；
- 用户计划、审批、订单、成交、持仓和复盘；
- 账户级与策略级风险状态。

不得从全局持仓和全局成交直接生成用户报告。

## 13. 可靠通知与错误处理

新增 `v2_notification_event` 与 `v2_notification_delivery`。

### 13.1 Notification Event

```text
event_id             VARCHAR(64) PRIMARY KEY
user_strategy_id     VARCHAR(64) NULL
account_id           VARCHAR(64) NULL
user_id              VARCHAR(64) NOT NULL
event_type           VARCHAR(32) NOT NULL
payload              JSONB NOT NULL
occurred_at           TIMESTAMPTZ NOT NULL
created_at            TIMESTAMPTZ NOT NULL
```

系统级/市场级通知允许 `user_strategy_id` 为空，但必须已有明确 `user_id`。

### 13.2 Notification Delivery

```text
delivery_id          VARCHAR(64) PRIMARY KEY
event_id             VARCHAR(64) NOT NULL
user_id              VARCHAR(64) NOT NULL
channel              VARCHAR(16) NOT NULL
status               VARCHAR(16) NOT NULL  # PENDING / SENDING / RETRY / SENT / FAILED
attempt_count        INTEGER NOT NULL
next_attempt_at      TIMESTAMPTZ NULL
claimed_at           TIMESTAMPTZ NULL
last_error_code      VARCHAR(64) NULL
created_at           TIMESTAMPTZ NOT NULL
sent_at              TIMESTAMPTZ NULL
UNIQUE(event_id, user_id, channel)
```

规则：

- 交易事务内写 `NotificationEvent/Delivery`，不直接请求 Telegram；
- worker 提交发送结果；
- Telegram 故障不得回滚成交；
- 网络失败指数退避；
- Telegram 429 遵循 `retry_after`；
- 401/403 标记绑定不可用并停止无限重试；
- 达到最大重试次数后 `FAILED` 并产生管理员告警；
- worker claim 时写 `claimed_at`；超过 5 分钟仍为 `SENDING` 的记录自动恢复为 `RETRY`，避免进程崩溃后永久卡住；
- 消息正文按用户 locale 生成；
- HTML 模式下对策略名、原因等动态文本转义；
- 日志仅记录 event_id、user_id、错误码，不记录 Token、完整 chat_id 或敏感正文。

## 14. API 草案

### 策略目录与私有策略

```text
GET    /api/v1/strategy-catalog
GET    /api/v1/my/strategies
POST   /api/v1/my/strategies
POST   /api/v1/my/strategies/{strategy_id}/versions
POST   /api/v1/my/strategies/{strategy_id}/publish-request
POST   /api/v1/admin/strategy-publication/{request_id}/approve
POST   /api/v1/admin/strategy-publication/{request_id}/reject
```

### 用户账户

```text
GET    /api/v1/my/accounts
POST   /api/v1/my/accounts
GET    /api/v1/my/accounts/{account_id}
PATCH  /api/v1/my/accounts/{account_id}
```

### 运行实例

```text
GET    /api/v1/my/strategy-instances
POST   /api/v1/my/strategy-instances
GET    /api/v1/my/strategy-instances/{user_strategy_id}
PATCH  /api/v1/my/strategy-instances/{user_strategy_id}
POST   /api/v1/my/strategy-instances/{user_strategy_id}/enable
POST   /api/v1/my/strategy-instances/{user_strategy_id}/disable
POST   /api/v1/my/strategy-instances/{user_strategy_id}/upgrade
```

### Telegram

```text
POST   /api/v1/auth/me/telegram/challenge
GET    /api/v1/auth/me/telegram/status
DELETE /api/v1/auth/me/telegram
POST   /api/v1/integrations/telegram/webhook
GET    /api/v1/my/notification-preferences
PATCH  /api/v1/my/notification-preferences
GET    /api/v1/my/notification-deliveries
```

所有 `/my/` 资源从 JWT 当前用户确定 owner，不接受请求体覆盖 `user_id`。

## 15. 前端信息架构

策略管理分为：

1. **策略广场**：公共策略列表、版本、说明、风险级别、“使用策略”；
2. **我的策略**：用户私有策略、新建、版本、申请公开；
3. **运行中的策略**：账户、版本、参数、启停、升级提示、通知偏好；
4. **我的账户**：共享组合账户和独立策略子账户；
5. **个人中心 / Telegram**：一次性链接绑定、绑定状态、解绑、用户级通知偏好。

启用公共策略向导：

```text
选择公共版本
→ 选择账户模式（默认独立子账户）
→ 设置本金/配额和风险参数
→ 选择执行模式
→ 选择通知类型
→ 确认并启用
```

## 16. 分阶段实施

### 阶段 1：策略定义、账户和运行实例

- 扩展策略 owner/visibility/publication；
- 引入不可变版本；
- 新增用户账户和运行实例；
- 上线公共策略、私有策略、账户和运行实例 API/UI；
- 尚不替换完整交易链时，运行实例只进入 shadow 模式。

### 阶段 2：交易链归属与用户隔离

按顺序改造：

```text
候选消费
→ TradePlan
→ RiskCheck / Approval
→ OrderIntent
→ OrderRequest
→ ExecutionRecord
→ Fill
→ Position
→ Replay / PnL
```

- 所有新写入必须携带运行实例和账户；
- 普通查询默认按当前用户隔离；
- 管理员全局接口独立；
- 共享账户使用数据库锁或原子预算预留；
- 完成后关闭旧全局写入路径。

### 阶段 3：Telegram 安全绑定与定向通知

- 上线一次性 deep link、webhook、chat_id 唯一约束；
- 上线用户级/实例级偏好；
- 新通知链先 shadow 计算收件人但不发送；
- 对比旧广播与新定向路由；
- 只对管理员测试账户启用真实推送；
- 验证后关闭成交和报告的 `broadcast_telegram()`；
- 最后开放全部用户绑定。

### 阶段 4：私有策略公开与版本升级

- 私有策略 owner 权限；
- 公开申请和管理员审核；
- 发布不可变公共版本；
- 新版本提示与用户确认升级；
- 旧持仓默认平仓后再升级。

## 17. 旧数据迁移

1. 现有策略标记为平台公共策略，保持现有策略和版本 ID；
2. 为管理员创建 `legacy` 用户账户和相应运行实例；
3. 现有计划、订单、成交、持仓和复盘按 `strategy_version_id + market` 回填到 legacy 运行实例；
4. 无法可靠归属的数据保留为只读 `legacy_unassigned`，不得武断归给普通用户；
5. 第一阶段新增列允许 NULL，完成逐表回填和孤儿检查后再改为 NOT NULL；
6. 回填脚本必须幂等、可重跑，并输出逐表 source/staged/updated/unassigned 计数；
7. 迁移期间旧 API 可读，但新旧写路径不得同时产生无归属数据；
8. 部署前保留 PostgreSQL 全库备份和应用回滚版本。

## 18. 验收标准

### 18.1 用户隔离

用户 A/B 同时选择同一公共策略：

- 生成不同 `user_strategy_id`；
- 默认生成各自独立子账户；
- 本金、现金、持仓、盈亏和风控相互独立；
- A 无法读取或修改 B 的资源；
- 管理员全局访问产生审计日志。

### 18.2 账户模式

- 独立子账户不占用其他策略资金；
- 共享组合账户中各策略受自身配额约束；
- 账户总敞口不能因并发策略下单超限；
- 现金与风险预算预留具有事务原子性；
- 同一用户可以在不同账户运行同一版本。

### 18.3 策略权限与版本

- 公共策略普通用户只读；
- 私有策略只有 owner 可见和编辑；
- 公开必须经过审核；
- 已运行/发布版本不能原地修改；
- 公共新版本不会自动切换用户实例；
- 用户确认升级后，仅未来计划使用新版本；历史记录仍引用旧版本。

### 18.4 Telegram

- 平台只配置一个 Bot Token；
- 用户通过一次性链接绑定；
- 绑定码过期、复用或被其他用户使用均失败；
- 同一 chat_id 不能绑定多个用户；
- A 的信号、风控、订单、成交和用户报告只发给 A；
- 用户关闭某实例通知类型后不再收到该类消息；
- 公共市场报告只发给在该市场有启用实例且打开订阅的用户；
- Telegram 故障不回滚交易；
- 重试不会重复推送；
- 中文和英文用户收到对应语言文本。

### 18.5 旧数据与回归

- 升级前后旧策略、持仓、成交、复盘逐表计数一致；
- 旧数据成功归入 legacy 账户或明确标记未归属；
- 不产生新的无 `user_strategy_id/account_id` 交易数据；
- 回填可重复运行；
- 原有公共策略执行结果在同样输入和参数下不变；
- 数据库迁移、API、前端、scheduler 和 Telegram worker 均有单元、集成和端到端测试。

## 19. 成功定义

功能完成后，系统能够明确回答任意一笔计划、订单、成交、持仓或通知的以下问题：

```text
属于哪个用户？
属于哪个用户账户？
属于哪个用户策略运行实例？
使用哪个不可变策略版本？
为什么发送或未发送 Telegram？
```

若任一问题只能通过猜测、字符串约定或全局广播回答，则该链路尚未完成多用户隔离。
