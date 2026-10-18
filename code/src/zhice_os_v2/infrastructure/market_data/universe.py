"""采集宇宙选取（从 V2 库算，复刻 V1 口径）。

- daily：v2_instrument 全部 ACTIVE 标的（该市场）。
- minute：近35日历日 avg(turnover) Top-N ∪ 在仓 ∪ 当日候选（V1 minute_capture 口径）。
- snapshot：Top-N by turnover ∪ 在仓 ∪ 当日候选（保证持仓/候选必有新鲜快照）。
"""
from __future__ import annotations

import psycopg2

from zhice_os_v2.config.settings import get_settings


def _conn():
    return psycopg2.connect(get_settings().db_main_url)


def _held_and_candidates(cur, market: str) -> set[str]:
    cur.execute(
        "SELECT DISTINCT ts_code FROM v2_position "
        "WHERE status='OPEN' AND market=%s AND ts_code IS NOT NULL",
        (market,),
    )
    codes = {r[0] for r in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT ts_code FROM v2_candidate_asset "
        "WHERE status='ACTIVE' AND market=%s AND ts_code IS NOT NULL "
        "AND created_at::date >= CURRENT_DATE",
        (market,),
    )
    codes |= {r[0] for r in cur.fetchall()}
    return codes


def _top_by_turnover(cur, market: str, top_n: int) -> list[str]:
    cur.execute(
        """
        WITH recent AS (
            SELECT symbol, avg(turnover) AS avg_t
            FROM v2_bar_daily
            WHERE trade_date > CURRENT_DATE - 35 AND market=%s
            GROUP BY symbol
        )
        SELECT symbol FROM recent ORDER BY avg_t DESC NULLS LAST LIMIT %s
        """,
        (market, top_n),
    )
    return [r[0] for r in cur.fetchall()]


def daily_universe(market: str = "CN") -> list[str]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol FROM v2_instrument WHERE market=%s AND status='ACTIVE' "
                "ORDER BY symbol",
                (market,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def minute_universe(market: str = "CN", top_n: int = 1200) -> list[str]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            codes = set(_top_by_turnover(cur, market, top_n))
            codes |= _held_and_candidates(cur, market)
            return sorted(codes)
    finally:
        conn.close()


def snapshot_universe(market: str = "CN", top_n: int = 800) -> list[str]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            codes = set(_top_by_turnover(cur, market, top_n))
            codes |= _held_and_candidates(cur, market)
            return sorted(codes)
    finally:
        conn.close()
