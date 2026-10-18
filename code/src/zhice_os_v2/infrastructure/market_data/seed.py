"""演示数据种子。"""
from __future__ import annotations

from zhice_os_v2.infrastructure.market_data.memory import KlineBar, MemoryMarketData, Quote


def seed_demo_data(data: MemoryMarketData) -> None:
    symbols = ["600519.SH", "000001.SZ", "AAPL", "00700.HK", "BTC/USDT"]
    for i, symbol in enumerate(symbols):
        price = 100.0 + i * 50
        pre_close = price - 2
        data.seed_quote(Quote(ts_code=symbol, price=price, pre_close=pre_close, volume=100000, turnover=price * 100000, snapshot_time="2026-08-18T08:00:00Z"))
        data.seed_bars(
            symbol,
            [
                KlineBar(
                    time=f"2026-08-18T09:{m:02d}:00Z",
                    open=pre_close + 0.5,
                    high=pre_close + 3,
                    low=pre_close - 1,
                    close=pre_close + 1 + m * 0.1,
                    volume=1000 + m * 100,
                )
                for m in range(30)
            ],
        )
