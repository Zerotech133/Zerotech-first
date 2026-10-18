"""US 网关（复刻 V1 USGateway 核心路径）：日线 Nasdaq→yfinance；快照 Finnhub→yfinance。

Nasdaq 是 V1 库里 US 的主 source（免 key，需浏览器 UA/Referer）。US 分钟走 yfinance
1m 盘中（免 key，逐 code 有限流，故上层限小宇宙）。单位一律不缩放（股 / USD）。
符号：ts_code=裸 ticker（AAPL），yfinance 用 _yf_symbol（. → -）。
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests

from zhice_os_v2.config.settings import get_settings

from .dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO

_NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def _dec(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, float) and v != v:
            return None
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _num(v: object) -> Decimal | None:
    if v is None:
        return None
    s = str(v).replace("$", "").replace(",", "").strip()
    return _dec(s) if s else None


def _bar_ts_utc(idx: object) -> datetime | None:
    """pandas 索引(bar 时间戳) → UTC datetime。非 datetime/解析失败返 None（回退 now）。"""
    try:
        bt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(bt, datetime):
        return None
    return bt.astimezone(UTC) if bt.tzinfo is not None else bt.replace(tzinfo=UTC)


class NasdaqDailyClient:
    """Nasdaq 历史日线（免 key，需浏览器头）。"""

    @staticmethod
    def _sym(ts: str) -> str:
        return ts.replace("_", ".")

    def daily(self, ts: str, limit: int = 60) -> list[DailyBarDTO]:
        today = datetime.now(UTC).date()
        frm = today - timedelta(days=max(limit * 2, 90))
        try:
            r = requests.get(
                f"https://api.nasdaq.com/api/quote/{self._sym(ts)}/historical",
                headers=_NASDAQ_HEADERS, timeout=20,
                params={"assetclass": "stocks", "fromdate": frm.isoformat(),
                        "todate": today.isoformat(), "limit": "9999"},
            )
            r.raise_for_status()
            rows = (((r.json() or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
        except Exception:  # noqa: BLE001
            return []
        out = []
        for row in rows:
            try:
                td = datetime.strptime(row["date"], "%m/%d/%Y").date()
            except (ValueError, KeyError, TypeError):
                continue
            close = _num(row.get("close"))
            vol = _num(row.get("volume"))
            turnover = (close * vol) if (close and vol) else None
            out.append(DailyBarDTO(
                ts_code=ts, trade_date=td, open=_num(row.get("open")),
                high=_num(row.get("high")), low=_num(row.get("low")), close=close,
                volume_shares=vol, turnover_cny=turnover, adj_type="none", source_name="nasdaq",
            ))
        out.sort(key=lambda b: b.trade_date)
        return out[-limit:]


class FinnhubQuoteClient:
    def __init__(self, token: str) -> None:
        self._token = token

    @staticmethod
    def _sym(ts: str) -> str:
        return ts.replace("_", ".")

    def snapshot(self, ts: str) -> LatestSnapshotDTO | None:
        if not self._token:
            return None
        try:
            import httpx
            r = httpx.get("https://finnhub.io/api/v1/quote", timeout=8.0,
                          params={"symbol": self._sym(ts), "token": self._token})
            r.raise_for_status()
            d = r.json()
        except Exception:  # noqa: BLE001
            return None
        price = _dec(d.get("c"))
        if not price or price == 0:
            return None
        st = datetime.fromtimestamp(d["t"], tz=UTC) if d.get("t") else datetime.now(UTC)
        return LatestSnapshotDTO(
            ts_code=ts, snapshot_time=st, price=price, pre_close=_dec(d.get("pc")),
            open=_dec(d.get("o")), high=_dec(d.get("h")), low=_dec(d.get("l")),
            source_name="finnhub",
        )


def _yf_symbol(ts: str) -> str:
    return ts.replace("_", "-").replace(".", "-")


def _yf_daily(ts: str, limit: int = 60) -> list[DailyBarDTO]:
    try:
        import yfinance as yf
        today = datetime.now(UTC).date()
        frm = today - timedelta(days=max(limit * 2, 90))
        df = yf.Ticker(_yf_symbol(ts)).history(
            start=frm.isoformat(), end=(today + timedelta(days=1)).isoformat(), auto_adjust=False)
    except Exception:  # noqa: BLE001
        return []
    out: list[DailyBarDTO] = []
    if df is None or df.empty:
        return out
    for idx, row in df.iterrows():
        td = idx.date() if hasattr(idx, "date") else None
        if td is None:
            continue
        out.append(DailyBarDTO(
            ts_code=ts, trade_date=td, open=_dec(row.get("Open")), high=_dec(row.get("High")),
            low=_dec(row.get("Low")), close=_dec(row.get("Close")),
            volume_shares=_dec(row.get("Volume")), adj_type="none", source_name="yfinance",
        ))
    return out[-limit:]


def _yf_snapshot(ts: str) -> LatestSnapshotDTO | None:
    try:
        import yfinance as yf
        df = yf.Ticker(_yf_symbol(ts)).history(period="1d", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    price = _dec(row.get("Close"))
    if not price or price == 0:
        return None
    # 新鲜度护栏依赖源 tick 时间：用 bar 时间戳 df.index[-1]，非抓取时刻 now
    src_dt = _bar_ts_utc(df.index[-1])
    return LatestSnapshotDTO(
        ts_code=ts, snapshot_time=src_dt or datetime.now(UTC), price=price,
        open=_dec(row.get("Open")), high=_dec(row.get("High")), low=_dec(row.get("Low")),
        volume_shares=_dec(row.get("Volume")), source_name="yfinance",
    )


def _yf_minute(ts: str, trade_date: str) -> list[MinuteBarDTO]:
    """yfinance 1m 盘中分钟线（period=1d=最近一个交易日）。

    trade_date 作**宽松**过滤：若返回 bar 里存在 target 当日（交易所本地日）则只取当日；
    否则全取。这样避免宿主时区≠交易所时区时（US 盘中跨宿主午夜）把当日实时分钟误丢。
    trade_date 一律用 bar 自身交易所本地日，bar_time 归一到 UTC。
    """
    try:
        import yfinance as yf
        df = yf.Ticker(_yf_symbol(ts)).history(period="1d", interval="1m", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    try:
        target = date.fromisoformat(trade_date)
    except (ValueError, TypeError):
        target = None
    parsed: list[MinuteBarDTO] = []
    for idx, row in df.iterrows():
        bar_date = idx.date() if hasattr(idx, "date") else None
        if bar_date is None:
            continue
        bt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        bt = bt.astimezone(UTC) if bt.tzinfo is not None else bt.replace(tzinfo=UTC)
        close = _dec(row.get("Close"))
        if not close or close == 0:
            continue
        parsed.append(MinuteBarDTO(
            ts_code=ts, bar_time=bt, trade_date=bar_date, open=_dec(row.get("Open")),
            high=_dec(row.get("High")), low=_dec(row.get("Low")), close=close,
            volume_shares=_dec(row.get("Volume")), source_name="yfinance",
        ))
    if target is not None:
        same_day = [b for b in parsed if b.trade_date == target]
        if same_day:
            return same_day
    return parsed


def _us_exchange_map() -> dict[str, str]:
    """{ticker: exchange} 从 v2_instrument 读（供东财 US secid 前缀映射）。失败返回空。"""
    import psycopg2

    from zhice_os_v2.config.settings import get_settings
    conn = None
    try:
        conn = psycopg2.connect(get_settings().db_main_url)
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, exchange FROM v2_instrument WHERE market='US'")
            return {s: e for s, e in cur.fetchall()}
    except Exception:  # noqa: BLE001
        return {}
    finally:
        if conn is not None:
            conn.close()


class USGateway:
    def __init__(self) -> None:
        from .eastmoney import EastMoneyUSClient
        self._nasdaq = NasdaqDailyClient()
        self._finnhub = FinnhubQuoteClient(get_settings().finnhub_token)
        self._em = EastMoneyUSClient()      # 分钟主源（东财）
        self._ex_map: dict[str, str] | None = None

    def close(self) -> None:
        self._em.close()

    def _exchange(self, ts: str) -> str:
        if self._ex_map is None:
            self._ex_map = _us_exchange_map()
        return self._ex_map.get(ts, "NASDAQ")

    def get_daily_bars_batch(
        self, ts_codes: list[str], limit: int = 60, start: str | None = None, end: str | None = None
    ) -> dict[str, list[DailyBarDTO]]:
        out: dict[str, list[DailyBarDTO]] = {}
        for ts in ts_codes:
            # yfinance 优先、nasdaq 兜底：部分部署环境 api.nasdaq.com 不可达，
            # nasdaq 优先会每标的白等整整 timeout(20s)才落到 yfinance——全市场慢抄
            # 下会把单轮拖到数十小时。yfinance 稳定可达(~1s)，数据口径一致。
            bars = _yf_daily(ts, limit=limit) or self._nasdaq.daily(ts, limit=limit)
            if bars:
                out[ts] = bars
        return out

    def get_latest_snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        out: dict[str, LatestSnapshotDTO] = {}
        for ts in ts_codes:
            snap = self._finnhub.snapshot(ts) or _yf_snapshot(ts)
            if snap is not None:
                out[ts] = snap
        return out

    def get_minute_bars(self, ts_code: str, trade_date: str) -> list[MinuteBarDTO]:
        return (self._em.minute(ts_code, self._exchange(ts_code), trade_date)
                or _yf_minute(ts_code, trade_date))
