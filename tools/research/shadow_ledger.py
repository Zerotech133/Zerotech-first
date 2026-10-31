# -*- coding: utf-8 -*-
"""选股影子盘（P2）：模拟 T+1 收盘调仓，记录 NAV/回撤/换手，输出门户 JSON。

账本：research/shadow/selection/ledger.jsonl  （{date, nav, ret, turnover, n_positions}）
持仓：research/shadow/selection/positions_<date>.json（当日调仓后权重）
门户：portal/static/shadow_selection.json

用法：
  python tools/research/shadow_ledger.py --backfill 20     # 回填最近 20 个交易日（模拟）
  python tools/research/shadow_ledger.py --date 20261030    # 单日调仓并记录
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
SHADOW = REPO / "research" / "shadow" / "selection"
STATIC = REPO / "portal" / "static"
sys.path.insert(0, str(REPO / "tools" / "research"))
import portfolio_build  # noqa: E402
import selection_engine  # noqa: E402

COST = 0.003


def trade_dates(n: int = 30) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs[-n:]]


def adj_close(dt: str, codes: list[str]) -> pd.Series:
    cf = DATA / "daily" / f"d_{dt}.parquet"
    af = DATA / "adj_factor" / f"d_{dt}.parquet"
    c = pq.read_table(cf, columns=["ts_code", "close"]).to_pandas()
    a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas() if af.exists() else pd.DataFrame({"ts_code": [], "adj_factor": []})
    m = c.merge(a, on="ts_code", how="left")
    m["adj"] = m["close"] * m["adj_factor"].fillna(1.0)
    return m.set_index("ts_code")["adj"].reindex(codes)


def step(prev_pos: dict | None, date: str, next_date: str) -> dict:
    """prev_pos 在 date 收盘建立；计算 next_date 日收益（T+1 持有），并在 next_date 调仓。"""
    w_prev = prev_pos["weights"] if prev_pos else {}
    codes = list(w_prev)
    if codes:
        c0 = adj_close(date, codes)
        c1 = adj_close(next_date, codes)
        ret_i = (c1 / c0 - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        gross = float(sum(w_prev[c] * float(ret_i[c]) for c in codes))
    else:
        gross = 0.0
    # next_date 收盘调仓（生成新持仓）
    pos = portfolio_build.build(next_date)
    w_new = {h["ts_code"]: h["weight"] for h in pos["holdings"]}
    turnover = float(sum(abs(w_new.get(c, 0) - w_prev.get(c, 0)) for c in set(w_new) | set(w_prev)))
    net = gross - COST * turnover
    return {"gross": gross, "net": net, "turnover": turnover, "weights": w_new, "positions": pos}


def write_outputs(ledger: list[dict], latest_pos: dict) -> None:
    nav = 1.0
    rows = []
    for r in ledger:
        nav *= 1 + r["net"]
        rows.append({**r, "nav": round(nav, 6)})
    if rows:
        s = pd.Series([r["nav"] for r in rows])
        rets = s.pct_change().dropna()
        mdd = float((s / s.cummax() - 1).min())
        ann = float((1 + rets.mean()) ** 252 - 1) if len(rets) > 5 and rets.mean() > -1 else None
        vol = float(rets.std(ddof=1) * np.sqrt(252)) if len(rets) > 2 else None
    else:
        mdd, ann, vol = 0.0, None, None
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ledger": rows[-60:],
        "nav": round(rows[-1]["nav"], 6) if rows else 1.0,
        "days": len(rows),
        "mdd_pct": round(mdd * 100, 2),
        "ann_ret_pct": round(ann * 100, 2) if ann is not None else None,
        "ann_vol_pct": round(vol * 100, 2) if vol is not None else None,
        "latest_positions": latest_pos,
        "note": "影子盘模拟（T+1 收盘调仓、成本 30bp×换手）；未含冲击成本/停牌；仅供研究",
    }
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "shadow_selection.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (SHADOW / "shadow_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--date", default="")
    ap.add_argument("--target-vol", type=float, default=0.15)
    args = ap.parse_args(argv)
    SHADOW.mkdir(parents=True, exist_ok=True)
    dates = trade_dates(args.backfill + 2) if args.backfill else [args.date or selection_engine.latest_trade_date()]
    ledger_path = SHADOW / "ledger.jsonl"

    if args.backfill:
        start_dates = trade_dates(args.backfill + 2)
        prev = None
        ledger = []
        for i in range(len(start_dates) - 2):
            d, nd = start_dates[i], start_dates[i + 1]
            st = step(prev, d, nd)
            prev = {"weights": st["weights"]}
            ledger.append({"date": nd, "ret": round(st["gross"], 6), "net": round(st["net"], 6),
                           "turnover": round(st["turnover"], 4), "n_positions": len(st["weights"])})
            (SHADOW / f"positions_{nd}.json").write_text(json.dumps(st["positions"], ensure_ascii=False, indent=1), encoding="utf-8")
        ledger_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ledger) + "\n", encoding="utf-8")
        payload = write_outputs(ledger, prev)
        print(json.dumps({"days": payload["days"], "nav": payload["nav"], "mdd_pct": payload["mdd_pct"],
                          "ann_ret_pct": payload["ann_ret_pct"], "ann_vol_pct": payload["ann_vol_pct"]}, ensure_ascii=False))
        return 0

    date = dates[0]
    pos = portfolio_build.build(date, args.target_vol)
    (SHADOW / f"positions_{date}.json").write_text(json.dumps(pos, ensure_ascii=False, indent=1), encoding="utf-8")
    ledger = []
    if ledger_path.exists():
        ledger = [json.loads(x) for x in ledger_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    ledger.append({"date": date, "ret": 0.0, "net": -COST * 1.0, "turnover": 1.0, "n_positions": pos["n"]})
    ledger_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ledger) + "\n", encoding="utf-8")
    payload = write_outputs(ledger, pos)
    print(json.dumps({"date": date, "positions": pos["n"], "cash": pos["cash"], "nav": payload["nav"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
