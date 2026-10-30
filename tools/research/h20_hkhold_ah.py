# -*- coding: utf-8 -*-
"""H20 · 港股通持仓变化 × A/H 价差。

假设：南向持仓增加（Δratio 高）× A/H 溢价高（A 相对 H 贵）→ 后续 A 股相对 H 跑输
（溢价收敛 / H 更强）。

数据：hk_hold（南向持股比例 ratio，exchange=HK）、daily、hk_daily、fx_daily（USDCNH/USDHKD）。
样本：hk_daily 起 2023-09-18 ~ 2026-10-29；IS ≤2024-12-31 / OOS ≥2025-01-01。
成本：30bp（配对双边近似）。
产出：research/reports/H20_hkhold_ah_20261030.{md,json}；台账 H20。
"""
from __future__ import annotations

import glob
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
ah = importlib.import_module("ah_premium")
ah.START = "20230918"
ah.END = "20261029"
ah.IS_END = "20241231"
ah.COST = 0.003

DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
IS_CUT = "20241231"
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
    if not frames:
        return pd.DataFrame(columns=["trade_date", "h_code", "ratio"])
    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = out["trade_date"].astype(str)
    out = out.rename(columns={"ts_code": "h_code"})
    out["ratio"] = pd.to_numeric(out["ratio"], errors="coerce")
    return out


def stat(s: pd.Series) -> dict:
    s = pd.Series(s, dtype=float).dropna()
    if len(s) < 15:
        return {"n": int(len(s))}
    t = s.mean() / s.std(ddof=1) * np.sqrt(len(s)) if s.std(ddof=1) else 0.0
    return {"n": int(len(s)), "mean_pct": round(float(s.mean()) * 100, 3), "t": round(float(t), 2)}


def main() -> int:
    pairs = ah.load_pairs()
    fx = ah.load_fx()
    panel = ah.build_panel(pairs, fx)
    print("panel rows:", len(panel), "pairs:", panel["name"].nunique(),
          "dates:", panel["trade_date"].nunique(), flush=True)
    hold = load_hold(pairs)
    print("hold rows:", len(hold), "h_codes:", hold["h_code"].nunique(), flush=True)
    df = panel.merge(hold, on=["trade_date", "h_code"], how="left")

    # 日度累积相对收益（A/H 相对，T+1 入场）
    rel = df.pivot_table(index="trade_date", columns="name", values="rel_ret")
    cum = (1 + rel.fillna(0)).cumprod()
    dates = list(rel.index)
    # 周频采样：每个自然周最后一个交易日
    dts = pd.to_datetime(dates, format="%Y%m%d")
    week = dts.isocalendar()
    wk = pd.DataFrame({"date": dates, "wy": week["year"].astype(str) + "-" + week["week"].astype(str)})
    weekly = sorted(wk.groupby("wy")["date"].max().tolist())
    print("weekly dates:", len(weekly), weekly[0], weekly[-1], flush=True)

    # 每周信号：Δratio_5w（PIT：只用到当周）、溢价分位
    hd = df[["trade_date", "name", "ratio", "premium"]].copy()
    hd["ratio"] = pd.to_numeric(hd["ratio"], errors="coerce")
    hw = hd[hd["trade_date"].isin(weekly)].copy()
    hw = hw.sort_values(["name", "trade_date"])
    hw["d_ratio_5w"] = hw.groupby("name")["ratio"].diff(5)
    # 溢价/Δratio 的当周横截面 rank（0-1）
    hw["prem_rank"] = hw.groupby("trade_date")["premium"].rank(pct=True)
    hw["dr_rank"] = hw.groupby("trade_date")["d_ratio_5w"].rank(pct=True)

    rows = []
    for dt in weekly:
        g = hw[hw["trade_date"] == dt]
        if len(g) < 10:
            continue
        i = dates.index(dt)
        if i >= len(dates) - 1:
            continue
        for h in (5, 10, 20):
            j = min(i + 1 + h, len(dates) - 1)
            fwd = (cum.iloc[j] / cum.iloc[i + 1] - 1).rename("fwd")
            gg = g.set_index("name").join(fwd, how="inner").dropna(subset=["fwd", "prem_rank", "dr_rank"])
            if len(gg) < 10:
                continue
            hi_p = gg[gg["prem_rank"] >= 0.67]
            lo_p = gg[gg["prem_rank"] <= 0.33]
            hi_h = hi_p[hi_p["dr_rank"] >= 0.67]
            lo_h = hi_p[hi_p["dr_rank"] <= 0.33]
            # 假设组：高溢价 & 高持仓增
            if len(hi_h) >= 3:
                rows.append({"date": dt, "horizon": h, "group": "hi_prem_hi_hold",
                             "rel": float(hi_h["fwd"].mean())})
            if len(lo_h) >= 3:
                rows.append({"date": dt, "horizon": h, "group": "hi_prem_lo_hold",
                             "rel": float(lo_h["fwd"].mean())})
            if len(hi_h) >= 3 and len(lo_h) >= 3:
                rows.append({"date": dt, "horizon": h, "group": "spread_hi_hold_minus_lo_hold",
                             "rel": float(hi_h["fwd"].mean() - lo_h["fwd"].mean() - COST)})
    e = pd.DataFrame(rows)
    print("event rows:", len(e), flush=True)
    res = {"meta": {"pairs": int(panel["name"].nunique()), "panel_rows": int(len(panel)),
                    "hold_h_codes": int(hold["h_code"].nunique()), "weekly": len(weekly),
                    "start": ah.START, "end": ah.END, "is_end": IS_CUT, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "groups": {}}
    for h in (5, 10, 20):
        res["groups"][f"h{h}"] = {}
        for grp in ("hi_prem_hi_hold", "hi_prem_lo_hold", "spread_hi_hold_minus_lo_hold"):
            sub = e[(e["horizon"] == h) & (e["group"] == grp)]
            res["groups"][f"h{h}"][grp] = {
                "all": stat(sub["rel"]),
                "IS": stat(sub[sub["date"] <= IS_CUT]["rel"]),
                "OOS": stat(sub[sub["date"] > IS_CUT]["rel"]),
            }
    sp = res["groups"]["h10"]["spread_hi_hold_minus_lo_hold"]["OOS"]
    sp_is = res["groups"]["h10"]["spread_hi_hold_minus_lo_hold"]["IS"]
    hi = res["groups"]["h10"]["hi_prem_hi_hold"]["OOS"]
    status = "backtested" if (sp.get("n", 0) >= 15 and sp.get("t") is not None
                              and abs(sp["t"]) >= 2 and (sp["mean_pct"] < 0)
                              and abs(sp_is.get("t", 0) or 0) >= 1.5
                              and (sp_is.get("mean_pct", 0) or 0) * (sp["mean_pct"] or 0) > 0) else "rejected"
    res["status"] = status

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H20_hkhold_ah_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")

    L = ["# H20 · 港股通持仓变化 × A/H 价差", "",
         "- 样本：%s ~ %s（hk_daily 起）；配对 %d；周频 %d 期；IS≤%s / OOS≥2025-01-01；成本 30bp" % (
             ah.START, ah.END, res["meta"]["pairs"], len(weekly), IS_CUT),
         "- 分组：高溢价(rank≥0.67) × 高持仓增(Δratio rank≥0.67) vs 低持仓增；结果=未来 A 相对 H 收益（负=A 跑输 H）", "",
         "| 窗口 | 高溢价+高持仓增 | 高溢价+低持仓增 | 价差(高-低,含成本) |", "|---|---|---|---|"]
    for h in (5, 10, 20):
        g = res["groups"][f"h{h}"]
        L.append("| %d 日 | %s | %s | %s |" % (h, f(g["hi_prem_hi_hold"]["all"]),
                                               f(g["hi_prem_lo_hold"]["all"]),
                                               f(g["spread_hi_hold_minus_lo_hold"]["all"])))
    L += ["", "## 分样本（10 日）", "| 样本 | 高溢价+高持仓增 | 价差 |", "|---|---|---|"]
    for s in ("IS", "OOS"):
        g = res["groups"]["h10"]
        L.append("| %s | %s | %s |" % (s, f(g["hi_prem_hi_hold"][s]), f(g["spread_hi_hold_minus_lo_hold"][s])))
    L += ["", "## 结论", "- 状态：**%s**（OOS 价差 t=%s，高溢价+高持仓增组 OOS %s）" % (
        status, sp.get("t"), f(hi)), "",
        "## 局限", "- hk_daily 2023-09 起（样本 ~2.8 年，IS 仅 15 个月）；南向 ratio 缺失的 H 股被剔除；",
        "- 未区分行业/市值；配对由名称匹配（H15 同法，收益相关≥0.3 过滤）；未考虑融券可得性（做空 A/H）。"]
    (OUT / "H20_hkhold_ah_20261030.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"status": status, "h10_oos_spread": sp, "h10_is_spread": sp_is,
                      "h10_oos_hi": hi, "md": str(OUT / "H20_hkhold_ah_20261030.md")}, ensure_ascii=False, indent=1), flush=True)

    # 台账
    import sqlite3
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    exists = con.execute("SELECT id FROM hypotheses WHERE id='H20'").fetchone()
    row = {"id": "H20", "title": "港股通持仓变化 × A/H 价差",
           "question": "南向持仓增加 × A/H 高溢价 → 后续 A 股相对 H 跑输？",
           "data_sources": "hk_hold(南向 ratio)+daily+hk_daily+fx_daily",
           "method": "周频；Δratio 5周 × 溢价横截面 rank；未来 5/10/20 日 A 相对 H 收益；IS≤2024/OOS≥2025；30bp",
           "gates": "PIT=是（周频只用当周已知）；OOS=独立；成本=30bp；容量=港股通标的",
           "status": status, "owner": "DSH-Lead",
           "conclusion": "配对 %d / 周频 %d 期；10日 OOS 价差 %s，高溢价+高持仓增组 OOS %s → %s" % (
               res["meta"]["pairs"], len(weekly), f(sp), f(hi), status),
           "evidence_path": "research/reports/H20_hkhold_ah_20261030.md",
           "created_at": now, "updated_at": now}
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    keys = [k for k in row if k in cols]
    if exists:
        con.execute("UPDATE hypotheses SET %s WHERE id='H20'" % ",".join("%s=?" % k for k in keys if k != "id"),
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print("H20 已写台账", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
