# -*- coding: utf-8 -*-
"""S06 反转组合影子候选：mom_20 + mom_120 双反转组合（top20 等权，月频调仓）。

依据：mining v2（93 月）+ 四关（mom_20 净多空 14.3%、mom_120 12.5%，均 PASS）。
本脚本：
  1) 组合回测：z(-ret20)+z(-ret120)，top20 等权，月频，成本 0.15% 单边×换手；
  2) 与市场等权 / 单因子 top20 对比；年度收益、CAGR、波动、MDD；
  3) 组合四关复核（G1-G4 同 four_gate 口径）；
  4) 生成 2026-10-30 初始影子持仓（research/shadow/s06/ + portal/static/s06_shadow.json）。

边界：研究/影子候选，不实盘；成本/容量粗估；current stock_basic 偏差。
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from pit_utils import listed_days  # noqa: E402

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
SHADOW = REPO / "research" / "shadow" / "s06"
STATIC = REPO / "portal" / "static"
COST = 0.0015          # 单边
TOPN = 20


def window_dates(start: str, buffer_days: int = 150) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    s = pd.Series(ds)
    return s[s < start].tail(buffer_days).tolist() + s[s >= start].tolist()


def build_panel(dts: list[str]):
    parts = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=["ts_code", "close", "amount"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        parts.append(d[["ts_code", "trade_date", "adj", "amount"]])
        if len(parts) >= 300:
            chunk = pd.concat(parts, ignore_index=True)
            parts = []
            adj = chunk.pivot_table(index="trade_date", columns="ts_code", values="adj")
            amt = chunk.pivot_table(index="trade_date", columns="ts_code", values="amount")
            if "adj_acc" not in locals():
                adj_acc = adj
            else:
                adj_acc = pd.concat([adj_acc, adj], axis=0)
            if "amt_acc" not in locals():
                amt_acc = amt
            else:
                amt_acc = pd.concat([amt_acc, amt], axis=0)
    if parts:
        chunk = pd.concat(parts, ignore_index=True)
        adj = chunk.pivot_table(index="trade_date", columns="ts_code", values="adj")
        amt = chunk.pivot_table(index="trade_date", columns="ts_code", values="amount")
        if "adj_acc" not in locals():
            adj_acc = adj
        else:
            adj_acc = pd.concat([adj_acc, adj], axis=0)
        if "amt_acc" not in locals():
            amt_acc = amt
        else:
            amt_acc = pd.concat([amt_acc, amt], axis=0)
    adj_acc.index = [str(x) for x in adj_acc.index]
    amt_acc.index = [str(x) for x in amt_acc.index]
    return adj_acc.sort_index(), amt_acc.sort_index()


def main() -> int:
    dts = window_dates("20190101")
    print("trade days:", len(dts), flush=True)
    adj, amt = build_panel(dts)
    print("panels:", adj.shape, amt.shape, flush=True)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    def listed_at(dt_):
        return listed_days(sb, dt_)
    s = pd.Series(adj.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]
    mends = [m for m in mends if m >= "20190101"]

    rows = []
    prev = set()
    last_codes = []
    last_score = {}
    for i, dt in enumerate(mends[:-1]):
        nxt = mends[i + 1]
        if dt not in adj.index or nxt not in adj.index:
            continue
        hist = adj.loc[:dt]
        px = hist.iloc[-1]
        fwd = adj.loc[nxt] / px - 1.0
        uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed_at(dt).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
        cc = px.index[uni.fillna(False)]
        if len(cc) < 200:
            continue
        r20 = (px / hist.iloc[-21] - 1).reindex(cc)
        r120 = (px / hist.iloc[-121] - 1).reindex(cc)
        def z(x):
            x = x.astype(float)
            return (x - x.mean()) / x.std() if x.std() > 0 else x * 0
        score = (z(-r20) + z(-r120)).dropna()
        if len(score) < TOPN:
            continue
        top = score.sort_values(ascending=False).head(TOPN)
        r_top = float(fwd.reindex(top.index).mean())
        r_mkt = float(fwd.reindex(cc).mean())
        turnover = len(set(top.index) - prev) / len(top)
        cost = turnover * COST * 2
        mkt60 = float((hist.iloc[-1] / hist.iloc[-61] - 1).mean()) if len(hist) > 61 else 0.0
        # 容量
        a = amt.loc[dt].reindex(top.index)
        cap = float(a.sum() * 1000.0 * 0.05 / 1e8) if a.notna().any() else np.nan
        # IC（组合分数 vs 前向收益）
        d = pd.DataFrame({"x": score, "y": fwd.reindex(score.index)}).dropna()
        ic = float(d["x"].corr(d["y"], method="spearman")) if len(d) > 100 else np.nan
        rows.append({"date": nxt, "ret": r_top - cost, "ret_gross": r_top, "market": r_mkt,
                     "excess": r_top - cost - r_mkt, "turnover": turnover, "cap_yi": cap,
                     "ic": ic, "regime": "up" if mkt60 > 0 else "down", "n": len(top)})
        prev = set(top.index)
        last_codes, last_score = list(top.index), top.to_dict()

    df = pd.DataFrame(rows)
    nav = (1 + df["ret"]).cumprod()
    mkt_nav = (1 + df["market"]).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    years = df["date"].str[:4]
    yearly = df.groupby(years).apply(lambda g: float((1 + g["ret"]).prod() - 1), include_groups=False)
    yearly_mkt = df.groupby(years).apply(lambda g: float((1 + g["market"]).prod() - 1), include_groups=False)
    ic = df["ic"].dropna().to_numpy()
    t_ic = float(ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))) if len(ic) > 2 else 0.0
    ann = float(nav.iloc[-1] ** (12 / len(df)) - 1)
    vol = float(df["ret"].std() * np.sqrt(12))
    # 四关
    g1 = bool((yearly > 0).mean() >= 0.70 and t_ic >= 2.0)
    g2 = bool(ann > 0 and float(df["excess"].mean() * 12) > 0)
    cap_med = float(df["cap_yi"].median()) if df["cap_yi"].notna().any() else 0.0
    g3 = bool(cap_med >= 0.5)
    up = df[df["regime"] == "up"]["excess"]
    dn = df[df["regime"] == "down"]["excess"]
    g4 = bool(len(up) > 6 and len(dn) > 6 and up.mean() > 0 and dn.mean() > 0)
    report = {
        "meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "window": f"{mends[0]} .. {mends[-2]}", "months": len(df),
                 "rule": "z(-ret20)+z(-ret120) 等权组合；top20 等权；月频；成本单边0.15%×换手",
                 "universe": "非ST/上市≥250日/价≥2（未加流动性过滤）；current stock_basic 偏差",
                 "note": "S06 影子候选回测（研究）"},
        "metrics": {"nav": round(float(nav.iloc[-1]), 4), "cagr_pct": round(ann * 100, 2),
                    "vol_pct": round(vol * 100, 2), "mdd_pct": round(mdd * 100, 2),
                    "market_cagr_pct": round(float(mkt_nav.iloc[-1] ** (12 / len(df)) - 1) * 100, 2),
                    "avg_turnover": round(float(df["turnover"].mean()), 3),
                    "ic_mean": round(float(ic.mean()), 4), "ic_t": round(t_ic, 2),
                    "capacity_median_yi": round(cap_med, 2)},
        "gates": {"G1": g1, "G2": g2, "G3": g3, "G4": g4},
        "yearly": {str(y): {"s06": round(float(v) * 100, 1), "market": round(float(yearly_mkt.loc[y]) * 100, 1)}
                   for y, v in yearly.items()},
        "regime": {"up_mean_excess_pct": round(float(up.mean()) * 100, 3) if len(up) else None,
                   "down_mean_excess_pct": round(float(dn.mean()) * 100, 3) if len(dn) else None},
        "monthly": df.round(6).to_dict(orient="records"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d")
    (OUT / f"s06_backtest_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # 初始影子持仓（最新月末）
    dt = mends[-2]
    hist = adj.loc[:dt]
    px = hist.iloc[-1]
    uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
          (listed_at(dt).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
    cc = px.index[uni.fillna(False)]
    r20 = (px / hist.iloc[-21] - 1).reindex(cc)
    r120 = (px / hist.iloc[-121] - 1).reindex(cc)
    zf = lambda x: (x - x.mean()) / x.std() if x.std() > 0 else x * 0
    score = (zf(-r20) + zf(-r120)).dropna()
    picks = score.sort_values(ascending=False).head(TOPN)
    w = 1.0 / len(picks)
    pos = {"generated_at": report["meta"]["generated_at"], "strategy": "S06_reversal_combo_mom20_120",
           "signal_date": dt, "n": int(len(picks)), "weight_per": round(w, 4),
           "rule": report["meta"]["rule"],
           "holdings": [{"ts_code": c, "name": names.get(c), "score": round(float(s), 4),
                         "w": round(w, 4), "close": round(float(px.get(c)), 2)} for c, s in picks.items()]}
    SHADOW.mkdir(parents=True, exist_ok=True)
    (SHADOW / f"positions_{dt}.json").write_text(json.dumps(pos, ensure_ascii=False, indent=1), encoding="utf-8")
    # 门户静态
    STATIC.mkdir(parents=True, exist_ok=True)
    pub = {**pos, "backtest": {"cagr_pct": report["metrics"]["cagr_pct"], "mdd_pct": report["metrics"]["mdd_pct"],
                                "mkt_cagr_pct": report["metrics"]["market_cagr_pct"], "ic_t": report["metrics"]["ic_t"],
                                "capacity_median_yi": report["metrics"]["capacity_median_yi"]},
           "gates": report["gates"], "yearly": report["yearly"],
           "next_rebalance": "2026-11-30（月末）", "status": "shadow_candidate", "disclaimer": "研究候选，不构成投资建议"}
    (STATIC / "s06_shadow.json").write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"cagr": report["metrics"]["cagr_pct"], "mdd": report["metrics"]["mdd_pct"],
                      "mkt_cagr": report["metrics"]["market_cagr_pct"], "gates": report["gates"],
                      "picks": [(h["ts_code"], h["name"]) for h in pos["holdings"][:8]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
