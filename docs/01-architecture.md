# 01 · 目标架构

## 1. 总体分层

```
┌──────────────────────────────────────────────────────────┐
│                       Frontend 工作台                     │
│  交易 / 资产 / 研究 / 系统 / 运维 / 大屏 / 实时行情        │
└───────────────┬──────────────────────────────────────────┘
                │ HTTP / WebSocket / SSE
┌───────────────▼──────────────────────────────────────────┐
│                    API Gateway / BFF                      │
│  鉴权 · 路由 · 限流 · 版本 · 实时推送代理                 │
└───────────────┬──────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────┐
│                  Application Services                     │
│  行情 · 候选/计划 · 风控 · 执行 · 持仓 · 复盘 · 学习      │
│  策略注册 · 账户 · 治理 · 自动化 · 报告                   │
└───────────────┬──────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────┐
│                      Domain Core                         │
│  Strategy · Candidate · Plan · Risk · Order · Position   │
│  Replay · Account · MarketProfile · Event                │
│  （纯 Python，不依赖 FastAPI/SQLAlchemy）                │
└───────────────┬──────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────┐
│                    Ports & Adapters                       │
│  DB Repository · MarketDataProvider · BrokerPort         │
│  Notifier · Reporter · ResearchGateway · Cache           │
│  Calendar · Clock · IdGenerator                          │
└───────────────┬──────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────┐
│              Infrastructure / Runtime                     │
│  PostgreSQL · Redis · APScheduler · Docker                │
│  事件表/Outbox · 文件存储 · 日志/Trace · 审计             │
└──────────────────────────────────────────────────────────┘
```

## 2. 模块化单体

V2 采用**模块化单体**，而不是微服务。

原因：

- 交易闭环需要强一致性，微服务会引入分布式事务。
- 个人系统运维成本低更重要。
- 当前瓶颈不是并发，而是代码边界。
- 模块化单体可以保留“未来拆服务”的可能。

## 3. 关键组件

### 3.1 MarketContext

所有应用服务通过 `MarketContext` 获取市场相关能力：

```python
@dataclass(frozen=True)
class MarketContext:
    market: str
    profile: MarketProfile
    db: DatabaseRouter
    calendar: TradingCalendar
    data_provider: MarketDataProvider
    broker: BrokerPort
    currency: str
    timezone: ZoneInfo
```

### 3.2 Pipeline

工作流以可配置流水线表达：

```python
INTRADAY_PIPELINE = [
    capture_snapshots,
    refresh_universe_snapshots,
    evaluate_entry_signals,
    evaluate_spec_exits,
    evaluate_static_exits,
    execute_orders,
    update_positions,
    publish_market_snapshot,
    send_alerts,
]
```

### 3.3 Event Bus

领域事件解耦副作用：

```python
@dataclass
class PositionClosed:
    market: str
    position_lot_id: int
    strategy_version_id: int
    ts_code: str
    pnl: Decimal
    closed_at: datetime
```

事件订阅者：

- 复盘服务
- 学习服务
- 报告服务
- 通知服务
- 审计服务

### 3.4 Strategy Executor

统一策略执行接口：

```python
class StrategyExecutor(Protocol):
    def generate_candidates(
        self,
        ctx: MarketContext,
        spec: StrategySpec,
        trade_date: date,
    ) -> list[CandidateSignal]: ...
```

## 4. Ontology & Action 层

在领域模型之上引入 Palantir 式“本体 + 行动”：

- `Ontology`：统一对象、关系、属性。
- `Action`：把交易、审批、策略注册、数据维护等统一建模为可审计行动。
- `Provenance`：数据血缘。
- `Governance`：权限、审批、审计、熔断。

详见 `docs/15-palantir-inspired-design.md`。

## 5. 模块划分

```
apps/
  api/              FastAPI 入口与路由
  scheduler/        调度入口
  realtime/         WebSocket/SSE 网关
domain/
  strategy/
  candidate/
  planning/
  risk/
  execution/
  portfolio/
  replay/
  learning/
  account/
  market/
  event/
application/
  services/         用例编排
  pipelines/        工作流流水线
infrastructure/
  db/
  repository/
  market_data/
  broker/
  cache/
  notifier/
  reporter/
  research/
  observability/
frontend/
  src/
    features/
```

## 5. 部署形态

```
nginx (web)
api
scheduler-cn
scheduler-us
scheduler-hk
scheduler-crypto
realtime-gateway (可选)
redis
postgres
autoheal
```

## 6. 演进边界

V2 可以先独立存在，再逐步与 V1 共享数据/迁移能力。
