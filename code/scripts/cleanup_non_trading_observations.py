# -*- coding: utf-8 -*-
"""清理“非交易日”生成的 v2_strategy_observation。

默认 dry-run；只有显式 --apply 才删除，删除前先写 CSV 备份。
按 strategy_id/code 推断市场，再查 v2_market_calendar：
- CN/HK/US 周六日不生成；
- CRYPTO 周末可能开市，按日历保留；
- 无法判断市场的行不删除，列入 unknown 报告。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

ROOT = Path(r"C:\项目\zhice-os-v2-windows-20260920\code")
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from ingest_observations_backtest import (  # noqa: E402
    _is_market_open,
    _market_for_strategy,
)

COLUMNS = [
    "id", "strategy_id", "signal_date", "code", "name", "signal",
    "entry_date", "ret_t1", "ret_t5", "ret_t10", "ret_t20",
    "hit", "max_dd", "status", "updated_at",
]


def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _scan(conn, only_date: date | None = None) -> tuple[list[dict], list[dict]]:
    where = "WHERE signal_date IS NOT NULL"
    params: dict = {}
    if only_date:
        where += " AND signal_date=:d"
        params["d"] = only_date
    rows = conn.execute(sa.text(
        "SELECT " + ", ".join(COLUMNS) + " FROM v2_strategy_observation "
        + where + " ORDER BY signal_date, strategy_id, code"
    ), params).mappings().all()

    invalid: list[dict] = []
    unknown: list[dict] = []
    for r in rows:
        market = _market_for_strategy(str(r["strategy_id"]), str(r["code"]))
        if not market:
            unknown.append(dict(r))
            continue
        if _is_market_open(conn, market, r["signal_date"]):
            continue
        item = dict(r)
        item["market"] = market
        invalid.append(item)
    return invalid, unknown


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = COLUMNS + ["market"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="只处理某个 signal_date（YYYY-MM-DD）")
    ap.add_argument("--apply", action="store_true", help="确认删除（默认 dry-run）")
    args = ap.parse_args(argv)

    only_date = date.fromisoformat(args.date) if args.date else None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path(rf"C:\Temp\v2_strategy_observation_non_trading_{stamp}.csv")
    summary = Path(rf"C:\Temp\v2_strategy_observation_non_trading_{stamp}.json")

    engine = _engine()
    with engine.begin() as conn:
        invalid, unknown = _scan(conn, only_date)
        _write_csv(invalid + unknown, backup)
        payload = {
            "mode": "apply" if args.apply else "dry-run",
            "date": str(only_date) if only_date else None,
            "invalid": len(invalid),
            "unknown_market": len(unknown),
            "skipped_unknown_ids": [int(r["id"]) for r in unknown],
            "backup": str(backup),
            "invalid_by_market": {},
        }
        for r in invalid:
            m = r["market"]
            payload["invalid_by_market"][m] = payload["invalid_by_market"].get(m, 0) + 1

        if args.apply and invalid:
            ids = [int(r["id"]) for r in invalid]
            # 逐条删除，保持事务；ids 来自本事务刚扫描的行
            for oid in ids:
                conn.execute(sa.text(
                    "DELETE FROM v2_strategy_observation WHERE id=:id"
                ), {"id": oid})
            payload["deleted"] = len(ids)
        else:
            payload["deleted"] = 0

        summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
