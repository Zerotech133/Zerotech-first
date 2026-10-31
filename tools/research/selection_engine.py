# -*- coding: utf-8 -*-
"""选股能力 MVP v1.1 · Strategy → Selection（策略→候选清单）。

结构（分层可替换）：
  L1 过滤器（已验证：H14 质量陷阱规避 + 可交易性）
      - 剔除 ST/退市、上市<250日、价格<2、成交额<5000万、当日涨停不可买；
      - 剔除「高估值层 + 高 ROE（层内前20%）」= H14 质量陷阱（台账 H14 backtested）；
      - 剔除 ROE<0 亏损股（风险控制，非 alpha 来源）；
  L2 排序因子（MVP 未验证，需影子盘；权重可配）
      - 动量：20日收益（0.45）
      - 短期反转：5日收益（负向，0.25）
      - 拥挤度：换手率分位（低换手优，0.20）
      - 质量中性：ROE 层内分位中高（0.10）
  L3 组合约束：每行业≤3只，top N
输出：research/reports/selection_h14_<date>.json（含分层依据/因子快照/免责声明）

用法：python tools/research/selection_engine.py --date 20261030 --top 20
"""
from __future__ import annotations

import argparse
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
STRATEGY_ID = "selection_mvp_v1.1"

WEIGHTS = {"momentum20": 0.45, "reversal5": 0.25, "low_turnover": 0.20, "quality_mid": 0.10}


def z(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0


def latest_trade_date() -> str:
    files = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return Path(files[-1]).stem[2:]


def load_universe(date: str, hist_days: int = 26) -> tuple[pd.DataFrame, dict]:
    """返回当日截面 + 近 N 日收盘矩阵（用于动量/反转）。"""
    files = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    idx = [i for i, f in enumerate(files) if Path(f).stem[2:] == date]
    if not idx:
        raise SystemExit(f"date {date} 无 daily 文件")
    i = idx[0]
    hist_files = files[max(0, i - hist_days + 1): i + 1]
    closes = []
    for f in hist_files:
        df = pq.read_table(f, columns=["ts_code", "trade_date", "close"]).to_pandas()
        closes.append(df.set_index("ts_code")["close"].rename(Path(f).stem[2:]))
    px = pd.concat(closes, axis=1)
    px = px[~px.index.duplicated()]

    d = pq.read_table(DATA / "daily" / f"d_{date}.parquet").to_pandas()
    db = pq.read_table(DATA / "daily_basic" / f"d_{date}.parquet").to_pandas()
    d = d.merge(db[["ts_code", "pe_ttm", "circ_mv", "turnover_rate"]], on="ts_code", how="left")
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas()
    d = d.merge(sb[["ts_code", "name", "industry", "list_date"]], on="ts_code", how="left")
    dates = list(px.columns)
    c = px.reindex(d["ts_code"])
    d["ret20"] = (c[dates[-1]] / c[dates[max(0, len(dates) - 21)]] - 1.0).values
    d["ret5"] = (c[dates[-1]] / c[dates[max(0, len(dates) - 6)]] - 1.0).values
    meta = {"hist_days": len(dates), "from": dates[0], "to": dates[-1]}
    return d, meta


def load_latest_fina(date: str) -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(DATA / "fina_indicator" / "*.parquet"))):
        df = pq.read_table(f, columns=["ts_code", "ann_date", "end_date", "roe"]).to_pandas()
        df["ann_date"] = df["ann_date"].astype(str)
        df["end_date"] = df["end_date"].astype(str)
        df = df[(df["ann_date"] <= date) & (df["ann_date"].str.len() == 8) & df["roe"].notna()]
        if df.empty:
            continue
        rows.append(df.sort_values(["ann_date", "end_date"]).tail(1)[["ts_code", "ann_date", "end_date", "roe"]])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "roe"])


def build(date: str, top: int) -> dict:
    u, meta = load_universe(date)
    fin = load_latest_fina(date)
    u = u.merge(fin, on="ts_code", how="left")
    listing_days = (pd.to_datetime(date) - pd.to_datetime(u["list_date"], format="%Y%m%d", errors="coerce")).dt.days
    drops = {
        "st": u["name"].fillna("").str.contains(r"ST|\*ST|退", regex=True),
        "new": listing_days.fillna(0) < 250,
        "price": u["close"].fillna(0) < 2.0,
        "liquidity": u["amount"].fillna(0) * 1000.0 < 5e7,
        "limitup": u["pct_chg"].fillna(0) >= 9.5,
        "missing": u["pe_ttm"].isna() | u["roe"].isna() | (u["pe_ttm"] <= 0) | u["ret20"].isna(),
        "loss": u["roe"] <= 0,  # 风险控制：剔除亏损股
    }
    u["_eligible"] = ~pd.concat(drops.values(), axis=1).any(axis=1)
    e = u[u["_eligible"]].copy()

    # H14 质量陷阱规避：高估值层 + 层内 ROE 前 20% 剔除
    e["pe_bucket"] = pd.qcut(e["pe_ttm"], 3, labels=["低估值", "中估值", "高估值"], duplicates="drop")
    e["roe_pct_in_pe"] = e.groupby("pe_bucket", observed=True)["roe"].rank(pct=True)
    trap = (e["pe_bucket"].astype(str) == "高估值") & (e["roe_pct_in_pe"] >= 0.8)
    trap_n = int(trap.sum())
    e = e[~trap].copy()

    # L2 排序因子（MVP 未验证）
    e["z_mom"] = z(e["ret20"])
    e["z_rev"] = z(-e["ret5"])
    e["z_turn"] = z(-e["turnover_rate"])
    e["z_q"] = z(e["roe_pct_in_pe"])
    e["score"] = (WEIGHTS["momentum20"] * e["z_mom"] + WEIGHTS["reversal5"] * e["z_rev"]
                  + WEIGHTS["low_turnover"] * e["z_turn"] + WEIGHTS["quality_mid"] * e["z_q"])

    e = e.sort_values("score", ascending=False)
    picks, per_ind = [], {}
    for _, r in e.iterrows():
        ind = r["industry"] or "未知"
        if per_ind.get(ind, 0) >= 3:
            continue
        per_ind[ind] = per_ind.get(ind, 0) + 1
        picks.append({
            "ts_code": r["ts_code"], "name": r["name"], "industry": r["industry"],
            "close": round(float(r["close"]), 2), "pct_chg": round(float(r["pct_chg"]), 2),
            "pe_ttm": round(float(r["pe_ttm"]), 2), "roe": round(float(r["roe"]), 3),
            "ret20": round(float(r["ret20"]), 4), "ret5": round(float(r["ret5"]), 4),
            "turnover_rate": round(float(r["turnover_rate"]), 2) if pd.notna(r["turnover_rate"]) else None,
            "pe_bucket": str(r["pe_bucket"]), "roe_pct_in_pe": round(float(r["roe_pct_in_pe"]), 3),
            "score": round(float(r["score"]), 4),
            "reason": (f"H14规避通过（非高PE高ROE陷阱）；动量20d {r['ret20']:+.1%}；5d {r['ret5']:+.1%}；"
                       f"换手 {r['turnover_rate']:.1f}%；{r['pe_bucket']}"),
        })
        if len(picks) >= top:
            break

    return {
        "strategy_id": STRATEGY_ID, "strategy_name": "选股 MVP v1.1（H14 规避 + 量价/流动性排序）",
        "date": date, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "top": top, "hist": meta,
        "filters": {k: int(v.sum()) for k, v in drops.items()} | {"input": int(len(u)), "eligible_before_h14": int(len(e)) + trap_n,
                                                                  "h14_trap_excluded": trap_n, "eligible_after_h14": int(len(e))},
        "weights": WEIGHTS, "candidates": int(len(e)), "picks": picks,
        "validation": {
            "validated": ["可交易性过滤（工程常规）", "H14 质量陷阱规避（台账 H14 backtested）"],
            "unvalidated": ["动量20d", "短期反转5d", "低换手", "ROE 层内中性（MVP 权重，未过影子盘）"],
            "caveat": "候选清单为研究输出，不构成投资建议；未做行业/市值中性化与成本模型；实盘前须四关+影子盘+人工批准。",
        },
        "disclaimer": "研究用途，禁止直接实盘。",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    date = args.date or latest_trade_date()
    out = build(date, args.top)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"selection_mvp_{date}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"# {out['strategy_name']} {date} | 候选 {out['candidates']} → {len(out['picks'])} 只")
        print(f"# 过滤: {out['filters']}")
        print("# 权重:", out["weights"])
        for i, r in enumerate(out["picks"], 1):
            print(f"{i:2d}. {r['ts_code']} {r['name']} {r['industry']} | close {r['close']} PE {r['pe_ttm']} ROE {r['roe']} "
                  f"| 20d {r['ret20']:+.1%} 5d {r['ret5']:+.1%} | score {r['score']}")
        print(f"# 输出: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
