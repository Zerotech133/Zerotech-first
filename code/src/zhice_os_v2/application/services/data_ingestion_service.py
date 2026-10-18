"""数据接入服务：将 Provider 数据写入 MarketData。"""
from __future__ import annotations

from zhice_os_v2.infrastructure.market_data.memory import MemoryMarketData
from zhice_os_v2.infrastructure.market_data.providers import StaticProvider


class DataIngestionService:
    def __init__(self, market_data: MemoryMarketData) -> None:
        self._market_data = market_data

    def refresh_quotes(self, provider: StaticProvider, symbols: list[str]) -> int:
        quotes = provider.get_quotes(symbols)
        for quote in quotes:
            self._market_data.seed_quote(quote)
        return len(quotes)

    def refresh_daily_bars(self, provider, symbol: str, start, end) -> int:
        bars = provider.get_daily_bars(symbol, start, end)
        self._market_data.seed_bars(symbol, bars)
        return len(bars)
