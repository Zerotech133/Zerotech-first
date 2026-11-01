# -*- coding: utf-8 -*-
"""H15b · A/H 溢价收敛「可交易性子样本」：只保留 A 股为融资融券标的的配对。

H15 主结论（IC OOS t=-10/-12，成本后年化 16%）未考虑 A 股做空可行性。
本脚本用 margin_detail（近 60 日出现 = 两融标的/可融券）过滤 A 腿，
重跑同一检验，回答「扣除做空限制后是否还有 alpha」。

产出：research/reports/AH_premium_shortable_20261030.{md,json}
用法：python tools/research/ah_premium_shortable.py
"""
from __future__ import annotations

import glob
import json
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
START, END = "20230918", "20261029"
IS_END = "20250630"
COST = 0.003
MIN_PAIRS = 15


def _norm(name: str) -> str:
    s = str(name or "").strip().upper()
    for a, b in (("Ａ", "A"), (" ", ""), ("\u3000", ""), ("-", ""), ("－", ""), ("*", "")):
        s = s.replace(a, b)
    return s


def load_pairs() -> pd.DataFrame:
    hk = pq.read_table(glob.glob(str(DATA / "hk_basic" / "*.parquet"))[0]).to_pandas()
    a = pq.read_table(glob.glob(str(DATA / "stock_basic" / "*.parquet"))[0]).to_pandas()
    hk["k"] = hk["name"].map(_norm)
    a["k"] = a["name"].map(_norm)
    m = a.merge(hk, on="k", suffixes=("_a", "_h"))
    out = m[["ts_code_a", "ts_code_h", "name_a"]].copy()
    out.columns = ["a_code", "h_code", "name"]
    return out


def margin_eligible() -> set[str]:
    fs = sorted(glob.glob(str(DATA / "margin_detail" / "d_*.parquet")))[-60:]
    codes = set()
    for f in fs:
        for c in pq.read_table(f, columns=["ts_code"]).column("ts_code").to_pylist():
            codes.add(str(c))
    return codes


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


def main(argv=None) -> int:
    pairs = load_pairs()
    eligible = margin_eligible()
    sub = pairs[pairs["a_code"].isin(eligible)].copy()
    print("A/H 配对:", len(pairs), "其中 A 股两融标的:", len(sub), flush=True)

    cn = pq.read_table(DATA / "fx_daily" / "USDCNH_FXCM.parquet").to_pandas()
    hkfx = pq.read_table(DATA / "fx_daily" / "USDHKD_FXCM.parquet").to_pandas()
    for d in (cn, hkfx):
        d["trade_date"] = d["trade_date"].astype(str)
    fx = cn.merge(hkfx, on="trade_date", suffixes=("_cn", "_hk"))
    fx["hkd_cny"] = fx["close_cn"] / fx["close_hk"]
    fx = fx[["trade_date", "hkd_cny"]]

    a = load_daily(set(sub["a_code"]), "daily").rename(columns={"ts_code": "a_code", "close": "a_close"})
    h = load_daily(set(sub["h_code"]), "hk_daily").rename(columns={"ts_code": "h_code", "close": "h_close"})
    df = a.merge(h, on="trade_date").merge(fx, on="trade_date").merge(sub, on=["a_code", "h_code"])
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
    df = df[df["name"].isin(good)]
    print("有效配对:", df["name"].nunique(), "rows:", len(df), "dates:", df["trade_date"].nunique(), flush=True)

    prem = df.pivot_table(index="trade_date", columns="name", values="premium")
    rel = df.pivot_table(index="trade_date", columns="name", values="rel_ret")
    mean = prem.rolling(60, min_periods=40).mean()
    std = prem.rolling(60, min_periods=40).std()
    z = (prem - mean) / std
    cum = (1 + rel.fillna(0)).cumprod()
    res = {"meta": {"pairs_all": int(len(pairs)), "pairs_shortable": int(df["name"].nunique()),
                    "rows": int(len(df)), "start": str(df["trade_date"].min()),
                    "end": str(df["trade_date"].max()), "is_end": IS_END, "cost": COST,
                    "min_pairs": MIN_PAIRS,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}}
    for hor in (5, 20):
        fwd = cum.shift(-(hor + 1)) / cum.shift(-1) - 1
        ics, ics_d, spreads, spr_d = [], [], [], []
        for dt in fwd.index:
            if dt not in z.index:
                continue
            zz = z.loc[dt].dropna()
            ff = fwd.loc[dt].dropna()
            common = zz.index.intersection(ff.index)
            if len(common) < MIN_PAIRS:
                continue
            ics.append(float(pd.Series(zz[common]).corr(pd.Series(ff[common]), method="spearman")))
            ics_d.append(str(dt))
            top, bot = zz[common].quantile(0.67), zz[common].quantile(0.33)
            hi = common[zz[common] >= top]
            lo = common[zz[common] <= bot]
            if len(hi) >= 3 and len(lo) >= 3:
                spreads.append(float(ff[lo].mean() - ff[hi].mean()))
                spr_d.append(str(dt))
        ics = pd.Series(ics, index=ics_d, dtype=float)
        spr = pd.Series(spreads, index=spr_d, dtype=float)
        cut = IS_END.replace("-", "")
        def stat(s):
            if len(s) < 20:
                return {"n": 0}
            return {"n": int(len(s)), "mean": float(s.mean()),
                    "t": float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))}
        res["h%d" % hor] = {"ic": {"all": stat(ics), "is": stat(ics[[i for i in ics.index if i <= cut]]),
                                   "oos": stat(ics[[i for i in ics.index if i > cut]])},
                            "ls": {"all": stat(spr), "is": stat(spr[[i for i in spr.index if i <= cut]]),
                                   "oos": stat(spr[[i for i in spr.index if i > cut]])}}
        if len(spr):
            gross = float(spr.mean()) * 252.0 / hor
            res["h%d" % hor]["ls_annual"] = {"gross_pct": round(gross * 100, 2),
                                             "net_pct": round((gross - COST * 252.0 / hor) * 100, 2)}
    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / "AH_premium_shortable_20261030.json"
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# H15b · A/H 溢价（A 股两融标的子样本）", "",
             "- 子样本：A/H 配对 %d 对中 A 股为两融标的 %d 对（近 60 日 margin_detail 出现）" % (
                 res["meta"]["pairs_all"], res["meta"]["pairs_shortable"]),
             "- 样本：%s ~ %s，%d 行；其余口径同 H15（T+1 执行，成本 0.3%%/次）" % (
                 res["meta"]["start"], res["meta"]["end"], res["meta"]["rows"]), "",
             "| 窗口 | IC OOS t | L/S 毛年化 | 成本后年化 |", "|---|---|---|---|"]
    for hor in (5, 20):
        r = res["h%d" % hor]
        oos_t = (r["ic"]["oos"] or {}).get("t")
        ann = r.get("ls_annual", {})
        lines.append("| %d 日 | %s | %s%% | %s%% |" % (
            hor, round(oos_t, 2) if oos_t else "—", ann.get("gross_pct", "—"), ann.get("net_pct", "—")))
    lines += ["", "> 说明：A 腿可融券不代表券源充足；H 腿做空未过滤（港股借券无公开数据）。"]
    md = OUT / "AH_premium_shortable_20261030.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"pairs": res["meta"]["pairs_shortable"],
                      "h5_ic_oos_t": (res["h5"]["ic"]["oos"] or {}).get("t"),
                      "h20_ic_oos_t": (res["h20"]["ic"]["oos"] or {}).get("t"),
                      "h5_net_pct": res["h5"].get("ls_annual", {}).get("net_pct"),
                      "h20_net_pct": res["h20"].get("ls_annual", {}).get("net_pct"),
                      "md": str(md)}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
