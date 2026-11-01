# -*- coding: utf-8 -*-
"""Minervini Trend Template v1.1：股票池过滤 × 退出规则 × RS 口径 组合回测。

背景：外部回测（MarketInOut 14 池/TSX 25 年）指出「股票池/退出规则」比入场更重要。
本脚本在 A 股 2019-2026 上对照：
  股票池：U0=全A（非ST/上市≥250/价≥2）；U1=20日均额≥1亿；U2=20日均额≥5亿
  RS：R_approx = 全A横截面 ret12m+ret3m 百分位；R_252 = ret252 百分位（MarketInOut 式）
  退出：E_200m=月末收盘破SMA200；E_200d=日收盘破SMA200；E_8=入场价-8%；E_10=-10%
入场：月末 TT 8 条全过 → 按 RS 降序填 20 个仓位，每仓 5%（现金保留）；月末换仓/补仓；
      成本 0.15% 单边；TT 口径同 trend_template.py（C3 端点、C6 30%、C7 25%）。
输出：research/reports/trend_template_v11_<tag>.{json,md}
边界：研究回测，非实盘；幸存者/ST 偏差（current stock_basic）；成交额流动性为代理。
"""
from __future__ import annotations

import glob
import itertools
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
COST = 0.0015
SLOTS = 20
WEIGHT = 1.0 / SLOTS


def build(dts: list[str]):
    rows_adj, rows_amt = [], []
    for i, dt in enumerate(dts):
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=["ts_code", "close", "amount"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        rows_adj.append(d[["ts_code", "trade_date", "adj"]])
        rows_amt.append(d[["ts_code", "trade_date", "amount"]])
        if len(rows_adj) >= 300:
            ca = pd.concat(rows_adj, ignore_index=True)
            cm = pd.concat(rows_amt, ignore_index=True)
            rows_adj, rows_amt = [], []
            adj = ca.pivot_table(index="trade_date", columns="ts_code", values="adj")
            amt = cm.pivot_table(index="trade_date", columns="ts_code", values="amount")
            adj_acc = adj if "adj_acc" not in locals() else pd.concat([adj_acc, adj], axis=0)
            amt_acc = amt if "amt_acc" not in locals() else pd.concat([amt_acc, amt], axis=0)
    if rows_adj:
        ca = pd.concat(rows_adj, ignore_index=True)
        cm = pd.concat(rows_amt, ignore_index=True)
        adj = ca.pivot_table(index="trade_date", columns="ts_code", values="adj")
        amt = cm.pivot_table(index="trade_date", columns="ts_code", values="amount")
        adj_acc = adj if "adj_acc" not in locals() else pd.concat([adj_acc, adj], axis=0)
        amt_acc = amt if "amt_acc" not in locals() else pd.concat([amt_acc, amt], axis=0)
    adj_acc.index = [str(x) for x in adj_acc.index]
    amt_acc.index = [str(x) for x in amt_acc.index]
    return adj_acc.sort_index(), amt_acc.sort_index()


def month_end_positions(dates: list[str]) -> list[int]:
    s = pd.Series(dates)
    last = s.groupby(s.str[:6]).apply(lambda g: g.index[-1])
    return [int(i) for i in last.tolist()]


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    all_d = [Path(f).stem[2:] for f in fs]
    pre = [d for d in all_d if d < "20190101"][-260:]
    dts = pre + [d for d in all_d if d >= "20190101"]
    adj, amt = build(dts)
    dates = adj.index.tolist()
    print("panel:", adj.shape, dates[0], "..", dates[-1], flush=True)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    list_dates_ = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    def listed_at(dt_):
        """PIT：截至信号日的上市天数（2026-11-01 修复：原用最新日→次新股前视）"""
        return (pd.Timestamp(dt_) - list_dates_).dt.days
    me = month_end_positions(dates)

    def snapshots(i: int) -> dict:
        """月末 i 的 TT 候选与 RS。"""
        hist = adj.iloc[: i + 1]
        px = hist.iloc[-1]
        sma50 = hist.iloc[-50:].mean()
        sma150 = hist.iloc[-150:].mean()
        sma200 = hist.iloc[-200:].mean()
        sma200_prev = adj.iloc[: i + 1 - 21].iloc[-200:].mean() if i >= 220 else sma200
        y = hist.iloc[-250:]
        hi, lo = y.max(), y.min()
        r12 = px / hist.iloc[-251] - 1 if len(hist) > 251 else px * 0
        r3 = px / hist.iloc[-63] - 1 if len(hist) > 63 else px * 0
        rs_approx = (r12 + r3).rank(pct=True) * 98 + 1
        rs252 = (px / hist.iloc[-252] - 1 if len(hist) > 252 else px * 0).rank(pct=True) * 98 + 1
        base = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
               (listed_at(dates[i]).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
        cond = ((px > sma150) & (px > sma200) & (sma150 > sma200) & (sma200 > sma200_prev) &
                (sma50 > sma150) & (sma50 > sma200) & (px > sma50) &
                (px >= 1.30 * lo) & (px >= 0.75 * hi))
        liq = amt.iloc[: i + 1].iloc[-20:].mean()
        return {"px": px, "base": base, "cond": cond, "rs_approx": rs_approx, "rs252": rs252, "liq": liq}

    # 预先算月末快照（93 个）
    _, first_me = next(((i, p) for i, p in enumerate(me) if dates[p] >= "20190101"), (None, None))
    snaps = {}
    for i in me:
        if dates[i] >= "20190101":
            snaps[i] = snapshots(i)
    print("snapshots:", len(snaps), flush=True)

    results = []
    for uni_name, uni_min, rs_name, exit_name in itertools.product(
            ["U0_all", "U1_liq1e", "U2_liq5e"], [0, 1e8 * 1000, 5e8 * 1000],
            ["R_approx", "R_252"], ["E_200m", "E_200d", "E_8", "E_10"]):
        # amount 单位千元 → 阈值换算：1亿元 = 1e5 千元
        thr = {0: 0.0, int(1e8 * 1000): 1e5, int(5e8 * 1000): 5e5}[int(uni_min)]
        cash = 1.0
        pos = {}  # code -> {entry_px, shares}
        nav_hist, trades, exposure = [], 0, []
        for i, dt in enumerate(dates):
            px = adj.iloc[i]
            # 日退出规则（E_200d/E_8/E_10）
            if exit_name in ("E_200d", "E_8", "E_10"):
                for code in list(pos.keys()):
                    p = float(px.get(code, np.nan))
                    if np.isnan(p):
                        continue
                    if exit_name == "E_200d":
                        if i >= 200:
                            sma = float(adj.iloc[i - 199: i + 1][code].mean())
                            hit = p < sma
                        else:
                            hit = False
                    elif exit_name == "E_8":
                        hit = p <= pos[code]["entry_px"] * 0.92
                    else:
                        hit = p <= pos[code]["entry_px"] * 0.90
                    if hit:
                        cash += pos[code]["shares"] * p * (1 - COST)
                        del pos[code]
                        trades += 1
            if i in snaps:
                s = snaps[i]
                if exit_name == "E_200m":
                    for code in list(pos.keys()):
                        p = float(px.get(code, np.nan))
                        if not np.isnan(p):
                            sma = float(adj.iloc[max(0, i - 199): i + 1][code].mean())
                            if p < sma:
                                cash += pos[code]["shares"] * p * (1 - COST)
                                del pos[code]
                                trades += 1
                # 入场/补仓
                elig = s["cond"] & s["base"]
                if thr > 0:
                    elig &= (s["liq"] >= thr)
                rs_key = "rs_approx" if rs_name == "R_approx" else "rs252"
                cand = s[rs_key][elig].sort_values(ascending=False)
                free = SLOTS - len(pos)
                for code in [c for c in cand.index if c not in pos][:free]:
                    p = float(s["px"].get(code, np.nan))
                    if np.isnan(p) or cash < WEIGHT * 0.5:
                        continue
                    budget = min(WEIGHT, cash)
                    pos[code] = {"entry_px": p, "shares": budget / p}
                    cash -= budget * (1 + COST)
                    trades += 1
            # 估值
            mv = sum(pos[c]["shares"] * float(px.get(c, 0) or 0) for c in pos)
            nav_hist.append(cash + mv)
            exposure.append(min(1.0, mv / (cash + mv)) if (cash + mv) > 0 else 0)
        nav = pd.Series(nav_hist, index=dates)
        ret = nav.pct_change().dropna()
        mdd = float((nav / nav.cummax() - 1).min())
        years = pd.Series(nav.index).str[:4].values
        df = pd.DataFrame({"nav": nav.values}, index=nav.index)
        yr = df.groupby(years)["nav"].last() / df.groupby(years)["nav"].first() - 1
        results.append({"universe": uni_name, "rs": rs_name, "exit": exit_name,
                        "cagr_pct": round(float(nav.iloc[-1] ** (252 / len(ret)) - 1) * 100, 2),
                        "mdd_pct": round(mdd * 100, 2),
                        "vol_pct": round(float(ret.std() * np.sqrt(252)) * 100, 2),
                        "avg_exposure_pct": round(float(np.mean(exposure)) * 100, 1),
                        "trades": trades,
                        "yearly": {str(y): round(float(v) * 100, 1) for y, v in yr.items()}})
        print(uni_name, rs_name, exit_name, results[-1]["cagr_pct"], results[-1]["mdd_pct"], flush=True)

    results.sort(key=lambda x: -x["cagr_pct"])
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{dates[0]} .. {dates[-1]}", "note": "TT v1.1 组合回测（研究）；20仓×5%；成本0.15%单边",
                    "caveats": ["current stock_basic → 幸存者/ST 偏差", "成交额作流动性/规模代理（daily_basic 仅2023+）",
                                 "TT 信号月末生成，次日起可交易（无未来函数）", "未计冲击成本/停牌"]},
           "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"trend_template_v11_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = [f"# Trend Template v1.1 组合回测 · {rep['meta']['window']}", "",
             "> 股票池 × RS 口径 × 退出规则 对照；20 仓×5%，成本 0.15% 单边；研究用。", "",
             "| 股票池 | RS | 退出 | CAGR | MDD | 波动 | 平均仓位 | 交易数 |", "|---|---|---|---|---|---|---|---|"]
    for x in results[:24]:
        lines.append(f"| {x['universe']} | {x['rs']} | {x['exit']} | {x['cagr_pct']}% | {x['mdd_pct']}% | {x['vol_pct']}% | {x['avg_exposure_pct']}% | {x['trades']} |")
    lines += ["", "## 最佳组合年度收益", ""]
    best = results[0]
    lines += [f"- {best['universe']} / {best['rs']} / {best['exit']}", "",
              "| 年 | 收益% |", "|---|---|"] + [f"| {y} | {v} |" for y, v in best["yearly"].items()]
    lines += ["", "## 读法", "- 对照外部研究：退出规则差异应体现在 MDD/换手；股票池差异应体现在 CAGR/稳定性；",
              "- 若 U2（高流动性）显著优于 U0 → 支持「大盘成分更有效」；若 E_200d 优于固定止损 → 支持「让利润奔跑」。"]
    (OUT / f"trend_template_v11_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"top": [(x["universe"], x["rs"], x["exit"], x["cagr_pct"], x["mdd_pct"]) for x in results[:5]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
