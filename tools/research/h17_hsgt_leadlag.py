# -*- coding: utf-8 -*-
"""H17 · 南向资金/港股通持股对 A/H 溢价的领先性检验。

数据：moneyflow_hsgt（南向净额，895 日）、hk_hold（港股通持股，891 日）、
      daily/hk_daily/fx（A/H 溢价面板，同 H15）。
检验：
  A) 横截面：H 股港股通持股比例 5 日变化 → 未来 20 日 A/H 相对收益（A−H，收敛方向）
  B) 时间序列：南向净流入 5 日累计 → 未来 20 日全市场溢价变化（中位数/离散度）
口径：T+1 执行；成本 30bp；IS 2023-24 / OOS 2025-26；OOS t 显著才 backtested。
产出：research/reports/H17_hsgt_leadlag_20261030.{md,json}；H17 台账更新。
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
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
START, END = "20230103", "20261029"
IS_END = "20241231"
COST = 0.003
MIN_PAIRS = 15


def _norm(name: str) -> str:
    s = str(name or "").strip().upper()
    for a, b in (("Ａ", "A"), (" ", ""), ("\u3000", ""), ("-", ""), ("－", ""), ("*", "")):
        s = s.replace(a, b)
    return s


def load_pairs():
    hk = pq.read_table(glob.glob(str(DATA / "hk_basic" / "*.parquet"))[0]).to_pandas()
    a = pq.read_table(glob.glob(str(DATA / "stock_basic" / "*.parquet"))[0]).to_pandas()
    hk["k"] = hk["name"].map(_norm)
    a["k"] = a["name"].map(_norm)
    m = a.merge(hk, on="k", suffixes=("_a", "_h"))
    out = m[["ts_code_a", "ts_code_h", "name_a"]].copy()
    out.columns = ["a_code", "h_code", "name"]
    return out


def load_daily(codes: set[str], prefix: str) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / prefix / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close"])
        mask = pc.is_in(tb["ts_code"], value_set=__import__("pyarrow").array(list(codes)))
        tb = tb.filter(mask)
        if tb.num_rows:
            frames.append(tb.to_pandas())
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(df):
        df["trade_date"] = df["trade_date"].astype(str)
    return df


def load_hold() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(DATA / "hk_hold" / "d_*.parquet"))):
        dt = Path(f).stem[2:]
        if not (START <= dt <= END):
            continue
        frames.append(pq.read_table(f, columns=["ts_code", "trade_date", "vol", "ratio"]).to_pandas())
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(df):
        df["trade_date"] = df["trade_date"].astype(str)
    return df


def main() -> int:
    pairs = load_pairs()
    fx_cn = pq.read_table(DATA / "fx_daily" / "USDCNH_FXCM.parquet").to_pandas()
    fx_hk = pq.read_table(DATA / "fx_daily" / "USDHKD_FXCM.parquet").to_pandas()
    for d in (fx_cn, fx_hk):
        d["trade_date"] = d["trade_date"].astype(str)
    fx = fx_cn.merge(fx_hk, on="trade_date", suffixes=("_cn", "_hk"))
    fx["hkd_cny"] = fx["close_cn"] / fx["close_hk"]
    fx = fx[["trade_date", "hkd_cny"]]

    a = load_daily(set(pairs["a_code"]), "daily").rename(columns={"ts_code": "a_code", "close": "a_close"})
    h = load_daily(set(pairs["h_code"]), "hk_daily").rename(columns={"ts_code": "h_code", "close": "h_close"})
    df = a.merge(h, on="trade_date").merge(fx, on="trade_date").merge(pairs, on=["a_code", "h_code"])
    df["h_cny"] = df["h_close"] * df["hkd_cny"]
    df["premium"] = df["a_close"] / df["h_cny"] - 1
    df = df.sort_values(["name", "trade_date"])
    g = df.groupby("name")
    df["a_ret"] = g["a_close"].pct_change()
    df["h_ret_cny"] = g["h_cny"].pct_change()
    df["rel_ret"] = df["a_ret"] - df["h_ret_cny"]
    corr = df.groupby("name")[["a_ret", "h_ret_cny"]].apply(
        lambda x: x["a_ret"].corr(x["h_ret_cny"]) if len(x) > 30 else np.nan)
    good = set(corr[corr >= 0.3].index)
    df = df[df["name"].isin(good)].copy()
    print("pairs:", df["name"].nunique(), "rows:", len(df), flush=True)

    # 港股通持股比例（H 股）→ 5 日变化
    hold = load_hold()
    hold = hold[hold["ts_code"].isin(set(df["h_code"]))].copy()
    hp = hold.pivot_table(index="trade_date", columns="ts_code", values="ratio")
    hchg5 = hp.pct_change(5) if hp.notna().any().any() else pd.DataFrame()
    hchg5 = hp.diff(5)  # 持股比例变化（百分点）
    prem = df.pivot_table(index="trade_date", columns="name", values="premium")
    rel = df.pivot_table(index="trade_date", columns="name", values="rel_ret")
    hcode_of = dict(zip(df["name"], df["h_code"]))
    prem_chg_fwd = prem.shift(-21) - prem.shift(-1)  # 未来 20 日溢价变化
    rel_fwd = ((1 + rel.fillna(0)).cumprod().shift(-21) / (1 + rel.fillna(0)).cumprod().shift(-1) - 1)

    # A) 横截面 IC：hchg5(H) vs 未来 20 日溢价变化 / 相对收益
    recs = []
    for dt in prem_chg_fwd.index:
        if dt not in hchg5.index:
            continue
        for name in prem.columns:
            hc = hcode_of.get(name)
            if hc is None or hc not in hchg5.columns:
                continue
            x = hchg5.at[dt, hc] if (dt in hchg5.index and hc in hchg5.columns) else np.nan
            y = prem_chg_fwd.at[dt, name] if (dt in prem_chg_fwd.index and name in prem_chg_fwd.columns) else np.nan
            z = rel_fwd.at[dt, name] if (dt in rel_fwd.index and name in rel_fwd.columns) else np.nan
            if pd.isna(x) or pd.isna(y):
                continue
            recs.append({"date": dt, "name": name, "hchg5": float(x), "prem_chg": float(y),
                         "rel_fwd": float(z) if not pd.isna(z) else np.nan,
                         "is_oos": "IS" if dt <= IS_END else "OOS"})
    ev = pd.DataFrame(recs)
    print("cross-section obs:", len(ev), flush=True)

    def ic_by_date(ycol):
        ics, dts = [], []
        for dt, gg in ev.groupby("date"):
            gg = gg.dropna(subset=["hchg5", ycol])
            if len(gg) < MIN_PAIRS:
                continue
            ics.append(float(gg["hchg5"].corr(gg[ycol], method="spearman")))
            dts.append(str(dt))
        return pd.Series(ics, index=dts, dtype=float)

    def stat(s):
        s = s.dropna()
        if len(s) < 20:
            return {"n": int(len(s))}
        return {"n": int(len(s)), "mean": float(s.mean()),
                "t": float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))}

    cut = IS_END
    res = {"meta": {"pairs": int(df["name"].nunique()), "rows": int(len(df)),
                    "start": START, "end": END, "is_end": IS_END, "cost": COST,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}}
    for label, col in (("premium_change", "prem_chg"), ("relative_return", "rel_fwd")):
        ics = ic_by_date(col)
        res[label] = {"all": stat(ics), "IS": stat(ics[[i for i in ics.index if i <= cut]]),
                      "OOS": stat(ics[[i for i in ics.index if i > cut]])}
        print(label, res[label], flush=True)

    # B) 时间序列：南向净流入 5 日累计 → 未来 20 日全市场溢价变化
    mf = pq.read_table(DATA / "moneyflow_hsgt" / "moneyflow_hsgt.parquet").to_pandas()
    mf["trade_date"] = mf["trade_date"].astype(str)
    mf = mf[(mf["trade_date"] >= START) & (mf["trade_date"] <= END)]
    mf["south_money"] = pd.to_numeric(mf["south_money"], errors="coerce")
    mf = mf.set_index("trade_date").sort_index()
    south5 = mf["south_money"].rolling(5).sum()
    prem_med = prem.median(axis=1).reindex(mf.index)
    fwd20 = (prem_med.shift(-21) - prem_med.shift(-1))
    ts = pd.DataFrame({"south5": south5, "fwd_prem_chg": fwd20}).dropna()
    ts_is = ts[ts.index <= IS_END]
    ts_oos = ts[ts.index > IS_END]
    corr_all = float(ts["south5"].corr(ts["fwd_prem_chg"], method="spearman")) if len(ts) > 30 else None
    corr_oos = float(ts_oos["south5"].corr(ts_oos["fwd_prem_chg"], method="spearman")) if len(ts_oos) > 20 else None
    res["timeseries"] = {"n": int(len(ts)), "spearman": corr_all, "spearman_oos": corr_oos,
                         "south5_mean": float(ts["south5"].mean()) if len(ts) else None}
    print("timeseries:", res["timeseries"], flush=True)

    # 判定
    oos_t = abs((res["premium_change"]["OOS"] or {}).get("t", 0) or 0)
    oos_t_rel = abs((res["relative_return"]["OOS"] or {}).get("t", 0) or 0)
    status = "backtested" if max(oos_t, oos_t_rel) >= 2 else "rejected"
    res["status"] = status

    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "H17_hsgt_leadlag_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    def f(x):
        return "mean %.4f (t=%.2f, n=%d)" % (x["mean"], x["t"], x["n"]) if x.get("n") and "t" in x else "—"
    L = ["# H17 · 南向资金/港股通持股对 A/H 溢价的领先性", "",
         "- 配对 %d；样本 %s ~ %s；港股通持股 5 日变化 → 未来 20 日溢价变化/相对收益（T+1 执行，成本 30bp）" % (
             res["meta"]["pairs"], START, END), "",
         "## A) 横截面 IC（hchg5 → 未来 20 日）",
         "| 目标 | 全样本 | IS | OOS |", "|---|---|---|---|",
         "| 溢价变化 | %s | %s | %s |" % (f(res["premium_change"]["all"]), f(res["premium_change"]["IS"]), f(res["premium_change"]["OOS"])),
         "| 相对收益 | %s | %s | %s |" % (f(res["relative_return"]["all"]), f(res["relative_return"]["IS"]), f(res["relative_return"]["OOS"])), "",
         "## B) 时间序列（南向 5 日净额 → 未来 20 日溢价中位变化）",
         "- n=%d，Spearman=%.4f（OOS=%.4f）" % (
             res["timeseries"]["n"], res["timeseries"]["spearman"] or 0, res["timeseries"]["spearman_oos"] or 0), "",
         "## 结论", "- 状态：**%s**" % status,
         "- 判据：OOS 横截面 IC t ≥ 2 才 backtested；否则 rejected（数据已备，可为后续再检验复用）"]
    md = OUT / "H17_hsgt_leadlag_20261030.md"
    md.write_text("\n".join(L), encoding="utf-8")

    con = sqlite3.connect(str(REPO / "research" / "hypotheses.db"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    oos_pc = (res["premium_change"]["OOS"] or {})
    concl = "南向持股 5 日变化→20 日溢价变化 OOS %s；时间序列 Spearman=%s；状态 %s" % (
        ("t=%.2f" % oos_pc.get("t")) if oos_pc.get("t") is not None else "无", res["timeseries"]["spearman_oos"], status)
    con.execute("UPDATE hypotheses SET status=?, conclusion=?, evidence_path=?, updated_at=? WHERE id='H17'",
                (status, concl, "research/reports/H17_hsgt_leadlag_20261030.md", now))
    con.commit()
    con.close()
    print(json.dumps({"status": status, "premium_change_oos": res["premium_change"]["OOS"],
                      "relative_return_oos": res["relative_return"]["OOS"],
                      "timeseries": res["timeseries"], "md": str(md)}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
