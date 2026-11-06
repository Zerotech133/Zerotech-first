# -*- coding: utf-8 -*-
"""ACAD1 观察待定行 + 青萍回测证据入库（真实数据；不伪造收益）。

- 观察：ACAD1 2026-10-29 池子 10 只 → status=pending（尚无未来行情）；
- 回测：qpzm 2026-10-30 批量回测 meta + 统计（来自 rps_batch_backtest.json）。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.config.settings import get_settings

ROOT = Path(r"C:\项目\老田的AI助手")


def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _market_for_strategy(strategy_id: str, code: str = "") -> str | None:
    """推断 observation 所属市场；无法可靠判断时返回 None（不猜）。"""
    sid = (strategy_id or "").strip().lower()
    if sid.startswith("v1-cn") or sid in {"chanlun", "dk"}:
        return "CN"
    if sid.startswith("v1-hk"):
        return "HK"
    if sid.startswith("v1-us"):
        return "US"
    if sid.startswith("v1-crypto"):
        return "CRYPTO"
    # qpzm 是跨市场策略，只能从代码格式判断
    if sid == "qpzm":
        c = (code or "").strip().upper()
        if c.startswith(("SH", "SZ", "BJ")) or c.endswith((".SH", ".SZ", ".BJ")):
            return "CN"
        if c.isdigit() and len(c) == 5:
            return "HK"
        if "/" in c:
            return "CRYPTO"
    return None


def _is_market_open(conn, market: str, trade_date: date) -> bool:
    """市场日历优先；无日历行时回退 profile；未知市场 fail-closed。"""
    market = (market or "").strip().upper()
    if not market:
        return False
    row = conn.execute(sa.text(
        "SELECT is_open FROM v2_market_calendar "
        "WHERE market=:m AND trade_date=:d"
    ), {"m": market, "d": trade_date}).first()
    if row is not None:
        return bool(row[0])
    try:
        from zhice_os_v2.domain.market.registry import get_profile
        return bool(get_profile(market).fallback_is_open(trade_date))
    except Exception:  # noqa: BLE001 - 未知市场/日历缺失时不写入
        return False


def _filter_trading_day_pending(conn, rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """只保留 signal_date 对所属市场为交易日的 observation 行。"""
    pending: list[dict] = []
    skipped: dict[str, int] = {}
    for r in rows:
        market = (r.get("market") or _market_for_strategy(
            str(r.get("strategy_id") or ""), str(r.get("code") or "")) or "").upper()
        if not market or not _is_market_open(conn, market, r["signal_date"]):
            key = market or "UNKNOWN"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        pending.append(dict(
            strategy_id=r["strategy_id"], signal_date=r["signal_date"],
            code=r["code"], name=r["name"], signal=r["signal"], status="pending",
        ))
    return pending, skipped


def main() -> int:
    engine = _engine()
    with engine.begin() as c:
        # 1) pending observations for ACAD1 pool；仅生成交易日 observation
        pool = c.execute(sa.text(
            "SELECT pool_date, market, strategy_id, code, name, signal FROM v2_acad1_pool "
            "WHERE pool_date = (SELECT max(pool_date) FROM v2_acad1_pool)"
        )).mappings().all()
        raw = [dict(strategy_id=r["strategy_id"], signal_date=r["pool_date"],
                    code=r["code"], name=r["name"], signal=r["signal"],
                    market=r["market"]) for r in pool]
        pending, skipped = _filter_trading_day_pending(c, raw)
        if skipped:
            print("skipped_non_trading=%d %s" % (
                sum(skipped.values()),
                ",".join("%s:%d" % (k, v) for k, v in sorted(skipped.items())),
            ))
        if pending:
            t = sa.table("v2_strategy_observation", *[sa.column(x) for x in (
                "strategy_id", "signal_date", "code", "name", "signal", "status")])
            st = pg_insert(t).values(pending).on_conflict_do_nothing(
                constraint="uq_v2_strategy_observation")
            c.execute(st)

        # 2) qpzm backtest evidence
        bt = json.loads((ROOT / "quant_backtest/reports/rps_batch_backtest.json")
                        .read_text(encoding="utf-8"))
        meta = bt.get("meta") or {}
        batches = bt.get("batches") or []
        priced = sum(1 for b in batches if b.get("n_picks"))
        metrics = {
            "n_batches": len(batches),
            "n_priced": priced,
            "holds": meta.get("holds"),
            "cost_roundtrip": meta.get("cost_roundtrip"),
            "generated_at": meta.get("generated_at"),
            "source": "rps_batch_backtest.json",
            "note": "批次回测统计；非组合净值/非样本外结论",
        }
        t2 = sa.table("v2_strategy_backtest", *[sa.column(x) for x in (
            "strategy_id", "window", "cost_model", "metrics", "equity_path", "manifest_sha")])
        t2 = sa.table("v2_strategy_backtest", sa.column("strategy_id", sa.String()), sa.column("window", sa.String()), sa.column("cost_model", sa.String()), sa.column("metrics", postgresql.JSONB()), sa.column("equity_path", sa.Text()), sa.column("manifest_sha", sa.String()))
        row = [dict(strategy_id="qpzm", window="batch_20261029",
                    cost_model="roundtrip=0.0015", metrics=metrics,
                    equity_path="", manifest_sha="")]
        stmt = pg_insert(t2).values(row)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_v2_strategy_backtest",
            set_={"metrics": stmt.excluded.metrics},
        )
        c.execute(stmt)
        print("pending_obs=%d backtest=1" % len(pending))
        print("obs_total", c.execute(sa.text("SELECT count(*) FROM v2_strategy_observation")).scalar())
        print("bt_total", c.execute(sa.text("SELECT count(*) FROM v2_strategy_backtest")).scalar())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
