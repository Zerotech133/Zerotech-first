"""从 V1 导入最近 N 天分钟K（默认 top 20 只活跃标的）。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

V1_URL = os.getenv("V1_DB_URL")
V2_URL = os.getenv("V2_DB_URL") or __import__("zhice_os_v2.config.settings", fromlist=["get_settings"]).get_settings().db_main_url
DAYS = int(os.getenv("MINUTE_DAYS", "7"))
TOP_N = int(os.getenv("MINUTE_TOP_N", "20"))


def main() -> None:
    v1 = create_engine(V1_URL)
    v2 = create_engine(V2_URL)
    now = datetime.now(timezone.utc)
    start = (datetime.now(timezone.utc) - timedelta(days=DAYS)).date()

    # 选最近日K成交额靠前的标的
    with v2.connect() as conn:
        symbols = [r[0] for r in conn.execute(
            text("""
                SELECT symbol FROM v2_bar_daily
                WHERE trade_date >= :start
                GROUP BY symbol
                ORDER BY MAX(turnover) DESC
                LIMIT :top_n
            """),
            {"start": start, "top_n": TOP_N},
        ).fetchall()]

    inserted = 0
    with v1.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT b.ts_code, s.market, b.bar_time, b.trade_date,
                       b.open, b.high, b.low, b.close, b.volume_shares, b.turnover_cny
                FROM fact_bar_minute b
                JOIN security s ON s.id = b.security_id
                WHERE b.ts_code = ANY(:codes) AND b.trade_date >= :start
                ORDER BY b.bar_time
            """),
            {"codes": symbols, "start": start},
        ).mappings().all()

    with v2.begin() as conn:
        for row in rows:
            market = row["market"] or "CN"
            instrument_id = f"{market}:{row['ts_code']}"
            res = conn.execute(
                text("""
                    INSERT INTO v2_bar_minute
                        (instrument_id, market, symbol, bar_time, trade_date,
                         open, high, low, close, volume, turnover, source, created_at)
                    VALUES (:instrument_id, :market, :symbol, :bar_time, :trade_date,
                            :open, :high, :low, :close, :volume, :turnover, 'v1_import', :now)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "instrument_id": instrument_id,
                    "market": market,
                    "symbol": row["ts_code"],
                    "bar_time": row["bar_time"],
                    "trade_date": row["trade_date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume_shares"],
                    "turnover": row["turnover_cny"],
                    "now": now,
                },
            )
            inserted += res.rowcount or 0

    print(f"minute inserted={inserted}, symbols={len(symbols)}")


if __name__ == "__main__":
    main()
