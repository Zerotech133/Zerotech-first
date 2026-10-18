"""均值回归策略（MeanReversion v1.1）— M0 观察策略：只生成候选跟踪，不触发下单。

规则：
  卫生过滤同趋势策略（非ST、价格 3~300、20日均额 ≥ 3000万、样本 ≥ 26）
  R1  收盘价低于 MA20 至少 7%（超跌）
  R2  20日收益率 > -35%（排除基本面崩塌式下跌）
  R3  当日跌幅 > -3%（下跌动能衰减，不接飞刀）

  评分：偏离度越深分数越高
    score = |close/MA20 - 1| 的百分位
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.trend_momentum import (
    MIN_AVG_TURNOVER_20D,
    MIN_BARS,
    PRICE_MAX,
    PRICE_MIN,
    compute_features,
    is_st_like,
)

DEVIATION_MIN = -0.07     # 至少低于 MA20 7%
RET20_FLOOR = -0.35       # 20日跌幅下限
DAY_CHANGE_FLOOR = -3.0   # 当日跌幅下限（pct_change 为百分数）


def screen(features: pd.DataFrame, max_candidates: int = 20) -> list[dict]:
    """执行过滤与评分，返回 [{security_id, ts_code, score, reason}, ...]。"""
    if features.empty:
        return []
    f = features.copy()
    f["is_st"] = f["name"].map(is_st_like)
    f["deviation"] = f["close"] / f["ma20"] - 1

    mask = (
        (f["bars"] >= MIN_BARS)
        & (~f["is_st"])
        & f["close"].between(PRICE_MIN, PRICE_MAX)
        & (f["avg_turnover20"] >= MIN_AVG_TURNOVER_20D)
        & (f["deviation"] <= DEVIATION_MIN)
        & (f["ret20"] > RET20_FLOOR)
        & (f["pct_change"] > DAY_CHANGE_FLOOR)
    )
    sel = f[mask].copy()
    if sel.empty:
        return []

    sel["score"] = sel["deviation"].abs().rank(pct=True).round(4)
    top = sel.nlargest(max_candidates, "score")

    return [
        {
            "security_id": str(r.security_id),
            "ts_code": r.ts_code,
            "score": float(r.score),
            "reason": (
                f"均值回归: close={r.close:.2f} 低于MA20={r.ma20:.2f} "
                f"{abs(r.deviation):.1%}, 20日={r.ret20:.1%}, 当日={r.pct_change:.2f}%"
            ),
        }
        for r in top.itertuples()
    ]


def select_candidates(trade_date: date, max_candidates: int = 20, market: str = "CN") -> list[dict]:
    from zhice_os_v2.domain.strategy.data_loader import load_recent_bars

    bars = load_recent_bars(trade_date, market=market)
    features = compute_features(bars)
    return screen(features, max_candidates=max_candidates)
