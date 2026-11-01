# -*- coding: utf-8 -*-
"""S08-v2 验证：对「M/K20/score」与基线「M/K50/eq」做年度/regime/容量/四关对照。"""
from __future__ import annotations

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
from s08_research import build  # noqa: E402

COST = 0.0015
CONFIGS = {
    "S08_base": {"K": 50, "weight": "eq", "neutral": "raw"},
    "S08_v2_top": {"K": 20, "weight": "score", "neutral": "raw"},
    "S08_v2_ind": {"K": 20, "weight": "score", "neutral": "ind"},
}


def run(cfg: dict, adj, amt, dates, names, industry, listed, mends) -> dict:
    prev = set()
    rows = []
    for j in range(len(mends) - 1):
        dt, nxt = mends[j], mends[j + 1]
        if dt not in adj.index or nxt not in adj.index:
            continue
        hist = adj.loc[:dt]
        px = hist.iloc[-1]
        ret120 = px / hist.iloc[-121] - 1
        uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed_at(dt).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
        cc = px.index[uni.fillna(False)]
        score = (-ret120).reindex(cc).dropna()
        if cfg["neutral"] == "ind":
            g = score.groupby(industry.reindex(score.index))
            score = ((score - g.transform("mean")) / g.transform("std").replace(0, np.nan)).dropna()
        top = score.sort_values(ascending=False)
        picks = top.head(cfg["K"]).index
        if cfg["weight"] == "eq":
            w = np.ones(len(picks)) / len(picks)
        else:
            r = np.arange(len(picks), 0, -1, dtype=float)
            w = r / r.sum()
        fwd = adj.loc[nxt] / px - 1.0
        pr = float(np.nansum(w * fwd.reindex(picks).fillna(0).values))
        turnover = len(set(picks) - prev) / len(picks)
        pr -= turnover * COST * 2
        mkt = float(fwd.reindex(cc).mean())
        mkt60 = float((hist.iloc[-1] / hist.iloc[-61] - 1).mean()) if len(hist) > 61 else 0.0
        a = amt.loc[dt].reindex(picks)
        cap = float(a.sum() * 1000.0 * 0.05 / 1e8) if a.notna().any() else np.nan
        rows.append({"date": nxt, "ret": pr, "market": mkt, "excess": pr - mkt,
                     "turnover": turnover, "cap_yi": cap, "regime": "up" if mkt60 > 0 else "down"})
        prev = set(picks)
    df = pd.DataFrame(rows)
    nav = (1 + df["ret"]).cumprod()
    years = df["date"].str[:4]
    yearly = df.groupby(years)["ret"].apply(lambda g: float((1 + g).prod() - 1))
    ex = df["excess"]
    t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0.0
    up, dn = df[df["regime"] == "up"]["excess"], df[df["regime"] == "down"]["excess"]
    return {"cagr_pct": round(float(nav.iloc[-1] ** (12 / len(df)) - 1) * 100, 2),
            "mdd_pct": round(float((nav / nav.cummax() - 1).min()) * 100, 2),
            "vol_pct": round(float(df["ret"].std() * np.sqrt(12)) * 100, 2),
            "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(t, 2),
            "avg_turnover": round(float(df["turnover"].mean()), 3),
            "capacity_median_yi": round(float(df["cap_yi"].median()), 2),
            "up_excess_pct": round(float(up.mean()) * 100, 3), "down_excess_pct": round(float(dn.mean()) * 100, 3),
            "positive_years": f"{int((yearly > 0).sum())}/{len(yearly)}",
            "yearly": {str(y): round(float(v) * 100, 1) for y, v in yearly.items()},
            "gates": {"G1": bool((yearly > 0).mean() >= 0.70 and t >= 2.0),
                      "G2": bool(float(nav.iloc[-1] ** (12 / len(df)) - 1) > 0 and ex.mean() > 0),
                      "G3": bool(float(df["cap_yi"].median()) >= 0.5),
                      "G4": bool(len(up) > 6 and len(dn) > 6 and up.mean() > 0 and dn.mean() > 0)}}


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-150:]
    dts = pre + [d for d in ds if d >= "20190101"]
    adj, amt = build(dts)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    list_dates_ = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    def listed_at(dt_):
        return (pd.Timestamp(dt_) - list_dates_).dt.days
    s = pd.Series(adj.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]
    out = {}
    for name, cfg in CONFIGS.items():
        out[name] = run(cfg, adj, amt, adj.index.tolist(), names, industry, listed, mends)
        print(name, out[name]["cagr_pct"], out[name]["mdd_pct"], out[name]["excess_t"], out[name]["gates"], flush=True)
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "months": len(mends) - 1,
                    "note": "S08-v2 验证（研究）；成本0.15%单边×换手；容量=Σ成交额×5%"},
           "configs": out}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"s08_v2_verify_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# S08-v2 验证 · {rep['meta']['window']}", "", "| 配置 | CAGR | MDD | 波动 | 月均超额 | t | 换手 | 容量(亿) | 正收益年 | G1/G2/G3/G4 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in out.items():
        g = v["gates"]
        lines.append(f"| {name} | {v['cagr_pct']}% | {v['mdd_pct']}% | {v['vol_pct']}% | {v['excess_monthly_pct']}% | {v['excess_t']} | {v['avg_turnover']} | {v['capacity_median_yi']} | {v['positive_years']} | {'/'.join('✅' if g[k] else '❌' for k in ('G1','G2','G3','G4'))} |")
    lines += ["", "## 年度收益（%）", "", "| 年 | " + " | ".join(out) + " |", "|---|" + "---|" * len(out)]
    years = sorted({y for v in out.values() for y in v["yearly"]})
    for y in years:
        lines.append(f"| {y} | " + " | ".join(str(out[c]["yearly"].get(y, "")) for c in out) + " |")
    (OUT / f"s08_v2_verify_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
