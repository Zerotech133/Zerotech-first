"""行情数据源端口。"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from zhice_os_v2.infrastructure.market_data.memory import KlineBar, Quote


class MarketDataProvider(Protocol):
    def get_quotes(self, symbols: list[str]) -> list[Quote]: ...
    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[KlineBar]: ...
    def get_minute_bars(self, symbol: str, trade_date: date) -> list[KlineBar]: ...
