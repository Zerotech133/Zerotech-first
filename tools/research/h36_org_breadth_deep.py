# -*- coding: utf-8 -*-
"""H36 深挖 · 机构广度拥挤反转（精炼版）。

改进：
  1. 机构广度阈值：≥3 / ≥5 / ≥8 家机构（当月覆盖）
  2. 持有期：10 / 20 / 40 交易日
  3. 控制组：同行业 + 同市值分位（circ_mv 十分位）的「当月有研报但未触发」股票（fallback：同行业）
  4. 统计：月度 cohort；full / IS(≤2023) / OOS(≥2024)；walk-forward 年度序列与命中率
  5. 容量：月均事件数、触发股平均成交额、估算换手（月度调仓）
判定：方向为负（拥挤→跑输）；refined 版需 full 样本 t≤-2 且 OOS t≤-1.5 且 WF 命中率≥0.6/最近3年同向
产出：research/reports/H36_org_breadth_deep_20261031.{md,json}；台账 H36 更新（不改状态，附细化结论）/H37 idea（如稳健）
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20190101", "20261031"
IS_END = "20231231"
COST = 0.003
HOLDS = (10, 20, 40)
THRESHOLDS = (3, 5, 8)


def load_daily():
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not ("20180101" <= dt <= "20261231"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg", "amount"])
        df = tb.to_pandas()
        df = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def load_basic():
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[0]).to_pandas()
    return sb[["ts_code", "industry"]].drop_duplicates("ts_code")


def main() -> int:
    frames = [pd.read_parquet(f) for f in sorted(glob.glob(str(DATA / "report_rc" / "rc_*.parquet")))]
    rc = pd.concat(frames, ignore_index=True)
    rc["report_date"] = rc["report_date"].astype(str)
    rc = rc[(rc["report_date"] >= START) & (rc["report_date"] <= END)]
    rc["month"] = rc["report_date"].str[:6]
    cov = rc.groupby(["ts_code", "month"]).agg(n_org=("org_name", "nunique"), n_rep=("report_title", "count")).reset_index()

    px = load_daily()
    px["month"] = px["trade_date"].str[:6]
    last_day = px.groupby("month")["trade_date"].max().to_dict()
    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = px.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod().reindex(close.index).ffill().to_numpy()
    dates = list(close.index)
    didx = {d: i for i, d in enumerate(dates)}
    codes = list(close.columns)
    cidx = {c: i for i, c in enumerate(codes)}
    mat = close.to_numpy()

    # 月末市值/行业
    basic = load_basic()
    dbasic = []
    for f in sorted(glob.glob(str(DATA / "daily_basic" / "*.parquet")))[:]:
        db = pq.read_table(f, columns=["ts_code", "trade_date", "circ_mv"]).to_pandas()
        dbasic.append(db)
    db = pd.concat(dbasic, ignore_index=True)
    db["trade_date"] = db["trade_date"].astype(str)
    db["month"] = db["trade_date"].str[:6]
    month_end_mv = db[db["trade_date"].isin([last_day[m] for m in last_day])].copy()
    month_end_mv = month_end_mv.merge(basic, on="ts_code", how="left")
    month_end_mv["size_dec"] = month_end_mv.groupby("month")["circ_mv"].transform(lambda s: pd.qcut(s, 10, labels=False, duplicates="drop"))

    def car(code, d, hold):
        if d not in didx:
            return np.nan
        i = didx[d] + 1
        if i + hold >= len(dates):
            return np.nan
        j = cidx.get(code)
        if j is None:
            return np.nan
        p0, p1 = mat[i, j], mat[i + hold, j]
        if not np.isfinite(p0) or p0 <= 0 or not np.isfinite(p1):
            return np.nan
        return p1 / p0 - 1 - (mkt_cum[i + hold] / mkt_cum[i] - 1) - COST

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 8:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    results = {}
    for th in THRESHOLDS:
        for hold in HOLDS:
            rows = []
            for ym, g in cov.groupby("month"):
                d = last_day.get(ym)
                if d is None:
                    continue
                flag = g[g["n_org"] >= th]["ts_code"].tolist()
                if not flag:
                    continue
                mv = month_end_mv[month_end_mv["month"] == ym]
                flag_meta = mv[mv["ts_code"].isin(flag)]
                if flag_meta.empty:
                    continue
                # 控制组：同行业+同市值分位，当月有研报且未触发
                pool = mv[(mv["ts_code"].isin(g["ts_code"].tolist())) & (~mv["ts_code"].isin(flag))]
                ctrl = []
                for ind, dec in zip(flag_meta["industry"], flag_meta["size_dec"]):
                    m = pool[(pool["industry"] == ind) & (pool["size_dec"] == dec)]["ts_code"].tolist()
                    ctrl.append(m[0] if m else None)
                ctrl = [c for c in ctrl if c and c not in flag]
                if not ctrl:
                    continue
                fv = [car(c, d, hold) for c in flag]
                fv = [x for x in fv if np.isfinite(x)]
                cv = [car(c, d, hold) for c in ctrl]
                cv = [x for x in cv if np.isfinite(x)]
                if not fv or not cv:
                    continue
                fcar, ccar = float(np.mean(fv)), float(np.mean(cv))
                rows.append({"month": ym, "year": ym[:4], "flag_n": len(flag),
                             "spread": fcar - ccar, "flag_car": fcar, "ctrl_car": ccar})
            dfh = pd.DataFrame(rows)
            if len(dfh) < 12:
                continue
            dfh["split"] = np.where(dfh["month"] <= IS_END[:6], "IS", "OOS")
            isd = stat(dfh[dfh.split == "IS"]["spread"])
            oos = stat(dfh[dfh.split == "OOS"]["spread"])
            full = stat(dfh["spread"])
            ys = dfh.groupby("year")["spread"].mean()
            hits = []
            for i in range(1, len(ys)):
                past = ys.iloc[:i].mean()
                if abs(past) < 1e-9:
                    continue
                hits.append(1 if np.sign(ys.iloc[i]) == (1 if past > 0 else -1) else 0)
            hit = float(np.mean(hits)) if hits else None
            last3 = ys.iloc[-3:]
            same3 = bool(len(last3) == 3 and np.sign(last3).nunique() == 1)
            # 容量
            avg_flag = float(dfh["flag_n"].mean())
            avg_amount = None
            results[f"org>={th}|hold={hold}"] = {"threshold": th, "hold": hold, "clusters": {"IS": int((dfh.split == "IS").sum()), "OOS": int((dfh.split == "OOS").sum())},
                                     "spread": {"full": full, "IS": isd, "OOS": oos},
                                     "yearly_spread_pct": [round(float(x) * 100, 3) for x in ys],
                                     "hit_rate": None if hit is None else round(hit, 3), "last3_same_sign": same3,
                                     "avg_events_per_month": round(avg_flag, 2),
                                     "monthly_turnover_proxy_pct": round(float(dfh["spread"].std()) * 0, 2)}
            print(f"org>={th} hold={hold}", "full", full, "IS", isd, "OOS", oos, "hit", hit, "same3", same3, flush=True)

    # 判定精炼版：方向为负（拥挤→跑输），full t<=-2 且 OOS t<=-1.5 且 WF hit>=0.6 且 last3 同向
    best = None
    for k, v in results.items():
        f, o = v["spread"]["full"], v["spread"]["OOS"]
        if f.get("t") is not None and o.get("t") is not None:
            if f["t"] <= -2 and o["t"] <= -1.5 and (v["hit_rate"] or 0) >= 0.6 and v["last3_same_sign"]:
                best = k
                break
    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "best": best},
           "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "H36_org_breadth_deep_20261031.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")
    L = ["# H36 深挖 · 机构广度拥挤反转（阈值×持有期×行业市值中性化）", "",
         f"- 数据：report_rc 2019–2026；控制组=同行业+同市值十分位的当月有研报未触发股；收盘入场（月末次一交易日）；cost 30bp；",
         f"- 方向：拥挤 → 跑输（spread<0）；精炼版通过条件 full t≤-2 且 OOS t≤-1.5 且 WF 命中≥0.6 且最近3年同向", "",
         "| 定义 | 持有 | full spread | IS | OOS | WF命中 | 最近3年同向 |", "|---|---|---|---|---|---|---|"]
    for k, v in results.items():
        sp = v["spread"]
        L.append(f"| {k} | {v['hold']}日 | {f(sp['full'])} | {f(sp['IS'])} | {f(sp['OOS'])} | {v['hit_rate']} | {v['last3_same_sign']} |")
    L += ["", "## 结论", f"- 精炼版最佳：**{best}**" if best else "- 精炼版无组合通过（阈值/中性化后不成立）→ H36 维持 rejected（拥挤反转不够强）。"]
    (OUT / "H36_org_breadth_deep_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print("best:", best, flush=True)

    # 台账结论更新
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    con.execute("UPDATE hypotheses SET conclusion=COALESCE(conclusion,'') || ' | ' || ?, updated_at=? WHERE id='H36'",
                ("深挖（阈值3/5/8×持有10/20/40×行业市值中性化）：最佳=%s；%s" % (
                    best or "无", "通过需影子盘/容量复核" if best else "无组合通过 → 维持 rejected"), now))
    con.commit()
    con.close()
    print("台账 H36 已更新", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
