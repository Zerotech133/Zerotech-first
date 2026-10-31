# -*- coding: utf-8 -*-
"""H34-H36 v2 · 研报评级/盈利预测/覆盖因子（report_rc 历史数据）。

数据：quant_backtest/data/tushare/report_rc（2019-2026，tushare report_rc 同步）
特征（月末可知，次一交易日收盘入场）：
  H34 评级上调：当月平均评级 - 上月平均评级 ≥ +0.3（评级数字化 买入5/增持4/中性3/减持2/卖出1）→ 预期未来 20 日跑赢
  H35 盈利预测上修：当月净利润预测中位数 / 上月 - 1 ≥ +5%（同股票）→ 预期未来 20 日跑赢
  H36 机构覆盖广度：当月覆盖机构数 ≥3（高关注/拥挤）→ 预期未来 20 日跑输（反转）
口径：20/60 日 CAR（相对等权市场、成本 30bp）；月度 cohort（同一月内多股票取均值，控制组=当月有研报未触发股票）；
      IS≤2023 / OOS≥2024；按 cohort 月份 t + block bootstrap。
产出：research/reports/H34-36_report_rc_factors_20261031.{md,json}；台账 H34-H36。
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
sys.path.insert(0, str(REPO / "tools" / "research"))
import stat_utils  # noqa: E402

START, END = "20190101", "20261031"
IS_END = "20231231"
COST = 0.003
HORIZONS = (20, 60)
RATING = {"买入": 5, "强烈推荐": 5, "增持": 4, "推荐": 4, "优于大市": 4, "谨慎推荐": 4,
          "中性": 3, "持有": 3, "同步大市": 3, "中性偏多": 3,
          "减持": 2, "弱于大市": 2, "卖出": 1}


def load_rc() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "report_rc" / "rc_*.parquet"))):
        df = pd.read_parquet(f)
        frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["report_date"] = d["report_date"].astype(str)
    d = d[pd.to_numeric(d["report_date"], errors="coerce").notna()]
    d = d[(d["report_date"] >= "20190101") & (d["report_date"] <= END)]
    d["month"] = d["report_date"].str[:6]
    d["rating_num"] = d["rating"].map(RATING)
    d["np"] = pd.to_numeric(d["np"], errors="coerce")
    return d


def load_daily():
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not ("20180101" <= dt <= "20261231"):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pct_chg"])
        df = tb.to_pandas()
        df = df[df["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
        if len(df):
            frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def main() -> int:
    rc = load_rc()
    print("rc rows:", len(rc), "codes:", rc["ts_code"].nunique(), "months:", rc["month"].nunique(), flush=True)
    dmin, dmax = rc["month"].min(), rc["month"].max()
    months = (int(dmax[:4]) - int(dmin[:4])) * 12 + (int(dmax[4:]) - int(dmin[4:]))
    print("coverage:", dmin, "~", dmax, "months:", months, flush=True)
    if months < 18:
        print("DATA_INSUFFICIENT", flush=True)
        return 1
    # 月度聚合（PIT：当月内研报 → 月末已知）
    m = rc.groupby(["ts_code", "month"]).agg(
        rating=("rating_num", "mean"), n_rep=("report_title", "count"),
        n_org=("org_name", "nunique"), np_med=("np", "median")).reset_index()
    m = m.sort_values(["ts_code", "month"])
    g = m.groupby("ts_code", sort=False)
    m["rating_prev"] = g["rating"].shift(1)
    m["np_prev"] = g["np_med"].shift(1)
    m["month_prev"] = g["month"].shift(1)
    # 上月必须相邻（防跨年缺口）：month 的上一自然月
    def prev_month(ym: str) -> str:
        y, mm = int(ym[:4]), int(ym[4:])
        return f"{y-1}12" if mm == 1 else f"{y}{mm-1:02d}"
    m["adjacent"] = m["month_prev"] == m["month"].map(lambda x: prev_month(str(x)))
    m["rating_up"] = (m["adjacent"]) & (m["rating_prev"].notna()) & (m["rating"] - m["rating_prev"] >= 0.3)
    m["np_up"] = (m["adjacent"]) & (m["np_prev"].notna()) & (m["np_prev"] > 0) & (m["np_med"] / m["np_prev"] - 1 >= 0.05)
    m["org_breadth"] = m["n_org"] >= 3
    m = m[(m["month"] >= START[:6]) & (m["month"] <= END[:6])]
    print("monthly obs:", len(m), "signals:", {k: int(m[k].sum()) for k in ("rating_up", "np_up", "org_breadth")}, flush=True)

    # 行情：月末最后交易日 + 20/60 交易日后
    px = load_daily()
    px["month"] = px["trade_date"].str[:6]
    last_day = px.groupby("month")["trade_date"].max().to_dict()
    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = px.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod()
    dates = list(close.index)
    didx = {d: i for i, d in enumerate(dates)}
    codes = list(close.columns)
    cidx = {c: i for i, c in enumerate(codes)}
    mat = close.to_numpy()

    def cohort_car(codes_list: list[str], month: str, hold: int):
        d = last_day.get(month)
        if d is None or d not in didx:
            return None
        i = didx[d] + 1
        if i + hold >= len(dates):
            return None
        rows = []
        cols = []
        for c in codes_list:
            j = cidx.get(c)
            if j is None:
                continue
            rows.append(i)
            cols.append(j)
        if not rows:
            return None
        p0 = mat[rows, cols]
        p1 = mat[[r + hold for r in rows], cols]
        mm = mkt_cum.iloc[[r + hold for r in rows]].to_numpy() / mkt_cum.iloc[rows].to_numpy() - 1
        ret = p1 / p0 - 1 - mm - COST
        ret = ret[np.isfinite(ret) & (p0 > 0)]
        return ret if len(ret) else None

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 8:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    m["ym"] = m["month"]
    all_by_month = {ym: g["ts_code"].tolist() for ym, g in m.groupby("ym")}
    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "rc_rows": int(len(rc)), "rc_codes": int(rc["ts_code"].nunique()),
                    "coverage": [dmin, dmax], "months": months,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "hypotheses": {}}
    sigs = {
        "H34_rating_up": ("rating_up", +1, "评级上调 → 跑赢"),
        "H35_np_revision_up": ("np_up", +1, "盈利预测上修 → 跑赢"),
        "H36_org_breadth": ("org_breadth", -1, "机构广度≥3（拥挤）→ 跑输"),
    }
    for hid, (col, direction, desc) in sigs.items():
        grp = {"expect": desc, "direction": direction, "events": int(m[col].sum())}
        spread_by_h = {}
        for h in HORIZONS:
            srows = []
            for ym, grp_month in m.groupby("month"):
                flag_codes = grp_month[grp_month[col]]["ts_code"].tolist()
                if not flag_codes:
                    continue
                fcar = cohort_car(flag_codes, ym, h)
                if fcar is None:
                    continue
                all_codes = all_by_month.get(ym, [])
                ctrl = cohort_car([c for c in all_codes if c not in set(flag_codes)], ym, h)
                srows.append({"month": ym, "flag": float(np.mean(fcar)),
                              "ctrl": float(np.mean(ctrl)) if ctrl is not None and len(ctrl) else np.nan,
                              "n": len(fcar)})
            dfh = pd.DataFrame(srows).dropna()
            if len(dfh) < 8:
                grp[f"h{h}"] = {"n": len(dfh)}
                continue
            dfh["spread"] = dfh["flag"] - dfh["ctrl"]
            dfh["split"] = np.where(dfh["month"] <= IS_END[:6], "IS", "OOS")
            isd = stat(dfh[dfh.split == "IS"]["spread"])
            oos = stat(dfh[dfh.split == "OOS"]["spread"])
            grp[f"h{h}"] = {"flag": {"IS": stat(dfh[dfh.split == "IS"]["flag"]), "OOS": stat(dfh[dfh.split == "OOS"]["flag"])},
                            "spread": {"IS": isd, "OOS": oos},
                            "clusters": {"IS": int((dfh.split == "IS").sum()), "OOS": int((dfh.split == "OOS").sum())},
                            "bootstrap": stat_utils.block_bootstrap_p(dfh[dfh.split == "OOS"]["spread"], block=3, n_boot=300),
                            "avg_ev_per_month": round(float(dfh["n"].mean()), 2) if "n" in dfh else None}
            spread_by_h[h] = grp[f"h{h}"]
            print(hid, f"h{h}", "clusters", grp[f"h{h}"]["clusters"], "spread IS", isd, "OOS", oos, flush=True)
        # 判定：任一窗口方向一致且 IS/OOS 双显著
        best = None
        for h in HORIZONS:
            v = spread_by_h.get(h)
            if not v:
                continue
            isd, oos = v["spread"]["IS"], v["spread"]["OOS"]
            if oos.get("t") is None or isd.get("t") is None:
                continue
            if direction * oos["t"] >= 2 and direction * isd["t"] >= 1.5 and direction * oos["mean_pct"] > 0:
                best = h
                break
        grp["status"] = "backtested" if best else "rejected"
        grp["best_horizon"] = best
        rep["hypotheses"][hid] = grp

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H34-36_report_rc_factors_20261031.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")
    L = ["# 研报评级/盈利预测/覆盖因子（H34–H36，report_rc 版）", "",
         f"- 数据：report_rc {rep['meta']['rc_rows']:,} 条 / {rep['meta']['rc_codes']} 只 / 覆盖 {rep['meta']['coverage'][0]}~{rep['meta']['coverage'][1]}",
         f"- 月度事件 → 月末次一交易日入场 → {HORIZONS} 日 CAR（相对等权、30bp）；控制组=当月有研报未触发股票；IS≤2023 / OOS≥2024", "",
         "| 假设 | 预期 | 事件 | 20日 spread IS | 20日 spread OOS | 最佳窗口 | 判定 |", "|---|---|---|---|---|---|---|"]
    for hid, grp in rep["hypotheses"].items():
        h20 = grp.get("h20", {})
        sp = h20.get("spread", {}) if isinstance(h20, dict) else {}
        L.append(f"| {hid} | {grp['expect']} | {grp['events']:,} | {f(sp.get('IS', {}))} | {f(sp.get('OOS', {}))} | {grp.get('best_horizon')} | **{grp['status']}** |")
    L += ["", "## 结论",
          "- 评级上调/盈利预测上修是卖方研究常见 alpha 线索（短期动量），机构广度是拥挤度（反转）线索；",
          "- 只有方向一致且 IS/OOS 双显著才 backtested；否则 rejected。"]
    (OUT / "H34-36_report_rc_factors_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: v["status"] for k, v in rep["hypotheses"].items()}, ensure_ascii=False), flush=True)

    # 台账
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    titles = {"H34_rating_up": "研报评级上调 → 未来 20 日跑赢？",
              "H35_np_revision_up": "盈利预测上修（净利润预测上调≥5%）→ 未来 20 日跑赢？",
              "H36_org_breadth": "研报机构广度（≥3家）→ 未来 20 日拥挤反转？"}

    def upsert(row):
        keys = [k for k in row if k in cols]
        if con.execute("SELECT id FROM hypotheses WHERE id=?", (row["id"],)).fetchone():
            con.execute("UPDATE hypotheses SET %s WHERE id=?" % ",".join("%s=?" % k for k in keys if k != "id"),
                        [row[k] for k in keys if k != "id"] + [row["id"]])
        else:
            con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                        [row[k] for k in keys])

    for hid, grp in rep["hypotheses"].items():
        hid_key = hid.split("_")[0]
        upsert({"id": hid_key, "title": titles[hid], "question": titles[hid],
                "data_sources": "report_rc + daily",
                "method": "月度聚合评级/净利预测/机构数；月末次一交易日入场；20/60日 CAR；月度 cohort 控制组；IS≤2023/OOS≥2024；30bp；block bootstrap",
                "gates": "PIT=是（研报发布月）；OOS=独立；成本=30bp；容量=待评估",
                "status": grp["status"], "owner": "DSH-Lead",
                "conclusion": "事件 %d；20日 spread IS %s / OOS %s；最佳窗口 %s → %s" % (
                    grp["events"], f(grp.get("h20", {}).get("spread", {}).get("IS", {})),
                    f(grp.get("h20", {}).get("spread", {}).get("OOS", {})), grp.get("best_horizon"), grp["status"]),
                "evidence_path": "research/reports/H34-36_report_rc_factors_20261031.md",
                "created_at": now, "updated_at": now})
    con.commit()
    con.close()
    print("台账 H34-H36 已写（report_rc 版）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
