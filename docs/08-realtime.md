# 08 · 实时行情

## 1. 目标

提供：

- 行情列表实时更新
- 个股快照实时更新
- 分时数据
- 多周期K线切换
- 最新一根K线增量更新

## 2. 传输方式

保留 REST 查询：

```
GET /api/v1/market/quotes
GET /api/v1/market/snapshot/{ts_code}
GET /api/v1/market/kline/{ts_code}
```

新增 WebSocket/SSE：

```
WS /ws/market/quotes
WS /ws/market/{ts_code}
```

## 3. 订阅模型

```ts
interface MarketChannel {
  channel: 'quotes' | 'security';
  market: string;
  symbol?: string;
}

// 客户端订阅
ws.send(JSON.stringify({ type: 'subscribe', channel: 'quotes', market: 'CN' }));

// 服务端推送
ws.send(JSON.stringify({
  type: 'snapshot',
  market: 'CN',
  symbol: '600519.SH',
  price: 1700.01,
  pct_change: 1.23,
  snapshot_time: '...',
}));
```

## 4. K线接口

统一接口：

```
GET /api/v1/market/kline/{ts_code}?market=CN&period=5m&limit=300
```

周期：

- 1m / 5m / 15m / 30m / 60m
- day / week

服务端从 1 分钟原始数据重采样，不额外落多套周期表。

## 5. 前端组件

- 行情列表：REST 首屏 + WebSocket 增量更新
- 个股详情：REST 拉历史K线 + WebSocket 推最新快照/最新K线
- 分时图：当日 1 分钟收盘价连线
- K线图：SVG/Canvas 蜡烛图 + 成交量副图

## 6. 数据源适配

每个市场 gateway 需要提供：

```python
class MarketDataProvider(Protocol):
    def get_latest_snapshot_batch(self, symbols: list[str]) -> dict[str, LatestSnapshot]: ...
    def get_minute_bars(self, symbol: str, trade_date: date) -> list[MinuteBar]: ...
    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...
    def subscribe(self, symbols: list[str]) -> AsyncIterator[Snapshot]: ...
```
