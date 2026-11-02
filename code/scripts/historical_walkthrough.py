# -*- coding: utf-8 -*-
"""历史三天走通检查：同一日期内的日报→池→ACAD1→卡→反馈/督导是否齐全。

只读；不写 DB。
用法：python scripts/historical_walkthrough.py [YYYY-MM-DD ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

OUT = Path(r"C:\Temp\historical_walkthrough.json")
DATES = ["2026-10-29", "2026-10-30", "2026-10-31"]


def main() -> int:
    dates = sys.argv[1:] or DATES
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    rows = []
    with e.connect() as c:
        for d in dates:
            def q(sql: str):
                return c.execute(sa.text(sql), {"d": d}).all()
            reports = {str(m): int(n) for m, n in q(
                "SELECT market,count(*) FROM v2_selection_report WHERE report_date=:d GROUP BY market")}
            pool = {str(m): int(n) for m, n in q(
                "SELECT market,count(*) FROM v2_selection_pool WHERE pool_date=:d GROUP BY market")}
            acad1 = {str(m): int(n) for m, n in q(
                "SELECT market,count(*) FROM v2_acad1_pool WHERE pool_date=:d GROUP BY market")}
            cards = {str(s): int(n) for s, n in q(
                "SELECT status,count(*) FROM v2_stock_research WHERE pool_date=:d GROUP BY status")}
            feedback = c.execute(sa.text(
                "SELECT count(*) FROM v2_selection_feedback WHERE pool_date=:d"), {"d": d}).scalar()
            supervision = c.execute(sa.text(
                "SELECT count(*) FROM v2_strategy_supervision WHERE supervision_date=:d"), {"d": d}).scalar()
            checks = {
                "reports_ge_10": sum(reports.values()) >= 10,
                "four_markets": len(reports) == 4,
                "pool_four_markets": len(pool) == 4,
                "acad1_four_markets": len(acad1) == 4,
                "acad1_le_30": sum(acad1.values()) <= 30,
                "acad1_cn_hk": acad1.get("CN", 0) >= 10 and acad1.get("HK", 0) >= 8,
                "cards_present": sum(cards.values()) > 0,
                "feedback_ge_1": int(feedback or 0) >= 1,
                "supervision_ge_1": int(supervision or 0) >= 1,
            }
            gaps = [k for k, v in checks.items() if not v]
            rows.append({
                "date": d,
                "reports": reports,
                "selection_pool": pool,
                "acad1": acad1,
                "cards": cards,
                "feedback": int(feedback or 0),
                "supervision": int(supervision or 0),
                "checks": checks,
                "gaps": gaps,
                "same_day_complete": not gaps,
            })
    out = {
        "schema": "zhice.historical_walkthrough/v1",
        "read_only": True,
        "dates": dates,
        "rows": rows,
        "conclusion": {
            "complete_dates": [r["date"] for r in rows if r["same_day_complete"]],
            "none_complete": all(not r["same_day_complete"] for r in rows),
            "note": "历史日期不是同一天完整链路；试运行需要 11-02 起重新产出一整天的完整链路。",
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["conclusion"], ensure_ascii=False))
    for r in rows:
        print(r["date"], "gaps=", r["gaps"], "reports=", r["reports"], "acad1=", r["acad1"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
