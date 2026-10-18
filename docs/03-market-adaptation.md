# 03 · 多市场设计

## 1. 目标

让 CN / US / HK / CRYPTO 成为同一套内核的四个市场实例，而不是四套分支代码。

## 2. MarketProfile

每个市场实现统一协议：

```python
class MarketProfile(Protocol):
    market: str
    tz: ZoneInfo
    currency: str
    settlement: str
    cost_model: CostModel

    def exchanges(self) -> tuple[str, ...]: ...
    def lot_size(self, instrument: Instrument) -> int: ...
    def price_limit_pct(self, instrument: Instrument) -> Decimal | None: ...
    def sessions(self) -> list[Session]: ...
    def trading_date(self, now: datetime | None) -> date: ...
    def fallback_is_open(self, d: date) -> bool: ...
    def calendar_exchange(self) -> str: ...
```

## 3. MarketContext

```python
@dataclass(frozen=True)
class MarketContext:
    market: str
    profile: MarketProfile
    db: DatabaseRouter
    calendar: TradingCalendar
    data_provider: MarketDataProvider
    broker: BrokerPort
```

## 4. 各市场差异

| 维度 | CN | US | HK | CRYPTO |
|---|---|---|---|---|
| 时区 | Asia/Shanghai | America/New_York | Asia/Hong_Kong | UTC |
| 货币 | CNY | USD | HKD | USDT |
| 结算 | T+1 | T+1 | T+2 | T+0 |
| 当日卖出 | 否 | 是 | 是 | 是 |
| 整手 | 100 | 1 | 1（MVP） | 支持小数 |
| 涨跌停 | 有 | 无 | 无 | 无 |
| 交易时段 | 双段 | 单段 | 双段 | 24/7 |
| 数据源 | DataCenter/Tushare | 多源 | 待补强 | Binance 等 |
| 财务数据 | 已完善 | 待接入 | 待接入 | 不适用 |

## 5. 市场能力矩阵

V2 要求所有市场逐步补齐：

- 盘前 / 盘中 / 盘后
- 候选池 / 计划 / 风控 / 执行 / 持仓
- 复盘
- 日报 / 晚报
- 学习闭环
- 晋升 / 降级
- 行情列表 / 分时 / 多周期K线

## 6. 新增市场流程

1. 新增 `MarketProfile`
2. 注册 `MarketContext`
3. 配置数据库路由
4. 配置数据源 Gateway
5. 配置交易日历
6. 配置调度 JOB_SPECS
7. 配置前端市场常量
8. 灌入种子数据
9. 跑通一轮模拟闭环
