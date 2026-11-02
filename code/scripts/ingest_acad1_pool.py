# -*- coding: utf-8 -*-
"""ACAD1 首批选池（2026-10-29 数据）→ v2_acad1_pool / v2_acad1_review。

规则（可复核，不是随机）：
- 只取 sidecar `chanlun` 的 CN 买点；
- 排除 ST/*ST 与北交所（920 开头）；
- 每只票只保留优先级最高买点：1买 > 2买 > 3买；
- 趋势优先级 up > flat/range > down；
- 最多 10 只，作为“研究池”，不是交易池。
青萍 CN 当日无买入/观察；量子双刀未运行 → 不选。
"""
from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.config.settings import get_settings

POOL_DATE = date(2026, 10, 29)


def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _pick(conn) -> list[dict]:
    rows = conn.execute(sa.text("""
        SELECT DISTINCT ON (code)
            strategy_id, code, name, market, signal, score, data_date, reason,
            CASE signal WHEN '1买' THEN 1 WHEN '2买' THEN 2 ELSE 3 END AS sig_rank,
            CASE
                WHEN reason LIKE '%trend=up%' THEN 1
                WHEN reason LIKE '%trend=flat%' OR reason LIKE '%trend=range%' THEN 2
                ELSE 3
            END AS trend_rank
        FROM v2_strategy_daily
        WHERE strategy_id = 'chanlun' AND market = 'CN' AND data_date = :d
          AND signal LIKE '%买%'
          AND name NOT LIKE '%ST%'
          AND code NOT LIKE '920%'
        ORDER BY code,
            CASE signal WHEN '1买' THEN 1 WHEN '2买' THEN 2 ELSE 3 END,
            CASE
                WHEN reason LIKE '%trend=up%' THEN 1
                WHEN reason LIKE '%trend=flat%' OR reason LIKE '%trend=range%' THEN 2
                ELSE 3
            END
    """), {"d": POOL_DATE}).mappings().all()
    rows = sorted(rows, key=lambda r: (r["sig_rank"], r["trend_rank"], r["code"]))[:10]
    out = []
    for r in rows:
        out.append(dict(
            pool_date=POOL_DATE, strategy_id="chanlun", code=r["code"], name=r["name"],
            market="CN", signal=r["signal"], score=r["score"],
            selection_reason=(
                "ACAD1选池：缠论%s信号，趋势%s，非ST、非北交所；研究池非交易指令"
                % (r["signal"], "up" if "trend=up" in (r.get("reason") or "") else "非up")
            ),
            selected_by="ACAD1", status="selected", data_date=r["data_date"],
        ))
    return out


def main() -> int:
    engine = _engine()
    with engine.begin() as conn:
        rows = _pick(conn)
        if rows:
            t = sa.table("v2_acad1_pool", *[sa.column(c) for c in (
                "pool_date", "strategy_id", "code", "name", "market", "signal",
                "score", "selection_reason", "selected_by", "status", "data_date")])
            stmt = pg_insert(t).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_v2_acad1_pool_item",
                set_={c: stmt.excluded[c] for c in (
                    "name", "signal", "score", "selection_reason", "status", "data_date")},
            )
            conn.execute(stmt)
        # review：同一天一条，幂等重建
        conn.execute(sa.text("DELETE FROM v2_acad1_review WHERE review_date = :d AND topic = :t"),
                     {"d": POOL_DATE, "t": "2026-10-29 首批研究池选池"})
        conn.execute(sa.text("""
            INSERT INTO v2_acad1_review
                (review_date, topic, strategy_id, verdict, summary, evidence, reviewer)
            VALUES (:d, :topic, 'chanlun', '观察', :summary, :evidence, 'ACAD1')
        """), {
            "d": POOL_DATE,
            "topic": "2026-10-29 首批研究池选池",
            "summary": "从缠论 CN 买点选 %d 只入研究池；青萍 CN 无买入/观察，量子双刀未运行，均未选。研究池不是交易池。" % len(rows),
            "evidence": "chanlun 10-29 CN 49 条/买点 22；排除 ST/*ST 与 920 北交所；优先级 1买>2买>3买、趋势 up 优先；最多 10 只。",
        })
        print("pool_rows=%d review=1" % len(rows))
        for r in rows:
            print(r["code"], r["name"], r["signal"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
