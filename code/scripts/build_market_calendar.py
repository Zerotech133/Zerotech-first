"""补建/校正交易日历 v2_market_calendar（pandas_market_calendars）。

用法：
    python scripts/build_market_calendar.py                 # 全市场 CN/US/HK/CRYPTO
    python scripts/build_market_calendar.py --market HK
    python scripts/build_market_calendar.py --start 2015-01-01 --end 2027-12-31

- 日历名取 get_profile(market).calendar_exchange()（SSE/NYSE/HKEX，均为 mcal 合法名）。
- CRYPTO 无交易所日历 → 全日历日 is_open=True。
- 写全日期范围（含非交易日 is_open=False），与 CN 现状一致；幂等 upsert on (market, trade_date)。
- 先建唯一索引 uq_v2_market_calendar_mt（原表无约束，import_v1_data 的 ON CONFLICT 曾失效）。
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

import pandas_market_calendars as mcal
import psycopg2
from psycopg2.extras import execute_values

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.market.registry import get_profile

MARKETS = ("CN", "US", "HK", "CRYPTO")
DEFAULT_START = "2015-01-01"


def _default_end() -> str:
    return f"{datetime.now().year + 1}-12-31"


def _trading_days(market: str, start: str, end: str) -> set[date] | None:
    """交易日集合；CRYPTO 返回 None 表示全开。"""
    if market == "CRYPTO":
        return None
    name = get_profile(market).calendar_exchange()
    sched = mcal.get_calendar(name).schedule(start_date=start, end_date=end)
    return {d.date() for d in sched.index}


def build(market: str, start: str, end: str) -> int:
    days = _trading_days(market, start, end)
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    now = datetime.now(UTC)
    values = []
    cur = d0
    while cur <= d1:
        is_open = True if days is None else (cur in days)
        values.append((market, cur, is_open, now))
        cur += timedelta(days=1)

    conn = psycopg2.connect(get_settings().db_main_url)
    try:
        with conn, conn.cursor() as c:
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_market_calendar_mt "
                "ON v2_market_calendar (market, trade_date)"
            )
        with conn, conn.cursor() as c:
            execute_values(c, """
                INSERT INTO v2_market_calendar (market, trade_date, is_open, created_at)
                VALUES %s
                ON CONFLICT (market, trade_date) DO UPDATE SET is_open = EXCLUDED.is_open
            """, values, page_size=1000)
        open_days = sum(1 for v in values if v[2])
        print(f"  {market}: 写入 {len(values)} 行（开市 {open_days}）{start}~{end}")
        return len(values)
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="all", choices=("all", *MARKETS))
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=_default_end())
    args = ap.parse_args()
    markets = MARKETS if args.market == "all" else (args.market,)
    print(f"构建交易日历 {args.start}~{args.end}")
    for m in markets:
        build(m, args.start, args.end)
    print("完成。")


if __name__ == "__main__":
    main()
