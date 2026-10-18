"""数据源适配器。

当前提供：
- StaticProvider：开发/测试
- TushareProvider：A股
- YFinanceProvider：美股/港股
- BinanceProvider：虚拟币

真实 Provider 使用 lazy import，未安装依赖或网络不可用时抛出明确异常。
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from zhice_os_v2.infrastructure.market_data.memory import KlineBar, Quote


def _ts(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class StaticProvider:
    """基于预置数据的 Provider，用于开发/测试。"""

    def __init__(self, quotes: list[Quote] | None = None, bars: dict[str, list[KlineBar]] | None = None) -> None:
        self._quotes = quotes or []
        self._bars = bars or {}

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        return [q for q in self._quotes if q.ts_code in symbols]

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[KlineBar]:
        return self._bars.get(symbol, [])

    def get_minute_bars(self, symbol: str, trade_date: date) -> list[KlineBar]:
        return self._bars.get(symbol, [])


class TushareProvider:
    """Tushare A股数据源。"""

    def __init__(self, token: str | None = None) -> None:
        self._token = token

    def _pro(self):
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError("tushare not installed") from exc
        return ts.pro_api(self._token)

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        # 使用最近日线作为快照近似，真实实时快照可后续扩展
        rows = self._pro().daily(ts_code=",".join(symbols))
        quotes: list[Quote] = []
        if rows is None or rows.empty:
            return quotes
        for _, r in rows.iterrows():
            price = _num(r.get("close"))
            pre_close = _num(r.get("pre_close"))
            quotes.append(
                Quote(
                    ts_code=str(r.get("ts_code", "")),
                    price=price,
                    pre_close=pre_close,
                    volume=_num(r.get("vol")),
                    turnover=_num(r.get("amount")) * 1000,
                    snapshot_time=datetime.now(UTC).isoformat(),
                )
            )
        return quotes

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[KlineBar]:
        rows = self._pro().daily(
            ts_code=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        bars: list[KlineBar] = []
        if rows is None or rows.empty:
            return bars
        for _, r in rows.iterrows():
            bars.append(
                KlineBar(
                    time=str(r.get("trade_date", "")),
                    open=_num(r.get("open")),
                    high=_num(r.get("high")),
                    low=_num(r.get("low")),
                    close=_num(r.get("close")),
                    volume=_num(r.get("vol")),
                )
            )
        return bars

    def get_minute_bars(self, symbol: str, trade_date: date) -> list[KlineBar]:
        # Tushare 分钟线需要更高权限，这里留空实现，后续按权限接入。
        return []


class YFinanceProvider:
    """Yahoo Finance 数据源。"""

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance not installed") from exc
        quotes = []
        for symbol in symbols:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = _num(getattr(info, "last_price", None))
            pre_close = _num(getattr(info, "previous_close", None))
            quotes.append(
                Quote(
                    ts_code=symbol,
                    price=price,
                    pre_close=pre_close,
                    snapshot_time=datetime.now(UTC).isoformat(),
                )
            )
        return quotes

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[KlineBar]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance not installed") from exc
        df = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=False, progress=False)
        return self._df_to_bars(df)

    def get_minute_bars(self, symbol: str, trade_date: date) -> list[KlineBar]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance not installed") from exc
        df = yf.download(symbol, period="1d", interval="1m", progress=False)
        return self._df_to_bars(df)

    @staticmethod
    def _df_to_bars(df) -> list[KlineBar]:
        bars: list[KlineBar] = []
        if df is None or df.empty:
            return bars
        for index, row in df.iterrows():
            bars.append(
                KlineBar(
                    time=index.isoformat(),
                    open=_num(row.get("Open")),
                    high=_num(row.get("High")),
                    low=_num(row.get("Low")),
                    close=_num(row.get("Close")),
                    volume=_num(row.get("Volume")),
                )
            )
        return bars


class BinanceProvider:
    """Binance 数据源。"""

    def __init__(self) -> None:
        self._base = "https://api.binance.com/api/v3"

    def _get(self, path: str, params: dict) -> Any:
        import httpx

        resp = httpx.get(f"{self._base}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_quotes(self, symbols: list[str]) -> list[Quote]:
        quotes = []
        for symbol in symbols:
            data = self._get("/ticker/24hr", {"symbol": symbol.replace("/", "")})
            price = _num(data.get("lastPrice"))
            pre_close = _num(data.get("prevClosePrice"))
            quotes.append(
                Quote(
                    ts_code=symbol,
                    price=price,
                    pre_close=pre_close,
                    volume=_num(data.get("volume")),
                    turnover=_num(data.get("quoteVolume")),
                    snapshot_time=datetime.now(UTC).isoformat(),
                )
            )
        return quotes

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[KlineBar]:
        data = self._get(
            "/klines",
            {
                "symbol": symbol.replace("/", ""),
                "interval": "1d",
                "startTime": int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000),
                "endTime": int(datetime.combine(end, datetime.min.time(), tzinfo=UTC).timestamp() * 1000),
                "limit": 1000,
            },
        )
        return self._klines_to_bars(data)

    def get_minute_bars(self, symbol: str, trade_date: date) -> list[KlineBar]:
        data = self._get(
            "/klines",
            {
                "symbol": symbol.replace("/", ""),
                "interval": "1m",
                "startTime": int(datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC).timestamp() * 1000),
                "endTime": int(datetime.combine(trade_date, datetime.max.time(), tzinfo=UTC).timestamp() * 1000),
                "limit": 1000,
            },
        )
        return self._klines_to_bars(data)

    @staticmethod
    def _klines_to_bars(data: list) -> list[KlineBar]:
        bars = []
        for item in data:
            bars.append(
                KlineBar(
                    time=datetime.fromtimestamp(item[0] / 1000, tz=UTC).isoformat(),
                    open=_num(item[1]),
                    high=_num(item[2]),
                    low=_num(item[3]),
                    close=_num(item[4]),
                    volume=_num(item[5]),
                )
            )
        return bars

