# -*- coding: utf-8 -*-
"""S11 mom_120 稳健影子网格：long-only 可交易性检验（PIT 修正版）。

因子：mom_120（-过去120日收益，反转方向）。
网格：K(20/50) × 权重(eq/score) × 中性化(raw/行业) × 频率(M/Q) × regime(none/down-only)
成本 0.15% 单边×换手；宇宙=非ST/按信号日上市≥250日/价≥2。
输出：research/reports/s11_mom120_robust_<tag>.{json,md}
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
from sepa_backtest import load  # noqa: E402

COST = 0.0015


def z(s: pd.Series) -> pd.Series:
    s = s.replace([np.inf, -np.inf], np.nan)
    mu, sd = s.mean(), s.std()
    return (s - mu) / sd if sd and sd > 0 else s * 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-bj", action="store_true", help="纳入北交所（默认排除）")
    args = ap.parse_args(argv)
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-300:]
    dts = pre + [d for d in ds if d >= "20190101"]
    P = load(dts)
    dates = P["a_close"].index.tolist()
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    list_dates = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    amt = {}
    s = pd.Series(dates)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6]) if g.iloc[-1] >= "20190101"]
    for dt in mends:
        f = DATA / "daily" / ("d_" + dt + ".parquet")
        if f.exists():
            amt[dt] = pq.read_table(f, columns=["ts_code", "amount"]).to_pandas().set_index("ts_code")["amount"]
    # 市场 regime：60 日等权收益
    mret = P["a_close"].pct_change().mean(axis=1)
    mkt = (1 + mret).cumprod()
    def regime(dt: str) -> str:
        i = dates.index(dt)
        if i < 61:
            return "up"
        return "down" if (mkt.iloc[i] / mkt.iloc[i - 60] - 1) < 0 else "up"

    results = []
    for K in (20, 50):
        for wmode in ("eq", "score"):
            for neutral in ("raw", "ind"):
                for freq in ("M", "Q"):
                    for reg in ("none", "down"):
                        ms = mends if freq == "M" else [m for m in mends if int(m[4:6]) in (3, 6, 9, 12)]
                        prev = set(); rets, mkts, caps = [], [], []
                        for j in range(len(ms) - 1):
                            dt, nxt = ms[j], ms[j + 1]
                            if dt not in P["a_close"].index or nxt not in P["a_close"].index:
                                continue
                            if reg == "down" and regime(dt) != "down":
                                rets.append(0.0); mkts.append(0.0); caps.append(np.nan)
                                continue
                            i = dates.index(dt)
                            c = P["a_close"].iloc[i]
                            uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                                  ((pd.Timestamp(dt) - list_dates).dt.days.reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
                            if not args.include_bj:
                                uni = uni & ~c.index.str.endswith(".BJ")
                            cc = c.index[uni.fillna(False)]
                            score = (-(c / P["a_close"].iloc[i - 120] - 1)).reindex(cc).dropna()
                            if neutral == "ind":
                                g = score.groupby(industry.reindex(score.index))
                                score = ((score - g.transform("mean")) / g.transform("std").replace(0, np.nan)).dropna()
                            picks = score.sort_values(ascending=False).head(K).index
                            if len(picks) < 5:
                                continue
                            if wmode == "eq":
                                w = np.ones(len(picks)) / len(picks)
                            else:
                                r = np.arange(len(picks), 0, -1, dtype=float); w = r / r.sum()
                            fwd = P["a_close"].iloc[dates.index(nxt)] / c - 1
                            pr = float(np.nansum(w * fwd.reindex(picks).fillna(0).values))
                            mkt_r = float(fwd.reindex(cc).mean())
                            turnover = len(set(picks) - prev) / len(picks)
                            pr -= turnover * COST * 2
                            rets.append(pr); mkts.append(mkt_r)
                            if dt in amt:
                                caps.append(float(np.nansum(amt[dt].reindex(picks).values)) * 1000 * 0.05 / 1e8)
                            else:
                                caps.append(np.nan)
                            prev = set(picks)
                        rets = np.array(rets); mkts = np.array(mkts)
                        nav = np.cumprod(1 + rets); yrs = len(rets) / (12 if freq == "M" else 4)
                        ex = rets - mkts
                        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0
                        results.append({"K": K, "weight": wmode, "neutral": neutral, "freq": freq, "regime": reg,
                                        "cagr_pct": round(float(nav[-1] ** (1 / yrs) - 1) * 100, 2),
                                        "mdd_pct": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 2),
                                        "excess_monthly_pct": round(float(ex.mean()) * 100, 3),
                                        "excess_t": round(t, 2),
                                        "capacity_median_yi": round(float(np.nanmedian(caps)), 2),
                                        "months": len(rets)})
    # 维度边际（excess_t 均值）
    marg = {}
    for dim in ("K", "weight", "neutral", "freq", "regime"):
        vals = {}
        for r in results:
            vals.setdefault(str(r[dim]), []).append(r["excess_t"])
        marg[dim] = {k: round(float(np.mean(v)), 2) for k, v in sorted(vals.items())}
    results.sort(key=lambda r: -r["excess_t"])
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}",
                    "note": "S11 mom_120 long-only 稳健网格（PIT）；成本0.15%单边×换手；regime=市场60日趋势；北交所=" + ("含" if args.include_bj else "排除")},
           "marginal": marg, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"s11_mom120_robust_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = [f"# S11 mom_120 long-only 稳健网格 · {rep['meta']['window']}", "",
         "> 32 组合：K/权重/中性化/频率/regime；看维度边际而非挑单格；成本 0.15% 单边×换手", ""]
    for dim, vals in marg.items():
        L.append(f"- **{dim}**：" + " / ".join(f"{k}={v}" for k, v in vals.items()))
    L += ["", "## Top 10（按 long-only 超额 t）", "",
          "| K | 权重 | 中性化 | 频率 | regime | CAGR | MDD | 月均超额 | t | 容量 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results[:10]:
        L.append(f"| {r['K']} | {r['weight']} | {r['neutral']} | {r['freq']} | {r['regime']} | {r['cagr_pct']}% | {r['mdd_pct']}% | {r['excess_monthly_pct']}% | {r['excess_t']} | {r['capacity_median_yi']}亿 |")
    best = results[0]
    L += ["", "## 结论", f"- 最佳 long-only 配置：K={best['K']}/{best['weight']}/{best['neutral']}/{best['freq']}/regime={best['regime']}："
          f"CAGR {best['cagr_pct']}%、MDD {best['mdd_pct']}%、月均超额 {best['excess_monthly_pct']}%、t={best['excess_t']}、容量 {best['capacity_median_yi']} 亿",
          "- 若最佳 t<1.5 且 CAGR 不稳定 → long-only 不可交易，mom_120 只保留 LS 研究价值，不启影子。"]
    (OUT / f"s11_mom120_robust_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"marginal": marg, "top3": [(r["K"], r["weight"], r["neutral"], r["freq"], r["regime"], r["cagr_pct"], r["excess_t"], r["capacity_median_yi"]) for r in results[:3]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
