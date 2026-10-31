# -*- coding: utf-8 -*-
"""H30-H32 · 企业尽调知识库「造假红旗」→ 基本面因子 → 年报公告事件研究。

知识来源：knowledge_docs/企业分析与尽调知识库.md（红旗清单）
  H30 存贷双高：货币资金/总资产 与 有息负债/总资产 均处横截面高位（≥70 分位）
  H31 商誉高企：商誉/归母净资产 > 30%
  H32 现金流质量差：经营现金流/净利润 < 0.7 且净利润>0
口径：年报（end_date=12-31），公告日 ann_date 后 T+1 收盘入场；未来 60 交易日 CAR（相对等权市场、成本 30bp）；
      IS≤2023 / OOS≥2024；月度 cohort 聚合 t + block bootstrap（stat_utils）；控制组=同月未触发股票。
产出：research/reports/H30-32_quality_redflags_20261031.{md,json}；台账 H30-H32。
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

START, END = "20200101", "20261029"
IS_END = "20231231"
COST = 0.003
HOLD = 60


def load_fin() -> pd.DataFrame:
    cols_bs = ["ts_code", "ann_date", "end_date", "money_cap", "total_assets", "goodwill",
               "st_borr", "lt_borr", "bond_payable", "total_hldr_eqy_exc_min_int"]
    cols_is = ["ts_code", "ann_date", "end_date", "n_income", "n_income_attr_p", "total_revenue"]
    cols_cf = ["ts_code", "ann_date", "end_date", "n_cashflow_act"]
    bs = pd.concat([pq.read_table(f, columns=cols_bs).to_pandas() for f in sorted(glob.glob(str(DATA / "balancesheet" / "*.parquet")))], ignore_index=True)
    inc = pd.concat([pq.read_table(f, columns=cols_is).to_pandas() for f in sorted(glob.glob(str(DATA / "income" / "*.parquet")))], ignore_index=True)
    cf = pd.concat([pq.read_table(f, columns=cols_cf).to_pandas() for f in sorted(glob.glob(str(DATA / "cashflow" / "*.parquet")))], ignore_index=True)
    for d in (bs, inc, cf):
        d["end_date"] = d["end_date"].astype(str)
        d["ann_date"] = d["ann_date"].astype(str)
    bs = bs[bs["end_date"].str.endswith("1231")]
    inc = inc[inc["end_date"].str.endswith("1231")]
    cf = cf[cf["end_date"].str.endswith("1231")]
    m = bs.merge(inc[["ts_code", "end_date", "n_income", "n_income_attr_p", "total_revenue"]], on=["ts_code", "end_date"], how="left")
    m = m.merge(cf[["ts_code", "end_date", "n_cashflow_act"]], on=["ts_code", "end_date"], how="left")
    m = m[(m["ann_date"] >= START) & (m["ann_date"] <= END) & (m["ann_date"].str.len() == 8)]
    return m


def load_daily() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "daily" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not ("20190101" <= dt <= "20261130"):
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
    fin = load_fin()
    print("financial rows:", len(fin), "codes:", fin["ts_code"].nunique(), flush=True)
    # ---- 特征 ----
    fin["debt"] = fin[["st_borr", "lt_borr", "bond_payable"]].fillna(0).sum(axis=1)
    fin["cash_ratio_assets"] = fin["money_cap"] / fin["total_assets"].replace(0, np.nan)
    fin["debt_ratio_assets"] = fin["debt"] / fin["total_assets"].replace(0, np.nan)
    fin["goodwill_eq"] = fin["goodwill"] / fin["total_hldr_eqy_exc_min_int"].replace(0, np.nan)
    fin["ocf_ni"] = fin["n_cashflow_act"] / fin["n_income"].where(fin["n_income"] > 0)
    # 横截面分位（按年报年份）
    fin["yr"] = fin["end_date"].str[:4]
    fin["cash_pct"] = fin.groupby("yr")["cash_ratio_assets"].rank(pct=True)
    fin["debt_pct"] = fin.groupby("yr")["debt_ratio_assets"].rank(pct=True)
    # 排除金融类（银行/保险/证券）
    try:
        sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[0]).to_pandas()
        fin = fin.merge(sb[["ts_code", "industry"]], on="ts_code", how="left")
        fin = fin[~fin["industry"].fillna("").str.contains("银行|保险|证券|多元金融")]
    except Exception:  # noqa: BLE001
        pass
    fin["H30"] = (fin["cash_pct"] >= 0.7) & (fin["debt_pct"] >= 0.7)
    fin["H31"] = fin["goodwill_eq"] > 0.3
    fin["H32"] = (fin["ocf_ni"] < 0.7) & (fin["n_income"] > 0)
    # ---- 行情与 CAR ----
    px = load_daily()
    close = px.pivot_table(index="trade_date", columns="ts_code", values="close")
    mkt = px.groupby("trade_date")["pct_chg"].mean() / 100.0
    mkt_cum = (1 + mkt.fillna(0)).cumprod()
    dates = list(close.index)
    idx = {d: i for i, d in enumerate(dates)}
    ev = fin[fin[["H30", "H31", "H32"]].any(axis=1)].copy()
    print("events(any flag):", len(ev), flush=True)
    rows = []
    for r in ev.itertuples():
        d = r.ann_date
        if d not in idx:
            continue
        i = idx[d] + 1
        if i >= len(dates) - HOLD:
            continue
        code = r.ts_code
        if code not in close.columns:
            continue
        p0 = close.iloc[i].get(code, np.nan)
        p1 = close.iloc[i + HOLD].get(code, np.nan)
        if pd.isna(p0) or pd.isna(p1) or p0 == 0:
            continue
        ret = p1 / p0 - 1
        m = mkt_cum.iloc[i + HOLD] / mkt_cum.iloc[i] - 1
        rows.append({"ts_code": code, "ann_date": d, "month": d[:6], "split": "IS" if d <= IS_END else "OOS",
                     "car": ret - m - COST, "H30": bool(r.H30), "H31": bool(r.H31), "H32": bool(r.H32)})
    e = pd.DataFrame(rows)
    print("obs:", len(e), flush=True)
    # 同月未触发股票作控制组（从 fin 全量里找同月公告无 flag 的）
    non = fin[~fin[["H30", "H31", "H32"]].any(axis=1)]
    rows2 = []
    for r in non.itertuples():
        d = r.ann_date
        if d not in idx:
            continue
        i = idx[d] + 1
        if i >= len(dates) - HOLD:
            continue
        code = r.ts_code
        if code not in close.columns:
            continue
        p0 = close.iloc[i].get(code, np.nan)
        p1 = close.iloc[i + HOLD].get(code, np.nan)
        if pd.isna(p0) or pd.isna(p1) or p0 == 0:
            continue
        m = mkt_cum.iloc[i + HOLD] / mkt_cum.iloc[i] - 1
        rows2.append({"month": d[:6], "split": "IS" if d <= IS_END else "OOS",
                      "car": p1 / p0 - 1 - m - COST})
    ctrl = pd.DataFrame(rows2)
    print("ctrl obs:", len(ctrl), flush=True)

    def stat(x):
        x = pd.Series(x, dtype=float).dropna()
        if len(x) < 8:
            return {"n": int(len(x))}
        return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3),
                "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}

    rep = {"meta": {"start": START, "end": END, "is_end": IS_END, "hold": HOLD, "cost": COST,
                    "financial_rows": int(len(fin)), "events": int(len(e)), "control": int(len(ctrl)),
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
           "hypotheses": {}}
    monthly_ctrl = ctrl.groupby("month")["car"].mean()
    for hid in ("H30", "H31", "H32"):
        sub = e[e[hid]]
        if len(sub) < 50:
            rep["hypotheses"][hid] = {"events": int(len(sub)), "status": "rejected", "note": "样本不足"}
            continue
        monthly = sub.groupby("month")["car"].mean()
        common = monthly.index.intersection(monthly_ctrl.index)
        spread = monthly[common] - monthly_ctrl[common]
        split = np.where(spread.index <= IS_END[:6], "IS", "OOS")
        m_is = spread[split == "IS"]
        m_oos = spread[split == "OOS"]
        grp = {"events": int(len(sub)),
               "flag_car": {"all": stat(sub["car"]), "IS": stat(sub[sub["split"] == "IS"]["car"]),
                            "OOS": stat(sub[sub["split"] == "OOS"]["car"])},
               "spread_vs_ctrl": {"all": stat(spread), "IS": stat(m_is), "OOS": stat(m_oos)},
               "bootstrap_oos": stat_utils.block_bootstrap_p(m_oos, block=3, n_boot=300),
               "monthly_n": int(len(spread)),
               "expect": "红旗 → 未来 60 日跑输（spread<0）"}
        # 判定：OOS spread t ≤ -2 且 IS spread 同向 t ≤ -1.5
        g = grp["spread_vs_ctrl"]
        ok = (g["OOS"].get("t") is not None and g["OOS"]["t"] <= -2
              and g["IS"].get("t") is not None and g["IS"]["t"] <= -1.5)
        grp["status"] = "backtested" if ok else "rejected"
        rep["hypotheses"][hid] = grp
        print(hid, "events", len(sub), "OOS", g["OOS"], "IS", g["IS"], "->", grp["status"], flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H30-32_quality_redflags_20261031.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    def f(x):
        return "%.2f%%(t=%s,n=%s)" % (x["mean_pct"], x.get("t"), x["n"]) if x.get("mean_pct") is not None else ("n=%d" % x["n"] if x.get("n") else "—")

    L = ["# 企业尽调红旗 → 基本面因子（H30–H32）", "",
         f"- 知识来源：`knowledge_docs/企业分析与尽调知识库.md` 红旗清单；年报公告 PIT；未来 {HOLD} 交易日 CAR（相对等权市场、成本 30bp）；IS≤2023 / OOS≥2024",
         f"- 样本：年报 {rep['meta']['financial_rows']:,} 条；触发事件 {rep['meta']['events']:,}；控制组 {rep['meta']['control']:,}", "",
         "| 假设 | 触发数 | 触发组 OOS | 控制组差（spread）IS | spread OOS | 判定 |",
         "|---|---|---|---|---|---|"]
    for hid, grp in rep["hypotheses"].items():
        if "flag_car" not in grp:
            L.append(f"| {hid} | {grp.get('events')} | — | — | — | rejected（样本不足） |")
            continue
        L.append(f"| {hid} | {grp['events']:,} | {f(grp['flag_car']['OOS'])} | {f(grp['spread_vs_ctrl']['IS'])} | {f(grp['spread_vs_ctrl']['OOS'])} | **{grp['status']}** |")
    L += ["", "## 结论",
          "- 红旗假设需「触发组显著跑输控制组」；若 spread IS/OOS 同向且 t≤-2/≤-1.5 才算 backtested；",
          "- 该测试为因子初筛（未做行业/市值中性化、未剔除 ST），通过后才进入精细化与容量评估。"]
    (OUT / "H30-32_quality_redflags_20261031.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({k: v.get("status") for k, v in rep["hypotheses"].items()}, ensure_ascii=False), flush=True)

    # 台账
    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cols = [c[1] for c in con.execute("PRAGMA table_info(hypotheses)")]
    titles = {"H30": "存贷双高（货币资金高+有息负债高）→ 未来 60 日跑输？",
              "H31": "商誉/净资产>30% → 未来 60 日跑输？",
              "H32": "经营现金流/净利润<0.7 → 未来 60 日跑输？"}

    def upsert(row):
        keys = [k for k in row if k in cols]
        if con.execute("SELECT id FROM hypotheses WHERE id=?", (row["id"],)).fetchone():
            con.execute("UPDATE hypotheses SET %s WHERE id=?" % ",".join("%s=?" % k for k in keys if k != "id"),
                        [row[k] for k in keys if k != "id"] + [row["id"]])
        else:
            con.execute("INSERT INTO hypotheses (%s) VALUES (%s)" % (",".join(keys), ",".join("?" * len(keys))),
                        [row[k] for k in keys])

    for hid, grp in rep["hypotheses"].items():
        if "flag_car" not in grp:
            continue
        upsert({"id": hid, "title": titles[hid], "question": titles[hid],
                "data_sources": "income/balancesheet/cashflow（年报）+ daily",
                "method": f"年报特征→公告日后 T+1 入场→{HOLD}日 CAR（相对等权、β未调整、30bp）；月度 cohort 控制组；IS≤2023/OOS≥2024；block bootstrap",
                "gates": "PIT=是（公告日）；OOS=独立；成本=30bp；容量=待评估",
                "status": grp["status"], "owner": "DSH-Lead",
                "conclusion": "触发 %d；spread IS %s / OOS %s → %s" % (
                    grp["events"], f(grp["spread_vs_ctrl"]["IS"]), f(grp["spread_vs_ctrl"]["OOS"]), grp["status"]),
                "evidence_path": "research/reports/H30-32_quality_redflags_20261031.md",
                "created_at": now, "updated_at": now})
    con.commit()
    con.close()
    print("台账 H30-H32 已写", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
