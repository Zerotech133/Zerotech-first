# -*- coding: utf-8 -*-
"""三天试运行每日检查：输出 5 项证据与缺口。"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings


def main() -> int:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        if day is None:
            day = c.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
        report_days = c.execute(sa.text(
            "SELECT report_date, market, count(*) FROM v2_selection_report "
            "GROUP BY report_date, market ORDER BY report_date DESC, market")).all()
        report_anchor = c.execute(sa.text(
            "SELECT max(report_date) FROM v2_selection_report")).scalar()
        report_strategies = c.execute(sa.text(
            "SELECT count(DISTINCT strategy_id) FROM v2_selection_report "
            "WHERE report_date=:d"), {"d": report_anchor}).scalar()
        fresh_from = day - timedelta(days=3) if isinstance(day, date) else day
        report_fresh_strategies = c.execute(sa.text(
            "SELECT count(DISTINCT strategy_id) FROM v2_selection_report "
            "WHERE report_date >= :d"), {"d": fresh_from}).scalar()
        pool = c.execute(sa.text(
            "SELECT market, count(*) FROM v2_selection_pool WHERE pool_date=:d GROUP BY market ORDER BY market"),
            {"d": day}).all()
        acad1 = c.execute(sa.text(
            "SELECT market, count(*) FROM v2_acad1_pool WHERE pool_date=:d GROUP BY market ORDER BY market"),
            {"d": day}).all()
        cards = c.execute(sa.text(
            "SELECT status, count(*) FROM v2_stock_research WHERE pool_date=:d GROUP BY status"), {"d": day}).all()
        feedback = c.execute(sa.text(
            "SELECT count(*) FROM v2_selection_feedback WHERE pool_date=:d"), {"d": day}).scalar()
        supervision = c.execute(sa.text(
            "SELECT count(*) FROM v2_strategy_supervision WHERE supervision_date=:d"), {"d": day}).scalar()
        obs = c.execute(sa.text(
            "SELECT status, count(*) FROM v2_strategy_observation WHERE signal_date=:d GROUP BY status"),
            {"d": day}).all()

    report_total = sum(n for d, m, n in report_days if d == day)
    report_total_anchor = sum(n for d, m, n in report_days if d == report_anchor)
    market_counts = {m: n for m, n in pool}
    acad_counts = {m: n for m, n in acad1}
    checks = {
        "reports_ge_10": report_fresh_strategies >= 10,
        "four_markets": set(market_counts) == {"CN", "HK", "US", "CRYPTO"},
        "acad1_total_le_30": sum(acad_counts.values()) <= 30,
        "acad1_cn_hk_priority": acad_counts.get("CN", 0) >= 10 and acad_counts.get("HK", 0) >= 8,
        "acad1_all_markets": set(acad_counts) == {"CN", "HK", "US", "CRYPTO"},
        "feedback_ge_1": feedback >= 1,
        "supervision_ge_1": supervision >= 1,
    }
    result = {
        "day": str(day),
        "report_anchor": str(report_anchor),
        "report_total_today": report_total,
        "report_total_anchor": report_total_anchor,
        "report_strategies_anchor": report_strategies,
        "report_fresh_strategies": report_fresh_strategies,
        "report_days": [{"date": str(d), "market": m, "n": n} for d, m, n in report_days],
        "pool": market_counts,
        "acad1": acad_counts,
        "cards": dict(cards),
        "feedback": feedback,
        "supervision": supervision,
        "observations": dict(obs),
        "checks": checks,
        "pass": all(checks.values()),
    }
    out = Path(r"C:\Temp") / f"trial_check_{day}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("saved", out)
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
