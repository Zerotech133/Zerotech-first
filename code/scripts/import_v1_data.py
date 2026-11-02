"""从原版系统复制证券与日K数据到 V2。

用法：
    V1_DB_URL=postgresql://... V2_DB_URL=postgresql://... python scripts/import_v1_data.py
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

V1_URL = os.getenv("V1_DB_URL")
V2_URL = os.getenv("V2_DB_URL") or __import__("zhice_os_v2.config.settings", fromlist=["get_settings"]).get_settings().db_main_url


def main() -> None:
    v1 = create_engine(V1_URL)
    v2 = create_engine(V2_URL)
    now = datetime.now(timezone.utc)

    # 复制证券主数据
    with v1.connect() as conn:
        securities = conn.execute(
            text("SELECT ts_code, symbol, exchange, market, name, list_date FROM security WHERE status IN ('L','1')")
        ).mappings().all()

    inserted = 0
    with v2.begin() as conn:
        for row in securities:
            instrument_id = f"{row['market']}:{row['ts_code']}"
            res = conn.execute(
                text("""
                    INSERT INTO v2_instrument
                        (instrument_id, market, symbol, exchange, currency, name, lot_size, status, created_at)
                    VALUES (:instrument_id, :market, :symbol, :exchange, :currency, :name, :lot_size, 'ACTIVE', :now)
                    ON CONFLICT (instrument_id) DO NOTHING
                """),
                {
                    "instrument_id": instrument_id,
                    "market": row["market"] or "CN",
                    "symbol": row["ts_code"],
                    "exchange": row["exchange"],
                    "currency": "CNY" if (row["market"] or "CN") == "CN" else "USD",
                    "name": row["name"] or "",
                    "lot_size": 100 if (row["market"] or "CN") == "CN" else 1,
                    "now": now,
                },
            )
            inserted += res.rowcount or 0

    # 复制日K（限制最近 1000 根，可按需调整）
    bars_inserted = 0
    with v1.connect() as conn:
        bars = conn.execute(
            text("""
                SELECT b.ts_code, s.market, b.trade_date, b.open, b.high, b.low, b.close,
                       b.volume_shares, b.turnover_cny
                FROM fact_bar_daily b
                JOIN security s ON s.id = b.security_id
                WHERE b.adj_type = 'none'
                ORDER BY b.trade_date DESC
                LIMIT 2000000
            """)
        ).mappings().all()

    with v2.begin() as conn:
        for row in bars:
            market = row["market"] or "CN"
            instrument_id = f"{market}:{row['ts_code']}"
            res = conn.execute(
                text("""
                    INSERT INTO v2_bar_daily
                        (instrument_id, market, symbol, trade_date, open, high, low, close, volume, turnover, source, created_at)
                    VALUES (:instrument_id, :market, :symbol, :trade_date, :open, :high, :low, :close, :volume, :turnover, 'v1_import', :now)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "instrument_id": instrument_id,
                    "market": market,
                    "symbol": row["ts_code"],
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
            bars_inserted += res.rowcount or 0

    # 复制交易日历
    calendar_inserted = 0
    with v1.connect() as conn:
        calendar = conn.execute(
            text("SELECT trade_date, is_open FROM market_day")
        ).mappings().all()

    with v2.begin() as conn:
        for row in calendar:
            res = conn.execute(
                text("""
                    INSERT INTO v2_market_calendar (market, trade_date, is_open, created_at)
                    VALUES ('CN', :trade_date, :is_open, :now)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "trade_date": row["trade_date"],
                    "is_open": bool(row["is_open"]),
                    "now": now,
                },
            )
            calendar_inserted += res.rowcount or 0

    print(
        f"securities inserted={inserted}, bars inserted={bars_inserted}, calendar inserted={calendar_inserted}"
    )


if __name__ == "__main__":
    main()
