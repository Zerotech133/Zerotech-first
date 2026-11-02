# -*- coding: utf-8 -*-
"""首批 sidecar 策略数据入库（幂等）。

数据源（只读）：
- 青萍之末：quant_backtest/reports/rps_daily_batches.jsonl 最新 live 批次；
- 缠论：quant_backtest/data/chanlun_pool.jsonl 最新 detect_date 的 CN 记录；
- 量子双刀：无任务，仅登记 research 空记录。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.config.settings import get_settings

ROOT = Path(r"C:\项目\老田的AI助手")
SIDECARS = "v2_strategy_sidecar"
DAILY = "v2_strategy_daily"


def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _date(yyyymmdd: str) -> date:
    s = str(yyyymmdd).replace("-", "")[:8]
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _sidecars(conn):
    rows = [
        dict(strategy_id="qpzm", display_name="青萍之末", status="live", owner="STRAT1",
             ops_support="ST1", source_type="task", health="green",
             data_date=date(2026, 10, 29),
             note="RpsDaily 日批；live picks=56；回测报告另存"),
        dict(strategy_id="chanlun", display_name="缠论三类买卖点", status="shadow", owner="STRAT1",
             ops_support="ST1", source_type="task", health="green",
             data_date=date(2026, 10, 29),
             note="ChanlunScan 扫描；最新 detect 2026-10-29 CN 49 条/买点 22"),
        dict(strategy_id="dk", display_name="量子双刀", status="research", owner="STRAT1",
             ops_support="", source_type="none", health="no_task", data_date=None,
             note="无计划任务，仅研究态；不产每日选股"),
    ]
    stmt = pg_insert(sa.table(SIDECARS, *[sa.column(k) for k in (
        "strategy_id", "display_name", "status", "owner", "ops_support",
        "source_type", "health", "data_date", "note")])).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["strategy_id"],
        set_={k: stmt.excluded[k] for k in (
            "display_name", "status", "owner", "ops_support", "source_type",
            "health", "data_date", "note")},
    )
    conn.execute(stmt)


def _qpzm_rows():
    p = ROOT / "quant_backtest/reports/rps_daily_batches.jsonl"
    last = None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                last = json.loads(line)
            except ValueError:
                continue
    if not last:
        return []
    d = _date(last["date"])
    out = []
    for x in last.get("picks") or []:
        code = str(x.get("code") or "").strip()
        if not code:
            continue
        out.append(dict(strategy_id="qpzm", market=str(x.get("market") or "CN").upper(),
                        trade_date=d, data_date=d, code=code,
                        name=str(x.get("name") or ""), signal=str(x.get("action") or ""),
                        score=x.get("score"), reason=str(x.get("dims") or ""),
                        snapshot_sha=""))
    return out


def _chanlun_rows():
    p = ROOT / "quant_backtest/data/chanlun_pool.jsonl"
    recs = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    cn = [r for r in recs if r.get("market") == "CN"]
    latest = max((str(r.get("detect_date") or "") for r in cn), default="")
    out = []
    for r in cn:
        if str(r.get("detect_date") or "") != latest:
            continue
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        out.append(dict(strategy_id="chanlun", market="CN",
                        trade_date=_date(latest), data_date=_date(latest), code=code,
                        name=str(r.get("name") or ""), signal=str(r.get("type") or ""),
                        score=None,
                        reason="detect_date=%s; trend=%s; bei=%s" % (
                            latest, r.get("trend"), r.get("bei")),
                        snapshot_sha=""))
    return out


def main() -> int:
    engine = _engine()
    with engine.begin() as conn:
        _sidecars(conn)
        rows = _qpzm_rows() + _chanlun_rows()
        if rows:
            cols = ["strategy_id", "market", "trade_date", "data_date", "code",
                    "name", "signal", "score", "reason", "snapshot_sha"]
            stmt = pg_insert(sa.table(DAILY, *[sa.column(c) for c in cols])).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_v2_strategy_daily_signal",
                set_={c: stmt.excluded[c] for c in (
                    "name", "signal", "score", "reason", "data_date", "snapshot_sha")},
            )
            conn.execute(stmt)
        print("sidecars=3 daily_rows=%d" % len(rows))
        counts = dict(conn.execute(sa.text(
            "SELECT strategy_id, count(*) FROM v2_strategy_daily GROUP BY strategy_id"
        )).all())
        print("db_counts", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
