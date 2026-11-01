# -*- coding: utf-8 -*-
"""S07/S08 单因子小仓位影子：mom_20（S07）/ mom_120（S08）多头 top50 等权。

依据：H38/H39 四关 PASS（LS），但 long-only 薄利（+1.3%/+2.2%/年）→ 先影子验证成本/容量。
构造（影子同构回测）：每月末取因子分数 top50 等权（多头），月频调仓；
  成本=换手×0.15%单边×2；对比等权市场；容量=Σ持仓成交额×参与率。
输出：
  research/reports/s07s08_backtest_<date>.json / .md
  research/shadow/s07/positions_<dt>.json、s08/...
  portal/static/s07_shadow.json、s08_shadow.json
边界：研究/影子候选（小仓位），不实盘；current stock_basic 偏差；容量粗估。
"""
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
SHADOW = REPO / "research" / "shadow"
STATIC = REPO / "portal" / "static"
COST = 0.0015
TOPN = 50
STRATS = {
    "S07_mom20_long": {"name": "S07 短期反转（-20日收益）多头", "window": 20, "dir": -1},
    "S08_mom120_long": {"name": "S08 长期反转（-120日收益）多头", "window": 120, "dir": -1},
}


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
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    list_dates_ = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    def listed_at(dt_):
        """PIT：截至信号日的上市天数（2026-11-01 修复：原用最新日→次新股前视）"""
        return (pd.Timestamp(dt_) - list_dates_).dt.days
    s = pd.Series(adj.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]
    mends = [m for m in mends if m >= "20190101"]

    # 回测
    results = {sid: [] for sid in STRATS}
    prev = {sid: set() for sid in STRATS}
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
        if len(cc) < 300:
            continue
        mkt = float(fwd.reindex(cc).mean())
        for sid, cfg in STRATS.items():
            w = cfg["window"]
            ret = px / hist.iloc[-(w + 1)] - 1 if len(hist) > w else None
            if ret is None:
                continue
            score = (cfg["dir"] * ret).reindex(cc).dropna()
            if len(score) < TOPN:
                continue
            top = score.sort_values(ascending=False).head(TOPN)
            gross = float(fwd.reindex(top.index).mean())
            turnover = len(set(top.index) - prev[sid]) / len(top)
            net = gross - turnover * COST * 2
            a = amt.loc[dt].reindex(top.index)
            cap5 = float(a.sum() * 1000.0 * 0.05 / 1e8) if a.notna().any() else np.nan
            results[sid].append({"date": nxt, "ret": net, "gross": gross, "market": mkt,
                                 "excess": net - mkt, "turnover": turnover, "cap5_yi": cap5})
            prev[sid] = set(top.index)

    report = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "rule": "月频 top50 等权（多头）；成本 0.15%单边×换手；宇宙=非ST/上市≥250日/价≥2",
                        "window": f"{mends[0]} .. {mends[-2]}", "note": "S07/S08 单因子小仓位影子回测（研究）"},
              "strategies": {}}
    for sid, cfg in STRATS.items():
        df = pd.DataFrame(results[sid])
        if df.empty:
            continue
        nav = (1 + df["ret"]).cumprod()
        mdd = float((nav / nav.cummax() - 1).min())
        years = df["date"].str[:4]
        yearly = df.groupby(years)["ret"].apply(lambda g: float((1 + g).prod() - 1))
        ex = df["excess"]
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0.0
        report["strategies"][sid] = {
            "name": cfg["name"],
            "metrics": {"cagr_pct": round(float(nav.iloc[-1] ** (12 / len(df)) - 1) * 100, 2),
                        "mdd_pct": round(mdd * 100, 2),
                        "vol_pct": round(float(df["ret"].std() * np.sqrt(12)) * 100, 2),
                        "excess_mean_monthly_pct": round(float(ex.mean()) * 100, 3),
                        "excess_t": round(t, 2),
                        "avg_turnover": round(float(df["turnover"].mean()), 3),
                        "capacity5_median_yi": round(float(df["cap5_yi"].median()), 2),
                        "months": len(df)},
            "yearly": {str(y): round(float(v) * 100, 1) for y, v in yearly.items()},
            "monthly": df.round(6).to_dict(orient="records"),
        }
    OUT.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d")
    (OUT / f"s07s08_backtest_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = ["# S07/S08 单因子小仓位影子 · 同构回测", "",
             f"- 窗口 {report['meta']['window']}；月频 top{TOPN} 等权；成本 0.15%单边×换手", "",
             "| 策略 | CAGR | MDD | 年化波动 | 月均超额 | 超额 t | 月换手 | 容量中位(5%) |", "|---|---|---|---|---|---|---|---|"]
    for sid, v in report["strategies"].items():
        m = v["metrics"]
        lines.append(f"| {sid} | {m['cagr_pct']}% | {m['mdd_pct']}% | {m['vol_pct']}% | {m['excess_mean_monthly_pct']}% | {m['excess_t']} | {m['avg_turnover']} | {m['capacity5_median_yi']} 亿 |")
    lines += ["", "## 年度收益（%）", "", "| 年 | " + " | ".join(report["strategies"].keys()) + " |", "|---|" + "---|" * len(report["strategies"])]
    years = sorted({y for v in report["strategies"].values() for y in v["yearly"]})
    for y in years:
        lines.append(f"| {y} | " + " | ".join(str(v["yearly"].get(y, "")) for v in report["strategies"].values()) + " |")
    lines += ["", "> 单因子薄利：月均超额与 t 值决定是否值得继续影子；容量为 5% 参与率粗估。"]
    (OUT / f"s07s08_backtest_{tag}.md").write_text("\n".join(lines), encoding="utf-8")

    # 初始影子持仓（最新交易日 2026-10-30 信号，持有至 11 月末）
    sig = mends[-1]
    hist = adj.loc[:sig]
    px = hist.iloc[-1]
    uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
          (listed_at(sig).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
    cc = px.index[uni.fillna(False)]
    STATIC.mkdir(parents=True, exist_ok=True)
    for sid, cfg in STRATS.items():
        w = cfg["window"]
        ret = px / hist.iloc[-(w + 1)] - 1
        score = (cfg["dir"] * ret).reindex(cc).dropna()
        top = score.sort_values(ascending=False).head(TOPN)
        wt = 1.0 / len(top)
        pos = {"generated_at": report["meta"]["generated_at"], "strategy": sid, "name": cfg["name"],
               "signal_date": sig, "n": int(len(top)), "weight_per": round(wt, 4),
               "rule": report["meta"]["rule"], "next_rebalance": "2026-11-30（月末）",
               "holdings": [{"ts_code": c, "name": names.get(c), "score": round(float(v), 4),
                             "w": round(wt, 4), "close": round(float(px.get(c)), 2)} for c, v in top.items()]}
        d = SHADOW / sid.lower()[:4].replace("s0", "s0") if False else SHADOW / sid.split("_")[0].lower()
        d = SHADOW / sid.split("_")[0].lower()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"positions_{sig}.json").write_text(json.dumps(pos, ensure_ascii=False, indent=1), encoding="utf-8")
        pub = {**pos, "backtest": report["strategies"][sid]["metrics"],
               "yearly": report["strategies"][sid]["yearly"],
               "status": "shadow_small_position", "disclaimer": "研究/影子，不构成投资建议"}
        (STATIC / f"{sid.split('_')[0].lower()}_shadow.json").write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
        print(sid, "picks", len(top), "cap5", report["strategies"][sid]["metrics"]["capacity5_median_yi"], flush=True)
    print(json.dumps({k: v["metrics"] for k, v in report["strategies"].items()}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
