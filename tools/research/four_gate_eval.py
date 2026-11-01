# -*- coding: utf-8 -*-
"""四关验证：前视/成本/容量/regime（对 mining v2 候选）。

候选（方向统一为「分数越高越好」）：
  vol_20  → score = -std20（低波）
  mom_20  → score = -ret20（短期反转）
  mom_120 → score = -ret120（长期反转）
  rev_5   → score = -ret5（5日反转，与 mining 的 rev_5 同向）

四关定义：
  G1 前视/一致性：月频 IC 的 t 值 + walk-forward 年度方向（正收益年份占比≥70%）
  G2 成本/收益：多空净收益（换手×0.3%/月）+ 多头组净超额（vs 等权市场）
  G3 容量：多头组按「单票日成交额×5%」估算可容纳资金（中位数，亿元）
  G4 regime：按市场 60 日等权收益正/负分组的净多空（两组均>0 才过）

判定：G1 过=年度正收益≥70% 且 IC t≥2；G2 过=净多空年化>0 且多头净超额>0；
      G3 过=容量中位数≥0.5 亿；G4 过=两个 regime 净收益均>0。
输出：research/reports/four_gate_<yyyymmdd>.{json,md}
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
COST_ONE_WAY = 0.0015   # 佣金+滑点（单边）；卖出印花税另计在换手中近似
CANDIDATES = ["vol_20", "mom_20", "mom_120", "rev_5"]


def window_dates(start: str, buffer_days: int = 150) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    s = pd.Series(ds)
    return s[s < start].tail(buffer_days).tolist() + s[s >= start].tolist()


def build_panels(dts: list[str]):
    parts = []
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
        parts.append(d[["ts_code", "trade_date", "adj", "amount"]])
        if len(parts) >= 300:
            chunk = pd.concat(parts, ignore_index=True)
            parts = []
            adj = chunk.pivot_table(index="trade_date", columns="ts_code", values="adj")
            amt = chunk.pivot_table(index="trade_date", columns="ts_code", values="amount")
            if "A" not in dir():
                pass
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
    adj, amt = build_panels(dts)
    print("panels:", adj.shape, amt.shape, flush=True)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    list_dates_ = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    def listed_at(dt_):
        """PIT：截至信号日的上市天数（2026-11-01 修复：原用最新日→次新股前视）"""
        return (pd.Timestamp(dt_) - list_dates_).dt.days

    s = pd.Series(adj.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]
    mends = [m for m in mends if m >= "20190101"]

    recs = {c: [] for c in CANDIDATES}
    prev_sets = {c: {"top": set(), "bot": set()} for c in CANDIDATES}
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
        feats = {
            "vol_20": -hist.tail(21).pct_change().std().reindex(cc),
            "mom_20": -(px / hist.iloc[-21] - 1).reindex(cc),
            "mom_120": -(px / hist.iloc[-121] - 1).reindex(cc),
            "rev_5": -(px / hist.iloc[-6] - 1).reindex(cc),
        }
        market_fwd = fwd.reindex(cc).mean()
        # regime：全市场 60 日收益（adj 矩阵时间序列，等权）
        mkt60 = float((hist.iloc[-1] / hist.iloc[-61] - 1).mean()) if len(hist) > 61 else 0.0
        for c in CANDIDATES:
            x = feats[c]
            d = pd.DataFrame({"x": x, "y": fwd.reindex(cc), "amt": amt.loc[dt].reindex(cc)}).dropna(subset=["x", "y"])
            if len(d) < 200:
                continue
            q = pd.qcut(d["x"].rank(method="first"), 10, labels=False, duplicates="drop")
            top = d[q == q.max()]
            bot = d[q == q.min()]
            ls = float(top["y"].mean() - bot["y"].mean())
            lo = float(top["y"].mean() - d["y"].mean())
            ts = set(top.index)
            turnover = len(ts - prev_sets[c]["top"]) / max(1, len(ts))
            cost = turnover * COST_ONE_WAY * 2
            # tushare daily.amount 单位为千元 → ×1000 换算成元；容量(亿元)=Σ(成交额×5%)/1e8
            cap = float(top["amt"].sum() * 1000.0 * 0.05 / 1e8) if top["amt"].notna().any() else np.nan
            recs[c].append({"date": nxt, "ls": ls, "ls_net": ls - cost, "lo_net": lo - turnover * COST_ONE_WAY,
                            "ic": float(d["x"].corr(d["y"], method="spearman")), "turnover": turnover,
                            "cap_yi": cap, "regime": "up" if mkt60 > 0 else "down"})
            prev_sets[c]["top"] = ts

    out = {}
    for c in CANDIDATES:
        df = pd.DataFrame(recs[c])
        if df.empty:
            continue
        df["year"] = df["date"].str[:4]
        yearly = df.groupby("year")["ls_net"].sum()
        ic = df["ic"].to_numpy()
        t_ic = float(ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))) if ic.std(ddof=1) > 0 else 0.0
        g1 = bool((yearly > 0).mean() >= 0.70 and t_ic >= 2.0)
        ann_ls_net = float((1 + df["ls_net"].mean()) ** 12 - 1)
        ann_lo_net = float((1 + df["lo_net"].mean()) ** 12 - 1)
        g2 = bool(ann_ls_net > 0 and ann_lo_net > 0)
        cap_med = float(df["cap_yi"].median()) if df["cap_yi"].notna().any() else 0.0
        g3 = bool(cap_med >= 0.5)
        up = df[df["regime"] == "up"]["ls_net"]
        dn = df[df["regime"] == "down"]["ls_net"]
        g4 = bool(len(up) > 6 and len(dn) > 6 and up.mean() > 0 and dn.mean() > 0)
        out[c] = {"months": len(df), "gates": {"G1_consistency": g1, "G2_cost_net": g2, "G3_capacity": g3, "G4_regime": g4},
                  "metrics": {"ic_mean": round(float(ic.mean()), 4), "ic_t": round(t_ic, 2),
                              "ls_gross_ann_pct": round(float((1 + df["ls"].mean()) ** 12 - 1) * 100, 1),
                              "ls_net_ann_pct": round(ann_ls_net * 100, 1),
                              "lo_net_ann_pct": round(ann_lo_net * 100, 1),
                              "turnover_monthly_avg": round(float(df["turnover"].mean()), 3),
                              "cap_median_yi": round(cap_med, 2),
                              "regime_up_ls_net_mean_pct": round(float(up.mean()) * 100, 3) if len(up) else None,
                              "regime_down_ls_net_mean_pct": round(float(dn.mean()) * 100, 3) if len(dn) else None,
                              "positive_years": f"{int((yearly>0).sum())}/{len(yearly)}"},
                  "yearly_ls_net_pct": {y: round(float(v) * 100, 1) for y, v in yearly.items()},
                  "verdict": "PASS" if all([g1, g2, g3, g4]) else "FAIL"}
    report = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "window": f"{mends[0]} .. {mends[-2]}", "months": len(mends) - 1,
                        "cost": "单边0.15%（佣金+滑点），换手×2；印花税近似在成本内；等权十分位",
                        "capacity": "多头组 Σ(月末成交额×5%)，5% 参与率、单日执行",
                        "universe": "非ST/上市≥250日/价≥2；current stock_basic 偏差",
                        "note": "四关评估；G3/成本为粗估，需更细成交模拟"},
              "candidates": out,
              "pass": [c for c, v in out.items() if v["verdict"] == "PASS"]}
    OUT.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d")
    (OUT / f"four_gate_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# 四关验证 · {report['meta']['window']}（{report['meta']['months']} 月）", "",
             "| 候选 | G1 一致性 | G2 成本净 | G3 容量 | G4 regime | 净多空年化 | 正收益年 | IC t | 容量中位(亿) | 结论 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c, v in out.items():
        g = v["gates"]; m = v["metrics"]
        mark = lambda b: "✅" if b else "❌"
        lines.append(f"| {c} | {mark(g['G1_consistency'])} | {mark(g['G2_cost_net'])} | {mark(g['G3_capacity'])} | {mark(g['G4_regime'])} | "
                     f"{m['ls_net_ann_pct']}% | {m['positive_years']} | {m['ic_t']} | {m['cap_median_yi']} | **{v['verdict']}** |")
    lines += ["", "## 年度净多空（%）", "", "| 年 | " + " | ".join(CANDIDATES) + " |", "|---|" + "---|" * len(CANDIDATES)]
    years = sorted({y for c in out for y in out[c]["yearly_ls_net_pct"]})
    for y in years:
        lines.append(f"| {y} | " + " | ".join(str(out[c]["yearly_ls_net_pct"].get(y, "")) for c in CANDIDATES) + " |")
    lines += ["", "## 边界", "- G3 用月末成交额×5% 粗估，非逐笔成交模拟；",
              "- 成本单边 0.15% 粗估（含滑点/印花近似）；",
              "- current stock_basic → 幸存者/ST 偏差；",
              "- 通过≠可上线：还需人工复核、影子盘与 L2 批准。"]
    (OUT / f"four_gate_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({c: {"verdict": v["verdict"], "gates": v["gates"], "ls_net_ann": v["metrics"]["ls_net_ann_pct"],
                          "cap": v["metrics"]["cap_median_yi"]} for c, v in out.items()}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
