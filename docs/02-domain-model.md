# 02 · 领域模型

## 1. 设计目标

- 核心领域逻辑不依赖 FastAPI / SQLAlchemy / HTTP。
- 业务规则可以被单元测试直接覆盖。
- 数据库、数据源、券商都是外部端口。

## 2. 聚合根与实体

| 聚合根 | 核心实体 | 关键行为 |
|---|---|---|
| Strategy | StrategyVersion, StrategySpec | 注册、审批、晋升、降级 |
| CandidatePool | CandidateAsset | 生成候选、过滤、排序 |
| TradePlan | Entry/Exit/Invalidation | 创建、审批、拒绝、过期 |
| RiskCheck | RiskDecision | 预交易风控、熔断 |
| Order | OrderIntent, FillRecord | 下单、撤单、成交、幂等 |
| PositionLot | Position | 开仓、加仓、平仓、盈亏 |
| Account | Cash, MarketValue | 入金、估值、日结 |
| ReplayCase | AttributionRecord | 生成复盘、归因、知识化 |
| LearningExperiment | LearningCase | 学习、实验、晋升建议 |

## 3. 核心领域对象示例

### 3.1 StrategySpec

```python
class StrategySpec(BaseModel):
    spec_version: int
    market: str
    universe: Universe
    factors: list[FactorSpec]
    rules: list[RuleSpec]
    params: dict
    risk: RiskSpec
    execution: ExecutionSpec
    backtest_perf: dict
    research_ref: str | None
```

### 3.2 TradePlan

```python
class TradePlan:
    id: TradePlanId
    strategy_version_id: int
    instrument: Instrument
    ref_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    status: PlanStatus
```

### 3.3 PositionLot

```python
class PositionLot:
    id: PositionLotId
    strategy_version_id: int
    instrument: Instrument
    open_qty: Decimal
    open_price: Decimal
    opened_at: datetime
    status: PositionStatus
```

## 4. 领域服务

### 4.1 TradePlanService

```python
class TradePlanService:
    def create_from_candidate(
        self,
        candidate: Candidate,
        policy: ExitPolicy,
        ref_price: Price,
    ) -> TradePlan: ...
```

### 4.2 RiskService

```python
class RiskService:
    def check_plan(self, ctx: MarketContext, plan: TradePlan) -> RiskDecision: ...
```

### 4.3 ExecutionService

```python
class ExecutionService:
    def open_position(self, ctx, plan: TradePlan, quote: Quote) -> OrderResult: ...
    def close_position(self, ctx, lot: PositionLot, quote: Quote, reason: str) -> OrderResult: ...
```

### 4.4 ReplayService

```python
class ReplayService:
    def build_replay_case(self, ctx, lot: PositionLot, fills: list[Fill]) -> ReplayCase: ...
```

## 5. 领域事件

所有状态变更发布领域事件：

- `CandidateGenerated`
- `TradePlanCreated`
- `RiskApproved`
- `OrderFilled`
- `PositionOpened`
- `PositionClosed`
- `ReplayCaseCreated`
- `LearningCaseCreated`
- `StrategyPromoted`
- `StrategyDemoted`

## 6. 不变量

- 同一策略版本同一标的同时最多一个有效 TradePlan。
- 风控未通过不允许下单。
- 平仓必须产生成交记录。
- 账户净值不允许出现负数。
- 跨市场币种不允许直接相加。
