"""对照组策略 CTRL_MKT（V1 chan/control_basket.py 移植）。

大盘门 + 日期种子随机等权篮子：与 CHAN_V4/V6 同门同出场，唯一差异是选股
（缠论 vs 随机）——隔离"选股本身"的价值。种子=日期序数，可复现、无未来函数。
"""
from __future__ import annotations

import logging
import random
from datetime import date

logger = logging.getLogger(__name__)


def select_candidates(trade_date: date, max_candidates: int = 20,
                      market: str = "CN") -> list[dict]:
    from zhice_os_v2.domain.strategy.strategies.chan.market_regime import (
        market_regime_permits_entry,
    )
    from zhice_os_v2.domain.strategy.strategies.chan.strategy import _load_universe

    if not market_regime_permits_entry(trade_date, market):
        return []
    codes = _load_universe(trade_date, market)
    if not codes:
        return []
    rng = random.Random(trade_date.toordinal())
    picked = rng.sample(codes, min(max_candidates, len(codes)))
    return [{"security_id": "", "ts_code": c, "score": 0.0, "reason": "对照组:随机等权篮子"}
            for c in picked]
