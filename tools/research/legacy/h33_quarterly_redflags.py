# -*- coding: utf-8 -*-
"""H33 · H30 存贷双高 · 季度公告扩展重测（提高统计功效）。

改进（相对 H30 年报版）：
  1. 事件=所有定期报告公告（季报/半年报/年报，≈4x 观测）；
  2. 特征使用「事件日前最近一次已公告年报」的存贷双高标记（PIT，滞后≤18个月）；
  3. 60 日 CAR（相对等权市场、成本 30bp），月度 cohort 控制组，IS≤2023/OOS≥2024；
  4. 辅以年度 Fama-MacBeth 横截面回归（flag 对 60 日 CAR，按年取系数/ t）。
产出：research/reports/H33_quarterly_redflags_20261031.{md,json}；台账 H33。
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

START, END = "20190101", "20261029"
IS_END = "20231231"
COST = 0.003
HOLD = 60


def load_annual_features() -> pd.DataFrame:
    cols_bs = ["ts_code", "ann_date", "end_date", "money_cap", "total_assets", "goodwill",
               "st_borr", "lt_borr", "bond_payable", "total_hldr_eqy_exc_min_int"]
    cols_inc = ["ts_code", "ann_date", "end_date", "n_income"]
    cols_cf = ["ts_code", "ann_date", "end_date", "n_cashflow_act"]
    bs = pd.concat([pq.read_table(f, columns=cols_bs).to_pandas() for f in sorted(glob.glob(str(DATA / "balancesheet" / "*.parquet")))], ignore_index=True)
    inc = pd.concat([pq.read_table(f, columns=cols_inc).to_pandas() for f in sorted(glob.glob(str(DATA / "income" / "*.parquet")))], ignore_index=True)
    cf = pd.concat([pq.read_table(f, columns=cols_cf).to_pandas() for f in sorted(glob.glob(str(DATA / "cashflow" / "*.parquet")))], ignore_index=True)
    for d in (bs, inc, cf):
        d["end_date"] = d["end_date"].astype(str)
        d["ann_date"] = d["ann_date"].astype(str)
    for d in (bs, inc, cf):
        d.drop_duplicates(subset=["ts_code", "end_date"], inplace=True)
    bs = bs[bs["end_date"].str.endswith("1231")]
    inc = inc[inc["end_date"].str.endswith("1231")]
    cf = cf[cf["end_date"].str.endswith("1231")]
    m = bs.merge(inc[["ts_code", "end_date", "n_income"]], on=["ts_code", "end_date"], how="left")
    m = m.merge(cf[["ts_code", "end_date", "n_cashflow_act"]], on=["ts_code", "end_date"], how="left")
    m = m[(m["ann_date"].str.len() == 8) & (m["ann_date"] >= "20180101") & (m["ann_date"] <= END)]
    m["debt"] = m[["st_borr", "lt_borr", "bond_payable"]].fillna(0).sum(axis=1)
    m["cash_ra"] = m["money_cap"] / m["total_assets"].replace(0, np.nan)
    m["debt_ra"] = m["debt"] / m["total_assets"].replace(0, np.nan)
    m["goodwill_eq"] = m["goodwill"] / m["total_hldr_eqy_exc_min_int"].replace(0, np.nan)
    m["ocf_ni"] = m["n_cashflow_act"] / m["n_income"].where(m["n_income"] > 0)
    m["yr"] = m["end_date"].str[:4]
    m["cash_pct"] = m.groupby("yr")["cash_ra"].rank(pct=True)
    m["debt_pct"] = m.groupby("yr")["debt_ra"].rank(pct=True)
    m["H30"] = (m["cash_pct"] >= 0.7) & (m["debt_pct"] >= 0.7)
    m["H31"] = m["goodwill_eq"] > 0.3
    m["H32"] = (m["ocf_ni"] < 0.7) & (m["n_income"] > 0)
    # 只保留一般行业
    try:
        sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[0]).to_pandas()
        m = m.merge(sb[["ts_code", "industry"]], on="ts_code", how="left")
        m = m[~m["industry"].fillna("").str.contains("银行|保险|证券|多元金融")]
    except Exception:  # noqa: BLE001
        pass
    return m[["ts_code", "ann_date", "end_date", "cash_ra", "debt_ra", "goodwill_eq", "ocf_ni", "H30", "H31", "H32"]]


def load_announcements() -> pd.DataFrame:
    """所有定期报告公告（含季报/半年报）。"""
    rows = []
    for f in sorted(glob.glob(str(DATA / "income" / "*.parquet"))):
        tb = pq.read_table(f, columns=["ts_code", "ann_date", "end_date"])
        df = tb.to_pandas()
        df = df[(df["ann_date"].str.len() == 8) & (df["ann_date"] >= START) & (df["ann_date"] <= END)]
        if len(df):
            rows.append(df)
    d = pd.concat(rows, ignore_index=True)
    d = d.drop_duplicates(subset=["ts_code", "ann_date", "end_date"])
    return d


def load_daily_matrix():
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
    close = d.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = d.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod().reindex(close.index).ffill()
    return close, mkt_cum


def main() -> int:
    feats = load_annual_features()
    print("annual features:", len(feats), flush=True)
    anns = load_announcements()
    print("announcements:", len(anns), "codes:", anns["ts_code"].nunique(), flush=True)
    # PIT 附着：每个公告取之前最近一次已公告年报特征（≤540 天），按股票用 searchsorted
    feats["ann_date_dt"] = pd.to_datetime(feats["ann_date"], format="%Y%m%d")
    anns["ann_date_dt"] = pd.to_datetime(anns["ann_date"], format="%Y%m%d")
    fmap = {code: g.sort_values("ann_date_dt") for code, g in feats.groupby("ts_code")}
    pieces = []
    for code, g in anns.groupby("ts_code"):
        f = fmap.get(code)
        if f is None or f.empty:
            continue
        a = g.sort_values("ann_date_dt")
        fd = f["ann_date_dt"].to_numpy().astype("datetime64[D]")
        ad = a["ann_date_dt"].to_numpy().astype("datetime64[D]")
        pos = np.searchsorted(fd, ad, side="right") - 1
        valid = pos >= 0
        pos_c = np.clip(pos, 0, None)
        within = np.zeros(len(a), dtype=bool)
        within[valid] = (ad[valid] - fd[pos_c[valid]]) <= np.timedelta64(540, "D")
        if not within.any():
            continue
        sub = a.iloc[np.where(within)[0]].copy()
        fp = f.iloc[pos_c[within]]
        sub["H30"] = fp["H30"].to_numpy()
        sub["H31"] = fp["H31"].to_numpy()
        sub["H32"] = fp["H32"].to_numpy()
        pieces.append(sub[["ts_code", "ann_date", "H30", "H31", "H32"]])
    ev = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    ev = ev.dropna(subset=["H30"])
    print("events with known annual features:", len(ev), flush=True)

    close, mkt_cum = load_daily_matrix()
    dates = list(close.index)
    date_idx = {d: i for i, d in enumerate(dates)}
    codes = list(close.columns)
    code_idx = {c: i for i, c in enumerate(codes)}
    mat = close.to_numpy()

    ev["d_idx"] = ev["ann_date"].map(date_idx)
    ev = ev.dropna(subset=["d_idx"])
    ev["i"] = ev["d_idx"].astype(int) + 1
    ev = ev[(ev["i"] + HOLD) < len(dates)]
    rows = ev["i"].to_numpy()
    cols = ev["ts_code"].map(code_idx).to_numpy()
    valid = ~pd.isna(cols)
    rows, cols = rows[valid].astype(int), cols[valid].astype(int)
    ev = ev[valid]
    p0 = mat[rows, cols]
    p1 = mat[rows + HOLD, cols]
    m = mkt_cum.iloc[rows + HOLD].to_numpy() / mkt_cum.iloc[rows].to_numpy() - 1
    car = p1 / p0 - 1 - m - COST
    ok = np.isfinite(car) & (p0 > 0)
    ev = ev[ok].copy()
    ev["car"] = car[ok]
    ev["month"] = ev["ann_date"].str[:6]
    ev["split"] = np.where(ev["ann_date"] <= IS_END, "IS", "OOS")
    print("obs:", len(ev), "flags:", {k: int(ev[k].sum()) for k in ("H30", "H31", "H32")}, flush=True)

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 8:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "hold": HOLD, "cost": COST,
                    "announcements": int(len(anns)), "events": int(len(ev)),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "hypotheses": {}}
    ctrl_month = ev[~(ev[["H30", "H31", "H32"]].any(axis=1))].groupby("month")["car"].mean()
    for hid in ("H30", "H31", "H32"):
        sub = ev[ev[hid]]
        if len(sub) < 100:
            rep["hypotheses"][hid] = {"events": int(len(sub)), "status": "rejected", "note": "样本不足"}
            continue
        monthly = sub.groupby("month")["car"].mean()
        common = monthly.index.intersection(ctrl_month.index)
        spread = monthly[common] - ctrl_month[common]
        is_mask = spread.index <= IS_END[:6]
        m_is, m_oos = spread[is_mask], spread[~is_mask]
        # Fama-MacBeth：按年横截面回归 flag -> car
        fm = []
        for yr, g in sub.groupby(sub["ann_date"].str[:4]):
            if len(g) < 30:
                continue
            y = g["car"].to_numpy()
            x = g[hid].astype(float).to_numpy()
            if x.std() == 0:
                continue
            beta = np.polyfit(x, y, 1)[0]
            fm.append({"year": yr, "beta_pct": round(float(beta) * 100, 3), "n": int(len(g))})
        fm_beta = np.mean([x["beta_pct"] for x in fm]) / 100 if fm else None
        fm_t = None
        if fm:
            vals = np.array([x["beta_pct"] for x in fm])
            fm_t = round(float(vals.mean() / vals.std(ddof=1) * np.sqrt(len(vals))), 2) if len(vals) > 2 and vals.std(ddof=1) > 0 else None
        grp = {"events": int(len(sub)),
               "flag_car": {"all": stat(sub["car"]), "IS": stat(sub[sub["split"] == "IS"]["car"]),
                            "OOS": stat(sub[sub["split"] == "OOS"]["car"])},
               "spread_vs_ctrl": {"all": stat(spread), "IS": stat(m_is), "OOS": stat(m_oos)},
               "clusters": {"IS": int(len(m_is)), "OOS": int(len(m_oos)), "total": int(len(spread))},
               "bootstrap_oos": stat_utils.block_bootstrap_p(m_oos, block=3, n_boot=300),
               "fama_macbeth": {"beta_pct": round(float(fm_beta) * 100, 3) if fm_beta is not None else None,
                                "t": fm_t, "years": len(fm)},
               "expect": "红旗 → 未来 60 日跑输（spread<0）"}
        g = grp["spread_vs_ctrl"]
        ok2 = (g["OOS"].get("t") is not None and g["OOS"]["t"] <= -2
               and g["IS"].get("t") is not None and g["IS"]["t"] <= -1.5)
        grp["status"] = "backtested" if ok2 else "rejected"
        rep["hypotheses"][hid] = grp
        print(hid, "events", len(sub), "clusters", grp["clusters"], "spread OOS", g["OOS"], "IS", g["IS"],
              "FM", grp["fama_macbeth"], "->", grp["status"], flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H33_quarterly_redflags_20261031.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")

    L = ["# H33 · 红旗因子季度公告扩展重测", "",
         f"- 事件=定期报告公告 {rep['meta']['announcements']:,}（季报/半年报/年报）；特征=最近已知年报（PIT ≤18 个月）；未来 {HOLD} 日 CAR；成本 30bp；IS≤2023/OOS≥2024",
         f"- 观测 {rep['meta']['events']:,}；月度 cohort（IS/OOS）见下表；Fama-MacBeth=年度横截面回归 flag 系数均值", "",
         "| 假设 | 触发数 | 触发组 OOS | spread IS | spread OOS | cohorts IS/OOS | FM beta(t) | 判定 |",
         "|---|---|---|---|---|---|---|---|"]
    for hid, grp in rep["hypotheses"].items():
        if "flag_car" not in grp:
            L.append(f"| {hid} | {grp.get('events')} | — | — | — | — | — | rejected（样本不足） |")
            continue
        fm = grp["fama_macbeth"]
        L.append(f"| {hid} | {grp['events']:,} | {f(grp['flag_car']['OOS'])} | {f(grp['spread_vs_ctrl']['IS'])} | {f(grp['spread_vs_ctrl']['OOS'])} | "
                 f"{grp['clusters']['IS']}/{grp['clusters']['OOS']} | {fm['beta_pct']}%(t={fm['t']}) | **{grp['status']}** |")
    L += ["", "## 结论",
          "- 季度扩展后 cohort 数量大幅提升；若 H30 spread 仍仅 OOS 显著 → 维持 rejected（不稳健）；",
          "- Fama-MacBeth 独立佐证：flag 系数均值若为负且 t≤-2 支持红旗有效；否则不支持。"]
    (OUT / "H33_quarterly_redflags_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: v.get("status") for k, v in rep["hypotheses"].items()}, ensure_ascii=False), flush=True)

    # 台账 H33（存贷双高季度版）
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    g30 = rep["hypotheses"].get("H30", {})
    row = {"id": "H33", "title": "存贷双高（季度公告扩展版）",
           "question": "存贷双高公司，在每次定期报告公告后 60 日是否跑输（季度扩展提高功效）？",
           "data_sources": "income/balancesheet/cashflow + daily（季度公告事件）",
           "method": "最近已知年报特征 PIT；公告后 T+1 入场；60日 CAR；月度 cohort 控制组 + 年度 Fama-MacBeth；IS≤2023/OOS≥2024；30bp",
           "gates": "PIT=是；OOS=独立；成本=30bp；容量=待评估",
           "status": g30.get("status", "rejected"), "owner": "DSH-Lead",
           "conclusion": "触发 %s；cohorts IS/OOS=%s；spread IS %s / OOS %s；FM %s" % (
               g30.get("events"), g30.get("clusters"), f(g30.get("spread_vs_ctrl", {}).get("IS", {})),
               f(g30.get("spread_vs_ctrl", {}).get("OOS", {})), g30.get("fama_macbeth")),
           "evidence_path": "research/reports/H33_quarterly_redflags_20261031.md",
           "created_at": now, "updated_at": now}
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    keys = [k for k in row if k in cols]
    if con.execute("SELECT id FROM hypotheses WHERE id='H33'").fetchone():
        con.execute("UPDATE hypotheses SET %s WHERE id='H33'" % ",".join("%s=?" % k for k in keys if k != "id"),
                    [row[k] for k in keys if k != "id"])
    else:
        con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                    [row[k] for k in keys])
    con.commit()
    con.close()
    print("台账 H33 已写", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
