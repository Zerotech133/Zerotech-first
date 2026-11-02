# -*- coding: utf-8 -*-
"""候选池评分 v0（只读，不写库）：40/25/15/10/10。

用法：
  python scripts/score_candidate_pool.py [--date YYYY-MM-DD] [--top 30] [--json PATH]
不改变 ACAD1 池/研究卡/任何状态；供人工评审前排序参考。
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

W_PCT = 0.40
W_RES = 0.25
W_DQ = 0.15
W_RELIABILITY = 0.10
W_RISK = 0.10
RELIABILITY_NEUTRAL = 0.5


def percentile_rank(values: list[float]) -> list[float]:
    """返回 0~1 百分位；相同值取平均秩。"""
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (values[i], i))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        pct = 0.0 if n == 1 else avg / (n - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = pct
        i = j + 1
    return ranks


def _resonance_score(srcs: str) -> float:
    # v0：来源策略去重计数，4 个及以上记满分；不做家族去重（后续版本）
    n = len({x.strip() for x in (srcs or "").split(",") if x.strip()})
    return min(1.0, n / 4.0)


def _data_quality(row: dict, has_bar: bool) -> float:
    score = 1.0
    if not row.get("name"):
        score -= 0.2
    if not row.get("signal_types"):
        score -= 0.2
    if row.get("max_score") is None:
        score -= 0.2
    if not row.get("source_strategies"):
        score -= 0.2
    if not has_bar:
        score -= 0.2
    return max(0.0, round(score, 4))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", default=r"C:\Temp\candidate_scores.json")
    args = ap.parse_args()

    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        d = date.fromisoformat(args.date) if args.date else c.execute(
            sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
        rows = c.execute(sa.text("""
            SELECT code, name, market, source_strategies, signal_types, max_score
            FROM v2_selection_pool WHERE pool_date=:d
        """), {"d": d}).mappings().all()
        codes = [r["code"] for r in rows]
        bars = set()
        if codes:
            bars = {r[0] for r in c.execute(sa.text(
                "SELECT DISTINCT symbol FROM v2_bar_daily WHERE market IN ('CN','HK','US') "
                "AND symbol = ANY(:codes)"), {"codes": codes}).all()}
    if not rows:
        print("no rows", d)
        return 2

    by_market: dict[str, list[dict]] = {}
    for r in rows:
        by_market.setdefault(r["market"] or "CN", []).append(dict(r))

    out = []
    for mkt, items in by_market.items():
        vals = [float(x.get("max_score") or 0) for x in items]
        pcts = percentile_rank(vals)
        for x, pct in zip(items, pcts):
            dq = _data_quality(x, x["code"] in bars)
            res = _resonance_score(x.get("source_strategies") or "")
            risk = 0.0  # v0 无风险数据，显式置 0，不用猜测
            total = (W_PCT * pct + W_RES * res + W_DQ * dq
                     + W_RELIABILITY * RELIABILITY_NEUTRAL - W_RISK * risk)
            out.append({
                "market": mkt, "code": x["code"], "name": x.get("name") or "",
                "raw_score": float(x["max_score"]) if x.get("max_score") is not None else None,
                "pct_rank": round(pct, 4),
                "resonance_score": round(res, 4),
                "data_quality": dq,
                "reliability": RELIABILITY_NEUTRAL,
                "risk_penalty": risk,
                "total": round(total, 4),
                "source_strategies": x.get("source_strategies") or "",
                "signal_types": x.get("signal_types") or "",
                "score_version": "v0_20261031",
            })

    out.sort(key=lambda x: (-x["total"], x["market"], x["code"]))
    payload = {"pool_date": str(d), "top": out[: args.top], "all": out}
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("date", d, "rows", len(out), "saved", args.json)
    for x in out[: args.top]:
        print("%-2s %-10s %-8s total=%.4f pct=%.3f res=%.2f dq=%.2f raw=%s" % (
            x["market"], x["code"], (x["name"] or "")[:8], x["total"], x["pct_rank"],
            x["resonance_score"], x["data_quality"], x["raw_score"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
