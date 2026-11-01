# -*- coding: utf-8 -*-
"""S08 长期反转影子：月度调仓 + 逐月跟踪（幂等）。

用法：
  python tools/research/s08_rebalance.py            # 用最新数据：月末则调仓，否则只 mark-to-market
  python tools/research/s08_rebalance.py --dry-run  # 只打印
产出：
  research/shadow/s08/positions_<signal>.json
  research/shadow/s08/ledger.jsonl   {date, from, ret, market, excess, n}
  portal/static/s08_shadow.json      （页面读取；保留回测指标）
边界：研究/影子，不实盘；容量/成本粗估。
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
SHADOW = REPO / "research" / "shadow" / "s08"
STATIC = REPO / "portal" / "static"
SID = "S08_mom120_long"
TOPN = 50
WINDOW = 120


def last_dates(n: int = 320) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs[-n:]]


def day_frame(dt: str, cols=("ts_code", "close", "amount")) -> pd.DataFrame:
    d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=list(cols)).to_pandas()
    af = DATA / "adj_factor" / f"d_{dt}.parquet"
    if af.exists():
        a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
        d = d.merge(a, on="ts_code", how="left")
        d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
    else:
        d["adj"] = d["close"]
    return d.set_index("ts_code")


def is_month_last(dt: str, dts: list[str]) -> bool:
    later = [x for x in dts if x > dt and x[:6] == dt[:6]]
    return len(later) == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    dts = last_dates(320)
    latest = dts[-1]
    sig_pos = sorted(SHADOW.glob("positions_*.json"))
    prev_pos = json.loads(sig_pos[-1].read_text(encoding="utf-8")) if sig_pos else None
    prev_sig = prev_pos.get("signal_date") if prev_pos else None

    # 当前持仓 mark-to-market（若有）
    cur_ret = None
    if prev_pos and prev_sig and prev_sig in dts:
        p0 = day_frame(prev_sig)["adj"]
        p1 = day_frame(latest)["adj"]
        holds = prev_pos["holdings"]
        rr = []
        for h in holds:
            c = h["ts_code"]
            if c in p0.index and c in p1.index and p0[c] and p1[c]:
                rr.append(p1[c] / p0[c] - 1.0)
        cur_ret = float(np.mean(rr)) if rr else None
        uni = day_frame(latest)["adj"]
        mkt = float((uni / p0.reindex(uni.index) - 1.0).mean())
    else:
        mkt = None

    rebalance = bool(prev_sig != latest and is_month_last(latest, dts))
    if args.dry_run:
        print(json.dumps({"latest": latest, "prev_sig": prev_sig, "rebalance": rebalance,
                          "cur_ret": cur_ret, "mkt": mkt}, ensure_ascii=False))
        return 0

    # 调仓：以 latest 为信号日重算 top50
    if rebalance:
        hist = pd.concat([day_frame(d, ("ts_code", "close"))["adj"].rename(d) for d in dts[-WINDOW - 1:]], axis=1).T
        px = hist.iloc[-1]
        ret120 = px / hist.iloc[-(WINDOW + 1)] - 1
        sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
        listed = (pd.Timestamp(latest) - pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")).dt.days
        uni = (~sb["name"].reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed.reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
        cc = px.index[uni.fillna(False)]
        score = (-ret120).reindex(cc).dropna()
        top = score.sort_values(ascending=False).head(TOPN)
        w = 1.0 / len(top)
        amt = day_frame(latest)["amount"].reindex(top.index)
        cap5 = float(amt.sum() * 1000.0 * 0.05 / 1e8) if amt.notna().any() else np.nan
        pos = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "strategy": SID,
               "name": "S08 长期反转（-120日收益）多头 top50", "signal_date": latest, "n": int(len(top)),
               "weight_per": round(w, 4), "capacity5_yi": round(cap5, 2),
               "holdings": [{"ts_code": c, "name": sb["name"].get(c), "score": round(float(v), 4),
                             "w": round(w, 4), "close": round(float(px.get(c)), 2)} for c, v in top.items()]}
        (SHADOW / f"positions_{latest}.json").write_text(json.dumps(pos, ensure_ascii=False, indent=1), encoding="utf-8")
        prev_pos, prev_sig = pos, latest
        cur_ret = 0.0
        print("rebalanced:", latest, "n=", len(top), "cap5=", round(cap5, 2), flush=True)

    # 账本
    ledger = []
    lf = SHADOW / "ledger.jsonl"
    if lf.exists():
        ledger = [json.loads(x) for x in lf.read_text(encoding="utf-8").splitlines() if x.strip()]
    last = ledger[-1]["date"] if ledger else None
    if last != latest and cur_ret is not None:
        row = {"date": latest, "from": prev_sig, "ret": round(cur_ret, 6),
               "market": round(float(mkt or 0.0), 6),
               "excess": round(cur_ret - float(mkt or 0.0), 6), "n": len(prev_pos["holdings"])}
        ledger.append(row)
        with lf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("ledger:", row, flush=True)

    # 静态输出
    static = STATIC / "s08_shadow.json"
    pub = json.loads(static.read_text(encoding="utf-8")) if static.exists() else {}
    pub.update(prev_pos)
    pub["ledger"] = ledger[-24:]
    pub["tracking"] = {"since": prev_sig, "cur_ret_pct": round(float(cur_ret or 0) * 100, 2),
                       "market_pct": round(float(mkt or 0) * 100, 2),
                       "excess_pct": round(float((cur_ret or 0) - (mkt or 0)) * 100, 2)}
    pub["next_rebalance"] = "下一个月末（每月 1 日 08:40 检查）"
    pub["status"] = "shadow_observing_weak"
    pub["disclaimer"] = "研究/影子，不构成投资建议"
    static.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"latest": latest, "signal": prev_sig, "tracking": pub["tracking"], "ledger_n": len(ledger)},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
