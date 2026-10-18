# 06 · 事件驱动架构

## 1. 为什么需要事件

V1 中，主流程直接调用复盘、学习、报告、通知，导致：

- 主流程过长
- 新增副作用要改主流程
- 难以单独重放

事件驱动后：

- 主流程只发布领域事件
- 复盘/学习/报告/通知成为订阅者
- 可以异步或同步执行
- 关键事件持久化到事件表

## 2. 事件分类

### 2.1 市场数据事件

- `DailyBarIngested`
- `MinuteBarIngested`
- `SnapshotUpdated`
- `MarketDataQualityIssue`

### 2.2 交易事件

- `CandidateGenerated`
- `TradePlanCreated`
- `RiskApproved`
- `RiskRejected`
- `OrderSubmitted`
- `OrderFilled`
- `OrderCancelled`
- `PositionOpened`
- `PositionClosed`
- `PortfolioRebalanced`

### 2.3 策略事件

- `StrategyRegistered`
- `StrategyApproved`
- `StrategyRejected`
- `StrategyPromoted`
- `StrategyDemoted`
- `StrategyPerformanceAlert`

### 2.4 系统事件

- `SchedulerHeartbeat`
- `WorkflowStarted`
- `WorkflowFinished`
- `WorkflowFailed`
- `AlertRaised`

## 3. 事件结构

```python
@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    occurred_at: datetime
    aggregate_type: str
    aggregate_id: str
    payload: dict
    trace_id: str | None
```

## 4. 事件总线

第一版使用进程内事件总线 + 数据库 Outbox：

```
DomainService -> DomainEventBus -> OutboxRepository -> Dispatcher -> Subscribers
```

- 同步订阅者：复盘、审计等需要强一致的副作用
- 异步订阅者：报告、通知、学习等可以延迟的副作用

未来可替换为 Redis Stream 或 Kafka。

## 5. 事件表设计

```sql
CREATE TABLE domain_event (
    id BIGSERIAL PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE,
    event_type VARCHAR(128) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    trace_id VARCHAR(64),
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 6. 事件订阅示例

```python
@subscribe(PositionClosed)
def on_position_closed(event: PositionClosed, ctx: MarketContext):
    replay_service.build_replay_case(ctx, event)
    learning_service.record_outcome(ctx, event)
    report_service.refresh_today_pnl(ctx, event)
    notification_service.notify_if_needed(ctx, event)
```
