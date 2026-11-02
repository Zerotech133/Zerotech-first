# -*- coding: utf-8 -*-
"""候选池 → ACAD1 研究池同步（基础信号优先 + 增强排序）。

顺序：
1. 只允许基础信号 base_state=BUY 的候选进入 ACAD1；
2. 在 BUY 内按「策略共振 > 证据 > 评分 > 代码」排序；
3. 每市场配额 CN12/HK10/US6/CRYPTO2，合计≤30；
4. 只写研究池，不自动通过/否决。

用法：python scripts/sync_candidate_to_acad1.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.config.settings import get_settings

QUOTAS = {"CN": 12, "HK": 10, "US": 6, "CRYPTO": 2}
TOTAL_CAP = 30
EVIDENCE = Path(r"C:\Temp\acad1_evidence_bundle.json")


def evidence_rank() -> dict:
    out = {}
    if not EVIDENCE.exists():
        return out
    try:
        for it in json.loads(EVIDENCE.read_text(encoding="utf-8")).get("items") or []:
            lvl = {"full": 3, "partial": 2, "none": 1}.get(it.get("evidence_level"), 0)
            out[(it.get("market"), it.get("code"))] = lvl
            if it.get("pool_code"):
                out[(it.get("market"), it.get("pool_code"))] = lvl
    except Exception:  # noqa: BLE001
        pass
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.begin() as c:
        d = c.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
        if not d:
            print("no candidate pool"); return 0
        base_latest = c.execute(sa.text("SELECT max(trade_date) FROM v2_base_signal")).scalar()
        if not base_latest or base_latest < d:
            print(f"stale base signals: base={base_latest} pool={d}（先重建基础信号）")
            return 2
        rows = c.execute(sa.text("""
            SELECT sp.code, sp.name, sp.market, sp.source_strategies, sp.signal_types, sp.max_score,
                   b.base_state, b.trigger_rule
            FROM v2_selection_pool sp
            LEFT JOIN LATERAL (
                SELECT base_state, trigger_rule FROM v2_base_signal bs
                WHERE bs.market=sp.market AND bs.code=sp.code
                ORDER BY bs.trade_date DESC LIMIT 1
            ) b ON true
            WHERE sp.pool_date=:d
        """), {"d": d}).mappings().all()
        ev = evidence_rank()
        selected, skipped = [], []
        for mkt, quota in QUOTAS.items():
            pool = []
            for r in [x for x in rows if x["market"] == mkt]:
                if r["base_state"] != "BUY":
                    skipped.append({"market": mkt, "code": r["code"], "base_state": r["base_state"]})
                    continue
                srcs = [x for x in (r["source_strategies"] or "").split(",") if x.strip()]
                res = len(srcs)
                e_rank = ev.get((mkt, r["code"]), 0)
                pool.append((res, e_rank, float(r["max_score"] or 0), r["code"], r, srcs))
            pool.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
            for _, _, _, _, r, srcs in pool[:quota]:
                if len(selected) >= TOTAL_CAP:
                    break
                selected.append((r, srcs))
        if not selected:
            print("no BUY candidates"); return 0
        vals = []
        for r, srcs in selected:
            res = len(srcs)
            e_rank = ev.get((r["market"], r["code"]), 0)
            ev_label = {3: "full", 2: "partial", 1: "none"}.get(e_rank, "unknown")
            vals.append(dict(
                pool_date=d, strategy_id=srcs[0] if srcs else "MULTI",
                code=r["code"], name=r["name"] or "", market=r["market"] or "CN",
                signal=r["signal_types"] or "", score=r["max_score"],
                selection_reason=(
                    f"基础信号：买点；来源[{','.join(srcs) or '—'}]；信号[{r['signal_types'] or '—'}]；评分{r['max_score']}。"
                    f"增强排序：共振{res}维；证据{ev_label}。研究池非交易，不自动通过/否决。"
                ),
                selected_by="ACAD1", status="selected", data_date=d,
            ))
        print(json.dumps({"pool_date": str(d), "base_latest": str(base_latest), "selected": len(vals),
                          "skipped_non_buy": len(skipped),
                          "skipped_sample": skipped[:8], "dry_run": args.dry_run}, ensure_ascii=False))
        if args.dry_run:
            return 0
        t = sa.table("v2_acad1_pool", *[sa.column(x) for x in (
            "pool_date", "strategy_id", "code", "name", "market", "signal",
            "score", "selection_reason", "selected_by", "status", "data_date")])
        stmt = pg_insert(t).values(vals)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_v2_acad1_pool_item",
            set_={k: getattr(stmt.excluded, k) for k in (
                "name", "signal", "score", "selection_reason", "status", "data_date")})
        c.execute(stmt)
        codes = [r["code"] for r, _ in selected]
        c.execute(sa.text("DELETE FROM v2_acad1_pool WHERE pool_date=:d AND code NOT IN :codes"),
                  {"d": d, "codes": tuple(codes)})
        c.execute(sa.text("DELETE FROM v2_acad1_review WHERE review_date=:d AND topic=:t"),
                  {"d": d, "t": "候选池同步 ACAD1日复核30"})
        c.execute(sa.text("""
            INSERT INTO v2_acad1_review (review_date, topic, strategy_id, verdict, summary, evidence, reviewer)
            VALUES (:d,'候选池同步 ACAD1日复核30','', '观察', :s, :e, 'ACAD1')
        """), {"d": d, "s": f"先过滤基础信号 BUY，再按共振/证据/评分取每市场配额（合计≤{TOTAL_CAP}）；研究池非交易。",
                 "e": f"来源：v2_selection_pool + v2_base_signal；非 BUY 跳过 {len(skipped)} 只。"})
        print("pool_date=%s synced=%d" % (d, len(vals)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
