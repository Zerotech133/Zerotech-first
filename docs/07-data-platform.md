# 07 · 数据平台

## 1. 目标

- 统一多市场数据接入
- 数据质量前置
- 业务层通过 Repository 访问数据
- 支持历史回补、增量更新、实时快照

## 2. 数据流

```
SourceAdapter → NormalizedDTO → QualityCheck → Upsert → PublishDataEvent
```

## 3. Instrument 统一模型

```python
InstrumentId = NewType("InstrumentId", int)

@dataclass(frozen=True)
class Instrument:
    id: InstrumentId
    market: str
    symbol: str
    exchange: str
    currency: str
    lot_size: int | None
    status: str
```

所有业务表通过 `instrument_id` 关联，`symbol` 只做展示冗余。

## 4. Repository 模式

业务代码不直接写 SQL：

```python
class PositionRepository:
    def __init__(self, ctx: MarketContext): ...

    def find_open_by_strategy(self, strategy_version_id: int) -> list[PositionLot]: ...
    def save(self, lot: PositionLot) -> None: ...
```

## 5. 多库策略

保留 V1 的多市场独立数据库：

- `zhice_cn`
- `zhice_us`
- `zhice_hk`
- `zhice_crypto`

但通过 `DatabaseRouter` 统一路由：

```python
class DatabaseRouter:
    def get_session(self, market: str) -> Session: ...
    def get_engine(self, market: str) -> Engine: ...
```

## 6. 数据质量

每张事实表保留：

- `quality_score`
- `quality_flag`
- `source_name`
- `ingest_time`
- `data_version`

质量规则按市场配置，例如：

- CN：涨跌停价格校验
- US：异常成交量校验
- HK：数据完整度校验
- CRYPTO：OHLC 关系校验

## 7. 日历与时钟

- 交易日历按市场独立存储。
- 所有“当前交易日”通过 `MarketProfile.trading_date()` 获取。
- 不允许散落 `date.today()`。

## 8. 历史回补

每个市场提供可重跑的回补脚本：

```bash
python -m tools.backfill --market US --start 2025-01-01 --end 2025-12-31
```

回补流程：

1. 拉取原始数据
2. 标准化
3. 质量检查
4. 幂等 upsert
5. 发布数据事件
