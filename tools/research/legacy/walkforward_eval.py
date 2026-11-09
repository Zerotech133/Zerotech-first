# -*- coding: utf-8 -*-
"""walk-forward 稳健性框架：逐年滚动重估（解决「OOS-only」线索的 regime 问题）。

背景：H30（存贷双高）与 H35（盈利预测上修）在固定 IS(≤2023)/OOS(≥2024) 下只 OOS 显著；
可能是「2024-2026 环境不同」。本框架改为：
  1. 每年独立估计因子 spread（触发组 - 控制组），得到年度序列表；
  2. 逐年 walk-forward 方向检验：用 1..Y-1 年数据定方向，Y 年验证（符号一致=命中）；
  3. 输出：年度 spread、年度 t、累计路径、命中率、符号稳定性；
  4. 判定：命中率≥60% 且最近 3 年同向 → "regime-robust"（仍非 backtested，需前视/容量复核）；
     年度符号翻转严重 → "regime-dependent"。
覆盖：
  A. report_rc 因子：H34 评级上调 / H35 盈利预测上修 / H36 机构广度（月度事件，20日 CAR）
  B. H30 存贷双高（年报事件，60日 CAR）
产出：research/reports/walkforward_20261031.{md,json}
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
START, END = "20190101", "20261031"
COST = 0.003
RATING = {"买入": 5, "强烈推荐": 5, "增持": 4, "推荐": 4, "优于大市": 4, "谨慎推荐": 4,
          "中性": 3, "持有": 3, "同步大市": 3, "中性偏多": 3,
          "减持": 2, "弱于大市": 2, "卖出": 1}


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


def car_at(close_mat, cidx, mkt_cum, dates, didx, code, d, hold):
    if d not in didx:
        return np.nan
    i = didx[d] + 1
    if i + hold >= len(dates):
        return np.nan
    j = cidx.get(code)
    if j is None:
        return np.nan
    p0 = close_mat[i, j]
    p1 = close_mat[i + hold, j]
    if not np.isfinite(p0) or p0 <= 0 or not np.isfinite(p1):
        return np.nan
    m = mkt_cum[i + hold] / mkt_cum[i] - 1
    return p1 / p0 - 1 - m - COST


def wf_stats(year_spread: pd.Series, year_t: pd.Series):
    years = list(year_spread.index)
    # walk-forward 命中：用过去年份均值方向预测下一年
    hits, preds = [], []
    for i in range(1, len(years)):
        past = year_spread.iloc[:i].mean()
        if abs(past) < 1e-9:
            continue
        preds.append(1 if past > 0 else -1)
        hits.append(1 if np.sign(year_spread.iloc[i]) == preds[-1] else 0)
    hit_rate = float(np.mean(hits)) if hits else None
    last3 = year_spread.iloc[-3:]
    sign_stable = bool(len(last3) == 3 and (np.sign(last3).nunique() == 1))
    mean_pct = float(year_spread.mean() * 100)
    # 年度均值 t（跨年，n 很小仅参考）
    t = float(year_spread.mean() / year_spread.std(ddof=1) * np.sqrt(len(year_spread))) if len(year_spread) > 2 and year_spread.std(ddof=1) > 0 else None
    return {"years": years, "yearly_spread_pct": [round(float(x) * 100, 3) for x in year_spread],
            "yearly_t": [None if pd.isna(x) else round(float(x), 2) for x in year_t],
            "mean_pct": round(mean_pct, 3), "t": None if t is None else round(t, 2),
            "hit_rate": None if hit_rate is None else round(hit_rate, 3),
            "last3_same_sign": sign_stable,
            "verdict": ("regime-robust" if (hit_rate is not None and hit_rate >= 0.6 and sign_stable) else "regime-dependent")}


def main() -> int:
    px = load_daily()
    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = px.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod().reindex(close.index).ffill().to_numpy()
    dates = list(close.index)
    didx = {d: i for i, d in enumerate(dates)}
    codes = list(close.columns)
    cidx = {c: i for i, c in enumerate(codes)}
    mat = close.to_numpy()
    rep = {"meta": {"start": START, "end": END, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}, "report_rc_factors": {}, "H30": {}}

    # ---------- A. report_rc 因子 ----------
    frames = [pd.read_parquet(f) for f in sorted(glob.glob(str(DATA / "report_rc" / "rc_*.parquet")))]
    rc = pd.concat(frames, ignore_index=True)
    rc["report_date"] = rc["report_date"].astype(str)
    rc = rc[(rc["report_date"] >= START) & (rc["report_date"] <= END)]
    rc["month"] = rc["report_date"].str[:6]
    rc["rating_num"] = rc["rating"].map(RATING)
    rc["np"] = pd.to_numeric(rc["np"], errors="coerce")
    m = rc.groupby(["ts_code", "month"]).agg(rating=("rating_num", "mean"), n_rep=("report_title", "count"),
                                             n_org=("org_name", "nunique"), np_med=("np", "median")).reset_index()
    m = m.sort_values(["ts_code", "month"])
    g = m.groupby("ts_code", sort=False)
    m["rating_prev"] = g["rating"].shift(1)
    m["np_prev"] = g["np_med"].shift(1)
    m["month_prev"] = g["month"].shift(1)

    def prev_month(ym):
        y, mm = int(ym[:4]), int(ym[4:])
        return f"{y-1}12" if mm == 1 else f"{y}{mm-1:02d}"
    m["adjacent"] = m["month_prev"] == m["month"].map(lambda x: prev_month(str(x)))
    m["rating_up"] = m["adjacent"] & m["rating_prev"].notna() & (m["rating"] - m["rating_prev"] >= 0.3)
    m["np_up"] = m["adjacent"] & m["np_prev"].notna() & (m["np_prev"] > 0) & (m["np_med"] / m["np_prev"] - 1 >= 0.05)
    m["org_breadth"] = m["n_org"] >= 3
    # 月末最后交易日
    px2 = px.copy()
    px2["month"] = px2["trade_date"].str[:6]
    last_day = px2.groupby("month")["trade_date"].max().to_dict()
    all_by_month = {ym: g2["ts_code"].tolist() for ym, g2 in m.groupby("month")}

    def cohort_mean(codes_list, ym, hold):
        d = last_day.get(ym)
        if d is None:
            return np.nan
        vals = [car_at(mat, cidx, mkt_cum, dates, didx, c, d, hold) for c in codes_list]
        vals = [v for v in vals if np.isfinite(v)]
        return float(np.mean(vals)) if vals else np.nan

    sigs = {"H34_rating_up": ("rating_up", +1, "评级上调"), "H35_np_revision_up": ("np_up", +1, "盈利预测上修"),
            "H36_org_breadth": ("org_breadth", -1, "机构广度(拥挤反转)")}
    for hid, (col, direction, label) in sigs.items():
        rows = []
        for ym, g3 in m.groupby("month"):
            flag = g3[g3[col]]["ts_code"].tolist()
            if not flag:
                continue
            fcar = cohort_mean(flag, ym, 20)
            ctrl = cohort_mean([c for c in all_by_month.get(ym, []) if c not in set(flag)], ym, 20)
            if not np.isfinite(fcar) or not np.isfinite(ctrl):
                continue
            rows.append({"month": ym, "year": ym[:4], "spread": (fcar - ctrl) * direction})
        dfh = pd.DataFrame(rows)
        if len(dfh) < 12:
            rep["report_rc_factors"][hid] = {"verdict": "data_insufficient", "n": len(dfh)}
            continue
        ys = dfh.groupby("year")["spread"].mean()
        yt = dfh.groupby("year")["spread"].apply(lambda s: s.mean() / s.std(ddof=1) * np.sqrt(len(s)) if len(s) > 2 and s.std(ddof=1) > 0 else np.nan)
        st = wf_stats(ys, yt)
        st.update({"expect": label, "direction": direction, "monthly_n": int(len(dfh))})
        rep["report_rc_factors"][hid] = st
        print(hid, "yearly", st["yearly_spread_pct"], "hit", st["hit_rate"], "->", st["verdict"], flush=True)

    # ---------- B. H30 存贷双高（年报事件，60日 CAR）----------
    cols_bs = ["ts_code", "ann_date", "end_date", "money_cap", "total_assets", "st_borr", "lt_borr", "bond_payable"]
    bs = pd.concat([pq.read_table(f, columns=cols_bs).to_pandas() for f in sorted(glob.glob(str(DATA / "balancesheet" / "*.parquet")))], ignore_index=True)
    bs["end_date"] = bs["end_date"].astype(str)
    bs["ann_date"] = bs["ann_date"].astype(str)
    bs = bs[bs["end_date"].str.endswith("1231")]
    bs = bs.drop_duplicates(subset=["ts_code", "end_date"])
    bs["debt"] = bs[["st_borr", "lt_borr", "bond_payable"]].fillna(0).sum(axis=1)
    bs["cash_ra"] = bs["money_cap"] / bs["total_assets"].replace(0, np.nan)
    bs["debt_ra"] = bs["debt"] / bs["total_assets"].replace(0, np.nan)
    bs["yr"] = bs["end_date"].str[:4]
    bs["cash_pct"] = bs.groupby("yr")["cash_ra"].rank(pct=True)
    bs["debt_pct"] = bs.groupby("yr")["debt_ra"].rank(pct=True)
    bs["H30"] = (bs["cash_pct"] >= 0.7) & (bs["debt_pct"] >= 0.7)
    bs = bs[(bs["ann_date"].str.len() == 8) & (bs["ann_date"] >= START) & (bs["ann_date"] <= END)]
    px3 = px.copy()
    px3["month"] = px3["trade_date"].str[:6]
    rows = []
    for r in bs.itertuples():
        d = r.ann_date
        fcar = car_at(mat, cidx, mkt_cum, dates, didx, r.ts_code, d, 60)
        if np.isfinite(fcar):
            rows.append({"ann_date": d, "year": d[:4], "H30": bool(r.H30), "car": fcar})
    e = pd.DataFrame(rows)
    e_ctrl = e[~e["H30"]].groupby("year")["car"].mean()
    e_flag = e[e["H30"]].groupby("year")["car"].mean()
    ys = (e_flag - e_ctrl).dropna()
    # H30 预期负：direction=-1
    ys_dir = ys * (-1)
    st = wf_stats(ys_dir, ys_dir)
    st.update({"expect": "存贷双高→跑输(负)", "direction": -1, "events": int(e["H30"].sum()),
               "raw_yearly_spread_pct": [round(float(x) * 100, 3) for x in ys]})
    rep["H30"] = st
    print("H30 yearly", st["yearly_spread_pct"], "hit", st["hit_rate"], "->", st["verdict"], flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "walkforward_20261031.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# walk-forward 稳健性评估（H30 / H34–H36）", "",
         "- 逐年独立估计因子 spread（触发组-控制组），方向已按假设统一（正值=符合预期）；cost 30bp；2019–2026",
         "- 命中率=用过去年份均值方向预测下一年的正确率；last3_same_sign=最近3年同向；", "",
         "| 因子 | 年度 spread(%) | 命中率 | 最近3年同向 | 判定 |", "|---|---|---|---|---|"]
    for name, st in list(rep["report_rc_factors"].items()) + [("H30", rep["H30"])]:
        if "yearly_spread_pct" not in st:
            L.append(f"| {name} | — | — | — | {st.get('verdict')} |")
            continue
        L.append(f"| {name} | {st['yearly_spread_pct']} | {st['hit_rate']} | {st['last3_same_sign']} | **{st['verdict']}** |")
    L += ["", "## 结论",
          "- regime-robust 表示「方向在年度层面稳定」，仍不等于可交易（需前视/容量/成本复核）；",
          "- regime-dependent 表示因子有效性随年份翻转，固定切分会误导（正是 H30/H35 OOS-only 的根源）。"]
    (OUT / "walkforward_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: v.get("verdict") for k, v in list(rep["report_rc_factors"].items()) + [("H30", rep["H30"])]},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
