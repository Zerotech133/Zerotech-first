"""日线历史回补：对全市场标的确保有 ≥days 天日线；已覆盖跳过；节流+退避+幂等，可续跑。"""
from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta

from zhice_os_v2.api.routers.app_state_store import _load_state, _save_state
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily
from zhice_os_v2.infrastructure.market_data.universe import daily_universe

logger = logging.getLogger(__name__)
_CURSOR_KEY = "backfill_daily_cursor"


def backfill_pre_close(market: str) -> int:
    from zhice_os_v2.scheduler_jobs import backfill_pre_close as _b
    return _b(market)


def _min_trade_date(market: str, symbol: str) -> date | None:
    from sqlalchemy import text
    s = get_session()
    try:
        return s.execute(
            text("SELECT min(trade_date) FROM v2_bar_daily WHERE market=:m AND symbol=:s"),
            {"m": market, "s": symbol},
        ).scalar()
    finally:
        s.close()


def backfill_daily_history(
    market: str, *, days: int, slice_size: int, batch_size: int, throttle_ms: int,
    max_retries: int, base_ms: int, max_symbols: int | None = None,
) -> dict:
    from zhice_os_v2.infrastructure.market_data.throttle import fetch_daily_chunk

    del batch_size  # 公共 API 形参：逐 symbol 取数暂未用到，保留供 CLI/sweep 对齐
    codes = daily_universe(market)
    if not codes:
        return {"processed": 0, "written": 0, "skipped": 0, "errors": 0, "cursor": 0}
    state = _load_state(_CURSOR_KEY, {})
    off = int(state.get(market, 0)) % len(codes)
    end_idx = min(len(codes), off + (max_symbols or slice_size))
    window = codes[off:end_idx]
    today = datetime.now(UTC).date()
    lo = today - timedelta(days=days)
    g = None  # 惰性创建：全跳过时绝不建网关
    processed = written = skipped = errors = 0
    try:
        for sym in window:
            mn = _min_trade_date(market, sym)
            if mn is not None and mn <= lo:
                skipped += 1
                continue
            if g is None:
                g = get_data_gateway(market)
            try:
                batch = fetch_daily_chunk(g, [sym], limit=days + 15, start=lo, end=today,
                                          max_retries=max_retries, base_ms=base_ms)
                rows = [b for bars in batch.values() for b in bars]
                written += upsert_daily(rows, market)
                processed += 1
            except Exception:
                errors += 1
                logger.exception("backfill 失败 %s:%s", market, sym)
            if throttle_ms > 0:
                time.sleep(throttle_ms / 1000)
    finally:
        if g is not None:
            g.close()
    if written:
        backfill_pre_close(market)
    cursor = end_idx % len(codes)
    _save_state(_CURSOR_KEY, {**state, market: cursor})
    return {"processed": processed, "written": written, "skipped": skipped,
            "errors": errors, "cursor": cursor}
