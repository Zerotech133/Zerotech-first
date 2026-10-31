# -*- coding: utf-8 -*-
"""组合构建（P2）：选股清单 → 组合权重（波动率目标 + 单票/行业上限 + 成本估算）。

方法：
  1) 个股 20 日已实现波动率（复权价日收益 std × √252）；
  2) 逆波动率加权（risk parity-lite）；
  3) 单票上限 5%（超限迭代再分配）；行业上限 30%；总仓位由目标年化波动（默认 15%）缩放，上限 1.0（不加杠杆）；
  4) 输出权重、行业暴露、组合年化波动、现金比例、换手估算（与上一期权重对比时给）。

用法：python tools/research/portfolio_build.py --date 20261030 --target-vol 0.15
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
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine  # noqa: E402


def trade_dates(n: int = 30) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs[-n:]]


def adj_close_panel(dates: list[str], codes: list[str]) -> pd.DataFrame:
    rows = {}
    for dt in dates:
        cf = DATA / "daily" / f"d_{dt}.parquet"
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if not cf.exists():
            continue
        c = pq.read_table(cf, columns=["ts_code", "close"]).to_pandas()
        a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas() if af.exists() else pd.DataFrame({"ts_code": [], "adj_factor": []})
        m = c.merge(a, on="ts_code", how="left")
        m["adj"] = m["close"] * m["adj_factor"].fillna(1.0)
        rows[dt] = m.set_index("ts_code")["adj"]
    # 行=日期、列=股票（pct_change 必须沿时间轴）
    px = pd.concat(rows, axis=1).T
    px.index = [str(x) for x in px.index]
    return px.reindex(columns=codes)


def build(date: str, target_vol: float = 0.15, top: int = 20, single_cap: float = 0.05, ind_cap: float = 0.30) -> dict:
    sel = selection_engine.build(date, top)
    picks = sel["picks"]
    codes = [p["ts_code"] for p in picks]
    dates = trade_dates(25)
    px = adj_close_panel(dates, codes)
    rets = px.pct_change().iloc[1:]
    vol = rets.std(ddof=1) * np.sqrt(252)  # Series: index=股票
    vol = vol.replace(0, np.nan)
    inv = 1.0 / vol
    w = inv / inv.sum()
    # 单票上限（迭代再分配）
    for _ in range(50):
        over = w[w > single_cap]
        if over.empty:
            break
        excess = (w[w > single_cap] - single_cap).sum()
        w[w > single_cap] = single_cap
        rest = w[w < single_cap]
        if rest.empty:
            break
        w[w < single_cap] = rest + excess * rest / rest.sum()
    # 行业上限（迭代削减到行业上限，超出部分按比例分给其他行业）
    meta = {p["ts_code"]: p for p in picks}
    for _ in range(20):
        tmp = w.reset_index()
        tmp.columns = ["ts_code", "w"]
        tmp["ind"] = tmp["ts_code"].map(lambda c: meta[c].get("industry") or "未知")
        ind_w = tmp.groupby("ind")["w"].sum()
        bad = ind_w[ind_w > ind_cap]
        if bad.empty:
            break
        for ind in bad.index:
            idx = tmp[tmp["ind"] == ind].index
            scale = ind_cap / tmp.loc[idx, "w"].sum()
            w.iloc[idx] *= scale
        w = w / w.sum()
    # 波动率目标缩放：组合年化波动估算
    cov = rets[codes].cov() * 252
    port_vol = float(np.sqrt(float(w.values @ cov.values @ w.values)))
    scale = min(1.0, target_vol / port_vol) if port_vol > 0 else 1.0
    w = w * scale
    holdings = []
    for c in codes:
        p = meta[c]
        holdings.append({"ts_code": c, "name": p["name"], "industry": p.get("industry"),
                         "weight": round(float(w[c]), 4), "vol_ann": round(float(vol[c]) * 100, 1) if pd.notna(vol[c]) else None,
                         "score": p["score"]})
    holdings.sort(key=lambda x: -x["weight"])
    ind_exp = {}
    for h in holdings:
        ind_exp[h["industry"] or "未知"] = round(ind_exp.get(h["industry"] or "未知", 0) + h["weight"], 4)
    return {
        "date": date, "method": "inverse-vol + 5% single cap + 30% industry cap + vol target",
        "target_vol_ann": target_vol, "estimated_vol_ann": round(port_vol * 100, 2),
        "scale": round(scale, 4), "cash": round(1 - sum(h["weight"] for h in holdings), 4),
        "holdings": holdings, "industry_exposure": ind_exp,
        "n": len(holdings), "selection": {k: sel[k] for k in ("candidates", "filters", "weights")},
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "组合权重为研究输出；S02 排序未验证；实盘前须过影子盘+审批",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--target-vol", type=float, default=0.15)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)
    date = args.date or selection_engine.latest_trade_date()
    out = build(date, args.target_vol, args.top)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"portfolio_{date}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"date": date, "n": out["n"], "cash": out["cash"], "est_vol": out["estimated_vol_ann"],
                      "top_weights": [(h["ts_code"], h["weight"]) for h in out["holdings"][:5]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
