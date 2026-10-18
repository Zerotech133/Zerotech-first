"""大盘级别方向门（市场 regime gate）— 缠论"大级别定方向"的正确层级。

自 V1 modules.candidate_engine.strategies.chan.market_regime 移植（语义原样），
V1 SQL（fact_bar_daily）改为 V2 ORM（v2_bar_daily，市场内等权）。

指数构造：v2_bar_daily 单市场全标的等权日收益的累计乘积。
判据同 engine.weekly_trend_permits_long：收盘 < 下行的周线慢EMA(span26,近5周走低) → risk-off。
进程内缓存整条日频序列；as-of 切片(≤trade_date)保证无未来函数。生产每日 reset_cache() 刷新。
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan.engine import MACD_SLOW, WEEKLY_MIN_BARS
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

# 按 market 隔离缓存，避免 CN/CRYPTO 首次加载结果互相污染。
_MKT_CACHE: dict[str, pd.DataFrame | None] = {}


def _load_market_index(market: str = "CN") -> pd.DataFrame:
    """单市场等权累计指数(日频)。进程内缓存一次；返回列 dt / idx。"""
    market_key = str(market or "CN").upper()
    if _MKT_CACHE.get(market_key) is not None:
        return _MKT_CACHE[market_key]
    try:
        session = get_session()
        try:
            rows = session.query(
                orm.BarDailyModel.trade_date,
                orm.BarDailyModel.close,
                orm.BarDailyModel.instrument_id,
            ).filter(
                orm.BarDailyModel.market == market,
            ).order_by(orm.BarDailyModel.trade_date).all()
        finally:
            session.close()
    except Exception as e:
        logger.warning("market_index_load_failed: %s", e)
        _MKT_CACHE[market_key] = pd.DataFrame(columns=["dt", "idx"])
        return _MKT_CACHE[market_key]

    df = pd.DataFrame(
        [(r[0], r[1], r[2]) for r in rows],
        columns=["trade_date", "close", "security_id"],
    )
    if df.empty:
        _MKT_CACHE[market_key] = pd.DataFrame(columns=["dt", "idx"])
        return _MKT_CACHE[market_key]
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    # 等权日收益：逐股 pct_change 再横截面等权（首日/新股无前收 → NaN，mean 自动剔除）。
    # 按个股分组的 pct_change 天然只配对同股相邻交易日 → 免疫成分/幸存者变化，
    # 且不再被高价股主导（原 ratio-of-means 会翻符号，托盘时误判 risk-on）。
    df = df.sort_values(["security_id", "trade_date"])
    df["ret"] = df.groupby("security_id")["close"].pct_change()
    daily = df.groupby("trade_date")["ret"].mean().dropna().reset_index()
    daily["dt"] = pd.to_datetime(daily["trade_date"])
    daily["idx"] = (1 + daily["ret"]).cumprod()
    _MKT_CACHE[market_key] = daily[["dt", "idx"]].copy()
    logger.info("market_index_loaded market=%s days=%s span=%s~%s",
                market, len(daily),
                daily["dt"].min().date() if len(daily) else None,
                daily["dt"].max().date() if len(daily) else None)
    return _MKT_CACHE[market_key]


def market_regime_permits_entry(trade_date: date, market: str = "CN") -> bool:
    """大盘级别方向门：市场等权指数周线下跌趋势时禁止新建仓，其余放行。

    risk-off ⇔ 指数收盘 < 周线EMA(慢) 且 该EMA本身在下行（近5周走低）。
    周线历史 < WEEKLY_MIN_BARS → 放行（不因缺数据误拦）。
    """
    df = _load_market_index(market)
    if df.empty:
        return True
    s = df[df["dt"] <= pd.Timestamp(trade_date)].set_index("dt")["idx"]
    wk = s.resample("W-FRI").last().dropna()
    if len(wk) < WEEKLY_MIN_BARS:
        return True
    ema = wk.ewm(span=MACD_SLOW, adjust=False).mean()
    below_ema = wk.iloc[-1] < ema.iloc[-1]
    ema_falling = ema.iloc[-1] < ema.iloc[-5]
    return not (below_ema and ema_falling)


def reset_cache() -> None:
    """清缓存（生产每日增量后调用，让指数纳入最新交易日）。"""
    _MKT_CACHE.clear()
