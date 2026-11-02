"""从 V1 导入基本面指标数据。"""
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

    with v1.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ts_code, market, end_date, ann_date, roe, roe_dt,
                       debt_to_assets, netprofit_yoy, profit_dedt_cny
                FROM fact_financial_indicator
                WHERE ann_date IS NOT NULL
                ORDER BY end_date DESC
                LIMIT 500000
            """)
        ).mappings().all()

    inserted = 0
    with v2.begin() as conn:
        for row in rows:
            def _safe(v, limit=100000):
                if v is None:
                    return None
                fv = float(v)
                return v if abs(fv) <= limit else None
            row = dict(row)
            row["roe"] = _safe(row.get("roe"))
            row["roe_dt"] = _safe(row.get("roe_dt"))
            row["debt_to_assets"] = _safe(row.get("debt_to_assets"))
            row["netprofit_yoy"] = _safe(row.get("netprofit_yoy"))
            row["profit_dedt_cny"] = _safe(row.get("profit_dedt_cny"), limit=10**12)
            res = conn.execute(
                text("""
                    INSERT INTO v2_fundamental
                        (symbol, market, end_date, ann_date, roe, roe_dt,
                         debt_to_assets, netprofit_yoy, profit_dedt_cny, created_at)
                    VALUES (:symbol, :market, :end_date, :ann_date, :roe, :roe_dt,
                            :debt_to_assets, :netprofit_yoy, :profit_dedt_cny, :now)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "symbol": row["ts_code"],
                    "market": row["market"] or "CN",
                    "end_date": row["end_date"],
                    "ann_date": row["ann_date"],
                    "roe": row["roe"],
                    "roe_dt": row["roe_dt"],
                    "debt_to_assets": row["debt_to_assets"],
                    "netprofit_yoy": row["netprofit_yoy"],
                    "profit_dedt_cny": row["profit_dedt_cny"],
                    "now": now,
                },
            )
            inserted += res.rowcount or 0

    print(f"fundamentals inserted={inserted}")


if __name__ == "__main__":
    main()
