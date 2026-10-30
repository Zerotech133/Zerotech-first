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
    ap = argparse.ArgumentParser(description="三手法 paper 回放（含费用/滑点/仓位/大盘regime）")
    ap.add_argument("--codes", default="", help="逗号分隔；缺省读 ACAD1 池")
    ap.add_argument("--limit", type=int, default=31)
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--methods", default=",".join(tm.METHODS))
    ap.add_argument("--index", default="sh000001")
    ap.add_argument("--source", default="auto", choices=["auto", "local", "network"])
    ap.add_argument("--sector", dest="sector", action="store_true", default=True)
    ap.add_argument("--no-sector", dest="sector", action="store_false")
    ap.add_argument("--classic-exit", action="store_true", help="关闭移动止盈/ATR目标，用于对比")
    ap.add_argument("--relative-strength", action="store_true", help="个股相对行业指数20日强弱过滤")
    ap.add_argument("--commission", type=float, default=tm.DEFAULT_COSTS["commission"])
    ap.add_argument("--stamp", type=float, default=tm.DEFAULT_COSTS["stamp"])
    ap.add_argument("--slippage", type=float, default=tm.DEFAULT_COSTS["slippage"])
    ap.add_argument("--single-max", type=float, default=tm.DEFAULT_LIMITS["single_max"])
    ap.add_argument("--total-max", type=float, default=tm.DEFAULT_LIMITS["total_max"])
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    codes = [x.strip() for x in args.codes.split(",") if x.strip()] or _pool_codes(limit=args.limit)
    methods = [x for x in args.methods.split(",") if x]
    costs = {"commission": args.commission, "stamp": args.stamp, "slippage": args.slippage}
    limits = {"single_max": args.single_max, "total_max": args.total_max}
    if args.source == "local":
        kfn = tm.local_kline
    elif args.source == "network":
        def kfn(code, n):
            import sys as _s
            _s.path.insert(0, os.path.join(REPO, "agent_platform", "engine"))
            from tools import kline_daily as _k
            return _k(code, n)
    else:
        kfn = tm.auto_kline
    sector_map, sector_meta = {}, {"sector_covered": 0, "reason": "disabled"}
    if args.sector:
        try:
            from agent_platform import sector_regime as sr
            sector_map, sector_meta = sr.load_regime(codes)
        except Exception as exc:  # noqa: BLE001
            sector_meta = {"sector_covered": 0, "reason": str(exc)[:120]}
    res = tm.replay_with_constraints(codes, days=args.days, methods=methods,
                                     kline_fn=kfn, index_kline_fn=kfn,
                                     costs=costs, limits=limits, index_code=args.index,
                                     sector_regime_by_code=sector_map,
                                     exit_opt=not args.classic_exit,
                                     relative_strength=args.relative_strength)
    res["sector"] = sector_meta
    os.makedirs(OUT_DIR, exist_ok=True)
    path = args.out or os.path.join(OUT_DIR, "replay_net_%s.json" % date.today().isoformat())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("replay ->", path)
    for m, st in res["methods"].items():
        print("%s: trades=%s win_rate=%s nav_end=%s total=%s%% max_dd=%s%% regime=%s" % (
            m, st.get("trades"), st.get("win_rate"), st.get("nav_end"),
            st.get("total_return_pct"), st.get("max_drawdown_pct"),
            st.get("regime_filter")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
