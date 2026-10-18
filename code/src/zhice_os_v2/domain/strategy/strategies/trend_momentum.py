"""趋势动量策略（TrendMomentum v1.1）— 生产主策略的真实选股逻辑。

规则（对文档11 策略框架的第一个落地实现）：

  卫生过滤：
    F1  非 ST / 非退市整理（名称含 ST/退/* 剔除）
    F2  收盘价 3 ~ 500 元（0-B:委托 CNProfile.candidate_price_band，上限 300→500）
    F3  20日日均成交额 ≥ 3000万（流动性门槛）
    F4  样本 ≥ 26 根K线（次新股剔除，保证指标可算）

  趋势确认：
    T1  收盘价 > MA20（价在趋势线上）
    T2  MA5 > MA10 > MA20（均线多头排列）
    T3  MA20 高于其 5 日前值（趋势线向上）

  动量约束：
    M1  20日收益率 ∈ [5%, 50%]（有动量但未过热）

  评分（越大越优）：
    score = 0.7 × 20日动量百分位 + 0.3 × 流动性百分位
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from zhice_os_v2.domain.market.registry import get_profile

_CN = get_profile("CN")
MIN_BARS = 26
_pmin, _pmax = _CN.candidate_price_band()
PRICE_MIN, PRICE_MAX = float(_pmin), float(_pmax)   # 0-B:委托CNProfile(3~500)
MIN_AVG_TURNOVER_20D = 30_000_000
MOMENTUM_MIN, MOMENTUM_MAX = 0.05, 0.50

is_st_like = _CN.is_st_like   # 0-B:ST判定委托CNProfile(消除重复,含空名fail-safe)


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """逐票计算指标，返回每票最后一个交易日的特征行。

    输入列: security_id, ts_code, name, trade_date, close, turnover_cny
    输出列: 输入列 + bars, ma5, ma10, ma20, ma20_prev5, ret20, avg_turnover20
    """
    if df.empty:
        return df.assign(bars=0, ma5=None, ma10=None, ma20=None,
                         ma20_prev5=None, ret20=None, avg_turnover20=None)
    df = df.sort_values(["security_id", "trade_date"]).copy()
    g_close = df.groupby("security_id")["close"]
    df["ma5"] = g_close.transform(lambda s: s.rolling(5).mean())
    df["ma10"] = g_close.transform(lambda s: s.rolling(10).mean())
    df["ma20"] = g_close.transform(lambda s: s.rolling(20).mean())
    df["ma20_prev5"] = df.groupby("security_id")["ma20"].shift(5)
    df["ret20"] = g_close.transform(lambda s: s / s.shift(20) - 1)
    df["avg_turnover20"] = df.groupby("security_id")["turnover_cny"].transform(
        lambda s: s.rolling(20).mean()
    )
    df["bars"] = df.groupby("security_id")["close"].transform("count")
    return df.groupby("security_id").tail(1)


def screen(
    features: pd.DataFrame,
    max_candidates: int = 20,
    price_min: float = PRICE_MIN,
    price_max: float = PRICE_MAX,
    min_avg_turnover: float = MIN_AVG_TURNOVER_20D,
    is_st_fn=is_st_like,
    min_bars: int = MIN_BARS,
    momentum_min: float = MOMENTUM_MIN,
    momentum_max: float = MOMENTUM_MAX,
) -> list[dict]:
    """执行过滤与评分，返回 [{security_id, ts_code, score, reason}, ...]（按分数降序）。

    阈值/ST 判定通过参数注入（默认取模块级 CN 常量 → 既有调用逐字节）。
    min_bars/动量带同样可注入，供 crypto 等市场按 profile 放宽。
    """
    if features.empty:
        return []
    f = features.copy()
    f["is_st"] = f["name"].map(is_st_fn)

    mask = (
        (f["bars"] >= min_bars)
        & (~f["is_st"])
        & f["close"].between(price_min, price_max)
        & (f["avg_turnover20"] >= min_avg_turnover)
        & (f["close"] > f["ma20"])
        & (f["ma5"] > f["ma10"])
        & (f["ma10"] > f["ma20"])
        & (f["ma20"] > f["ma20_prev5"])
        & f["ret20"].between(momentum_min, momentum_max)
    )
    sel = f[mask].copy()
    if sel.empty:
        return []

    sel["mom_pct"] = sel["ret20"].rank(pct=True)
    sel["liq_pct"] = sel["avg_turnover20"].rank(pct=True)
    sel["score"] = (0.7 * sel["mom_pct"] + 0.3 * sel["liq_pct"]).round(4)
    top = sel.nlargest(max_candidates, "score")

    return [
        {
            "security_id": str(r.security_id),
            "ts_code": r.ts_code,
            "score": float(r.score),
            "reason": (
                f"趋势动量: close={r.close:.2f}>MA20={r.ma20:.2f}, "
                f"MA5>MA10>MA20多头排列, MA20向上, "
                f"20日动量={r.ret20:.1%}, 20日均额={r.avg_turnover20 / 1e8:.2f}亿"
            ),
        }
        for r in top.itertuples()
    ]


def select_candidates(
    trade_date: date, max_candidates: int = 20, market: str = "CN"
) -> list[dict]:
    """入口：取数 → 指标 → 筛选评分。

    market-aware：阈值/ST/价格带按 get_profile(market)；CN 时 profile==_CN → 逐字节。
    """
    from zhice_os_v2.domain.strategy.data_loader import load_recent_bars

    prof = get_profile(market)
    _lo, _hi = prof.candidate_price_band()   # 价格带按 profile；ST 判定按 profile

    bars = load_recent_bars(trade_date, market=market)
    features = compute_features(bars)
    # 选股阈值按 profile 注入（未 override 的 profile 用模块级 CN 常量默认 → CN/US/HK 逐字节）：
    #   20日均额门槛 / 动量带 / 最小 bars。crypto profile 已按 USDT 口径放宽（见 CryptoProfile）。
    _mo = getattr(prof, "trend_momentum_band", lambda: (MOMENTUM_MIN, MOMENTUM_MAX))()
    return screen(
        features,
        max_candidates=max_candidates,
        price_min=float(_lo),
        price_max=float(_hi),
        is_st_fn=prof.is_st_like,
        min_avg_turnover=float(
            getattr(prof, "trend_min_avg_turnover_20d", lambda: MIN_AVG_TURNOVER_20D)()
        ),
        min_bars=int(getattr(prof, "trend_min_bars", lambda: MIN_BARS)()),
        momentum_min=float(_mo[0]),
        momentum_max=float(_mo[1]),
    )
