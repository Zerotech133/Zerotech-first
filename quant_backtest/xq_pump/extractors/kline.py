# -*- coding: utf-8 -*-
"""K 线增量抽取（pysnowball.kline，非 HTTP）。

增量语义：按 (symbol, date) 覆盖写；每次拉最近 count 根，已存在的日期自动跳过
（INSERT OR REPLACE），新日期追加。无需游标。
依赖：pysnowball（用户侧已装；沙箱无网未验证）。
"""

from __future__ import annotations

from ..cursor import CursorStore
from ..store import Store
from ._parse import parse_kline


def collect_kline(
    session,
    store: Store,
    cursor: CursorStore,
    symbols: list,
    period: str = "day",
    count: int = 240,
) -> dict:
    total = 0
    for sym in symbols:
        payload = session.kline(sym, period=period, count=count)
        rows = parse_kline(sym, payload)
        if rows:
            store.upsert_kline(rows)
            total += len(rows)
    return {"new": total, "symbols": len(symbols)}
