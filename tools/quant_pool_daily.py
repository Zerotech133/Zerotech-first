# -*- coding: utf-8 -*-
"""每日股票池 → 组合计划（2026-10-30 WO-INV-POOL-COMBO）。

用法::

    python -X utf8 tools/quant_pool_daily.py --codes 600519,000858,601318
    python -X utf8 tools/quant_pool_daily.py --source xunyou --top 3
    python -X utf8 tools/quant_pool_daily.py --source xunyou --top 3 --run-shadow
    python -X utf8 tools/quant_pool_daily.py --source picks --top 3 --json

只读组合计划，不接实盘；`--run-shadow` 会跑 run_shadow --codes（耗时较长）。
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
from agent_platform import portfolio_composer, watchlist  # noqa: E402

SHADOW = os.path.join(REPO, "agent_platform", "data", "shadow", "shadow_ledger.jsonl")


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
            if str(r.get("date") or "") != day:
                continue
            if str(r.get("symbol") or "") not in codes:
                continue
            if r.get("status") != "done":
                continue
            latest[str(r["symbol"])] = r
    return latest


def main(argv=None):
    ap = argparse.ArgumentParser(description="每日股票池组合计划（只读）")
    ap.add_argument("--codes", default=None, help="逗号分隔代码，如 600519,000858")
    ap.add_argument("--source", default="xunyou", choices=["xunyou", "picks"])
    ap.add_argument("--pool-file", default=None)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--shadow", default=SHADOW)
    ap.add_argument("--run-shadow", action="store_true",
                    help="对池内代码跑 run_shadow（真跑模型，耗时）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    pool = watchlist.resolve_codes(codes=args.codes, pool_file=args.pool_file,
                                   source=args.source, top=args.top)
    if not pool:
        print("[pool] 无法解析股票池", file=sys.stderr)
        return 2
    codes = [x["code"] for x in pool]
    print("[pool] %s" % ",".join("%s/%s" % (x["code"], x.get("name") or "") for x in pool))
    if args.run_shadow:
        cmd = [sys.executable, "-X", "utf8",
               os.path.join(REPO, "agent_platform", "run_shadow.py"),
               "--codes", ",".join(codes)]
        print("[run-shadow] %s" % " ".join(cmd))
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            print("[run-shadow] rc=%s（继续用已有决策）" % r.returncode,
                  file=sys.stderr)
    decisions = load_decisions(args.date, codes, shadow=args.shadow)
    plan = portfolio_composer.compose(pool, decisions)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(portfolio_composer.render_markdown(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
