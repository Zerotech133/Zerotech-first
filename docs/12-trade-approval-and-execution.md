# 12 · 统一交易推送、授权与执行

## 1. 需求

所有市场、所有策略产生的交易指令，都走同一条“推送 → 授权 → 执行”链路：

```
策略信号 → 交易指令 → 统一推送中心 → 用户授权 → 执行引擎
                                    ↓
                          自动跟单开关 / IM / 弹窗
```

## 2. 设计目标

- 所有市场统一交易推送接口。
- 授权渠道可插拔：系统弹窗为主，IM（微信/QQ/飞书等）作为预留渠道，后期版本实现。
- 支持按“市场 + 策略”配置自动跟单开关。
- 支持模拟交易和实盘交易独立开关，且可同时开启。
- 模拟交易可自动执行，实盘交易默认必须人工授权；人工开启自动跟单后才可自动执行。
- 所有授权行为可审计、可追溯。

## 3. 核心概念

### 3.1 OrderIntent

策略产生的原始交易意图：

```python
class OrderIntent:
    id: str
    strategy_version_id: int
    market: str
    instrument: Instrument
    direction: Direction  # BUY / SELL / CLOSE
    qty: Decimal
    limit_price: Decimal | None
    reason: str
    signal_snapshot: dict
    created_at: datetime
```

### 3.2 ApprovalRequest

统一授权请求：

```python
class ApprovalRequest:
    id: str
    order_intent_id: str
    status: ApprovalStatus  # PENDING / APPROVED / REJECTED / EXPIRED / CANCELLED
    channel: ApprovalChannel  # WEB_POPUP / IM_WECHAT / IM_QQ / IM_FEISHU / AUTO
    token: str
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    reject_reason: str | None
```

### 3.3 ExecutionCommand

授权通过后生成的执行命令：

```python
class ExecutionCommand:
    approval_request_id: str
    order_intent_id: str
    modes: list[ExecutionMode]  # [SIMULATED] / [LIVE] / [SIMULATED, LIVE]
```

## 4. 授权流程

```
┌──────────────┐
│ 策略信号      │
└──────┬───────┘
       ▼
┌──────────────┐
│ OrderIntent  │
└──────┬───────┘
       ▼
┌───────────────────────────────┐
│   TradeApprovalService        │
│   - 创建 ApprovalRequest      │
│   - 按“市场+策略”判断开关      │
└──────┬────────────────────────┘
       │
       ├── 模拟交易开关开启
       │     └── 模拟盘自动执行（默认关闭，需手动开启）
       │
       ├── 实盘自动跟单开启
       │     └── 实盘自动执行
       │
       └── 手动模式
             ├── Web 弹窗（当前主渠道）
             ├── IM 推送（预留）
             └── 超时/撤销
```

实盘默认规则：

- 实盘开关默认关闭。
- 即使实盘开关打开，默认也需要人工授权。
- 只有人工开启该“市场 + 策略”的实盘自动跟单后，实盘才允许自动执行。

## 5. 授权渠道

### 5.1 Web 弹窗

- 交易台实时弹出授权卡片。
- 展示策略、市场、标的、方向、数量、价格、理由、风控摘要。
- 按钮：通过 / 拒绝。
- 可选：备注拒绝原因。

### 5.2 IM 推送（预留）

- 当前版本先不在 IM 渠道落地，但保留统一接口和数据结构。
- 未来支持微信/QQ/飞书等渠道时，只需要新增 Channel Adapter。
- 每次推送带唯一 token，防止误操作。
- 多渠道可同时推送，任一处授权后其余渠道同步失效。

### 5.3 自动跟单

- 按“市场 + 策略”分别配置，每个市场的每个策略都有独立开关。
- 默认全部关闭。
- 开启后不再弹窗，直接进入执行阶段。
- 自动跟单必须配合全局“熔断总开关”和单日单策略上限。

## 6. 执行开关

每个“市场 + 策略”可以有独立的执行模式开关，默认全部关闭：

| 开关 | 默认 | 说明 |
|---|---|---|
| SIMULATED | 关 | 开启后模拟交易可自动执行 |
| LIVE | 关 | 开启后仅代表“允许实盘”，仍需人工授权或开启实盘自动跟单 |
| PAPER | 关 | 可选：纸面交易 |

允许组合：

- 仅模拟
- 仅实盘
- 模拟 + 实盘同时执行

执行规则：

- 模拟交易：只要 `SIMULATED` 开关打开，策略信号可直接自动执行模拟盘。
- 实盘交易：`LIVE` 开关打开后，默认仍需人工授权；只有同时打开该策略的“实盘自动跟单”开关，才允许自动执行实盘。

同一授权通过后，`ExecutionOrchestrator` 会并行向多个 BrokerPort 发送指令。

## 7. 执行编排

```python
class ExecutionOrchestrator:
    def execute(self, command: ExecutionCommand, ctx: MarketContext):
        for mode in command.modes:
            broker = broker_factory.create(mode, ctx.market)
            broker.place_order(ctx, order_intent)
```

每个模式有独立的：

- 账户
- 持仓
- 风控
- 绩效统计

## 8. 幂等与一致性

- `ApprovalRequest.token` 全局唯一。
- 每个 `OrderIntent` 只允许生成一个有效 `ApprovalRequest`。
- 授权回调必须校验 token、状态、过期时间。
- 执行层必须幂等，避免重复下单。

## 9. 审计

所有授权记录写入审计表：

- 谁在什么时间通过/拒绝
- 通过哪个渠道
- 是否自动跟单
- 授权后生成了哪些执行命令
- 模拟/实盘各执行结果

## 10. 界面设计

统一授权中心：

```
┌──────────────────────────────────────────┐
│ 待授权交易                               │
├──────────────────────────────────────────┤
│ [市场] [策略] [标的] [方向] [数量] [价格] │
│ 理由：...                                │
│ [通过] [拒绝]  [自动跟单开关]            │
└──────────────────────────────────────────┘
```
