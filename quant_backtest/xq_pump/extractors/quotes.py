# -*- coding: utf-8 -*-
"""行情快照增量抽取（pysnowball.quotec，非 HTTP）。

增量语义：快照按 (symbol, ts) 覆盖写；每次运行追加一条「当前时点」快照，
历史时点保留，可回溯任意日的行情截面。无需游标。
依赖：pysnowball（用户侧已装；沙箱无网未验证）。
"""

from __future__ import annotations

from ..cursor import CursorStore
from ..store import Store
from ._parse import parse_quote


def collect_quotes(session, store: Store, cursor: CursorStore, symbols: list) -> dict:
    new = 0
    for sym in symbols:
        payload = session.quotec(sym)
        row = parse_quote(sym, payload)
        if row:
            store.upsert_quotes([row])
            new += 1
    return {"new": new, "symbols": len(symbols)}
