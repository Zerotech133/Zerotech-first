# -*- coding: utf-8 -*-
"""从 sidecar 最新日线生成每日选股中台日报/选股/统一池（可重复跑）。

规则：
- 按 sidecar 策略 × 市场循环，不再只处理 CN；
- 买入/观察进入 picks；回避只写“风险回避”日报，不进入 picks；
- 某市场全回避也出日报，不静默跳过。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.config.settings import get_settings


def main() -> int:
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    reports = 0
    with e.begin() as c:
        for sid in ("qpzm", "chanlun", "dk"):
            markets = c.execute(sa.text("""
                SELECT market, max(data_date) AS d FROM v2_strategy_daily
                WHERE strategy_id=:s GROUP BY market ORDER BY market
            """), {"s": sid}).mappings().all()
            for m in markets:
                d = m["d"]
                rows = c.execute(sa.text("""
                    SELECT code,name,signal,score,reason FROM v2_strategy_daily
                    WHERE strategy_id=:s AND data_date=:d AND market=:m
                      AND (signal LIKE '%买%' OR signal LIKE '%观察%')
                      AND signal NOT LIKE '%回避%'
                """), {"s": sid, "d": d, "m": m["market"]}).mappings().all()
                uniq = {}
                for x in rows:
                    uniq.setdefault(x["code"], x)
                rows = list(uniq.values())
                avoid = bool(c.execute(sa.text("""
                    SELECT 1 FROM v2_strategy_daily
                    WHERE strategy_id=:s AND data_date=:d AND market=:m AND signal LIKE '%回避%'
                    LIMIT 1
                """), {"s": sid, "d": d, "m": m["market"]}).scalar())
                if not rows and not avoid and sid != "dk":
                    continue
                mv = ("环境闸门：全部回避（无买点）" if avoid and not rows
                      else "自动汇总：策略当日信号（待分析师补充观点）")
                rid = c.execute(sa.text("""
                    INSERT INTO v2_selection_report
                      (report_date,strategy_id,market,analyst,market_view,yesterday_review,lessons,status)
                    VALUES (:d,:s,:m,:a,:mv,'无上期复盘数据','', 'submitted')
                    ON CONFLICT ON CONSTRAINT uq_v2_selection_report DO UPDATE SET
                      status='submitted', submitted_at=now(), market_view=EXCLUDED.market_view
                    RETURNING id
                """), {"d": d, "s": sid, "m": m["market"], "a": "STRAT1", "mv": mv}).scalar()
                c.execute(sa.text("DELETE FROM v2_selection_pick WHERE report_id=:r"), {"r": rid})
                t = sa.table("v2_selection_pick", *[sa.column(x) for x in (
                    "report_id", "report_date", "strategy_id", "code", "name",
                    "signal_type", "score", "reason")])
                if rows:
                    c.execute(pg_insert(t).values([dict(
                        report_id=rid, report_date=d, strategy_id=sid, code=x["code"],
                        name=x["name"], signal_type=x["signal"], score=x["score"],
                        reason=x["reason"] or "") for x in rows]))
                reports += 1
        pool_date = c.execute(sa.text("SELECT max(report_date) FROM v2_selection_report")).scalar()
        c.execute(sa.text("DELETE FROM v2_selection_pool WHERE pool_date=:d"), {"d": pool_date})
        pool = c.execute(sa.text("""
            SELECT p.code, max(p.name) name, string_agg(DISTINCT p.strategy_id, ',') src,
                   string_agg(DISTINCT p.signal_type, ',') sigs, max(p.score) mx
            FROM v2_selection_pick p
            JOIN v2_selection_report r ON r.id=p.report_id
            WHERE (p.strategy_id, r.market, p.report_date) IN (
              SELECT strategy_id, market, max(report_date)
              FROM v2_selection_report GROUP BY strategy_id, market)
            GROUP BY p.code
        """)).mappings().all()
        t = sa.table("v2_selection_pool", *[sa.column(x) for x in (
            "pool_date", "code", "name", "source_strategies", "signal_types", "max_score", "status")])
        if pool:
            c.execute(pg_insert(t).values([dict(
                pool_date=pool_date, code=x["code"], name=x["name"] or "",
                source_strategies=x["src"] or "", signal_types=x["sigs"] or "",
                max_score=x["mx"], status="published") for x in pool]))
        print("pool_date=%s reports=%d pool=%d" % (pool_date, reports, len(pool)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
