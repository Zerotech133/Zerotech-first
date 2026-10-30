# -*- coding: utf-8 -*-
"""交易手法 paper 历史回放（WO-METHODS）。只读 K 线，不写 paper 账本。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from agent_platform import trading_methods as tm  # noqa: E402

POOL = os.path.join(REPO, "agent_platform", "data", "recommendation_pool", "latest.json")
OUT_DIR = os.path.join(REPO, "agent_platform", "data", "methods")


def _pool_codes(path=None, limit=None):
    try:
        d = json.load(open(path or POOL, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    codes = [str(x.get("code")) for x in d.get("items") or [] if x.get("code")]
    return codes[:limit] if limit else codes


def main(argv=None):
    ap = argparse.ArgumentParser(description="三手法 paper 回放（trend_pullback/volume_breakout/oversold_rebound）")
    ap.add_argument("--codes", default="", help="逗号分隔；缺省读 ACAD1 池")
    ap.add_argument("--limit", type=int, default=31)
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--methods", default=",".join(tm.METHODS))
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    codes = [x.strip() for x in args.codes.split(",") if x.strip()] or _pool_codes(limit=args.limit)
    methods = [x for x in args.methods.split(",") if x]
    res = tm.replay(codes, days=args.days, methods=methods)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = args.out or os.path.join(OUT_DIR, "replay_%s.json" % date.today().isoformat())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("replay ->", path)
    for m, st in res["methods"].items():
        print("%s: codes=%s trades=%s win_rate=%s avg_code_ret=%s" % (
            m, st.get("codes"), st.get("trades"), st.get("win_rate"),
            st.get("avg_code_return_pct")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
