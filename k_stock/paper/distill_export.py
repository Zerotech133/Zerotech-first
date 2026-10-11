# -*- coding: utf-8 -*-
"""蒸馏语料导出器（K-STOCK-100 M2，077u）。

从 paper.db（orders×equity_marks）+ judge_reviews 拼 episode 语料 →
data/distill_corpus.jsonl。schema 见 docs/plans/2026-10-11_模拟市场三端架构
_K-STOCK-100.md §4。原则：
- outcome（T+N 收益）留 null 由回填器后续补——绝不硬凑；
- guard_flags 记录执行链干预（守卫拦截/撤销/风控）——「Agent 想做什么」与
  「系统让它做了什么」的差值是蒸馏最贵的信号；
- 增量导出：按 orders.id 水位续写，重复跑幂等。

用法：
  python k_stock/paper/distill_export.py             # 增量导出
  python k_stock/paper/distill_export.py --since 1   # 最近 N 天
  python k_stock/paper/distill_export.py --stats     # 语料统计
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"
CORPUS = Path(__file__).resolve().parent.parent / "data" / "distill_corpus.jsonl"
REVIEWS = Path(__file__).resolve().parent.parent / "data" / "judge_reviews"


def _connect():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _watermark() -> int:
    """已导出最大 orders.id（语料文件尾扫一遍）。"""
    if not CORPUS.exists():
        return 0
    last = 0
    with CORPUS.open(encoding="utf-8") as f:
        for line in f:
            try:
                last = max(last, json.loads(line)["order_id"])
            except Exception:
                continue
    return last


def _guard_flags(reason: str, status: str) -> list[str]:
    flags = []
    r = reason or ""
    if "superseded" in r:
        flags.append("replan_cancelled")
    if "陈旧产物" in r:
        flags.append("stale_rejected")
    if status == "rejected":
        flags.append("rejected_limit_up" if "涨停" in r else "rejected_other")
    if status == "cancelled" and "superseded" not in r:
        flags.append("cancelled_other")
    return flags


def _judge_lookup() -> dict:
    """date → {score, flags}（评审已跑才有，缺失静默空）。"""
    out = {}
    if REVIEWS.exists():
        for fp in REVIEWS.glob("*.json"):
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                out[d["date"]] = {"score": d.get("score"),
                                  "flags": [f["rule"] for f in d.get("findings", [])
                                            if f["severity"] in ("high", "critical")]}
            except Exception:
                continue
    return out


def export(since_days: int | None = None) -> dict:
    con = _connect()
    try:
        wm = _watermark()
        sql = ("SELECT * FROM orders WHERE id>? AND source IN ('ai','manual') "
               "AND status IN ('pending','filled','rejected','cancelled')")
        args = [wm]
        if since_days:
            iso = (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
            sql += " AND ts>=?"
            args.append(iso)  # 占位：水位优先，since 仅提示用
        rows = con.execute(sql + " ORDER BY id", args).fetchall()
        judges = _judge_lookup()
        n = 0
        with CORPUS.open("a", encoding="utf-8") as f:
            for r in rows:
                o = dict(r)
                ts = o["ts"] or ""
                jdate = ts[:10].replace("-", "")
                j = judges.get(jdate, {})
                ep = {
                    "episode_id": f"{jdate}-{o['account_id']}-{o['stock']}-{o['side']}-{o['id']}",
                    "order_id": o["id"],
                    "ts": ts, "account": o["account_id"], "strategy": o["strategy"],
                    "context": {"run_ref": o["ref"], "conf": o["conf"],
                                "equity": None, "pos_pct": None, "regime": None},
                    "signal": {"side": o["side"], "qty": o["qty"],
                               "plan_date": o["plan_date"], "price_base": o["price_base"],
                               "reason": o["reason"]},
                    "exec": {"status": o["status"], "fill_price": o["filled_price"],
                             "fee": o["fee"], "order_kind": o["order_kind"],
                             "guard_flags": _guard_flags(o["reason"] or "", o["status"])},
                    "outcome": {"ret_5d": None, "ret_20d": None,
                                "mfe": None, "mae": None},
                    "judge": {"score": j.get("score"), "flags": j.get("flags", []),
                              "narrative": None},
                }
                f.write(json.dumps(ep, ensure_ascii=False) + "\n")
                n += 1
        return {"exported": n, "watermark_before": wm,
                "total_lines": sum(1 for _ in CORPUS.open(encoding="utf-8")) if CORPUS.exists() else 0,
                "path": str(CORPUS)}
    finally:
        con.close()


def stats() -> dict:
    if not CORPUS.exists():
        return {"total": 0}
    total = train = guarded = 0
    by_status: dict[str, int] = {}
    with CORPUS.open(encoding="utf-8") as f:
        for line in f:
            try:
                ep = json.loads(line)
            except Exception:
                continue
            total += 1
            if ep["exec"]["status"] == "filled":
                train += 1
            if ep["exec"]["guard_flags"]:
                guarded += 1
            by_status[ep["exec"]["status"]] = by_status.get(ep["exec"]["status"], 0) + 1
    return {"total": total, "trainable_filled": train,
            "guard_intervened": guarded, "by_status": by_status, "path": str(CORPUS)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="蒸馏语料导出")
    ap.add_argument("--since", type=int, default=None, help="最近 N 天（缺省全量增量）")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    r = stats() if a.stats else export(a.since)
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
