# 05 · 执行与券商抽象

## 1. 目标

模拟盘、纸交易、真实券商都通过同一个 `BrokerPort` 接入。

## 2. BrokerPort

```python
class BrokerPort(Protocol):
    def get_account(self, ctx: MarketContext) -> AccountSnapshot: ...
    def place_order(self, ctx: MarketContext, order: OrderRequest) -> OrderResult: ...
    def cancel_order(self, ctx: MarketContext, order_id: str) -> None: ...
    def get_order_status(self, ctx: MarketContext, order_id: str) -> OrderStatus: ...
    def subscribe_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]: ...
```

## 3. 实现

| 实现 | 说明 |
|---|---|
| SimulatedBroker | 保留现有模拟撮合 |
| PaperBroker | 基于实时行情的纸面交易 |
| AlpacaBroker | 美股实盘/纸交易 |
| IBBroker | 盈透证券 |
| LongbridgeBroker | 港股/美股 |
| BinanceBroker | 加密货币 |

## 4. 订单状态机

```
PENDING → SUBMITTED → PARTIALLY_FILLED → FILLED
              ↓              ↓
          CANCELLED      CANCELLED
              ↓
           REJECTED
```

## 5. 幂等与对账

- 每次下单生成幂等键。
- 成交回报必须关联原始订单。
- 每日对账：本地持仓 vs 券商持仓。
- 异常订单自动告警。

## 6. 模拟/实盘双轨

同一策略可以同时运行：

- 模拟版本：`SimulatedBroker`
- 实盘版本：`RealBroker`（需人工开启）

通过 `ExecutionMode` 区分：

```python
class ExecutionMode(str, Enum):
    M0 = "M0"       # 只研究
    M1 = "M1"       # 模拟盘
    M2 = "M2"       # 纸交易
    M3 = "M3"       # 实盘
```
