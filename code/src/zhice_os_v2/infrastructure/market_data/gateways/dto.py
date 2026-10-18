"""统一行情 DTO（复刻 V1 infrastructure/datacenter_client/dto.py）。

单位口径（务必对齐 V1）：
- daily：DataCenter/tushare 上游为 手/千元；换算后 volume_shares=股、turnover_cny=元。
- minute / latest：上游即 股/元，不换算。
- US/HK/CRYPTO：一律不缩放（股/USD、股/HKD、base 量/USDT）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass
class DailyBarDTO:
    ts_code: str
    trade_date: date
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    pre_close: Decimal | None = None
    change_amount: Decimal | None = None
    pct_change: Decimal | None = None
    volume_shares: Decimal | None = None   # 标准：股
    turnover_cny: Decimal | None = None    # 标准：元（美/港/币为对应本位币）
    adj_type: str = "none"
    source_name: str | None = None


@dataclass
class MinuteBarDTO:
    ts_code: str
    bar_time: datetime
    trade_date: date
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume_shares: Decimal | None = None   # 股
    turnover_cny: Decimal | None = None    # 元
    source_name: str | None = None


@dataclass
class LatestSnapshotDTO:
    ts_code: str
    snapshot_time: datetime
    price: Decimal | None = None
    pre_close: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume_shares: Decimal | None = None   # 股
    turnover_cny: Decimal | None = None    # 元
    trade_count: int | None = None
    bid_price_1: Decimal | None = None
    bid_volume_1: Decimal | None = None
    ask_price_1: Decimal | None = None
    ask_volume_1: Decimal | None = None
    source_name: str | None = None
