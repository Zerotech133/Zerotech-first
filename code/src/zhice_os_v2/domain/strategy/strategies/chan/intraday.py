"""缠论 30 分钟小级别确认（V1 chan/intraday.py 移植）。

合成：5min bar 每交易日内按顺序每 6 根连续聚合为 30min（不用时钟 resample，
防午休错位）；as-of 只取 ≤ 信号日收盘。

确认算法：30min bar < MIN_BARS(约8交易日) → 放行（缺数据不误拦）；
否则最近一笔方向向上（拐头）或 MACD 底背驰 → 确认。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_MIN_30MIN_BARS = 60      # 约 8 交易日（A 股每日 4 根 30min）
_LOOKBACK_DAYS = 45       # 自然日窗口


def load_30min_bars(ts_code: str, as_of: date, market: str = "CN") -> pd.DataFrame:
    """≤as_of 的 30min OHLCV（每交易日内每 6 根 5min 连续聚合）。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    start = as_of - timedelta(days=_LOOKBACK_DAYS)
    session = get_session()
    try:
        rows = (
            session.query(
                orm.BarMinuteModel.trade_date,
                orm.BarMinuteModel.open,
                orm.BarMinuteModel.high,
                orm.BarMinuteModel.low,
                orm.BarMinuteModel.close,
                orm.BarMinuteModel.volume,
            )
            .filter(orm.BarMinuteModel.symbol == ts_code,
                    orm.BarMinuteModel.market == market,
                    orm.BarMinuteModel.trade_date <= as_of,
                    orm.BarMinuteModel.trade_date >= start)
            .order_by(orm.BarMinuteModel.trade_date, orm.BarMinuteModel.bar_time)
            .all()
        )
    finally:
        session.close()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
    # 每交易日内每 6 根连续聚合（V1 cumcount//6 语义，防午休时钟错位）
    df = df.sort_values(["trade_date"])
    df["seq"] = df.groupby("trade_date").cumcount() // 6
    agg = (df.groupby(["trade_date", "seq"])
           .agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"))
           .reset_index().sort_values(["trade_date", "seq"])
           .reset_index(drop=True))
    return agg[["open", "high", "low", "close", "volume"]].astype(float)


def intraday_confirms_buy(ts_code: str, as_of: date, market: str = "CN") -> bool:
    """30min 级别确认买点：最近笔向上 或 MACD 底背驰。缺数据放行（不误拦）。"""
    df = load_30min_bars(ts_code, as_of, market)
    if df.empty or len(df) < _MIN_30MIN_BARS:
        return True  # V1 语义：数据不足不拦

    closes = df["close"].tolist()
    # 简笔方向：最近 3 根收盘抬升 → 拐头向上
    if len(closes) >= 3 and closes[-1] > closes[-2] >= closes[-3] * 0.999:
        return True
    # MACD 底背驰（简化 V1）：DIF 由负转零轴上穿 + 新低抬升
    try:
        import pandas as _pd
        s = _pd.Series(closes)
        ema12 = s.ewm(span=12, adjust=False).mean()
        ema26 = s.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        if len(dif) >= 2 and dif.iloc[-1] > 0 >= dif.iloc[-2]:
            return True  # DIF 零轴上穿
        # 创新低但 DIF 未创新低 → 底背驰
        n = min(len(s), 20)
        if s.iloc[-1] <= s.iloc[-n:].min() and dif.iloc[-1] > dif.iloc[-n:].min():
            return True
    except Exception:
        return True
    return False
