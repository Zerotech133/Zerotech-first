# 管理员账户权限与权益管理设计

- 日期：2026-09-09
- 状态：用户已确认；自审修订完成，待用户复核
- 适用系统：zhice-OS V2
- 目标分支：`feat/admin-entitlement-management`

## 1. 目标

在不混淆 RBAC、会员和策略订阅的前提下，为 `ADMIN` 提供完整、可靠、可审计的账户授权能力：

1. 管理六种既有 RBAC 角色，并补齐前端遗漏的 `USER`。
2. 查看、精确设置、永久授权和撤销指定用户的会员权益。
3. 查看、单项或批量设置、永久授权和撤销平台策略订阅。
4. 管理员写操作必须填写原因，并原子保存请求幂等结果和逐资源审计记录。
5. 角色变更、账户停用或密码变更后，使目标用户旧会话立即失效。
6. 修复“订单已支付但权益发放失败后无法自动补发”的可靠性缺口。
7. 支付完成只发放订单指定权益，永远不自动修改 RBAC 角色。
8. 在现有用户管理页提供清晰的“权限与权益”管理抽屉。

## 2. 已确认的产品决策

1. 采用完整权益管理方案，不建设通用套餐或任意资源 ACL 引擎。
2. 不新增 `SUPER_ADMIN`；`admin` 是用户名，最高角色仍为 `ADMIN`。
3. RBAC、会员、策略订阅是相互独立的三个维度。
4. 撤销会员不撤销用户已有策略订阅。
5. 会员与策略订阅均支持“指定到期时间”和“永久有效”。
6. 会员撤销后立即停用该用户运行中的自建策略实例，但不自动平仓。
7. 管理员可以一次批量授权多个平台策略，批次要么全部成功，要么全部回滚。
8. 管理员的角色和权益写操作均要求原因必填并持久化审计。
9. 角色修改后强制目标用户重新登录。
10. 已支付未发放订单采用“自动重试 + 管理员人工兜底”。
11. 未经额外确认，本功能只在本地实现和验证，不修改生产账户、不部署到 `207.56.221.140`。

## 3. 非目标

本次不实现：

- VIP1～VIPn、套餐组合、优惠券、退款或自动退款；
- 未来新增策略自动继承某次“全部策略”授权；
- 用户私有策略的跨用户分享或 ACL；
- 撤销会员时自动平仓；
- 撤回已经进入通知 outbox 的消息；
- 给非 `ADMIN` 角色配置细粒度权限矩阵；
- 新增账户重新启用功能；
- 改变公开注册默认 `USER` 的现有规则；
- 将付费用户自动升级为 `TRADER`、`ADMIN` 或其他 RBAC 角色。

## 4. 三个实施与发布单元

本能力跨越认证、权益和支付三个故障域，必须分三个可独立测试、可独立提交的单元实施，不能作为一个不可观察的大改动上线。

### 单元 A：会话版本与统一身份解析

- 迁移 `0029`：`v2_user.auth_version`；
- Token 携带会话版本；
- 角色、停用和密码变更递增版本；
- 端点依赖与全局写限制中间件使用同一数据库身份解析结果；
- 不改变会员、订阅和支付模型。

### 单元 B：管理员权益管理与审计

- 迁移 `0030`：永久/撤销/乐观锁字段、管理员操作幂等表、逐资源审计表；
- 统一权益有效性谓词；
- 查询、设置、撤销和批量授权 API；
- 用户管理抽屉和完整双语；
- 不改变支付回调状态机。

### 单元 C：支付发放状态机与补偿

- 迁移 `0031`：支付发放状态、租约、历史歧义复核及产品快照约束；
- 订单级幂等发放；
- 自动重试、崩溃恢复和管理员人工兜底；
- 自助支付页面展示真实发放状态。

本地最终验收覆盖三单元组合。生产发布必须依序 A → B → C，每个单元通过门禁后再启用下一单元；不允许新旧 API 二进制混跑。

## 5. 核心语义

### 5.1 RBAC

保留现有六种角色：

```text
ADMIN
USER
TRADER
RESEARCHER
RISK_MANAGER
VIEWER
```

角色按职责授权，不是可比较大小的数字等级。最后一个有效 `ADMIN` 继续受后端保护。

### 5.2 会员

会员记录只有 `free` 与 `member` 两种 tier。付费会员有效条件统一为：

```text
tier == "member"
AND revoked_at IS NULL
AND (is_permanent = true OR valid_until >= now)
```

数据库不存在会员行时，领域层将其投影为虚拟状态：

```text
tier=free, version=0, effective_status=free
```

首次设置会员要求客户端提交 `version=0` 并创建记录。撤销缺失记录或 free 记录返回 409 `entitlements.membership_not_granted`，不伪造一条 revoked 记录。

### 5.3 策略订阅

某用户对某平台策略的订阅有效条件统一为：

```text
revoked_at IS NULL
AND (is_permanent = true OR valid_until >= now)
```

不存在的订阅投影为 `version=0`。首次设置要求对应策略版本值为 0。

新购买或管理员新授权使用统一的“当前可授权平台策略”谓词：

```text
owner_user_id IS NULL
AND visibility = 'PUBLIC'
AND publication_status = 'PUBLISHED'
AND status = 'ACTIVE'
AND market 属于当前部署启用市场
```

支付下单、管理员授权和前端候选列表必须调用同一服务谓词。对于在策略仍可售时已经创建的订单，支付发放按订单快照履约，即使策略随后暂停；该策略记录不得硬删除。

### 5.4 存储状态与实时状态

API 明确区分：

- `stored_status`：数据库缓存投影，可由到期任务更新；
- `effective_status`：请求时按统一谓词实时计算，取值 `free/active/permanent/expired/revoked`；
- `is_active`：便捷布尔值，来自实时谓词。

所有权限门禁、调度和通知分发只使用实时谓词，不能仅依赖 `stored_status`。free 会员的 `effective_status=free`，即使历史数据库行的 `stored_status=active`。

## 6. 单元 A：会话版本与认证一致性

### 6.1 数据模型（迁移 `0029`）

`v2_user` 新增：

```text
auth_version INTEGER NOT NULL DEFAULT 1
```

### 6.2 Token 兼容规则

- 新 Token 必须携带数据库当前 `auth_version`；
- Token 版本与数据库不一致返回 401 `auth.session_revoked`；
- 为避免部署瞬间踢出全部历史会话，缺少该 claim 的旧 Token 仅在数据库版本仍为 1 时兼容；
- 一旦目标用户版本递增，所有缺少 claim 或版本不一致的旧 Token 都失效；
- 继续兼容并保留 `password_changed_at` 校验。

### 6.3 触发版本递增的动作

以下动作与原业务修改在同一事务递增 `auth_version`：

- 管理员修改角色；
- 管理员停用账户；
- 用户本人修改密码；
- 管理员重置他人密码。

本次不新增账户重新启用 API。停用和密码操作不纳入权益审计表，继续使用现有安全日志/访问审计；其版本递增必须有回归测试。

### 6.4 角色乐观锁与最后管理员保护

角色修改请求使用字段名 `auth_version`，不使用含义模糊的 `version`。

角色降级或账户停用时，为避免两个管理员并发互相降级产生死锁，锁顺序固定为：

1. 按稳定主键顺序锁定全部有效 `ADMIN`；
2. 再锁定目标用户；
3. 检查最后管理员规则与 `auth_version`；
4. 更新角色/状态并递增版本。

所有角色和停用入口必须复用同一仓储事务，禁止保留绕过保护的旧写路径。

### 6.5 统一身份解析

端点依赖和全局中间件在使用角色前统一完成：

1. Token 签名与有效期校验；
2. 数据库读取用户；
3. 用户存在且启用；
4. `auth_version` 匹配；
5. `password_changed_at` 兼容校验；
6. 使用数据库当前角色授权。

不得继续出现端点使用数据库角色、VIEWER/USER 写限制中间件使用 Token 旧角色的分裂语义。

### 6.6 部署约束

单元 A 不支持新旧 API 二进制同时写库。发布时必须短暂停写：停止 API 和 scheduler → 执行 `0029` → 启动全部新实例。回滚应用前须确认没有依赖新 Token 的旧客户端兼容问题；数据库迁移保留 `auth_version`，采用前向修复，不回退安全版本列。

## 7. 单元 B：权益数据模型与审计

### 7.1 `v2_membership`（迁移 `0030`）

新增：

```text
is_permanent       BOOLEAN NOT NULL DEFAULT false
revoked_at         TIMESTAMPTZ NULL
revoked_by_user_id VARCHAR(64) NULL REFERENCES v2_user(user_id)
version            INTEGER NOT NULL DEFAULT 1
```

`stored_status` 约束扩展为 `active/expired/revoked`。

数据库 CHECK 不变量：

1. `free` 必须是 `is_permanent=false`、`valid_until IS NULL`、未撤销；
2. 未撤销的 `member` 必须恰好满足一种期限模式：永久且日期为空，或非永久且日期非空；
3. `revoked_at` 与 `revoked_by_user_id` 必须同时为空或同时非空；
4. `stored_status='revoked'` 当且仅当 `revoked_at IS NOT NULL`；
5. `version >= 1`。

历史异常数据在迁移前由 preflight 报告：`member + NULL 日期`、`free + 非空日期`、未知状态。存在异常即阻止迁移，不静默猜测权益。

### 7.2 `v2_strategy_subscription`

新增与会员相同的：

```text
is_permanent
revoked_at
revoked_by_user_id
version
```

并将 `valid_until` 改为可空。数据库约束保证：

- 永久时日期为空；
- 非永久时日期非空；
- 撤销字段成对；
- `stored_status='revoked'` 与撤销时间一致；
- `version >= 1`；
- 保留 `(user_id, strategy_id)` 唯一约束。

### 7.3 管理员操作幂等表

新增 `v2_admin_operation`，负责请求级幂等，不与逐资源审计混在一张表：

```text
operation_id         VARCHAR(64) PRIMARY KEY
actor_user_id        VARCHAR(64) NOT NULL REFERENCES v2_user(user_id)
idempotency_key      VARCHAR(128) NOT NULL
request_fingerprint  VARCHAR(64) NOT NULL
action               VARCHAR(48) NOT NULL
target_user_id       VARCHAR(64) NOT NULL REFERENCES v2_user(user_id)
response_status      INTEGER NOT NULL
response_body        JSONB NOT NULL
created_at           TIMESTAMPTZ NOT NULL
UNIQUE(actor_user_id, idempotency_key)
```

规则：

- `Idempotency-Key` 去除首尾空白后长度 8～128，只允许可打印 ASCII；
- 指纹覆盖 HTTP method、规范化 action/route、target user 和规范化业务请求体；
- reason trim；策略 ID 去重并排序；versions map 按键排序；日期转 UTC；剔除与 mode 无关字段；
- 使用稳定 JSON 编码后 SHA-256；
- 相同键、相同指纹返回第一次保存的状态码和响应体；
- 相同键、不同指纹返回 409 `entitlements.idempotency_conflict`；
- 操作失败导致业务事务回滚时不保存 completed 记录，客户端可用同一键安全重试；
- 记录长期保留，与审计同生命周期。

### 7.4 逐资源权益审计表

新增 `v2_entitlement_audit`：

```text
audit_id        VARCHAR(64) PRIMARY KEY
operation_id    VARCHAR(64) NULL REFERENCES v2_admin_operation(operation_id)
actor_user_id   VARCHAR(64) NULL REFERENCES v2_user(user_id)
target_user_id  VARCHAR(64) NOT NULL REFERENCES v2_user(user_id)
source_type     VARCHAR(16) NOT NULL  # ADMIN / PAYMENT
action          VARCHAR(48) NOT NULL
resource_type   VARCHAR(24) NOT NULL  # ROLE / MEMBERSHIP / SUBSCRIPTION / PAYMENT_GRANT
resource_id     VARCHAR(128) NULL
reason          VARCHAR(500) NOT NULL
before_state    JSONB NOT NULL
after_state     JSONB NOT NULL
source_order_id VARCHAR(64) NULL
created_at      TIMESTAMPTZ NOT NULL
```

- 一个批量操作写一条 `v2_admin_operation`，每个策略订阅写一条 audit，以 `operation_id` 关联；
- `resource_id` 对订阅就是 `strategy_id`，支持直接过滤和索引；
- 支付来源用 `source_order_id` 建唯一约束，防止同一订单重复写发放审计；
- 权益前后状态与请求响应分离，不把 audit 的 `after_state` 兼作幂等响应；
- 审计表不复制 `notify_raw`/webhook 原文。现有支付原文存储不在本次迁移中删除，但日志和新增审计不得复制签名、Token、密钥或堆栈。

### 7.5 设置、永久与撤销

指定日期授权：

```text
is_permanent=false
valid_until=<UTC future>
revoked_at=NULL
revoked_by_user_id=NULL
stored_status=active
version += 1
```

永久授权：

```text
is_permanent=true
valid_until=NULL
revoked_at=NULL
revoked_by_user_id=NULL
stored_status=active
version += 1
```

撤销保留原期限字段用于审计，但写入：

```text
revoked_at=<UTC now>
revoked_by_user_id=<actor>
stored_status=revoked
version += 1
```

重新授权清除撤销字段。管理员对 inactive 用户：允许查询、改角色和撤销已有权益；禁止新授权/延期，返回 409。支付履约不受该限制：已付款 inactive 用户仍获得订单权益，但账户保持不可登录，重新启用（未来能力）后可使用尚未到期的权益。

### 7.6 会员撤销的交易边界

“立即停用”精确定义为：撤销事务提交后，不允许再产生新的开仓风险；既有持仓不自动平仓，并保留退出/止损等减仓能力。

同一数据库事务必须：

1. 撤销会员；
2. 将该用户所有 enabled 自建策略实例设为 disabled；
3. 取消尚未提交到 broker 的入场计划、审批请求和入场意图；
4. 释放这些被取消入场动作占用的资金预留；
5. 不取消退出、止损、止盈和其他减仓动作；
6. 写逐资源审计。

执行器在每个“开仓提交到 broker”边界前重新检查会员实时状态和实例 enabled，防止撤销事务之前排队、之后才开始执行的任务继续开仓。

已经提交给外部 broker 且尚未成交的开仓单无法与数据库事务原子撤销：

- 撤销事务写入 broker cancel outbox；
- worker 尽快撤单并记录结果；
- API 返回 `pending_external_cancellations`；
- 若撤单竞态中成交，形成的持仓保留在 exit-only 管理中，不做自动反向平仓；
- 外部撤单失败会告警，但不回滚已经生效的会员撤销。

若当前部署仅模拟交易，该 outbox 可以为空，但执行门禁和数据状态测试仍必须存在，保证未来实盘接入不会绕过撤权。

### 7.7 事务与锁顺序

管理员权益写的固定顺序：

```text
幂等 operation identity
→ target user
→ membership
→ subscriptions（strategy_id 升序）
→ instances / pending entry work / reservations
→ audit events
→ operation response
```

任意一步失败全部回滚。角色降级例外采用 §6.4 的“先锁所有有效 ADMIN”顺序。支付写采用 §10.5 的顺序。

### 7.8 迁移与回退

`0030` 是可前向兼容但在产生永久/撤销状态后不可无损 downgrade 的迁移：

- downgrade 前先检查不存在永久或撤销记录；满足时才恢复旧约束并删除新列；
- 检查不满足则明确终止 downgrade，不把永久权益随意换算成日期、不抹掉撤销历史；
- 生产故障采用修复应用或前向迁移，不承诺数据有损回退。

## 8. 单元 B：管理员 API 契约

统一前缀：

```text
/api/v1/admin/entitlements
```

全部接口仅允许 `ADMIN`。写接口要求 `Idempotency-Key`；`reason` trim 后 1～500 字符。

### 8.1 用户权益摘要列表

```http
GET /api/v1/admin/entitlements/users?page=1&page_size=20&query=alice
```

- `page>=1`，`page_size` 默认 20、最大 100；
- 按 `created_at DESC, user_id DESC` 稳定排序；
- 返回用户基本信息、角色、`auth_version`、账号状态、会员 `effective_status` 和有效策略订阅数量；
- 查询失败不得在前端投影为免费用户。

### 8.2 单用户完整权益

```http
GET /api/v1/admin/entitlements/users/{user_id}
```

返回确定结构：

```json
{
  "user": {"user_id":"u-demo","username":"alice","role":"USER","is_active":true,"auth_version":2},
  "membership": {"tier":"member","stored_status":"active","effective_status":"permanent","is_active":true,"is_permanent":true,"valid_until":null,"revoked_at":null,"version":3},
  "subscriptions": [],
  "recent_audits": [],
  "payment_grant_summary": {"pending":0,"retrying":0,"failed":0,"manual_review":0}
}
```

`recent_audits` 固定最多 20 条；完整历史使用审计分页接口。

### 8.3 修改角色

继续使用现有路径，避免创建第二套角色 API：

```http
PUT /api/v1/auth/users/{user_id}/role
Idempotency-Key: <key>
```

```json
{
  "role": "USER",
  "reason": "完成账户审核",
  "auth_version": 2
}
```

- 最后管理员保护、角色更新、`auth_version + 1`、逐资源审计和幂等响应同事务；
- 旧请求体不再允许绕过原因、并发版本和审计；仓库内前端同步升级；
- 修改自身角色成功后，前端立即清理登录态并返回登录页。

### 8.4 设置会员

```http
PUT /api/v1/admin/entitlements/users/{user_id}/membership
Idempotency-Key: <key>
```

指定日期：

```json
{"mode":"until","valid_until":"2027-01-01T00:00:00+08:00","reason":"年度会员授权","version":3}
```

永久：

```json
{"mode":"permanent","reason":"永久会员授权","version":3}
```

- 日期必须带时区且严格晚于服务器当前时间；
- `permanent` 禁止携带日期；
- 缺少会员行使用 `version=0`。

### 8.5 撤销会员

```http
POST /api/v1/admin/entitlements/users/{user_id}/membership/revoke
Idempotency-Key: <key>
```

```json
{"reason":"退款撤销","version":4}
```

返回最新会员状态、取消的数据库入场工作 ID、释放的预留金额、停用实例 ID 和 `pending_external_cancellations`。同一幂等键重放返回原响应；使用新键撤销已撤销会员返回当前状态并记录 `membership_revoke_noop`，但版本不再递增。

### 8.6 批量设置策略订阅

```http
PUT /api/v1/admin/entitlements/users/{user_id}/subscriptions
Idempotency-Key: <key>
```

指定日期：

```json
{
  "strategy_ids":["strategy-us-a","strategy-crypto-b"],
  "mode":"until",
  "valid_until":"2027-09-09T23:59:00+08:00",
  "reason":"组合套餐授权",
  "versions":{"strategy-us-a":1,"strategy-crypto-b":0}
}
```

永久时省略 `valid_until`。规则：

- 策略去重后 1～100 项；
- `versions` 必须与去重后的策略集合完全一致；
- 不存在订阅版本为 0；
- 任一策略不可授权或版本冲突，整批回滚；
- 响应按 `strategy_id` 排序返回每项新状态。

### 8.7 撤销策略订阅

单项和批量均归一到批量契约：

```http
POST /api/v1/admin/entitlements/users/{user_id}/subscriptions/revoke
Idempotency-Key: <key>
```

```json
{
  "strategy_ids":["strategy-us-a","strategy-crypto-b"],
  "versions":{"strategy-us-a":2,"strategy-crypto-b":3},
  "reason":"停止策略服务"
}
```

1～100 项，版本集合必须一致，整批成功或回滚。单项 UI 发送仅含一个元素的批量请求，不再建设重复端点。

### 8.8 审计查询

```http
GET /api/v1/admin/entitlements/audits?page=1&page_size=50&target_user_id=u-demo&resource_type=SUBSCRIPTION&strategy_id=strategy-us-a
```

- `page_size` 默认 50、最大 200；
- 支持 `actor_user_id/action/resource_type/strategy_id/from/to`；
- 时间参数必须带时区；
- 按 `created_at DESC, audit_id DESC` 稳定排序。

### 8.9 旧按月人工发放接口

保留现有两个路径供兼容：

```text
POST /api/v1/admin/billing/grant-membership
POST /api/v1/admin/billing/grant-subscription
```

请求增加 `reason`、对应 `version`，并要求 `Idempotency-Key`；内部复用新事务服务和审计。永久权益收到按月发放时保持永久，不降级。支付失败订单不得走该入口补发，必须使用订单级 retry/review API。

## 9. 单元 B：管理后台界面

入口保持：

```text
/{market}/system?tab=users
```

### 9.1 用户列表

- 补齐 `USER` 中英文角色标签；
- 角色列改为只读标签，避免列表误触；
- 增加会员实时状态摘要；
- 行操作增加“权限与权益”；
- 页面提供“待处理支付”入口。

### 9.2 权限与权益抽屉

抽屉展示用户、RBAC、会员、有效/撤销策略订阅数量，并分四块：

1. 角色权限：六角色、必填原因、明确保存；
2. 会员权益：指定日期/永久、原因、设置和撤销；
3. 策略订阅：平台策略多选、统一期限/永久、批量授权/撤销；
4. 操作记录：管理员、动作、前后值摘要、原因、时间。

三类操作分别提交，不提供伪装成跨接口原子操作的“全部保存”。危险操作二次确认，并明确会员撤销会停止新开仓、保留既有持仓的退出管理且不会撤销策略订阅。

### 9.3 前端请求规则

- 切换用户时清空旧详情并忽略迟到响应；
- 查询失败不降级成“免费/无订阅”；
- 写按钮独立 loading，禁止重复点击；
- 每次用户动作生成一个幂等键；超时重试复用原键；
- 写成功但刷新失败显示“操作已提交，状态刷新失败”，不诱导再次发放；
- 自己降级后立即清理本地登录态并跳转登录页；
- API 日期使用带时区 ISO 8601，界面按当前 locale/timezone 展示。

## 10. 单元 C：支付发放状态机

### 10.1 `v2_payment_order`（迁移 `0031`）

新增：

```text
grant_status                   VARCHAR(20) NOT NULL DEFAULT 'pending'
                               # pending / processing / retrying / granted / failed / manual_review
grant_attempts                 INTEGER NOT NULL DEFAULT 0
automatic_grant_attempts       INTEGER NOT NULL DEFAULT 0
next_grant_retry_at            TIMESTAMPTZ NULL
last_grant_error_code          VARCHAR(128) NULL
last_grant_error_summary       VARCHAR(1000) NULL
grant_lease_until              TIMESTAMPTZ NULL
grant_lease_owner              VARCHAR(128) NULL
historical_grant_ambiguous     BOOLEAN NOT NULL DEFAULT false
grant_reviewed_at              TIMESTAMPTZ NULL
grant_reviewed_by_user_id      VARCHAR(64) NULL REFERENCES v2_user(user_id)
grant_review_decision          VARCHAR(24) NULL  # RETRY / MARK_GRANTED
product_name_snapshot          VARCHAR(255) NOT NULL
market_snapshot                VARCHAR(16) NULL
```

同时增加：

- `product_ref` 对 `v2_strategy.strategy_id` 的可空外键，删除策略采用 RESTRICT；
- CHECK：membership 订单 `product_ref IS NULL`，subscription 订单 `product_ref IS NOT NULL`；
- retry/review 字段组合约束；
- attempts 非负约束。

迁移 preflight 检查历史 subscription 订单不存在孤儿 `product_ref`。存在则阻止迁移并输出订单 ID 清单，不静默删除或猜测策略。

### 10.2 历史数据回填

- `granted_at IS NOT NULL`：`grant_status=granted`；
- 历史 `status=paid AND granted_at IS NULL`：`grant_status=manual_review`、`historical_grant_ambiguous=true`、`next_grant_retry_at=NULL`；
- 其他历史订单：`grant_status=pending`，但未支付状态不会被 worker 扫描；
- 产品名称和市场从当前策略/订单信息回填；无法确定时阻止迁移。

历史歧义订单绝不自动领取，必须由管理员选择：

- `RETRY`：确认尚未补发，进入 pending 并立即执行；
- `MARK_GRANTED`：确认历史已人工满足，只标记订单 granted，不再次修改权益。

两个决策均要求原因、幂等键和审计。

### 10.3 产品生命周期

平台策略一旦存在订阅、pending 订单、paid 未发放订单或支付历史，不允许硬删除，只允许归档/停用。新订单和新管理员授权只允许满足 §5.3 谓词的策略；已经创建的合法订单按不可变快照履约。

订单快照至少保存：策略 ID、展示名、市场、购买月数、成交价格和产品类型。既有 `months/amount_usdt/product_type/product_ref` 参与快照，新增名称和市场用于审计与策略停用后的展示。

### 10.4 完整状态转换

| 当前状态 | 事件 | 下一状态 | 说明 |
|---|---|---|---|
| pending（未支付） | 合法 paid webhook | pending（已支付） | 同时设 `next_grant_retry_at=paid_at` |
| pending/retrying | worker 认领 | processing | 短事务写 lease，并递增 attempts |
| processing | 发放事务成功 | granted | 权益、审计、订单状态同事务 |
| processing | 发放失败 | retrying/failed | 记录稳定错误摘要和下次时间 |
| processing 且 lease 过期 | worker 回收 | processing | 新 owner 认领，继续幂等发放 |
| manual_review | 管理员 RETRY | pending | 清除歧义，due now |
| manual_review | 管理员 MARK_GRANTED | granted | 不修改权益，记录人工确认 |
| granted | 任意重试 | granted | 返回已发放，不重复权益 |
| failed | 人工 retry | pending | 自动次数不清零，人工可继续 |

`processing` 且租约未过期不能被其他 worker 或人工重试抢占，返回/跳过 `grant_in_progress`。

### 10.5 认领、发放与崩溃恢复

采用短认领事务：

1. 扫描 due 的 `pending/retrying`，以及 lease 已过期的 `processing`；
2. `FOR UPDATE SKIP LOCKED` 认领订单，写 `processing`、owner、5 分钟 lease；
3. 自动 worker 认领时同时递增 `grant_attempts` 和 `automatic_grant_attempts`；人工认领只递增总次数；
4. 提交认领事务；
5. 发放事务按固定顺序锁定 `order → target user → membership/subscription → audit`；
6. 校验 lease owner、订单 paid、策略快照和当前 grant 状态；
7. 原子提交权益、PAYMENT audit、`grant_status=granted`、`granted_at` 和清除 lease；
8. 失败后用独立短事务把同一 lease owner 的订单改为 retrying/failed，保存稳定错误码和最多 1000 字摘要，不保存异常栈、SQL、密钥或 webhook 原文；
9. worker 在步骤 2 后崩溃，lease 到期后由扫描器回收 processing 状态。

两个订单或管理员与支付同时写同一用户权益时，target user 是每用户串行化点。不存在的权益行也因此不会产生并发首次插入竞态。数据库唯一冲突只捕获明确的唯一约束异常，不能把任意数据库异常误报为重复。

### 10.6 重试策略

自动最大尝试 8 次，退避分钟数：

```text
1, 2, 5, 10, 30, 60, 180, 360
```

- 新 paid 订单 due now；即时发放也是第一次自动尝试；
- 自动失败达到 8 次后进入 failed 并告警；
- 人工重试不增加 `automatic_grant_attempts`，不受其上限限制；
- 人工重试仍使用订单级幂等，不会重复延长权益；
- 永久权益收到月费订单时，订单标记已发放，权益保持永久；
- 已撤销或过期权益续费从当前时间开始并清除撤销；
- 尚有效的定期权益从原到期时间追加 `30 × months` 天。

### 10.7 Webhook 响应

Webhook 成功入账后立即触发一次发放认领。若权益发放失败，但 paid 状态和重试信息已可靠保存，则仍向网关返回 `ok`，避免依赖外部重复回调充当内部队列。验签、金额不足、未知订单等入账前错误继续返回现有业务错误。

## 11. 单元 C：支付管理 API

### 11.1 待处理列表

```http
GET /api/v1/admin/entitlements/payment-grants?page=1&page_size=50&grant_status=failed
```

- 默认返回 paid 且非 granted 的订单；
- 支持 `user_id/grant_status/from/to`；
- `page_size` 默认 50、最大 200；
- 按 `paid_at DESC, order_id DESC` 稳定排序；
- 普通用户不能读取内部错误详情。

### 11.2 人工重试

```http
POST /api/v1/admin/entitlements/payment-grants/{order_id}/retry
Idempotency-Key: <key>
```

```json
{"reason":"自动重试失败，人工恢复"}
```

- 适用于 failed/retrying/pending 且非历史歧义的 paid 订单；
- processing 且 lease 未过期返回 409；
- granted 返回当前结果，不重复发放；
- historical ambiguous 返回 409 `billing.grant_review_required`。

### 11.3 历史歧义复核

```http
POST /api/v1/admin/entitlements/payment-grants/{order_id}/review
Idempotency-Key: <key>
```

重试发放：

```json
{"decision":"RETRY","reason":"核对后确认未发放"}
```

确认已满足：

```json
{"decision":"MARK_GRANTED","reason":"历史订单已于人工补单中满足"}
```

只允许 `manual_review + historical_grant_ambiguous=true`。决策、管理员、时间、原因和订单前后状态进入审计。

### 11.4 用户自助订单状态

订单查询响应增加：

```text
grant_status
grant_pending（布尔）
```

仅当 `grant_status=granted` 时前端显示“权益已到账”。paid 的 pending/processing/retrying 显示“支付已确认，权益发放中”；failed 显示“支付已确认，权益发放异常，已通知管理员”，不暴露内部错误摘要。

## 12. 错误约定

继续使用现有国际化结构：

```json
{"detail":{"code":"..."}}
```

- 401：Token 缺失、无效、会话版本过期；
- 403：非 `ADMIN`；
- 404：目标用户、策略、权益或订单不存在；
- 409：乐观锁、幂等载荷冲突、最后管理员、状态冲突、inactive 用户新授权、历史订单待复核；
- 422：字段、模式组合、时区、原因、幂等键或批量数量错误；
- 400：平台策略资格、产品快照等业务拒绝。

所有新增错误码在中英文资源中同步定义。响应不得包含数据库错误、异常栈、密钥或原始 webhook。

## 13. 测试与验收

### 13.1 单元 A

- `0029` upgrade；
- 新旧 Token 兼容及版本递增后失效；
- 角色、停用、本人改密、管理员重置密码均递增版本；
- 中间件和端点依赖使用数据库当前身份；
- 两名管理员并发互相降级/停用不会死锁，也不会留下零管理员；
- 新旧 API 版本不得混跑的部署检查。

### 13.2 单元 B

- `0030` preflight、upgrade、受限 downgrade；
- 缺失会员行投影为 free/version 0；
- 指定日期、永久、过期、撤销、重新授权；
- `stored_status/effective_status` 分离；
- 非管理员 403；原因、幂等键、版本校验；
- 相同幂等请求重放，不同载荷 409；
- 批量操作写一个 operation、多条逐策略 audit；
- 多策略授权/撤销全有或全无；
- inactive 用户新授权拒绝、撤销允许；
- 会员和策略订阅保持独立；
- 撤销会员后实例停用、在途开仓取消、预留释放、退出动作保留；
- 执行器提交开仓前二次门禁；
- 外部撤单 outbox 与竞态成交进入 exit-only；
- 当前可授权策略谓词在支付、管理员和目录三处一致。

### 13.3 单元 C

- `0031` preflight、历史回填和孤儿策略阻断；
- 新 paid 订单 due now；
- processing lease 崩溃后可回收；
- 两 worker `SKIP LOCKED` 不重复发放；
- 两订单及管理员/支付并发写同一权益不丢更新；
- 会员订单只修改会员，策略订单只修改指定订阅；
- 两类订单支付前后 `v2_user.role` 不变；
- 自动 8 次退避、failed 告警、人工恢复；
- webhook、自动重试、人工重试只发一次；
- 历史歧义未复核不执行；RETRY 与 MARK_GRANTED 均可审计；
- inactive 用户已付款仍履约但不可登录；
- 永久权益收到月费订单不降级；
- 平台策略有订单/订阅历史时禁止硬删除；
- PostgreSQL 真并发验证，不只依赖 SQLite。

### 13.4 前端与浏览器 E2E

1. `USER` 出现在中英文角色选项；
2. 非管理员看不到入口；
3. `admin` 打开用户权益抽屉并看到实时状态；
4. 将测试账户改成 `USER`，旧会话立即失效；
5. 设置永久会员；
6. 批量授权两个平台策略；
7. 测试账户重新登录后看到正确权益；
8. 撤销会员后新开仓停止、实例停用、既有订阅仍有效；
9. 审计显示管理员、逐资源前后值、原因和时间；
10. 用户切换无陈旧响应污染；
11. 请求超时复用幂等键，不重复授权；
12. 模拟支付发放失败，验证自动重试、failed、人工 retry；
13. 历史歧义订单必须明确复核；
14. 自助支付只有 grant_status=granted 才显示权益到账。

## 14. 实施与生产门禁

### 14.1 本地实施顺序

1. 单元 A：迁移、Token、统一身份解析和并发测试；
2. 单元 B：迁移、领域谓词、事务仓储、审计、API、前端；
3. 单元 C：迁移、订单状态机、worker、管理 API、自助页面；
4. 全套 Python、前端、真实 PostgreSQL 并发与浏览器 E2E；
5. Python、TypeScript、数据库、安全和综合代码复核。

### 14.2 生产前置检查

在任何远程部署前必须另行获得明确授权，并完成：

- 数据库备份；
- 当前迁移版本与三份 migration preflight；
- 历史权益异常清单；
- subscription 订单孤儿策略清单；
- 历史 `paid + granted_at NULL` 歧义订单只读清单；
- 确认生产没有旧 API 实例混跑；
- 确认 scheduler 与 API 使用同一代码版本；
- 确认平台策略硬删除路径已受 RESTRICT/业务保护。

### 14.3 生产发布和观察

依序发布 A、B、C。每个单元完成迁移、启动、健康检查和专项 smoke test 后才进入下一单元。C 启用自动 worker 时，只扫描迁移后确认为非歧义的新订单；历史歧义订单保持 manual_review，必须人工逐单决策。

迁移 `0030/0031` 产生永久、撤销或复核状态后采用前向修复，不承诺无损数据库 downgrade。应用回退不得绕过新列和新状态，必须使用明确的兼容版本。
