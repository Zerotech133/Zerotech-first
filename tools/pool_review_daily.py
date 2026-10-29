# -*- coding: utf-8 -*-
"""司马徽（ACAD1）推荐池每日复盘（2026-10-30 WO-INV-RECOMMEND-POOL）。

读取推荐池（默认 latest.json，缺失时自动 build）→ 逐票关联当日 D1 决策 →
写 `agent_platform/data/pool_review/<date>.json`（每票一条复盘记录）→ 组合计划。

用法::

    python -X utf8 tools/pool_review_daily.py --build --json
    python -X utf8 tools/pool_review_daily.py --date 2026-10-30 --json
    python -X utf8 tools/pool_review_daily.py --run-shadow --limit 5
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from datetime import date as _date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from agent_platform import (  # noqa: E402
    analysis_digest, portfolio_composer, recommendation_pool,
)

SHADOW = os.path.join(REPO, "agent_platform", "data", "shadow", "shadow_ledger.jsonl")
REVIEW_DIR = os.path.join(REPO, "agent_platform", "data", "pool_review")


def load_decisions(day, codes, shadow=None):
    latest = {}
    p = shadow or SHADOW
    if not os.path.exists(p):
        return latest
    with io.open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            sym = str(r.get("symbol") or "")
            if str(r.get("date") or "") == day and sym in codes and r.get("status") == "done":
                latest[sym] = r
    return latest


def review(day, pool, decisions, analyses=None, write=True):
    analyses = analyses or {}
    records = []
    for item in pool.get("items") or []:
        code = str(item.get("code") or "")
        d = (decisions or {}).get(code) or {}
        rec = {
            "date": day, "item_id": item.get("item_id"), "code": code,
            "name": item.get("name") or d.get("name") or "",
            "tier": item.get("tier"), "sources": item.get("sources") or [],
            "data_date": item.get("data_date"), "freshness_days": item.get("freshness_days"),
            "status": item.get("status"), "risk_flags": item.get("risk_flags") or [],
            "recommendation": d.get("recommendation"),
            "confidence": d.get("confidence"),
            "price": d.get("price_at_decision"),
            "parse_ok": bool(d.get("parse_ok")),
            "decision_status": d.get("status"),
            "action": "pending_analysis" if not d else (
                "buy" if str(d.get("recommendation") or "").upper() in ("BUY", "STRONG_BUY")
                else ("avoid" if str(d.get("recommendation") or "").upper() in ("SELL", "STRONG_SELL")
                      else "watch")),
            "analyses": analyses.get(code) or {},
        }
        records.append(rec)
    reviewed = sum(1 for r in records if r["decision_status"] == "done")
    pending = len(records) - reviewed
    summary = {
        "date": day, "pool_id": pool.get("pool_id"),
        "pool_total": len(records), "reviewed": reviewed, "pending": pending,
        "by_action": {a: sum(1 for r in records if r["action"] == a)
                      for a in ("buy", "watch", "avoid", "pending_analysis")},
        "by_tier": {t: sum(1 for r in records if r.get("tier") == t)
                    for t in (pool.get("tiers") or [])},
    }
    plan = portfolio_composer.compose(
        [{"code": r["code"], "name": r["name"], "industry": "",
          "tier": r["tier"]} for r in records],
        decisions, analyses=analyses)
    payload = {"schema": "acad1_pool_review/v1",
               "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "pool_id": pool.get("pool_id"), "summary": summary,
               "records": records, "plan": plan}
    if write:
        os.makedirs(REVIEW_DIR, exist_ok=True)
        path = os.path.join(REVIEW_DIR, day + ".json")
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        payload["path"] = path
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="ACAD1 推荐池每日复盘")
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--pool", default=None)
    ap.add_argument("--shadow", default=SHADOW)
    ap.add_argument("--run-shadow", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    if args.build or not args.pool:
        pool = recommendation_pool.build_pool(day=args.date)
    else:
        pool = json.load(io.open(args.pool, encoding="utf-8"))
    codes = [str(x.get("code")) for x in pool.get("items") or []]
    decisions = load_decisions(args.date, set(codes), shadow=args.shadow)
    missing = [c for c in codes if c not in decisions]
    if args.run_shadow and missing:
        batch = missing[:max(1, args.limit)]
        cmd = [sys.executable, "-X", "utf8",
               os.path.join(REPO, "agent_platform", "run_shadow.py"),
               "--codes", ",".join(batch)]
        print("[run-shadow] %s" % " ".join(cmd))
        subprocess.run(cmd, cwd=REPO)
        decisions = load_decisions(args.date, set(codes), shadow=args.shadow)
    analyses = analysis_digest.latest_analyses(codes)
    res = review(args.date, pool, decisions, analyses, write=not args.no_write)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        s = res["summary"]
        print("[pool] %s total=%d reviewed=%d pending=%d actions=%s" % (
            args.date, s["pool_total"], s["reviewed"], s["pending"], s["by_action"]))
        print(portfolio_composer.render_markdown(res["plan"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
