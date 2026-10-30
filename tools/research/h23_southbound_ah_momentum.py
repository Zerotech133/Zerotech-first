# -*- coding: utf-8 -*-
"""H23 · 南向增持 → A 相对 H 走强（20 日）· 重叠修正检验。

线索来源：H20-v2 中「高溢价×高持仓增 vs 低持仓增」20 日价差 +1.00%(t=2.36)，OOS +1.22%。
但周频信号 + 20 日持有 → 严重重叠，t 值虚高。本检验：
1) 非重叠抽样：按周序列每 4 周取 1（4 个偏移分别计算，取平均/范围）；
2) block bootstrap：块长 4 周，500 次重抽样，OOS 价差均值的 p 值；
3) 判定：OOS 非重叠 t≥2 且 bootstrap p<0.05 → backtested，否则 rejected。

数据：hk_hold + daily + hk_daily(2021回补) + fx；样本 2021-01~2026-10；IS≤2023 / OOS≥2024。
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import glob

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
ah = importlib.import_module("ah_premium")
ah.START = "20210104"
ah.END = "20261029"
ah.IS_END = "20231231"
ah.COST = 0.003
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
IS_CUT = "20231231"
COST = 0.003


def load_hold(pairs: pd.DataFrame) -> pd.DataFrame:
    codes = set(pairs["h_code"])
    frames = []
    for f in sorted(glob.glob(str(DATA / "hk_hold" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (ah.START <= dt <= ah.END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "ratio", "exchange"])
        df = tb.to_pandas()
        df = df[(df["exchange"] == "HK") & (df["ts_code"].isin(codes))]
        if len(df):
            frames.append(df[["trade_date", "ts_code", "ratio"]])
    m = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["trade_date", "ts_code", "ratio"])
    m["trade_date"] = m["trade_date"].astype(str)
    m = m.rename(columns={"ts_code": "h_code"})
    m["ratio"] = pd.to_numeric(m["ratio"], errors="coerce")
    return m


def tstat(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 8 or s.std(ddof=1) == 0:
        return 0.0
    return float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))


def main() -> int:
    pairs = ah.load_pairs()
    fx = ah.load_fx()
    panel = ah.build_panel(pairs, fx)
    hold = load_hold(pairs)
    df = panel.merge(hold, on=["trade_date", "h_code"], how="left")
    print("panel:", len(df), "hold rows:", len(hold), flush=True)
    rel = df.pivot_table(index="trade_date", columns="name", values="rel_ret")
    cum = (1 + rel.fillna(0)).cumprod()
    dates = list(rel.index)
    dts = pd.to_datetime(dates, format="%Y%m%d")
    wk = dts.isocalendar()
    wdf = pd.DataFrame({"date": dates, "wy": wk["year"].astype(str) + "-" + wk["week"].astype(str)})
    weekly = sorted(wdf.groupby("wy")["date"].max().tolist())

    hd = df[["trade_date", "name", "ratio", "premium"]].copy()
    hd["ratio"] = pd.to_numeric(hd["ratio"], errors="coerce")
    hw = hd[hd["trade_date"].isin(weekly)].sort_values(["name", "trade_date"])
    hw["d_ratio_5w"] = hw.groupby("name")["ratio"].diff(5)
    hw["prem_rank"] = hw.groupby("trade_date")["premium"].rank(pct=True)
    hw["dr_rank"] = hw.groupby("trade_date")["d_ratio_5w"].rank(pct=True)

    rows = []
    for dt in weekly:
        g = hw[hw["trade_date"] == dt]
        if len(g) < 10:
            continue
        i = dates.index(dt)
        j = i + 1 + 20
        if j >= len(dates):
            continue
        fwd = (cum.iloc[j] / cum.iloc[i + 1] - 1).rename("fwd")
        gg = g.set_index("name").join(fwd, how="inner").dropna(subset=["fwd", "prem_rank", "dr_rank"])
        hi_p = gg[gg["prem_rank"] >= 0.67]
        hi_h = hi_p[hi_p["dr_rank"] >= 0.67]
        lo_h = hi_p[hi_p["dr_rank"] <= 0.33]
        if len(hi_h) >= 3 and len(lo_h) >= 3:
            rows.append({"date": dt, "spread": float(hi_h["fwd"].mean() - lo_h["fwd"].mean() - COST),
                         "hi": float(hi_h["fwd"].mean())})
    e = pd.DataFrame(rows)
    print("weekly spread obs:", len(e), flush=True)
    e["split"] = np.where(e["date"] <= IS_CUT, "IS", "OOS")

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 5:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3), "t": round(tstat(x), 2)}

    # 1) 全周频（未修正，参考）
    res = {"meta": {"obs": int(len(e)), "weekly": len(weekly), "start": dates[0], "end": dates[-1],
                    "is_end": IS_CUT, "cost": COST, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "weekly": {"all": stat(e["spread"]), "IS": stat(e.loc[e.split == "IS", "spread"]),
                      "OOS": stat(e.loc[e.split == "OOS", "spread"])},
           "nonoverlap": {}}
    # 2) 非重叠：4 个偏移各取每 4 周 1 个
    for off in range(4):
        sub = e.iloc[off::4]
        res["nonoverlap"][f"off{off}"] = {"all": stat(sub["spread"]),
                                          "IS": stat(sub.loc[sub.split == "IS", "spread"]),
                                          "OOS": stat(sub.loc[sub.split == "OOS", "spread"])}
    oos_nl = [res["nonoverlap"][f"off{o}"].get("OOS", {}).get("t") for o in range(4)]
    oos_t = [x for x in oos_nl if x is not None]
    res["nonoverlap_oos_t"] = {"values": oos_nl, "mean": round(float(np.mean(oos_t)), 2) if oos_t else None,
                               "max": max(oos_t) if oos_t else None}
    # 3) block bootstrap（块长 4 周，OOS）
    oos = e[e.split == "OOS"]["spread"].dropna().to_numpy()
    rng = np.random.default_rng(42)
    boot = []
    if len(oos) >= 20:
        n_blocks = max(1, len(oos) // 4)
        for _ in range(500):
            starts = rng.integers(0, max(1, len(oos) - 4), size=n_blocks)
            sample = np.concatenate([oos[s:s + 4] for s in starts])[: len(oos)]
            boot.append(sample.mean())
    boot = np.array(boot)
    p_one_sided = float((boot <= 0).mean()) if len(boot) else None
    res["bootstrap"] = {"n": int(len(boot)), "mean_pct": round(float(boot.mean()) * 100, 3) if len(boot) else None,
                        "p_le_0": round(p_one_sided, 4) if p_one_sided is not None else None}

    mean_t = res["nonoverlap_oos_t"]["mean"]
    status = "backtested" if (mean_t is not None and mean_t >= 2
                              and p_one_sided is not None and p_one_sided < 0.05) else "rejected"
    res["status"] = status
    js = OUT / "H23_southbound_ah_momentum_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# H23 · 南向增持 → A 相对 H 走强（20 日）· 重叠修正检验", "",
         "- 来源：H20-v2 探索线索（周频 20 日价差 +1.00% t=2.36；OOS +1.22% t=2.33）",
         f"- 样本：{dates[0]} ~ {dates[-1]}；周频 {len(weekly)} 期，有效价差 {len(e)} 期；IS≤{IS_CUT} / OOS≥2024；成本 30bp",
         "", "| 口径 | 全样本 | IS | OOS |", "|---|---|---|---|",
         "| 周频（未修正，参考） | %s | %s | %s |" % (
             res["weekly"]["all"], res["weekly"]["IS"], res["weekly"]["OOS"]),
         "| 非重叠（4 偏移均值 t） | — | — | **%s**（4 个偏移 t：%s） |" % (
             res["nonoverlap_oos_t"]["mean"], oos_nl),
         "", "## Block bootstrap（OOS，块长 4 周，500 次）",
         "- 均值：%s%%；P(均值≤0)=%s" % (res["bootstrap"]["mean_pct"], res["bootstrap"]["p_le_0"]),
         "", "## 结论", "- 状态：**%s**" % status,
         "- 解释：周频重叠使 t 虚高；非重叠/自助法修正后若不再显著，则线索不成立，仅记录备查。", "",
         "## 交付", "- 脚本：`tools/research/h23_southbound_ah_momentum.py`",
         "- 报告：`research/reports/H23_southbound_ah_momentum_20261030.{md,json}`",
         "- 台账：H23 = %s" % status]
    (OUT / "H23_southbound_ah_momentum_20261030.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"status": status, "weekly": res["weekly"], "nonoverlap_oos": res["nonoverlap_oos_t"],
                      "bootstrap": res["bootstrap"]}, ensure_ascii=False, indent=1), flush=True)

    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    row = {"id": "H23", "title": "南向增持 → A 相对 H 走强（20日）",
           "question": "港股通持仓增加（Δratio 高分位）是否预示未来 20 日 A 股相对 H 股走强？",
           "data_sources": "hk_hold + daily + hk_daily(2021回补) + fx",
           "method": "周频 Δratio 5周 分位；20日 A-H 相对收益；非重叠抽样(4偏移)+block bootstrap 修正；IS≤2023/OOS≥2024；30bp",
           "gates": "PIT=是；OOS=独立；成本=30bp；容量=A/H配对；重叠=已修正",
           "status": status, "owner": "DSH-Lead",
           "conclusion": "周频 OOS %s；非重叠 OOS 平均 t=%s；bootstrap P(≤0)=%s → %s" % (
               res["weekly"]["OOS"], res["nonoverlap_oos_t"]["mean"], res["bootstrap"]["p_le_0"], status),
           "evidence_path": "research/reports/H23_southbound_ah_momentum_20261030.md",
           "created_at": now, "updated_at": now}
    keys = [k for k in row if k in cols]
    if con.execute("SELECT id FROM hypotheses WHERE id='H23'").fetchone():
        con.execute("UPDATE hypotheses SET %s WHERE id='H23'" % ",".join("%s=?" % k for k in keys if k != "id"),
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print("H23 已写台账", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
