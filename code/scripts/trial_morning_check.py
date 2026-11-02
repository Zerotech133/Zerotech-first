# -*- coding: utf-8 -*-
"""试运行 09:30 开跑前只读检查（不写 DB、不触发任务）。"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

OUT = Path(r"C:\Temp\trial_morning_check.json")
MD = Path(r"C:\Temp\trial_morning_check.md")
SERVICES = {
    "frontend": "http://127.0.0.1:5175/",
    "api": "http://127.0.0.1:8105/api/v1/health",
    "hermes": "http://127.0.0.1:8787/health",
    "portal": "http://127.0.0.1:8734/hermes/",
}


def _http(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return int(r.status)
    except Exception:
        return 0


def main() -> int:
    checks: dict[str, bool] = {}
    detail: dict[str, object] = {}
    for name, url in SERVICES.items():
        code = _http(url)
        checks[f"service_{name}"] = code == 200
        detail[f"service_{name}"] = code

    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        pool_date = c.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
        acad_date = c.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
        acad = c.execute(sa.text("""
            SELECT p.market, count(*) AS n,
                   count(*) FILTER (WHERE b.base_state = 'BUY') AS buy
            FROM v2_acad1_pool p
            LEFT JOIN LATERAL (
              SELECT base_state FROM v2_base_signal b
              WHERE b.market=p.market AND b.code=p.code
              ORDER BY b.trade_date DESC LIMIT 1
            ) b ON true
            WHERE p.pool_date=(SELECT max(pool_date) FROM v2_acad1_pool)
            GROUP BY p.market ORDER BY p.market
        """)).mappings().all()
        names_bad = c.execute(sa.text("""
            SELECT count(*) FROM v2_base_signal
            WHERE base_state IN ('BUY','SELL','AVOID','NEUTRAL')
              AND (name IS NULL OR name='' OR name=code)
        """)).scalar()
        reports = c.execute(sa.text("""
            SELECT market, max(report_date) FROM v2_selection_report
            GROUP BY market ORDER BY market
        """)).all()
    total = sum(int(r["n"]) for r in acad)
    buy = sum(int(r["buy"]) for r in acad)
    checks["pool_date_ge_2026_10_31"] = bool(pool_date and pool_date >= date(2026, 10, 31))
    checks["acad1_le_30"] = total <= 30
    checks["acad1_all_buy"] = total > 0 and buy == total
    checks["names_clean"] = int(names_bad or 0) == 0
    checks["four_market_reports"] = len({r[0] for r in reports}) >= 4

    result = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "asof_pool_date": str(pool_date),
        "asof_acad1_date": str(acad_date),
        "acad1_total": total,
        "acad1_buy": buy,
        "acad1_by_market": {r["market"]: {"n": int(r["n"]), "buy": int(r["buy"])} for r in acad},
        "reports": {str(k): str(v) for k, v in reports},
        "checks": checks,
        "pass": all(checks.values()),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 试运行 09:30 开跑检查 {result['checked_at']}", "", f"pass={result['pass']}", ""]
    for k, v in checks.items():
        lines.append(f"- {k}: {'PASS' if v else 'FAIL'}")
    lines += ["", f"pool_date={pool_date} acad1={total} buy={buy}", f"json={OUT}"]
    MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
