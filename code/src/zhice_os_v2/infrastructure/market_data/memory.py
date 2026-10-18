"""内存行情数据源，用于开发/测试/演示。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quote:
    ts_code: str
    price: float
    pre_close: float
    volume: float = 0
    turnover: float = 0
    snapshot_time: str = ""


@dataclass
class KlineBar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class MemoryMarketData:
    def __init__(self) -> None:
        self.quotes: dict[str, Quote] = {}
        self.bars: dict[str, list[KlineBar]] = {}

    def seed_quote(self, quote: Quote) -> None:
        self.quotes[quote.ts_code] = quote

    def seed_bars(self, ts_code: str, bars: list[KlineBar]) -> None:
        self.bars[ts_code] = bars

    def list_quotes(self) -> list[Quote]:
        return list(self.quotes.values())

    def get_quote(self, ts_code: str) -> Quote | None:
        return self.quotes.get(ts_code)

    def get_bars(self, ts_code: str, limit: int = 300) -> list[KlineBar]:
        return self.bars.get(ts_code, [])[-limit:]
