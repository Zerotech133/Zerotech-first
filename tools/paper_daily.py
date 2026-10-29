# -*- coding: utf-8 -*-
"""量化部 paper 日报（2026-10-30 WO-INV-PAPER-MVP）。

读当天 shadow_ledger 决策 → risk_gate → paper_book 纸面记账；不接实盘。
默认只处理 status=done 且有 recommendation 的决策；同日同票去重。

用法::

    python -X utf8 tools/paper_daily.py --date 2026-10-30 --json
    python -X utf8 tools/paper_daily.py --date 2026-10-30 --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date as _date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from agent_platform.paper import paper_book, risk_gate  # noqa: E402

SHADOW = os.path.join(REPO, "agent_platform", "data", "shadow", "shadow_ledger.jsonl")
PAPER_DIR = os.path.join(REPO, "agent_platform", "data", "paper")
CONFIG = os.path.join(REPO, "agent_platform", "paper", "paper_config.json")


def load_shadow(path=None):
    rows = []
    p = path or SHADOW
    if not os.path.exists(p):
        return rows
    with io.open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def latest_per_symbol(rows, day):
    latest = {}
    for r in rows:
        if str(r.get("date") or "") != day:
            continue
        if r.get("status") != "done" or not r.get("recommendation"):
            continue
        latest[str(r.get("symbol") or "")] = r
    return [latest[k] for k in sorted(latest)]


def run(day=None, shadow=None, state_path=None, ledger_path=None, config=None,
        dry_run=False, day_pnl_pct=None):
    day = day or _date.today().isoformat()
    if paper_book.kill_switch_on():
        return {"date": day, "killed": True, "reason": "PAPER_KILL/KILL file", "results": []}
    cfg = risk_gate.load_config(config or CONFIG)
    state = paper_book.load_state(state_path)
    decisions = latest_per_symbol(load_shadow(shadow), day)
    seen = set() if dry_run else paper_book.existing_keys(ledger_path)
    results = []
    for d in decisions:
        gate = risk_gate.evaluate(d, state=state, cfg=cfg, day_pnl_pct=day_pnl_pct)
        if dry_run:
            rec = {"duplicate": False, "record": None, "state": state}
        else:
            rec = paper_book.apply_decision(
                d, gate, state, day, ledger_path=ledger_path, seen=seen)
            state = rec["state"]
        results.append({"symbol": d.get("symbol"), "recommendation": d.get("recommendation"),
                        "gate": gate, "duplicate": rec["duplicate"],
                        "record": rec["record"]})
    if not dry_run and results:
        paper_book.save_state(state, state_path)
    return {"date": day, "killed": False,
            "decisions": len(decisions),
            "recorded": sum(1 for r in results if not r["duplicate"]),
            "blocked": sum(1 for r in results if r["gate"]["verdict"] == "block"),
            "reduced": sum(1 for r in results if r["gate"]["verdict"] == "reduce"),
            "nav": state.get("nav"), "cash_weight": state.get("cash_weight"),
            "positions": state.get("positions"), "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser(description="量化部 paper 日报（只读决策→纸面记账）")
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--shadow", default=SHADOW)
    ap.add_argument("--state", default=paper_book.DEFAULT_STATE)
    ap.add_argument("--ledger", default=paper_book.DEFAULT_LEDGER)
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--day-pnl", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.date, args.shadow, args.state, args.ledger, args.config,
              args.dry_run, args.day_pnl)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("[paper] date=%s decisions=%d recorded=%d blocked=%d reduced=%d nav=%s cash=%s" % (
            res["date"], res.get("decisions", 0), res.get("recorded", 0),
            res.get("blocked", 0), res.get("reduced", 0),
            res.get("nav"), res.get("cash_weight")))
    return 2 if res.get("killed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
