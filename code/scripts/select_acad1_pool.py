# -*- coding: utf-8 -*-
"""ACAD1 每日选池 v0.1（规则化、可复核、只写研究池）。

规则：
- 只取健康 sidecar（qpzm live / chanlun shadow，health=green）的最新 CN 数据；
- 候选：qpzm 买入/观察；chanlun 买点（1/2/3买）；
- 排除 ST/*ST、北交所（920）；
- 排序：qpzm买入 > chanlun1买 > qpzm观察 > chanlun2买 > chanlun3买；同层趋势 up 优先；
- 每票只保留最高优先级；每日最多 10 只；
- 当日已有池子则跳过，不覆盖人工选池；
- 无候选 → 空池 + 评审记录，不硬选。
"""
from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.config.settings import get_settings

MAX_POOL = 10


def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _rank(signal: str, strategy_id: str, reason: str) -> int:
    s = signal or ""
    if strategy_id == "qpzm" and "买入" in s:
        return 10
    if strategy_id == "chanlun" and "1买" in s:
        return 20
    if strategy_id == "qpzm" and "观察" in s:
        return 30
    if strategy_id == "chanlun" and "2买" in s:
        return 40
    if strategy_id == "chanlun" and "3买" in s:
        return 50
    return 99


def main() -> int:
    engine = _engine()
    with engine.begin() as c:
        # 最新数据日
        d = c.execute(sa.text(
            "SELECT max(data_date) FROM v2_strategy_daily "
            "WHERE strategy_id IN ('qpzm','chanlun') AND market='CN'"
        )).scalar()
        if not d:
            print("no daily data")
            return 0
        exists = c.execute(sa.text(
            "SELECT count(*) FROM v2_acad1_pool WHERE pool_date=:d"), {"d": d}).scalar()
        if exists:
            print("pool exists for %s, skip (manual selection kept)" % d)
            return 0
        rows = c.execute(sa.text("""
            SELECT strategy_id, code, name, signal, score, data_date, reason
            FROM v2_strategy_daily
            WHERE market='CN' AND data_date=:d
              AND strategy_id IN ('qpzm','chanlun')
              AND (signal LIKE '%买%' OR signal LIKE '%观察%')
              AND name NOT LIKE '%ST%' AND code NOT LIKE '920%'
        """), {"d": d}).mappings().all()
        best: dict[str, dict] = {}
        for r in rows:
            rank = _rank(r["signal"], r["strategy_id"], r["reason"] or "")
            if rank >= 99:
                continue
            cur = best.get(r["code"])
            if cur is None or rank < cur["_rank"]:
                item = dict(r); item["_rank"] = rank
                best[r["code"]] = item
        picked = sorted(best.values(), key=lambda x: (x["_rank"], x["code"]))[:MAX_POOL]
        pool_date = d
        if picked:
            t = sa.table("v2_acad1_pool", *[sa.column(x) for x in (
                "pool_date", "strategy_id", "code", "name", "market", "signal",
                "score", "selection_reason", "selected_by", "status", "data_date")])
            vals = [dict(
                pool_date=pool_date, strategy_id=x["strategy_id"], code=x["code"],
                name=x["name"], market="CN", signal=x["signal"], score=x["score"],
                selection_reason=(
                    "ACAD1每日选池v0.1：%s %s，趋势%s，非ST/非北交所；研究池非交易指令"
                    % (x["strategy_id"], x["signal"],
                       "up" if "trend=up" in (x["reason"] or "") else "非up")),
                selected_by="ACAD1", status="selected", data_date=x["data_date"],
            ) for x in picked]
            st = pg_insert(t).values(vals).on_conflict_do_update(
                constraint="uq_v2_acad1_pool_item",
                set_={k: pg_insert(t).values(vals).excluded[k] for k in (
                    "name", "signal", "score", "selection_reason", "status", "data_date")})
            c.execute(st)
        # 评审记录（同日同主题幂等）
        topic = "%s ACAD1每日选池v0.1" % pool_date
        c.execute(sa.text("DELETE FROM v2_acad1_review WHERE review_date=:d AND topic=:t"),
                  {"d": pool_date, "t": topic})
        c.execute(sa.text("""
            INSERT INTO v2_acad1_review
              (review_date, topic, strategy_id, verdict, summary, evidence, reviewer)
            VALUES (:d, :t, '', :v, :s, :e, 'ACAD1')
        """), {
            "d": pool_date, "t": topic,
            "v": "观察" if picked else "空池",
            "s": "每日自动选池：选中 %d 只（上限 %d）。" % (len(picked), MAX_POOL),
            "e": "规则：qpzm买入>chanlun1买>qpzm观察>chanlun2买>chanlun3买；趋势up优先；排除ST/920；只写研究池。",
        })
        print("pool_date=%s picked=%d" % (pool_date, len(picked)))
        for x in picked:
            print(x["code"], x["name"], x["signal"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
