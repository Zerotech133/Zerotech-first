"""回测数据加载 — 只读 zhice-db bar 表。

核实结论（2026-10-16 information_schema 实查）：
- v2_bar_minute：CRYPTO 37 标的 × 1m（binance 源，2026-08-21~10-16，234 万行）→
  15m/1h 由 1m pandas 重采样（向量化，无逐 bar SQL）。
- v2_bar_daily：CRYPTO 37 标的日线 → regime 门（market_regime_permits_entry）输入。
- 15m 缺数据时自动降 1h（报告注明 freq_fallback=True）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

# 档位 → 1m 重采样规则（pandas 3.x 频率串小写）
FREQ_RESAMPLE_RULE: dict[str, str] = {
    "5m": "5min",
    "15m": "15min",
    "1h": "60min",
}
FREQ_ORDER = ["5m", "15m", "1h"]  # 降级方向（越右越粗）


@dataclass
class BarData:
    """回测输入数据（多标的宽表，dt 升序，列为 OHLCV + symbol）。"""

    market: str
    freq: str                      # 实际生效档位（可能降级）
    requested_freq: str
    freq_fallback: bool            # True = 15m 缺数据自动降 1h
    symbols: list[str]
    bars: pd.DataFrame             # 列: dt/open/high/low/close/volume/symbol
    daily: pd.DataFrame | None = None   # 日线（regime 门用），列 trade_date/symbol/close
    fallback_reason: str = ""

    def by_symbol(self) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for sym, grp in self.bars.groupby("symbol", sort=False):
            out[str(sym)] = grp.drop(columns=["symbol"]).reset_index(drop=True)
        return out


def _connect(db_url: str):
    from zhice_os_v2.agent_platform.db import shared_engine

    return shared_engine(db_url)


def load_minutes(
    market: str,
    symbols: list[str] | None,
    start: datetime,
    end: datetime,
    freq: str,
    db_url: str,
    buffer_bars: int = 60,
) -> pd.DataFrame:
    """拉 1m 线（带预热 buffer）并重采样到目标档位。返回升序多标的宽表。

    buffer_bars：目标档位根数换算的分钟数（保证 rolling 窗口首根可用）。
    """
    freq_key = freq if freq in FREQ_RESAMPLE_RULE else "15m"
    rule = FREQ_RESAMPLE_RULE[freq_key]
    minutes_per_bar = {"5m": 5, "15m": 15, "1h": 60}[freq_key]
    buf_start = start - timedelta(minutes=minutes_per_bar * buffer_bars)

    params: dict = {"market": market, "start": buf_start, "end": end}
    sym_clause = ""
    if symbols:
        sym_clause = "and symbol in :symbols"
        params["symbols"] = tuple(symbols)

    sql = text(
        f"""
        select symbol, bar_time, open, high, low, close, volume
        from v2_bar_minute
        where market = :market and bar_time >= :start and bar_time < :end {sym_clause}
        order by symbol, bar_time
        """
    ).bindparams(bindparam("symbols", expanding=True)) if symbols else text(
        """
        select symbol, bar_time, open, high, low, close, volume
        from v2_bar_minute
        where market = :market and bar_time >= :start and bar_time < :end
        order by symbol, bar_time
        """
    )
    with _connect(db_url).connect() as conn:
        raw = pd.read_sql(sql, conn, params=params, parse_dates={"bar_time": {"utc": True}})

    if raw.empty:
        return raw.rename(columns={"bar_time": "dt"})[
            ["dt", "open", "high", "low", "close", "volume", "symbol"]
        ]

    for col in ("open", "high", "low", "close", "volume"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw = raw.dropna(subset=["close"])

    # 逐标的重采样（向量化；组内 bar_time 单调升）
    parts: list[pd.DataFrame] = []
    for sym, grp in raw.groupby("symbol", sort=False):
        g = grp.set_index("bar_time").sort_index()
        agg = g.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        agg = agg.dropna(subset=["close"]).reset_index().rename(columns={"bar_time": "dt"})
        agg["symbol"] = sym
        parts.append(agg)
    out = pd.concat(parts, ignore_index=True) if parts else raw.iloc[0:0]
    return out


def load_daily(market: str, start: datetime, end: datetime, db_url: str) -> pd.DataFrame | None:
    """拉日线（regime 门 + 全池符号发现）。空则 None。"""
    sql = text(
        """
        select symbol, trade_date, open, high, low, close, pre_close, volume, turnover
        from v2_bar_daily
        where market = :market and trade_date >= :start and trade_date <= :end
        order by symbol, trade_date
        """
    )
    with _connect(db_url).connect() as conn:
        raw = pd.read_sql(sql, conn, params={"market": market,
                                             "start": start.date(), "end": end.date()})
    if raw.empty:
        return None
    for col in ("open", "high", "low", "close", "pre_close", "volume", "turnover"):
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
    return raw.dropna(subset=["close"])


def discover_symbols(market: str, db_url: str) -> list[str]:
    """全池符号发现：优先 v2_latest_snapshot（现役），回退 v2_bar_minute distinct。"""
    with _connect(db_url).connect() as conn:
        rows = conn.execute(
            text("select distinct symbol from v2_latest_snapshot where market = :m order by symbol"),
            {"m": market},
        ).fetchall()
        if not rows:
            rows = conn.execute(
                text("select distinct symbol from v2_bar_minute where market = :m order by symbol"),
                {"m": market},
            ).fetchall()
    return [r[0] for r in rows]


def load_bars(
    market: str,
    symbols: list[str] | None,
    start: datetime,
    end: datetime,
    freq: str,
    db_url: str,
    warmup_bars: int = 60,
    fallback: bool = True,
) -> BarData:
    """回测数据装配入口：档位探测 → 拉数 →（缺数据时）自动降档。

    降级全序「粗先细后」：请求档 → 1h → 5m。
    例：请求 15m → [15m, 1h, 5m]；请求 5m → [5m, 1h]（无更粗档）。
    注意：1h 档也不足时最终可能落 5m（报告 freq_fallback/fallback_reason 注明实际档位）。
    """
    requested = freq if freq in FREQ_ORDER else "15m"
    req_idx = FREQ_ORDER.index(requested)
    coarser = FREQ_ORDER[req_idx + 1:]           # 更粗（5m→15m→1h 方向的右侧）
    finer = list(reversed(FREQ_ORDER[:req_idx]))  # 更细（最后兜底）
    tried: list[str] = [requested]
    if fallback:
        tried += coarser + finer

    last_err = ""
    for i, f in enumerate(tried):
        try:
            df = load_minutes(market, symbols, start, end, f, db_url, buffer_bars=warmup_bars)
        except Exception as exc:  # 连接失败等 → 尝试下一档/最终抛出
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        if not df.empty and df.groupby("symbol").size().min() >= max(50, warmup_bars):
            daily = load_daily(market, start - timedelta(days=10), end, db_url)
            return BarData(
                market=market, freq=f, requested_freq=requested,
                freq_fallback=(f != requested),
                symbols=sorted(df["symbol"].unique().tolist()),
                bars=df, daily=daily,
                fallback_reason="" if f == requested else f"{requested}数据不足，自动降级{f}",
            )
        last_err = f"{f} 数据不足（{0 if df.empty else int(df.groupby('symbol').size().min())} 根/标的）"

    raise RuntimeError(f"无可用 bar 数据（tried={tried}, last={last_err}）")


# ── regime 门（zhice 只读复用） ────────────────────────────────────────────
def zhice_regime_fn() -> object:
    """直接 import 复用 zhice market_regime_permits_entry（硬规范：不许重写）。

    该函数内部自带进程级缓存 + as-of 切片（≤trade_date 无未来函数），
    DB 异常时自身降级为放行（返回 True），测试安全。
    """
    from zhice_os_v2.domain.strategy.strategies.chan.market_regime import (
        market_regime_permits_entry,
    )

    return market_regime_permits_entry


def make_regime_fn(daily: pd.DataFrame | None, market: str):
    """离线适配器：仅用于合成数据回测/单测（无 DB 依赖场景）。

    语义与 zhice market_regime_permits_entry 一致（等权指数周线 EMA 门，
    周 <30 根放行）；生产与真数据回测一律用 zhice_regime_fn() 直接复用。
    """
    if daily is None or daily.empty:
        return lambda d, m: True

    df = daily.copy()
    df["ret"] = df.sort_values(["symbol", "trade_date"]).groupby("symbol")["close"].pct_change()
    daily_eq = df.groupby("trade_date")["ret"].mean().dropna().reset_index()
    daily_eq["dt"] = pd.to_datetime(daily_eq["trade_date"], utc=True)
    daily_eq["idx"] = (1 + daily_eq["ret"]).cumprod()
    series = daily_eq.set_index("dt")["idx"]
    if len(series) < 30:
        return lambda d, m: True   # 周线历史不足 → 放行（zhice 同语义）

    def _permits(d, m):
        s = series[series.index <= pd.Timestamp(d, tz="UTC")]
        if s.empty:
            return True
        wk = s.resample("W-FRI").last().dropna()
        if len(wk) < 30:           # WEEKLY_MIN_BARS
            return True
        ema = wk.ewm(span=26, adjust=False).mean()   # MACD_SLOW
        below = wk.iloc[-1] < ema.iloc[-1]
        falling = ema.iloc[-1] < ema.iloc[-5]
        return not (below and falling)

    return _permits
