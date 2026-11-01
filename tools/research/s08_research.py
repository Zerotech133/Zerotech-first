# -*- coding: utf-8 -*-
"""S08（mom_120 长反转多头）改进研究：频率×持仓数×权重×中性化×行业上限。

原则：不挑单一最优格，看**维度边际效应**（同维度各格平均）+ 年度稳健性；防曲线拟合。
候选改进：
  rebalance: M=月度 / Q=季度
  K: 20 / 50 / 100
  weight: eq=等权 / score=按分数排名加权
  neutral: raw=原始分数 / ind=行业中性化（行业内 z）
  indcap: none / 30%（单行业权重≤30%）
成本 0.15% 单边×换手；宇宙=非ST/上市≥250日/价≥2（与 S08 同构）。
输出：research/reports/s08_research_<tag>.{json,md}
"""
from __future__ import annotations

import glob
import itertools
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
COST = 0.0015


def build(dts: list[str]):
    a_rows, m_rows = [], []
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
        a_rows.append(d[["ts_code", "trade_date", "adj"]])
        m_rows.append(d[["ts_code", "trade_date", "amount"]])
        if len(a_rows) >= 300:
            ca, cm = pd.concat(a_rows, ignore_index=True), pd.concat(m_rows, ignore_index=True)
            a_rows, m_rows = [], []
            adj = ca.pivot_table(index="trade_date", columns="ts_code", values="adj")
            amt = cm.pivot_table(index="trade_date", columns="ts_code", values="amount")
            if "adj_acc" not in locals():
                adj_acc = adj
            else:
                adj_acc = pd.concat([adj_acc, adj], axis=0)
            if "amt_acc" not in locals():
                amt_acc = amt
            else:
                amt_acc = pd.concat([amt_acc, amt], axis=0)
    if a_rows:
        ca, cm = pd.concat(a_rows, ignore_index=True), pd.concat(m_rows, ignore_index=True)
        adj = ca.pivot_table(index="trade_date", columns="ts_code", values="adj")
        amt = cm.pivot_table(index="trade_date", columns="ts_code", values="amount")
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
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-150:]
    dts = pre + [d for d in ds if d >= "20190101"]
    adj, amt = build(dts)
    dates = adj.index.tolist()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    def listed_at(dt_):
        return listed_days(sb, dt_)
    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]
    # 调仓日集合
    def rebal_dates(freq: str) -> list[str]:
        if freq == "M":
            return mends
        q = []
        for i, m in enumerate(mends):
            if pd.Timestamp(m).month in (3, 6, 9, 12) or i == len(mends) - 1:
                q.append(m)
        return q
    REB = {f: rebal_dates(f) for f in ("M", "Q")}

    results = []
    for freq, K, wmode, neutral, indcap in itertools.product(
            ["M", "Q"], [20, 50, 100], ["eq", "score"], ["raw", "ind"], ["none", "30"]):
        reb = REB[freq]
        prev = set()
        rets, mrets = [], []
        for j in range(len(reb) - 1):
            dt, nxt = reb[j], reb[j + 1]
            if dt not in adj.index or nxt not in adj.index:
                continue
            hist = adj.loc[:dt]
            px = hist.iloc[-1]
            ret120 = px / hist.iloc[-121] - 1
            uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                  (listed_at(dt).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
            cc = px.index[uni.fillna(False)]
            score = (-ret120).reindex(cc).dropna()
            if neutral == "ind":
                g = score.groupby(industry.reindex(score.index))
                score = (score - g.transform("mean")) / g.transform("std").replace(0, np.nan)
                score = score.dropna()
            top = score.sort_values(ascending=False)
            if indcap == "30":
                picked, cnt = [], {}
                for code in top.index:
                    ind = industry.get(code, "未知")
                    cap = max(1, int(K * 0.30))
                    if cnt.get(ind, 0) >= cap:
                        continue
                    picked.append(code); cnt[ind] = cnt.get(ind, 0) + 1
                    if len(picked) >= K:
                        break
                picks = pd.Index(picked)
            else:
                picks = top.head(K).index
            if wmode == "eq":
                w = np.ones(len(picks)) / len(picks)
            else:
                r = np.arange(len(picks), 0, -1, dtype=float)
                w = r / r.sum()
            fwd = adj.loc[nxt] / px - 1.0
            pr = float(np.nansum(w * fwd.reindex(picks).fillna(0).values))
            turnover = len(set(picks) - prev) / len(picks)
            pr -= turnover * COST * 2
            rets.append(pr)
            mrets.append(float(fwd.reindex(cc).mean()))
            prev = set(picks)
        if len(rets) < 12:
            continue
        rets = np.array(rets); mrets = np.array(mrets)
        per_year = 12 if freq == "M" else 4
        nav = np.cumprod(1 + rets)
        mdd = float((nav / np.maximum.accumulate(nav) - 1).min())
        ex = rets - mrets
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0.0
        results.append({"freq": freq, "K": K, "weight": wmode, "neutral": neutral, "indcap": indcap,
                        "cagr_pct": round(float(nav[-1] ** (per_year / len(rets)) - 1) * 100, 2),
                        "mdd_pct": round(mdd * 100, 2),
                        "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(t, 2),
                        "n": len(rets)})
    # 维度边际（均值）
    dims = {}
    for dim in ("freq", "K", "weight", "neutral", "indcap"):
        vals = {}
        for r in results:
            vals.setdefault(str(r[dim]), []).append(r["cagr_pct"])
        dims[dim] = {k: round(float(np.mean(v)), 2) for k, v in sorted(vals.items())}
    results.sort(key=lambda x: -x["cagr_pct"])
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{dates[0]} .. {dates[-1]}", "combos": len(results),
                    "note": "S08 改进研究：看维度边际而非单格最优；成本0.15%单边×换手；研究输出"},
           "marginal": dims, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"s08_research_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# S08（mom_120 多头）改进研究 · {rep['meta']['window']}", "",
             "> 组合数 %d；看维度边际（同维度各格平均 CAGR），不做单格挑参。" % len(results), "",
             "## 维度边际（平均 CAGR%，越高越好）", ""]
    for dim, vals in dims.items():
        lines.append(f"- **{dim}**：" + " / ".join(f"{k}={v}" for k, v in vals.items()))
    lines += ["", "## Top 15 组合", "", "| 频率 | K | 权重 | 中性化 | 行业上限 | CAGR | MDD | 月均超额 | t |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results[:15]:
        lines.append(f"| {r['freq']} | {r['K']} | {r['weight']} | {r['neutral']} | {r['indcap']} | {r['cagr_pct']}% | {r['mdd_pct']}% | {r['excess_monthly_pct']}% | {r['excess_t']} |")
    lines += ["", "## 读法", "- 若某维度边际差异明显且跨格一致 → 该改进稳健；",
              "- 若只有个别格好 → 视为噪声/拟合，不采纳；", "- 改进后仍需四关 + 影子复核才可改 S08 配置。"]
    (OUT / f"s08_research_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"combos": len(results), "marginal": dims, "top5": [(r["freq"], r["K"], r["weight"], r["neutral"], r["indcap"], r["cagr_pct"]) for r in results[:5]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
